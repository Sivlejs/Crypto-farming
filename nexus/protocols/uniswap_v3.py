"""
Uniswap V3 Protocol Adapter for Nexus AI.

Provides:
  • QuoterV2  – off-chain price quotes without state changes
  • SwapRouter02 – single and multi-hop swaps
  • Pool data – tick/liquidity/fee tier lookup
"""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Deployed addresses ────────────────────────────────────────

V3_ADDRESSES: dict[str, dict] = {
    "ethereum": {
        "factory":        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "quoter_v2":      "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
        "swap_router02":  "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
        "nft_manager":    "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    },
    "polygon": {
        "factory":        "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "quoter_v2":      "0x61fFE014bA17989E743c5F6cB21bF9697530B21e",
        "swap_router02":  "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45",
        "nft_manager":    "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
    },
}

# Common fee tiers in bps×100 (0.01% = 100, 0.05% = 500, 0.3% = 3000, 1% = 10000)
FEE_TIERS = [100, 500, 3000, 10000]

# ── ABIs ──────────────────────────────────────────────────────

QUOTER_V2_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tokenIn",            "type": "address"},
                    {"name": "tokenOut",           "type": "address"},
                    {"name": "amountIn",           "type": "uint256"},
                    {"name": "fee",                "type": "uint24"},
                    {"name": "sqrtPriceLimitX96",  "type": "uint160"},
                ],
            }
        ],
        "outputs": [
            {"name": "amountOut",            "type": "uint256"},
            {"name": "sqrtPriceX96After",    "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate",          "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
    },
    {
        "name": "quoteExactInput",
        "type": "function",
        "inputs": [
            {"name": "path",     "type": "bytes"},
            {"name": "amountIn", "type": "uint256"},
        ],
        "outputs": [
            {"name": "amountOut",              "type": "uint256"},
            {"name": "sqrtPriceX96AfterList",  "type": "uint160[]"},
            {"name": "initializedTicksCrossedList", "type": "uint32[]"},
            {"name": "gasEstimate",            "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
    },
]

SWAP_ROUTER_ABI = [
    {
        "name": "exactInputSingle",
        "type": "function",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tokenIn",           "type": "address"},
                    {"name": "tokenOut",          "type": "address"},
                    {"name": "fee",               "type": "uint24"},
                    {"name": "recipient",         "type": "address"},
                    {"name": "amountIn",          "type": "uint256"},
                    {"name": "amountOutMinimum",  "type": "uint256"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
            }
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
    },
    {
        "name": "exactInput",
        "type": "function",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "path",             "type": "bytes"},
                    {"name": "recipient",        "type": "address"},
                    {"name": "amountIn",         "type": "uint256"},
                    {"name": "amountOutMinimum", "type": "uint256"},
                ],
            }
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
    },
]

FACTORY_ABI = [
    {
        "name": "getPool",
        "type": "function",
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "fee",    "type": "uint24"},
        ],
        "outputs": [{"name": "pool", "type": "address"}],
        "stateMutability": "view",
    },
]


def _encode_v3_path(tokens: list[str], fees: list[int]) -> bytes:
    """Encode a multi-hop path for Uniswap V3 (packed bytes)."""
    assert len(tokens) == len(fees) + 1
    result = bytes.fromhex(tokens[0][2:])
    for i, fee in enumerate(fees):
        result += fee.to_bytes(3, "big")
        result += bytes.fromhex(tokens[i + 1][2:])
    return result


class UniswapV3Client:
    """Uniswap V3 quoter + router client."""

    def __init__(self, w3: Web3, chain: str):
        self.w3 = w3
        self.chain = chain
        addrs = V3_ADDRESSES.get(chain, {})

        q_addr = addrs.get("quoter_v2")
        self.quoter = (
            w3.eth.contract(
                address=Web3.to_checksum_address(q_addr), abi=QUOTER_V2_ABI
            )
            if q_addr else None
        )

        r_addr = addrs.get("swap_router02")
        self.router = (
            w3.eth.contract(
                address=Web3.to_checksum_address(r_addr), abi=SWAP_ROUTER_ABI
            )
            if r_addr else None
        )

        f_addr = addrs.get("factory")
        self.factory = (
            w3.eth.contract(
                address=Web3.to_checksum_address(f_addr), abi=FACTORY_ABI
            )
            if f_addr else None
        )

    def is_available(self) -> bool:
        return self.quoter is not None

    def get_best_quote(
        self, token_in: str, token_out: str, amount_in_wei: int
    ) -> Optional[tuple[int, int]]:
        """
        Try all fee tiers, return (best_amount_out, best_fee) or None.
        Uses call() not send() – no gas consumed.
        """
        if not self.quoter:
            return None

        best_out = 0
        best_fee = 0

        for fee in FEE_TIERS:
            try:
                result = self.quoter.functions.quoteExactInputSingle({
                    "tokenIn":           Web3.to_checksum_address(token_in),
                    "tokenOut":          Web3.to_checksum_address(token_out),
                    "amountIn":          amount_in_wei,
                    "fee":               fee,
                    "sqrtPriceLimitX96": 0,
                }).call()
                amount_out = result[0]
                if amount_out > best_out:
                    best_out = amount_out
                    best_fee = fee
            except Exception:
                continue

        return (best_out, best_fee) if best_out > 0 else None

    def get_price(
        self, token_in: str, token_out: str, amount_in: float, decimals_in: int
    ) -> Optional[float]:
        """Human-readable price: how much token_out per 1 token_in."""
        amount_wei = int(amount_in * 10**decimals_in)
        result = self.get_best_quote(
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            amount_wei,
        )
        if not result:
            return None
        # Caller must normalise by decimals_out
        return result[0]

    def build_swap_tx(
        self,
        token_in: str,
        token_out: str,
        amount_in_wei: int,
        amount_out_min_wei: int,
        fee: int,
        recipient: str,
        deadline: int,
        from_addr: str,
        nonce: int,
        gas_params: dict,
    ) -> Optional[dict]:
        """Build an exactInputSingle swap transaction."""
        if not self.router:
            return None
        try:
            return self.router.functions.exactInputSingle({
                "tokenIn":           Web3.to_checksum_address(token_in),
                "tokenOut":          Web3.to_checksum_address(token_out),
                "fee":               fee,
                "recipient":         Web3.to_checksum_address(recipient),
                "amountIn":          amount_in_wei,
                "amountOutMinimum":  amount_out_min_wei,
                "sqrtPriceLimitX96": 0,
            }).build_transaction({
                "from":  Web3.to_checksum_address(from_addr),
                "nonce": nonce,
                "gas":   250_000,
                **gas_params,
            })
        except Exception as exc:
            logger.error("build_swap_tx V3: %s", exc)
            return None
