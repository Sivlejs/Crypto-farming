"""
Opportunity Monitor for Nexus AI.

Runs all enabled strategies and aggregates discovered opportunities.
Thread-safe; results are stored in an in-memory queue for the executor.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, List, Optional

from nexus.blockchain import BlockchainManager
from nexus.strategies.base import Opportunity
from nexus.strategies.arbitrage import ArbitrageStrategy
from nexus.strategies.yield_farming import YieldFarmingStrategy
from nexus.strategies.liquidity_mining import LiquidityMiningStrategy
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MAX_HISTORY = 200  # keep last N opportunities


class OpportunityMonitor:
    """Continuously scans for farming opportunities across all strategies."""

    def __init__(self, blockchain_manager: BlockchainManager):
        self.bm = blockchain_manager
        self._strategies = self._build_strategies()
        self._lock = threading.Lock()
        self._opportunities: Deque[Opportunity] = deque(maxlen=MAX_HISTORY)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._scan_count = 0
        self._last_scan_ts: float = 0.0

    # ── Strategy initialisation ───────────────────────────────

    def _build_strategies(self):
        strategies = []
        if Config.STRATEGY_ARBITRAGE:
            strategies.append(ArbitrageStrategy(self.bm, Config))
        if Config.STRATEGY_YIELD_FARMING:
            strategies.append(YieldFarmingStrategy(self.bm, Config))
        if Config.STRATEGY_LIQUIDITY_MINING:
            strategies.append(LiquidityMiningStrategy(self.bm, Config))
        logger.info("Enabled strategies: %s", [s.name for s in strategies])
        return strategies

    # ── Background scanning ───────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        logger.info("OpportunityMonitor started (interval=%ds)", Config.SCAN_INTERVAL_SECONDS)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _scan_loop(self):
        while self._running:
            try:
                self._run_scan()
            except Exception as exc:
                logger.error("Scan error: %s", exc)
            time.sleep(Config.SCAN_INTERVAL_SECONDS)

    def _run_scan(self):
        start = time.time()
        found: List[Opportunity] = []
        for strategy in self._strategies:
            try:
                opps = strategy.find_opportunities()
                found.extend(opps)
                if opps:
                    logger.info(
                        "[%s] Found %d opportunity(-ies)", strategy.name, len(opps)
                    )
            except Exception as exc:
                logger.error("[%s] Strategy error: %s", strategy.name, exc)

        # Sort by composite score
        found.sort(key=lambda o: o.score(), reverse=True)

        with self._lock:
            for opp in found:
                self._opportunities.appendleft(opp)
            self._scan_count += 1
            self._last_scan_ts = time.time()

        elapsed = time.time() - start
        logger.info("Scan #%d complete in %.2fs – %d opportunities", self._scan_count, elapsed, len(found))

    # ── Public API ────────────────────────────────────────────

    def get_opportunities(self, limit: int = 50) -> List[Opportunity]:
        with self._lock:
            return list(self._opportunities)[:limit]

    def get_best_opportunity(self) -> Optional[Opportunity]:
        with self._lock:
            pending = [o for o in self._opportunities if not o.executed]
            if not pending:
                return None
            return max(pending, key=lambda o: o.score())

    def mark_executed(self, opp: Opportunity, tx_hash: str):
        opp.executed = True
        opp.tx_hash = tx_hash

    def status(self) -> dict:
        with self._lock:
            total = len(self._opportunities)
            executed = sum(1 for o in self._opportunities if o.executed)
            return {
                "running": self._running,
                "scan_count": self._scan_count,
                "last_scan": self._last_scan_ts,
                "total_found": total,
                "executed": executed,
                "pending": total - executed,
            }
