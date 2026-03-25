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

# Retry configuration for API calls
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds, exponential backoff: 2, 4, 8...

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
    _yield_cache: tuple[List[dict], float] = ([], 0.0)  # (pools, timestamp) for yield rates
    CACHE_TTL = 30  # seconds
    YIELD_CACHE_TTL = 300  # 5 minutes for yield data (DeFi Llama updates less frequently)

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
        Fetch DeFi yield rates from multiple sources with fallback logic.
        Returns list of {protocol, chain, pool, apy, tvl_usd}.
        """
        now = time.time()
        
        # Check if we have a fresh cache
        cached_pools, cache_time = cls._yield_cache
        if cached_pools and (now - cache_time) < cls.YIELD_CACHE_TTL:
            logger.debug("Using cached yield rates (%d pools)", len(cached_pools))
            return cached_pools

        # Try multi-source fetcher first
        try:
            from nexus.protocols.pool_sources import get_pool_fetcher
            fetcher = get_pool_fetcher()
            pool_data_list = fetcher.fetch_all_pools()
            
            if pool_data_list:
                result = []
                for p in pool_data_list:
                    if p.tvl_usd > 50_000 and p.apy_total > 0:
                        result.append({
                            "pool_id": p.pool_id,
                            "protocol": p.protocol,
                            "chain": p.chain,
                            "symbol": p.symbol,
                            "apy": round(p.apy_total, 2),
                            "tvl_usd": p.tvl_usd,
                            "apy_reward": round(p.apy_reward, 2),
                            "apy_base": round(p.apy_base, 2),
                        })
                
                result.sort(key=lambda x: x["apy"], reverse=True)
                result = result[:100]  # Top 100 pools
                
                # Update cache on success
                cls._yield_cache = (result, now)
                
                source_status = fetcher.get_source_status()
                active_sources = sum(1 for v in source_status.values() if v)
                logger.info("Fetched %d yield pools from %d sources", len(result), active_sources)
                return result
        except Exception as exc:
            logger.warning("Multi-source fetcher failed: %s. Falling back to DeFi Llama.", exc)

        # Fallback to DeFi Llama only
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(
                    "https://yields.llama.fi/pools",
                    timeout=15,
                )
                resp.raise_for_status()
                pools = resp.json().get("data", [])

                # Filter for well-known, high-TVL pools with positive APY
                result = []
                for p in pools:
                    tvl = p.get("tvlUsd", 0)
                    apy = p.get("apy")
                    if tvl > 50_000 and apy is not None and apy > 0:
                        result.append(
                            {
                                "pool_id": p.get("pool", ""),
                                "protocol": p.get("project", ""),
                                "chain": p.get("chain", "").lower(),
                                "symbol": p.get("symbol", ""),
                                "apy": round(float(apy), 2),
                                "tvl_usd": float(tvl),
                                "apy_reward": round(float(p.get("apyReward") or 0), 2),
                                "apy_base": round(float(p.get("apyBase") or 0), 2),
                            }
                        )
                result.sort(key=lambda x: x["apy"], reverse=True)
                result = result[:100]  # Top 100 pools for more opportunities
                
                # Update cache on success
                cls._yield_cache = (result, now)
                logger.info("Fetched %d yield pools from DeFi Llama (fallback)", len(result))
                return result
                
            except Exception as exc:
                last_error = exc
                retry_delay_seconds = RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "DeFi Llama yield fetch failed (attempt %d/%d): %s. Retrying in %ds...",
                    attempt + 1, MAX_RETRIES, exc, retry_delay_seconds
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(retry_delay_seconds)
        
        # All retries failed - use stale cache if available
        if cached_pools:
            logger.warning(
                "DeFi Llama API unavailable after %d retries. Using stale cache (%d pools, %.0fs old)",
                MAX_RETRIES, len(cached_pools), now - cache_time
            )
            return cached_pools
        
        # No cache available
        logger.error("Yield fetch failed after %d retries with no cache: %s", MAX_RETRIES, last_error)
        return []
