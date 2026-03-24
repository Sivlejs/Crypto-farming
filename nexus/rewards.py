"""
Reward Tracker for Nexus AI.

Persists executed trades and accumulated rewards to a SQLite database
and provides aggregated statistics for the dashboard.
"""
from __future__ import annotations

import time
from typing import List, Optional

from sqlalchemy import create_engine, Column, String, Float, Boolean, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from nexus.strategies.base import Opportunity, OpportunityType
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = "nexus_rewards.db"


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(Float, nullable=False)
    opp_type = Column(String(32), nullable=False)
    chain = Column(String(32), nullable=False)
    description = Column(Text, nullable=False)
    estimated_profit_usd = Column(Float, nullable=False)
    actual_profit_usd = Column(Float, nullable=True)
    confidence = Column(Float, nullable=False)
    tx_hash = Column(String(128), nullable=True)
    dry_run = Column(Boolean, nullable=False, default=True)
    success = Column(Boolean, nullable=False, default=False)


class RewardTracker:
    """Stores and queries trade history and reward totals."""

    def __init__(self, db_path: str = DB_PATH):
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine)
        logger.info("RewardTracker initialised (%s)", db_path)

    # ── Write ─────────────────────────────────────────────────

    def record(
        self,
        opp: Opportunity,
        tx_hash: Optional[str],
        actual_profit_usd: Optional[float],
        dry_run: bool,
    ):
        success = tx_hash is not None
        with Session(self._engine) as session:
            record = TradeRecord(
                timestamp=opp.timestamp,
                opp_type=opp.type.value,
                chain=opp.chain,
                description=opp.description,
                estimated_profit_usd=opp.estimated_profit_usd,
                actual_profit_usd=actual_profit_usd,
                confidence=opp.confidence,
                tx_hash=tx_hash,
                dry_run=dry_run,
                success=success,
            )
            session.add(record)
            session.commit()
            logger.info(
                "Trade recorded: %s | profit=$%.4f | tx=%s",
                opp.description,
                opp.estimated_profit_usd,
                tx_hash,
            )

    # ── Read ──────────────────────────────────────────────────

    def get_recent_trades(self, limit: int = 50) -> List[dict]:
        with Session(self._engine) as session:
            records = (
                session.query(TradeRecord)
                .order_by(TradeRecord.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [self._to_dict(r) for r in records]

    def get_stats(self) -> dict:
        with Session(self._engine) as session:
            total = session.query(TradeRecord).count()
            successful = session.query(TradeRecord).filter(TradeRecord.success == True).count()

            total_estimated = (
                session.query(TradeRecord)
                .filter(TradeRecord.success == True)
                .with_entities(TradeRecord.estimated_profit_usd)
                .all()
            )
            estimated_sum = sum(r[0] for r in total_estimated if r[0])

            actual_profits = (
                session.query(TradeRecord)
                .filter(
                    TradeRecord.success == True,
                    TradeRecord.actual_profit_usd.isnot(None),
                )
                .with_entities(TradeRecord.actual_profit_usd)
                .all()
            )
            actual_sum = sum(r[0] for r in actual_profits if r[0])

            by_type: dict[str, int] = {}
            for opp_type in OpportunityType:
                count = (
                    session.query(TradeRecord)
                    .filter(TradeRecord.opp_type == opp_type.value)
                    .count()
                )
                by_type[opp_type.value] = count

            return {
                "total_trades": total,
                "successful_trades": successful,
                "estimated_total_profit_usd": round(estimated_sum, 4),
                "actual_total_profit_usd": round(actual_sum, 4),
                "by_type": by_type,
            }

    @staticmethod
    def _to_dict(r: TradeRecord) -> dict:
        return {
            "id": r.id,
            "timestamp": r.timestamp,
            "opp_type": r.opp_type,
            "chain": r.chain,
            "description": r.description,
            "estimated_profit_usd": r.estimated_profit_usd,
            "actual_profit_usd": r.actual_profit_usd,
            "confidence": r.confidence,
            "tx_hash": r.tx_hash,
            "dry_run": r.dry_run,
            "success": r.success,
        }
