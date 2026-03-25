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

Usage:
    fetcher = get_pool_fetcher()
    pools = fetcher.fetch_all_pools()
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import requests

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────

# Retry settings
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds

# Cache TTL in seconds
CACHE_TTL = 300  # 5 minutes

# Minimum TVL to include a pool (USD)
MIN_TVL_USD = 50_000

# Supported chains for filtering
SUPPORTED_CHAINS = {
    "ethereum", "arbitrum", "optimism", "base", "polygon", "bsc", "avalanche"
}

# Chain name normalization mapping
CHAIN_ALIASES: Dict[str, str] = {
    # Ethereum
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    # Arbitrum
    "arbitrum": "arbitrum",
    "arbitrum_one": "arbitrum",
    "arbitrum-one": "arbitrum",
    # Optimism
    "optimism": "optimism",
    "op": "optimism",
    # Base
    "base": "base",
    # Polygon
    "polygon": "polygon",
    "matic": "polygon",
    "polygon_pos": "polygon",
    # BSC
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "binance-smart-chain": "bsc",
    # Avalanche
    "avalanche": "avalanche",
    "avax": "avalanche",
    "avalanche_c": "avalanche",
    # Fantom (not in supported but useful for mapping)
    "fantom": "fantom",
    "ftm": "fantom",
    # Gnosis
    "gnosis": "gnosis",
    "xdai": "gnosis",
}


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
    """Make HTTP request with retry logic and exponential backoff."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url, params=params, timeout=timeout, headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BACKOFF_BASE ** attempt
                logger.debug(
                    "Request to %s failed (attempt %d/%d): %s. Retrying in %ds",
                    url, attempt + 1, MAX_RETRIES, exc, delay
                )
                time.sleep(delay)
    logger.warning("Request to %s failed after %d retries: %s", url, MAX_RETRIES, last_error)
    return None


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
    """Fetch pools from Balancer subgraphs."""
    pools: List[PoolData] = []
    
    for chain, endpoint in BALANCER_SUBGRAPH_ENDPOINTS.items():
        try:
            resp = requests.post(
                endpoint,
                json={"query": BALANCER_POOLS_QUERY},
                timeout=15,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Balancer: failed to fetch %s pools: %s", chain, exc)
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
    """Fetch pools from Uniswap V3 subgraphs."""
    pools: List[PoolData] = []
    
    for chain, endpoint in UNISWAP_V3_SUBGRAPH_ENDPOINTS.items():
        try:
            resp = requests.post(
                endpoint,
                json={"query": UNISWAP_V3_POOLS_QUERY},
                timeout=15,
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Uniswap V3: failed to fetch %s pools: %s", chain, exc)
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
    """
    
    def __init__(self):
        self._cache: List[PoolData] = []
        self._cache_time: float = 0.0
        self._lock_sources: Set[str] = set()  # Track which sources succeeded
    
    def _fetch_source(self, source_name: str, fetch_func) -> List[PoolData]:
        """Wrapper to safely fetch from a source."""
        try:
            pools = fetch_func()
            if pools:
                self._lock_sources.add(source_name)
            return pools
        except Exception as exc:
            logger.warning("Failed to fetch from %s: %s", source_name, exc)
            return []
    
    def fetch_all_pools(self, force_refresh: bool = False) -> List[PoolData]:
        """
        Fetch pools from all sources in parallel.
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh data.
            
        Returns:
            Deduplicated list of PoolData sorted by APY.
        """
        now = time.time()
        
        # Check cache
        if not force_refresh and self._cache and (now - self._cache_time) < CACHE_TTL:
            logger.debug("Returning cached pools (%d pools)", len(self._cache))
            return self._cache
        
        self._lock_sources.clear()
        all_pools: List[PoolData] = []
        
        # Fetch from all sources in parallel
        sources = [
            ("defillama", fetch_defillama_pools),
            ("curve", fetch_curve_pools),
            ("balancer", fetch_balancer_pools),
            ("uniswap-v3", fetch_uniswap_v3_pools),
            # 1inch provides metadata, not pools - include only if others fail
        ]
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._fetch_source, name, func): name
                for name, func in sources
            }
            
            for future in as_completed(futures, timeout=60):
                source_name = futures[future]
                try:
                    pools = future.result()
                    all_pools.extend(pools)
                    logger.info("Fetched %d pools from %s", len(pools), source_name)
                except Exception as exc:
                    logger.warning("Error fetching from %s: %s", source_name, exc)
        
        # If no pools from primary sources, try 1inch as fallback
        if len(all_pools) < 10:
            logger.info("Few pools found, trying 1inch fallback")
            inch_pools = fetch_1inch_liquidity_sources()
            all_pools.extend(inch_pools)
        
        # Deduplicate by pool_id (prefer DeFi Llama data)
        seen_ids: Set[str] = set()
        unique_pools: List[PoolData] = []
        
        # Sort by source priority: defillama > curve > uniswap-v3 > balancer > 1inch
        source_priority = {"defillama": 0, "curve": 1, "uniswap-v3": 2, "balancer": 3, "1inch": 4}
        all_pools.sort(key=lambda p: source_priority.get(p.source, 5))
        
        for pool in all_pools:
            # Normalize pool ID for deduplication
            normalized_id = pool.pool_id.lower()
            if normalized_id not in seen_ids:
                seen_ids.add(normalized_id)
                unique_pools.append(pool)
        
        # Sort by APY descending
        unique_pools.sort(key=lambda p: p.apy_total, reverse=True)
        
        # Update cache
        self._cache = unique_pools
        self._cache_time = now
        
        # Log diagnostic info
        if len(unique_pools) == 0:
            logger.warning(
                "No pools found from any source. This usually means:\n"
                "  1. External APIs (DeFi Llama, Curve, etc.) are unreachable\n"
                "  2. Network/DNS issues are blocking API access\n"
                "  3. All APIs are rate-limiting requests\n"
                "Check your network connection and API access."
            )
        else:
            logger.info(
                "Multi-source fetch complete: %d unique pools from %d sources",
                len(unique_pools), len(self._lock_sources)
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
    
    def get_source_status(self) -> Dict[str, bool]:
        """Get which sources successfully returned data."""
        return {
            "defillama": "defillama" in self._lock_sources,
            "curve": "curve" in self._lock_sources,
            "balancer": "balancer" in self._lock_sources,
            "uniswap-v3": "uniswap-v3" in self._lock_sources,
        }


# ── Singleton ─────────────────────────────────────────────────

_pool_fetcher: Optional[MultiSourcePoolFetcher] = None


def get_pool_fetcher() -> MultiSourcePoolFetcher:
    """Get or create the singleton pool fetcher."""
    global _pool_fetcher
    if _pool_fetcher is None:
        _pool_fetcher = MultiSourcePoolFetcher()
    return _pool_fetcher
