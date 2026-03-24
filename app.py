"""
Nexus AI – Flask Web Application

Provides a real-time dashboard and REST API for the crypto farming agent.
"""
from __future__ import annotations

# ── Eventlet monkey patching ──────────────────────────────────
# MUST happen before ANY other imports (threading, socket, ssl, etc.)
# Required for gunicorn eventlet worker and Flask-SocketIO async_mode="eventlet"
import eventlet
eventlet.monkey_patch()

import os

from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from flask_socketio import SocketIO, emit
import threading
import time

from nexus.agent import get_agent
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
from nexus.chat.nexus_llm import get_chat_engine
from nexus.voice.engine import get_voice_engine

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


# ── Chat & Voice API ──────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Send a text (or voice-transcribed) message to Nexus AI and get a reply.

    Request JSON:
        { "message": "What's my profit?", "speak": true }

    Response JSON:
        { "reply": "...", "intent": "profit", "action": null, "speak": true }
    """
    try:
        data = request.get_json(force=True) or {}
        user_message = (data.get("message") or "").strip()
        if not user_message:
            return jsonify({"error": "message is required"}), 400

        # Gather live bot status for context
        bot_status = {}
        try:
            agent = get_agent()
            bot_status = agent.status()
        except Exception:
            pass

        chat = get_chat_engine()
        result = chat.chat(user_message, bot_status=bot_status)

        # Execute any requested bot action
        action = result.get("action")
        if action == "start":
            try:
                get_agent().start()
            except Exception:
                pass
        elif action == "stop":
            try:
                get_agent().stop()
            except Exception:
                pass
        elif action == "payout":
            try:
                get_agent().force_payout()
            except Exception:
                pass
        elif action == "set_dry_run":
            try:
                dry = data.get("dry_run", True)
                Config.DRY_RUN = dry
            except Exception:
                pass

        return jsonify(result)
    except Exception as exc:
        logger.error("Chat error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/voice/tts", methods=["POST"])
def api_voice_tts():
    """
    Text-to-speech endpoint.

    Request JSON: { "text": "Hello from Nexus" }
    Response:
      - MP3 audio bytes (Content-Type: audio/mpeg) if ElevenLabs is configured
      - JSON { "use_browser_tts": true, "text": "..." } otherwise
    """
    try:
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "text is required"}), 400

        engine = get_voice_engine()
        audio = engine.text_to_speech(text)
        if audio:
            return Response(audio, mimetype="audio/mpeg")
        # Signal client to use browser synthesis
        return jsonify({"use_browser_tts": True, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/voice/status")
def api_voice_status():
    """Return voice engine configuration."""
    try:
        engine = get_voice_engine()
        return jsonify(engine.status())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Bot Control API ───────────────────────────────────────────

@app.route("/api/control", methods=["POST"])
def api_control():
    """
    Control the bot from the dashboard.

    Request JSON: { "action": "start" | "stop" | "set_dry_run", "value": ... }
    """
    try:
        data = request.get_json(force=True) or {}
        action = data.get("action", "")
        agent = get_agent()
        if action == "start":
            agent.start()
            return jsonify({"ok": True, "action": "start"})
        elif action == "stop":
            agent.stop()
            return jsonify({"ok": True, "action": "stop"})
        elif action == "set_dry_run":
            val = bool(data.get("value", True))
            Config.DRY_RUN = val
            return jsonify({"ok": True, "dry_run": val})
        return jsonify({"error": f"Unknown action: {action}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Timing API ────────────────────────────────────────────────

@app.route("/api/timing")
def api_timing():
    """Return gas oracle and trade scheduler stats."""
    try:
        from nexus.timing.trade_scheduler import get_trade_scheduler
        scheduler = get_trade_scheduler()
        return jsonify(scheduler.stats())
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
