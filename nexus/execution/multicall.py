"""
Multicall3 Batch RPC for Nexus AI.

Aggregates many eth_call requests into a single RPC round-trip using the
Multicall3 contract (deployed at the same address on every EVM chain).

Reduces price-fetch latency by 10–50x versus sequential HTTP calls.
"""
from __future__ import annotations

from typing import Any

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Multicall3 — same address on every chain
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

MULTICALL3_ABI = [
    {
        "name": "aggregate3",
        "type": "function",
        "inputs": [
            {
                "name": "calls",
                "type": "tuple[]",
                "components": [
                    {"name": "target",       "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData",     "type": "bytes"},
                ],
            }
        ],
        "outputs": [
            {
                "name": "returnData",
                "type": "tuple[]",
                "components": [
                    {"name": "success",    "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
            }
        ],
        "stateMutability": "view",
    }
]


class MulticallClient:
    """Batch multiple eth_calls into one RPC request."""

    def __init__(self, w3: Web3):
        self.w3 = w3
        self._contract = w3.eth.contract(
            address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
            abi=MULTICALL3_ABI,
        )

    def call(self, calls: list[tuple[str, bytes]]) -> list[tuple[bool, bytes]]:
        """
        Execute a batch of calls.

        Args:
            calls: list of (target_address, encoded_call_data)

        Returns:
            list of (success, return_data) in matching order
        """
        if not calls:
            return []

        mc_calls = [
            {
                "target":       Web3.to_checksum_address(addr),
                "allowFailure": True,
                "callData":     data,
            }
            for addr, data in calls
        ]

        try:
            results = self._contract.functions.aggregate3(mc_calls).call()
            return [(r[0], r[1]) for r in results]
        except Exception as exc:
            logger.warning("Multicall3 failed: %s – falling back to sequential", exc)
            # Fallback: call each individually
            output = []
            for addr, data in calls:
                try:
                    result = self.w3.eth.call({"to": addr, "data": data})
                    output.append((True, result))
                except Exception as e:
                    logger.debug("Fallback call failed: %s", e)
                    output.append((False, b""))
            return output

    def get_erc20_balances(
        self, token_address: str, wallets: list[str]
    ) -> dict[str, int]:
        """
        Fetch ERC-20 balances for multiple wallets in one round-trip.
        Returns {wallet: balance_wei}.
        """
        # balanceOf(address) selector = 0x70a08231
        selector = bytes.fromhex("70a08231")
        calls = [
            (
                token_address,
                selector + bytes.fromhex("000000000000000000000000" + w[2:].lower()),
            )
            for w in wallets
        ]
        results = self.call(calls)
        return {
            w: int(results[i][1].hex(), 16) if results[i][0] and results[i][1] else 0
            for i, w in enumerate(wallets)
        }

    def get_token_prices_v2(
        self,
        router_address: str,
        router_abi: list,
        pairs: list[tuple[str, str]],
        amount_in: int = 10**18,
    ) -> dict[tuple, int]:
        """
        Fetch getAmountsOut for multiple pairs in one call.
        Returns {(token_in, token_out): amount_out}.
        """
        # Build encoded calls
        router = self.w3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=router_abi,
        )
        calls = []
        valid_pairs = []
        for token_in, token_out in pairs:
            try:
                data = router.encodeABI(
                    fn_name="getAmountsOut",
                    args=[
                        amount_in,
                        [
                            Web3.to_checksum_address(token_in),
                            Web3.to_checksum_address(token_out),
                        ],
                    ],
                )
                calls.append((router_address, data))
                valid_pairs.append((token_in, token_out))
            except Exception:
                continue

        if not calls:
            return {}

        results = self.call(calls)
        output: dict[tuple, int] = {}
        for i, (tok_in, tok_out) in enumerate(valid_pairs):
            success, ret = results[i]
            if success and ret:
                try:
                    decoded = self.w3.codec.decode(["uint256[]"], ret)
                    amounts = decoded[0]
                    output[(tok_in, tok_out)] = amounts[-1]
                except Exception:
                    pass
        return output
