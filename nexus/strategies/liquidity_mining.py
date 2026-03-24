"""
Liquidity Mining Strategy for Nexus AI.

Identifies the best liquidity pool positions where LP rewards
(trading fees + token incentives) exceed the opportunity cost.
Focuses on stablecoin and major-pair pools to minimise impermanent loss.
"""
from __future__ import annotations

from typing import List

from nexus.protocols.dex_aggregator import PriceAggregator
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Only consider pools where reward APY is a meaningful portion
MIN_REWARD_APY = 3.0
MIN_TVL_USD = 1_000_000

# Low-impermanent-loss pairs (stable or tightly correlated)
LOW_IL_KEYWORDS = {
    "usdc", "usdt", "dai", "busd", "frax", "mim",
    "wbtc-eth", "eth-steth", "matic-stmatic",
}

CHAIN_ALIASES = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
}


class LiquidityMiningStrategy(BaseStrategy):
    """Finds high-reward liquidity pool opportunities."""

    name = "liquidity_mining"

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

            apy_reward = pool.get("apy_reward") or 0
            apy_base = pool.get("apy_base") or 0
            tvl = pool.get("tvl_usd", 0)

            if apy_reward < MIN_REWARD_APY or tvl < MIN_TVL_USD:
                continue

            symbol_lower = pool.get("symbol", "").lower()
            is_low_il = any(kw in symbol_lower for kw in LOW_IL_KEYWORDS)

            # Higher confidence for stablecoin pairs
            base_confidence = 0.5 if is_low_il else 0.3
            tvl_boost = min(0.35, tvl / 20_000_000)
            confidence = min(0.95, base_confidence + tvl_boost)

            total_apy = apy_reward + apy_base
            monthly_profit = Config.MAX_TRADE_USD * (total_apy / 100) / 12

            if monthly_profit < Config.MIN_PROFIT_USD:
                continue

            opp = self._make_opportunity(
                opp_type=OpportunityType.LIQUIDITY_MINING,
                chain=our_chain,
                description=(
                    f"LP {pool['protocol']} – {pool['symbol']} "
                    f"({total_apy:.1f}% APY: {apy_base:.1f}% fees + "
                    f"{apy_reward:.1f}% rewards)"
                ),
                profit_usd=monthly_profit,
                confidence=confidence,
                details={
                    "pool_id": pool.get("pool_id", ""),
                    "protocol": pool.get("protocol", ""),
                    "symbol": pool.get("symbol", ""),
                    "apy_total": total_apy,
                    "apy_base": apy_base,
                    "apy_reward": apy_reward,
                    "tvl_usd": tvl,
                    "low_il": is_low_il,
                    "monthly_profit_est": round(monthly_profit, 2),
                },
            )
            opportunities.append(opp)

        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities[:10]
