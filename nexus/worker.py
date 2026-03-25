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

from nexus.agent import get_agent
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger("worker")


def main():
    logger.info("=" * 60)
    logger.info("  Nexus AI Worker starting — DRY_RUN=%s", Config.DRY_RUN)
    logger.info("=" * 60)

    try:
        agent = get_agent()
    except Exception as exc:
        logger.error("Failed to initialize agent: %s\nFull traceback:\n%s", exc, traceback.format_exc())
        raise

    # ── Graceful shutdown ─────────────────────────────────────
    def _stop(sig, _frame):
        logger.info("Signal %s received — stopping agent…", sig)
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    # ── Start the agent ───────────────────────────────────────
    agent.start()
    logger.info("Agent running. Press Ctrl+C to stop.")

    # Keep the worker alive
    try:
        while True:
            time.sleep(30)
            stats = agent.tracker.get_stats()
            brain = agent.brain.status()
            logger.info(
                "Heartbeat | trades=%d wins=%d profit=$%.4f | "
                "ML=%s regime=%s | scans=%d",
                stats.get("total_trades", 0),
                stats.get("successful_trades", 0),
                stats.get("estimated_total_profit_usd", 0),
                "ON" if brain["ml_active"] else f"OFF (need {brain['trades_until_ml']} more)",
                brain["market_regime"]["regime"],
                (agent.monitor.status() or {}).get("scan_count", 0),
            )
    except KeyboardInterrupt:
        agent.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.error("Worker crashed: %s\nFull traceback:\n%s", exc, traceback.format_exc())
        sys.exit(1)
