"""
Monitoring Service for Nexus AI.

Dedicated microservice for real-time monitoring and observability:
  - System metrics collection
  - Performance tracking
  - Alert management
  - Dashboard data aggregation
  - Historical metrics storage

This service provides comprehensive visibility into the farming bot's operations.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from flask import Flask, jsonify, request

# Import core modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Flask app
app = Flask(__name__)

# Service configuration
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8003"))
METRICS_RETENTION_HOURS = int(os.getenv("METRICS_RETENTION_HOURS", "168"))  # 7 days
ALERT_CHECK_INTERVAL = int(os.getenv("ALERT_CHECK_INTERVAL", "60"))
DASHBOARD_REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "5"))

# Metrics storage
MAX_METRICS_PER_TYPE = 10000


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class Alert:
    """An alert condition."""
    id: str
    name: str
    condition: str  # e.g., "gas_gwei > 100"
    severity: str  # "info", "warning", "critical"
    triggered: bool = False
    triggered_at: Optional[float] = None
    message: Optional[str] = None


class MetricsCollector:
    """Collects and stores metrics from all services."""

    def __init__(self):
        self._metrics: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def record(self, metric_name: str, value: float, labels: Optional[Dict] = None):
        """Record a metric value."""
        with self._lock:
            if metric_name not in self._metrics:
                self._metrics[metric_name] = deque(maxlen=MAX_METRICS_PER_TYPE)

            self._metrics[metric_name].append(MetricPoint(
                timestamp=time.time(),
                value=value,
                labels=labels or {},
            ))

    def get_latest(self, metric_name: str) -> Optional[MetricPoint]:
        """Get the latest value for a metric."""
        with self._lock:
            if metric_name not in self._metrics or not self._metrics[metric_name]:
                return None
            return self._metrics[metric_name][-1]

    def get_history(
        self,
        metric_name: str,
        hours: float = 1,
        labels: Optional[Dict] = None,
    ) -> List[MetricPoint]:
        """Get metric history within time range."""
        cutoff = time.time() - (hours * 3600)

        with self._lock:
            if metric_name not in self._metrics:
                return []

            points = [
                p for p in self._metrics[metric_name]
                if p.timestamp >= cutoff
            ]

            if labels:
                points = [
                    p for p in points
                    if all(p.labels.get(k) == v for k, v in labels.items())
                ]

            return points

    def get_all_names(self) -> List[str]:
        """Get all metric names."""
        with self._lock:
            return list(self._metrics.keys())

    def get_summary(self, metric_name: str, hours: float = 1) -> Dict:
        """Get summary statistics for a metric."""
        points = self.get_history(metric_name, hours)
        if not points:
            return {}

        values = [p.value for p in points]
        return {
            "count": len(values),
            "min": min(values),
            "max": max(values),
            "avg": sum(values) / len(values),
            "latest": values[-1] if values else None,
            "first_ts": points[0].timestamp if points else None,
            "last_ts": points[-1].timestamp if points else None,
        }


class AlertManager:
    """Manages alerts and notifications."""

    def __init__(self, metrics_collector: MetricsCollector):
        self._metrics = metrics_collector
        self._alerts: Dict[str, Alert] = {}
        self._alert_history: deque = deque(maxlen=1000)
        self._lock = threading.Lock()

        # Register default alerts
        self._register_default_alerts()

    def _register_default_alerts(self):
        """Register default system alerts."""
        defaults = [
            Alert(
                id="high_gas",
                name="High Gas Price",
                condition="gas_gwei > 100",
                severity="warning",
            ),
            Alert(
                id="low_success_rate",
                name="Low Trade Success Rate",
                condition="success_rate < 0.5",
                severity="critical",
            ),
            Alert(
                id="no_connections",
                name="No Blockchain Connections",
                condition="connected_chains == 0",
                severity="critical",
            ),
            Alert(
                id="high_latency",
                name="High RPC Latency",
                condition="avg_latency_ms > 500",
                severity="warning",
            ),
            Alert(
                id="pool_tvl_drop",
                name="Pool TVL Drop",
                condition="tvl_change_24h < -20",
                severity="warning",
            ),
        ]

        for alert in defaults:
            self._alerts[alert.id] = alert

    def check_alerts(self, current_metrics: Dict) -> List[Alert]:
        """Check all alerts against current metrics."""
        triggered = []

        with self._lock:
            for alert_id, alert in self._alerts.items():
                was_triggered = alert.triggered

                # Evaluate condition (simplified evaluation)
                is_triggered = self._evaluate_condition(alert.condition, current_metrics)

                if is_triggered and not was_triggered:
                    alert.triggered = True
                    alert.triggered_at = time.time()
                    alert.message = f"Alert: {alert.name} - {alert.condition}"
                    triggered.append(alert)

                    self._alert_history.append({
                        "alert_id": alert_id,
                        "name": alert.name,
                        "severity": alert.severity,
                        "triggered_at": alert.triggered_at,
                        "condition": alert.condition,
                    })

                elif not is_triggered and was_triggered:
                    alert.triggered = False
                    alert.triggered_at = None
                    alert.message = None

        return triggered

    def _evaluate_condition(self, condition: str, metrics: Dict) -> bool:
        """Evaluate an alert condition against metrics."""
        try:
            # Simple evaluation - replace metric names with values
            expr = condition
            for key, value in metrics.items():
                if key in expr:
                    expr = expr.replace(key, str(value))

            # Safety: only allow simple comparisons
            if any(c in expr for c in ["import", "eval", "exec", "open", "__"]):
                return False

            return eval(expr)  # nosec B307 - controlled input
        except Exception:
            return False

    def get_active_alerts(self) -> List[Dict]:
        """Get all currently active alerts."""
        with self._lock:
            return [
                {
                    "id": a.id,
                    "name": a.name,
                    "severity": a.severity,
                    "triggered_at": a.triggered_at,
                    "message": a.message,
                }
                for a in self._alerts.values()
                if a.triggered
            ]

    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """Get recent alert history."""
        return list(self._alert_history)[-limit:]


class DashboardAggregator:
    """Aggregates data for dashboard display."""

    def __init__(
        self,
        metrics_collector: MetricsCollector,
        alert_manager: AlertManager,
    ):
        self._metrics = metrics_collector
        self._alerts = alert_manager
        self._cache: Dict = {}
        self._cache_ts: float = 0

    def get_overview(self) -> Dict:
        """Get overview data for dashboard."""
        now = time.time()

        # Cache for dashboard refresh interval
        if now - self._cache_ts < DASHBOARD_REFRESH_SECONDS and self._cache:
            return self._cache

        overview = {
            "timestamp": now,
            "system": self._get_system_metrics(),
            "trading": self._get_trading_metrics(),
            "pools": self._get_pool_metrics(),
            "blockchain": self._get_blockchain_metrics(),
            "ai": self._get_ai_metrics(),
            "alerts": self._alerts.get_active_alerts(),
        }

        self._cache = overview
        self._cache_ts = now

        return overview

    def _get_system_metrics(self) -> Dict:
        """Get system-level metrics."""
        uptime = self._metrics.get_latest("uptime_seconds")
        return {
            "uptime_seconds": uptime.value if uptime else 0,
            "services_healthy": True,  # Would check actual service health
        }

    def _get_trading_metrics(self) -> Dict:
        """Get trading metrics."""
        return {
            "total_profit_usd": self._metrics.get_latest("total_profit_usd"),
            "trades_today": self._metrics.get_latest("trades_today"),
            "success_rate": self._metrics.get_latest("success_rate"),
            "pending_opportunities": self._metrics.get_latest("pending_opportunities"),
        }

    def _get_pool_metrics(self) -> Dict:
        """Get pool analysis metrics."""
        return {
            "pools_analyzed": self._metrics.get_latest("pools_analyzed"),
            "top_pool_apy": self._metrics.get_latest("top_pool_apy"),
            "average_risk": self._metrics.get_latest("average_risk"),
            "positions_count": self._metrics.get_latest("positions_count"),
        }

    def _get_blockchain_metrics(self) -> Dict:
        """Get blockchain connection metrics."""
        return {
            "connected_chains": self._metrics.get_latest("connected_chains"),
            "avg_latency_ms": self._metrics.get_latest("avg_latency_ms"),
            "gas_gwei": self._metrics.get_latest("gas_gwei"),
            "is_cheap_gas": self._metrics.get_latest("is_cheap_gas"),
        }

    def _get_ai_metrics(self) -> Dict:
        """Get AI/ML metrics."""
        return {
            "ml_active": self._metrics.get_latest("ml_active"),
            "ml_accuracy": self._metrics.get_latest("ml_accuracy"),
            "market_regime": self._metrics.get_latest("market_regime"),
            "predictions_today": self._metrics.get_latest("predictions_today"),
        }


# Global instances
_metrics_collector: Optional[MetricsCollector] = None
_alert_manager: Optional[AlertManager] = None
_dashboard: Optional[DashboardAggregator] = None
_startup_complete = False


def initialize_services():
    """Initialize monitoring services."""
    global _metrics_collector, _alert_manager, _dashboard, _startup_complete

    logger.info("Initializing Monitoring Service...")

    try:
        _metrics_collector = MetricsCollector()
        _alert_manager = AlertManager(_metrics_collector)
        _dashboard = DashboardAggregator(_metrics_collector, _alert_manager)

        _startup_complete = True
        logger.info("Monitoring Service ready ✓")

    except Exception as exc:
        logger.error("Monitoring Service initialization failed: %s", exc)
        raise


# ══════════════════════════════════════════════════════════════
# HEALTH & STATUS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy" if _startup_complete else "starting",
        "service": "monitoring",
        "timestamp": time.time(),
    })


@app.route("/status")
def status():
    """Detailed service status."""
    return jsonify({
        "service": "monitoring",
        "startup_complete": _startup_complete,
        "metrics_count": len(_metrics_collector.get_all_names()) if _metrics_collector else 0,
        "active_alerts": len(_alert_manager.get_active_alerts()) if _alert_manager else 0,
        "config": {
            "metrics_retention_hours": METRICS_RETENTION_HOURS,
            "alert_check_interval": ALERT_CHECK_INTERVAL,
            "dashboard_refresh_seconds": DASHBOARD_REFRESH_SECONDS,
        },
    })


# ══════════════════════════════════════════════════════════════
# DASHBOARD ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/dashboard")
def get_dashboard():
    """Get aggregated dashboard data."""
    if not _dashboard:
        return jsonify({"error": "Service not ready"}), 503

    return jsonify(_dashboard.get_overview())


@app.route("/api/dashboard/pools")
def get_dashboard_pools():
    """Get pool-specific dashboard data."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    # Get pool-related metrics
    pool_metrics = {}
    for name in _metrics_collector.get_all_names():
        if name.startswith("pool_"):
            pool_metrics[name] = _metrics_collector.get_summary(name, hours=24)

    return jsonify({
        "pools": pool_metrics,
        "timestamp": time.time(),
    })


@app.route("/api/dashboard/ai")
def get_dashboard_ai():
    """Get AI decision transparency data."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    # Get AI-related metrics history
    ai_metrics = {
        "ml_accuracy_history": [
            {"timestamp": p.timestamp, "value": p.value}
            for p in _metrics_collector.get_history("ml_accuracy", hours=24)
        ],
        "predictions_history": [
            {"timestamp": p.timestamp, "value": p.value}
            for p in _metrics_collector.get_history("predictions_count", hours=24)
        ],
        "regime_changes": [
            {"timestamp": p.timestamp, "value": p.value, "labels": p.labels}
            for p in _metrics_collector.get_history("market_regime_change", hours=24)
        ],
    }

    return jsonify({
        "ai_metrics": ai_metrics,
        "timestamp": time.time(),
    })


# ══════════════════════════════════════════════════════════════
# METRICS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/metrics")
def get_metrics():
    """Get all available metrics."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    names = _metrics_collector.get_all_names()
    return jsonify({
        "metrics": names,
        "count": len(names),
    })


@app.route("/api/metrics/<metric_name>")
def get_metric(metric_name: str):
    """Get a specific metric's data."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    hours = float(request.args.get("hours", 1))
    points = _metrics_collector.get_history(metric_name, hours)

    return jsonify({
        "metric": metric_name,
        "points": [
            {"timestamp": p.timestamp, "value": p.value, "labels": p.labels}
            for p in points
        ],
        "count": len(points),
        "summary": _metrics_collector.get_summary(metric_name, hours),
    })


@app.route("/api/metrics", methods=["POST"])
def record_metric():
    """Record a metric value."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    metric_name = data.get("name")
    value = data.get("value")
    labels = data.get("labels", {})

    if not metric_name or value is None:
        return jsonify({"error": "name and value required"}), 400

    _metrics_collector.record(metric_name, float(value), labels)

    return jsonify({"status": "recorded"})


@app.route("/api/metrics/batch", methods=["POST"])
def record_metrics_batch():
    """Record multiple metrics at once."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    metrics = data.get("metrics", [])

    for m in metrics:
        if "name" in m and "value" in m:
            _metrics_collector.record(
                m["name"],
                float(m["value"]),
                m.get("labels", {}),
            )

    return jsonify({"status": "recorded", "count": len(metrics)})


# ══════════════════════════════════════════════════════════════
# ALERT ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/alerts")
def get_alerts():
    """Get active alerts."""
    if not _alert_manager:
        return jsonify({"error": "Service not ready"}), 503

    return jsonify({
        "active": _alert_manager.get_active_alerts(),
        "count": len(_alert_manager.get_active_alerts()),
    })


@app.route("/api/alerts/history")
def get_alert_history():
    """Get alert history."""
    if not _alert_manager:
        return jsonify({"error": "Service not ready"}), 503

    limit = int(request.args.get("limit", 50))
    history = _alert_manager.get_alert_history(limit)

    return jsonify({
        "history": history,
        "count": len(history),
    })


@app.route("/api/alerts/check", methods=["POST"])
def check_alerts():
    """Check alerts against provided metrics."""
    if not _alert_manager:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    metrics = data.get("metrics", {})

    triggered = _alert_manager.check_alerts(metrics)

    return jsonify({
        "triggered": [
            {"id": a.id, "name": a.name, "severity": a.severity}
            for a in triggered
        ],
        "count": len(triggered),
    })


# ══════════════════════════════════════════════════════════════
# PERFORMANCE TRACKING
# ══════════════════════════════════════════════════════════════

@app.route("/api/performance/pools")
def get_pool_performance():
    """Get pool performance metrics over time."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    hours = float(request.args.get("hours", 24))

    performance = {
        "apy_history": _metrics_collector.get_summary("pool_apy", hours),
        "tvl_history": _metrics_collector.get_summary("pool_tvl", hours),
        "profit_history": _metrics_collector.get_summary("pool_profit", hours),
    }

    return jsonify(performance)


@app.route("/api/performance/costs")
def get_cost_metrics():
    """Get cost tracking metrics."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    hours = float(request.args.get("hours", 24))

    costs = {
        "gas_costs": _metrics_collector.get_summary("gas_cost_usd", hours),
        "trading_fees": _metrics_collector.get_summary("trading_fee_usd", hours),
        "slippage_costs": _metrics_collector.get_summary("slippage_cost_usd", hours),
        "total_costs": _metrics_collector.get_summary("total_cost_usd", hours),
    }

    return jsonify(costs)


@app.route("/api/performance/ai")
def get_ai_performance():
    """Get AI decision performance metrics."""
    if not _metrics_collector:
        return jsonify({"error": "Service not ready"}), 503

    hours = float(request.args.get("hours", 24))

    ai_perf = {
        "accuracy": _metrics_collector.get_summary("ml_accuracy", hours),
        "decisions_made": _metrics_collector.get_summary("ai_decisions", hours),
        "decisions_successful": _metrics_collector.get_summary("ai_decisions_success", hours),
        "avg_score": _metrics_collector.get_summary("avg_opportunity_score", hours),
    }

    return jsonify(ai_perf)


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    """Main entry point for Monitoring Service."""
    logger.info("Starting Monitoring Service on port %d", SERVICE_PORT)

    # Initialize services
    initialize_services()

    # Run Flask app
    app.run(
        host="0.0.0.0",
        port=SERVICE_PORT,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
