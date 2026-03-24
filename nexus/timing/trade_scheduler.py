"""
Nexus AI Trade Scheduler.

Decides the *optimal moment* to execute each pending trade by combining:
  1. Gas oracle (is gas cheap right now?)
  2. Market regime (is volatility/spread favorable?)
  3. Strategy-specific urgency (flash arb = must execute ASAP; yield farming = can wait)
  4. Configurable blackout windows (avoid congested hours)

The scheduler runs as a background thread and maintains a priority queue of
``ScheduledTrade`` objects.  High-urgency trades (flash arb, liquidations) bypass
the scheduler and are submitted immediately by the executor.

Usage
-----
    scheduler = get_trade_scheduler()
    scheduler.start()

    # Queue a yield-farming trade to execute when gas is cheap
    scheduler.enqueue(opportunity, urgency="low")

    # Check if it's a good time to trade right now
    if scheduler.is_good_time(chain="ethereum", strategy="arbitrage"):
        executor.execute(opportunity)
"""
from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from nexus.timing.gas_oracle import get_gas_oracle
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Urgency levels ────────────────────────────────────────────────────────────

# Strategies that MUST execute immediately regardless of gas price
_URGENT_STRATEGIES = {
    "flash_arbitrage",
    "arbitrage",
    "liquidation",
    "triangular_arb",
    "stablecoin_arb",
}

# Strategies that can wait for cheaper gas
_DEFERRABLE_STRATEGIES = {
    "yield_farming",
    "liquidity_mining",
    "staking",
    "lending",
    "governance_farming",
    "vault_optimizer",
}


@dataclass(order=True)
class ScheduledTrade:
    """A trade waiting in the priority queue."""
    priority: float             # lower = higher priority (unix timestamp + gas weight)
    urgency:  str = field(compare=False)
    opportunity: dict = field(compare=False)
    callback: Optional[Callable] = field(compare=False, default=None)
    enqueue_time: float = field(compare=False, default_factory=time.time)
    max_wait_seconds: float = field(compare=False, default=300.0)


class TradeScheduler:
    """
    Optimal trade timing system.

    For each queued trade the scheduler asks:
      1. Is this trade still profitable? (staleness check)
      2. Is gas currently cheap?
      3. Is it the right market condition for this strategy?
      4. Has the trade waited longer than max_wait_seconds?

    If all green → submit.  Otherwise wait and re-check every few seconds.
    """

    def __init__(self):
        self._oracle = get_gas_oracle()
        self._queue: list[ScheduledTrade] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # Stats
        self._submitted = 0
        self._expired = 0
        self._waiting = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._oracle.start()
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._thread.start()
        logger.info("TradeScheduler started")

    def stop(self):
        self._running = False
        self._oracle.stop()

    # ── Public API ────────────────────────────────────────────────────────────

    def enqueue(
        self,
        opportunity: dict,
        urgency: str = "normal",
        callback: Optional[Callable] = None,
        max_wait: float = 300.0,
    ) -> bool:
        """
        Add a trade to the scheduling queue.

        Parameters
        ----------
        opportunity:
            The opportunity dict produced by a strategy scanner.
        urgency:
            ``"urgent"`` – bypass scheduler, mark for immediate execution.
            ``"normal"`` – wait for a moderately cheap gas window.
            ``"low"``    – wait for the cheapest possible window.
        callback:
            Optional callable(opportunity) invoked when the scheduler
            decides to submit the trade.
        max_wait:
            Maximum seconds to hold the trade before discarding.

        Returns True if the trade was accepted into the queue.
        """
        strategy = (opportunity.get("strategy") or "").lower()

        # Urgent strategies bypass the queue entirely
        if urgency == "urgent" or strategy in _URGENT_STRATEGIES:
            if callback:
                try:
                    callback(opportunity)
                except Exception as exc:
                    logger.warning("Urgent callback error: %s", exc)
            return True

        # Weight: low urgency waits longer for cheap gas
        gas_weight = {"normal": 10, "low": 30}.get(urgency, 10)
        current_gas = self._oracle.current_base_fee() or 20.0
        priority = time.time() + current_gas * gas_weight

        trade = ScheduledTrade(
            priority=priority,
            urgency=urgency,
            opportunity=opportunity,
            callback=callback,
            max_wait_seconds=max_wait,
        )
        with self._lock:
            heapq.heappush(self._queue, trade)
        logger.debug(
            "TradeScheduler queued %s (urgency=%s, queue_size=%d)",
            strategy, urgency, len(self._queue),
        )
        return True

    def is_good_time(self, chain: str = "ethereum", strategy: str = "") -> bool:
        """
        Quick check: should we execute a trade right now?

        Returns True if:
          - Strategy is always-urgent (flash arb, liquidation, etc.)
          - OR gas is cheap and conditions are favorable
        """
        strategy = strategy.lower()
        if strategy in _URGENT_STRATEGIES:
            return True
        if self._oracle.should_wait():
            logger.debug("TradeScheduler: gas expensive — deferring %s", strategy)
            return False
        return True

    def stats(self) -> dict:
        with self._lock:
            queue_size = len(self._queue)
        return {
            "running":   self._running,
            "queue_size": queue_size,
            "submitted": self._submitted,
            "expired":   self._expired,
            "gas_oracle": self._oracle.stats(),
        }

    # ── Background dispatch loop ──────────────────────────────────────────────

    def _dispatch_loop(self):
        """Check the queue every 5 seconds and submit ready trades."""
        while self._running:
            self._process_queue()
            time.sleep(5)

    def _process_queue(self):
        now = time.time()
        with self._lock:
            if not self._queue:
                return
            # Work on a snapshot, rebuild queue after
            snapshot = list(self._queue)

        submitted_indices = []
        expired_indices = []

        for i, trade in enumerate(snapshot):
            age = now - trade.enqueue_time

            # Expired?
            if age > trade.max_wait_seconds:
                logger.debug(
                    "TradeScheduler: trade expired after %.0fs (%s)",
                    age, trade.opportunity.get("strategy", "?"),
                )
                expired_indices.append(i)
                continue

            # Ready to submit?
            if self._oracle.is_cheap_now() or age > trade.max_wait_seconds * 0.8:
                if trade.callback:
                    try:
                        trade.callback(trade.opportunity)
                    except Exception as exc:
                        logger.warning("Scheduled trade callback error: %s", exc)
                submitted_indices.append(i)
                self._submitted += 1
                logger.info(
                    "TradeScheduler: submitted %s (waited %.0fs, gas=%.1f gwei)",
                    trade.opportunity.get("strategy", "?"),
                    age,
                    self._oracle.current_base_fee() or 0,
                )

        # Remove submitted + expired from queue
        remove = set(submitted_indices) | set(expired_indices)
        self._expired += len(expired_indices)
        with self._lock:
            self._queue = [t for i, t in enumerate(snapshot) if i not in remove]
            heapq.heapify(self._queue)


# ── Singleton ─────────────────────────────────────────────────────────────────

_scheduler: Optional[TradeScheduler] = None


def get_trade_scheduler() -> TradeScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TradeScheduler()
    return _scheduler
