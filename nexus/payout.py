"""
Payout Manager for Nexus AI.

After each profitable trade, automatically distributes rewards to the
user's configured destination(s):

  1. On-chain EVM sweep  – sends tokens/coins directly to any address
     (use your Coinbase or Cash App ETH/USDC deposit address here).

  2. Coinbase Wallet API – uses your Coinbase API key to move funds
     internally or to an external address from your Coinbase account.

  3. Cash App Bitcoin    – sends BTC to your Cash App on-chain Bitcoin
     address, or logs a Lightning invoice request for your Cash App
     Lightning / $cashtag address.

Flow
----
  1. Trade executes successfully → executor calls payout_manager.queue(profit_usd, chain)
  2. PayoutManager accumulates pending USD value
  3. When pending >= PAYOUT_THRESHOLD_USD, a sweep is triggered
  4. Preferred payout route is tried in order:
       Coinbase API → on-chain EVM transfer → Bitcoin (manual guidance)
  5. Result is stored in the DB via RewardTracker (payout_history table)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Optional

import requests
from web3 import Web3
from eth_account import Account

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── ERC-20 transfer ABI ───────────────────────────────────────
ERC20_TRANSFER_ABI = [
    {
        "name": "transfer",
        "type": "function",
        "inputs": [
            {"name": "to",    "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "decimals",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]

# USDC contract addresses per chain (used as the default payout token)
USDC_ADDRESSES: dict[str, str] = {
    "ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "bsc":      "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
    "polygon":  "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}

COINBASE_API_BASE = "https://api.coinbase.com"


# ═════════════════════════════════════════════════════════════
#  PayoutManager
# ═════════════════════════════════════════════════════════════

class PayoutManager:
    """
    Accumulates trade profits and sweeps them to Coinbase / Cash App
    once the configured threshold is reached.
    """

    def __init__(self, blockchain_manager=None):
        self.bm = blockchain_manager
        self._lock = threading.Lock()
        self._pending_usd: float = 0.0
        self._total_paid_usd: float = 0.0
        self._payout_count: int = 0
        self._last_payout_ts: float = 0.0
        self._history: list[dict] = []

    # ── Public API ────────────────────────────────────────────

    def queue(self, profit_usd: float, chain: str) -> None:
        """
        Add a trade profit to the pending balance.
        A sweep is triggered automatically when the threshold is met.
        """
        with self._lock:
            self._pending_usd += profit_usd
            logger.info(
                "Payout queue +$%.4f (pending=$%.4f / threshold=$%.2f)",
                profit_usd, self._pending_usd, Config.PAYOUT_THRESHOLD_USD,
            )
            if self._pending_usd >= Config.PAYOUT_THRESHOLD_USD:
                amount = self._pending_usd
                self._pending_usd = 0.0

        if "amount" in dir():
            # Run sweep off the lock
            threading.Thread(
                target=self._sweep,
                args=(amount, chain),
                daemon=True,
            ).start()

    def sweep_now(self, chain: str = "ethereum") -> dict:
        """Force an immediate sweep regardless of threshold."""
        with self._lock:
            amount = self._pending_usd
            self._pending_usd = 0.0
        if amount <= 0:
            return {"success": False, "reason": "No pending balance"}
        return self._sweep(amount, chain)

    def status(self) -> dict:
        with self._lock:
            return {
                "pending_usd": round(self._pending_usd, 4),
                "total_paid_usd": round(self._total_paid_usd, 4),
                "payout_count": self._payout_count,
                "last_payout_ts": self._last_payout_ts,
                "threshold_usd": Config.PAYOUT_THRESHOLD_USD,
                "payout_address": Config.PAYOUT_ADDRESS or "not configured",
                "payout_chain": Config.PAYOUT_CHAIN,
                "payout_mode": self._active_mode(),
                "coinbase_configured": bool(Config.COINBASE_API_KEY and Config.COINBASE_API_SECRET),
                "lightning_configured": bool(Config.PAYOUT_LIGHTNING_ADDRESS),
                "dry_run": Config.DRY_RUN,
            }

    def history(self, limit: int = 20) -> list:
        with self._lock:
            return list(reversed(self._history[-limit:]))

    # ── Sweep orchestration ───────────────────────────────────

    def _sweep(self, amount_usd: float, chain: str) -> dict:
        logger.info("Initiating payout sweep: $%.4f on %s", amount_usd, chain)

        if Config.DRY_RUN:
            return self._record_result(
                amount_usd=amount_usd,
                method="dry_run",
                destination=Config.PAYOUT_ADDRESS or "not configured",
                tx_ref="DRY_RUN_SIMULATED",
                success=True,
            )

        # Try routes in order of preference
        result = None

        # 1. Coinbase API (if configured)
        if Config.COINBASE_API_KEY and Config.COINBASE_API_SECRET:
            result = self._payout_via_coinbase(amount_usd)

        # 2. On-chain EVM transfer (if payout address is set)
        if not result and Config.PAYOUT_ADDRESS:
            result = self._payout_onchain(amount_usd, chain)

        # 3. Lightning fallback: request invoice and log it
        if not result and Config.PAYOUT_LIGHTNING_ADDRESS:
            result = self._payout_lightning(amount_usd)

        if not result:
            logger.warning("No payout route configured – profit stays in bot wallet.")
            result = {
                "success": False,
                "method": "none",
                "reason": "No payout destination configured.",
            }

        return self._record_result(
            amount_usd=amount_usd,
            method=result.get("method", "unknown"),
            destination=result.get("destination", ""),
            tx_ref=result.get("tx_ref", ""),
            success=result.get("success", False),
            error=result.get("reason", ""),
        )

    # ── Route 1: Coinbase Wallet API ──────────────────────────

    def _payout_via_coinbase(self, amount_usd: float) -> Optional[dict]:
        """
        Use the Coinbase Wallet API (v2) to send funds.

        Docs: https://docs.cdp.coinbase.com/coinbase-app/docs/api-transactions
        The API key must be a Coinbase 'API Key' (not Advanced Trade).
        Set COINBASE_ACCOUNT_ID to the UUID of the source account, or
        the system will look up the first USDC/ETH account automatically.
        """
        try:
            # Resolve account ID
            account_id = Config.COINBASE_ACCOUNT_ID or self._coinbase_find_account()
            if not account_id:
                logger.warning("Coinbase account ID not found – skipping Coinbase payout")
                return None

            currency = Config.PAYOUT_TOKEN.upper()  # e.g. "USDC"
            to_address = Config.PAYOUT_ADDRESS

            if not to_address:
                # Send to self (just log as received) - no external address needed
                logger.info("Coinbase payout: no external address; funds stay in Coinbase account.")
                return {
                    "success": True,
                    "method": "coinbase_internal",
                    "destination": f"coinbase:{account_id}",
                    "tx_ref": f"coinbase_internal_{int(time.time())}",
                }

            payload = {
                "type": "send",
                "to": to_address,
                "amount": str(round(amount_usd, 6)),
                "currency": currency,
                "description": "Nexus AI farming reward",
                "idem": f"nexus_{int(time.time())}",  # idempotency key
            }

            resp = self._coinbase_request(
                "POST",
                f"/v2/accounts/{account_id}/transactions",
                payload,
            )

            if resp and resp.get("data"):
                tx_id = resp["data"].get("id", "")
                logger.info("Coinbase send successful: tx_id=%s", tx_id)
                return {
                    "success": True,
                    "method": "coinbase_api",
                    "destination": to_address,
                    "tx_ref": tx_id,
                }

            logger.warning("Coinbase API send failed: %s", resp)
            return None

        except Exception as exc:
            logger.error("Coinbase payout error: %s", exc)
            return None

    def _coinbase_find_account(self) -> Optional[str]:
        """Find the first USDC or ETH account in the Coinbase wallet."""
        try:
            resp = self._coinbase_request("GET", "/v2/accounts?limit=20")
            if not resp:
                return None
            preferred = [Config.PAYOUT_TOKEN.upper(), "ETH", "USDC"]
            accounts = resp.get("data", [])
            for currency in preferred:
                for acct in accounts:
                    if acct.get("currency", {}).get("code") == currency:
                        return acct["id"]
            return accounts[0]["id"] if accounts else None
        except Exception as exc:
            logger.error("Coinbase account lookup failed: %s", exc)
            return None

    def _coinbase_request(self, method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
        """Sign and send a Coinbase API v2 request."""
        timestamp = str(int(time.time()))
        body_str = json.dumps(body) if body else ""
        message = timestamp + method.upper() + path + body_str
        signature = hmac.new(
            Config.COINBASE_API_SECRET.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        headers = {
            "CB-ACCESS-KEY":       Config.COINBASE_API_KEY,
            "CB-ACCESS-SIGN":      signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-VERSION":          "2016-02-18",
            "Content-Type":        "application/json",
        }

        url = COINBASE_API_BASE + path
        resp = requests.request(
            method,
            url,
            headers=headers,
            data=body_str if body else None,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        logger.warning("Coinbase API %s %s → %s: %s", method, path, resp.status_code, resp.text[:200])
        return None

    # ── Route 2: On-chain EVM transfer ────────────────────────

    def _payout_onchain(self, amount_usd: float, chain: str) -> Optional[dict]:
        """
        Transfer USDC (or native coin) to PAYOUT_ADDRESS on-chain.

        Works for:
          • Coinbase deposit addresses (ETH/USDC)
          • Cash App deposit addresses (if using ETH network on Cash App)
          • Any EVM-compatible address
        """
        if not self.bm:
            logger.warning("No blockchain manager for on-chain payout")
            return None

        target_chain = Config.PAYOUT_CHAIN or chain
        w3 = self.bm.get_web3(target_chain)
        if not w3:
            logger.warning("No Web3 connection for chain %s", target_chain)
            return None

        to_addr = Web3.to_checksum_address(Config.PAYOUT_ADDRESS)
        account = Account.from_key(Config.WALLET_PRIVATE_KEY)
        gas_price = w3.eth.gas_price

        payout_token = Config.PAYOUT_TOKEN.upper()

        try:
            if payout_token in ("ETH", "BNB", "MATIC", "NATIVE"):
                # Send native coin
                from nexus.protocols.dex_aggregator import PriceAggregator
                native_sym = {"ethereum": "ETH", "bsc": "BNB", "polygon": "MATIC"}.get(target_chain, "ETH")
                native_price = PriceAggregator.get_price(native_sym) or 2000.0
                amount_native = amount_usd / native_price
                amount_wei = int(amount_native * 10**18)

                # Keep a small reserve for gas
                reserve = int(0.005 * 10**18)
                if amount_wei <= reserve:
                    logger.warning("Payout amount too small after gas reserve")
                    return None

                tx = {
                    "from":     account.address,
                    "to":       to_addr,
                    "value":    amount_wei - reserve,
                    "nonce":    w3.eth.get_transaction_count(account.address),
                    "gasPrice": gas_price,
                    "gas":      21_000,
                    "chainId":  w3.eth.chain_id,
                }
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                hash_hex = tx_hash.hex()
                logger.info("On-chain native payout: %s → %s (%s)", hash_hex, to_addr, target_chain)
                return {
                    "success": True,
                    "method": "onchain_native",
                    "destination": to_addr,
                    "tx_ref": hash_hex,
                }

            else:
                # ERC-20 token transfer (default: USDC)
                token_addr = USDC_ADDRESSES.get(target_chain)
                if not token_addr:
                    logger.warning("No USDC address for chain %s", target_chain)
                    return None

                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(token_addr),
                    abi=ERC20_TRANSFER_ABI,
                )
                decimals = contract.functions.decimals().call()
                amount_token = int(amount_usd * 10**decimals)

                # Check balance
                balance = contract.functions.balanceOf(account.address).call()
                if balance < amount_token:
                    logger.warning(
                        "Insufficient %s balance: have %s, need %s",
                        payout_token, balance, amount_token,
                    )
                    return None

                tx = contract.functions.transfer(to_addr, amount_token).build_transaction({
                    "from":     account.address,
                    "nonce":    w3.eth.get_transaction_count(account.address),
                    "gasPrice": gas_price,
                    "gas":      65_000,
                })
                signed = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                hash_hex = tx_hash.hex()
                logger.info("On-chain USDC payout: %s → %s (%s)", hash_hex, to_addr, target_chain)
                return {
                    "success": True,
                    "method": "onchain_erc20",
                    "destination": to_addr,
                    "tx_ref": hash_hex,
                }

        except Exception as exc:
            logger.error("On-chain payout failed: %s", exc)
            return None

    # ── Route 3: Lightning / Cash App $cashtag ────────────────

    def _payout_lightning(self, amount_usd: float) -> Optional[dict]:
        """
        Resolve a Lightning address (e.g. $cashtag@cash.app or user@domain)
        via LNURL-pay and return the invoice for payment.

        NOTE: Paying the invoice requires a connected Lightning node.
        When no node is configured, this method logs the invoice so the
        user can pay it manually from any Lightning wallet.
        """
        ln_address = Config.PAYOUT_LIGHTNING_ADDRESS.strip()
        if not ln_address:
            return None

        # Normalise Cash App $cashtag → Lightning address format
        if ln_address.startswith("$"):
            ln_address = ln_address.lstrip("$") + "@cash.app"

        try:
            # Convert USD to satoshis (1 BTC ≈ dynamic price)
            from nexus.protocols.dex_aggregator import PriceAggregator
            btc_price = PriceAggregator.get_price("BTC") or 60_000.0
            amount_sats = int((amount_usd / btc_price) * 100_000_000)
            amount_msats = amount_sats * 1000

            # LNURL-pay: resolve Lightning address
            user, domain = ln_address.split("@", 1)
            lnurl_endpoint = f"https://{domain}/.well-known/lnurlp/{user}"
            resp = requests.get(lnurl_endpoint, timeout=10)
            resp.raise_for_status()
            lnurl_data = resp.json()

            min_msats = lnurl_data.get("minSendable", 1000)
            max_msats = lnurl_data.get("maxSendable", 100_000_000_000)
            callback   = lnurl_data.get("callback", "")

            if not min_msats <= amount_msats <= max_msats:
                logger.warning(
                    "Lightning amount %d msats out of range [%d, %d]",
                    amount_msats, min_msats, max_msats,
                )
                amount_msats = max(min_msats, min(amount_msats, max_msats))

            # Request invoice
            inv_resp = requests.get(
                callback,
                params={"amount": amount_msats, "comment": "Nexus AI reward"},
                timeout=10,
            )
            inv_resp.raise_for_status()
            inv_data = inv_resp.json()
            invoice = inv_data.get("pr", "")

            if not invoice:
                logger.warning("No Lightning invoice returned for %s", ln_address)
                return None

            logger.info(
                "Lightning invoice for $%.4f (→ %s sats) to %s:\n%s",
                amount_usd, amount_sats, ln_address, invoice,
            )

            # Attempt payment via configured Lightning node (if any)
            if Config.LIGHTNING_NODE_URL:
                paid = self._pay_lightning_invoice(invoice, amount_msats)
                if paid:
                    return {
                        "success": True,
                        "method": "lightning_paid",
                        "destination": ln_address,
                        "tx_ref": invoice[:60] + "…",
                    }

            # No node configured – store invoice for manual payment
            return {
                "success": True,
                "method": "lightning_invoice_pending",
                "destination": ln_address,
                "tx_ref": invoice[:80] + "…",
                "note": "Pay this invoice manually from any Lightning wallet",
            }

        except Exception as exc:
            logger.error("Lightning payout error: %s", exc)
            return None

    def _pay_lightning_invoice(self, invoice: str, amount_msats: int) -> bool:
        """
        Send payment via a self-hosted LND or LNbits node.
        Set LIGHTNING_NODE_URL and LIGHTNING_NODE_MACAROON (for LND)
        or LIGHTNING_NODE_API_KEY (for LNbits).
        """
        node_url = Config.LIGHTNING_NODE_URL.rstrip("/")
        headers: dict = {}
        payload: dict = {}

        if Config.LIGHTNING_NODE_MACAROON:
            # LND REST API
            headers["Grpc-Metadata-macaroon"] = Config.LIGHTNING_NODE_MACAROON
            payload = {"payment_request": invoice}
            endpoint = f"{node_url}/v1/channels/transactions"
        elif Config.LIGHTNING_NODE_API_KEY:
            # LNbits API
            headers["X-Api-Key"] = Config.LIGHTNING_NODE_API_KEY
            payload = {"bolt11": invoice, "out": True}
            endpoint = f"{node_url}/api/v1/payments"
        else:
            return False

        try:
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                logger.info("Lightning payment sent: %s", resp.json())
                return True
            logger.warning("Lightning payment failed: %s %s", resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            logger.error("Lightning node request failed: %s", exc)
            return False

    # ── Record keeping ────────────────────────────────────────

    def _record_result(
        self,
        amount_usd: float,
        method: str,
        destination: str,
        tx_ref: str,
        success: bool,
        error: str = "",
    ) -> dict:
        entry = {
            "timestamp": time.time(),
            "amount_usd": round(amount_usd, 4),
            "method": method,
            "destination": destination,
            "tx_ref": tx_ref,
            "success": success,
            "error": error,
        }
        with self._lock:
            if success:
                self._total_paid_usd += amount_usd
                self._payout_count += 1
                self._last_payout_ts = time.time()
            self._history.append(entry)

        log = logger.info if success else logger.warning
        log(
            "Payout [%s] $%.4f via %s → %s | success=%s%s",
            "DRY_RUN" if method == "dry_run" else "LIVE",
            amount_usd, method, destination or "n/a",
            success,
            f" ({error})" if error else "",
        )
        return entry

    # ── Helpers ───────────────────────────────────────────────

    def _active_mode(self) -> str:
        if Config.DRY_RUN:
            return "dry_run"
        if Config.COINBASE_API_KEY:
            return "coinbase_api"
        if Config.PAYOUT_ADDRESS:
            return "onchain"
        if Config.PAYOUT_LIGHTNING_ADDRESS:
            return "lightning"
        return "unconfigured"


# ── Singleton ─────────────────────────────────────────────────

_payout_manager: Optional[PayoutManager] = None


def get_payout_manager(blockchain_manager=None) -> PayoutManager:
    global _payout_manager
    if _payout_manager is None:
        _payout_manager = PayoutManager(blockchain_manager)
    return _payout_manager
