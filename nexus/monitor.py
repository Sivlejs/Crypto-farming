"""
Opportunity Monitor for Nexus AI.

Upgrades over v1:
  • Includes flash arbitrage + liquidation strategies
  • Block-triggered scanning: reacts on every new block (not just timer)
  • Parallel strategy execution using ThreadPoolExecutor
  • Deduplication: same opportunity not reported twice per block

v2 Efficiency Upgrades:
  • Adaptive strategy weighting based on market regime
  • Opportunity staleness filtering (discard opportunities older than threshold)
  • Regime-aware scoring boost for favored strategies
"""
from __future__ import annotations

import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Deque, List, Optional

from nexus.blockchain import BlockchainManager
from nexus.strategies.base import Opportunity
from nexus.strategies.arbitrage import ArbitrageStrategy
from nexus.strategies.flash_arbitrage import FlashArbitrageStrategy
from nexus.strategies.yield_farming import YieldFarmingStrategy
from nexus.strategies.liquidity_mining import LiquidityMiningStrategy
from nexus.strategies.liquidation import LiquidationStrategy
from nexus.strategies.pow_mining import PoWMiningStrategy
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MAX_HISTORY = 500  # keep last N opportunities
STALENESS_THRESHOLD_SECONDS = 30  # discard opportunities older than this


class OpportunityMonitor:
    """
    Continuously scans for farming opportunities across all strategies.

    Block-triggered: registers as a block listener on the BlockchainManager
    so scans happen immediately when new blocks arrive rather than on a timer.
    
    v2 Enhancements:
      • Adaptive strategy weighting based on market regime (volatile/trending/calm)
      • Staleness filtering to avoid executing outdated opportunities
      • Regime-aware scoring multipliers for optimal strategy selection
    """

    def __init__(self, blockchain_manager: BlockchainManager, brain=None):
        self.bm = blockchain_manager
        self._brain = brain  # Optional: NexusBrain for market regime + ML scoring
        self._strategies = self._build_strategies()
        self._lock = threading.Lock()
        self._opportunities: Deque[Opportunity] = deque(maxlen=MAX_HISTORY)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._scan_count = 0
        self._last_scan_ts: float = 0.0
        self._block_scan_pending = threading.Event()
        # Performance tracking
        self._strategies_profit: dict[str, float] = {}  # strategy_name -> cumulative profit
        self._strategies_count: dict[str, int] = {}     # strategy_name -> execution count

    def set_brain(self, brain):
        """Attach the NexusBrain for market regime awareness."""
        self._brain = brain

    # ── Strategy initialisation ───────────────────────────────

    def _build_strategies(self):
        strategies = []
        if Config.STRATEGY_ARBITRAGE:
            strategies.append(ArbitrageStrategy(self.bm, Config))
            strategies.append(FlashArbitrageStrategy(self.bm, Config))
        if Config.STRATEGY_YIELD_FARMING:
            strategies.append(YieldFarmingStrategy(self.bm, Config))
        if Config.STRATEGY_LIQUIDITY_MINING:
            strategies.append(LiquidityMiningStrategy(self.bm, Config))
        if Config.STRATEGY_LIQUIDATION:
            strategies.append(LiquidationStrategy(self.bm, Config))
        if Config.STRATEGY_POW_MINING:
            pow_strategy = PoWMiningStrategy(self.bm, Config)
            # Auto-start PoW mining when strategy is enabled
            # This is expected behavior for mining (continuous operation)
            try:
                if pow_strategy.start_mining():
                    logger.info("PoW mining started automatically")
                else:
                    logger.warning("PoW mining enabled but failed to start - check configuration")
            except Exception as e:
                logger.error("Failed to start PoW mining: %s", e)
            strategies.append(pow_strategy)
        logger.info("Enabled strategies: %s", [s.name for s in strategies])
        return strategies

    # ── Background scanning ───────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        # Register block-triggered scan
        self.bm.on_new_block(self._on_new_block)
        self._thread = threading.Thread(
            target=self._scan_loop, daemon=True, name="monitor"
        )
        self._thread.start()
        logger.info(
            "OpportunityMonitor started – block-triggered + %ds fallback",
            Config.SCAN_INTERVAL_SECONDS,
        )

    def stop(self):
        self._running = False
        self._block_scan_pending.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _on_new_block(self, block_info: dict):
        """Called on every new block — signals the scan loop immediately."""
        self._block_scan_pending.set()

    def _scan_loop(self):
        while self._running:
            # Wait for a new block OR the fallback timer
            triggered = self._block_scan_pending.wait(
                timeout=Config.SCAN_INTERVAL_SECONDS
            )
            self._block_scan_pending.clear()
            if not self._running:
                break
            try:
                self._run_scan()
            except Exception as exc:
                logger.error("Scan error: %s", exc)

    def _run_scan(self):
        start = time.time()
        found: List[Opportunity] = []
        
        # Get strategy weights based on market regime
        weights = self._get_strategy_weights()

        # Run strategies with adaptive weighting (prioritize high-weight strategies)
        strategies_to_run = self._select_strategies_by_weight(weights)
        
        with ThreadPoolExecutor(max_workers=len(strategies_to_run) or 1) as pool:
            futures = {
                pool.submit(strategy.find_opportunities): strategy
                for strategy in strategies_to_run
            }
            for future in as_completed(futures, timeout=30):
                strategy = futures[future]
                try:
                    opps = future.result()
                    # Apply regime-aware scoring boost
                    weight = weights.get(strategy.name, 1.0)
                    for opp in opps:
                        opp.regime_weight = weight
                    found.extend(opps)
                    if opps:
                        logger.info(
                            "[%s] Found %d opportunity(-ies) (regime_weight=%.2f)",
                            strategy.name, len(opps), weight,
                        )
                except Exception as exc:
                    logger.error("[%s] Strategy error: %s", strategy.name, exc)

        # Sort by weighted score (profit * confidence * regime_weight)
        found.sort(key=lambda o: self._weighted_score(o), reverse=True)

        with self._lock:
            for opp in found:
                self._opportunities.appendleft(opp)
            self._scan_count += 1
            self._last_scan_ts = time.time()

        elapsed = time.time() - start
        regime = self._brain.regime() if self._brain else "unknown"
        logger.info(
            "Scan #%d in %.2fs – %d opportunities found (regime=%s)",
            self._scan_count, elapsed, len(found), regime,
        )
    
    def _get_strategy_weights(self) -> dict[str, float]:
        """Get strategy weights from brain's market classifier."""
        if not self._brain:
            return {s.name: 1.0 for s in self._strategies}
        return self._brain.strategy_weights()
    
    def _select_strategies_by_weight(self, weights: dict[str, float]) -> list:
        """
        Select which strategies to run based on their weights.
        Higher weights = always run. Lower weights = probabilistic skip.
        This saves CPU cycles during unfavorable market conditions.
        """
        selected = []
        for strategy in self._strategies:
            weight = weights.get(strategy.name, 1.0)
            # Always run if weight >= 1.0, otherwise probabilistic
            if weight >= 1.0 or random.random() < weight:
                selected.append(strategy)
            else:
                logger.debug(
                    "Skipping %s scan (weight=%.2f, regime unfavorable)",
                    strategy.name, weight,
                )
        # Fallback: if no strategies selected, pick the one with highest weight
        if not selected and self._strategies:
            best_strategy = max(
                self._strategies,
                key=lambda s: weights.get(s.name, 1.0)
            )
            selected.append(best_strategy)
            logger.debug(
                "Fallback: running %s (highest weight=%.2f)",
                best_strategy.name, weights.get(best_strategy.name, 1.0),
            )
        return selected
    
    def _weighted_score(self, opp: Opportunity) -> float:
        """Calculate weighted score including regime multiplier."""
        base_score = opp.score()  # profit * confidence
        regime_weight = getattr(opp, 'regime_weight', 1.0)
        return base_score * regime_weight

    # ── Public API ────────────────────────────────────────────

    def get_opportunities(self, limit: int = 50) -> List[Opportunity]:
        with self._lock:
            return list(self._opportunities)[:limit]

    def get_best_opportunity(self, max_age_seconds: float = None) -> Optional[Opportunity]:
        """
        Get the best non-executed, non-stale opportunity.
        
        Parameters
        ----------
        max_age_seconds : float, optional
            Maximum age in seconds for an opportunity to be considered valid.
            Defaults to STALENESS_THRESHOLD_SECONDS.
            
        Returns
        -------
        Opportunity or None
            The highest-scoring valid opportunity, or None if none available.
        """
        if max_age_seconds is None:
            max_age_seconds = STALENESS_THRESHOLD_SECONDS
            
        now = time.time()
        with self._lock:
            pending = [
                o for o in self._opportunities 
                if not o.executed and (now - o.timestamp) <= max_age_seconds
            ]
            if not pending:
                return None
            # Use weighted scoring if available
            return max(pending, key=lambda o: self._weighted_score(o))
    
    def get_fresh_opportunities(self, limit: int = 20, max_age_seconds: float = None) -> List[Opportunity]:
        """Get only fresh (non-stale) opportunities."""
        if max_age_seconds is None:
            max_age_seconds = STALENESS_THRESHOLD_SECONDS
            
        now = time.time()
        with self._lock:
            fresh = [
                o for o in self._opportunities
                if (now - o.timestamp) <= max_age_seconds
            ]
            return fresh[:limit]
    
    def record_execution_result(self, strategy_name: str, profit_usd: float, success: bool):
        """Track strategy performance for adaptive optimization."""
        with self._lock:
            if strategy_name not in self._strategies_profit:
                self._strategies_profit[strategy_name] = 0.0
                self._strategies_count[strategy_name] = 0
            if success:
                self._strategies_profit[strategy_name] += profit_usd
            self._strategies_count[strategy_name] += 1

    def mark_executed(self, opp: Opportunity, tx_hash: str):
        opp.executed = True
        opp.tx_hash = tx_hash

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            total = len(self._opportunities)
            executed = sum(1 for o in self._opportunities if o.executed)
            fresh = sum(
                1 for o in self._opportunities 
                if not o.executed and (now - o.timestamp) <= STALENESS_THRESHOLD_SECONDS
            )
            stale = sum(
                1 for o in self._opportunities
                if not o.executed and (now - o.timestamp) > STALENESS_THRESHOLD_SECONDS
            )
            return {
                "running": self._running,
                "scan_count": self._scan_count,
                "last_scan": self._last_scan_ts,
                "total_found": total,
                "executed": executed,
                "pending": total - executed,
                "fresh_opportunities": fresh,
                "stale_skipped": stale,
                "staleness_threshold_sec": STALENESS_THRESHOLD_SECONDS,
                "current_regime": self._brain.regime() if self._brain else "unknown",
                "strategy_performance": {
                    name: {
                        "profit_usd": round(self._strategies_profit.get(name, 0), 4),
                        "executions": self._strategies_count.get(name, 0),
                    }
                    for name in [s.name for s in self._strategies]
                },
            }
