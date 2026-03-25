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

# Minimum TVL filter – ignore tiny pools (lowered to find more opportunities)
MIN_TVL_USD = 50_000
# Minimum APY to flag as an opportunity (lowered for realistic markets)
MIN_APY = 1.5
# Extra confidence when a high reward APY is on top of base APY
REWARD_CONFIDENCE_BOOST = 0.1

# Chain name aliases from DeFi Llama -> our chain keys
# Extended to support all major chains returned by DeFi Llama API
CHAIN_ALIASES = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
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
        
        # Log once if no chains are connected (common issue)
        if not connected:
            logger.warning(
                "No blockchain connections available. Pool discovery will still work, "
                "but execution will require chain connectivity. Check RPC settings."
            )

        for pool in pools:
            raw_chain = pool.get("chain", "").lower()
            our_chain = CHAIN_ALIASES.get(raw_chain)
            
            # Skip pools from completely unsupported chains (not in CHAIN_ALIASES)
            if not our_chain:
                continue
            
            # Track if this chain is connected for execution
            chain_connected = our_chain in connected

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
            
            # Reduce confidence if chain is not connected (can't execute)
            if not chain_connected:
                confidence *= 0.5  # Half confidence for disconnected chains

            # Estimated monthly profit on MAX_TRADE_USD
            monthly_profit = Config.MAX_TRADE_USD * (apy / 100) / 12

            if monthly_profit < Config.MIN_PROFIT_USD:
                continue

            # Build description with connection status
            desc_prefix = "" if chain_connected else "[Not Connected] "
            
            opp = self._make_opportunity(
                opp_type=OpportunityType.YIELD_FARMING,
                chain=our_chain,
                description=(
                    f"{desc_prefix}{pool['protocol']} – {pool['symbol']} "
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
                    "chain_connected": chain_connected,
                },
            )
            opportunities.append(opp)

        # Sort by score descending, return top 10
        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities[:10]
