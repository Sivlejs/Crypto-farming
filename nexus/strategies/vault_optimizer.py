"""Vault Yield Optimizer — finds highest APY across Yearn, Beefy, Convex, Curve."""
from __future__ import annotations
from typing import List
import requests
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

LLAMA_POOLS = "https://yields.llama.fi/pools"
TARGET_PROJECTS = {"yearn-finance","beefy-finance","convex-finance","curve-dex","balancer","aura"}
CAPITAL = 10_000
MIN_TVL = 500_000
MIN_APY = 5.0

class VaultOptimizerStrategy(BaseStrategy):
    name = "vault_optimizer"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        try:
            resp = requests.get(LLAMA_POOLS, timeout=10)
            resp.raise_for_status()
            pools = resp.json().get("data", [])
        except Exception as e:
            logger.debug("vault_optimizer llama fetch: %s", e)
            return []

        filtered = [
            p for p in pools
            if p.get("project","") in TARGET_PROJECTS
            and float(p.get("tvlUsd", 0)) >= MIN_TVL
            and float(p.get("apy", 0) or 0) >= MIN_APY
        ]
        filtered.sort(key=lambda p: float(p.get("apy", 0) or 0), reverse=True)

        for pool in filtered[:8]:
            try:
                apy       = float(pool.get("apy", 0) or 0)
                daily_pct = apy / 365 / 100
                profit    = CAPITAL * daily_pct
                if profit < Config.MIN_PROFIT_USD / 2:
                    continue
                chain = pool.get("chain","").lower()
                opps.append(self._make_opportunity(
                    opp_type=OpportunityType.YIELD_FARMING,
                    chain=chain or "ethereum",
                    description=f"🏦 {pool.get('project')} {pool.get('symbol','')}: {apy:.1f}% APY",
                    profit_usd=profit,
                    confidence=0.72,
                    details={"strategy":"vault_optimizer","project":pool.get("project"),
                             "symbol":pool.get("symbol"),"apy":apy,"tvl":pool.get("tvlUsd"),
                             "pool_id":pool.get("pool","")},
                ))
            except Exception:
                continue
        return opps[:5]
