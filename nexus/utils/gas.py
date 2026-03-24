"""
EIP-1559 Dynamic Gas Manager for Nexus AI.

Supports both:
  • EIP-1559 chains (Ethereum, Polygon) — baseFee + priorityFee model
  • Legacy chains (BSC) — gasPrice model

Strategy:
  - Reads the last N blocks to build a fee history
  - Selects an aggressive priority fee for time-sensitive arb transactions
  - Selects a standard priority fee for yield/LP transactions
  - Hard cap at Config.MAX_GAS_GWEI to protect from gas-spike losses
"""
from __future__ import annotations

from typing import Literal, Optional

from web3 import Web3

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

SpeedT = Literal["slow", "standard", "fast", "aggressive"]

# Priority fee percentile targets (lower = cheaper, higher = faster)
_PERCENTILE_MAP: dict[SpeedT, int] = {
    "slow":       10,
    "standard":   50,
    "fast":       75,
    "aggressive": 90,
}

# EIP-1559 capable chain IDs
EIP1559_CHAINS = {1, 137, 42161, 10, 8453}  # ETH, Polygon, Arbitrum, Optimism, Base


class GasManager:
    """Builds optimal gas parameters for a given chain and speed preference."""

    def __init__(self, w3: Web3, chain: str):
        self.w3 = w3
        self.chain = chain
        try:
            self._chain_id = w3.eth.chain_id
        except Exception:
            self._chain_id = 0

    def is_eip1559(self) -> bool:
        return self._chain_id in EIP1559_CHAINS

    # ── Public API ────────────────────────────────────────────

    def get_gas_params(self, speed: SpeedT = "fast") -> dict:
        """
        Return a dict suitable for passing to build_transaction():
          EIP-1559: {"maxFeePerGas": ..., "maxPriorityFeePerGas": ...}
          Legacy:   {"gasPrice": ...}
        Both are capped at MAX_GAS_GWEI.
        """
        try:
            if self.is_eip1559():
                return self._eip1559_params(speed)
            else:
                return self._legacy_params(speed)
        except Exception as exc:
            logger.warning("Gas estimation error (%s): %s – using fallback", self.chain, exc)
            return self._fallback_params()

    def estimate_cost_usd(
        self,
        gas_units: int,
        native_price_usd: float,
        speed: SpeedT = "fast",
    ) -> float:
        """Return estimated transaction cost in USD."""
        params = self.get_gas_params(speed)
        if "maxFeePerGas" in params:
            gwei = params["maxFeePerGas"] / 1e9
        else:
            gwei = params["gasPrice"] / 1e9
        eth = gwei * 1e-9 * gas_units
        return eth * native_price_usd

    def get_gwei(self, speed: SpeedT = "fast") -> float:
        params = self.get_gas_params(speed)
        if "maxFeePerGas" in params:
            return params["maxFeePerGas"] / 1e9
        return params.get("gasPrice", 0) / 1e9

    # ── EIP-1559 ──────────────────────────────────────────────

    def _eip1559_params(self, speed: SpeedT) -> dict:
        percentile = _PERCENTILE_MAP[speed]

        # Use fee_history for the last 5 blocks
        history = self.w3.eth.fee_history(
            block_count=5,
            newest_block="latest",
            reward_percentiles=[percentile],
        )

        # Median priority fee from history
        rewards = [r[0] for r in history.get("reward", []) if r]
        if rewards:
            priority_fee = sorted(rewards)[len(rewards) // 2]
        else:
            priority_fee = self.w3.eth.max_priority_fee

        # baseFee from the pending block
        base_fee = history["baseFeePerGas"][-1]

        # maxFeePerGas = 2 × baseFee + priorityFee  (EIP-1559 recommended)
        max_fee = 2 * base_fee + priority_fee

        # Hard cap
        cap_wei = int(Config.MAX_GAS_GWEI * 1e9)
        max_fee = min(max_fee, cap_wei)
        priority_fee = min(priority_fee, max_fee)

        logger.debug(
            "[%s] EIP-1559 gas: base=%.2f maxFee=%.2f priority=%.2f Gwei",
            self.chain,
            base_fee / 1e9,
            max_fee / 1e9,
            priority_fee / 1e9,
        )
        return {
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }

    # ── Legacy (BSC etc.) ─────────────────────────────────────

    def _legacy_params(self, speed: SpeedT) -> dict:
        base = self.w3.eth.gas_price
        multipliers: dict[SpeedT, float] = {
            "slow": 0.9,
            "standard": 1.0,
            "fast": 1.1,
            "aggressive": 1.25,
        }
        gas_price = int(base * multipliers[speed])
        cap_wei = int(Config.MAX_GAS_GWEI * 1e9)
        gas_price = min(gas_price, cap_wei)
        logger.debug("[%s] Legacy gas: %.2f Gwei", self.chain, gas_price / 1e9)
        return {"gasPrice": gas_price}

    def _fallback_params(self) -> dict:
        fallback_wei = int(min(20, Config.MAX_GAS_GWEI) * 1e9)
        if self.is_eip1559():
            return {"maxFeePerGas": fallback_wei, "maxPriorityFeePerGas": fallback_wei // 10}
        return {"gasPrice": fallback_wei}
