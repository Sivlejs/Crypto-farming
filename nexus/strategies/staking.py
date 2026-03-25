"""
Liquid Staking Optimizer Strategy for Nexus AI.
Scans Lido, Rocket Pool, Frax, Binance staking, and Ankr for the best staking yields.
"""
from __future__ import annotations

import requests
from typing import List

from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

MIN_STAKING_APY = 3.0  # percent
CAPITAL_USD = 10_000.0  # notional capital for profit estimation

STAKING_PROTOCOLS: list[dict] = [
    {"name": "Lido",         "token": "stETH",   "chain": "ethereum", "apy": 4.0},
    {"name": "Rocket Pool",  "token": "rETH",    "chain": "ethereum", "apy": 3.8},
    {"name": "Frax",         "token": "sfrxETH", "chain": "ethereum", "apy": 4.3},
    {"name": "Binance",      "token": "WBNB",    "chain": "bsc",      "apy": 3.5},
    {"name": "Ankr",         "token": "ankrETH", "chain": "ethereum", "apy": 3.2},
]

DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"


class StakingStrategy(BaseStrategy):
    """Liquid staking yield optimizer."""

    name = "staking"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []
        live_apys = self._fetch_live_apys()
        
        connected = set(self.bm.connected_chains()) if self.bm else set()
        
        # Log once if no chains are connected
        if not connected:
            logger.warning(
                "[staking] No blockchain connections available. Using default APYs."
            )

        for proto in STAKING_PROTOCOLS:
            try:
                apy = live_apys.get(proto["token"], proto["apy"])
                if apy < MIN_STAKING_APY:
                    logger.debug("[staking] %s APY %.2f%% below min, skipping", proto["name"], apy)
                    continue

                chain = proto["chain"]
                chain_connected = chain in connected
                
                annual_profit = CAPITAL_USD * (apy / 100)
                daily_profit = annual_profit / 365
                confidence = min(0.9, apy / 10)
                
                # Reduce confidence if chain not connected
                if not chain_connected:
                    confidence *= 0.5

                logger.info("[staking] %s %s APY=%.2f%% daily_profit=$%.4f",
                            chain, proto["name"], apy, daily_profit)

                if daily_profit >= Config.MIN_PROFIT_USD:
                    desc_prefix = "" if chain_connected else "[Not Connected] "
                    opp = self._make_opportunity(
                        opp_type=OpportunityType.YIELD_FARMING,
                        chain=chain,
                        description=f"{desc_prefix}Stake via {proto['name']} for {apy:.2f}% APY ({proto['token']})",
                        profit_usd=daily_profit,
                        confidence=confidence,
                        details={
                            "protocol": proto["name"],
                            "token": proto["token"],
                            "apy_pct": round(apy, 4),
                            "capital_usd": CAPITAL_USD,
                            "annual_profit_usd": round(annual_profit, 2),
                            "chain_connected": chain_connected,
                        },
                    )
                    opportunities.append(opp)
            except Exception as exc:
                logger.warning("[staking] Error processing %s: %s", proto["name"], exc)

        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities

    def _fetch_live_apys(self) -> dict[str, float]:
        """Pull live APYs from DeFi Llama; fall back to defaults on error."""
        result: dict[str, float] = {}
        try:
            resp = requests.get(DEFILLAMA_POOLS_URL, timeout=10)
            resp.raise_for_status()
            pools = resp.json().get("data", [])
            target_symbols = {p["token"] for p in STAKING_PROTOCOLS}
            for pool in pools:
                sym = pool.get("symbol", "")
                # Compute APY: use explicit value or sum of base + reward
                apy_base = float(pool.get("apyBase") or 0)
                apy_reward = float(pool.get("apyReward") or 0)
                apy = pool.get("apy")
                if apy is not None and apy > 0:
                    apy = float(apy)
                else:
                    apy = apy_base + apy_reward
                
                if sym in target_symbols and apy > 0:
                    result[sym] = apy
        except Exception as exc:
            logger.warning("[staking] DeFi Llama fetch failed: %s", exc)
        return result
