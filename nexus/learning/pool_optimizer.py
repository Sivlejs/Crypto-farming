"""
Pool Optimizer — Dynamic Pool Selection & Profit Optimization for Nexus AI.

Provides intelligent decision-making for:
  1. Dynamic pool selection based on real-time conditions
  2. Profit split optimization across multiple pools
  3. Transaction cost optimization (gas timing, batch operations)
  4. Rebalancing decisions based on changing market conditions

The optimizer works in tandem with the PoolAnalyzer to make real-time
allocation decisions that consistently outperform typical farming strategies.

Usage:
    optimizer = get_pool_optimizer()
    decision = optimizer.get_optimal_allocation(capital=1000, risk="medium")
    split = optimizer.optimize_profit_split(pools, capital=1000)
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np

from nexus.learning.pool_analyzer import (
    get_pool_analyzer,
    PoolAnalyzer,
    PoolMetrics,
    AllocationRecommendation,
)
from nexus.timing.gas_oracle import get_gas_oracle
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)


class OptimizationStrategy(Enum):
    """Available optimization strategies."""
    MAX_YIELD = "max_yield"           # Maximize APY regardless of risk
    RISK_ADJUSTED = "risk_adjusted"   # Maximize Sharpe-like ratio
    CONSERVATIVE = "conservative"     # Minimize risk, stable returns
    BALANCED = "balanced"             # Balance between yield and risk
    GAS_EFFICIENT = "gas_efficient"   # Minimize transaction costs


class RebalanceReason(Enum):
    """Reasons for triggering a rebalance."""
    APY_DRIFT = "apy_drift"           # APY changed significantly
    TVL_DROP = "tvl_drop"             # TVL dropped (risk signal)
    BETTER_POOL = "better_pool"       # Found better opportunity
    RISK_INCREASE = "risk_increase"   # Risk score increased
    GAS_OPTIMAL = "gas_optimal"       # Good gas conditions for rebalance
    SCHEDULED = "scheduled"           # Regular scheduled rebalance
    MANUAL = "manual"                 # User-triggered


@dataclass
class PoolPosition:
    """Current position in a farming pool."""
    pool_id: str
    chain: str
    protocol: str
    symbol: str
    entry_amount_usd: float
    current_value_usd: float
    entry_apy: float
    current_apy: float
    entry_timestamp: float
    last_update: float
    accrued_rewards_usd: float = 0.0
    unrealized_il_usd: float = 0.0

    @property
    def pnl_usd(self) -> float:
        """Total PnL including rewards and IL."""
        return (
            self.current_value_usd - self.entry_amount_usd +
            self.accrued_rewards_usd - self.unrealized_il_usd
        )

    @property
    def pnl_pct(self) -> float:
        """PnL as percentage of entry amount."""
        if self.entry_amount_usd == 0:
            return 0.0
        return (self.pnl_usd / self.entry_amount_usd) * 100

    @property
    def duration_hours(self) -> float:
        """Time in position in hours."""
        return (time.time() - self.entry_timestamp) / 3600

    def to_dict(self) -> dict:
        return {
            "pool_id": self.pool_id,
            "chain": self.chain,
            "protocol": self.protocol,
            "symbol": self.symbol,
            "entry_amount_usd": round(self.entry_amount_usd, 2),
            "current_value_usd": round(self.current_value_usd, 2),
            "entry_apy": round(self.entry_apy, 2),
            "current_apy": round(self.current_apy, 2),
            "pnl_usd": round(self.pnl_usd, 2),
            "pnl_pct": round(self.pnl_pct, 2),
            "duration_hours": round(self.duration_hours, 2),
            "accrued_rewards_usd": round(self.accrued_rewards_usd, 2),
            "unrealized_il_usd": round(self.unrealized_il_usd, 2),
        }


@dataclass
class OptimizationDecision:
    """A decision made by the optimizer."""
    action: str  # "enter", "exit", "rebalance", "hold"
    pool_id: str
    amount_usd: float
    reason: str
    confidence: float
    expected_impact_usd: float
    gas_cost_estimate_usd: float
    urgency: str  # "immediate", "normal", "low"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "pool_id": self.pool_id,
            "amount_usd": round(self.amount_usd, 2),
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "expected_impact_usd": round(self.expected_impact_usd, 2),
            "gas_cost_estimate_usd": round(self.gas_cost_estimate_usd, 2),
            "urgency": self.urgency,
            "timestamp": self.timestamp,
        }


@dataclass
class ProfitSplit:
    """Optimized profit split across pools."""
    allocations: List[Dict]  # [{"pool_id": ..., "amount_usd": ..., "pct": ...}]
    total_expected_apy: float
    weighted_risk: float
    reasoning: str
    rebalance_needed: bool = False
    rebalance_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "allocations": self.allocations,
            "total_expected_apy": round(self.total_expected_apy, 2),
            "weighted_risk": round(self.weighted_risk, 3),
            "reasoning": self.reasoning,
            "rebalance_needed": self.rebalance_needed,
            "rebalance_reason": self.rebalance_reason,
        }


class PoolOptimizer:
    """
    AI-powered pool optimizer for dynamic selection and profit optimization.

    Features:
      - Real-time pool selection based on multiple factors
      - Profit split optimization across pools
      - Gas-aware transaction timing
      - Automatic rebalancing triggers
      - Position tracking and PnL monitoring
    """

    def __init__(self):
        self._analyzer = get_pool_analyzer()
        self._gas_oracle = get_gas_oracle()

        # Current positions
        self._positions: Dict[str, PoolPosition] = {}
        self._position_lock = threading.Lock()

        # Decision history for learning
        self._decision_history: deque = deque(maxlen=500)

        # Optimization parameters (can be tuned over time)
        self._params = {
            "min_allocation_usd": 50.0,
            "max_allocation_pct": 40.0,
            "rebalance_threshold_pct": 20.0,  # APY drift to trigger rebalance
            "min_position_duration_hours": 24.0,  # Min time before exit
            "gas_cost_factor": 0.02,  # Max gas as % of capital
            "diversification_bonus": 0.1,  # Bonus for diversification
        }

        # Performance tracking
        self._total_decisions = 0
        self._successful_decisions = 0
        self._total_gas_saved_usd = 0.0

        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

        logger.info("PoolOptimizer initialized")

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Start background position monitoring."""
        if self._running:
            return
        self._running = True
        self._analyzer.start()
        self._gas_oracle.start()

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="pool-optimizer"
        )
        self._monitor_thread.start()
        logger.info("PoolOptimizer started")

    def stop(self):
        self._running = False
        self._analyzer.stop()
        self._gas_oracle.stop()

    def _monitor_loop(self):
        """Background loop to monitor positions and trigger rebalances."""
        while self._running:
            try:
                self._update_positions()
                self._check_rebalance_triggers()
            except Exception as exc:
                logger.warning("Position monitoring error: %s", exc)
            time.sleep(60)  # Check every minute

    # ── Core Optimization API ─────────────────────────────────

    def get_optimal_allocation(
        self,
        capital_usd: float,
        strategy: OptimizationStrategy = OptimizationStrategy.BALANCED,
        chains: Optional[List[str]] = None,
        exclude_pools: Optional[List[str]] = None,
        max_pools: int = 5,
    ) -> Tuple[List[AllocationRecommendation], ProfitSplit]:
        """
        Get optimal pool allocation for given capital.

        Parameters
        ----------
        capital_usd : float
            Total capital to allocate
        strategy : OptimizationStrategy
            Optimization strategy to use
        chains : list, optional
            Limit to specific chains
        exclude_pools : list, optional
            Pool IDs to exclude
        max_pools : int
            Maximum number of pools

        Returns
        -------
        Tuple[List[AllocationRecommendation], ProfitSplit]
            Recommendations and optimized profit split
        """
        # Map strategy to risk tolerance
        risk_map = {
            OptimizationStrategy.MAX_YIELD: "high",
            OptimizationStrategy.RISK_ADJUSTED: "medium",
            OptimizationStrategy.CONSERVATIVE: "low",
            OptimizationStrategy.BALANCED: "medium",
            OptimizationStrategy.GAS_EFFICIENT: "medium",
        }
        risk_tolerance = risk_map.get(strategy, "medium")

        # Get base recommendations from analyzer
        recommendations = self._analyzer.recommend_allocation(
            available_capital=capital_usd,
            risk_tolerance=risk_tolerance,
            chains=chains,
            max_pools=max_pools,
        )

        # Filter excluded pools
        if exclude_pools:
            recommendations = [
                r for r in recommendations
                if r.pool_id not in exclude_pools
            ]

        # Apply strategy-specific adjustments
        if strategy == OptimizationStrategy.MAX_YIELD:
            recommendations = self._optimize_for_yield(recommendations)
        elif strategy == OptimizationStrategy.GAS_EFFICIENT:
            recommendations = self._optimize_for_gas(recommendations, capital_usd)
        elif strategy == OptimizationStrategy.CONSERVATIVE:
            recommendations = self._optimize_for_safety(recommendations)

        # Calculate profit split
        profit_split = self._calculate_profit_split(recommendations, capital_usd)

        # Record decision
        self._total_decisions += 1
        self._decision_history.append({
            "timestamp": time.time(),
            "capital_usd": capital_usd,
            "strategy": strategy.value,
            "pools_selected": len(recommendations),
            "expected_apy": profit_split.total_expected_apy,
        })

        return recommendations, profit_split

    def optimize_profit_split(
        self,
        pool_ids: List[str],
        capital_usd: float,
        constraints: Optional[Dict] = None,
    ) -> ProfitSplit:
        """
        Optimize profit split across specified pools.

        Parameters
        ----------
        pool_ids : list
            Pool IDs to consider
        capital_usd : float
            Total capital to allocate
        constraints : dict, optional
            Allocation constraints (min/max per pool)

        Returns
        -------
        ProfitSplit
            Optimized allocation
        """
        constraints = constraints or {}
        min_per_pool = constraints.get("min_per_pool", self._params["min_allocation_usd"])
        max_pct = constraints.get("max_pct", self._params["max_allocation_pct"])

        # Get pool metrics
        pools = []
        for pid in pool_ids:
            metrics = self._analyzer.get_pool(pid)
            if metrics:
                pools.append(metrics)

        if not pools:
            return ProfitSplit(
                allocations=[],
                total_expected_apy=0,
                weighted_risk=0,
                reasoning="No valid pools found",
            )

        # Calculate optimal weights using mean-variance optimization
        weights = self._mean_variance_optimization(pools)

        # Apply constraints
        allocations = []
        total_apy = 0
        total_risk = 0

        for pool, weight in zip(pools, weights):
            # Apply max allocation constraint
            weight = min(weight, max_pct / 100)
            amount = capital_usd * weight

            # Skip if below minimum
            if amount < min_per_pool:
                continue

            allocations.append({
                "pool_id": pool.pool_id,
                "symbol": pool.symbol,
                "protocol": pool.protocol,
                "chain": pool.chain,
                "amount_usd": round(amount, 2),
                "pct": round(weight * 100, 2),
                "expected_apy": round(pool.apy_total, 2),
            })

            total_apy += pool.apy_total * weight
            total_risk += pool.risk_score * weight

        # Normalize if needed
        if allocations:
            total_allocated = sum(a["amount_usd"] for a in allocations)
            if total_allocated < capital_usd * 0.95:
                # Scale up allocations
                scale = capital_usd / total_allocated
                for a in allocations:
                    a["amount_usd"] = round(a["amount_usd"] * scale, 2)
                    a["pct"] = round(a["pct"] * scale, 2)

        # Generate reasoning
        reasoning = self._generate_split_reasoning(allocations, pools)

        return ProfitSplit(
            allocations=allocations,
            total_expected_apy=round(total_apy, 2),
            weighted_risk=round(total_risk, 3),
            reasoning=reasoning,
        )

    def should_rebalance(
        self,
        position: PoolPosition,
        current_metrics: Optional[PoolMetrics] = None,
    ) -> Tuple[bool, Optional[RebalanceReason], str]:
        """
        Determine if a position should be rebalanced.

        Returns
        -------
        Tuple[bool, RebalanceReason, str]
            (should_rebalance, reason, explanation)
        """
        if not current_metrics:
            current_metrics = self._analyzer.get_pool(position.pool_id)

        if not current_metrics:
            return False, None, "Pool metrics unavailable"

        # Check minimum duration
        if position.duration_hours < self._params["min_position_duration_hours"]:
            return False, None, "Position too new"

        # Check APY drift
        apy_change_pct = abs(
            (current_metrics.apy_total - position.entry_apy) /
            max(position.entry_apy, 1) * 100
        )
        if apy_change_pct > self._params["rebalance_threshold_pct"]:
            return True, RebalanceReason.APY_DRIFT, f"APY changed {apy_change_pct:.1f}%"

        # Check TVL drop
        if current_metrics.tvl_change_7d < -20:
            return True, RebalanceReason.TVL_DROP, f"TVL dropped {current_metrics.tvl_change_7d:.1f}%"

        # Check risk increase
        # Would need entry_risk to compare, simplified here
        if current_metrics.risk_score > 0.7:
            return True, RebalanceReason.RISK_INCREASE, f"Risk increased to {current_metrics.risk_score:.2f}"

        # Check for better opportunities
        top_pools = self._analyzer.get_top_pools(
            chain=position.chain,
            limit=5,
            min_tvl=500_000,
        )
        if top_pools:
            best = top_pools[0]
            if (best.pool_id != position.pool_id and
                best.composite_score > current_metrics.composite_score * 1.3):
                return True, RebalanceReason.BETTER_POOL, f"Better pool: {best.symbol} ({best.protocol})"

        return False, None, "No rebalance needed"

    def get_optimization_decision(
        self,
        capital_usd: float,
        action_type: str = "enter",  # "enter", "exit", "rebalance"
        pool_id: Optional[str] = None,
    ) -> OptimizationDecision:
        """
        Get a specific optimization decision with full analysis.
        """
        # Get current gas conditions
        current_gas = self._gas_oracle.current_base_fee() or 30
        is_cheap_gas = self._gas_oracle.is_cheap_now()

        # Estimate gas cost
        gas_cost_usd = self._estimate_gas_cost(current_gas)

        if action_type == "enter":
            # Find best pool for entry
            top_pools = self._analyzer.get_top_pools(limit=10)
            if not top_pools:
                return OptimizationDecision(
                    action="hold",
                    pool_id="",
                    amount_usd=0,
                    reason="No suitable pools available",
                    confidence=0.5,
                    expected_impact_usd=0,
                    gas_cost_estimate_usd=gas_cost_usd,
                    urgency="low",
                )

            best = top_pools[0]

            # Check if gas cost is acceptable
            gas_ratio = gas_cost_usd / capital_usd
            if gas_ratio > self._params["gas_cost_factor"] and not is_cheap_gas:
                return OptimizationDecision(
                    action="hold",
                    pool_id=best.pool_id,
                    amount_usd=capital_usd,
                    reason=f"Gas too expensive ({gas_ratio*100:.1f}% of capital), wait for cheaper",
                    confidence=0.7,
                    expected_impact_usd=-gas_cost_usd,
                    gas_cost_estimate_usd=gas_cost_usd,
                    urgency="low",
                )

            expected_daily = capital_usd * (best.apy_total / 365 / 100)
            return OptimizationDecision(
                action="enter",
                pool_id=best.pool_id,
                amount_usd=capital_usd,
                reason=f"Best opportunity: {best.symbol} @ {best.apy_total:.1f}% APY",
                confidence=best.confidence,
                expected_impact_usd=expected_daily,
                gas_cost_estimate_usd=gas_cost_usd,
                urgency="normal" if is_cheap_gas else "low",
            )

        elif action_type == "exit" and pool_id:
            position = self._positions.get(pool_id)
            if not position:
                return OptimizationDecision(
                    action="hold",
                    pool_id=pool_id,
                    amount_usd=0,
                    reason="Position not found",
                    confidence=0.5,
                    expected_impact_usd=0,
                    gas_cost_estimate_usd=gas_cost_usd,
                    urgency="low",
                )

            return OptimizationDecision(
                action="exit",
                pool_id=pool_id,
                amount_usd=position.current_value_usd,
                reason=f"Exit with PnL: ${position.pnl_usd:.2f} ({position.pnl_pct:.1f}%)",
                confidence=0.8,
                expected_impact_usd=position.pnl_usd - gas_cost_usd,
                gas_cost_estimate_usd=gas_cost_usd,
                urgency="normal",
            )

        return OptimizationDecision(
            action="hold",
            pool_id=pool_id or "",
            amount_usd=capital_usd,
            reason="No action recommended",
            confidence=0.5,
            expected_impact_usd=0,
            gas_cost_estimate_usd=gas_cost_usd,
            urgency="low",
        )

    # ── Position Management ───────────────────────────────────

    def record_entry(
        self,
        pool_id: str,
        amount_usd: float,
        entry_apy: float,
    ):
        """Record a new position entry."""
        metrics = self._analyzer.get_pool(pool_id)
        if not metrics:
            logger.warning("Recording entry for unknown pool: %s", pool_id)
            return

        position = PoolPosition(
            pool_id=pool_id,
            chain=metrics.chain,
            protocol=metrics.protocol,
            symbol=metrics.symbol,
            entry_amount_usd=amount_usd,
            current_value_usd=amount_usd,
            entry_apy=entry_apy,
            current_apy=metrics.apy_total,
            entry_timestamp=time.time(),
            last_update=time.time(),
        )

        with self._position_lock:
            self._positions[pool_id] = position

        logger.info(
            "Position entered: %s (%s) - $%.2f @ %.1f%% APY",
            metrics.symbol, metrics.protocol, amount_usd, entry_apy
        )

    def record_exit(
        self,
        pool_id: str,
        exit_value_usd: float,
        rewards_usd: float = 0,
    ):
        """Record position exit and learn from outcome."""
        with self._position_lock:
            position = self._positions.pop(pool_id, None)

        if not position:
            logger.warning("Exit recorded for unknown position: %s", pool_id)
            return

        # Calculate final PnL
        position.current_value_usd = exit_value_usd
        position.accrued_rewards_usd = rewards_usd

        # Feed back to analyzer for learning
        self._analyzer.learn_from_outcome(
            pool_id=pool_id,
            actual_return=exit_value_usd + rewards_usd,
            duration_hours=position.duration_hours,
            success=position.pnl_usd > 0,
        )

        if position.pnl_usd > 0:
            self._successful_decisions += 1

        logger.info(
            "Position exited: %s - PnL: $%.2f (%.1f%%) over %.1fh",
            position.symbol, position.pnl_usd, position.pnl_pct, position.duration_hours
        )

    def get_positions(self) -> List[dict]:
        """Get all current positions."""
        with self._position_lock:
            return [p.to_dict() for p in self._positions.values()]

    def get_total_exposure(self) -> dict:
        """Get total exposure across all positions."""
        with self._position_lock:
            total_value = sum(p.current_value_usd for p in self._positions.values())
            total_pnl = sum(p.pnl_usd for p in self._positions.values())

            by_chain = {}
            by_protocol = {}
            for p in self._positions.values():
                by_chain[p.chain] = by_chain.get(p.chain, 0) + p.current_value_usd
                by_protocol[p.protocol] = by_protocol.get(p.protocol, 0) + p.current_value_usd

        return {
            "total_value_usd": round(total_value, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "position_count": len(self._positions),
            "by_chain": {k: round(v, 2) for k, v in by_chain.items()},
            "by_protocol": {k: round(v, 2) for k, v in by_protocol.items()},
        }

    # ── Internal Optimization Methods ─────────────────────────

    def _optimize_for_yield(
        self,
        recommendations: List[AllocationRecommendation],
    ) -> List[AllocationRecommendation]:
        """Adjust recommendations to maximize yield."""
        # Sort by APY and increase allocation to top performers
        sorted_recs = sorted(recommendations, key=lambda r: r.expected_apy, reverse=True)

        total = 100
        adjusted = []
        for i, rec in enumerate(sorted_recs[:3]):  # Focus on top 3
            # Exponential allocation favoring top yields
            alloc = total * (0.5 ** i)
            rec.allocation_pct = min(50, alloc)
            adjusted.append(rec)
            total -= rec.allocation_pct

        return adjusted

    def _optimize_for_gas(
        self,
        recommendations: List[AllocationRecommendation],
        capital_usd: float,
    ) -> List[AllocationRecommendation]:
        """Adjust recommendations to minimize gas costs."""
        # Prefer fewer, larger positions to minimize transactions
        # Also prefer cheaper chains

        chain_gas_costs = {
            "ethereum": 30,  # Relative gas cost
            "arbitrum": 1,
            "optimism": 1,
            "base": 1,
            "polygon": 5,
            "bsc": 3,
            "avalanche": 5,
        }

        # Score by APY / gas cost ratio
        scored = []
        for rec in recommendations:
            gas_cost = chain_gas_costs.get(rec.chain, 10)
            efficiency = rec.expected_apy / gas_cost
            scored.append((efficiency, rec))

        scored.sort(reverse=True)

        # Take top 2-3 most gas-efficient
        adjusted = []
        total_pct = 100
        for i, (_, rec) in enumerate(scored[:3]):
            rec.allocation_pct = total_pct / (3 - i)
            adjusted.append(rec)
            total_pct -= rec.allocation_pct

        return adjusted

    def _optimize_for_safety(
        self,
        recommendations: List[AllocationRecommendation],
    ) -> List[AllocationRecommendation]:
        """Adjust recommendations to minimize risk."""
        # Filter to low-risk only
        safe_recs = [r for r in recommendations if r.risk_level == "low"]

        if not safe_recs:
            # Fall back to medium risk
            safe_recs = [r for r in recommendations if r.risk_level in ("low", "medium")]

        # Equal weight among safe pools
        if safe_recs:
            equal_pct = 100 / len(safe_recs)
            for rec in safe_recs:
                rec.allocation_pct = equal_pct

        return safe_recs

    def _mean_variance_optimization(
        self,
        pools: List[PoolMetrics],
    ) -> List[float]:
        """
        Perform mean-variance optimization to find optimal weights.
        Simplified Markowitz-style optimization.
        """
        n = len(pools)
        if n == 0:
            return []
        if n == 1:
            return [1.0]

        # Extract returns and risks
        returns = np.array([p.apy_total for p in pools])
        risks = np.array([p.risk_score + p.apy_volatility / 100 for p in pools])

        # Sharpe-like ratio (simplified)
        sharpe = returns / (risks + 0.1)

        # Normalize to get weights
        weights = sharpe / sharpe.sum()

        # Apply minimum threshold
        min_weight = 0.05
        weights = np.clip(weights, min_weight, 1.0)
        weights = weights / weights.sum()  # Renormalize

        return weights.tolist()

    def _calculate_profit_split(
        self,
        recommendations: List[AllocationRecommendation],
        capital_usd: float,
    ) -> ProfitSplit:
        """Calculate the profit split from recommendations."""
        allocations = []
        total_apy = 0
        total_risk = 0

        for rec in recommendations:
            amount = capital_usd * (rec.allocation_pct / 100)
            allocations.append({
                "pool_id": rec.pool_id,
                "symbol": rec.symbol,
                "protocol": rec.protocol,
                "chain": rec.chain,
                "amount_usd": round(amount, 2),
                "pct": round(rec.allocation_pct, 2),
                "expected_apy": round(rec.expected_apy, 2),
            })

            weight = rec.allocation_pct / 100
            total_apy += rec.expected_apy * weight

            risk_map = {"low": 0.2, "medium": 0.5, "high": 0.8}
            total_risk += risk_map.get(rec.risk_level, 0.5) * weight

        reasoning = self._generate_split_reasoning(allocations, [])

        return ProfitSplit(
            allocations=allocations,
            total_expected_apy=round(total_apy, 2),
            weighted_risk=round(total_risk, 3),
            reasoning=reasoning,
        )

    def _generate_split_reasoning(
        self,
        allocations: List[dict],
        pools: List[PoolMetrics],
    ) -> str:
        """Generate human-readable reasoning for the split."""
        if not allocations:
            return "No allocations recommended"

        parts = []

        # Diversification analysis
        chains = set(a["chain"] for a in allocations)
        protocols = set(a["protocol"] for a in allocations)
        parts.append(f"Diversified across {len(chains)} chains and {len(protocols)} protocols")

        # Risk analysis
        avg_apy = sum(a["expected_apy"] * a["pct"] for a in allocations) / 100
        parts.append(f"Weighted APY: {avg_apy:.1f}%")

        # Top allocation
        top = max(allocations, key=lambda a: a["pct"])
        parts.append(f"Largest position: {top['symbol']} ({top['pct']:.0f}%)")

        return "; ".join(parts)

    def _estimate_gas_cost(self, gas_gwei: float) -> float:
        """Estimate gas cost in USD for a typical farming transaction."""
        # Assume ~200k gas for a typical swap/deposit
        gas_units = 200_000
        # Assume ETH price ~$3000 (would get from price feed in production)
        eth_price = 3000

        gas_eth = (gas_gwei * gas_units) / 1e9
        return gas_eth * eth_price

    def _update_positions(self):
        """Update all position values."""
        with self._position_lock:
            for pid, position in self._positions.items():
                metrics = self._analyzer.get_pool(pid)
                if metrics:
                    position.current_apy = metrics.apy_total
                    position.last_update = time.time()

                    # Estimate current value based on time and APY
                    hours_elapsed = position.duration_hours
                    daily_rate = position.current_apy / 365 / 100
                    hourly_rate = daily_rate / 24
                    estimated_growth = position.entry_amount_usd * hourly_rate * hours_elapsed
                    position.accrued_rewards_usd = estimated_growth

    def _check_rebalance_triggers(self):
        """Check all positions for rebalance conditions."""
        with self._position_lock:
            positions = list(self._positions.values())

        for position in positions:
            should, reason, explanation = self.should_rebalance(position)
            if should:
                logger.info(
                    "Rebalance trigger for %s: %s - %s",
                    position.symbol, reason.value if reason else "unknown", explanation
                )
                # In production, this would trigger actual rebalancing

    # ── Status & Reporting ────────────────────────────────────

    def status(self) -> dict:
        """Return optimizer status."""
        exposure = self.get_total_exposure()

        return {
            "running": self._running,
            "positions": len(self._positions),
            "total_exposure_usd": exposure["total_value_usd"],
            "total_pnl_usd": exposure["total_pnl_usd"],
            "total_decisions": self._total_decisions,
            "successful_decisions": self._successful_decisions,
            "success_rate": round(
                self._successful_decisions / max(1, self._total_decisions) * 100, 1
            ),
            "params": self._params,
            "analyzer_status": self._analyzer.status(),
        }

    def get_decision_history(self, limit: int = 20) -> List[dict]:
        """Get recent optimization decisions."""
        return list(self._decision_history)[-limit:]


# Singleton
_optimizer: Optional[PoolOptimizer] = None


def get_pool_optimizer() -> PoolOptimizer:
    global _optimizer
    if _optimizer is None:
        _optimizer = PoolOptimizer()
    return _optimizer
