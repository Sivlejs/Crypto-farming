"""Perpetuals Funding Rate Arbitrage — earn the funding rate delta on GMX/dYdX."""
from __future__ import annotations
from typing import List
import requests
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.feeds.price_feed import get_price_feed
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

FUNDING_SOURCES = [
    {"name":"GMX ETH","symbol":"ETH","url":"https://api.gmx.io/funding_rates","rate_key":"ETH","fallback":0.0001},
    {"name":"GMX BTC","symbol":"BTC","url":"https://api.gmx.io/funding_rates","rate_key":"BTC","fallback":0.00008},
]
POSITION_SIZE_USD = 5000
MIN_FUNDING_APR   = 30   # % annual to bother

class PerpFundingStrategy(BaseStrategy):
    name = "perp_funding"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        feed   = get_price_feed()
        for src in FUNDING_SOURCES:
            try:
                rate_8h = self._get_rate(src)
                apr     = rate_8h * 3 * 365 * 100
                if abs(apr) < MIN_FUNDING_APR:
                    continue
                direction  = "short" if rate_8h > 0 else "long"
                daily_earn = POSITION_SIZE_USD * abs(rate_8h) * 3
                annual_usd = daily_earn * 365
                gas_cost   = 8.0
                net_daily  = daily_earn - gas_cost / 365
                if net_daily < Config.MIN_PROFIT_USD / 10:
                    continue
                opps.append(self._make_opportunity(
                    opp_type=OpportunityType.ARBITRAGE,
                    chain="arbitrum",
                    description=f"⚡ Perp funding {src['name']}: {direction} @ {apr:.1f}% APR",
                    profit_usd=net_daily,
                    confidence=0.70,
                    details={"strategy":"perp_funding","symbol":src["symbol"],
                             "direction":direction,"rate_8h":rate_8h,"apr":apr,
                             "position_usd":POSITION_SIZE_USD},
                ))
            except Exception as e:
                logger.debug("perp_funding %s: %s", src["name"], e)
        return opps

    def _get_rate(self, src: dict) -> float:
        try:
            r = requests.get(src["url"], timeout=5)
            r.raise_for_status()
            data = r.json()
            return float(data.get(src["rate_key"], src["fallback"]))
        except Exception:
            return src["fallback"]
