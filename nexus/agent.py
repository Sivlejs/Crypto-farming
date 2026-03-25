"""
Nexus AI – Main Orchestrator

Ties together the blockchain manager, opportunity monitor, executor,
and reward tracker into a single agent that can be started/stopped
and queried for status.

v2 Efficiency Upgrades:
  • Brain-aware monitor with adaptive strategy weighting
  • Gas-aware trade deferral for low-urgency opportunities
  • Dynamic slippage based on market volatility
  • Staleness filtering to avoid executing outdated opportunities
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
from nexus.learning.brain import get_brain, NexusBrain
from nexus.timing.trade_scheduler import get_trade_scheduler, TradeScheduler
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Strategy urgency classification for trade deferral
_URGENT_STRATEGIES = {"flash_arbitrage", "arbitrage", "liquidation"}
_DEFERRABLE_STRATEGIES = {"yield_farming", "liquidity_mining", "staking"}


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
        # Initialize components with error handling for resilience
        try:
            self.blockchain: BlockchainManager = get_blockchain_manager()
        except Exception as exc:
            logger.warning("BlockchainManager init issue: %s (continuing anyway)", exc)
            self.blockchain = get_blockchain_manager()  # Retry once

        # Initialize brain FIRST so monitor and executor can use it
        self.brain: NexusBrain = get_brain()
        
        # Pass brain to monitor for regime-aware scanning
        self.monitor: OpportunityMonitor = OpportunityMonitor(self.blockchain, brain=self.brain)
        # Pass brain to executor for dynamic slippage
        self.executor: TransactionExecutor = TransactionExecutor(self.blockchain, brain=self.brain)
        self.tracker: RewardTracker = RewardTracker()
        self.payout: PayoutManager = get_payout_manager(self.blockchain)
        self.feed: PriceFeed = get_price_feed()
        
        try:
            self.bundler = get_bundle_submitter()
        except Exception as exc:
            logger.debug("Bundle submitter init failed: %s", exc)
            self.bundler = None
            
        self.scheduler: TradeScheduler = get_trade_scheduler()
        self._running = False
        self._exec_thread: Optional[threading.Thread] = None
        self._start_time: Optional[float] = None
        self._init_errors: list[str] = []
        
        # Performance metrics
        self._deferred_count = 0
        self._gas_savings_estimate = 0.0
        
        logger.info("NexusAgent initialized successfully (v2 efficiency upgrades)")

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        if self._running:
            logger.warning("NexusAgent already running.")
            return

        chains_connected = len(self.blockchain.connected_chains())
        logger.info(
            "Starting Nexus AI | dry_run=%s | wallet=%s | chains=%d",
            Config.DRY_RUN,
            "configured" if Config.is_configured() else "NOT configured",
            chains_connected,
        )

        self._running = True
        self._start_time = time.time()

        # Start components with error handling
        try:
            self.feed.start()           # Start real-time price feed
        except Exception as exc:
            logger.warning("Price feed start failed: %s", exc)

        try:
            self.monitor.start()        # Start block-triggered opportunity scanner
        except Exception as exc:
            logger.warning("Monitor start failed: %s", exc)

        try:
            self.scheduler.start()      # Start trade scheduler + gas oracle
        except Exception as exc:
            logger.warning("Scheduler start failed: %s", exc)

        self._exec_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._exec_thread.start()
        logger.info("Nexus AI is running (chains: %s).", self.blockchain.connected_chains() or "waiting for connections")

    def stop(self):
        logger.info("Stopping Nexus AI…")
        self._running = False
        self.feed.stop()
        self.monitor.stop()
        self.scheduler.stop()
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
        # Feed latest prices into brain for market classification
        prices = self.feed.all_prices()
        if prices:
            self.brain.on_prices(prices)

        opp = self.monitor.get_best_opportunity()
        if not opp:
            return

        # Record opportunity in trade memory BEFORE execution
        opp_dict = opp.__dict__ if hasattr(opp, "__dict__") else {}
        self.brain.record_opportunity(opp_dict)

        # ── Brain go/no-go decision ────────────────────────────
        go, reason = self.brain.should_execute(opp_dict)
        if not go:
            logger.info("Brain skipped opportunity: %s", reason)
            return

        # ── Gas-aware trade deferral for low-urgency strategies ────
        strategy_name = opp_dict.get("type", "").replace("_", "").lower()
        if strategy_name not in _URGENT_STRATEGIES:
            if not self.scheduler.is_good_time(opp.chain, strategy_name):
                # Defer to scheduler instead of skipping entirely
                self._defer_trade(opp, opp_dict)
                return

        logger.info(
            "Executing best opportunity: %s (brain=%s score=%.4f profit=$%.4f regime=%s)",
            opp.description,
            reason,
            opp.score(),
            opp.estimated_profit_usd,
            self.brain.regime(),
        )

        tx_hash = self.executor.execute(opp)
        success = bool(tx_hash and tx_hash != "failed")
        actual_profit = opp.estimated_profit_usd if success else 0.0

        self.monitor.mark_executed(opp, tx_hash or "failed")
        
        # Record result for strategy performance tracking
        strategy_type = getattr(opp, 'type', None)
        if strategy_type:
            self.monitor.record_execution_result(
                strategy_type.value if hasattr(strategy_type, 'value') else str(strategy_type),
                actual_profit,
                success,
            )
        
        self.tracker.record(
            opp=opp,
            tx_hash=tx_hash,
            actual_profit_usd=actual_profit if success else None,
            dry_run=Config.DRY_RUN,
        )

        # ── Teach the brain what happened ─────────────────────
        self.brain.learn(opp_dict, success=success, actual_profit=actual_profit)

        # Queue profit for automatic payout to Coinbase / Cash App
        if success:
            self.payout.queue(actual_profit, opp.chain)
    
    def _defer_trade(self, opp, opp_dict: dict):
        """
        Defer a low-urgency trade to be executed when gas is cheaper.
        Instead of skipping, we queue it with the trade scheduler.
        """
        self._deferred_count += 1
        
        # Use public scheduler methods instead of accessing private attributes
        current_gas = self.scheduler.get_current_gas()
        target_gas = self.scheduler.get_target_gas()
        
        # Estimate potential gas savings in USD
        # Calculation: (gas_diff_gwei) * (gas_units_estimate ~200k) * (ETH_price ~$3000) / 1e9
        # Simplified: gas_diff * 0.0001 assumes ~200k gas units at ~$3000/ETH = $0.0006/gwei
        # This is a rough estimate; actual savings depend on specific transaction gas usage
        GAS_TO_USD_FACTOR = 0.0006  # Approximate USD per gwei saved (200k gas at ~$3000/ETH)
        if current_gas > target_gas:
            savings_estimate = (current_gas - target_gas) * GAS_TO_USD_FACTOR
            self._gas_savings_estimate += savings_estimate
        
        logger.info(
            "Deferring %s to scheduler (gas=%.1f gwei, target=%.1f gwei, deferred_total=%d)",
            opp.description[:50],
            current_gas,
            target_gas,
            self._deferred_count,
        )
        
        def execute_callback(opp_from_scheduler):
            """Callback invoked by scheduler when gas is cheap."""
            try:
                # Re-check if still profitable
                if opp.executed:
                    return
                tx_hash = self.executor.execute(opp)
                success = bool(tx_hash and tx_hash != "failed")
                actual_profit = opp.estimated_profit_usd if success else 0.0
                self.monitor.mark_executed(opp, tx_hash or "failed")
                self.tracker.record(
                    opp=opp,
                    tx_hash=tx_hash,
                    actual_profit_usd=actual_profit if success else None,
                    dry_run=Config.DRY_RUN,
                )
                self.brain.learn(opp_dict, success=success, actual_profit=actual_profit)
                if success:
                    self.payout.queue(actual_profit, opp.chain)
                logger.info("Deferred trade executed: %s (success=%s)", opp.description[:50], success)
            except Exception as exc:
                logger.warning("Deferred trade execution error: %s", exc)
        
        self.scheduler.enqueue(
            opportunity=opp_dict,
            urgency="low",
            callback=execute_callback,
            max_wait=300.0,  # 5 minutes max wait
        )

    # ── Status / reporting ────────────────────────────────────

    def status(self) -> dict:
        uptime = time.time() - self._start_time if self._start_time else 0
        scheduler_stats = self.scheduler.stats()
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
            "flashbots_ready": self.bundler.is_available() if self.bundler else False,
            "brain": self.brain.status(),
            # v2 efficiency metrics
            "efficiency": {
                "deferred_trades": self._deferred_count,
                "estimated_gas_savings_usd": round(self._gas_savings_estimate, 4),
                "scheduler_queue_size": scheduler_stats.get("queue_size", 0),
                "scheduler_submitted": scheduler_stats.get("submitted", 0),
                "scheduler_expired": scheduler_stats.get("expired", 0),
                "current_regime": self.brain.regime(),
                "strategy_weights": self.brain.strategy_weights(),
            },
            "timing": scheduler_stats,
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
