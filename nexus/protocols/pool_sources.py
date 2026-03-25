"""
Multi-Source Pool Fetcher for Nexus AI.

Aggregates pool data from multiple DeFi APIs and protocols to maximize
pool discovery across all supported chains. Provides fallback mechanisms
when individual sources fail.

Sources:
  - DeFi Llama (yields.llama.fi) - primary aggregate source
  - Curve Finance API - Curve pools across all chains
  - Uniswap V3 Subgraph - concentrated liquidity pools
  - Balancer API - weighted pools
  - 1inch - pool aggregation from multiple DEXes
  - Hardcoded fallback pools - guaranteed pools that always work

Usage:
    fetcher = get_pool_fetcher()
    pools = fetcher.fetch_all_pools()
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────

# Retry settings - using exponential backoff like successful mining programs
MAX_RETRIES = 4
RETRY_BACKOFF_DELAYS = [1, 2, 4, 8]  # seconds - exponential backoff

# Cache TTL in seconds
CACHE_TTL = 300  # 5 minutes

# Persistent cache file for offline/failure resilience
PERSISTENT_CACHE_FILE = Path(os.getenv("POOL_CACHE_FILE", "/tmp/nexus_pools_cache.json"))

# Minimum TVL to include a pool (USD) - configurable via environment
MIN_TVL_USD = float(os.getenv("MIN_TVL_USD", "10000"))  # Lowered from 50k to 10k

# Supported chains for filtering
SUPPORTED_CHAINS = {
    "ethereum", "arbitrum", "optimism", "base", "polygon", "bsc", "avalanche", "fantom", "gnosis"
}

# Chain name normalization mapping - comprehensive to handle all API variations
CHAIN_ALIASES: Dict[str, str] = {
    # Ethereum - multiple naming conventions across APIs
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "Ethereum": "ethereum",
    "ETH": "ethereum",
    "ethereum-mainnet": "ethereum",
    "eth-mainnet": "ethereum",
    # Arbitrum
    "arbitrum": "arbitrum",
    "arbitrum_one": "arbitrum",
    "arbitrum-one": "arbitrum",
    "Arbitrum": "arbitrum",
    "arb": "arbitrum",
    "ARB": "arbitrum",
    "arbitrum-mainnet": "arbitrum",
    # Optimism
    "optimism": "optimism",
    "op": "optimism",
    "OP": "optimism",
    "Optimism": "optimism",
    "optimistic": "optimism",
    "op-mainnet": "optimism",
    # Base
    "base": "base",
    "Base": "base",
    "BASE": "base",
    "base-mainnet": "base",
    # Polygon
    "polygon": "polygon",
    "matic": "polygon",
    "polygon_pos": "polygon",
    "Polygon": "polygon",
    "POLYGON": "polygon",
    "polygon-pos": "polygon",
    "polygon-mainnet": "polygon",
    # BSC / Binance Smart Chain
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "binance-smart-chain": "bsc",
    "BSC": "bsc",
    "BNB": "bsc",
    "Binance": "bsc",
    "bsc-mainnet": "bsc",
    "bnb-chain": "bsc",
    # Avalanche
    "avalanche": "avalanche",
    "avax": "avalanche",
    "avalanche_c": "avalanche",
    "Avalanche": "avalanche",
    "AVAX": "avalanche",
    "avalanche-c": "avalanche",
    "avax-mainnet": "avalanche",
    # Fantom
    "fantom": "fantom",
    "ftm": "fantom",
    "Fantom": "fantom",
    "FTM": "fantom",
    "fantom-mainnet": "fantom",
    # Gnosis
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "Gnosis": "gnosis",
    "gnosis-chain": "gnosis",
    "gno": "gnosis",
}


# ── Hardcoded Fallback Pools ──────────────────────────────────
# These are well-known, high-TVL pools that almost always exist.
# Used as guaranteed fallback when all API sources fail.
# Modeled after how mining programs (XMRig, CGMiner) have default pool configs.

FALLBACK_POOLS: List[Dict[str, Any]] = [
    # Ethereum - Major DEXes
    {
        "pool_id": "fallback-uniswap-v3-eth-usdc",
        "protocol": "uniswap-v3",
        "chain": "ethereum",
        "symbol": "ETH-USDC",
        "apy_total": 15.0,
        "apy_base": 15.0,
        "tvl_usd": 500_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["ETH", "USDC"],
        "url": "https://app.uniswap.org/pools",
    },
    {
        "pool_id": "fallback-uniswap-v3-wbtc-eth",
        "protocol": "uniswap-v3",
        "chain": "ethereum",
        "symbol": "WBTC-ETH",
        "apy_total": 8.0,
        "apy_base": 8.0,
        "tvl_usd": 300_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["WBTC", "ETH"],
        "url": "https://app.uniswap.org/pools",
    },
    {
        "pool_id": "fallback-curve-3pool",
        "protocol": "curve-dex",
        "chain": "ethereum",
        "symbol": "DAI-USDC-USDT",
        "apy_total": 3.5,
        "apy_base": 2.0,
        "apy_reward": 1.5,
        "tvl_usd": 800_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["DAI", "USDC", "USDT"],
        "reward_tokens": ["CRV"],
        "url": "https://curve.fi/#/ethereum/pools/3pool",
    },
    {
        "pool_id": "fallback-aave-v3-usdc",
        "protocol": "aave-v3",
        "chain": "ethereum",
        "symbol": "USDC",
        "apy_total": 4.5,
        "apy_base": 4.5,
        "tvl_usd": 1_500_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "url": "https://app.aave.com/reserve-overview/?underlyingAsset=usdc",
    },
    {
        "pool_id": "fallback-aave-v3-eth",
        "protocol": "aave-v3",
        "chain": "ethereum",
        "symbol": "WETH",
        "apy_total": 2.0,
        "apy_base": 2.0,
        "tvl_usd": 2_000_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["WETH"],
        "url": "https://app.aave.com/reserve-overview/?underlyingAsset=weth",
    },
    # Polygon - Major pools
    {
        "pool_id": "fallback-quickswap-eth-matic",
        "protocol": "quickswap",
        "chain": "polygon",
        "symbol": "WETH-MATIC",
        "apy_total": 25.0,
        "apy_base": 10.0,
        "apy_reward": 15.0,
        "tvl_usd": 50_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["WETH", "MATIC"],
        "reward_tokens": ["QUICK"],
        "url": "https://quickswap.exchange/#/pool",
    },
    {
        "pool_id": "fallback-aave-v3-polygon-usdc",
        "protocol": "aave-v3",
        "chain": "polygon",
        "symbol": "USDC",
        "apy_total": 5.5,
        "apy_base": 5.5,
        "tvl_usd": 200_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "url": "https://app.aave.com/",
    },
    # Arbitrum - Major pools
    {
        "pool_id": "fallback-uniswap-v3-arb-eth-usdc",
        "protocol": "uniswap-v3",
        "chain": "arbitrum",
        "symbol": "ETH-USDC",
        "apy_total": 20.0,
        "apy_base": 20.0,
        "tvl_usd": 150_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["ETH", "USDC"],
        "url": "https://app.uniswap.org/pools",
    },
    {
        "pool_id": "fallback-gmx-glp",
        "protocol": "gmx",
        "chain": "arbitrum",
        "symbol": "GLP",
        "apy_total": 30.0,
        "apy_base": 15.0,
        "apy_reward": 15.0,
        "tvl_usd": 400_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["ETH", "BTC", "USDC", "USDT", "DAI"],
        "reward_tokens": ["ETH", "esGMX"],
        "url": "https://app.gmx.io/#/earn",
    },
    {
        "pool_id": "fallback-aave-v3-arb-usdc",
        "protocol": "aave-v3",
        "chain": "arbitrum",
        "symbol": "USDC",
        "apy_total": 6.0,
        "apy_base": 6.0,
        "tvl_usd": 300_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "url": "https://app.aave.com/",
    },
    # BSC - Major pools
    {
        "pool_id": "fallback-pancakeswap-bnb-busd",
        "protocol": "pancakeswap",
        "chain": "bsc",
        "symbol": "BNB-BUSD",
        "apy_total": 18.0,
        "apy_base": 8.0,
        "apy_reward": 10.0,
        "tvl_usd": 100_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["BNB", "BUSD"],
        "reward_tokens": ["CAKE"],
        "url": "https://pancakeswap.finance/liquidity",
    },
    {
        "pool_id": "fallback-pancakeswap-cake-bnb",
        "protocol": "pancakeswap",
        "chain": "bsc",
        "symbol": "CAKE-BNB",
        "apy_total": 35.0,
        "apy_base": 15.0,
        "apy_reward": 20.0,
        "tvl_usd": 80_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["CAKE", "BNB"],
        "reward_tokens": ["CAKE"],
        "url": "https://pancakeswap.finance/liquidity",
    },
    {
        "pool_id": "fallback-venus-usdc",
        "protocol": "venus",
        "chain": "bsc",
        "symbol": "USDC",
        "apy_total": 5.0,
        "apy_base": 3.5,
        "apy_reward": 1.5,
        "tvl_usd": 150_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "reward_tokens": ["XVS"],
        "url": "https://app.venus.io/",
    },
    # Optimism - Major pools
    {
        "pool_id": "fallback-velodrome-eth-usdc",
        "protocol": "velodrome",
        "chain": "optimism",
        "symbol": "ETH-USDC",
        "apy_total": 25.0,
        "apy_base": 10.0,
        "apy_reward": 15.0,
        "tvl_usd": 60_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["ETH", "USDC"],
        "reward_tokens": ["VELO"],
        "url": "https://velodrome.finance/liquidity",
    },
    {
        "pool_id": "fallback-aave-v3-op-usdc",
        "protocol": "aave-v3",
        "chain": "optimism",
        "symbol": "USDC",
        "apy_total": 5.0,
        "apy_base": 5.0,
        "tvl_usd": 100_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "url": "https://app.aave.com/",
    },
    # Base - Major pools
    {
        "pool_id": "fallback-aerodrome-eth-usdc",
        "protocol": "aerodrome",
        "chain": "base",
        "symbol": "ETH-USDC",
        "apy_total": 30.0,
        "apy_base": 12.0,
        "apy_reward": 18.0,
        "tvl_usd": 80_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["ETH", "USDC"],
        "reward_tokens": ["AERO"],
        "url": "https://aerodrome.finance/liquidity",
    },
    # Avalanche - Major pools
    {
        "pool_id": "fallback-traderjoe-avax-usdc",
        "protocol": "trader-joe",
        "chain": "avalanche",
        "symbol": "AVAX-USDC",
        "apy_total": 22.0,
        "apy_base": 10.0,
        "apy_reward": 12.0,
        "tvl_usd": 40_000_000,
        "source": "fallback",
        "stablecoin": False,
        "exposure": ["AVAX", "USDC"],
        "reward_tokens": ["JOE"],
        "url": "https://traderjoexyz.com/avalanche/pool",
    },
    {
        "pool_id": "fallback-aave-v3-avax-usdc",
        "protocol": "aave-v3",
        "chain": "avalanche",
        "symbol": "USDC",
        "apy_total": 5.5,
        "apy_base": 5.5,
        "tvl_usd": 120_000_000,
        "source": "fallback",
        "stablecoin": True,
        "exposure": ["USDC"],
        "url": "https://app.aave.com/",
    },
]


def _get_fallback_pools() -> List[PoolData]:
    """
    Get hardcoded fallback pools as PoolData objects.
    
    These are well-established pools that should always be available.
    Used when all API sources fail - similar to how mining software
    has default pools configured.
    """
    pools = []
    for p in FALLBACK_POOLS:
        try:
            pools.append(PoolData(
                pool_id=p["pool_id"],
                protocol=p["protocol"],
                chain=p["chain"],
                symbol=p["symbol"],
                apy_total=p["apy_total"],
                apy_base=p.get("apy_base", p["apy_total"]),
                apy_reward=p.get("apy_reward", 0.0),
                tvl_usd=p["tvl_usd"],
                source=p.get("source", "fallback"),
                stablecoin=p.get("stablecoin", False),
                exposure=p.get("exposure", []),
                reward_tokens=p.get("reward_tokens", []),
                url=p.get("url", ""),
            ))
        except Exception as exc:
            logger.debug("Failed to parse fallback pool: %s", exc)
    return pools


@dataclass
class PoolData:
    """Unified pool data structure across all sources."""
    pool_id: str
    protocol: str
    chain: str
    symbol: str
    apy_total: float
    apy_base: float = 0.0
    apy_reward: float = 0.0
    tvl_usd: float = 0.0
    source: str = ""  # which API provided this pool
    stablecoin: bool = False
    exposure: List[str] = field(default_factory=list)
    reward_tokens: List[str] = field(default_factory=list)
    url: str = ""  # link to pool page
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "pool_id": self.pool_id,
            "protocol": self.protocol,
            "chain": self.chain,
            "symbol": self.symbol,
            "apy": round(self.apy_total, 2),
            "apy_base": round(self.apy_base, 2),
            "apy_reward": round(self.apy_reward, 2),
            "tvl_usd": round(self.tvl_usd, 2),
            "source": self.source,
            "stablecoin": self.stablecoin,
            "exposure": self.exposure,
            "reward_tokens": self.reward_tokens,
            "url": self.url,
            "last_updated": self.last_updated,
        }


def _normalize_chain(chain: str) -> Optional[str]:
    """Normalize chain name to our standard format."""
    if not chain:
        return None
    normalized = CHAIN_ALIASES.get(chain.lower().strip())
    if normalized and normalized in SUPPORTED_CHAINS:
        return normalized
    return None


def _safe_float(value, default: float = 0.0) -> float:
    """Safely convert to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _request_with_retry(
    url: str,
    params: Optional[dict] = None,
    timeout: int = 15,
    headers: Optional[dict] = None,
) -> Optional[dict]:
    """
    Make HTTP request with retry logic and exponential backoff.
    
    Uses delays [1s, 2s, 4s, 8s] like successful cryptocurrency applications.
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout, headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            last_error = exc
            # Handle rate limiting specifically
            if exc.response is not None and exc.response.status_code == 429:
                # Retry-After can be seconds (int) or HTTP date - handle both
                retry_header = exc.response.headers.get("Retry-After", "")
                try:
                    retry_after = int(retry_header)
                except (ValueError, TypeError):
                    retry_after = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)] * 2
                logger.warning("Rate limited by %s. Waiting %ds", url, retry_after)
                time.sleep(retry_after)
                continue
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)]
                logger.debug(
                    "HTTP error from %s (attempt %d/%d): %s. Retrying in %ds",
                    url, attempt + 1, MAX_RETRIES, exc, delay
                )
                time.sleep(delay)
        except requests.exceptions.Timeout as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)]
                logger.debug(
                    "Timeout from %s (attempt %d/%d). Retrying in %ds",
                    url, attempt + 1, MAX_RETRIES, delay
                )
                time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)]
                logger.debug(
                    "Request to %s failed (attempt %d/%d): %s. Retrying in %ds",
                    url, attempt + 1, MAX_RETRIES, exc, delay
                )
                time.sleep(delay)
    logger.warning("Request to %s failed after %d retries: %s", url, MAX_RETRIES, last_error)
    return None


def _graphql_request_with_retry(
    endpoint: str,
    query: str,
    timeout: int = 15,
) -> Optional[dict]:
    """
    Make GraphQL request with retry logic and exponential backoff.
    
    Handles The Graph API specifics including rate limiting.
    """
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                endpoint,
                json={"query": query},
                timeout=timeout,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            last_error = exc
            # Handle rate limiting for The Graph
            if exc.response is not None and exc.response.status_code in (429, 503):
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)] * 2
                logger.warning("Graph API rate limited/unavailable. Waiting %ds", delay)
                time.sleep(delay)
                continue
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)]
                logger.debug(
                    "GraphQL error from %s (attempt %d/%d): %s. Retrying in %ds",
                    endpoint, attempt + 1, MAX_RETRIES, exc, delay
                )
                time.sleep(delay)
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_DELAYS[min(attempt, len(RETRY_BACKOFF_DELAYS) - 1)]
                logger.debug(
                    "GraphQL request to %s failed (attempt %d/%d): %s. Retrying in %ds",
                    endpoint, attempt + 1, MAX_RETRIES, exc, delay
                )
                time.sleep(delay)
    logger.warning("GraphQL request to %s failed after %d retries: %s", endpoint, MAX_RETRIES, last_error)
    return None


# ── Persistent Cache Functions ────────────────────────────────

def _save_pools_to_disk(pools: List[PoolData]) -> bool:
    """
    Save pools to persistent disk cache.
    
    This ensures we always have a fallback if all APIs are unreachable,
    similar to how mining programs cache pool configurations.
    """
    try:
        cache_data = {
            "timestamp": time.time(),
            "pools": [p.to_dict() for p in pools],
        }
        PERSISTENT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PERSISTENT_CACHE_FILE, "w") as f:
            json.dump(cache_data, f)
        logger.debug("Saved %d pools to disk cache at %s", len(pools), PERSISTENT_CACHE_FILE)
        return True
    except Exception as exc:
        logger.debug("Failed to save pools to disk: %s", exc)
        return False


def _load_pools_from_disk(max_age_hours: int = 48) -> List[PoolData]:
    """
    Load pools from persistent disk cache.
    
    Returns cached pools if they exist and aren't too old.
    
    Args:
        max_age_hours: Maximum age of cache in hours before it's considered stale.
                       Default is 48 hours to allow recovery from extended API outages.
    """
    try:
        if not PERSISTENT_CACHE_FILE.exists():
            return []
        
        with open(PERSISTENT_CACHE_FILE) as f:
            cache_data = json.load(f)
        
        # Check age
        timestamp = cache_data.get("timestamp", 0)
        age_hours = (time.time() - timestamp) / 3600
        
        if age_hours > max_age_hours:
            logger.debug("Disk cache is %d hours old (max: %d), ignoring", int(age_hours), max_age_hours)
            return []
        
        pools = []
        for p in cache_data.get("pools", []):
            try:
                # Handle both 'apy' (from to_dict) and 'apy_total' (legacy) field names
                apy_total = p.get("apy_total", p.get("apy", 0))
                pools.append(PoolData(
                    pool_id=p["pool_id"],
                    protocol=p["protocol"],
                    chain=p["chain"],
                    symbol=p["symbol"],
                    apy_total=apy_total,
                    apy_base=p.get("apy_base", 0),
                    apy_reward=p.get("apy_reward", 0),
                    tvl_usd=p.get("tvl_usd", 0),
                    source=p.get("source", "disk_cache"),
                    stablecoin=p.get("stablecoin", False),
                    exposure=p.get("exposure", []),
                    reward_tokens=p.get("reward_tokens", []),
                    url=p.get("url", ""),
                    last_updated=timestamp,
                ))
            except Exception:
                continue
        
        logger.info("Loaded %d pools from disk cache (%.1f hours old)", len(pools), age_hours)
        return pools
    except Exception as exc:
        logger.debug("Failed to load pools from disk: %s", exc)
        return []


# ── DeFi Llama Source ─────────────────────────────────────────

DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"


def fetch_defillama_pools() -> List[PoolData]:
    """Fetch pools from DeFi Llama yields API."""
    pools: List[PoolData] = []
    
    data = _request_with_retry(DEFILLAMA_POOLS_URL, timeout=30)
    if not data:
        logger.warning("DeFi Llama: failed to fetch pools")
        return pools
    
    raw_pools = data.get("data", [])
    logger.info("DeFi Llama: received %d raw pools", len(raw_pools))
    
    for p in raw_pools:
        try:
            chain = _normalize_chain(p.get("chain", ""))
            if not chain:
                continue
            
            tvl = _safe_float(p.get("tvlUsd", 0))
            if tvl < MIN_TVL_USD:
                continue
            
            apy_base = _safe_float(p.get("apyBase", 0))
            apy_reward = _safe_float(p.get("apyReward", 0))
            
            # Use explicit apy if available, otherwise compute from base + reward
            apy_total = _safe_float(p.get("apy", 0))
            if apy_total <= 0:
                apy_total = apy_base + apy_reward
            
            # Skip pools with no yield
            if apy_total <= 0:
                continue
            
            pool_id = p.get("pool", "")
            if not pool_id:
                continue
            
            # Parse exposure tokens from symbol
            symbol = p.get("symbol", "")
            exposure = []
            if symbol:
                exposure = [t.strip() for t in symbol.replace("/", "-").split("-") if t.strip()]
            
            pools.append(PoolData(
                pool_id=pool_id,
                protocol=p.get("project", "unknown"),
                chain=chain,
                symbol=symbol,
                apy_total=apy_total,
                apy_base=apy_base,
                apy_reward=apy_reward,
                tvl_usd=tvl,
                source="defillama",
                stablecoin=bool(p.get("stablecoin", False)),
                exposure=exposure,
                reward_tokens=p.get("rewardTokens", []) or [],
            ))
        except Exception as exc:
            logger.debug("DeFi Llama: failed to parse pool: %s", exc)
            continue
    
    logger.info("DeFi Llama: parsed %d valid pools", len(pools))
    return pools


# ── Curve Finance Source ──────────────────────────────────────

CURVE_API_ENDPOINTS = {
    "ethereum": "https://api.curve.fi/api/getPools/ethereum/main",
    "arbitrum": "https://api.curve.fi/api/getPools/arbitrum/main",
    "polygon": "https://api.curve.fi/api/getPools/polygon/main",
    "optimism": "https://api.curve.fi/api/getPools/optimism/main",
    "avalanche": "https://api.curve.fi/api/getPools/avalanche/main",
    "base": "https://api.curve.fi/api/getPools/base/main",
}


def fetch_curve_pools() -> List[PoolData]:
    """Fetch pools from Curve Finance API."""
    pools: List[PoolData] = []
    
    for chain, url in CURVE_API_ENDPOINTS.items():
        data = _request_with_retry(url, timeout=15)
        if not data:
            logger.debug("Curve: failed to fetch %s pools", chain)
            continue
        
        pool_data = data.get("data", {}).get("poolData", [])
        
        for p in pool_data:
            try:
                tvl = _safe_float(p.get("usdTotal", 0))
                if tvl < MIN_TVL_USD:
                    continue
                
                # Curve APY comes from different sources
                apy_total = _safe_float(p.get("apy", 0))
                apy_base = _safe_float(p.get("baseApy", 0))
                apy_reward = _safe_float(p.get("crvApy", 0)) + _safe_float(p.get("additionalApy", 0))
                
                if apy_total <= 0:
                    apy_total = apy_base + apy_reward
                
                if apy_total <= 0:
                    continue
                
                pool_id = p.get("id") or p.get("address", "")
                if not pool_id:
                    continue
                
                # Curve pools are mostly stablecoin or similar-asset pairs
                coins = p.get("coins", [])
                symbol = "-".join([c.get("symbol", "?") for c in coins]) if coins else p.get("name", "")
                exposure = [c.get("symbol", "") for c in coins if c.get("symbol")]
                
                # Check if stablecoin pool
                stablecoin = any(
                    "usd" in s.lower() or "dai" in s.lower() or "frax" in s.lower()
                    for s in exposure
                )
                
                pools.append(PoolData(
                    pool_id=f"curve-{chain}-{pool_id}",
                    protocol="curve-dex",
                    chain=chain,
                    symbol=symbol,
                    apy_total=apy_total,
                    apy_base=apy_base,
                    apy_reward=apy_reward,
                    tvl_usd=tvl,
                    source="curve",
                    stablecoin=stablecoin,
                    exposure=exposure,
                    reward_tokens=["CRV"] if apy_reward > 0 else [],
                ))
            except Exception as exc:
                logger.debug("Curve: failed to parse pool: %s", exc)
                continue
    
    logger.info("Curve: fetched %d pools across %d chains", len(pools), len(CURVE_API_ENDPOINTS))
    return pools


# ── Balancer Source ───────────────────────────────────────────

BALANCER_SUBGRAPH_ENDPOINTS = {
    "ethereum": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-v2",
    "arbitrum": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-arbitrum-v2",
    "polygon": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-polygon-v2",
    "optimism": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-optimism-v2",
    "base": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-base-v2",
    "avalanche": "https://api.thegraph.com/subgraphs/name/balancer-labs/balancer-avalanche-v2",
}

BALANCER_POOLS_QUERY = """
{
  pools(
    first: 100,
    orderBy: totalLiquidity,
    orderDirection: desc,
    where: { totalLiquidity_gt: "50000" }
  ) {
    id
    name
    symbol
    address
    poolType
    totalLiquidity
    swapFee
    tokens {
      symbol
      balance
    }
  }
}
"""


def fetch_balancer_pools() -> List[PoolData]:
    """Fetch pools from Balancer subgraphs with retry logic."""
    pools: List[PoolData] = []
    
    for chain, endpoint in BALANCER_SUBGRAPH_ENDPOINTS.items():
        data = _graphql_request_with_retry(endpoint, BALANCER_POOLS_QUERY, timeout=20)
        if not data:
            logger.debug("Balancer: failed to fetch %s pools", chain)
            continue
        
        raw_pools = data.get("data", {}).get("pools", [])
        
        for p in raw_pools:
            try:
                tvl = _safe_float(p.get("totalLiquidity", 0))
                if tvl < MIN_TVL_USD:
                    continue
                
                # Balancer APY is based on swap fees and volume
                # We estimate based on swap fee (typically 0.01% to 1%)
                swap_fee = _safe_float(p.get("swapFee", 0))
                # Conservative estimate: assume 10% daily volume turnover
                apy_base = swap_fee * 0.1 * 365 * 100  # Convert to percentage
                
                if apy_base <= 0:
                    apy_base = 2.0  # Default estimate for Balancer pools
                
                pool_id = p.get("id", "")
                if not pool_id:
                    continue
                
                tokens = p.get("tokens", [])
                symbol = "-".join([t.get("symbol", "?") for t in tokens])
                exposure = [t.get("symbol", "") for t in tokens if t.get("symbol")]
                
                stablecoin = any(
                    "usd" in s.lower() or "dai" in s.lower()
                    for s in exposure
                )
                
                pools.append(PoolData(
                    pool_id=f"balancer-{chain}-{pool_id[:16]}",
                    protocol="balancer-v2",
                    chain=chain,
                    symbol=symbol or p.get("name", ""),
                    apy_total=apy_base,
                    apy_base=apy_base,
                    apy_reward=0.0,
                    tvl_usd=tvl,
                    source="balancer",
                    stablecoin=stablecoin,
                    exposure=exposure,
                ))
            except Exception as exc:
                logger.debug("Balancer: failed to parse pool: %s", exc)
                continue
    
    logger.info("Balancer: fetched %d pools", len(pools))
    return pools


# ── Uniswap V3 Source ─────────────────────────────────────────

UNISWAP_V3_SUBGRAPH_ENDPOINTS = {
    "ethereum": "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
    "arbitrum": "https://api.thegraph.com/subgraphs/name/ianlapham/arbitrum-minimal",
    "polygon": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon",
    "optimism": "https://api.thegraph.com/subgraphs/name/ianlapham/optimism-post-regenesis",
    "base": "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-base",
}

UNISWAP_V3_POOLS_QUERY = """
{
  pools(
    first: 100,
    orderBy: totalValueLockedUSD,
    orderDirection: desc,
    where: { totalValueLockedUSD_gt: "50000" }
  ) {
    id
    token0 { symbol }
    token1 { symbol }
    feeTier
    liquidity
    totalValueLockedUSD
    volumeUSD
    feesUSD
  }
}
"""


def fetch_uniswap_v3_pools() -> List[PoolData]:
    """Fetch pools from Uniswap V3 subgraphs with retry logic."""
    pools: List[PoolData] = []
    
    for chain, endpoint in UNISWAP_V3_SUBGRAPH_ENDPOINTS.items():
        data = _graphql_request_with_retry(endpoint, UNISWAP_V3_POOLS_QUERY, timeout=20)
        if not data:
            logger.debug("Uniswap V3: failed to fetch %s pools", chain)
            continue
        
        raw_pools = data.get("data", {}).get("pools", [])
        
        for p in raw_pools:
            try:
                tvl = _safe_float(p.get("totalValueLockedUSD", 0))
                if tvl < MIN_TVL_USD:
                    continue
                
                # Calculate APY based on fees and volume
                volume = _safe_float(p.get("volumeUSD", 0))
                fees = _safe_float(p.get("feesUSD", 0))
                
                # Annualize: assume fees are cumulative since pool creation
                # Conservative estimate based on fee tier
                fee_tier = _safe_float(p.get("feeTier", 3000)) / 1_000_000  # Convert to percentage
                
                # Estimate daily volume as 1% of cumulative
                daily_volume = volume * 0.01
                daily_fees = daily_volume * fee_tier
                apy_base = (daily_fees / max(tvl, 1)) * 365 * 100
                
                # Cap at reasonable APY
                apy_base = min(apy_base, 200.0)
                
                if apy_base <= 0:
                    apy_base = fee_tier * 10 * 100  # Fallback estimate
                
                pool_id = p.get("id", "")
                if not pool_id:
                    continue
                
                token0 = p.get("token0", {}).get("symbol", "?")
                token1 = p.get("token1", {}).get("symbol", "?")
                symbol = f"{token0}-{token1}"
                exposure = [token0, token1]
                
                stablecoin = any(
                    "usd" in s.lower() or "dai" in s.lower()
                    for s in exposure
                )
                
                pools.append(PoolData(
                    pool_id=f"uniswap-v3-{chain}-{pool_id[:16]}",
                    protocol="uniswap-v3",
                    chain=chain,
                    symbol=symbol,
                    apy_total=apy_base,
                    apy_base=apy_base,
                    apy_reward=0.0,
                    tvl_usd=tvl,
                    source="uniswap-v3",
                    stablecoin=stablecoin,
                    exposure=exposure,
                ))
            except Exception as exc:
                logger.debug("Uniswap V3: failed to parse pool: %s", exc)
                continue
    
    logger.info("Uniswap V3: fetched %d pools", len(pools))
    return pools


# ── 1inch Liquidity Sources ───────────────────────────────────

ONEINCH_API_BASE = "https://api.1inch.io/v5.0"
ONEINCH_CHAINS = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "avalanche": 43114,
    "base": 8453,
}


def fetch_1inch_liquidity_sources() -> List[PoolData]:
    """Fetch liquidity sources from 1inch API (protocol metadata)."""
    pools: List[PoolData] = []
    
    for chain, chain_id in ONEINCH_CHAINS.items():
        url = f"{ONEINCH_API_BASE}/{chain_id}/liquidity-sources"
        
        data = _request_with_retry(url, timeout=10)
        if not data:
            logger.debug("1inch: failed to fetch %s sources", chain)
            continue
        
        protocols = data.get("protocols", [])
        
        # 1inch provides protocol metadata, not pool-level data
        # We use this to identify active DEXes on each chain
        for protocol in protocols:
            try:
                protocol_id = protocol.get("id", "")
                protocol_name = protocol.get("title", protocol_id)
                
                if not protocol_id:
                    continue
                
                # Create a placeholder pool for each major protocol
                # This helps identify where to look for opportunities
                pools.append(PoolData(
                    pool_id=f"1inch-{chain}-{protocol_id}",
                    protocol=protocol_name,
                    chain=chain,
                    symbol="Multi-pair",
                    apy_total=5.0,  # Estimated average
                    apy_base=5.0,
                    tvl_usd=100_000,  # Placeholder
                    source="1inch",
                ))
            except Exception as exc:
                logger.debug("1inch: failed to parse protocol: %s", exc)
                continue
    
    logger.info("1inch: identified %d liquidity sources", len(pools))
    return pools


# ── Pool Fetcher ──────────────────────────────────────────────

class MultiSourcePoolFetcher:
    """
    Aggregates pool data from multiple DeFi sources.
    
    Fetches in parallel from all sources and deduplicates results.
    Includes robust fallback mechanisms:
    1. In-memory cache
    2. Persistent disk cache
    3. Hardcoded fallback pools (always available)
    
    Modeled after successful mining programs that never fail to find pools.
    """
    
    def __init__(self):
        self._cache: List[PoolData] = []
        self._cache_time: float = 0.0
        self._lock_sources: Set[str] = set()  # Track which sources succeeded
        self._last_error: Optional[str] = None
        self._consecutive_failures: int = 0
    
    def _fetch_source(self, source_name: str, fetch_func: Callable[[], List[PoolData]]) -> List[PoolData]:
        """Wrapper to safely fetch from a source with error tracking."""
        try:
            pools = fetch_func()
            if pools:
                self._lock_sources.add(source_name)
                logger.debug("Source %s returned %d pools", source_name, len(pools))
            return pools
        except Exception as exc:
            self._last_error = f"{source_name}: {exc}"
            logger.warning("Failed to fetch from %s: %s", source_name, exc)
            return []
    
    def fetch_all_pools(self, force_refresh: bool = False) -> List[PoolData]:
        """
        Fetch pools from all sources in parallel with robust fallback.
        
        Implements a multi-tier fallback strategy:
        1. Live API fetch from all sources
        2. Persistent disk cache (if APIs fail)
        3. Hardcoded fallback pools (guaranteed to always work)
        
        Args:
            force_refresh: If True, bypass in-memory cache and fetch fresh data.
            
        Returns:
            Deduplicated list of PoolData sorted by APY. Never returns empty list.
        """
        now = time.time()
        
        # Check in-memory cache first
        if not force_refresh and self._cache and (now - self._cache_time) < CACHE_TTL:
            logger.debug("Returning cached pools (%d pools)", len(self._cache))
            return self._cache
        
        self._lock_sources.clear()
        self._last_error = None
        all_pools: List[PoolData] = []
        
        # Define all sources with their fetch functions
        sources: List[Tuple[str, Callable[[], List[PoolData]]]] = [
            ("defillama", fetch_defillama_pools),
            ("curve", fetch_curve_pools),
            ("balancer", fetch_balancer_pools),
            ("uniswap-v3", fetch_uniswap_v3_pools),
        ]
        
        # Fetch from all sources in parallel
        try:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(self._fetch_source, name, func): name
                    for name, func in sources
                }
                
                for future in as_completed(futures, timeout=90):  # Increased timeout
                    source_name = futures[future]
                    try:
                        pools = future.result(timeout=30)
                        all_pools.extend(pools)
                        if pools:
                            logger.info("Fetched %d pools from %s", len(pools), source_name)
                    except Exception as exc:
                        logger.warning("Error fetching from %s: %s", source_name, exc)
        except Exception as exc:
            logger.error("ThreadPool execution failed: %s", exc)
        
        # Tier 1 fallback: If very few pools from APIs, try 1inch
        if len(all_pools) < 20:
            logger.info("Only %d pools from primary sources, trying 1inch", len(all_pools))
            try:
                inch_pools = fetch_1inch_liquidity_sources()
                all_pools.extend(inch_pools)
            except Exception as exc:
                logger.warning("1inch fallback also failed: %s", exc)
        
        # Tier 2 fallback: Load from persistent disk cache
        if len(all_pools) < 10:
            logger.warning("Very few pools from APIs (%d), loading disk cache", len(all_pools))
            disk_pools = _load_pools_from_disk(max_age_hours=48)  # Allow older cache
            if disk_pools:
                logger.info("Recovered %d pools from disk cache", len(disk_pools))
                all_pools.extend(disk_pools)
        
        # Tier 3 fallback: Hardcoded pools (always available)
        if len(all_pools) < 5:
            logger.warning("API sources failed, using hardcoded fallback pools")
            fallback_pools = _get_fallback_pools()
            all_pools.extend(fallback_pools)
            self._lock_sources.add("fallback")
            self._consecutive_failures += 1
        else:
            self._consecutive_failures = 0
        
        # Deduplicate by pool_id (prefer DeFi Llama data, then other live sources, then fallback)
        seen_ids: Set[str] = set()
        unique_pools: List[PoolData] = []
        
        # Sort by source priority: live data > disk cache > fallback
        source_priority = {
            "defillama": 0,
            "curve": 1, 
            "uniswap-v3": 2,
            "balancer": 3,
            "1inch": 4,
            "disk_cache": 5,
            "fallback": 6,
        }
        all_pools.sort(key=lambda p: source_priority.get(p.source, 5))
        
        for pool in all_pools:
            # Normalize pool ID for deduplication
            normalized_id = pool.pool_id.lower()
            if normalized_id not in seen_ids:
                seen_ids.add(normalized_id)
                unique_pools.append(pool)
        
        # Sort by APY descending
        unique_pools.sort(key=lambda p: p.apy_total, reverse=True)
        
        # Update in-memory cache
        self._cache = unique_pools
        self._cache_time = now
        
        # Save to persistent disk cache for future fallback (only if we got live data)
        if self._lock_sources - {"fallback", "disk_cache"}:
            _save_pools_to_disk(unique_pools)
        
        # Log diagnostic info
        if len(unique_pools) == 0:
            logger.error(
                "CRITICAL: No pools found from any source including fallback!\n"
                "This should never happen. Last error: %s", self._last_error
            )
        else:
            live_sources = self._lock_sources - {"fallback", "disk_cache"}
            logger.info(
                "Pool fetch complete: %d unique pools from %d live sources + %s",
                len(unique_pools),
                len(live_sources),
                "fallback" if "fallback" in self._lock_sources else "no fallback"
            )
            
            # Log warning if relying heavily on fallback
            if self._consecutive_failures >= 3:
                logger.warning(
                    "WARNING: %d consecutive API failures. Using fallback pools.\n"
                    "Check network connectivity and API rate limits.",
                    self._consecutive_failures
                )
        
        return unique_pools
    
    def get_pools_by_chain(self, chain: str) -> List[PoolData]:
        """Get pools filtered by chain."""
        pools = self.fetch_all_pools()
        return [p for p in pools if p.chain == chain]
    
    def get_pools_by_protocol(self, protocol: str) -> List[PoolData]:
        """Get pools filtered by protocol."""
        pools = self.fetch_all_pools()
        return [p for p in pools if protocol.lower() in p.protocol.lower()]
    
    def get_top_pools(
        self,
        limit: int = 50,
        min_tvl: float = MIN_TVL_USD,
        min_apy: float = 0.0,
        stablecoins_only: bool = False,
        chains: Optional[List[str]] = None,
    ) -> List[PoolData]:
        """
        Get top pools with filtering options.
        
        Args:
            limit: Maximum number of pools to return.
            min_tvl: Minimum TVL in USD.
            min_apy: Minimum APY percentage.
            stablecoins_only: If True, only return stablecoin pools.
            chains: List of chains to include (None = all).
            
        Returns:
            Filtered and sorted list of PoolData.
        """
        pools = self.fetch_all_pools()
        
        filtered = [
            p for p in pools
            if p.tvl_usd >= min_tvl
            and p.apy_total >= min_apy
            and (not stablecoins_only or p.stablecoin)
            and (chains is None or p.chain in chains)
        ]
        
        return filtered[:limit]
    
    def get_source_status(self) -> Dict[str, Any]:
        """Get detailed status of which sources returned data."""
        return {
            "sources": {
                "defillama": "defillama" in self._lock_sources,
                "curve": "curve" in self._lock_sources,
                "balancer": "balancer" in self._lock_sources,
                "uniswap-v3": "uniswap-v3" in self._lock_sources,
                "1inch": "1inch" in self._lock_sources,
                "disk_cache": "disk_cache" in self._lock_sources,
                "fallback": "fallback" in self._lock_sources,
            },
            "total_pools": len(self._cache),
            "cache_age_seconds": time.time() - self._cache_time if self._cache_time else None,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }
    
    def diagnose(self) -> Dict[str, Any]:
        """
        Run diagnostic checks on pool discovery.
        
        Returns detailed information about the health of pool discovery,
        useful for debugging issues.
        """
        diag = {
            "timestamp": time.time(),
            "source_status": self.get_source_status(),
            "checks": {},
        }
        
        # Check each source individually
        sources_to_check = [
            ("defillama", "https://yields.llama.fi/pools"),
            ("curve", "https://api.curve.fi/api/getPools/ethereum/main"),
        ]
        
        for source_name, url in sources_to_check:
            try:
                resp = requests.get(url, timeout=10)
                diag["checks"][source_name] = {
                    "reachable": True,
                    "status_code": resp.status_code,
                    "response_time_ms": int(resp.elapsed.total_seconds() * 1000),
                }
            except Exception as exc:
                diag["checks"][source_name] = {
                    "reachable": False,
                    "error": str(exc),
                }
        
        # Check disk cache
        diag["disk_cache"] = {
            "exists": PERSISTENT_CACHE_FILE.exists(),
            "path": str(PERSISTENT_CACHE_FILE),
        }
        if PERSISTENT_CACHE_FILE.exists():
            try:
                stat = PERSISTENT_CACHE_FILE.stat()
                diag["disk_cache"]["size_bytes"] = stat.st_size
                diag["disk_cache"]["age_hours"] = (time.time() - stat.st_mtime) / 3600
            except Exception:
                pass
        
        # Check fallback pools
        diag["fallback_pools_count"] = len(FALLBACK_POOLS)
        
        return diag


# ── Singleton ─────────────────────────────────────────────────

_pool_fetcher: Optional[MultiSourcePoolFetcher] = None


def get_pool_fetcher() -> MultiSourcePoolFetcher:
    """Get or create the singleton pool fetcher."""
    global _pool_fetcher
    if _pool_fetcher is None:
        _pool_fetcher = MultiSourcePoolFetcher()
    return _pool_fetcher
