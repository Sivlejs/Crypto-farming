"""
Uniswap V2 / V3 protocol adapter for Nexus AI.
Supports Ethereum, BSC (PancakeSwap), and Polygon (QuickSwap).
"""
from __future__ import annotations

from typing import Optional, Tuple

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Minimal ABIs ──────────────────────────────────────────────

UNISWAP_V2_ROUTER_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
    },
    {
        "name": "swapExactTokensForTokens",
        "type": "function",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "swapExactETHForTokens",
        "type": "function",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "payable",
    },
]

UNISWAP_V2_FACTORY_ABI = [
    {
        "name": "getPair",
        "type": "function",
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
        ],
        "outputs": [{"name": "pair", "type": "address"}],
        "stateMutability": "view",
    },
]

UNISWAP_V2_PAIR_ABI = [
    {
        "name": "getReserves",
        "type": "function",
        "inputs": [],
        "outputs": [
            {"name": "reserve0", "type": "uint112"},
            {"name": "reserve1", "type": "uint112"},
            {"name": "blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
    },
    {
        "name": "token0",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    },
    {
        "name": "token1",
        "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
    },
]

ERC20_ABI = [
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
    {
        "name": "approve",
        "type": "function",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "allowance",
        "type": "function",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]

# ── DEX router / factory addresses per chain ──────────────────

DEX_ADDRESSES: dict[str, dict] = {
    "ethereum": {
        "uniswap_v2_router": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        "uniswap_v2_factory": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "sushiswap_router": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
        "sushiswap_factory": "0xC0AEe478e3658e2610c5F7A4A2E1777cE9e4f2Ac",
        "weth": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "usdt": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "dai": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    },
    "bsc": {
        "pancakeswap_router": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
        "pancakeswap_factory": "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        "wbnb": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "usdt": "0x55d398326f99059fF775485246999027B3197955",
        "usdc": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "busd": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
    },
    "polygon": {
        "quickswap_router": "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
        "quickswap_factory": "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
        "sushiswap_router": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "sushiswap_factory": "0xc35DADB65012eC5796536bD9864eD8773aBc74C4",
        "wmatic": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        "usdc": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "usdt": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "dai": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
    },
}


class UniswapV2Client:
    """Lightweight Uniswap-V2-compatible DEX client."""

    def __init__(self, w3: Web3, router_address: str, factory_address: str):
        self.w3 = w3
        self.router = w3.eth.contract(
            address=Web3.to_checksum_address(router_address),
            abi=UNISWAP_V2_ROUTER_ABI,
        )
        self.factory = w3.eth.contract(
            address=Web3.to_checksum_address(factory_address),
            abi=UNISWAP_V2_FACTORY_ABI,
        )

    def get_amounts_out(
        self, amount_in_wei: int, path: list[str]
    ) -> Optional[list[int]]:
        """Return output amounts for a given input along a token path."""
        try:
            checksum_path = [Web3.to_checksum_address(t) for t in path]
            return self.router.functions.getAmountsOut(
                amount_in_wei, checksum_path
            ).call()
        except Exception as exc:
            logger.debug("getAmountsOut failed: %s", exc)
            return None

    def get_price(
        self, token_in: str, token_out: str, amount_in: float = 1.0
    ) -> Optional[float]:
        """Return how many token_out units 1 token_in fetches (human-readable)."""
        try:
            token_in_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_in), abi=ERC20_ABI
            )
            token_out_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_out), abi=ERC20_ABI
            )
            dec_in = token_in_contract.functions.decimals().call()
            dec_out = token_out_contract.functions.decimals().call()

            amount_in_wei = int(amount_in * (10**dec_in))
            amounts = self.get_amounts_out(amount_in_wei, [token_in, token_out])
            if not amounts:
                return None
            return amounts[-1] / (10**dec_out)
        except Exception as exc:
            logger.debug("get_price failed: %s", exc)
            return None

    def get_reserves(
        self, token_a: str, token_b: str
    ) -> Optional[Tuple[int, int]]:
        """Return pool reserves (token_a_reserve, token_b_reserve)."""
        try:
            pair_addr = self.factory.functions.getPair(
                Web3.to_checksum_address(token_a),
                Web3.to_checksum_address(token_b),
            ).call()
            if pair_addr == "0x" + "0" * 40:
                return None
            pair = self.w3.eth.contract(
                address=Web3.to_checksum_address(pair_addr),
                abi=UNISWAP_V2_PAIR_ABI,
            )
            reserves = pair.functions.getReserves().call()
            token0 = pair.functions.token0().call()
            if token0.lower() == token_a.lower():
                return reserves[0], reserves[1]
            return reserves[1], reserves[0]
        except Exception as exc:
            logger.debug("get_reserves failed: %s", exc)
            return None
