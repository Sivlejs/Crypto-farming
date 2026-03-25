"""Governance Token Farming — CRV/CVX/AURA/BAL veToken rewards."""
from __future__ import annotations
from typing import List
import requests
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

LLAMA_POOLS = "https://yields.llama.fi/pools"
GOV_PROJECTS = {"convex-finance","curve-dex","aura","balancer","frax","sushi","yearn-finance"}
CAPITAL   = 5_000
MIN_APY   = 8.0

# Chain name aliases
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
    "gnosis": "gnosis",
}

class GovernanceFarmingStrategy(BaseStrategy):
    name = "governance_farming"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        try:
            resp = requests.get(LLAMA_POOLS, timeout=10)
            resp.raise_for_status()
            pools = resp.json().get("data", [])
        except Exception as e:
            logger.debug("gov_farming: %s", e)
            return []

        connected = set(self.bm.connected_chains()) if self.bm else set()

        gov_pools = []
        for p in pools:
            if p.get("project","") not in GOV_PROJECTS:
                continue
            
            # Compute APYs properly
            base_apy = float(p.get("apyBase", 0) or 0)
            reward_apy = float(p.get("apyReward", 0) or 0)
            tvl = float(p.get("tvlUsd", 0) or 0)
            
            if reward_apy < MIN_APY or tvl < 100_000:
                continue
            
            gov_pools.append(p)
        
        gov_pools.sort(key=lambda p: float(p.get("apyReward", 0) or 0), reverse=True)

        for pool in gov_pools[:6]:
            try:
                base_apy   = float(pool.get("apyBase", 0) or 0)
                reward_apy = float(pool.get("apyReward", 0) or 0)
                total_apy  = base_apy + reward_apy
                daily      = CAPITAL * total_apy / 100 / 365
                if daily < Config.MIN_PROFIT_USD / 5:
                    continue
                
                raw_chain = pool.get("chain","").lower()
                chain = CHAIN_ALIASES.get(raw_chain, raw_chain) or "ethereum"
                chain_connected = chain in connected
                
                # Adjust confidence based on connection status
                confidence = 0.68
                if not chain_connected:
                    confidence *= 0.5
                
                desc_prefix = "" if chain_connected else "[Not Connected] "
                opps.append(self._make_opportunity(
                    opp_type=OpportunityType.YIELD_FARMING,
                    chain=chain,
                    description=f"{desc_prefix}🗳 {pool.get('project')} {pool.get('symbol','')}: {total_apy:.1f}% APY ({reward_apy:.1f}% gov rewards)",
                    profit_usd=daily,
                    confidence=confidence,
                    details={
                        "strategy": "governance_farming",
                        "project": pool.get("project"),
                        "symbol": pool.get("symbol"),
                        "base_apy": base_apy,
                        "reward_apy": reward_apy,
                        "total_apy": total_apy,
                        "chain_connected": chain_connected,
                    },
                ))
            except Exception:
                continue
        return opps[:4]
