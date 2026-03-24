"""
Liquidation Bot Strategy for Nexus AI.

Monitors Aave V3 (and Compound V2) for undercollateralized positions
and executes liquidations to earn the liquidation bonus (5–10%).

How it works:
  1. Fetch at-risk positions (healthFactor < 1.0) from on-chain data
     or from the Aave subgraph API.
  2. Calculate if executing the liquidation would be profitable after gas.
  3. Call liquidationCall() on Aave's Pool contract.
  4. Receive collateral at a discounted price (the liquidation bonus).

Earnings: 5% bonus on Aave for most assets.  Typical profit: $50–500/tx.
"""
from __future__ import annotations

from typing import List, Optional

import requests

from web3 import Web3

from nexus.protocols.dex_aggregator import PriceAggregator
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Aave V3 Pool ABI extensions for liquidation
AAVE_LIQUIDATION_ABI = [
    {
        "name": "liquidationCall",
        "type": "function",
        "inputs": [
            {"name": "collateralAsset",       "type": "address"},
            {"name": "debtAsset",             "type": "address"},
            {"name": "user",                  "type": "address"},
            {"name": "debtToCover",           "type": "uint256"},
            {"name": "receiveAToken",         "type": "bool"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "getUserAccountData",
        "type": "function",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase",        "type": "uint256"},
            {"name": "totalDebtBase",              "type": "uint256"},
            {"name": "availableBorrowsBase",       "type": "uint256"},
            {"name": "currentLiquidationThreshold","type": "uint256"},
            {"name": "ltv",                        "type": "uint256"},
            {"name": "healthFactor",               "type": "uint256"},
        ],
        "stateMutability": "view",
    },
]

AAVE_POOL_ADDRESSES: dict[str, str] = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "polygon":  "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
}

# Aave subgraph endpoints (The Graph) for at-risk positions
AAVE_SUBGRAPH: dict[str, str] = {
    "ethereum": "https://api.thegraph.com/subgraphs/name/aave/protocol-v3",
    "polygon":  "https://api.thegraph.com/subgraphs/name/aave/protocol-v3-polygon",
}

# Liquidation bonus ~ 5% on most assets
LIQUIDATION_BONUS_PCT = 5.0
LIQUIDATION_GAS_UNITS = 400_000


class LiquidationStrategy(BaseStrategy):
    """
    Liquidation bot for Aave V3.
    Monitors undercollateralized positions and executes liquidations.
    """

    name = "liquidation"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []

        for chain in self.bm.connected_chains():
            if chain not in AAVE_POOL_ADDRESSES:
                continue

            w3 = self.bm.get_web3(chain)
            if not w3:
                continue

            gas_gwei = self.bm.get_gas_price_gwei(chain)
            if gas_gwei > Config.MAX_GAS_GWEI:
                continue

            native_sym   = {"ethereum": "ETH", "polygon": "MATIC"}.get(chain, "ETH")
            native_price = PriceAggregator.get_price(native_sym) or 2_000.0
            gas_cost_usd = (gas_gwei * 1e-9) * LIQUIDATION_GAS_UNITS * native_price

            # Get at-risk positions from the subgraph
            at_risk = self._fetch_at_risk_positions(chain)

            pool_addr = AAVE_POOL_ADDRESSES[chain]
            pool = w3.eth.contract(
                address=Web3.to_checksum_address(pool_addr),
                abi=AAVE_LIQUIDATION_ABI,
            )

            for pos in at_risk[:20]:   # Check top 20 at-risk positions
                user_addr       = pos.get("user", "")
                health_factor   = float(pos.get("healthFactor", 1.0))
                total_debt_usd  = float(pos.get("totalDebtUSD", 0))
                collateral_usd  = float(pos.get("totalCollateralUSD", 0))

                if health_factor >= 1.0 or total_debt_usd < 100:
                    continue

                # Max liquidation: 50% of debt
                max_liquidation_usd = min(total_debt_usd * 0.5, Config.MAX_TRADE_USD * 5)

                # Gross profit = liquidation amount × bonus %
                gross_profit = max_liquidation_usd * (LIQUIDATION_BONUS_PCT / 100)
                net_profit   = gross_profit - gas_cost_usd

                if net_profit < Config.MIN_PROFIT_USD:
                    continue

                # Lower health factor = more urgent, higher confidence
                confidence = min(0.95, 1.0 - health_factor)

                opp = self._make_opportunity(
                    opp_type=OpportunityType.ARBITRAGE,
                    chain=chain,
                    description=(
                        f"🏦 Liquidation: {user_addr[:10]}… "
                        f"HF={health_factor:.3f}, debt=${total_debt_usd:,.0f}"
                    ),
                    profit_usd=net_profit,
                    confidence=confidence,
                    details={
                        "strategy":             "liquidation",
                        "user":                 user_addr,
                        "health_factor":        health_factor,
                        "total_debt_usd":       total_debt_usd,
                        "total_collateral_usd": collateral_usd,
                        "max_liquidation_usd":  max_liquidation_usd,
                        "bonus_pct":            LIQUIDATION_BONUS_PCT,
                        "gas_cost_usd":         round(gas_cost_usd, 4),
                    },
                )
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities[:10]

    # ── Subgraph queries ──────────────────────────────────────

    def _fetch_at_risk_positions(self, chain: str) -> list[dict]:
        """Fetch positions with health factor < 1.0 from Aave subgraph."""
        endpoint = AAVE_SUBGRAPH.get(chain)
        if not endpoint:
            return []

        query = """
        {
          users(
            first: 50
            where: { healthFactor_lt: "1000000000000000000" }
            orderBy: healthFactor
            orderDirection: asc
          ) {
            id
            healthFactor
            totalCollateralUSD
            totalDebtUSD
            borrowedReservesCount
          }
        }
        """
        try:
            resp = requests.post(
                endpoint,
                json={"query": query},
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            users = data.get("data", {}).get("users", [])
            return [
                {
                    "user":               u["id"],
                    "healthFactor":       int(u["healthFactor"]) / 1e18,
                    "totalCollateralUSD": float(u.get("totalCollateralUSD", 0)),
                    "totalDebtUSD":       float(u.get("totalDebtUSD", 0)),
                }
                for u in users
            ]
        except Exception as exc:
            logger.debug("Subgraph fetch failed for %s: %s", chain, exc)
            return []

    # ── Execution (called from executor) ─────────────────────

    def build_liquidation_tx(
        self,
        chain: str,
        w3: Web3,
        collateral_asset: str,
        debt_asset: str,
        user: str,
        debt_to_cover: int,
        from_addr: str,
        nonce: int,
        gas_params: dict,
    ) -> Optional[dict]:
        pool_addr = AAVE_POOL_ADDRESSES.get(chain)
        if not pool_addr:
            return None
        pool = w3.eth.contract(
            address=Web3.to_checksum_address(pool_addr),
            abi=AAVE_LIQUIDATION_ABI,
        )
        try:
            return pool.functions.liquidationCall(
                Web3.to_checksum_address(collateral_asset),
                Web3.to_checksum_address(debt_asset),
                Web3.to_checksum_address(user),
                debt_to_cover,
                False,   # receive underlying collateral, not aToken
            ).build_transaction({
                "from":  Web3.to_checksum_address(from_addr),
                "nonce": nonce,
                "gas":   LIQUIDATION_GAS_UNITS,
                **gas_params,
            })
        except Exception as exc:
            logger.error("build_liquidation_tx: %s", exc)
            return None
