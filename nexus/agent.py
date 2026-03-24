"""
Nexus AI – Main Orchestrator

Ties together the blockchain manager, opportunity monitor, executor,
and reward tracker into a single agent that can be started/stopped
and queried for status.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from nexus.blockchain import get_blockchain_manager, BlockchainManager
from nexus.monitor import OpportunityMonitor
from nexus.executor import TransactionExecutor
from nexus.rewards import RewardTracker
from nexus.payout import get_payout_manager, PayoutManager
from nexus.feeds.price_feed import get_price_feed, PriceFeed
from nexus.execution.bundle_submitter import get_bundle_submitter
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)


class NexusAgent:
    """
    Nexus AI – autonomous crypto farming agent.

    Flow:
      1. BlockchainManager connects to enabled chains.
      2. OpportunityMonitor scans for opportunities.
      3. For each profitable opportunity, TransactionExecutor executes it.
      4. RewardTracker records all outcomes.
    """

    def __init__(self):
        self.blockchain: BlockchainManager = get_blockchain_manager()
        self.monitor: OpportunityMonitor = OpportunityMonitor(self.blockchain)
        self.executor: TransactionExecutor = TransactionExecutor(self.blockchain)
        self.tracker: RewardTracker = RewardTracker()
        self.payout: PayoutManager = get_payout_manager(self.blockchain)
        self.feed: PriceFeed = get_price_feed()
        self.bundler = get_bundle_submitter()
        self._running = False
        self._exec_thread: Optional[threading.Thread] = None
        self._start_time: Optional[float] = None

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        if self._running:
            logger.warning("NexusAgent already running.")
            return
        logger.info(
            "Starting Nexus AI | dry_run=%s | wallet=%s",
            Config.DRY_RUN,
            "configured" if Config.is_configured() else "NOT configured",
        )
        self._running = True
        self._start_time = time.time()
        self.feed.start()           # Start real-time price feed
        self.monitor.start()        # Start block-triggered opportunity scanner
        self._exec_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._exec_thread.start()
        logger.info("Nexus AI is running.")

    def stop(self):
        logger.info("Stopping Nexus AI…")
        self._running = False
        self.feed.stop()
        self.monitor.stop()
        self.blockchain.stop()
        if self._exec_thread:
            self._exec_thread.join(timeout=10)
        logger.info("Nexus AI stopped.")

    # ── Execution loop ────────────────────────────────────────

    def _execution_loop(self):
        """Periodically pick the best opportunity and execute it."""
        while self._running:
            try:
                self._try_execute()
            except Exception as exc:
                logger.error("Execution loop error: %s", exc)
            time.sleep(Config.SCAN_INTERVAL_SECONDS)

    def _try_execute(self):
        opp = self.monitor.get_best_opportunity()
        if not opp:
            return

        if opp.estimated_profit_usd < Config.MIN_PROFIT_USD:
            return

        logger.info(
            "Executing best opportunity: %s (score=%.4f, profit=$%.4f)",
            opp.description,
            opp.score(),
            opp.estimated_profit_usd,
        )

        tx_hash = self.executor.execute(opp)
        self.monitor.mark_executed(opp, tx_hash or "failed")
        self.tracker.record(
            opp=opp,
            tx_hash=tx_hash,
            actual_profit_usd=opp.estimated_profit_usd if tx_hash else None,
            dry_run=Config.DRY_RUN,
        )

        # Queue profit for automatic payout to Coinbase / Cash App
        if tx_hash:
            self.payout.queue(opp.estimated_profit_usd, opp.chain)

    # ── Status / reporting ────────────────────────────────────

    def status(self) -> dict:
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "running": self._running,
            "uptime_seconds": round(uptime),
            "dry_run": Config.DRY_RUN,
            "config": Config.to_dict(),
            "blockchain": self.blockchain.status(),
            "monitor": self.monitor.status(),
            "rewards": self.tracker.get_stats(),
            "payout": self.payout.status(),
            "prices": self.feed.all_prices(),
            "flashbots_ready": self.bundler.is_available(),
        }

    def get_opportunities(self, limit: int = 20) -> list:
        return [o.to_dict() for o in self.monitor.get_opportunities(limit=limit)]

    def get_recent_trades(self, limit: int = 50) -> list:
        return self.tracker.get_recent_trades(limit=limit)

    def get_payout_history(self, limit: int = 20) -> list:
        return self.payout.history(limit=limit)

    def force_payout(self, chain: str = "ethereum") -> dict:
        return self.payout.sweep_now(chain=chain)


# ── Singleton ─────────────────────────────────────────────────

_agent: Optional[NexusAgent] = None
_agent_lock = threading.Lock()


def get_agent() -> NexusAgent:
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = NexusAgent()
    return _agent
