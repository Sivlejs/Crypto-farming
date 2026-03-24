"""
Nexus AI – Flask Web Application

Provides a real-time dashboard and REST API for the crypto farming agent.
"""
from __future__ import annotations

import os

from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_socketio import SocketIO, emit
import threading
import time

from nexus.agent import get_agent
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── App setup ─────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# ── Agent startup ─────────────────────────────────────────────

_agent_started = False
_agent_lock = threading.Lock()
_agent_start_thread: threading.Thread | None = None


def _start_agent_once():
    global _agent_started
    try:
        agent = get_agent()
        agent.start()
        with _agent_lock:
            _agent_started = True
        logger.info("Agent started successfully")
    except Exception as exc:
        logger.error("Failed to start agent: %s", exc)


# Start agent in background after first request is handled
@app.before_request
def ensure_agent():
    global _agent_start_thread
    if not _agent_started:
        with _agent_lock:
            # Only launch one start thread; check again inside the lock
            if not _agent_started and (
                _agent_start_thread is None or not _agent_start_thread.is_alive()
            ):
                _agent_start_thread = threading.Thread(target=_start_agent_once, daemon=True)
                _agent_start_thread.start()


# ── Web Routes ────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "nexus-ai"})


# ── REST API ──────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    try:
        agent = get_agent()
        return jsonify(agent.status())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/opportunities")
def api_opportunities():
    try:
        limit = int(request.args.get("limit", 20))
        agent = get_agent()
        return jsonify(agent.get_opportunities(limit=limit))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/trades")
def api_trades():
    try:
        limit = int(request.args.get("limit", 50))
        agent = get_agent()
        return jsonify(agent.get_recent_trades(limit=limit))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/prices")
def api_prices():
    try:
        from nexus.protocols.dex_aggregator import PriceAggregator
        prices = PriceAggregator.get_all_prices()
        return jsonify(prices)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/yields")
def api_yields():
    try:
        from nexus.protocols.dex_aggregator import PriceAggregator
        yields = PriceAggregator.get_yield_rates()
        return jsonify(yields[:20])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/config")
def api_config():
    return jsonify(Config.to_dict())


@app.route("/api/payout")
def api_payout():
    """Current payout status and accumulated pending balance."""
    try:
        agent = get_agent()
        return jsonify(agent.payout.status())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/payout/history")
def api_payout_history():
    """Recent payout transactions."""
    try:
        limit = int(request.args.get("limit", 20))
        agent = get_agent()
        return jsonify(agent.get_payout_history(limit=limit))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/payout/sweep", methods=["POST"])
def api_payout_sweep():
    """Manually trigger an immediate payout sweep."""
    try:
        chain = request.json.get("chain", "ethereum") if request.is_json else "ethereum"
        agent = get_agent()
        result = agent.force_payout(chain=chain)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/learning")
def api_learning():
    """Return full AI brain / learning status."""
    try:
        agent = get_agent()
        return jsonify(agent.brain.status())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/learning/params")
def api_learning_params():
    """Return current adaptive parameters."""
    try:
        agent = get_agent()
        return jsonify(agent.brain.optimizer.all_params())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/learning/regime")
def api_learning_regime():
    """Return current market regime and strategy weights."""
    try:
        agent = get_agent()
        return jsonify({
            "regime":           agent.brain.regime(),
            "strategy_weights": agent.brain.strategy_weights(),
            "market_status":    agent.brain.classifier.status(),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── WebSocket ─────────────────────────────────────────────────

@socketio.on("connect")
def ws_connect():
    logger.debug("WebSocket client connected")
    _push_status()


@socketio.on("request_update")
def ws_request_update():
    _push_status()


def _push_status():
    try:
        agent = get_agent()
        emit("status_update", agent.status())
        emit("opportunities_update", agent.get_opportunities(limit=10))
        emit("trades_update", agent.get_recent_trades(limit=10))
        emit("payout_update", agent.payout.status())
    except Exception as exc:
        logger.debug("WebSocket push failed: %s", exc)


def _background_pusher():
    """Periodically push updates to all connected WebSocket clients."""
    while True:
        time.sleep(Config.SCAN_INTERVAL_SECONDS)
        try:
            agent = get_agent()
            socketio.emit("status_update", agent.status())
            socketio.emit("opportunities_update", agent.get_opportunities(limit=10))
            socketio.emit("trades_update", agent.get_recent_trades(limit=10))
            socketio.emit("payout_update", agent.payout.status())
        except Exception:
            pass


# Start background pusher thread
_pusher_thread = threading.Thread(target=_background_pusher, daemon=True)
_pusher_thread.start()


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
