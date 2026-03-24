"""
Flashbots Bundle Submitter for Nexus AI.

Sends signed transaction bundles directly to block-builders via the
Flashbots MEV-Share relay, bypassing the public mempool entirely.

Benefits:
  • Transactions are invisible until included — no front-running
  • Atomic: the whole bundle succeeds or none of it lands
  • No gas wasted on failed transactions (if bundle not included,
    it simply isn't mined — no ETH spent)

Also supports:
  • bloXroute (BDN) for ultra-fast propagation
  • Eden Network as fallback relay

Docs: https://docs.flashbots.net/flashbots-auction/searchers/advanced/rpc-endpoint
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

FLASHBOTS_RELAY_ETH     = "https://relay.flashbots.net"
BLOXROUTE_RELAY_ETH     = "https://mev.api.blxrbdn.com"
EDEN_RELAY_ETH          = "https://api.edennetwork.io/v1/bundle"

# Block window: try to land in any of the next N blocks
TARGET_BLOCK_RANGE = 3


class BundleSubmitter:
    """
    Signs and submits MEV bundles to Flashbots (and optionally bloXroute/Eden).

    Set FLASHBOTS_SIGNING_KEY in .env — a dedicated Ethereum private key
    used ONLY to sign bundle requests (not the trading wallet key).
    """

    def __init__(self):
        signing_key = Config.FLASHBOTS_SIGNING_KEY
        if signing_key:
            self._signer = Account.from_key(signing_key)
            logger.info("BundleSubmitter ready — signer: %s…", self._signer.address[:12])
        else:
            self._signer = None
            logger.warning("FLASHBOTS_SIGNING_KEY not set — bundle submission disabled")

    def is_available(self) -> bool:
        return self._signer is not None

    # ── Main submission API ───────────────────────────────────

    def submit_bundle(
        self,
        signed_txs: list[bytes],
        w3: Web3,
        target_block: Optional[int] = None,
    ) -> dict:
        """
        Submit a list of raw signed transactions as a Flashbots bundle.

        Args:
            signed_txs:   list of raw signed tx bytes
            w3:           Web3 instance for the target chain
            target_block: specific block to target (defaults to next N blocks)

        Returns:
            dict with {success, bundle_hash, block_number, relay, error}
        """
        if not self.is_available():
            return {"success": False, "error": "Flashbots signing key not configured"}

        current_block = w3.eth.block_number

        hex_txs = [
            tx.hex() if isinstance(tx, bytes) else tx
            for tx in signed_txs
        ]

        results = []
        for offset in range(TARGET_BLOCK_RANGE):
            block_num = (target_block or current_block) + 1 + offset
            result = self._submit_to_flashbots(hex_txs, block_num)
            results.append(result)
            if result.get("success"):
                logger.info(
                    "Bundle submitted to Flashbots — block=%d hash=%s",
                    block_num,
                    result.get("bundle_hash", "")[:20],
                )
                return result

        # Fallback to bloXroute if Flashbots failed
        if Config.BLOXROUTE_AUTH_HEADER:
            br_result = self._submit_to_bloxroute(hex_txs, current_block + 1)
            if br_result.get("success"):
                logger.info("Bundle submitted via bloXroute")
                return br_result

        logger.warning("All bundle submission attempts failed: %s", results[-1])
        return results[-1] if results else {"success": False, "error": "No results"}

    def simulate_bundle(
        self, signed_txs: list[bytes], w3: Web3
    ) -> dict:
        """
        Simulate a bundle against a block without submitting.
        Returns profit estimate, gas used, and any revert reasons.
        """
        if not self.is_available():
            return {"success": False, "error": "Not configured"}

        current_block = w3.eth.block_number
        hex_txs = [
            tx.hex() if isinstance(tx, bytes) else tx
            for tx in signed_txs
        ]

        payload = {
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "eth_callBundle",
            "params": [
                {
                    "txs":              hex_txs,
                    "blockNumber":      hex(current_block + 1),
                    "stateBlockNumber": "latest",
                }
            ],
        }

        response = self._flashbots_rpc(payload)
        if response and "result" in response:
            result = response["result"]
            total_gas = sum(
                int(r.get("gasUsed", "0x0"), 16)
                for r in result.get("results", [])
            )
            return {
                "success":   True,
                "gas_used":  total_gas,
                "coin_base_diff": int(result.get("coinbaseDiff", "0x0"), 16),
                "results":   result.get("results", []),
            }
        return {"success": False, "error": str(response)}

    # ── Flashbots relay ───────────────────────────────────────

    def _submit_to_flashbots(self, hex_txs: list[str], block_number: int) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "eth_sendBundle",
            "params": [
                {
                    "txs":         hex_txs,
                    "blockNumber": hex(block_number),
                }
            ],
        }
        response = self._flashbots_rpc(payload)
        if response and "result" in response:
            return {
                "success":      True,
                "relay":        "flashbots",
                "bundle_hash":  response["result"].get("bundleHash", ""),
                "block_number": block_number,
            }
        error = response.get("error", {}).get("message", "Unknown") if response else "No response"
        return {"success": False, "relay": "flashbots", "error": error}

    def _flashbots_rpc(self, payload: dict) -> Optional[dict]:
        body = json.dumps(payload)
        # Flashbots requires a signature of keccak256(body) using the signer key
        message_hash = Web3.keccak(text=body).hex()
        signed = self._signer.sign_message(encode_defunct(hexstr=message_hash))
        signature = f"{self._signer.address}:{signed.signature.hex()}"

        try:
            resp = requests.post(
                FLASHBOTS_RELAY_ETH,
                data=body,
                headers={
                    "Content-Type":       "application/json",
                    "X-Flashbots-Signature": signature,
                },
                timeout=10,
            )
            return resp.json()
        except Exception as exc:
            logger.debug("Flashbots RPC error: %s", exc)
            return None

    # ── bloXroute relay ───────────────────────────────────────

    def _submit_to_bloxroute(self, hex_txs: list[str], block_number: int) -> dict:
        try:
            resp = requests.post(
                BLOXROUTE_RELAY_ETH,
                json={
                    "id":      "1",
                    "method":  "blxr_submit_bundle",
                    "params": {
                        "transaction": hex_txs,
                        "block_number": hex(block_number),
                    },
                },
                headers={
                    "Authorization": Config.BLOXROUTE_AUTH_HEADER,
                    "Content-Type":  "application/json",
                },
                timeout=10,
            )
            data = resp.json()
            if "result" in data:
                return {
                    "success":  True,
                    "relay":    "bloxroute",
                    "bundle_hash": data["result"].get("bundleHash", ""),
                }
            return {"success": False, "relay": "bloxroute", "error": str(data)}
        except Exception as exc:
            return {"success": False, "relay": "bloxroute", "error": str(exc)}


# Singleton
_submitter: Optional[BundleSubmitter] = None


def get_bundle_submitter() -> BundleSubmitter:
    global _submitter
    if _submitter is None:
        _submitter = BundleSubmitter()
    return _submitter
