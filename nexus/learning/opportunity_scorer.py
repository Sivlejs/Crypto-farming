"""
Opportunity Scorer — RandomForest ML model for Nexus AI.

Learns from historical trade outcomes to predict:
  1. Probability that an opportunity will succeed (P_success)
  2. Expected profit multiplier (actual vs estimated)

The composite ML score is: P_success × profit_multiplier × estimated_profit

After MIN_SAMPLES trades, the model replaces the hand-crafted confidence
score, making decisions increasingly data-driven over time.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

import numpy as np

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MODEL_DIR = Path(__file__).parent.parent.parent / "data" / "models"
try:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
except OSError as e:
    # On read-only filesystems or permission issues, fall back to /tmp
    logger.warning("Cannot create models directory %s: %s. Falling back to /tmp", MODEL_DIR, e)
    MODEL_DIR = Path("/tmp/nexus_data/models")
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as fallback_error:
        logger.critical("Cannot create fallback models directory %s: %s", MODEL_DIR, fallback_error)
        raise
CLASSIFIER_PATH = MODEL_DIR / "opp_classifier.pkl"
REGRESSOR_PATH  = MODEL_DIR / "profit_regressor.pkl"

# Minimum trades before the ML model activates
MIN_SAMPLES = 30
# Retrain every N new trades
RETRAIN_EVERY = 10

# Feature names (must match extract_features output)
FEATURE_NAMES = [
    "spread_pct", "profit_usd", "confidence", "gas_gwei",
    "time_hour", "time_weekday", "market_vol",
    "type_arbitrage", "type_yield_farming", "type_liquidity_mining", "type_liquidation",
    "chain_ethereum", "chain_bsc", "chain_polygon",
]


def extract_features(opp_row: dict) -> Optional[np.ndarray]:
    """Convert a raw opportunity dict or DB row into a feature vector."""
    try:
        t = opp_row.get("opp_type", opp_row.get("type", ""))
        c = opp_row.get("chain", "")
        return np.array([
            float(opp_row.get("spread_pct", 0)  or 0),
            float(opp_row.get("profit_usd", 0)   or opp_row.get("estimated_profit_usd", 0) or 0),
            float(opp_row.get("confidence", 0)   or 0),
            float(opp_row.get("gas_gwei", 0)     or 0),
            int(opp_row.get("time_hour", 0)      or 0),
            int(opp_row.get("time_weekday", 0)   or 0),
            float(opp_row.get("market_vol", 0)   or 0),
            1 if "arbitrage"        in t else 0,
            1 if "yield_farming"    in t else 0,
            1 if "liquidity_mining" in t else 0,
            1 if "liquidation"      in t else 0,
            1 if c == "ethereum" else 0,
            1 if c == "bsc"      else 0,
            1 if c == "polygon"  else 0,
        ], dtype=float)
    except Exception as exc:
        logger.debug("Feature extraction failed: %s", exc)
        return None


class OpportunityScorer:
    """
    RandomForest-based scorer.  Falls back to hand-crafted heuristics until
    enough data is collected.
    """

    def __init__(self):
        self._classifier = None   # predicts success probability
        self._regressor  = None   # predicts profit multiplier
        self._trained_on = 0
        self._retrain_counter = 0
        self._load_models()

    # ── Public API ────────────────────────────────────────────

    def score(self, opp: dict) -> float:
        """
        Return a [0,1] score.  Higher = more likely profitable.
        Uses ML model when available, otherwise heuristic.
        """
        if self._classifier is None:
            return self._heuristic_score(opp)

        feats = extract_features(opp)
        if feats is None:
            return self._heuristic_score(opp)

        try:
            p_success = self._classifier.predict_proba([feats])[0][1]
            if self._regressor:
                profit_mult = float(self._regressor.predict([feats])[0])
                profit_mult = max(0.0, min(profit_mult, 5.0))
            else:
                profit_mult = 1.0
            return float(np.clip(p_success * profit_mult, 0, 1))
        except Exception as exc:
            logger.debug("ML score failed: %s", exc)
            return self._heuristic_score(opp)

    def train(self, rows: list[dict]) -> dict:
        """
        Train (or re-train) models on a list of historical trade rows.
        Returns training stats dict.
        """
        if len(rows) < MIN_SAMPLES:
            return {"status": "not_enough_data", "samples": len(rows), "needed": MIN_SAMPLES}

        try:
            from sklearn.ensemble import RandomForestClassifier, GradientBoostingRegressor
            from sklearn.model_selection import cross_val_score
            from sklearn.preprocessing import StandardScaler

            X, y_cls, y_reg = [], [], []
            for row in rows:
                feats = extract_features(row)
                if feats is None:
                    continue
                X.append(feats)
                y_cls.append(int(row.get("success", 0) or 0))
                # profit multiplier = actual / estimated (capped 0–5)
                est = float(row.get("profit_usd", 1) or 1) or 1
                act = float(row.get("actual_profit", 0) or 0)
                y_reg.append(min(5.0, max(0.0, act / est)))

            if not X:
                return {"status": "no_features"}

            X_arr = np.array(X)

            # ── Classifier ─────────────────────────
            clf = RandomForestClassifier(
                n_estimators=150, max_depth=8,
                min_samples_split=4, random_state=42,
                class_weight="balanced", n_jobs=-1,
            )
            clf.fit(X_arr, y_cls)
            cv_acc = cross_val_score(clf, X_arr, y_cls, cv=min(5, len(X)//5 or 1), scoring="accuracy").mean()

            # ── Regressor ──────────────────────────
            reg = GradientBoostingRegressor(
                n_estimators=100, max_depth=4,
                learning_rate=0.05, random_state=42,
            )
            reg.fit(X_arr, y_reg)

            self._classifier  = clf
            self._regressor   = reg
            self._trained_on  = len(rows)
            self._save_models()

            # Feature importance (top 5)
            importances = sorted(
                zip(FEATURE_NAMES, clf.feature_importances_),
                key=lambda x: x[1], reverse=True,
            )[:5]

            result = {
                "status":       "trained",
                "samples":      len(rows),
                "cv_accuracy":  round(float(cv_acc), 4),
                "win_rate":     round(sum(y_cls)/len(y_cls)*100, 1),
                "top_features": [(n, round(float(v), 4)) for n, v in importances],
            }
            logger.info(
                "ML model trained: %d samples, accuracy=%.1f%% win_rate=%.1f%%",
                len(rows), cv_acc * 100, result["win_rate"],
            )
            return result

        except ImportError:
            return {"status": "scikit_learn_not_installed"}
        except Exception as exc:
            logger.error("Training failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def is_ml_active(self) -> bool:
        return self._classifier is not None

    def model_info(self) -> dict:
        return {
            "ml_active":   self.is_ml_active(),
            "trained_on":  self._trained_on,
            "min_samples": MIN_SAMPLES,
        }

    # ── Heuristic fallback ────────────────────────────────────

    @staticmethod
    def _heuristic_score(opp: dict) -> float:
        """Simple hand-crafted score before ML takes over."""
        confidence = float(opp.get("confidence", 0.5) or 0.5)
        profit     = float(opp.get("estimated_profit_usd", 0) or 0)
        gas        = float(opp.get("gas_gwei", 5) or 5)
        gas_penalty = max(0, 1 - gas / 300)
        profit_bonus = min(0.3, profit / 1000)
        return float(np.clip(confidence * 0.7 + gas_penalty * 0.15 + profit_bonus, 0, 1))

    # ── Persistence ───────────────────────────────────────────

    def _save_models(self):
        try:
            import joblib
            joblib.dump(self._classifier, CLASSIFIER_PATH)
            joblib.dump(self._regressor,  REGRESSOR_PATH)
            logger.debug("ML models saved to %s", MODEL_DIR)
        except Exception as exc:
            logger.warning("Could not save ML models: %s", exc)

    def _load_models(self):
        try:
            import joblib
            if CLASSIFIER_PATH.exists():
                self._classifier = joblib.load(CLASSIFIER_PATH)
            if REGRESSOR_PATH.exists():
                self._regressor  = joblib.load(REGRESSOR_PATH)
            if self._classifier:
                logger.info("ML models loaded from disk ✓")
        except Exception as exc:
            logger.warning("Could not load ML models: %s", exc)
            self._classifier = None
            self._regressor  = None
