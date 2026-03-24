"""
Parameter Optimizer for Nexus AI.

Continuously adjusts the bot's runtime parameters based on observed
trade performance to maximise profit over time.

Parameters it adapts
---------------------
min_profit_usd      → raised when win-rate is high; lowered when finding no trades
gas_multiplier      → raised when transactions miss blocks; lowered to save costs
slippage_tolerance  → raised on volatile markets; lowered on calm markets
confidence_threshold→ raised when false positives dominate; lowered when missing wins
scan_priority       → weights which strategy to prioritise each session
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Any

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# How many recent trades to evaluate for each adjustment
WINDOW = 50
# How often to run the optimizer (seconds)
OPTIMIZE_INTERVAL = 300   # every 5 minutes


class ParameterOptimizer:
    """
    Bayesian-inspired adaptive parameter tuner.

    Watches rolling performance windows and nudges parameters
    toward the configuration that historically maximises profit.
    """

    def __init__(self, config):
        self._config    = config
        self._last_run  = 0.0
        self._history: list[dict] = []   # rolling trade outcomes
        self._adjustments: list[dict] = []
        # Shadow parameters (override live config)
        self._params = {
            "min_profit_usd":      float(config.MIN_PROFIT_USD),
            "gas_multiplier":      1.0,
            "slippage_tolerance":  float(config.SLIPPAGE_PERCENT),
            "confidence_threshold": 0.3,
        }

    # ── Public API ────────────────────────────────────────────

    def add_outcome(self, opp: dict, success: bool, actual_profit: float):
        """Feed a trade result into the optimizer."""
        self._history.append({
            "timestamp":     time.time(),
            "strategy":      (opp.get("details") or {}).get("strategy", opp.get("type", "")),
            "chain":         opp.get("chain", ""),
            "estimated":     float(opp.get("estimated_profit_usd", 0) or 0),
            "actual":        actual_profit,
            "success":       success,
            "gas_gwei":      float(opp.get("gas_gwei", 0) or 0),
            "confidence":    float(opp.get("confidence", 0) or 0),
        })
        # Keep rolling window
        if len(self._history) > WINDOW * 3:
            self._history = self._history[-WINDOW * 3:]

        # Run optimization periodically
        now = time.time()
        if now - self._last_run > OPTIMIZE_INTERVAL:
            self._last_run = now
            self._optimize()

    def get(self, param: str, default: Any = None) -> Any:
        """Get current (possibly adjusted) parameter value."""
        return self._params.get(param, default)

    def all_params(self) -> dict:
        return dict(self._params)

    def get_strategy_weights(self) -> dict[str, float]:
        """
        Return relative weights for each strategy based on profit history.
        Higher weight → scanner runs it more often.
        """
        weights: dict[str, float] = {}
        strats = ["arbitrage", "flash_arbitrage", "yield_farming", "liquidity_mining", "liquidation"]
        for s in strats:
            relevant = [t for t in self._history if s in t["strategy"] and t["success"]]
            if relevant:
                avg_profit = statistics.mean(t["actual"] for t in relevant)
                weights[s] = max(0.1, min(3.0, avg_profit / max(self._params["min_profit_usd"], 0.01)))
            else:
                weights[s] = 1.0
        return weights

    def summary(self) -> dict:
        recent = self._history[-WINDOW:]
        if not recent:
            return {"params": self._params, "win_rate": 0, "adjustments": self._adjustments[-10:]}
        wins      = sum(1 for t in recent if t["success"])
        win_rate  = wins / len(recent) * 100
        avg_est   = statistics.mean(t["estimated"] for t in recent) if recent else 0
        avg_act   = statistics.mean(t["actual"]    for t in recent) if recent else 0
        return {
            "params":          self._params,
            "win_rate":        round(win_rate, 1),
            "avg_estimated":   round(avg_est, 4),
            "avg_actual":      round(avg_act, 4),
            "profit_accuracy": round(avg_act / avg_est, 3) if avg_est > 0 else 0,
            "adjustments":     self._adjustments[-10:],
            "sample_size":     len(recent),
        }

    # ── Optimization logic ────────────────────────────────────

    def _optimize(self):
        recent = self._history[-WINDOW:]
        if len(recent) < 10:
            return

        wins      = sum(1 for t in recent if t["success"])
        failures  = len(recent) - wins
        win_rate  = wins / len(recent)

        # ── Adjust min_profit_usd ─────────────────────────────
        if win_rate > 0.75 and wins >= 15:
            # Very high win rate → can be more selective, raise threshold slightly
            self._nudge("min_profit_usd", +10, "Win rate >75% — raising threshold")
        elif win_rate < 0.40 and len(recent) >= 20:
            # Too many failures → lower threshold to find easier trades
            self._nudge("min_profit_usd", -5, "Win rate <40% — lowering threshold")

        # ── Adjust gas multiplier ─────────────────────────────
        gas_list  = [t["gas_gwei"] for t in recent]
        avg_gas   = statistics.mean(gas_list) if gas_list else 10
        if avg_gas > 80 and win_rate < 0.5:
            # High gas + low win rate → boost multiplier to win block auctions
            self._nudge_mult("gas_multiplier", 1.1, "High gas + low wins — boosting gas")
        elif avg_gas < 20 and win_rate > 0.6:
            # Low gas + good wins → save gas fees
            self._nudge_mult("gas_multiplier", 0.95, "Low gas + good wins — reducing gas")

        # ── Adjust slippage tolerance ─────────────────────────
        actuals   = [t["actual"]    for t in recent if t["success"]]
        estimates = [t["estimated"] for t in recent if t["success"]]
        if actuals and estimates:
            avg_ratio = statistics.mean(a / max(e, 0.001) for a, e in zip(actuals, estimates))
            if avg_ratio < 0.7:
                # Getting much less than estimated → increase slippage allowance
                self._nudge("slippage_tolerance", +0.1, "Actual profit 30%+ below estimate — relaxing slippage")
            elif avg_ratio > 0.95 and self._params["slippage_tolerance"] > 0.3:
                # Very close to estimate → can tighten slippage
                self._nudge("slippage_tolerance", -0.05, "Estimates accurate — tightening slippage")

        # ── Adjust confidence threshold ───────────────────────
        if failures > wins * 2 and self._params["confidence_threshold"] < 0.9:
            self._nudge("confidence_threshold", +0.05, "Too many failures — raising confidence bar")
        elif win_rate > 0.8 and self._params["confidence_threshold"] > 0.2:
            self._nudge("confidence_threshold", -0.03, "Strong win rate — lowering confidence bar")

        logger.info("ParameterOptimizer adjusted: %s", self._params)

    def _nudge(self, param: str, delta: float, reason: str):
        old = self._params[param]
        # Guard rails per param
        limits = {
            "min_profit_usd":       (0.5, 500.0),
            "slippage_tolerance":   (0.1, 2.0),
            "confidence_threshold": (0.1, 0.95),
        }
        lo, hi = limits.get(param, (-1e9, 1e9))
        new = max(lo, min(hi, old + delta))
        if abs(new - old) > 1e-6:
            self._params[param] = new
            self._adjustments.append({
                "time":   time.time(),
                "param":  param,
                "old":    round(old, 4),
                "new":    round(new, 4),
                "reason": reason,
            })
            logger.info("Param %s: %.4f → %.4f (%s)", param, old, new, reason)

    def _nudge_mult(self, param: str, factor: float, reason: str):
        old = self._params.get(param, 1.0)
        new = max(0.5, min(2.5, old * factor))
        if abs(new - old) > 1e-4:
            self._params[param] = new
            self._adjustments.append({
                "time": time.time(), "param": param,
                "old": round(old, 4), "new": round(new, 4), "reason": reason,
            })
            logger.info("Param %s: %.3f → %.3f (%s)", param, old, new, reason)
