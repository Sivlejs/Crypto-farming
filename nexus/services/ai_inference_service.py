"""
AI Inference Service for Nexus AI.

Dedicated microservice for AI/ML operations:
  - Pool analysis and scoring
  - ML model predictions
  - Market regime classification
  - Opportunity scoring
  - Real-time learning updates

This service is designed to be horizontally scalable for increased
AI workload capacity.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

from flask import Flask, jsonify, request

# Import core AI modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from nexus.learning.pool_analyzer import get_pool_analyzer, PoolAnalyzer
from nexus.learning.pool_optimizer import get_pool_optimizer, PoolOptimizer
from nexus.learning.brain import get_brain, NexusBrain
from nexus.learning.market_classifier import MarketClassifier
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Flask app
app = Flask(__name__)

# Service configuration
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8001"))
MODEL_CACHE_SIZE = int(os.getenv("MODEL_CACHE_SIZE", "100"))
PREDICTION_BATCH_SIZE = int(os.getenv("PREDICTION_BATCH_SIZE", "50"))

# Singleton instances
_analyzer: Optional[PoolAnalyzer] = None
_optimizer: Optional[PoolOptimizer] = None
_brain: Optional[NexusBrain] = None
_classifier: Optional[MarketClassifier] = None
_startup_complete = False


def initialize_services():
    """Initialize all AI services."""
    global _analyzer, _optimizer, _brain, _classifier, _startup_complete

    logger.info("Initializing AI Inference Service...")

    try:
        # Initialize components
        _classifier = MarketClassifier()
        logger.info("MarketClassifier initialized")

        _brain = get_brain()
        logger.info("NexusBrain initialized")

        _analyzer = get_pool_analyzer()
        _analyzer.start()
        logger.info("PoolAnalyzer started")

        _optimizer = get_pool_optimizer()
        _optimizer.start()
        logger.info("PoolOptimizer started")

        _startup_complete = True
        logger.info("AI Inference Service ready ✓")

    except Exception as exc:
        logger.error("AI Service initialization failed: %s", exc)
        raise


# ══════════════════════════════════════════════════════════════
# HEALTH & STATUS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy" if _startup_complete else "starting",
        "service": "ai-inference",
        "timestamp": time.time(),
    })


@app.route("/status")
def status():
    """Detailed service status."""
    return jsonify({
        "service": "ai-inference",
        "startup_complete": _startup_complete,
        "components": {
            "analyzer": _analyzer.status() if _analyzer else None,
            "optimizer": _optimizer.status() if _optimizer else None,
            "brain": _brain.status() if _brain else None,
            "classifier": _classifier.status() if _classifier else None,
        },
        "config": {
            "model_cache_size": MODEL_CACHE_SIZE,
            "prediction_batch_size": PREDICTION_BATCH_SIZE,
        },
    })


# ══════════════════════════════════════════════════════════════
# POOL ANALYSIS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/pools")
def get_pools():
    """Get all analyzed pools with metrics."""
    if not _analyzer:
        return jsonify({"error": "Service not ready"}), 503

    chain = request.args.get("chain")
    limit = int(request.args.get("limit", 50))
    min_tvl = float(request.args.get("min_tvl", 500000))
    max_risk = float(request.args.get("max_risk", 0.8))
    stablecoins_only = request.args.get("stablecoins_only", "false").lower() == "true"

    pools = _analyzer.get_top_pools(
        chain=chain,
        limit=limit,
        min_tvl=min_tvl,
        max_risk=max_risk,
        stablecoins_only=stablecoins_only,
    )

    return jsonify({
        "pools": [p.to_dict() for p in pools],
        "count": len(pools),
        "filters": {
            "chain": chain,
            "min_tvl": min_tvl,
            "max_risk": max_risk,
            "stablecoins_only": stablecoins_only,
        },
    })


@app.route("/api/pools/<pool_id>")
def get_pool(pool_id: str):
    """Get detailed metrics for a specific pool."""
    if not _analyzer:
        return jsonify({"error": "Service not ready"}), 503

    pool = _analyzer.get_pool(pool_id)
    if not pool:
        return jsonify({"error": "Pool not found"}), 404

    return jsonify(pool.to_dict())


@app.route("/api/pools/compare", methods=["POST"])
def compare_pools():
    """Compare multiple pools side-by-side."""
    if not _analyzer:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    pool_ids = data.get("pool_ids", [])

    if not pool_ids:
        return jsonify({"error": "pool_ids required"}), 400

    comparisons = _analyzer.get_pool_comparison(pool_ids)
    return jsonify({"comparisons": comparisons})


@app.route("/api/pools/decisions")
def get_ai_decisions():
    """Get AI decision log for transparency."""
    if not _analyzer:
        return jsonify({"error": "Service not ready"}), 503

    limit = int(request.args.get("limit", 10))
    decisions = _analyzer.get_ai_decision_log(limit=limit)

    return jsonify({"decisions": decisions})


# ══════════════════════════════════════════════════════════════
# OPTIMIZATION ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/optimize/allocation", methods=["POST"])
def optimize_allocation():
    """Get optimized pool allocation recommendation."""
    if not _optimizer:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    capital_usd = float(data.get("capital_usd", 1000))
    strategy = data.get("strategy", "balanced")
    chains = data.get("chains")
    max_pools = int(data.get("max_pools", 5))

    from nexus.learning.pool_optimizer import OptimizationStrategy
    strategy_map = {
        "max_yield": OptimizationStrategy.MAX_YIELD,
        "risk_adjusted": OptimizationStrategy.RISK_ADJUSTED,
        "conservative": OptimizationStrategy.CONSERVATIVE,
        "balanced": OptimizationStrategy.BALANCED,
        "gas_efficient": OptimizationStrategy.GAS_EFFICIENT,
    }
    opt_strategy = strategy_map.get(strategy, OptimizationStrategy.BALANCED)

    recommendations, profit_split = _optimizer.get_optimal_allocation(
        capital_usd=capital_usd,
        strategy=opt_strategy,
        chains=chains,
        max_pools=max_pools,
    )

    return jsonify({
        "recommendations": [r.to_dict() for r in recommendations],
        "profit_split": profit_split.to_dict(),
        "input": {
            "capital_usd": capital_usd,
            "strategy": strategy,
            "chains": chains,
            "max_pools": max_pools,
        },
    })


@app.route("/api/optimize/split", methods=["POST"])
def optimize_split():
    """Optimize profit split across specified pools."""
    if not _optimizer:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    pool_ids = data.get("pool_ids", [])
    capital_usd = float(data.get("capital_usd", 1000))
    constraints = data.get("constraints", {})

    if not pool_ids:
        return jsonify({"error": "pool_ids required"}), 400

    split = _optimizer.optimize_profit_split(
        pool_ids=pool_ids,
        capital_usd=capital_usd,
        constraints=constraints,
    )

    return jsonify({"profit_split": split.to_dict()})


@app.route("/api/optimize/decision", methods=["POST"])
def get_decision():
    """Get optimization decision for a specific action."""
    if not _optimizer:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    capital_usd = float(data.get("capital_usd", 1000))
    action_type = data.get("action_type", "enter")
    pool_id = data.get("pool_id")

    decision = _optimizer.get_optimization_decision(
        capital_usd=capital_usd,
        action_type=action_type,
        pool_id=pool_id,
    )

    return jsonify({"decision": decision.to_dict()})


@app.route("/api/optimize/positions")
def get_positions():
    """Get current positions from optimizer."""
    if not _optimizer:
        return jsonify({"error": "Service not ready"}), 503

    positions = _optimizer.get_positions()
    exposure = _optimizer.get_total_exposure()

    return jsonify({
        "positions": positions,
        "exposure": exposure,
    })


# ══════════════════════════════════════════════════════════════
# BRAIN / ML ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/brain/status")
def brain_status():
    """Get AI brain status."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    return jsonify(_brain.status())


@app.route("/api/brain/score", methods=["POST"])
def score_opportunity():
    """Score an opportunity using ML model."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    opportunity = data.get("opportunity", {})

    if not opportunity:
        return jsonify({"error": "opportunity required"}), 400

    score = _brain.score(opportunity)
    should_execute, reason = _brain.should_execute(opportunity)

    return jsonify({
        "ml_score": round(score, 4),
        "should_execute": should_execute,
        "reason": reason,
        "threshold": _brain.threshold(),
        "min_profit": _brain.min_profit(),
    })


@app.route("/api/brain/learn", methods=["POST"])
def learn_outcome():
    """Feed trade outcome back to brain for learning."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    opportunity = data.get("opportunity", {})
    success = data.get("success", False)
    actual_profit = float(data.get("actual_profit", 0))

    if not opportunity:
        return jsonify({"error": "opportunity required"}), 400

    _brain.learn(opportunity, success=success, actual_profit=actual_profit)

    return jsonify({
        "status": "learned",
        "success": success,
        "actual_profit": actual_profit,
    })


@app.route("/api/brain/regime")
def get_regime():
    """Get current market regime and strategy weights."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    return jsonify({
        "regime": _brain.regime(),
        "strategy_weights": _brain.strategy_weights(),
    })


@app.route("/api/brain/prices", methods=["POST"])
def update_prices():
    """Update price data for market classification."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    prices = data.get("prices", {})

    if prices:
        _brain.on_prices(prices)

    return jsonify({
        "status": "updated",
        "regime": _brain.regime(),
    })


# ══════════════════════════════════════════════════════════════
# BATCH OPERATIONS
# ══════════════════════════════════════════════════════════════

@app.route("/api/batch/score", methods=["POST"])
def batch_score():
    """Score multiple opportunities in batch."""
    if not _brain:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    opportunities = data.get("opportunities", [])

    if not opportunities:
        return jsonify({"error": "opportunities required"}), 400

    # Limit batch size
    opportunities = opportunities[:PREDICTION_BATCH_SIZE]

    results = []
    for opp in opportunities:
        score = _brain.score(opp)
        should_execute, reason = _brain.should_execute(opp)
        results.append({
            "id": opp.get("id", ""),
            "ml_score": round(score, 4),
            "should_execute": should_execute,
            "reason": reason,
        })

    return jsonify({
        "results": results,
        "count": len(results),
    })


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    """Main entry point for AI Inference Service."""
    logger.info("Starting AI Inference Service on port %d", SERVICE_PORT)

    # Initialize in background thread to not block startup
    init_thread = threading.Thread(target=initialize_services, daemon=True)
    init_thread.start()

    # Run Flask app
    app.run(
        host="0.0.0.0",
        port=SERVICE_PORT,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
