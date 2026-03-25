"""
Multi-Protocol Lending Rate Optimizer for Nexus AI.
Compares supply APYs across Aave V3, Compound V3, Venus, and Euler
using the DeFi Llama yields API to find the highest-yield lending pools.
"""
from __future__ import annotations

import requests
from typing import List

from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

DEFILLAMA_POOLS_URL = "https://yields.llama.fi/pools"
MIN_TVL_USD = 1_000_000
MIN_APY = 2.0
CAPITAL_USD = 10_000.0

TARGET_TOKENS = {"USDC", "USDT", "DAI", "WETH", "WBTC"}
TARGET_PROJECTS = {"aave-v3", "compound-v3", "venus", "euler"}

# Extended chain mapping for all major chains
CHAIN_MAP = {
    "Ethereum": "ethereum",
    "ethereum": "ethereum",
    "BSC": "bsc",
    "bsc": "bsc",
    "binance": "bsc",
    "Polygon": "polygon",
    "polygon": "polygon",
    "matic": "polygon",
    "Arbitrum": "arbitrum",
    "arbitrum": "arbitrum",
    "Optimism": "optimism",
    "optimism": "optimism",
    "Base": "base",
    "base": "base",
    "Avalanche": "avalanche",
    "avalanche": "avalanche",
    "avax": "avalanche",
}


class LendingStrategy(BaseStrategy):
    """Multi-protocol lending rate optimizer."""

    name = "lending"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []
        try:
            pools = self._fetch_pools()
        except Exception as exc:
            logger.error("[lending] Failed to fetch pools: %s", exc)
            return opportunities

        connected = set(self.bm.connected_chains()) if self.bm else set()

        # Group by token → find best APY per token
        best: dict[str, dict] = {}
        for pool in pools:
            project = pool.get("project", "").lower()
            symbol = pool.get("symbol", "").upper()
            tvl = pool.get("tvlUsd") or 0.0
            chain_raw = pool.get("chain", "")

            if project not in TARGET_PROJECTS:
                continue
            if symbol not in TARGET_TOKENS:
                continue
            if tvl < MIN_TVL_USD:
                continue
            
            # Compute APY: use explicit value or sum of base + reward
            apy_base = float(pool.get("apyBase") or 0)
            apy_reward = float(pool.get("apyReward") or 0)
            apy = pool.get("apy")
            if apy is not None and apy > 0:
                apy = float(apy)
            else:
                apy = apy_base + apy_reward
            
            if apy < MIN_APY:
                continue

            chain = CHAIN_MAP.get(chain_raw, chain_raw.lower())
            key = symbol
            if key not in best or apy > best[key]["apy"]:
                best[key] = {
                    "symbol": symbol,
                    "project": project,
                    "apy": apy,
                    "apy_base": apy_base,
                    "apy_reward": apy_reward,
                    "tvl": float(tvl),
                    "chain": chain,
                    "chain_connected": chain in connected,
                }

        # Also find worst (current) APY per token to compute delta
        worst: dict[str, float] = {}
        for pool in pools:
            symbol = pool.get("symbol", "").upper()
            project = pool.get("project", "").lower()
            tvl = pool.get("tvlUsd") or 0.0
            
            # Compute APY same way
            apy_base = float(pool.get("apyBase") or 0)
            apy_reward = float(pool.get("apyReward") or 0)
            apy = pool.get("apy")
            if apy is not None and apy > 0:
                apy = float(apy)
            else:
                apy = apy_base + apy_reward
            
            if symbol in TARGET_TOKENS and project in TARGET_PROJECTS and tvl >= MIN_TVL_USD and apy > 0:
                if symbol not in worst or apy < worst[symbol]:
                    worst[symbol] = apy

        for symbol, info in best.items():
            try:
                current_apy = worst.get(symbol, info["apy"] * 0.5)
                apy_delta = info["apy"] - current_apy
                if apy_delta <= 0:
                    continue

                annual_profit = CAPITAL_USD * (apy_delta / 100)
                daily_profit = annual_profit / 365
                confidence = min(0.88, info["apy"] / 20)
                
                # Reduce confidence if chain not connected
                if not info["chain_connected"]:
                    confidence *= 0.5

                logger.info("[lending] %s best=%s %.2f%% (delta +%.2f%%) daily=$%.4f",
                            symbol, info["project"], info["apy"], apy_delta, daily_profit)

                if daily_profit >= Config.MIN_PROFIT_USD:
                    desc_prefix = "" if info["chain_connected"] else "[Not Connected] "
                    opp = self._make_opportunity(
                        opp_type=OpportunityType.YIELD_FARMING,
                        chain=info["chain"],
                        description=(
                            f"{desc_prefix}Lend {symbol} on {info['project']} at {info['apy']:.2f}% APY "
                            f"(+{apy_delta:.2f}% vs current best)"
                        ),
                        profit_usd=daily_profit,
                        confidence=confidence,
                        details={
                            "token": symbol,
                            "protocol": info["project"],
                            "best_apy_pct": round(info["apy"], 4),
                            "apy_base_pct": round(info["apy_base"], 4),
                            "apy_reward_pct": round(info["apy_reward"], 4),
                            "current_apy_pct": round(current_apy, 4),
                            "apy_delta_pct": round(apy_delta, 4),
                            "tvl_usd": round(info["tvl"], 0),
                            "capital_usd": CAPITAL_USD,
                            "chain_connected": info["chain_connected"],
                        },
                    )
                    opportunities.append(opp)
            except Exception as exc:
                logger.warning("[lending] Error for %s: %s", symbol, exc)

        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities

    def _fetch_pools(self) -> list[dict]:
        resp = requests.get(DEFILLAMA_POOLS_URL, timeout=12)
        resp.raise_for_status()
        return resp.json().get("data", [])
