"""
Transaction Executor for Nexus AI.

Responsible for building, signing, and submitting on-chain transactions
for discovered opportunities. All executions respect:
  - DRY_RUN mode (simulation only)
  - Gas price limits
  - Slippage tolerances
  - Pre-execution sanity checks
"""
from __future__ import annotations

import time
from typing import Optional

from web3 import Web3
from eth_account import Account

from nexus.blockchain import BlockchainManager
from nexus.strategies.base import Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum ETH/BNB/MATIC to keep in wallet for gas
RESERVE_NATIVE = 0.01


class TransactionExecutor:
    """Signs and submits transactions for approved opportunities."""

    def __init__(self, blockchain_manager: BlockchainManager):
        self.bm = blockchain_manager

    # ── Main entry point ──────────────────────────────────────

    def execute(self, opp: Opportunity) -> Optional[str]:
        """
        Attempt to execute an opportunity.
        Returns the transaction hash string on success, None on failure.
        """
        if not Config.is_configured():
            logger.warning("Wallet not configured – cannot execute transactions.")
            return None

        if Config.DRY_RUN:
            logger.info("[DRY RUN] Simulating execution: %s", opp.description)
            return self._simulate(opp)

        # Real execution path
        return self._execute_real(opp)

    # ── Simulation ────────────────────────────────────────────

    def _simulate(self, opp: Opportunity) -> str:
        """Simulate a transaction and return a deterministic fake hash."""
        time.sleep(0.1)
        # 0x prefix (2 chars) + 64 hex chars = 66 total, matching a real tx hash length
        fake_hash = "0x" + "sim0" + ("0" * 60)
        logger.info(
            "[DRY RUN] %s | Est. profit $%.4f | tx=%s",
            opp.description,
            opp.estimated_profit_usd,
            fake_hash,
        )
        return fake_hash

    # ── Real execution ────────────────────────────────────────

    def _execute_real(self, opp: Opportunity) -> Optional[str]:
        """Execute a real on-chain transaction."""
        chain = opp.chain
        w3 = self.bm.get_web3(chain)
        if not w3:
            logger.error("No Web3 connection for chain %s", chain)
            return None

        # Safety checks
        gas_gwei = self.bm.get_gas_price_gwei(chain)
        if gas_gwei > Config.MAX_GAS_GWEI:
            logger.warning(
                "Gas too high (%.1f Gwei) for %s – skipping", gas_gwei, opp.description
            )
            return None

        native_balance = self.bm.get_native_balance(chain, Config.WALLET_ADDRESS)
        if native_balance < RESERVE_NATIVE:
            logger.warning(
                "Insufficient native balance (%.6f) on %s – skipping", native_balance, chain
            )
            return None

        try:
            if opp.type == OpportunityType.ARBITRAGE:
                return self._execute_arbitrage(opp, w3)
            elif opp.type == OpportunityType.YIELD_FARMING:
                return self._execute_yield_farming(opp, w3)
            elif opp.type == OpportunityType.LIQUIDITY_MINING:
                return self._execute_liquidity_mining(opp, w3)
            else:
                logger.warning("Unknown opportunity type: %s", opp.type)
                return None
        except Exception as exc:
            logger.error("Execution failed for %s: %s", opp.description, exc)
            return None

    # ── Strategy-specific execution ───────────────────────────

    def _execute_arbitrage(self, opp: Opportunity, w3: Web3) -> Optional[str]:
        """Execute a DEX arbitrage trade."""
        details = opp.details
        chain = opp.chain

        from nexus.protocols.uniswap import UniswapV2Client, DEX_ADDRESSES, ERC20_ABI
        from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES

        addrs = DEX_ADDRESSES.get(chain, {})
        tokens = TOKEN_ADDRESSES.get(chain, {})

        sym_a = details.get("token_a")
        sym_b = details.get("token_b")
        buy_dex = details.get("buy_dex", "")
        sell_dex = details.get("sell_dex", "")
        trade_usd = details.get("trade_usd", 100)

        token_a_addr = tokens.get(sym_a)
        token_b_addr = tokens.get(sym_b)

        if not token_a_addr or not token_b_addr:
            logger.error("Token addresses not found for %s/%s", sym_a, sym_b)
            return None

        logger.info(
            "Executing arbitrage: Buy %s/%s on %s, sell on %s (est. $%.4f profit)",
            sym_a, sym_b, buy_dex, sell_dex, opp.estimated_profit_usd,
        )

        account = Account.from_key(Config.WALLET_PRIVATE_KEY)
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price

        # Step 1: determine router addresses
        buy_router_key = self._dex_name_to_key(buy_dex, chain, "router")
        sell_router_key = self._dex_name_to_key(sell_dex, chain, "router")
        buy_factory_key = self._dex_name_to_key(buy_dex, chain, "factory")
        sell_factory_key = self._dex_name_to_key(sell_dex, chain, "factory")

        buy_router = addrs.get(buy_router_key)
        sell_router = addrs.get(sell_router_key)
        buy_factory = addrs.get(buy_factory_key)
        sell_factory = addrs.get(sell_factory_key)

        if not all([buy_router, sell_router, buy_factory, sell_factory]):
            logger.error("Could not resolve DEX addresses for %s / %s", buy_dex, sell_dex)
            return None

        buy_client = UniswapV2Client(w3, buy_router, buy_factory)
        sell_client = UniswapV2Client(w3, sell_router, sell_factory)

        # Token A decimals
        token_a_contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_a_addr), abi=ERC20_ABI
        )
        dec_a = token_a_contract.functions.decimals().call()

        # Compute amount in (use portion of trade_usd worth of token_a)
        from nexus.protocols.dex_aggregator import PriceAggregator
        price_a = PriceAggregator.get_price(sym_a)
        if not price_a:
            logger.error("Could not get price for %s", sym_a)
            return None

        amount_in_a = trade_usd / price_a
        amount_in_wei = int(amount_in_a * 10**dec_a)

        # Get expected output on buy DEX
        amounts_buy = buy_client.get_amounts_out(amount_in_wei, [token_a_addr, token_b_addr])
        if not amounts_buy:
            logger.error("Could not get buy amounts")
            return None

        # Get expected output on sell DEX (swap token_b back to token_a)
        amounts_sell = sell_client.get_amounts_out(amounts_buy[-1], [token_b_addr, token_a_addr])
        if not amounts_sell:
            logger.error("Could not get sell amounts")
            return None

        profit_tokens = amounts_sell[-1] - amount_in_wei
        if profit_tokens <= 0:
            logger.warning("No longer profitable on-chain – skipping")
            return None

        # Apply slippage tolerance
        slippage_factor = 1 - Config.SLIPPAGE_PERCENT / 100
        min_amount_out_buy = int(amounts_buy[-1] * slippage_factor)
        min_amount_out_sell = int(amounts_sell[-1] * slippage_factor)

        deadline = int(time.time()) + 300  # 5-minute window

        # Approve token_a to buy router (if needed)
        allowance = token_a_contract.functions.allowance(
            account.address,
            Web3.to_checksum_address(buy_router),
        ).call()
        if allowance < amount_in_wei:
            approve_tx = token_a_contract.functions.approve(
                Web3.to_checksum_address(buy_router),
                2**256 - 1,
            ).build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": 60_000,
            })
            signed_approve = account.sign_transaction(approve_tx)
            approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)
            nonce += 1

        # Execute buy swap
        buy_contract = w3.eth.contract(
            address=Web3.to_checksum_address(buy_router),
            abi=[{
                "name": "swapExactTokensForTokens",
                "type": "function",
                "inputs": [
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMin", "type": "uint256"},
                    {"name": "path", "type": "address[]"},
                    {"name": "to", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                ],
                "outputs": [{"name": "amounts", "type": "uint256[]"}],
                "stateMutability": "nonpayable",
            }],
        )

        buy_tx = buy_contract.functions.swapExactTokensForTokens(
            amount_in_wei,
            min_amount_out_buy,
            [
                Web3.to_checksum_address(token_a_addr),
                Web3.to_checksum_address(token_b_addr),
            ],
            account.address,
            deadline,
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gasPrice": gas_price,
            "gas": 200_000,
        })

        signed_buy = account.sign_transaction(buy_tx)
        buy_hash = w3.eth.send_raw_transaction(signed_buy.raw_transaction)
        w3.eth.wait_for_transaction_receipt(buy_hash, timeout=120)
        nonce += 1

        # Approve token_b to sell router
        token_b_contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_b_addr), abi=ERC20_ABI
        )
        b_allowance = token_b_contract.functions.allowance(
            account.address,
            Web3.to_checksum_address(sell_router),
        ).call()
        if b_allowance < amounts_buy[-1]:
            approve_b_tx = token_b_contract.functions.approve(
                Web3.to_checksum_address(sell_router),
                2**256 - 1,
            ).build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": 60_000,
            })
            signed_approve_b = account.sign_transaction(approve_b_tx)
            approve_b_hash = w3.eth.send_raw_transaction(signed_approve_b.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_b_hash, timeout=60)
            nonce += 1

        # Execute sell swap
        sell_contract = w3.eth.contract(
            address=Web3.to_checksum_address(sell_router),
            abi=[{
                "name": "swapExactTokensForTokens",
                "type": "function",
                "inputs": [
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "amountOutMin", "type": "uint256"},
                    {"name": "path", "type": "address[]"},
                    {"name": "to", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                ],
                "outputs": [{"name": "amounts", "type": "uint256[]"}],
                "stateMutability": "nonpayable",
            }],
        )
        sell_tx = sell_contract.functions.swapExactTokensForTokens(
            amounts_buy[-1],
            min_amount_out_sell,
            [
                Web3.to_checksum_address(token_b_addr),
                Web3.to_checksum_address(token_a_addr),
            ],
            account.address,
            deadline,
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gasPrice": gas_price,
            "gas": 200_000,
        })

        signed_sell = account.sign_transaction(sell_tx)
        sell_hash = w3.eth.send_raw_transaction(signed_sell.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(sell_hash, timeout=120)

        final_hash = sell_hash.hex()
        status = "success" if receipt.status == 1 else "failed"
        logger.info("Arbitrage %s: %s", status, final_hash)
        return final_hash if receipt.status == 1 else None

    def _execute_yield_farming(self, opp: Opportunity, w3: Web3) -> Optional[str]:
        """Supply tokens to Aave to earn yield."""
        logger.info("Yield farming execution: %s", opp.description)
        # Full Aave supply interaction would go here.
        # For safety, log and return None unless explicitly implemented.
        logger.info("Yield farming auto-supply: currently requires manual position entry.")
        return None

    def _execute_liquidity_mining(self, opp: Opportunity, w3: Web3) -> Optional[str]:
        """Add liquidity to a DEX pool."""
        logger.info("Liquidity mining execution: %s", opp.description)
        logger.info("LP auto-entry: currently requires manual position entry.")
        return None

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _dex_name_to_key(dex_name: str, chain: str, suffix: str) -> str:
        """Convert a human-readable DEX name to its config key."""
        name_map = {
            "ethereum": {
                "Uniswap V2": "uniswap_v2",
                "SushiSwap": "sushiswap",
            },
            "bsc": {
                "PancakeSwap": "pancakeswap",
            },
            "polygon": {
                "QuickSwap": "quickswap",
                "SushiSwap": "sushiswap",
            },
        }
        base = name_map.get(chain, {}).get(dex_name, dex_name.lower().replace(" ", "_"))
        return f"{base}_{suffix}"
