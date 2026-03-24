"""
Multi-DEX price aggregator for Nexus AI.
Fetches prices from multiple sources and identifies discrepancies.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── CoinGecko price feed ──────────────────────────────────────

COINGECKO_IDS: dict[str, str] = {
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "MATIC": "matic-network",
    "BTC": "bitcoin",
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "AAVE": "aave",
    "UNI": "uniswap",
    "SUSHI": "sushi",
    "CAKE": "pancakeswap-token",
    "LINK": "chainlink",
}

# On-chain token addresses used for DEX price queries
# format: { chain: { symbol: address } }
TOKEN_ADDRESSES: dict[str, dict[str, str]] = {
    "ethereum": {
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    },
    "bsc": {
        "WBNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
        "BUSD": "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56",
        "WETH": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8",
    },
    "polygon": {
        "WMATIC": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",
        "USDC": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
        "USDT": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        "DAI": "0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063",
        "WETH": "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619",
    },
}

# ── Price Aggregator ──────────────────────────────────────────

class PriceAggregator:
    """Fetches and caches token prices from CoinGecko and on-chain DEX pools."""

    _cache: dict[str, tuple[float, float]] = {}  # symbol -> (price_usd, timestamp)
    CACHE_TTL = 30  # seconds

    @classmethod
    def get_prices_coingecko(cls, symbols: List[str]) -> Dict[str, float]:
        """Fetch prices in USD from CoinGecko API."""
        ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
        if not ids:
            return {}

        now = time.time()
        # Return cached values if fresh enough
        all_cached = {
            s: cls._cache[s][0]
            for s in symbols
            if s in cls._cache and now - cls._cache[s][1] < cls.CACHE_TTL
        }
        if len(all_cached) == len(symbols):
            return all_cached

        try:
            base = "https://pro-api.coingecko.com" if Config.COINGECKO_API_KEY else "https://api.coingecko.com"
            headers = {}
            if Config.COINGECKO_API_KEY:
                headers["x-cg-pro-api-key"] = Config.COINGECKO_API_KEY

            resp = requests.get(
                f"{base}/api/v3/simple/price",
                params={"ids": ",".join(ids), "vs_currencies": "usd"},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            prices: Dict[str, float] = {}
            for symbol in symbols:
                cg_id = COINGECKO_IDS.get(symbol)
                if cg_id and cg_id in data:
                    price = float(data[cg_id]["usd"])
                    prices[symbol] = price
                    cls._cache[symbol] = (price, now)
            return prices
        except Exception as exc:
            logger.warning("CoinGecko price fetch failed: %s", exc)
            # Return stale cache if available
            return {s: cls._cache[s][0] for s in symbols if s in cls._cache}

    @classmethod
    def get_price(cls, symbol: str) -> Optional[float]:
        prices = cls.get_prices_coingecko([symbol])
        return prices.get(symbol)

    @classmethod
    def get_all_prices(cls) -> Dict[str, float]:
        return cls.get_prices_coingecko(list(COINGECKO_IDS.keys()))

    @classmethod
    def get_yield_rates(cls) -> List[dict]:
        """
        Fetch DeFi yield rates from DeFi Llama.
        Returns list of {protocol, chain, pool, apy, tvl_usd}.
        """
        try:
            resp = requests.get(
                "https://yields.llama.fi/pools",
                timeout=15,
            )
            resp.raise_for_status()
            pools = resp.json().get("data", [])

            # Filter for well-known, high-TVL pools
            result = []
            for p in pools:
                if (
                    p.get("tvlUsd", 0) > 100_000
                    and p.get("apy") is not None
                    and p.get("apy", 0) > 0
                ):
                    result.append(
                        {
                            "pool_id": p.get("pool", ""),
                            "protocol": p.get("project", ""),
                            "chain": p.get("chain", "").lower(),
                            "symbol": p.get("symbol", ""),
                            "apy": round(float(p.get("apy", 0)), 2),
                            "tvl_usd": float(p.get("tvlUsd", 0)),
                            "apy_reward": round(float(p.get("apyReward") or 0), 2),
                            "apy_base": round(float(p.get("apyBase") or 0), 2),
                        }
                    )
            result.sort(key=lambda x: x["apy"], reverse=True)
            return result[:50]  # Top 50
        except Exception as exc:
            logger.warning("DeFi Llama yield fetch failed: %s", exc)
            return []
