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
    try:
        while True:
            time.sleep(30)
            try:
                stats = agent.tracker.get_stats()
                brain_status = agent.brain.status()
                monitor_status = agent.monitor.status() or {}
                chains_now = agent.blockchain.connected_chains()
                
                logger.info(
                    "Heartbeat | trades=%d wins=%d profit=$%.4f | "
                    "ML=%s regime=%s | scans=%d | chains=%d",
                    stats.get("total_trades", 0),
                    stats.get("successful_trades", 0),
                    stats.get("estimated_total_profit_usd", 0),
                    "ON" if brain_status.get("ml_active") else f"OFF (need {brain_status.get('trades_until_ml', 30)} more)",
                    (brain_status.get("market_regime") or {}).get("regime", "unknown"),
                    monitor_status.get("scan_count", 0),
                    len(chains_now),
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
