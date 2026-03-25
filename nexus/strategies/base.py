"""
Base strategy class and shared data structures for Nexus AI strategies.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class OpportunityType(str, Enum):
    ARBITRAGE = "arbitrage"
    YIELD_FARMING = "yield_farming"
    LIQUIDITY_MINING = "liquidity_mining"
    POW_MINING = "pow_mining"


@dataclass
class Opportunity:
    """Represents a discovered profit opportunity."""

    type: OpportunityType
    chain: str
    description: str
    estimated_profit_usd: float
    confidence: float          # 0.0 – 1.0
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    executed: bool = False
    tx_hash: Optional[str] = None

    def score(self) -> float:
        """Composite score used to rank opportunities."""
        return self.estimated_profit_usd * self.confidence

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "chain": self.chain,
            "description": self.description,
            "estimated_profit_usd": round(self.estimated_profit_usd, 4),
            "confidence": round(self.confidence, 4),
            "score": round(self.score(), 4),
            "details": self.details,
            "timestamp": self.timestamp,
            "executed": self.executed,
            "tx_hash": self.tx_hash,
        }


class BaseStrategy:
    """Abstract base class for all Nexus AI farming strategies."""

    name: str = "base"

    def __init__(self, blockchain_manager: Any, config: Any):
        self.bm = blockchain_manager
        self.config = config

    def find_opportunities(self) -> list[Opportunity]:
        """Scan for profitable opportunities. Override in subclasses."""
        raise NotImplementedError

    def _make_opportunity(
        self,
        opp_type: OpportunityType,
        chain: str,
        description: str,
        profit_usd: float,
        confidence: float,
        details: Optional[dict] = None,
    ) -> Opportunity:
        return Opportunity(
            type=opp_type,
            chain=chain,
            description=description,
            estimated_profit_usd=profit_usd,
            confidence=confidence,
            details=details or {},
        )
