"""
Yield Farming Strategy for Nexus AI.

Scans DeFi protocols for the highest APY opportunities and
recommends (or executes) fund reallocation to maximise yield.
"""
from __future__ import annotations

from typing import List

from nexus.protocols.dex_aggregator import PriceAggregator
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum TVL filter – ignore tiny pools
MIN_TVL_USD = 500_000
# Minimum APY to flag as an opportunity
MIN_APY = 5.0
# Extra confidence when a high reward APY is on top of base APY
REWARD_CONFIDENCE_BOOST = 0.1

# Chain name aliases from DeFi Llama -> our chain keys
CHAIN_ALIASES = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
}


class YieldFarmingStrategy(BaseStrategy):
    """Finds high-APY yield farming opportunities via DeFi Llama."""

    name = "yield_farming"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []

        try:
            pools = PriceAggregator.get_yield_rates()
        except Exception as exc:
            logger.warning("Could not fetch yield rates: %s", exc)
            return []

        connected = set(self.bm.connected_chains())

        for pool in pools:
            raw_chain = pool.get("chain", "").lower()
            our_chain = CHAIN_ALIASES.get(raw_chain)
            if our_chain not in connected:
                continue

            apy = pool.get("apy", 0)
            tvl = pool.get("tvl_usd", 0)

            if apy < MIN_APY or tvl < MIN_TVL_USD:
                continue

            # Confidence: higher TVL and lower reward-portion = more stable
            apy_base = pool.get("apy_base", 0) or 0
            apy_reward = pool.get("apy_reward", 0) or 0
            reward_ratio = apy_reward / apy if apy > 0 else 0

            base_confidence = min(0.9, tvl / 10_000_000)  # caps at 0.9 for $10M TVL
            reward_penalty = reward_ratio * 0.3  # reward tokens are more volatile
            confidence = max(0.1, base_confidence - reward_penalty)

            # Estimated monthly profit on MAX_TRADE_USD
            monthly_profit = Config.MAX_TRADE_USD * (apy / 100) / 12

            if monthly_profit < Config.MIN_PROFIT_USD:
                continue

            opp = self._make_opportunity(
                opp_type=OpportunityType.YIELD_FARMING,
                chain=our_chain,
                description=(
                    f"{pool['protocol']} – {pool['symbol']} "
                    f"({apy:.1f}% APY, ${tvl:,.0f} TVL)"
                ),
                profit_usd=monthly_profit,
                confidence=confidence,
                details={
                    "pool_id": pool.get("pool_id", ""),
                    "protocol": pool.get("protocol", ""),
                    "symbol": pool.get("symbol", ""),
                    "apy": apy,
                    "apy_base": apy_base,
                    "apy_reward": apy_reward,
                    "tvl_usd": tvl,
                    "monthly_profit_est": round(monthly_profit, 2),
                },
            )
            opportunities.append(opp)

        # Sort by score descending, return top 10
        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities[:10]
