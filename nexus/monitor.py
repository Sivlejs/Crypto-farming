"""
Opportunity Monitor for Nexus AI.

Focused on Proof-of-Work mining strategy only.
Provides block-triggered scanning and opportunity tracking.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, List, Optional

from nexus.blockchain import BlockchainManager
from nexus.strategies.base import Opportunity
from nexus.strategies.pow_mining import PoWMiningStrategy
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MAX_HISTORY = 500  # keep last N opportunities
STALENESS_THRESHOLD_SECONDS = 30  # discard opportunities older than this


class OpportunityMonitor:
    """
    Monitors PoW mining operations and tracks opportunities.

    Simplified to focus exclusively on Proof-of-Work mining strategy
    for maximum reliability and performance.
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
        """Initialize PoW mining strategy only."""
        strategies = []
        
        # PoW Mining is the only strategy - always enabled
        pow_strategy = PoWMiningStrategy(self.bm, Config)
        # Auto-start PoW mining when strategy is created
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

        # Run PoW mining strategy to get current mining status
        for strategy in self._strategies:
            try:
                opps = strategy.find_opportunities()
                for opp in opps:
                    opp.regime_weight = 1.0  # Mining always has full weight
                found.extend(opps)
                if opps:
                    logger.info(
                        "[%s] Found %d opportunity(-ies)",
                        strategy.name, len(opps),
                    )
            except Exception as exc:
                logger.error("[%s] Strategy error: %s", strategy.name, exc)

        # Sort by score
        found.sort(key=lambda o: self._weighted_score(o), reverse=True)

        with self._lock:
            for opp in found:
                self._opportunities.appendleft(opp)
            self._scan_count += 1
            self._last_scan_ts = time.time()

        elapsed = time.time() - start
        logger.debug(
            "Scan #%d in %.2fs – %d opportunities found",
            self._scan_count, elapsed, len(found),
        )
    
    def _weighted_score(self, opp: Opportunity) -> float:
        """Calculate weighted score."""
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
                "current_regime": self._brain.regime() if self._brain else "mining",
                "strategy_performance": {
                    name: {
                        "profit_usd": round(self._strategies_profit.get(name, 0), 4),
                        "executions": self._strategies_count.get(name, 0),
                    }
                    for name in [s.name for s in self._strategies]
                },
            }
