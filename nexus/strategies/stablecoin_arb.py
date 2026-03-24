"""Stablecoin De-peg Arbitrage — profit when USDC/USDT/DAI deviate from $1.00."""
from __future__ import annotations
from typing import List
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.feeds.price_feed import get_price_feed
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

STABLES = ["USDC","USDT","DAI","FRAX","BUSD","LUSD","USDD"]
TRADE_USD = 20_000
GAS_COST  = 15.0
PEG       = 1.0

class StablecoinArbStrategy(BaseStrategy):
    name = "stablecoin_arb"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        feed   = get_price_feed()
        prices = feed.all_prices()
        for sym in STABLES:
            price = prices.get(sym)
            if not price:
                continue
            deviation = price - PEG
            if abs(deviation) < 0.003:   # < 0.3% — not worth it
                continue
            profit_usd = TRADE_USD * abs(deviation) - GAS_COST
            if profit_usd < Config.MIN_PROFIT_USD:
                continue
            direction = "buy" if deviation < 0 else "sell"
            confidence = min(0.85, abs(deviation) * 50)
            opps.append(self._make_opportunity(
                opp_type=OpportunityType.ARBITRAGE,
                chain="ethereum",
                description=f"💲 {sym} de-peg: ${price:.5f} ({direction}, dev={deviation:+.4f})",
                profit_usd=profit_usd,
                confidence=confidence,
                details={"strategy":"stablecoin_arb","symbol":sym,"price":price,
                         "deviation":deviation,"direction":direction,"trade_usd":TRADE_USD},
            ))
        opps.sort(key=lambda o: o.score(), reverse=True)
        return opps[:3]
