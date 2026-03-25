"""
NexusBrain — Central Intelligence Hub for Nexus AI.

Orchestrates all learning modules:
  • TradeMemory      → persistent outcome store
  • OpportunityScorer → ML model scoring
  • ParameterOptimizer → adaptive parameter tuning
  • MarketClassifier  → market regime detection

The agent calls brain.score(opp) before deciding to execute,
and brain.learn(opp, success, profit) after every outcome.

Over time the brain improves:
  Round 1:   heuristic scoring (no data yet)
  Round 30:  ML model trained, scoring becomes data-driven
  Round 100+: parameters continuously auto-tuned to maximise profit
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from nexus.learning.trade_memory import TradeMemory
from nexus.learning.opportunity_scorer import OpportunityScorer, MIN_SAMPLES, RETRAIN_EVERY
from nexus.learning.parameter_optimizer import ParameterOptimizer
from nexus.learning.market_classifier import MarketClassifier
from nexus.utils.logger import get_logger

logger = get_logger(__name__)


class NexusBrain:
    """
    Single entry-point for all AI learning capabilities.

    Usage
    -----
    brain = NexusBrain(config)

    # Before execution:
    ml_score = brain.score(opp)          # [0,1] — higher = better
    go = ml_score > brain.threshold()

    # After execution:
    brain.learn(opp, success=True, actual_profit=12.50)

    # On every price update:
    brain.on_prices({"ETH": 3500, "BTC": 65000})
    """

    def __init__(self, config):
        self.memory    = TradeMemory()
        self.scorer    = OpportunityScorer()
        self.optimizer = ParameterOptimizer(config)
        self.classifier = MarketClassifier()
        self._lock     = threading.Lock()
        self._trade_counter = 0
        self._last_retrain  = 0
        
        # ML accuracy tracking (v2 efficiency upgrade)
        self._ml_predictions: list[dict] = []  # Recent predictions for analysis
        self._ml_correct_predictions = 0
        self._ml_incorrect_predictions = 0
        self._prediction_executed = 0  # How many opportunities ML approved
        self._prediction_skipped = 0   # How many opportunities ML rejected
        
        logger.info("NexusBrain initialised ✓ (v2 with ML accuracy tracking)")

    # ── Core API ──────────────────────────────────────────────

    def score(self, opp: dict) -> float:
        """Return ML-enhanced score for an opportunity."""
        return self.scorer.score(opp)

    def threshold(self) -> float:
        """Dynamic confidence threshold adjusted by the optimizer."""
        return self.optimizer.get("confidence_threshold", 0.3)

    def min_profit(self) -> float:
        """Dynamic minimum profit threshold."""
        return self.optimizer.get("min_profit_usd", 1.0)

    def gas_multiplier(self) -> float:
        return self.optimizer.get("gas_multiplier", 1.0)

    def should_execute(self, opp: dict) -> tuple[bool, str]:
        """
        Master go/no-go decision.
        Returns (True, reason) or (False, reason).
        """
        # Generate unique prediction ID for tracking
        import time as _time
        pred_id = f"{opp.get('id', '')}_{_time.time()}"
        
        profit = float(opp.get("estimated_profit_usd", 0) or 0)
        if profit < self.min_profit():
            self._prediction_skipped += 1
            return False, f"Profit ${profit:.4f} < threshold ${self.min_profit():.4f}"

        ml_score = self.score(opp)
        thresh   = self.threshold()
        if ml_score < thresh:
            self._prediction_skipped += 1
            self._ml_predictions.append({
                "pred_id": pred_id,
                "opp_id": opp.get("id", ""),
                "score": ml_score,
                "predicted_skip": True,
                "actual_success": None,  # Unknown - we skipped
            })
            return False, f"ML score {ml_score:.3f} < threshold {thresh:.3f}"

        # Record prediction for accuracy tracking with unique ID
        self._ml_predictions.append({
            "pred_id": pred_id,
            "opp_id": opp.get("id", ""),
            "score": ml_score,
            "predicted_skip": False,
            "actual_success": None,  # Will be updated in learn()
        })
        self._prediction_executed += 1
        
        # Store the prediction ID in the opportunity for later matching
        opp["_prediction_id"] = pred_id
        
        return True, f"ML score {ml_score:.3f} ✓ profit ${profit:.4f} ✓"

    def learn(self, opp: dict, success: bool, actual_profit: float):
        """
        Record trade outcome and trigger incremental learning.
        Call this after EVERY executed trade.
        """
        with self._lock:
            # Match prediction by ID if available, otherwise fall back to most recent
            pred_id = opp.get("_prediction_id")
            matched_pred = None
            
            if pred_id:
                # Find prediction by ID
                for pred in reversed(self._ml_predictions):
                    if pred.get("pred_id") == pred_id and pred.get("actual_success") is None:
                        matched_pred = pred
                        break
            
            # Fallback: find most recent unresolved prediction for this opportunity
            if not matched_pred:
                opp_id = opp.get("id", "")
                for pred in reversed(self._ml_predictions):
                    if pred.get("opp_id") == opp_id and pred.get("actual_success") is None:
                        matched_pred = pred
                        break
            
            # Update matched prediction
            if matched_pred:
                matched_pred["actual_success"] = success
                # Track accuracy for executed predictions only
                if not matched_pred.get("predicted_skip", False):
                    if success:
                        self._ml_correct_predictions += 1
                    else:
                        self._ml_incorrect_predictions += 1
            
            # Record to persistent store
            self.memory.record_outcome(opp.get("id", ""), success, actual_profit)

            # Feed optimizer
            self.optimizer.add_outcome(opp, success, actual_profit)

            self._trade_counter += 1

            # Retrain ML model periodically
            if self._trade_counter % RETRAIN_EVERY == 0:
                self._retrain()
            
            # Trim old predictions to prevent memory growth (keep last 1000)
            if len(self._ml_predictions) > 1000:
                self._ml_predictions = self._ml_predictions[-500:]

        logger.info(
            "Brain learned: %s | profit=$%.4f | trades=%d | ML accuracy=%.1f%%",
            "WIN ✓" if success else "FAIL ✗",
            actual_profit,
            self._trade_counter,
            self.ml_accuracy() * 100,
        )
    
    def ml_accuracy(self) -> float:
        """
        Return the ML model's prediction accuracy.
        Calculated as: correct predictions / total predictions.
        """
        total = self._ml_correct_predictions + self._ml_incorrect_predictions
        if total == 0:
            return 0.0
        return self._ml_correct_predictions / total

    def record_opportunity(self, opp: dict):
        """Call this when an opportunity is found (before execution decision)."""
        market_vol = self.classifier.volatility_pct("ETH")
        self.memory.record_opportunity(opp, market_vol)

    def on_prices(self, prices: dict):
        """Feed current market prices for regime classification."""
        self.classifier.update(prices)

    def regime(self) -> str:
        return self.classifier.regime()

    def strategy_weights(self) -> dict[str, float]:
        """Merged weights from market classifier + optimizer."""
        market_w  = self.classifier.strategy_weights()
        optim_w   = self.optimizer.get_strategy_weights()
        merged    = {}
        all_strats = set(list(market_w.keys()) + list(optim_w.keys()))
        for s in all_strats:
            merged[s] = round((market_w.get(s, 1.0) + optim_w.get(s, 1.0)) / 2, 3)
        return merged

    def status(self) -> dict:
        mem_stats = self.memory.get_stats()
        total_predictions = self._ml_correct_predictions + self._ml_incorrect_predictions
        return {
            "ml_active":        self.scorer.is_ml_active(),
            "model_info":       self.scorer.model_info(),
            "trades_until_ml":  max(0, MIN_SAMPLES - mem_stats.get("total_executed", 0)),
            "total_evaluated":  mem_stats.get("total_evaluated", 0),
            "total_executed":   mem_stats.get("total_executed", 0),
            "win_rate":         mem_stats.get("win_rate", 0),
            "best_trade_usd":   mem_stats.get("best_trade_usd", 0),
            "total_profit_usd": mem_stats.get("total_profit_usd", 0),
            "market_regime":    self.classifier.status(),
            "optimizer":        self.optimizer.summary(),
            "param_changes":    mem_stats.get("param_changes", []),
            "model_history":    mem_stats.get("model_history", []),
            # v2 ML accuracy tracking
            "ml_accuracy": {
                "accuracy_pct": round(self.ml_accuracy() * 100, 2),
                "correct_predictions": self._ml_correct_predictions,
                "incorrect_predictions": self._ml_incorrect_predictions,
                "total_predictions": total_predictions,
                "opportunities_approved": self._prediction_executed,
                "opportunities_rejected": self._prediction_skipped,
                "approval_rate_pct": round(
                    self._prediction_executed / max(1, self._prediction_executed + self._prediction_skipped) * 100, 2
                ),
            },
        }

    # ── Internal ──────────────────────────────────────────────

    def _retrain(self):
        rows = self.memory.get_training_data(limit=2000)
        result = self.scorer.train(rows)
        if result.get("status") == "trained":
            self.memory.log_model_version(
                model_type="RandomForest",
                accuracy=result.get("cv_accuracy", 0),
                n_samples=result.get("samples", 0),
                notes=str(result.get("top_features", "")),
            )
            logger.info(
                "Brain retrained: accuracy=%.1f%% | top features: %s",
                result.get("cv_accuracy", 0) * 100,
                result.get("top_features", []),
            )
        else:
            logger.debug("Retrain result: %s", result)


# Singleton
_brain: Optional[NexusBrain] = None


def get_brain(config=None) -> NexusBrain:
    global _brain
    if _brain is None:
        from nexus.utils.config import Config
        _brain = NexusBrain(config or Config)
    return _brain
