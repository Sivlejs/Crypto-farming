"""
Nexus AI Background Worker.

This process runs the actual farming bot — blockchain scanning,
opportunity detection, trade execution, and learning.

On Render, this runs as a separate "worker" service alongside
the web dashboard service, giving the bot its own dedicated
CPU/RAM at full capacity.

On a VPS, start this with: python nexus/worker.py
Or use the provided systemd service: scripts/nexus-worker.service
"""
from __future__ import annotations

import os
import signal
import sys
import time
import traceback

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger("worker")

# Maximum retry attempts for agent initialization
MAX_INIT_RETRIES = 5
RETRY_DELAY_SECONDS = 10


def main():
    logger.info("=" * 60)
    logger.info("  Nexus AI Worker starting — DRY_RUN=%s", Config.DRY_RUN)
    logger.info("=" * 60)

    # Try to initialize agent with retries
    agent = None
    last_error = None
    for attempt in range(1, MAX_INIT_RETRIES + 1):
        try:
            from nexus.agent import get_agent
            agent = get_agent()
            logger.info("Agent initialized successfully on attempt %d", attempt)
            break
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Agent initialization attempt %d/%d failed: %s",
                attempt, MAX_INIT_RETRIES, exc
            )
            if attempt < MAX_INIT_RETRIES:
                logger.info("Retrying in %d seconds...", RETRY_DELAY_SECONDS)
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                logger.error(
                    "All initialization attempts failed. Last error:\n%s",
                    traceback.format_exc()
                )

    if agent is None:
        logger.error("Failed to initialize agent after all retries: %s", last_error)
        sys.exit(1)

    # ── Graceful shutdown ─────────────────────────────────────
    def _stop(sig, _frame):
        logger.info("Signal %s received — stopping agent…", sig)
        try:
            agent.stop()
        except Exception as exc:
            logger.warning("Error during shutdown: %s", exc)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    # ── Start the agent ───────────────────────────────────────
    try:
        agent.start()
    except Exception as exc:
        logger.warning("Agent start encountered error (continuing): %s", exc)
    
    chains = agent.blockchain.connected_chains()
    logger.info(
        "Agent running with %d chains: %s. Press Ctrl+C to stop.",
        len(chains), chains or "none yet (will retry)"
    )

    # Keep the worker alive with heartbeat logging
    heartbeat_count = 0
    try:
        while True:
            time.sleep(30)
            heartbeat_count += 1
            try:
                stats = agent.tracker.get_stats()
                brain_status = agent.brain.status()
                monitor_status = agent.monitor.status() or {}
                chains_now = agent.blockchain.connected_chains()
                efficiency = agent.status().get("efficiency", {})
                ml_accuracy = brain_status.get("ml_accuracy", {})
                
                # Calculate hourly profit rate
                uptime_hours = (time.time() - agent._start_time) / 3600 if agent._start_time else 0
                profit_per_hour = stats.get("estimated_total_profit_usd", 0) / max(0.1, uptime_hours)
                
                # Standard heartbeat every 30 seconds
                logger.info(
                    "Heartbeat #%d | trades=%d wins=%d profit=$%.4f ($/hr=$%.4f) | "
                    "ML=%s accuracy=%.1f%% regime=%s | scans=%d fresh=%d | chains=%d | deferred=%d",
                    heartbeat_count,
                    stats.get("total_trades", 0),
                    stats.get("successful_trades", 0),
                    stats.get("estimated_total_profit_usd", 0),
                    profit_per_hour,
                    "ON" if brain_status.get("ml_active") else f"OFF (need {brain_status.get('trades_until_ml', 30)} more)",
                    ml_accuracy.get("accuracy_pct", 0),
                    (brain_status.get("market_regime") or {}).get("regime", "unknown"),
                    monitor_status.get("scan_count", 0),
                    monitor_status.get("fresh_opportunities", 0),
                    len(chains_now),
                    efficiency.get("deferred_trades", 0),
                )
                
                # Detailed status every 5 minutes (10 heartbeats)
                if heartbeat_count % 10 == 0:
                    logger.info(
                        "Detailed Status | strategy_weights=%s | gas_savings=$%.4f | "
                        "scheduler_queue=%d submitted=%d expired=%d",
                        brain_status.get("market_regime", {}).get("strategy_weights", {}),
                        efficiency.get("estimated_gas_savings_usd", 0),
                        efficiency.get("scheduler_queue_size", 0),
                        efficiency.get("scheduler_submitted", 0),
                        efficiency.get("scheduler_expired", 0),
                    )
                    
            except Exception as exc:
                logger.debug("Heartbeat error (non-fatal): %s", exc)
                
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        agent.stop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass  # Normal exit
    except Exception as exc:
        logger.error("Worker crashed: %s\nFull traceback:\n%s", exc, traceback.format_exc())
        sys.exit(1)
