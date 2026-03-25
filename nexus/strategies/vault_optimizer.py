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

        connected = set(self.bm.connected_chains()) if self.bm else set()

        filtered = []
        for p in pools:
            if p.get("project","") not in TARGET_PROJECTS:
                continue
            
            tvl = float(p.get("tvlUsd", 0) or 0)
            if tvl < MIN_TVL:
                continue
            
            # Compute APY properly: use explicit value or sum of base + reward
            apy_base = float(p.get("apyBase", 0) or 0)
            apy_reward = float(p.get("apyReward", 0) or 0)
            apy = p.get("apy")
            if apy is not None and apy > 0:
                apy = float(apy)
            else:
                apy = apy_base + apy_reward
            
            if apy < MIN_APY:
                continue
            
            # Store computed APY for sorting
            p["_computed_apy"] = apy
            filtered.append(p)
        
        filtered.sort(key=lambda p: p.get("_computed_apy", 0), reverse=True)

        for pool in filtered[:8]:
            try:
                apy = pool.get("_computed_apy", 0)
                apy_base = float(pool.get("apyBase", 0) or 0)
                apy_reward = float(pool.get("apyReward", 0) or 0)
                tvl = float(pool.get("tvlUsd", 0) or 0)
                
                daily_pct = apy / 365 / 100
                profit = CAPITAL * daily_pct
                if profit < Config.MIN_PROFIT_USD / 2:
                    continue
                
                raw_chain = pool.get("chain","").lower()
                chain = CHAIN_ALIASES.get(raw_chain, raw_chain)
                
                # Skip pools with unknown/unsupported chains
                if not chain:
                    logger.debug("[vault_optimizer] Skipping pool with unknown chain: %s", raw_chain)
                    continue
                
                chain_connected = chain in connected
                
                # Adjust confidence based on connection status
                confidence = 0.72
                if not chain_connected:
                    confidence *= 0.5
                
                desc_prefix = "" if chain_connected else "[Not Connected] "
                opps.append(self._make_opportunity(
                    opp_type=OpportunityType.YIELD_FARMING,
                    chain=chain,
                    description=f"{desc_prefix}🏦 {pool.get('project')} {pool.get('symbol','')}: {apy:.1f}% APY",
                    profit_usd=profit,
                    confidence=confidence,
                    details={
                        "strategy": "vault_optimizer",
                        "project": pool.get("project"),
                        "symbol": pool.get("symbol"),
                        "apy": apy,
                        "apy_base": apy_base,
                        "apy_reward": apy_reward,
                        "tvl": tvl,
                        "pool_id": pool.get("pool",""),
                        "chain_connected": chain_connected,
                    },
                ))
            except Exception:
                continue
        return opps[:5]
