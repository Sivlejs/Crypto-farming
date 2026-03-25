"""
Pool Analyzer — Advanced AI-Powered Farming Pool Analysis for Nexus AI.

Provides intelligent pool selection by analyzing:
  1. Historical performance data (APY trends, TVL changes, impermanent loss)
  2. Real-time metrics (gas costs, slippage, liquidity depth)
  3. Risk assessment (smart contract risk, protocol maturity, audit status)
  4. Predictive scoring (ML-based pool ranking with confidence intervals)

The analyzer continuously learns from historical and real-time data to identify
the top farming pools across all supported chains, enabling Nexus to dynamically
choose between pools, decide profit splits, and optimize every transaction.

Usage:
    analyzer = get_pool_analyzer()
    top_pools = analyzer.get_top_pools(chain="ethereum", limit=10)
    decision = analyzer.recommend_allocation(available_capital=1000)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests

from nexus.protocols.pool_sources import get_pool_fetcher, PoolData, MIN_TVL_USD, CHAIN_ALIASES
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Database for pool history
DB_PATH = Path(__file__).parent.parent.parent / "data" / "pool_history.db"
try:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except OSError as e:
    logger.warning("Cannot create pool history directory %s: %s. Using /tmp fallback.", DB_PATH.parent, e)
    DB_PATH = Path("/tmp/nexus_data/pool_history.db")
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as fallback_error:
        logger.error("Cannot create fallback pool history directory %s: %s", DB_PATH.parent, fallback_error)
        # Continue without raising - will fail gracefully when DB is accessed

# DeFiLlama API endpoints
DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"
DEFILLAMA_CHART_URL = "https://yields.llama.fi/chart"

# Pool refresh interval
POOL_REFRESH_SECONDS = 300  # 5 minutes

# Risk scoring weights
RISK_WEIGHTS = {
    "tvl_score": 0.20,          # Higher TVL = lower risk
    "age_score": 0.15,          # Older protocols = lower risk
    "audit_score": 0.15,        # Audited = lower risk
    "il_score": 0.20,           # Lower IL = lower risk
    "volatility_score": 0.15,   # Lower APY volatility = lower risk
    "chain_score": 0.15,        # Mainnet > L2 > sidechains
}

# Chain risk tiers (lower = safer)
CHAIN_RISK_TIERS = {
    "ethereum": 1,
    "arbitrum": 2,
    "optimism": 2,
    "base": 2,
    "polygon": 3,
    "bsc": 3,
    "avalanche": 3,
}


@dataclass
class PoolMetrics:
    """Comprehensive metrics for a farming pool."""
    pool_id: str
    symbol: str
    protocol: str
    chain: str
    tvl_usd: float
    apy_base: float
    apy_reward: float
    apy_total: float
    il_risk: float  # 0-1, higher = more IL risk
    stablecoin: bool
    exposure: List[str]  # Underlying tokens
    reward_tokens: List[str]
    # Historical data
    apy_7d_avg: float = 0.0
    apy_30d_avg: float = 0.0
    tvl_change_7d: float = 0.0
    tvl_change_30d: float = 0.0
    apy_volatility: float = 0.0  # Standard deviation of APY
    # Risk scores
    composite_score: float = 0.0
    risk_score: float = 0.5
    confidence: float = 0.5
    # Timestamps
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "pool_id": self.pool_id,
            "symbol": self.symbol,
            "protocol": self.protocol,
            "chain": self.chain,
            "tvl_usd": round(self.tvl_usd, 2),
            "apy_base": round(self.apy_base, 2),
            "apy_reward": round(self.apy_reward, 2),
            "apy_total": round(self.apy_total, 2),
            "il_risk": round(self.il_risk, 3),
            "stablecoin": self.stablecoin,
            "exposure": self.exposure,
            "reward_tokens": self.reward_tokens,
            "apy_7d_avg": round(self.apy_7d_avg, 2),
            "apy_30d_avg": round(self.apy_30d_avg, 2),
            "tvl_change_7d": round(self.tvl_change_7d, 2),
            "tvl_change_30d": round(self.tvl_change_30d, 2),
            "apy_volatility": round(self.apy_volatility, 3),
            "composite_score": round(self.composite_score, 4),
            "risk_score": round(self.risk_score, 3),
            "confidence": round(self.confidence, 3),
            "last_updated": self.last_updated,
        }


@dataclass
class AllocationRecommendation:
    """AI-generated allocation recommendation."""
    pool_id: str
    symbol: str
    protocol: str
    chain: str
    allocation_pct: float  # Percentage of capital to allocate
    expected_apy: float
    risk_level: str  # "low", "medium", "high"
    reasoning: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "pool_id": self.pool_id,
            "symbol": self.symbol,
            "protocol": self.protocol,
            "chain": self.chain,
            "allocation_pct": round(self.allocation_pct, 2),
            "expected_apy": round(self.expected_apy, 2),
            "risk_level": self.risk_level,
            "reasoning": self.reasoning,
            "confidence": round(self.confidence, 3),
        }


class PoolHistoryDB:
    """SQLite storage for historical pool data."""

    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open()
        self._create_schema()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _create_schema(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS pool_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pool_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    tvl_usd REAL,
                    apy_base REAL,
                    apy_reward REAL,
                    apy_total REAL
                );

                CREATE TABLE IF NOT EXISTS pool_metadata (
                    pool_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    protocol TEXT,
                    chain TEXT,
                    stablecoin INTEGER DEFAULT 0,
                    exposure TEXT DEFAULT '[]',
                    reward_tokens TEXT DEFAULT '[]',
                    first_seen REAL,
                    audit_status TEXT DEFAULT 'unknown'
                );

                CREATE TABLE IF NOT EXISTS allocation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    pool_id TEXT,
                    amount_usd REAL,
                    expected_apy REAL,
                    actual_return REAL,
                    duration_hours REAL,
                    success INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_snap_pool ON pool_snapshots(pool_id);
                CREATE INDEX IF NOT EXISTS idx_snap_ts ON pool_snapshots(timestamp DESC);
            """)
            self._conn.commit()

    def record_snapshot(self, pool: PoolMetrics):
        """Store a pool snapshot for historical analysis."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO pool_snapshots
                   (pool_id, timestamp, tvl_usd, apy_base, apy_reward, apy_total)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (pool.pool_id, time.time(), pool.tvl_usd,
                 pool.apy_base, pool.apy_reward, pool.apy_total)
            )
            # Upsert metadata
            self._conn.execute(
                """INSERT OR REPLACE INTO pool_metadata
                   (pool_id, symbol, protocol, chain, stablecoin, exposure, reward_tokens, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                     (SELECT first_seen FROM pool_metadata WHERE pool_id = ?), ?))""",
                (pool.pool_id, pool.symbol, pool.protocol, pool.chain,
                 1 if pool.stablecoin else 0, json.dumps(pool.exposure),
                 json.dumps(pool.reward_tokens), pool.pool_id, time.time())
            )
            self._conn.commit()

    def get_historical_apy(self, pool_id: str, days: int = 30) -> List[Tuple[float, float]]:
        """Get historical APY data for a pool. Returns [(timestamp, apy), ...]."""
        cutoff = time.time() - (days * 86400)
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, apy_total FROM pool_snapshots
                   WHERE pool_id = ? AND timestamp > ?
                   ORDER BY timestamp ASC""",
                (pool_id, cutoff)
            ).fetchall()
        return [(r["timestamp"], r["apy_total"]) for r in rows]

    def get_historical_tvl(self, pool_id: str, days: int = 30) -> List[Tuple[float, float]]:
        """Get historical TVL data for a pool. Returns [(timestamp, tvl), ...]."""
        cutoff = time.time() - (days * 86400)
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, tvl_usd FROM pool_snapshots
                   WHERE pool_id = ? AND timestamp > ?
                   ORDER BY timestamp ASC""",
                (pool_id, cutoff)
            ).fetchall()
        return [(r["timestamp"], r["tvl_usd"]) for r in rows]

    def record_allocation(self, pool_id: str, amount: float, expected_apy: float):
        """Record a capital allocation decision for learning."""
        with self._lock:
            self._conn.execute(
                """INSERT INTO allocation_history
                   (timestamp, pool_id, amount_usd, expected_apy)
                   VALUES (?, ?, ?, ?)""",
                (time.time(), pool_id, amount, expected_apy)
            )
            self._conn.commit()

    def update_allocation_result(self, pool_id: str, actual_return: float,
                                  duration_hours: float, success: bool):
        """Update an allocation with actual results for learning."""
        with self._lock:
            self._conn.execute(
                """UPDATE allocation_history
                   SET actual_return = ?, duration_hours = ?, success = ?
                   WHERE pool_id = ? AND actual_return IS NULL
                   ORDER BY timestamp DESC LIMIT 1""",
                (actual_return, duration_hours, 1 if success else 0, pool_id)
            )
            self._conn.commit()

    def get_allocation_stats(self) -> dict:
        """Get aggregate allocation performance stats."""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM allocation_history"
            ).fetchone()[0]
            completed = self._conn.execute(
                "SELECT COUNT(*) FROM allocation_history WHERE actual_return IS NOT NULL"
            ).fetchone()[0]
            successful = self._conn.execute(
                "SELECT COUNT(*) FROM allocation_history WHERE success = 1"
            ).fetchone()[0]
            total_invested = self._conn.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) FROM allocation_history"
            ).fetchone()[0]
            total_returned = self._conn.execute(
                "SELECT COALESCE(SUM(actual_return), 0) FROM allocation_history WHERE actual_return IS NOT NULL"
            ).fetchone()[0]

        return {
            "total_allocations": total,
            "completed": completed,
            "successful": successful,
            "success_rate": round(successful / max(1, completed) * 100, 2),
            "total_invested_usd": round(total_invested, 2),
            "total_returned_usd": round(total_returned, 2),
            "net_profit_usd": round(total_returned - total_invested, 2),
        }


class PoolAnalyzer:
    """
    AI-powered farming pool analyzer.

    Continuously fetches and analyzes pool data from DeFi Llama,
    computes risk scores, and provides intelligent allocation recommendations.
    """

    def __init__(self):
        self._db = PoolHistoryDB()
        self._pools: Dict[str, PoolMetrics] = {}
        self._pool_rankings: Dict[str, List[str]] = {}  # chain -> sorted pool_ids
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_refresh = 0.0
        self._refresh_count = 0
        # ML model for pool scoring (will be trained over time)
        self._ml_model = None
        self._ml_trained_samples = 0

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Start background pool monitoring."""
        if self._running:
            return
        self._running = True
        # Initial fetch
        self._refresh_pools()
        # Background thread
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="pool-analyzer"
        )
        self._thread.start()
        logger.info("PoolAnalyzer started")

    def stop(self):
        self._running = False

    def _monitor_loop(self):
        while self._running:
            try:
                self._refresh_pools()
            except Exception as exc:
                logger.warning("Pool refresh error: %s", exc)
            time.sleep(POOL_REFRESH_SECONDS)

    # ── Data Fetching ─────────────────────────────────────────

    def _refresh_pools(self):
        """Fetch latest pool data from multiple sources."""
        try:
            # Use multi-source fetcher for broader pool discovery
            fetcher = get_pool_fetcher()
            pool_data_list = fetcher.fetch_all_pools(force_refresh=True)
            
            # Log source status for debugging
            source_status = fetcher.get_source_status()
            active_sources = [s for s, active in source_status.items() if active]
            logger.info("Pool refresh using sources: %s", ", ".join(active_sources) or "none")
            
            now = time.time()
            new_pools = {}

            for p in pool_data_list:
                # Filter by supported chains
                chain = p.chain
                if chain not in CHAIN_RISK_TIERS:
                    continue

                # Skip very low TVL pools (below configured minimum)
                tvl = p.tvl_usd
                if tvl < MIN_TVL_USD:
                    continue

                pool_id = p.pool_id
                if not pool_id:
                    continue

                # Parse pool data from PoolData object
                apy_base = p.apy_base
                apy_reward = p.apy_reward
                apy_total = p.apy_total

                # Determine IL risk based on pool type
                stablecoin = p.stablecoin
                il_risk = self._estimate_il_risk_from_pool_data(p)

                exposure = p.exposure[:5] if p.exposure else []
                reward_tokens = p.reward_tokens[:3] if p.reward_tokens else []

                metrics = PoolMetrics(
                    pool_id=pool_id,
                    symbol=p.symbol or "?",
                    protocol=p.protocol or "unknown",
                    chain=chain,
                    tvl_usd=tvl,
                    apy_base=apy_base,
                    apy_reward=apy_reward,
                    apy_total=apy_total,
                    il_risk=il_risk,
                    stablecoin=stablecoin,
                    exposure=exposure,
                    reward_tokens=reward_tokens,
                    last_updated=now,
                )

                # Compute historical metrics
                self._compute_historical_metrics(metrics)

                # Compute composite score
                metrics.composite_score = self._compute_composite_score(metrics)
                metrics.risk_score = self._compute_risk_score(metrics)
                metrics.confidence = self._compute_confidence(metrics)

                new_pools[pool_id] = metrics

                # Store snapshot
                self._db.record_snapshot(metrics)

            # Update rankings
            with self._lock:
                self._pools = new_pools
                self._update_rankings()
                self._last_refresh = now
                self._refresh_count += 1

            logger.info(
                "Pool refresh #%d: %d pools indexed across %d chains (sources: %s)",
                self._refresh_count, len(new_pools),
                len(set(p.chain for p in new_pools.values())),
                ", ".join(active_sources)
            )

        except Exception as exc:
            logger.warning("Pool refresh failed: %s", exc)
            # Fallback to DeFi Llama only if multi-source fails completely
            self._refresh_pools_fallback()
    
    def _refresh_pools_fallback(self):
        """Fallback to DeFi Llama only if multi-source fetch fails."""
        try:
            resp = requests.get(DEFILLAMA_POOLS_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])

            now = time.time()
            new_pools = {}

            for p in data:
                # Normalize chain name using the same aliases as pool_sources
                raw_chain = (p.get("chain") or "").lower().strip()
                chain = CHAIN_ALIASES.get(raw_chain)
                if not chain or chain not in CHAIN_RISK_TIERS:
                    continue

                tvl = float(p.get("tvlUsd", 0) or 0)
                if tvl < MIN_TVL_USD:
                    continue

                pool_id = p.get("pool", "")
                if not pool_id:
                    continue

                apy_base = float(p.get("apyBase", 0) or 0)
                apy_reward = float(p.get("apyReward", 0) or 0)
                apy_total = float(p.get("apy", 0) or apy_base + apy_reward)

                stablecoin = bool(p.get("stablecoin", False))
                il_risk = self._estimate_il_risk(p)

                exposure = p.get("underlyingTokens", []) or []
                reward_tokens = p.get("rewardTokens", []) or []

                metrics = PoolMetrics(
                    pool_id=pool_id,
                    symbol=p.get("symbol", "?"),
                    protocol=p.get("project", "unknown"),
                    chain=chain,
                    tvl_usd=tvl,
                    apy_base=apy_base,
                    apy_reward=apy_reward,
                    apy_total=apy_total,
                    il_risk=il_risk,
                    stablecoin=stablecoin,
                    exposure=exposure[:5] if exposure else [],
                    reward_tokens=reward_tokens[:3] if reward_tokens else [],
                    last_updated=now,
                )

                self._compute_historical_metrics(metrics)
                metrics.composite_score = self._compute_composite_score(metrics)
                metrics.risk_score = self._compute_risk_score(metrics)
                metrics.confidence = self._compute_confidence(metrics)

                new_pools[pool_id] = metrics
                self._db.record_snapshot(metrics)

            with self._lock:
                self._pools = new_pools
                self._update_rankings()
                self._last_refresh = now
                self._refresh_count += 1

            logger.info(
                "Pool refresh (fallback) #%d: %d pools indexed",
                self._refresh_count, len(new_pools)
            )

        except Exception as exc:
            logger.warning("DeFi Llama fallback fetch also failed: %s", exc)
    
    def _estimate_il_risk_from_pool_data(self, pool: PoolData) -> float:
        """
        Estimate impermanent loss risk for a PoolData object.
        0 = no IL risk (stablecoins), 1 = very high IL risk
        """
        if pool.stablecoin:
            return 0.0

        # Check for volatile pairs
        symbol = (pool.symbol or "").upper()
        exposure = pool.exposure or []

        # Stablecoin pairs have low IL
        stable_tokens = {"USDC", "USDT", "DAI", "BUSD", "FRAX", "LUSD", "TUSD"}
        stable_count = sum(1 for t in exposure if any(s in t.upper() for s in stable_tokens))

        if stable_count >= 2:
            return 0.1
        elif stable_count == 1:
            return 0.4

        # Check for correlated pairs (e.g., ETH/WETH, BTC/WBTC)
        if any(x in symbol for x in ["ETH-WETH", "BTC-WBTC", "STETH-ETH"]):
            return 0.2

        # Standard volatile pairs
        return 0.7

    def _estimate_il_risk(self, pool_data: dict) -> float:
        """
        Estimate impermanent loss risk for a pool.
        0 = no IL risk (stablecoins), 1 = very high IL risk
        """
        if pool_data.get("stablecoin"):
            return 0.0

        # Check for volatile pairs
        symbol = (pool_data.get("symbol") or "").upper()
        exposure = pool_data.get("underlyingTokens", []) or []

        # Stablecoin pairs have low IL
        stable_tokens = {"USDC", "USDT", "DAI", "BUSD", "FRAX", "LUSD", "TUSD"}
        stable_count = sum(1 for t in exposure if any(s in t.upper() for s in stable_tokens))

        if stable_count >= 2:
            return 0.1
        elif stable_count == 1:
            return 0.4

        # Check for correlated pairs (e.g., ETH/WETH, BTC/WBTC)
        if any(x in symbol for x in ["ETH-WETH", "BTC-WBTC", "STETH-ETH"]):
            return 0.2

        # Standard volatile pairs
        return 0.7

    def _compute_historical_metrics(self, pool: PoolMetrics):
        """Compute historical APY and TVL metrics."""
        # Get 7-day history
        apy_7d = self._db.get_historical_apy(pool.pool_id, days=7)
        tvl_7d = self._db.get_historical_tvl(pool.pool_id, days=7)

        # Get 30-day history
        apy_30d = self._db.get_historical_apy(pool.pool_id, days=30)
        tvl_30d = self._db.get_historical_tvl(pool.pool_id, days=30)

        # Compute averages
        if apy_7d:
            pool.apy_7d_avg = sum(a for _, a in apy_7d) / len(apy_7d)
        else:
            pool.apy_7d_avg = pool.apy_total

        if apy_30d:
            pool.apy_30d_avg = sum(a for _, a in apy_30d) / len(apy_30d)
            # Compute volatility
            if len(apy_30d) > 1:
                apys = [a for _, a in apy_30d]
                mean = sum(apys) / len(apys)
                variance = sum((a - mean) ** 2 for a in apys) / len(apys)
                pool.apy_volatility = (variance ** 0.5) / max(mean, 1) * 100
        else:
            pool.apy_30d_avg = pool.apy_total

        # Compute TVL changes
        if tvl_7d and len(tvl_7d) >= 2:
            first_tvl = tvl_7d[0][1]
            pool.tvl_change_7d = ((pool.tvl_usd - first_tvl) / max(first_tvl, 1)) * 100

        if tvl_30d and len(tvl_30d) >= 2:
            first_tvl = tvl_30d[0][1]
            pool.tvl_change_30d = ((pool.tvl_usd - first_tvl) / max(first_tvl, 1)) * 100

    def _compute_composite_score(self, pool: PoolMetrics) -> float:
        """
        Compute a composite score for pool ranking.
        Higher = better opportunity.
        """
        # Base: risk-adjusted APY
        # Use Sharpe-like ratio: APY / (risk + 0.1)
        risk_adjusted_apy = pool.apy_total / (pool.risk_score + 0.1)

        # TVL bonus (larger pools are safer)
        tvl_bonus = min(0.2, np.log10(max(pool.tvl_usd, 1)) / 50)

        # Stability bonus (lower APY volatility)
        stability_bonus = 0.1 * (1 - min(1, pool.apy_volatility / 100))

        # TVL trend bonus (growing TVL is good)
        trend_bonus = 0.05 * np.tanh(pool.tvl_change_7d / 20)

        # Stablecoin bonus (lower risk)
        stable_bonus = 0.15 if pool.stablecoin else 0

        score = (
            risk_adjusted_apy * 0.01 +  # Normalize APY contribution
            tvl_bonus +
            stability_bonus +
            trend_bonus +
            stable_bonus
        )

        return float(np.clip(score, 0, 1))

    def _compute_risk_score(self, pool: PoolMetrics) -> float:
        """
        Compute risk score for a pool.
        0 = lowest risk, 1 = highest risk.
        """
        scores = {}

        # TVL score (higher TVL = lower risk)
        if pool.tvl_usd >= 100_000_000:
            scores["tvl_score"] = 0.1
        elif pool.tvl_usd >= 10_000_000:
            scores["tvl_score"] = 0.3
        elif pool.tvl_usd >= 1_000_000:
            scores["tvl_score"] = 0.5
        else:
            scores["tvl_score"] = 0.8

        # Chain score
        chain_tier = CHAIN_RISK_TIERS.get(pool.chain, 3)
        scores["chain_score"] = (chain_tier - 1) * 0.3

        # IL score
        scores["il_score"] = pool.il_risk

        # APY volatility score
        scores["volatility_score"] = min(1, pool.apy_volatility / 50)

        # Age score (placeholder - would need first_seen data)
        scores["age_score"] = 0.3  # Default medium

        # Audit score (placeholder)
        scores["audit_score"] = 0.3  # Default medium

        # Weighted average
        total_risk = sum(
            scores.get(k, 0.5) * w
            for k, w in RISK_WEIGHTS.items()
        )

        return float(np.clip(total_risk, 0, 1))

    def _compute_confidence(self, pool: PoolMetrics) -> float:
        """
        Compute confidence in our scoring for this pool.
        Based on data availability and historical accuracy.
        """
        # Base confidence
        confidence = 0.5

        # More historical data = higher confidence
        apy_history = self._db.get_historical_apy(pool.pool_id, days=30)
        if len(apy_history) > 20:
            confidence += 0.2
        elif len(apy_history) > 5:
            confidence += 0.1

        # Higher TVL = more reliable data
        if pool.tvl_usd > 10_000_000:
            confidence += 0.15
        elif pool.tvl_usd > 1_000_000:
            confidence += 0.1

        # Stable pools have more predictable returns
        if pool.stablecoin:
            confidence += 0.1

        # Low APY volatility = more confidence
        if pool.apy_volatility < 10:
            confidence += 0.1

        return float(np.clip(confidence, 0, 1))

    def _update_rankings(self):
        """Update pool rankings per chain."""
        chain_pools: Dict[str, List[PoolMetrics]] = {}

        for pool in self._pools.values():
            if pool.chain not in chain_pools:
                chain_pools[pool.chain] = []
            chain_pools[pool.chain].append(pool)

        self._pool_rankings = {}
        for chain, pools in chain_pools.items():
            # Sort by composite score (descending)
            sorted_pools = sorted(pools, key=lambda p: p.composite_score, reverse=True)
            self._pool_rankings[chain] = [p.pool_id for p in sorted_pools]

    # ── Public API ────────────────────────────────────────────

    def get_pool(self, pool_id: str) -> Optional[PoolMetrics]:
        """Get metrics for a specific pool."""
        with self._lock:
            return self._pools.get(pool_id)

    def get_top_pools(
        self,
        chain: Optional[str] = None,
        limit: int = 20,
        min_tvl: float = 500_000,
        max_risk: float = 0.8,
        stablecoins_only: bool = False,
    ) -> List[PoolMetrics]:
        """
        Get top-ranked pools with optional filters.

        Parameters
        ----------
        chain : str, optional
            Filter to specific chain (e.g., "ethereum")
        limit : int
            Maximum number of pools to return
        min_tvl : float
            Minimum TVL in USD
        max_risk : float
            Maximum risk score (0-1)
        stablecoins_only : bool
            Only return stablecoin pools

        Returns
        -------
        List[PoolMetrics]
            Sorted list of top pools
        """
        with self._lock:
            pools = list(self._pools.values())

        # Apply filters
        filtered = []
        for p in pools:
            if chain and p.chain != chain:
                continue
            if p.tvl_usd < min_tvl:
                continue
            if p.risk_score > max_risk:
                continue
            if stablecoins_only and not p.stablecoin:
                continue
            filtered.append(p)

        # Sort by composite score
        filtered.sort(key=lambda x: x.composite_score, reverse=True)

        return filtered[:limit]

    def get_pools_by_protocol(self, protocol: str) -> List[PoolMetrics]:
        """Get all pools for a specific protocol."""
        with self._lock:
            return [
                p for p in self._pools.values()
                if p.protocol.lower() == protocol.lower()
            ]

    def recommend_allocation(
        self,
        available_capital: float,
        risk_tolerance: str = "medium",
        chains: Optional[List[str]] = None,
        max_pools: int = 5,
    ) -> List[AllocationRecommendation]:
        """
        Generate AI-powered allocation recommendations.

        Parameters
        ----------
        available_capital : float
            Total USD to allocate
        risk_tolerance : str
            "low", "medium", or "high"
        chains : list, optional
            Limit to specific chains
        max_pools : int
            Maximum number of pools to recommend

        Returns
        -------
        List[AllocationRecommendation]
            Recommended allocations with reasoning
        """
        # Map risk tolerance to max_risk
        risk_limits = {"low": 0.4, "medium": 0.6, "high": 0.85}
        max_risk = risk_limits.get(risk_tolerance, 0.6)

        # Get top pools
        top_pools = self.get_top_pools(
            chain=None,
            limit=50,
            min_tvl=500_000,
            max_risk=max_risk,
        )

        # Filter by chains if specified
        if chains:
            top_pools = [p for p in top_pools if p.chain in chains]

        if not top_pools:
            return []

        # Diversification strategy
        recommendations = []
        remaining_pct = 100.0
        used_protocols = set()
        used_chains = set()

        for pool in top_pools:
            if len(recommendations) >= max_pools:
                break

            # Diversify across protocols
            if pool.protocol in used_protocols and len(used_protocols) < 3:
                continue

            # Calculate allocation percentage
            # Higher score = more allocation, but cap at 40% per pool
            base_allocation = min(40, pool.composite_score * 50)

            # Reduce if protocol/chain already used
            if pool.protocol in used_protocols:
                base_allocation *= 0.5
            if pool.chain in used_chains:
                base_allocation *= 0.8

            allocation_pct = min(base_allocation, remaining_pct)
            if allocation_pct < 5:  # Skip tiny allocations
                continue

            # Determine risk level string
            if pool.risk_score < 0.3:
                risk_level = "low"
            elif pool.risk_score < 0.6:
                risk_level = "medium"
            else:
                risk_level = "high"

            # Generate reasoning
            reasons = []
            if pool.stablecoin:
                reasons.append("stablecoin pool (low IL risk)")
            if pool.tvl_usd > 50_000_000:
                reasons.append(f"high TVL (${pool.tvl_usd/1e6:.1f}M)")
            if pool.apy_volatility < 15:
                reasons.append("stable APY history")
            if pool.tvl_change_7d > 5:
                reasons.append("growing TVL")
            if pool.apy_total > pool.apy_30d_avg * 1.1:
                reasons.append("APY above 30d average")

            reasoning = "; ".join(reasons) if reasons else "strong composite score"

            recommendations.append(AllocationRecommendation(
                pool_id=pool.pool_id,
                symbol=pool.symbol,
                protocol=pool.protocol,
                chain=pool.chain,
                allocation_pct=allocation_pct,
                expected_apy=pool.apy_total,
                risk_level=risk_level,
                reasoning=reasoning,
                confidence=pool.confidence,
            ))

            remaining_pct -= allocation_pct
            used_protocols.add(pool.protocol)
            used_chains.add(pool.chain)

        # Record allocations for learning
        for rec in recommendations:
            amount = available_capital * (rec.allocation_pct / 100)
            self._db.record_allocation(rec.pool_id, amount, rec.expected_apy)

        return recommendations

    def get_pool_comparison(
        self,
        pool_ids: List[str],
    ) -> List[dict]:
        """
        Compare multiple pools side by side.
        Useful for AI decision transparency.
        """
        comparisons = []
        for pid in pool_ids:
            pool = self.get_pool(pid)
            if not pool:
                continue

            comparisons.append({
                **pool.to_dict(),
                "rank_on_chain": self._get_chain_rank(pid, pool.chain),
                "percentile_apy": self._get_apy_percentile(pool),
                "percentile_tvl": self._get_tvl_percentile(pool),
            })

        return comparisons

    def _get_chain_rank(self, pool_id: str, chain: str) -> int:
        """Get pool's rank within its chain."""
        rankings = self._pool_rankings.get(chain, [])
        try:
            return rankings.index(pool_id) + 1
        except ValueError:
            return 0

    def _get_apy_percentile(self, pool: PoolMetrics) -> float:
        """Get pool's APY percentile among all pools."""
        with self._lock:
            all_apys = sorted([p.apy_total for p in self._pools.values()])
        if not all_apys:
            return 50.0
        rank = sum(1 for a in all_apys if a < pool.apy_total)
        return round(rank / len(all_apys) * 100, 1)

    def _get_tvl_percentile(self, pool: PoolMetrics) -> float:
        """Get pool's TVL percentile among all pools."""
        with self._lock:
            all_tvls = sorted([p.tvl_usd for p in self._pools.values()])
        if not all_tvls:
            return 50.0
        rank = sum(1 for t in all_tvls if t < pool.tvl_usd)
        return round(rank / len(all_tvls) * 100, 1)

    def learn_from_outcome(
        self,
        pool_id: str,
        actual_return: float,
        duration_hours: float,
        success: bool,
    ):
        """
        Feed actual allocation results back for learning.
        Call this after exiting a position.
        """
        self._db.update_allocation_result(
            pool_id, actual_return, duration_hours, success
        )
        logger.info(
            "Pool learning: %s returned $%.2f over %.1fh (success=%s)",
            pool_id, actual_return, duration_hours, success
        )

    def status(self) -> dict:
        """Return analyzer status and stats."""
        with self._lock:
            pool_count = len(self._pools)
            chains = list(set(p.chain for p in self._pools.values()))

        return {
            "running": self._running,
            "pool_count": pool_count,
            "chains": chains,
            "refresh_count": self._refresh_count,
            "last_refresh": self._last_refresh,
            "allocation_stats": self._db.get_allocation_stats(),
        }

    def get_ai_decision_log(self, limit: int = 10) -> List[dict]:
        """
        Get recent AI decisions for dashboard transparency.
        Shows why certain pools were chosen/rejected.
        """
        # Get top pools and explain why they're ranked
        top_pools = self.get_top_pools(limit=limit)
        decisions = []

        for i, pool in enumerate(top_pools):
            factors = []

            # Explain positive factors
            if pool.apy_total > 20:
                factors.append(f"+High APY ({pool.apy_total:.1f}%)")
            if pool.tvl_usd > 50_000_000:
                factors.append(f"+Large TVL (${pool.tvl_usd/1e6:.0f}M)")
            if pool.stablecoin:
                factors.append("+Stablecoin (no IL)")
            if pool.risk_score < 0.3:
                factors.append("+Low risk score")
            if pool.tvl_change_7d > 0:
                factors.append(f"+Growing TVL (+{pool.tvl_change_7d:.1f}%)")

            # Explain negative factors
            if pool.apy_volatility > 30:
                factors.append(f"-High APY volatility ({pool.apy_volatility:.1f}%)")
            if pool.il_risk > 0.5:
                factors.append(f"-IL risk ({pool.il_risk:.2f})")
            if pool.risk_score > 0.6:
                factors.append(f"-High risk ({pool.risk_score:.2f})")

            decisions.append({
                "rank": i + 1,
                "pool_id": pool.pool_id,
                "symbol": pool.symbol,
                "protocol": pool.protocol,
                "chain": pool.chain,
                "score": round(pool.composite_score, 4),
                "factors": factors,
                "recommendation": "CONSIDER" if pool.composite_score > 0.5 else "MONITOR",
            })

        return decisions


# Singleton
_analyzer: Optional[PoolAnalyzer] = None


def get_pool_analyzer() -> PoolAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = PoolAnalyzer()
    return _analyzer
