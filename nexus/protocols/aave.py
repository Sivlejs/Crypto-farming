"""
Aave V3 lending protocol adapter for Nexus AI.
Used for yield farming by supplying assets to earn interest.
"""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Minimal ABIs ──────────────────────────────────────────────

AAVE_POOL_ABI = [
    {
        "name": "supply",
        "type": "function",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "onBehalfOf", "type": "address"},
            {"name": "referralCode", "type": "uint16"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "withdraw",
        "type": "function",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "to", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "getReserveData",
        "type": "function",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "configuration", "type": "uint256"},
                    {"name": "liquidityIndex", "type": "uint128"},
                    {"name": "currentLiquidityRate", "type": "uint128"},
                    {"name": "variableBorrowIndex", "type": "uint128"},
                    {"name": "currentVariableBorrowRate", "type": "uint128"},
                    {"name": "currentStableBorrowRate", "type": "uint128"},
                    {"name": "lastUpdateTimestamp", "type": "uint40"},
                    {"name": "id", "type": "uint16"},
                    {"name": "aTokenAddress", "type": "address"},
                    {"name": "stableDebtTokenAddress", "type": "address"},
                    {"name": "variableDebtTokenAddress", "type": "address"},
                    {"name": "interestRateStrategyAddress", "type": "address"},
                    {"name": "accruedToTreasury", "type": "uint128"},
                    {"name": "unbacked", "type": "uint128"},
                    {"name": "isolationModeTotalDebt", "type": "uint128"},
                ],
            }
        ],
        "stateMutability": "view",
    },
    {
        "name": "getUserAccountData",
        "type": "function",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
    },
]

# Aave V3 pool addresses per chain
AAVE_POOL_ADDRESSES: dict[str, str] = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "polygon": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "avalanche": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
    "base": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
}

# Ray = 10^27 (Aave's precision for rates)
RAY = 10**27
SECONDS_PER_YEAR = 31_536_000


class AaveClient:
    """Read-only + execution client for Aave V3."""

    def __init__(self, w3: Web3, chain: str):
        self.w3 = w3
        self.chain = chain
        pool_addr = AAVE_POOL_ADDRESSES.get(chain)
        if pool_addr:
            self.pool = w3.eth.contract(
                address=Web3.to_checksum_address(pool_addr),
                abi=AAVE_POOL_ABI,
            )
        else:
            self.pool = None

    def is_supported(self) -> bool:
        return self.pool is not None

    def get_supply_apy(self, asset_address: str) -> Optional[float]:
        """Return current supply APY for an asset (0–100 %).

        Uses a linear approximation of the compound APY formula:
            APY ≈ liquidityRate / RAY  (accurate when rates are small)
        which avoids the expensive per-second exponentiation.
        """
        if not self.pool:
            return None
        try:
            data = self.pool.functions.getReserveData(
                Web3.to_checksum_address(asset_address)
            ).call()
            liquidity_rate = data[2]  # currentLiquidityRate in ray
            # Linear approximation: equivalent to (rate / RAY) * 100
            apy = (liquidity_rate / RAY) * 100
            return round(apy, 4)
        except Exception as exc:
            logger.debug("get_supply_apy failed for %s: %s", asset_address, exc)
            return None

    def get_user_account(self, user_address: str) -> Optional[dict]:
        """Return a summary of a user's Aave account."""
        if not self.pool:
            return None
        try:
            data = self.pool.functions.getUserAccountData(
                Web3.to_checksum_address(user_address)
            ).call()
            return {
                "total_collateral_usd": data[0] / 10**8,
                "total_debt_usd": data[1] / 10**8,
                "available_borrow_usd": data[2] / 10**8,
                "health_factor": data[5] / 10**18,
            }
        except Exception as exc:
            logger.debug("get_user_account failed: %s", exc)
            return None

    def build_supply_tx(
        self,
        asset: str,
        amount_wei: int,
        on_behalf_of: str,
        nonce: int,
        gas_price_wei: int,
    ) -> Optional[dict]:
        """Build (but do not send) a supply transaction."""
        if not self.pool:
            return None
        try:
            return self.pool.functions.supply(
                Web3.to_checksum_address(asset),
                amount_wei,
                Web3.to_checksum_address(on_behalf_of),
                0,
            ).build_transaction(
                {
                    "from": Web3.to_checksum_address(on_behalf_of),
                    "nonce": nonce,
                    "gasPrice": gas_price_wei,
                    "gas": 250_000,
                }
            )
        except Exception as exc:
            logger.error("build_supply_tx failed: %s", exc)
            return None
