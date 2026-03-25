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
_MAX_AGENT_INIT_ATTEMPTS = 3


def _start_agent_once():
    """Initialize and start the agent with retry logic."""
    global _agent_started
    
    init_attempts = 0
    while init_attempts < _MAX_AGENT_INIT_ATTEMPTS:
        init_attempts += 1
        try:
            agent = get_agent()
            agent.start()
            with _agent_lock:
                _agent_started = True
            chains = agent.blockchain.connected_chains()
            logger.info(
                "Agent started successfully with %d chains: %s",
                len(chains), chains or "none yet"
            )
            return
        except Exception as exc:
            logger.warning(
                "Agent start attempt %d/%d failed: %s",
                init_attempts, _MAX_AGENT_INIT_ATTEMPTS, exc
            )
            if init_attempts < _MAX_AGENT_INIT_ATTEMPTS:
                time.sleep(5)  # Wait before retry
    
    # After all retries, mark as started anyway to prevent infinite attempts
    # The agent will continue trying to connect to chains in the background
    logger.warning(
        "Agent initialization had issues but will continue running in degraded mode"
    )
    with _agent_lock:
        _agent_started = True


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
        cmd_params = result.get("params", {})
        
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
        elif action in ("set_min_profit", "set_gas_limit", "set_slippage", "set_threshold", "set_payout_addr"):
            # Handle settings changes from chat
            try:
                from nexus.utils.settings import get_settings
                from nexus.chat.command_processor import parse_command
                
                settings = get_settings()
                cmd = parse_command(user_message)
                value = cmd.params.get("value") or cmd.params.get("address")
                
                if value:
                    setting_map = {
                        "set_min_profit": "min_profit_usd",
                        "set_gas_limit": "max_gas_gwei",
                        "set_slippage": "slippage_percent",
                        "set_threshold": "payout_threshold_usd",
                        "set_payout_addr": "payout_address",
                    }
                    setting_key = setting_map.get(action)
                    if setting_key:
                        settings.set(setting_key, value)
            except Exception as exc:
                logger.debug("Settings update from chat failed: %s", exc)

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


# ── Mining API ────────────────────────────────────────────────

@app.route("/api/mining/status")
def api_mining_status():
    """Get PoW mining status and statistics."""
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy, get_mining_environment_info
        agent = get_agent()
        
        # Find the PoW mining strategy in the monitor
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if pow_strategy:
            status = pow_strategy.status()
        else:
            # Strategy not enabled, return environment info only
            status = {
                "name": "pow_mining",
                "configured": False,
                "running": False,
                "environment": get_mining_environment_info(),
            }
        
        return jsonify(status)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/start", methods=["POST"])
def api_mining_start():
    """Start PoW mining."""
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy
        agent = get_agent()
        
        # Find the PoW mining strategy
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if not pow_strategy:
            return jsonify({"error": "PoW mining strategy not enabled. Set STRATEGY_POW_MINING=true"}), 400
        
        if pow_strategy.start_mining():
            return jsonify({"ok": True, "message": "Mining started"})
        else:
            return jsonify({"error": "Failed to start mining. Check pool configuration."}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/stop", methods=["POST"])
def api_mining_stop():
    """Stop PoW mining."""
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy
        agent = get_agent()
        
        # Find the PoW mining strategy
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if pow_strategy:
            pow_strategy.stop_mining()
            return jsonify({"ok": True, "message": "Mining stopped"})
        else:
            return jsonify({"error": "PoW mining strategy not enabled"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/pause", methods=["POST"])
def api_mining_pause():
    """Temporarily pause mining (keeps pool connection)."""
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy
        agent = get_agent()
        
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if pow_strategy:
            pow_strategy.pause_mining()
            return jsonify({"ok": True, "message": "Mining paused"})
        else:
            return jsonify({"error": "PoW mining strategy not enabled"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/resume", methods=["POST"])
def api_mining_resume():
    """Resume paused mining."""
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy
        agent = get_agent()
        
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if pow_strategy:
            pow_strategy.resume_mining()
            return jsonify({"ok": True, "message": "Mining resumed"})
        else:
            return jsonify({"error": "PoW mining strategy not enabled"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/configure", methods=["POST"])
def api_mining_configure():
    """
    Update mining parameters.
    
    Request JSON: { "intensity": 50, "max_cpu_percent": 80 }
    """
    try:
        from nexus.strategies.pow_mining import PoWMiningStrategy
        data = request.get_json(force=True) or {}
        agent = get_agent()
        
        pow_strategy = None
        for strategy in agent.monitor._strategies:
            if isinstance(strategy, PoWMiningStrategy):
                pow_strategy = strategy
                break
        
        if not pow_strategy:
            return jsonify({"error": "PoW mining strategy not enabled"}), 400
        
        updated = []
        
        if "intensity" in data:
            intensity = int(data["intensity"])
            pow_strategy.update_intensity(intensity)
            updated.append(f"intensity={intensity}")
        
        if "threads" in data:
            threads = int(data["threads"])
            pow_strategy.update_threads(threads)
            updated.append(f"threads={threads} (requires restart)")
        
        return jsonify({
            "ok": True,
            "updated": updated,
            "status": pow_strategy.status()
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/mining/environment")
def api_mining_environment():
    """Get mining environment information (CPU, memory, virtual server detection)."""
    try:
        from nexus.strategies.pow_mining import get_mining_environment_info
        return jsonify(get_mining_environment_info())
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


# ── Settings API ──────────────────────────────────────────────

@app.route("/api/settings")
def api_settings():
    """Get all runtime-configurable settings."""
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        return jsonify(settings.get_all(include_sensitive=False))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings/<category>")
def api_settings_category(category: str):
    """Get settings for a specific category."""
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        return jsonify(settings.get_by_category(category, include_sensitive=False))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    """
    Update one or more settings.

    Request JSON: { "key": "value", ... }
    or single: { "key": "setting_name", "value": "new_value" }
    """
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        data = request.get_json(force=True) or {}

        # Handle single key-value update
        if "key" in data and "value" in data:
            result = settings.set(data["key"], data["value"])
            return jsonify(result)

        # Handle batch update
        results = []
        for key, value in data.items():
            result = settings.set(key, value)
            results.append(result)

        success_count = sum(1 for r in results if r.get("success"))
        return jsonify({
            "success": success_count == len(results),
            "updated": success_count,
            "total": len(results),
            "results": results,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings/reset", methods=["POST"])
def api_settings_reset():
    """
    Reset settings to defaults.

    Request JSON: { "key": "setting_name" } to reset one setting
    or { "all": true } to reset all settings
    """
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        data = request.get_json(force=True) or {}

        if data.get("all"):
            result = settings.reset_all()
        elif "key" in data:
            result = settings.reset(data["key"])
        else:
            return jsonify({"error": "Provide 'key' or 'all' in request body"}), 400

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings/coinbase", methods=["POST"])
def api_settings_coinbase():
    """
    Configure Coinbase API credentials.

    Request JSON: {
        "api_key": "your-api-key",
        "api_secret": "your-api-secret",
        "account_id": "optional-account-id"
    }
    """
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        data = request.get_json(force=True) or {}

        results = []
        if "api_key" in data:
            results.append(settings.set("coinbase_api_key", data["api_key"]))
        if "api_secret" in data:
            results.append(settings.set("coinbase_api_secret", data["api_secret"]))
        if "account_id" in data:
            results.append(settings.set("coinbase_account_id", data["account_id"]))

        success = all(r.get("success") for r in results)
        return jsonify({
            "success": success,
            "message": "Coinbase credentials updated" if success else "Some updates failed",
            "configured": bool(
                settings.get("coinbase_api_key") and settings.get("coinbase_api_secret")
            ),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/settings/payout", methods=["POST"])
def api_settings_payout():
    """
    Configure payout settings.

    Request JSON: {
        "address": "0x...",
        "chain": "ethereum",
        "token": "USDC",
        "threshold_usd": 10.0,
        "lightning_address": "$cashtag"
    }
    """
    try:
        from nexus.utils.settings import get_settings
        settings = get_settings()
        data = request.get_json(force=True) or {}

        mapping = {
            "address": "payout_address",
            "chain": "payout_chain",
            "token": "payout_token",
            "threshold_usd": "payout_threshold_usd",
            "lightning_address": "lightning_address",
        }

        results = []
        for request_key, setting_key in mapping.items():
            if request_key in data:
                results.append(settings.set(setting_key, data[request_key]))

        success = all(r.get("success") for r in results)
        return jsonify({
            "success": success,
            "message": "Payout settings updated" if success else "Some updates failed",
            "results": results,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Pool Analysis API (Advanced AI Infrastructure) ────────────

@app.route("/api/pools")
def api_pools():
    """
    Get analyzed farming pools with AI-powered metrics.
    
    Query parameters:
        chain (str): Filter by chain (e.g., "ethereum")
        limit (int): Max pools to return (default: 20)
        min_tvl (float): Minimum TVL in USD (default: 500000)
        max_risk (float): Maximum risk score 0-1 (default: 0.8)
        stablecoins_only (bool): Only stablecoin pools (default: false)
    """
    try:
        from nexus.learning.pool_analyzer import get_pool_analyzer
        analyzer = get_pool_analyzer()
        
        chain = request.args.get("chain")
        limit = int(request.args.get("limit", 20))
        min_tvl = float(request.args.get("min_tvl", 500000))
        max_risk = float(request.args.get("max_risk", 0.8))
        stablecoins_only = request.args.get("stablecoins_only", "false").lower() == "true"
        
        pools = analyzer.get_top_pools(
            chain=chain,
            limit=limit,
            min_tvl=min_tvl,
            max_risk=max_risk,
            stablecoins_only=stablecoins_only,
        )
        
        return jsonify({
            "pools": [p.to_dict() for p in pools],
            "count": len(pools),
            "analyzer_status": analyzer.status(),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pools/<pool_id>")
def api_pool_detail(pool_id: str):
    """Get detailed metrics for a specific pool."""
    try:
        from nexus.learning.pool_analyzer import get_pool_analyzer
        analyzer = get_pool_analyzer()
        
        pool = analyzer.get_pool(pool_id)
        if not pool:
            return jsonify({"error": "Pool not found"}), 404
            
        return jsonify(pool.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pools/compare", methods=["POST"])
def api_pools_compare():
    """Compare multiple pools side-by-side for decision transparency."""
    try:
        from nexus.learning.pool_analyzer import get_pool_analyzer
        analyzer = get_pool_analyzer()
        
        data = request.get_json(force=True) or {}
        pool_ids = data.get("pool_ids", [])
        
        if not pool_ids:
            return jsonify({"error": "pool_ids required"}), 400
            
        comparisons = analyzer.get_pool_comparison(pool_ids)
        return jsonify({"comparisons": comparisons})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pools/decisions")
def api_pool_decisions():
    """Get AI decision log showing why pools were ranked."""
    try:
        from nexus.learning.pool_analyzer import get_pool_analyzer
        analyzer = get_pool_analyzer()
        
        limit = int(request.args.get("limit", 10))
        decisions = analyzer.get_ai_decision_log(limit=limit)
        
        return jsonify({"decisions": decisions})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Pool Optimization API ─────────────────────────────────────

@app.route("/api/optimize/allocation", methods=["POST"])
def api_optimize_allocation():
    """
    Get AI-optimized pool allocation recommendation.
    
    Request JSON:
        capital_usd (float): Total capital to allocate
        strategy (str): "max_yield", "risk_adjusted", "conservative", "balanced", "gas_efficient"
        chains (list): Optional - limit to specific chains
        max_pools (int): Maximum pools to recommend (default: 5)
    """
    try:
        from nexus.learning.pool_optimizer import get_pool_optimizer, OptimizationStrategy
        optimizer = get_pool_optimizer()
        
        data = request.get_json(force=True) or {}
        capital_usd = float(data.get("capital_usd", 1000))
        strategy_name = data.get("strategy", "balanced")
        chains = data.get("chains")
        max_pools = int(data.get("max_pools", 5))
        
        strategy_map = {
            "max_yield": OptimizationStrategy.MAX_YIELD,
            "risk_adjusted": OptimizationStrategy.RISK_ADJUSTED,
            "conservative": OptimizationStrategy.CONSERVATIVE,
            "balanced": OptimizationStrategy.BALANCED,
            "gas_efficient": OptimizationStrategy.GAS_EFFICIENT,
        }
        strategy = strategy_map.get(strategy_name, OptimizationStrategy.BALANCED)
        
        recommendations, profit_split = optimizer.get_optimal_allocation(
            capital_usd=capital_usd,
            strategy=strategy,
            chains=chains,
            max_pools=max_pools,
        )
        
        return jsonify({
            "recommendations": [r.to_dict() for r in recommendations],
            "profit_split": profit_split.to_dict(),
            "input": {
                "capital_usd": capital_usd,
                "strategy": strategy_name,
                "chains": chains,
                "max_pools": max_pools,
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/optimize/split", methods=["POST"])
def api_optimize_split():
    """
    Optimize profit split across specified pools.
    
    Request JSON:
        pool_ids (list): Pool IDs to consider
        capital_usd (float): Total capital to allocate
        constraints (dict): Optional allocation constraints
    """
    try:
        from nexus.learning.pool_optimizer import get_pool_optimizer
        optimizer = get_pool_optimizer()
        
        data = request.get_json(force=True) or {}
        pool_ids = data.get("pool_ids", [])
        capital_usd = float(data.get("capital_usd", 1000))
        constraints = data.get("constraints", {})
        
        if not pool_ids:
            return jsonify({"error": "pool_ids required"}), 400
            
        split = optimizer.optimize_profit_split(
            pool_ids=pool_ids,
            capital_usd=capital_usd,
            constraints=constraints,
        )
        
        return jsonify({"profit_split": split.to_dict()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/optimize/positions")
def api_optimize_positions():
    """Get current positions and total exposure from optimizer."""
    try:
        from nexus.learning.pool_optimizer import get_pool_optimizer
        optimizer = get_pool_optimizer()
        
        positions = optimizer.get_positions()
        exposure = optimizer.get_total_exposure()
        
        return jsonify({
            "positions": positions,
            "exposure": exposure,
            "optimizer_status": optimizer.status(),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/optimize/decision", methods=["POST"])
def api_optimize_decision():
    """Get an optimization decision for a specific action."""
    try:
        from nexus.learning.pool_optimizer import get_pool_optimizer
        optimizer = get_pool_optimizer()
        
        data = request.get_json(force=True) or {}
        capital_usd = float(data.get("capital_usd", 1000))
        action_type = data.get("action_type", "enter")
        pool_id = data.get("pool_id")
        
        decision = optimizer.get_optimization_decision(
            capital_usd=capital_usd,
            action_type=action_type,
            pool_id=pool_id,
        )
        
        return jsonify({"decision": decision.to_dict()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Pool Executor API (Entry/Exit/Auto-Compound) ──────────────

@app.route("/api/executor/best-pools", methods=["POST"])
def api_executor_best_pools():
    """
    Get the best pools to enter with given capital.
    
    Uses multi-source data and AI optimization to find
    pools that outperform typical farming strategies.
    
    Request JSON:
        capital_usd (float): Available capital
        max_pools (int): Maximum pools to recommend (default: 5)
        risk_level (str): "low", "medium", or "high" (default: "medium")
    """
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        data = request.get_json(force=True) or {}
        capital_usd = float(data.get("capital_usd", 1000))
        max_pools = int(data.get("max_pools", 5))
        risk_level = data.get("risk_level", "medium")
        
        recommendations = executor.get_best_pools_to_enter(
            capital_usd=capital_usd,
            max_pools=max_pools,
            risk_level=risk_level,
        )
        
        return jsonify({
            "recommendations": recommendations,
            "count": len(recommendations),
            "input": {
                "capital_usd": capital_usd,
                "max_pools": max_pools,
                "risk_level": risk_level,
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/enter", methods=["POST"])
def api_executor_enter():
    """
    Enter a farming pool.
    
    Request JSON:
        pool_id (str): Pool identifier
        amount_usd (float): Amount to deposit
        max_slippage_bps (int): Max slippage in basis points (default: 50)
        wait_for_gas (bool): Wait for optimal gas (default: true)
    """
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        data = request.get_json(force=True) or {}
        pool_id = data.get("pool_id")
        amount_usd = float(data.get("amount_usd", 0))
        max_slippage_bps = int(data.get("max_slippage_bps", 50))
        wait_for_gas = data.get("wait_for_gas", True)
        
        if not pool_id:
            return jsonify({"error": "pool_id required"}), 400
        if amount_usd < 50:
            return jsonify({"error": "Minimum amount is $50"}), 400
        
        result = executor.enter_pool(
            pool_id=pool_id,
            amount_usd=amount_usd,
            max_slippage_bps=max_slippage_bps,
            wait_for_gas=wait_for_gas,
        )
        
        return jsonify(result.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/exit", methods=["POST"])
def api_executor_exit():
    """
    Exit a farming pool position.
    
    Request JSON:
        pool_id (str): Pool identifier
        percentage (float): Percentage to exit (default: 100)
        max_slippage_bps (int): Max slippage in basis points (default: 50)
    """
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        data = request.get_json(force=True) or {}
        pool_id = data.get("pool_id")
        percentage = float(data.get("percentage", 100))
        max_slippage_bps = int(data.get("max_slippage_bps", 50))
        
        if not pool_id:
            return jsonify({"error": "pool_id required"}), 400
        
        result = executor.exit_pool(
            pool_id=pool_id,
            percentage=percentage,
            max_slippage_bps=max_slippage_bps,
        )
        
        return jsonify(result.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/auto-enter", methods=["POST"])
def api_executor_auto_enter():
    """
    Automatically enter the best pools with given capital.
    
    This is the main endpoint for automated farming that
    outperforms manual strategies.
    
    Request JSON:
        capital_usd (float): Total capital to deploy
        max_pools (int): Maximum pools to enter (default: 5)
        risk_level (str): "low", "medium", or "high" (default: "medium")
    """
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        data = request.get_json(force=True) or {}
        capital_usd = float(data.get("capital_usd", 1000))
        max_pools = int(data.get("max_pools", 5))
        risk_level = data.get("risk_level", "medium")
        
        results = executor.enter_best_pools(
            capital_usd=capital_usd,
            max_pools=max_pools,
            risk_level=risk_level,
        )
        
        successful = sum(1 for r in results if r.status.value in ["success", "simulated"])
        total_deployed = sum(r.actual_amount_usd for r in results if r.status.value != "failed")
        
        return jsonify({
            "results": [r.to_dict() for r in results],
            "summary": {
                "pools_entered": successful,
                "total_deployed_usd": round(total_deployed, 2),
                "success_rate": round(successful / max(len(results), 1) * 100, 2),
            },
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/compound", methods=["POST"])
def api_executor_compound():
    """
    Auto-compound rewards for all active positions.
    
    Reinvests accrued rewards to maximize yield through
    compound interest.
    """
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        results = executor.auto_compound_all()
        
        total_compounded = sum(r.amount_usd for r in results if r.status.value != "failed")
        
        return jsonify({
            "results": [r.to_dict() for r in results],
            "total_compounded_usd": round(total_compounded, 2),
            "positions_compounded": len(results),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/positions")
def api_executor_positions():
    """Get all active pool positions."""
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        positions = executor.get_active_positions()
        
        return jsonify({
            "positions": positions,
            "count": len(positions),
            "total_value_usd": round(sum(p.get("entry_amount_usd", 0) for p in positions), 2),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/position/<pool_id>")
def api_executor_position(pool_id: str):
    """Get details for a specific position."""
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        position = executor.get_position(pool_id)
        if not position:
            return jsonify({"error": "Position not found"}), 404
        
        return jsonify(position)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/history")
def api_executor_history():
    """Get recent execution history."""
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        limit = int(request.args.get("limit", 50))
        history = executor.get_execution_history(limit=limit)
        
        return jsonify({
            "history": history,
            "count": len(history),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/executor/stats")
def api_executor_stats():
    """Get overall performance statistics."""
    try:
        from nexus.execution.pool_executor import get_pool_executor
        executor = get_pool_executor()
        
        stats = executor.get_performance_stats()
        
        return jsonify(stats)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pools/sources")
def api_pool_sources():
    """Get status of all pool data sources."""
    try:
        from nexus.protocols.pool_sources import get_pool_fetcher
        fetcher = get_pool_fetcher()
        
        # Trigger a fetch to update source status
        pools = fetcher.fetch_all_pools()
        source_status = fetcher.get_source_status()
        
        # Count pools by source
        pools_by_source = {}
        for p in pools:
            source = p.source
            pools_by_source[source] = pools_by_source.get(source, 0) + 1
        
        return jsonify({
            "sources": source_status,
            "pools_by_source": pools_by_source,
            "total_pools": len(pools),
            "active_sources": sum(1 for v in source_status.values() if v),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/pools/diagnostic")
def api_pool_diagnostic():
    """
    Comprehensive diagnostic for pool discovery issues.
    
    Returns detailed information about why pools might not be found,
    including chain connectivity, API status, and configuration.
    """
    try:
        from nexus.protocols.pool_sources import get_pool_fetcher, SUPPORTED_CHAINS
        from nexus.blockchain import BlockchainManager
        from nexus.utils.config import Config
        
        diagnostic = {
            "timestamp": time.time(),
            "issues": [],
            "warnings": [],
            "status": "ok",
        }
        
        # Check pool fetcher status
        fetcher = get_pool_fetcher()
        pools = fetcher.fetch_all_pools()
        source_status = fetcher.get_source_status()
        
        diagnostic["pool_sources"] = {
            "total_pools": len(pools),
            "sources": source_status,
            "active_sources": sum(1 for v in source_status.values() if v),
        }
        
        if len(pools) == 0:
            diagnostic["issues"].append({
                "severity": "critical",
                "message": "No pools fetched from any source",
                "suggestion": "Check network connectivity and API access to DeFi Llama, Curve, etc.",
            })
            diagnostic["status"] = "error"
        elif sum(1 for v in source_status.values() if v) == 0:
            diagnostic["warnings"].append({
                "message": "All pool sources reported failure",
                "suggestion": "APIs may be rate-limiting or unreachable. Using cached data if available.",
            })
        
        # Check blockchain connections
        try:
            bm = BlockchainManager()
            connected = bm.connected_chains()
            diagnostic["blockchain"] = {
                "connected_chains": connected,
                "total_connected": len(connected),
            }
            
            if not connected:
                diagnostic["issues"].append({
                    "severity": "high",
                    "message": "No blockchain connections established",
                    "suggestion": "Check RPC URLs in environment variables (ETH_RPC_URL, etc.) and ensure they're accessible.",
                })
                diagnostic["status"] = "degraded" if diagnostic["status"] != "error" else "error"
        except Exception as bm_exc:
            diagnostic["blockchain"] = {"error": str(bm_exc)}
            diagnostic["warnings"].append({
                "message": f"Could not check blockchain status: {bm_exc}",
            })
        
        # Check chain configuration
        chains_config = {
            "ethereum": Config.CHAIN_ETH,
            "bsc": Config.CHAIN_BSC,
            "polygon": Config.CHAIN_POLYGON,
            "arbitrum": Config.CHAIN_ARBITRUM,
            "optimism": Config.CHAIN_OPTIMISM,
            "base": Config.CHAIN_BASE,
            "avalanche": Config.CHAIN_AVALANCHE,
            "fantom": Config.CHAIN_FANTOM,
            "gnosis": Config.CHAIN_GNOSIS,
        }
        enabled_chains = [c for c, enabled in chains_config.items() if enabled]
        diagnostic["chain_config"] = {
            "enabled": enabled_chains,
            "disabled": [c for c, enabled in chains_config.items() if not enabled],
        }
        
        if len(enabled_chains) < 3:
            diagnostic["warnings"].append({
                "message": f"Only {len(enabled_chains)} chains enabled",
                "suggestion": "Enable more chains (CHAIN_ARBITRUM=true, etc.) to see more pools.",
            })
        
        # Count pools by chain
        pools_by_chain = {}
        for p in pools:
            pools_by_chain[p.chain] = pools_by_chain.get(p.chain, 0) + 1
        diagnostic["pools_by_chain"] = pools_by_chain
        
        # Check if pools exist but can't be discovered due to chain config
        pools_on_disabled_chains = sum(
            count for chain, count in pools_by_chain.items()
            if chain not in enabled_chains
        )
        if pools_on_disabled_chains > 0:
            diagnostic["warnings"].append({
                "message": f"{pools_on_disabled_chains} pools on disabled chains",
                "suggestion": "Enable additional chains to access these pools.",
            })
        
        return jsonify(diagnostic)
    except Exception as exc:
        return jsonify({
            "status": "error",
            "error": str(exc),
            "issues": [{"severity": "critical", "message": str(exc)}],
        }), 500

@app.route("/api/blockchain/latency")
def api_blockchain_latency():
    """Get RPC endpoint latency metrics for low-latency connection monitoring."""
    try:
        from nexus.execution.low_latency import get_low_latency_manager
        ll_manager = get_low_latency_manager()
        
        chain = request.args.get("chain")
        stats = ll_manager.get_endpoint_stats(chain=chain)
        
        return jsonify({
            "endpoints": stats,
            "manager_status": ll_manager.status(),
        })
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


# ── Efficiency API (v2 upgrade) ───────────────────────────────

@app.route("/api/efficiency")
def api_efficiency():
    """
    Return comprehensive efficiency metrics for the bot.
    
    Includes:
      - Deferred trades and gas savings
      - ML model accuracy
      - Strategy performance by regime
      - Scheduler queue status
    """
    try:
        agent = get_agent()
        status = agent.status()
        brain_status = status.get("brain", {})
        efficiency = status.get("efficiency", {})
        monitor_status = status.get("monitor", {})
        
        # Calculate profitability metrics
        uptime_hours = status.get("uptime_seconds", 0) / 3600
        rewards = status.get("rewards", {})
        profit = rewards.get("estimated_total_profit_usd", 0)
        profit_per_hour = profit / max(0.1, uptime_hours)
        
        return jsonify({
            "profitability": {
                "total_profit_usd": round(profit, 4),
                "profit_per_hour_usd": round(profit_per_hour, 4),
                "uptime_hours": round(uptime_hours, 2),
                "total_trades": rewards.get("total_trades", 0),
                "successful_trades": rewards.get("successful_trades", 0),
                "win_rate_pct": round(
                    rewards.get("successful_trades", 0) / max(1, rewards.get("total_trades", 0)) * 100, 2
                ),
            },
            "gas_optimization": {
                "deferred_trades": efficiency.get("deferred_trades", 0),
                "estimated_gas_savings_usd": efficiency.get("estimated_gas_savings_usd", 0),
                "scheduler_queue_size": efficiency.get("scheduler_queue_size", 0),
                "scheduler_submitted": efficiency.get("scheduler_submitted", 0),
                "scheduler_expired": efficiency.get("scheduler_expired", 0),
            },
            "ml_performance": brain_status.get("ml_accuracy", {}),
            "market_regime": {
                "current_regime": efficiency.get("current_regime", "unknown"),
                "strategy_weights": efficiency.get("strategy_weights", {}),
            },
            "opportunity_quality": {
                "fresh_opportunities": monitor_status.get("fresh_opportunities", 0),
                "stale_skipped": monitor_status.get("stale_skipped", 0),
                "staleness_threshold_sec": monitor_status.get("staleness_threshold_sec", 30),
            },
            "strategy_performance": monitor_status.get("strategy_performance", {}),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/efficiency/summary")
def api_efficiency_summary():
    """Return a concise efficiency summary for quick monitoring."""
    try:
        agent = get_agent()
        status = agent.status()
        rewards = status.get("rewards", {})
        efficiency = status.get("efficiency", {})
        brain = status.get("brain", {})
        ml_acc = brain.get("ml_accuracy", {})
        
        uptime = status.get("uptime_seconds", 0)
        profit = rewards.get("estimated_total_profit_usd", 0)
        uptime_hours = uptime / 3600
        
        return jsonify({
            "profit_usd": round(profit, 4),
            # Require at least 30 min uptime for meaningful hourly rate
            "profit_per_hour": round(profit / uptime_hours, 4) if uptime_hours >= 0.5 else 0.0,
            "win_rate_pct": round(
                rewards.get("successful_trades", 0) / max(1, rewards.get("total_trades", 0)) * 100, 2
            ),
            "ml_accuracy_pct": ml_acc.get("accuracy_pct", 0),
            "regime": efficiency.get("current_regime", "unknown"),
            "gas_savings_usd": efficiency.get("estimated_gas_savings_usd", 0),
            "deferred_trades": efficiency.get("deferred_trades", 0),
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


@socketio.on("request_efficiency")
def ws_request_efficiency():
    """Push efficiency metrics to the requesting client."""
    _push_efficiency()


def _push_status():
    try:
        agent = get_agent()
        emit("status_update", agent.status())
        emit("opportunities_update", agent.get_opportunities(limit=10))
        emit("trades_update", agent.get_recent_trades(limit=10))
        emit("payout_update", agent.payout.status())
    except Exception as exc:
        logger.debug("WebSocket push failed: %s", exc)


def _push_efficiency():
    """Push efficiency metrics via WebSocket."""
    try:
        agent = get_agent()
        status = agent.status()
        efficiency = status.get("efficiency", {})
        brain = status.get("brain", {})
        rewards = status.get("rewards", {})
        
        emit("efficiency_update", {
            "profit_usd": rewards.get("estimated_total_profit_usd", 0),
            "ml_accuracy": brain.get("ml_accuracy", {}),
            "regime": efficiency.get("current_regime", "unknown"),
            "strategy_weights": efficiency.get("strategy_weights", {}),
            "gas_savings": efficiency.get("estimated_gas_savings_usd", 0),
            "deferred_trades": efficiency.get("deferred_trades", 0),
        })
    except Exception as exc:
        logger.debug("WebSocket efficiency push failed: %s", exc)


def _background_pusher():
    """Periodically push updates to all connected WebSocket clients."""
    push_count = 0
    while True:
        time.sleep(Config.SCAN_INTERVAL_SECONDS)
        push_count += 1
        try:
            agent = get_agent()
            status = agent.status()
            socketio.emit("status_update", status)
            socketio.emit("opportunities_update", agent.get_opportunities(limit=10))
            socketio.emit("trades_update", agent.get_recent_trades(limit=10))
            socketio.emit("payout_update", agent.payout.status())
            
            # Push efficiency updates every 3rd cycle
            if push_count % 3 == 0:
                efficiency = status.get("efficiency", {})
                brain = status.get("brain", {})
                rewards = status.get("rewards", {})
                socketio.emit("efficiency_update", {
                    "profit_usd": rewards.get("estimated_total_profit_usd", 0),
                    "ml_accuracy": brain.get("ml_accuracy", {}),
                    "regime": efficiency.get("current_regime", "unknown"),
                    "strategy_weights": efficiency.get("strategy_weights", {}),
                    "gas_savings": efficiency.get("estimated_gas_savings_usd", 0),
                    "deferred_trades": efficiency.get("deferred_trades", 0),
                })
        except Exception:
            pass


# Start background pusher thread
_pusher_thread = threading.Thread(target=_background_pusher, daemon=True)
_pusher_thread.start()


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False)
