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

        gov_pools = [
            p for p in pools
            if p.get("project","") in GOV_PROJECTS
            and float(p.get("apyReward", 0) or 0) >= MIN_APY
            and float(p.get("tvlUsd", 0)) >= 100_000
        ]
        gov_pools.sort(key=lambda p: float(p.get("apyReward", 0) or 0), reverse=True)

        for pool in gov_pools[:6]:
            try:
                base_apy   = float(pool.get("apyBase", 0) or 0)
                reward_apy = float(pool.get("apyReward", 0) or 0)
                total_apy  = base_apy + reward_apy
                daily      = CAPITAL * total_apy / 100 / 365
                if daily < Config.MIN_PROFIT_USD / 5:
                    continue
                chain = pool.get("chain","").lower()
                opps.append(self._make_opportunity(
                    opp_type=OpportunityType.YIELD_FARMING,
                    chain=chain or "ethereum",
                    description=f"🗳 {pool.get('project')} {pool.get('symbol','')}: {total_apy:.1f}% APY ({reward_apy:.1f}% gov rewards)",
                    profit_usd=daily,
                    confidence=0.68,
                    details={"strategy":"governance_farming","project":pool.get("project"),
                             "symbol":pool.get("symbol"),"base_apy":base_apy,
                             "reward_apy":reward_apy,"total_apy":total_apy},
                ))
            except Exception:
                continue
        return opps[:4]
