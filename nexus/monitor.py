"""
Opportunity Monitor for Nexus AI.

Upgrades over v1:
  • Includes flash arbitrage + liquidation strategies
  • Block-triggered scanning: reacts on every new block (not just timer)
  • Parallel strategy execution using ThreadPoolExecutor
  • Deduplication: same opportunity not reported twice per block
"""
from __future__ import annotations

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
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MAX_HISTORY = 500  # keep last N opportunities


class OpportunityMonitor:
    """
    Continuously scans for farming opportunities across all strategies.

    Block-triggered: registers as a block listener on the BlockchainManager
    so scans happen immediately when new blocks arrive rather than on a timer.
    """

    def __init__(self, blockchain_manager: BlockchainManager):
        self.bm = blockchain_manager
        self._strategies = self._build_strategies()
        self._lock = threading.Lock()
        self._opportunities: Deque[Opportunity] = deque(maxlen=MAX_HISTORY)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._scan_count = 0
        self._last_scan_ts: float = 0.0
        self._block_scan_pending = threading.Event()

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

        # Run all strategies in parallel
        with ThreadPoolExecutor(max_workers=len(self._strategies) or 1) as pool:
            futures = {
                pool.submit(strategy.find_opportunities): strategy
                for strategy in self._strategies
            }
            for future in as_completed(futures, timeout=30):
                strategy = futures[future]
                try:
                    opps = future.result()
                    found.extend(opps)
                    if opps:
                        logger.info(
                            "[%s] Found %d opportunity(-ies)",
                            strategy.name, len(opps),
                        )
                except Exception as exc:
                    logger.error("[%s] Strategy error: %s", strategy.name, exc)

        found.sort(key=lambda o: o.score(), reverse=True)

        with self._lock:
            for opp in found:
                self._opportunities.appendleft(opp)
            self._scan_count += 1
            self._last_scan_ts = time.time()

        elapsed = time.time() - start
        logger.info(
            "Scan #%d in %.2fs – %d opportunities found",
            self._scan_count, elapsed, len(found),
        )

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
