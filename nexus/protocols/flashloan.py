"""
Flash Loan Protocol Interfaces for Nexus AI.

Supports:
  • Aave V3  – 0.09% fee, available on Ethereum + Polygon
  • Balancer – 0% fee flash loans (Vault flash), Ethereum + Polygon
"""
from __future__ import annotations

from typing import Optional

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Aave V3 ───────────────────────────────────────────────────

AAVE_V3_POOL: dict[str, str] = {
    "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "polygon":  "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
}

# Flash loan fee: 0.09% = 9 bps
AAVE_FLASH_FEE_BPS = 9

AAVE_POOL_FLASH_ABI = [
    {
        "name": "flashLoanSimple",
        "type": "function",
        "inputs": [
            {"name": "receiverAddress", "type": "address"},
            {"name": "asset",           "type": "address"},
            {"name": "amount",          "type": "uint256"},
            {"name": "params",          "type": "bytes"},
            {"name": "referralCode",    "type": "uint16"},
        ],
        "outputs": [],
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
                    {"name": "configuration",                "type": "uint256"},
                    {"name": "liquidityIndex",               "type": "uint128"},
                    {"name": "currentLiquidityRate",         "type": "uint128"},
                    {"name": "variableBorrowIndex",          "type": "uint128"},
                    {"name": "currentVariableBorrowRate",    "type": "uint128"},
                    {"name": "currentStableBorrowRate",      "type": "uint128"},
                    {"name": "lastUpdateTimestamp",          "type": "uint40"},
                    {"name": "id",                           "type": "uint16"},
                    {"name": "aTokenAddress",                "type": "address"},
                    {"name": "stableDebtTokenAddress",       "type": "address"},
                    {"name": "variableDebtTokenAddress",     "type": "address"},
                    {"name": "interestRateStrategyAddress",  "type": "address"},
                    {"name": "accruedToTreasury",            "type": "uint128"},
                    {"name": "unbacked",                     "type": "uint128"},
                    {"name": "isolationModeTotalDebt",       "type": "uint128"},
                ],
            }
        ],
        "stateMutability": "view",
    },
]

# ── Balancer Vault ────────────────────────────────────────────

BALANCER_VAULT: dict[str, str] = {
    "ethereum": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    "polygon":  "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
}

BALANCER_VAULT_ABI = [
    {
        "name": "flashLoan",
        "type": "function",
        "inputs": [
            {"name": "recipient",  "type": "address"},
            {"name": "tokens",     "type": "address[]"},
            {"name": "amounts",    "type": "uint256[]"},
            {"name": "userData",   "type": "bytes"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
]

# Balancer flash fee = 0%
BALANCER_FLASH_FEE_BPS = 0


# ── FlashLoanProvider ─────────────────────────────────────────

class FlashLoanProvider:
    """
    Wraps Aave V3 and Balancer flash loan capabilities.
    Chooses the cheapest available provider.
    """

    def __init__(self, w3: Web3, chain: str):
        self.w3 = w3
        self.chain = chain

        aave_addr = AAVE_V3_POOL.get(chain)
        self.aave = (
            w3.eth.contract(
                address=Web3.to_checksum_address(aave_addr),
                abi=AAVE_POOL_FLASH_ABI,
            )
            if aave_addr else None
        )

        bal_addr = BALANCER_VAULT.get(chain)
        self.balancer = (
            w3.eth.contract(
                address=Web3.to_checksum_address(bal_addr),
                abi=BALANCER_VAULT_ABI,
            )
            if bal_addr else None
        )

    def best_provider(self) -> str:
        """Return the cheapest flash loan provider available."""
        if self.balancer:
            return "balancer"   # 0% fee
        if self.aave:
            return "aave"       # 0.09% fee
        return "none"

    def calculate_fee(self, amount_wei: int, provider: str = "aave") -> int:
        """Return the flash loan fee in wei."""
        if provider == "balancer":
            return 0
        if provider == "aave":
            return (amount_wei * AAVE_FLASH_FEE_BPS) // 10_000
        return 0

    def build_aave_flash_tx(
        self,
        receiver_contract: str,
        asset: str,
        amount_wei: int,
        params_bytes: bytes,
        from_addr: str,
        nonce: int,
        gas_params: dict,
    ) -> Optional[dict]:
        """Build (but do not sign) an Aave flashLoanSimple transaction."""
        if not self.aave:
            return None
        try:
            return self.aave.functions.flashLoanSimple(
                Web3.to_checksum_address(receiver_contract),
                Web3.to_checksum_address(asset),
                amount_wei,
                params_bytes,
                0,
            ).build_transaction({
                "from":  Web3.to_checksum_address(from_addr),
                "nonce": nonce,
                "gas":   500_000,
                **gas_params,
            })
        except Exception as exc:
            logger.error("build_aave_flash_tx: %s", exc)
            return None

    def build_balancer_flash_tx(
        self,
        receiver_contract: str,
        tokens: list[str],
        amounts: list[int],
        user_data: bytes,
        from_addr: str,
        nonce: int,
        gas_params: dict,
    ) -> Optional[dict]:
        """Build a Balancer flashLoan transaction."""
        if not self.balancer:
            return None
        try:
            return self.balancer.functions.flashLoan(
                Web3.to_checksum_address(receiver_contract),
                [Web3.to_checksum_address(t) for t in tokens],
                amounts,
                user_data,
            ).build_transaction({
                "from":  Web3.to_checksum_address(from_addr),
                "nonce": nonce,
                "gas":   600_000,
                **gas_params,
            })
        except Exception as exc:
            logger.error("build_balancer_flash_tx: %s", exc)
            return None
