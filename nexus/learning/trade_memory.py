"""
Trade Memory — persistent SQLite feature store for Nexus AI learning.

Every opportunity that is evaluated (and its eventual outcome) is stored
here so the ML models can train on real data.

Schema
------
opportunities
  id, timestamp, opp_type, chain, strategy,
  spread_pct, profit_usd, confidence, gas_gwei, time_hour,
  time_weekday, market_vol_eth, executed (0/1),
  success (0/1/NULL), actual_profit_usd (NULL until outcome known)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "trade_memory.db"
try:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except OSError as e:
    # On read-only filesystems or permission issues, fall back to /tmp
    logger.warning("Cannot create data directory %s: %s. Falling back to /tmp", DB_PATH.parent, e)
    DB_PATH = Path("/tmp/nexus_data/trade_memory.db")
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as fallback_error:
        logger.critical("Cannot create fallback data directory %s: %s", DB_PATH.parent, fallback_error)
        raise


class TradeMemory:
    """Thread-safe SQLite store for opportunity features and trade outcomes."""

    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open()
        self._create_schema()

    # ── Connection ────────────────────────────────────────────

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _create_schema(self):
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id              TEXT PRIMARY KEY,
                    timestamp       REAL,
                    opp_type        TEXT,
                    chain           TEXT,
                    strategy        TEXT,
                    spread_pct      REAL DEFAULT 0,
                    profit_usd      REAL DEFAULT 0,
                    confidence      REAL DEFAULT 0,
                    gas_gwei        REAL DEFAULT 0,
                    time_hour       INTEGER DEFAULT 0,
                    time_weekday    INTEGER DEFAULT 0,
                    market_vol      REAL DEFAULT 0,
                    extra_features  TEXT DEFAULT '{}',
                    executed        INTEGER DEFAULT 0,
                    success         INTEGER,
                    actual_profit   REAL
                );

                CREATE TABLE IF NOT EXISTS model_versions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL,
                    model_type  TEXT,
                    accuracy    REAL,
                    n_samples   INTEGER,
                    notes       TEXT
                );

                CREATE TABLE IF NOT EXISTS param_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       REAL,
                    param_name      TEXT,
                    old_value       REAL,
                    new_value       REAL,
                    reason          TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_opp_ts       ON opportunities(timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_opp_type     ON opportunities(opp_type);
                CREATE INDEX IF NOT EXISTS idx_opp_executed ON opportunities(executed);
            """)
            self._conn.commit()

    # ── Write API ─────────────────────────────────────────────

    def record_opportunity(self, opp: dict, market_vol: float = 0.0):
        """Store an opportunity and its initial features before execution."""
        details = opp.get("details", {}) or {}
        extra = {
            "borrow_usd":    details.get("borrow_usd", 0),
            "flash_provider":details.get("flash_provider", ""),
            "buy_dex":       details.get("buy_dex", ""),
            "sell_dex":      details.get("sell_dex", ""),
        }
        now = time.time()
        t   = time.localtime(now)
        row = (
            opp.get("id", ""),
            now,
            opp.get("type", ""),
            opp.get("chain", ""),
            details.get("strategy", opp.get("type", "")),
            float(details.get("spread_pct", 0) or 0),
            float(opp.get("estimated_profit_usd", 0) or 0),
            float(opp.get("confidence", 0) or 0),
            float(opp.get("gas_gwei", 0) or 0),
            t.tm_hour,
            t.tm_wday,
            float(market_vol or 0),
            json.dumps(extra),
        )
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO opportunities
                   (id,timestamp,opp_type,chain,strategy,spread_pct,profit_usd,
                    confidence,gas_gwei,time_hour,time_weekday,market_vol,extra_features)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            self._conn.commit()

    def record_outcome(self, opp_id: str, success: bool, actual_profit: float):
        """Update an opportunity with its real trade outcome."""
        with self._lock:
            self._conn.execute(
                """UPDATE opportunities
                   SET executed=1, success=?, actual_profit=?
                   WHERE id=?""",
                (1 if success else 0, actual_profit, opp_id),
            )
            self._conn.commit()

    def log_param_change(self, name: str, old: float, new: float, reason: str = ""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO param_history (timestamp,param_name,old_value,new_value,reason) VALUES (?,?,?,?,?)",
                (time.time(), name, old, new, reason),
            )
            self._conn.commit()

    def log_model_version(self, model_type: str, accuracy: float, n_samples: int, notes: str = ""):
        with self._lock:
            self._conn.execute(
                "INSERT INTO model_versions (timestamp,model_type,accuracy,n_samples,notes) VALUES (?,?,?,?,?)",
                (time.time(), model_type, accuracy, n_samples, notes),
            )
            self._conn.commit()

    # ── Read API ──────────────────────────────────────────────

    def get_training_data(self, limit: int = 2000) -> list[dict]:
        """Return the most recent executed opportunities for training."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM opportunities
                   WHERE executed=1 AND success IS NOT NULL
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_outcomes(self, n: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT opp_type,chain,strategy,profit_usd,actual_profit,success,timestamp
                   FROM opportunities WHERE executed=1
                   ORDER BY timestamp DESC LIMIT ?""",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
            exec_ = self._conn.execute("SELECT COUNT(*) FROM opportunities WHERE executed=1").fetchone()[0]
            wins  = self._conn.execute("SELECT COUNT(*) FROM opportunities WHERE success=1").fetchone()[0]
            best  = self._conn.execute("SELECT MAX(actual_profit) FROM opportunities WHERE success=1").fetchone()[0]
            total_p = self._conn.execute("SELECT SUM(actual_profit) FROM opportunities WHERE success=1").fetchone()[0]
            params  = self._conn.execute("SELECT * FROM param_history ORDER BY timestamp DESC LIMIT 10").fetchall()
            models  = self._conn.execute("SELECT * FROM model_versions ORDER BY timestamp DESC LIMIT 5").fetchall()
        return {
            "total_evaluated":  total,
            "total_executed":   exec_,
            "wins":             wins,
            "win_rate":         round(wins / exec_ * 100, 1) if exec_ > 0 else 0,
            "best_trade_usd":   round(best or 0, 4),
            "total_profit_usd": round(total_p or 0, 4),
            "param_changes":    [dict(r) for r in params],
            "model_history":    [dict(r) for r in models],
        }

    def count_for_training(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM opportunities WHERE executed=1 AND success IS NOT NULL"
            ).fetchone()[0]
