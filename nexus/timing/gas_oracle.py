"""
Nexus AI Gas Oracle.

Continuously samples gas prices on Ethereum and uses a rolling window
of historical samples to predict the cheapest upcoming gas windows.

Features
--------
- Rolling ring-buffer of (timestamp, base_fee, priority_fee) samples
- Percentile-based "cheap window" detection (< 25th percentile = cheap)
- Hour-of-day profile built over time (identifies cheap hours)
- ``is_cheap_now()`` / ``should_wait()`` helpers used by the trade scheduler
- ``best_gas()`` returns the recommended gas parameters for a tx right now
"""
from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from typing import Optional

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Types ─────────────────────────────────────────────────────────────────────

GasSample = tuple[float, float, float]  # (timestamp, base_fee_gwei, priority_fee_gwei)

# ── Constants ─────────────────────────────────────────────────────────────────

_CHEAP_PERCENTILE = 25   # base_fee below this percentile → "cheap"
_EXPENSIVE_PERCENTILE = 75


class GasOracle:
    """
    Tracks historical gas prices and advises whether NOW is a good time
    to submit a transaction.
    """

    def __init__(self, max_samples: int | None = None):
        n = max_samples or Config.GAS_ORACLE_SAMPLES
        self._samples: deque[GasSample] = deque(maxlen=n)
        # Hour-of-day average base_fee (0-23)
        self._hour_avg: dict[int, list[float]] = {h: [] for h in range(24)}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start background sampling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        logger.info("GasOracle started (sampling every %ds)", Config.GAS_SAMPLE_INTERVAL)

    def stop(self):
        self._running = False

    # ── Public helpers ────────────────────────────────────────────────────────

    def record(self, base_fee_gwei: float, priority_fee_gwei: float = 1.5):
        """Manually record a gas sample (also called from the sample loop)."""
        sample: GasSample = (time.time(), base_fee_gwei, priority_fee_gwei)
        hour = time.localtime().tm_hour
        with self._lock:
            self._samples.append(sample)
            self._hour_avg[hour].append(base_fee_gwei)
            # Keep hour lists bounded
            if len(self._hour_avg[hour]) > 200:
                self._hour_avg[hour] = self._hour_avg[hour][-200:]

    def current_base_fee(self) -> Optional[float]:
        """Return the most recently sampled base fee in Gwei."""
        with self._lock:
            if self._samples:
                return self._samples[-1][1]
        return None

    def is_cheap_now(self) -> bool:
        """True when current gas is below the historical cheap threshold."""
        fee = self.current_base_fee()
        if fee is None:
            return True  # no data → optimistic
        thresh = self._percentile(_CHEAP_PERCENTILE)
        return fee <= thresh

    def should_wait(self) -> bool:
        """True when gas is expensive AND we have enough data to be confident."""
        with self._lock:
            if len(self._samples) < 10:
                return False
        fee = self.current_base_fee()
        if fee is None:
            return False
        thresh = self._percentile(_EXPENSIVE_PERCENTILE)
        return fee > thresh

    def best_gas(self) -> dict:
        """
        Return recommended EIP-1559 gas parameters.

        Returns a dict with:
          - ``max_fee_gwei``          (maxFeePerGas)
          - ``priority_fee_gwei``     (maxPriorityFeePerGas)
          - ``is_cheap``              bool
          - ``estimated_base_gwei``   current base fee
        """
        fee = self.current_base_fee() or 20.0
        # Add 20% buffer on top of current base, cap at Config.MAX_GAS_GWEI
        max_fee = min(fee * 1.2 + 1.5, Config.MAX_GAS_GWEI)
        priority = min(1.5, max_fee * 0.1)
        return {
            "max_fee_gwei":        round(max_fee, 2),
            "priority_fee_gwei":   round(priority, 2),
            "is_cheap":            self.is_cheap_now(),
            "estimated_base_gwei": round(fee, 2),
        }

    def cheapest_upcoming_hour(self) -> Optional[int]:
        """
        Return the hour of day (0-23) that historically has the lowest gas,
        or None if insufficient history.
        """
        with self._lock:
            avgs = {
                h: statistics.mean(vals)
                for h, vals in self._hour_avg.items()
                if len(vals) >= 3
            }
        if not avgs:
            return None
        return min(avgs, key=avgs.__getitem__)

    def stats(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {"samples": 0, "current_gwei": None, "is_cheap": None}
        fees = [s[1] for s in samples]
        current = fees[-1]
        cheapest_hour = self.cheapest_upcoming_hour()
        return {
            "samples":          len(samples),
            "current_gwei":     round(current, 2),
            "mean_gwei":        round(statistics.mean(fees), 2),
            "p25_gwei":         round(self._percentile(25), 2),
            "p75_gwei":         round(self._percentile(75), 2),
            "is_cheap":         self.is_cheap_now(),
            "should_wait":      self.should_wait(),
            "cheapest_hour":    cheapest_hour,
            "best_gas":         self.best_gas(),
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _percentile(self, pct: int) -> float:
        """Return the *pct*-th percentile of recorded base fees."""
        with self._lock:
            if not self._samples:
                return 50.0
            fees = sorted(s[1] for s in self._samples)
        k = (len(fees) - 1) * pct / 100
        lo, hi = int(k), min(int(k) + 1, len(fees) - 1)
        return fees[lo] + (fees[hi] - fees[lo]) * (k - lo)

    def _sample_loop(self):
        """Background loop: fetch Ethereum gas price and record it."""
        while self._running:
            try:
                self._fetch_and_record()
            except Exception as exc:
                logger.debug("GasOracle sample error: %s", exc)
            time.sleep(Config.GAS_SAMPLE_INTERVAL)

    def _fetch_and_record(self):
        """Fetch current Ethereum base fee via eth_gasPrice fallback."""
        try:
            # Import lazily to avoid circular imports
            from nexus.blockchain import get_blockchain_manager
            mgr = get_blockchain_manager()
            w3 = mgr.get_web3("ethereum")
            if w3 is None:
                return
            block = w3.eth.get_block("latest")
            # EIP-1559 base fee
            base_fee_wei = block.get("baseFeePerGas") or w3.eth.gas_price
            base_fee_gwei = base_fee_wei / 1e9
            # Priority fee estimate
            try:
                priority_gwei = w3.eth.max_priority_fee / 1e9
            except Exception:
                priority_gwei = 1.5
            self.record(base_fee_gwei, priority_gwei)
            logger.debug(
                "GasOracle: base=%.2f gwei  priority=%.2f gwei  cheap=%s",
                base_fee_gwei, priority_gwei, self.is_cheap_now(),
            )
        except Exception as exc:
            logger.debug("GasOracle._fetch_and_record: %s", exc)


# ── Singleton ─────────────────────────────────────────────────────────────────

_oracle: Optional[GasOracle] = None


def get_gas_oracle() -> GasOracle:
    global _oracle
    if _oracle is None:
        _oracle = GasOracle()
    return _oracle
