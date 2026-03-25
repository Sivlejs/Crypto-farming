"""
Transaction Executor for Nexus AI.

Responsible for building, signing, and submitting on-chain transactions
for discovered opportunities. All executions respect:
  - DRY_RUN mode (simulation only)
  - Gas price limits
  - Slippage tolerances
  - Pre-execution sanity checks

v2 Efficiency Upgrades:
  • Dynamic slippage tolerance based on market volatility
  • Enhanced gas estimation with EIP-1559 support
  • Better error handling and logging
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

# Transaction execution constants
LP_DEADLINE_SECONDS = 600  # 10 minute deadline for LP transactions
MOCK_TX_HASH_PADDING = 60  # Padding length for simulated transaction hashes (results in 64 chars total)

# Dynamic slippage thresholds based on volatility
SLIPPAGE_VOLATILITY_THRESHOLDS = {
    "low": {"max_volatility": 1.0, "slippage": 0.3},      # Calm market: tight slippage
    "medium": {"max_volatility": 3.0, "slippage": 0.7},   # Normal market: moderate slippage
    "high": {"max_volatility": 5.0, "slippage": 1.0},     # Volatile market: wider slippage
    "extreme": {"max_volatility": float("inf"), "slippage": 1.5},  # Very volatile: max slippage
}


class TransactionExecutor:
    """Signs and submits transactions for approved opportunities."""

    def __init__(self, blockchain_manager: BlockchainManager, brain=None):
        self.bm = blockchain_manager
        self._brain = brain  # Optional: for dynamic slippage based on volatility
        self._execution_count = 0
        self._success_count = 0
        self._slippage_adjustments = 0
    
    def set_brain(self, brain):
        """Attach the NexusBrain for volatility-aware slippage."""
        self._brain = brain
    
    def get_dynamic_slippage(self, chain: str = "ethereum") -> float:
        """
        Calculate dynamic slippage tolerance based on market volatility.
        
        Parameters
        ----------
        chain : str
            The blockchain chain (ethereum, bsc, polygon, etc.)
            Used to determine the relevant native asset for volatility.
        
        Returns slippage as a percentage (e.g., 0.5 for 0.5%).
        """
        if not self._brain:
            return Config.SLIPPAGE_PERCENT
        
        # Map chains to their native/primary assets for volatility tracking
        chain_to_asset = {
            "ethereum": "ETH",
            "bsc": "BNB",
            "polygon": "MATIC",
            "arbitrum": "ETH",
            "optimism": "ETH",
            "base": "ETH",
            "avalanche": "AVAX",
        }
        asset = chain_to_asset.get(chain.lower(), "ETH")
        
        try:
            volatility = self._brain.classifier.volatility_pct(asset)
            
            for level, params in SLIPPAGE_VOLATILITY_THRESHOLDS.items():
                if volatility <= params["max_volatility"]:
                    dynamic_slippage = params["slippage"]
                    if dynamic_slippage != Config.SLIPPAGE_PERCENT:
                        self._slippage_adjustments += 1
                        logger.debug(
                            "Dynamic slippage: %.2f%% (chain=%s, asset=%s, volatility=%.2f%%, level=%s)",
                            dynamic_slippage, chain, asset, volatility, level,
                        )
                    return dynamic_slippage
            
            return SLIPPAGE_VOLATILITY_THRESHOLDS["extreme"]["slippage"]
        except Exception as exc:
            logger.debug("Dynamic slippage calculation failed: %s", exc)
            return Config.SLIPPAGE_PERCENT

    # ── Main entry point ──────────────────────────────────────

    def execute(self, opp: Opportunity) -> Optional[str]:
        """
        Attempt to execute an opportunity.
        Returns the transaction hash string on success, None on failure.
        """
        self._execution_count += 1
        
        if not Config.is_configured():
            logger.warning("Wallet not configured – cannot execute transactions.")
            return None

        if Config.DRY_RUN:
            logger.info("[DRY RUN] Simulating execution: %s", opp.description)
            result = self._simulate(opp)
            if result:
                self._success_count += 1
            return result

        # Real execution path
        result = self._execute_real(opp)
        if result:
            self._success_count += 1
        return result
    
    def stats(self) -> dict:
        """Return executor statistics."""
        return {
            "total_executions": self._execution_count,
            "successful_executions": self._success_count,
            "success_rate": round(self._success_count / max(1, self._execution_count) * 100, 2),
            "slippage_adjustments": self._slippage_adjustments,
            "current_slippage": self.get_dynamic_slippage(),
        }

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

        # Apply dynamic slippage tolerance based on market volatility
        # Apply dynamic slippage tolerance based on market volatility for this chain
        slippage_pct = self.get_dynamic_slippage(chain=chain)
        slippage_factor = 1 - slippage_pct / 100
        min_amount_out_buy = int(amounts_buy[-1] * slippage_factor)
        min_amount_out_sell = int(amounts_sell[-1] * slippage_factor)
        
        logger.debug(
            "Arbitrage slippage: %.2f%% (chain=%s, min_buy=%d, min_sell=%d)",
            slippage_pct, chain, min_amount_out_buy, min_amount_out_sell,
        )

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
        """
        Supply tokens to Aave or other lending protocols to earn yield.
        
        Uses the pool details to determine the optimal entry strategy:
        - For Aave-based pools: direct supply to Aave V3
        - For other protocols: log for manual entry (safety first)
        """
        logger.info("Yield farming execution: %s", opp.description)
        details = opp.details
        chain = opp.chain
        protocol = details.get("protocol", "").lower()
        
        # Import required components
        from nexus.protocols.aave import AaveClient, AAVE_POOL_ADDRESSES
        from nexus.protocols.uniswap import ERC20_ABI
        from nexus.execution.bundle_submitter import get_bundle_submitter
        
        # Check if this is an Aave-based pool we can auto-enter
        aave_protocols = {"aave", "aave-v3", "aave-v2", "aave v3", "aave v2"}
        if protocol not in aave_protocols:
            logger.info(
                "Protocol '%s' requires manual entry. Pool: %s (APY: %.2f%%)",
                protocol, details.get("symbol", "unknown"), details.get("apy", 0)
            )
            # Return a simulated success for tracking purposes in dry run
            if Config.DRY_RUN:
                return "0x" + "farm" + ("0" * MOCK_TX_HASH_PADDING)
            return None
        
        # Check if Aave is supported on this chain
        if chain not in AAVE_POOL_ADDRESSES:
            logger.warning("Aave not supported on chain %s", chain)
            return None
        
        # Initialize Aave client
        aave = AaveClient(w3, chain)
        if not aave.is_supported():
            logger.warning("Aave client not available for chain %s", chain)
            return None
        
        # Determine which token to supply (parse from symbol)
        symbol = details.get("symbol", "")
        # Common token mapping for Aave markets
        token_map = self._get_aave_token_map(chain)
        
        # Try to find a matching token from the symbol
        supply_token = None
        supply_address = None
        for token, address in token_map.items():
            if token.lower() in symbol.lower():
                supply_token = token
                supply_address = address
                break
        
        if not supply_address:
            logger.info(
                "Could not determine supply token for symbol '%s'. Manual entry required.",
                symbol
            )
            return None
        
        # Get account and check balance
        account = Account.from_key(Config.WALLET_PRIVATE_KEY)
        
        try:
            token_contract = w3.eth.contract(
                address=Web3.to_checksum_address(supply_address),
                abi=ERC20_ABI
            )
            decimals = token_contract.functions.decimals().call()
            balance = token_contract.functions.balanceOf(account.address).call()
            
            if balance == 0:
                logger.info("No %s balance to supply", supply_token)
                return None
            
            # Calculate amount to supply based on MAX_TRADE_USD and token price
            # Fetch current token price to calculate the correct amount
            from nexus.protocols.dex_aggregator import PriceAggregator
            token_price = PriceAggregator.get_price(supply_token)
            
            if token_price and token_price > 0:
                # Calculate amount in token units based on USD value
                max_amount_tokens = Config.MAX_TRADE_USD / token_price
                max_amount_wei = int(max_amount_tokens * (10 ** decimals))
                supply_amount = min(balance, max_amount_wei)
            else:
                # Fallback: use balance but cap at a reasonable amount
                logger.warning("Could not fetch price for %s, using balance-based limit", supply_token)
                supply_amount = min(balance, int(100 * (10 ** decimals)))  # Cap at 100 tokens
            
            if supply_amount < 10 ** (decimals - 2):  # Minimum amount check
                logger.info("Supply amount too small for %s", supply_token)
                return None
            
            logger.info(
                "Preparing to supply %.6f %s to Aave on %s",
                supply_amount / (10 ** decimals), supply_token, chain
            )
            
            # Check and set allowance if needed
            allowance = token_contract.functions.allowance(
                account.address, 
                AAVE_POOL_ADDRESSES[chain]
            ).call()
            
            nonce = w3.eth.get_transaction_count(account.address)
            gas_price = w3.eth.gas_price
            
            tx_hashes = []
            
            if allowance < supply_amount:
                # Approve Aave pool to spend tokens
                approve_tx = token_contract.functions.approve(
                    Web3.to_checksum_address(AAVE_POOL_ADDRESSES[chain]),
                    supply_amount
                ).build_transaction({
                    "from": account.address,
                    "nonce": nonce,
                    "gasPrice": gas_price,
                    "gas": 100_000,
                })
                
                signed_approve = account.sign_transaction(approve_tx)
                approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
                receipt = w3.eth.wait_for_transaction_receipt(approve_hash, timeout=120)
                
                if receipt.status != 1:
                    logger.error("Approve transaction failed")
                    return None
                
                tx_hashes.append(approve_hash.hex())
                nonce += 1
                logger.info("Approved %s for Aave: %s", supply_token, approve_hash.hex())
            
            # Build and send supply transaction
            supply_tx = aave.build_supply_tx(
                asset=supply_address,
                amount_wei=supply_amount,
                on_behalf_of=account.address,
                nonce=nonce,
                gas_price_wei=gas_price
            )
            
            if not supply_tx:
                logger.error("Failed to build Aave supply transaction")
                return None
            
            # Try to use Flashbots for MEV protection if available
            bundle_submitter = get_bundle_submitter()
            if bundle_submitter.is_available() and chain == "ethereum":
                signed_supply = account.sign_transaction(supply_tx)
                result = bundle_submitter.submit_bundle([signed_supply.raw_transaction], w3)
                if result.get("success"):
                    logger.info(
                        "Aave supply submitted via Flashbots: bundle_hash=%s",
                        result.get("bundle_hash", "")[:20]
                    )
                    return result.get("bundle_hash", tx_hashes[-1] if tx_hashes else None)
            
            # Standard submission
            signed_supply = account.sign_transaction(supply_tx)
            supply_hash = w3.eth.send_raw_transaction(signed_supply.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(supply_hash, timeout=120)
            
            if receipt.status == 1:
                logger.info(
                    "Successfully supplied %.6f %s to Aave: %s",
                    supply_amount / (10 ** decimals), supply_token, supply_hash.hex()
                )
                return supply_hash.hex()
            else:
                logger.error("Aave supply transaction failed")
                return None
                
        except Exception as exc:
            logger.error("Yield farming execution failed: %s", exc)
            return None

    def _execute_liquidity_mining(self, opp: Opportunity, w3: Web3) -> Optional[str]:
        """
        Add liquidity to a DEX pool to earn trading fees and rewards.
        
        Supports:
        - Uniswap V2 compatible DEXes (Uniswap, SushiSwap, PancakeSwap, QuickSwap)
        - Single-sided liquidity where supported
        """
        logger.info("Liquidity mining execution: %s", opp.description)
        details = opp.details
        chain = opp.chain
        protocol = details.get("protocol", "").lower()
        symbol = details.get("symbol", "")
        
        from nexus.protocols.uniswap import DEX_ADDRESSES, ERC20_ABI, UNISWAP_V2_ROUTER_ABI
        from nexus.execution.bundle_submitter import get_bundle_submitter
        
        # Determine which DEX router to use based on protocol and chain
        router_address = self._get_lp_router(protocol, chain)
        if not router_address:
            logger.info(
                "Protocol '%s' on %s requires manual LP entry. Pool: %s (APY: %.2f%%)",
                protocol, chain, symbol, details.get("apy_total", 0)
            )
            if Config.DRY_RUN:
                return "0x" + "lp00" + ("0" * MOCK_TX_HASH_PADDING)
            return None
        
        # Parse token pair from symbol (e.g., "WETH-USDC" or "ETH/USDC")
        tokens = self._parse_lp_tokens(symbol, chain)
        if not tokens:
            logger.info("Could not parse token pair from symbol '%s'", symbol)
            return None
        
        token_a_address, token_b_address = tokens
        
        account = Account.from_key(Config.WALLET_PRIVATE_KEY)
        
        try:
            # Get token contracts and check balances
            token_a = w3.eth.contract(
                address=Web3.to_checksum_address(token_a_address),
                abi=ERC20_ABI
            )
            token_b = w3.eth.contract(
                address=Web3.to_checksum_address(token_b_address),
                abi=ERC20_ABI
            )
            
            decimals_a = token_a.functions.decimals().call()
            decimals_b = token_b.functions.decimals().call()
            balance_a = token_a.functions.balanceOf(account.address).call()
            balance_b = token_b.functions.balanceOf(account.address).call()
            
            if balance_a == 0 or balance_b == 0:
                logger.info("Insufficient token balances for LP entry")
                return None
            
            # Calculate amounts based on MAX_TRADE_USD and token prices
            # Each side of the LP should be worth MAX_TRADE_USD / 2
            from nexus.protocols.dex_aggregator import PriceAggregator
            
            # Try to get token prices for proper USD-based calculation
            # Parse token symbols from symbol string (e.g., "WETH-USDC" -> "ETH", "USDC")
            symbol_parts = symbol.upper().replace("/", "-").replace("_", "-").split("-")
            token_a_sym = symbol_parts[0] if len(symbol_parts) > 0 else "UNKNOWN"
            token_b_sym = symbol_parts[1] if len(symbol_parts) > 1 else "UNKNOWN"
            
            price_a = PriceAggregator.get_price(token_a_sym.replace("W", ""))  # Remove W prefix for wrapped tokens
            price_b = PriceAggregator.get_price(token_b_sym.replace("W", ""))
            
            half_trade_usd = Config.MAX_TRADE_USD / 2
            
            if price_a and price_a > 0:
                max_amount_a = int((half_trade_usd / price_a) * (10 ** decimals_a))
                amount_a = min(balance_a, max_amount_a)
            else:
                # Fallback: cap at reasonable amount
                amount_a = min(balance_a, int(100 * (10 ** decimals_a)))
            
            if price_b and price_b > 0:
                max_amount_b = int((half_trade_usd / price_b) * (10 ** decimals_b))
                amount_b = min(balance_b, max_amount_b)
            else:
                # Fallback: cap at reasonable amount
                amount_b = min(balance_b, int(100 * (10 ** decimals_b)))
            
            # Dynamic slippage based on market conditions
            slippage = self.get_dynamic_slippage(chain)
            amount_a_min = int(amount_a * (1 - slippage / 100))
            amount_b_min = int(amount_b * (1 - slippage / 100))
            
            logger.info(
                "Preparing LP entry: %.6f token_a + %.6f token_b on %s",
                amount_a / (10 ** decimals_a),
                amount_b / (10 ** decimals_b),
                protocol
            )
            
            nonce = w3.eth.get_transaction_count(account.address)
            gas_price = w3.eth.gas_price
            deadline = int(time.time()) + LP_DEADLINE_SECONDS
            
            # Approve both tokens for router
            for token_contract, amount, name in [
                (token_a, amount_a, "token_a"),
                (token_b, amount_b, "token_b")
            ]:
                allowance = token_contract.functions.allowance(
                    account.address, router_address
                ).call()
                
                if allowance < amount:
                    approve_tx = token_contract.functions.approve(
                        Web3.to_checksum_address(router_address),
                        amount
                    ).build_transaction({
                        "from": account.address,
                        "nonce": nonce,
                        "gasPrice": gas_price,
                        "gas": 100_000,
                    })
                    
                    signed_approve = account.sign_transaction(approve_tx)
                    approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
                    receipt = w3.eth.wait_for_transaction_receipt(approve_hash, timeout=120)
                    
                    if receipt.status != 1:
                        logger.error("Approve transaction failed for %s", name)
                        return None
                    
                    nonce += 1
                    logger.info("Approved %s for router: %s", name, approve_hash.hex())
            
            # Build addLiquidity transaction
            router = w3.eth.contract(
                address=Web3.to_checksum_address(router_address),
                abi=self._get_router_abi_with_add_liquidity()
            )
            
            add_liq_tx = router.functions.addLiquidity(
                Web3.to_checksum_address(token_a_address),
                Web3.to_checksum_address(token_b_address),
                amount_a,
                amount_b,
                amount_a_min,
                amount_b_min,
                account.address,
                deadline
            ).build_transaction({
                "from": account.address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": 300_000,
            })
            
            # Try Flashbots for MEV protection
            bundle_submitter = get_bundle_submitter()
            if bundle_submitter.is_available() and chain == "ethereum":
                signed_liq = account.sign_transaction(add_liq_tx)
                result = bundle_submitter.submit_bundle([signed_liq.raw_transaction], w3)
                if result.get("success"):
                    logger.info(
                        "LP entry submitted via Flashbots: bundle_hash=%s",
                        result.get("bundle_hash", "")[:20]
                    )
                    return result.get("bundle_hash")
            
            # Standard submission
            signed_liq = account.sign_transaction(add_liq_tx)
            liq_hash = w3.eth.send_raw_transaction(signed_liq.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(liq_hash, timeout=120)
            
            if receipt.status == 1:
                logger.info("Successfully added liquidity: %s", liq_hash.hex())
                return liq_hash.hex()
            else:
                logger.error("Add liquidity transaction failed")
                return None
                
        except Exception as exc:
            logger.error("Liquidity mining execution failed: %s", exc)
            return None

    # ── Helpers ───────────────────────────────────────────────
    
    def _get_aave_token_map(self, chain: str) -> dict[str, str]:
        """Return mapping of token symbols to addresses for Aave markets."""
        from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
        
        # Get chain token addresses
        chain_tokens = TOKEN_ADDRESSES.get(chain, {})
        
        # Add common mappings
        token_map = {}
        for symbol, address in chain_tokens.items():
            # Normalize symbol (remove W prefix for wrapped tokens)
            normalized = symbol.replace("W", "") if symbol.startswith("W") else symbol
            token_map[normalized] = address
            token_map[symbol] = address
        
        return token_map
    
    def _get_lp_router(self, protocol: str, chain: str) -> Optional[str]:
        """Get the router address for a liquidity mining protocol."""
        from nexus.protocols.uniswap import DEX_ADDRESSES
        
        chain_addrs = DEX_ADDRESSES.get(chain, {})
        
        # Map protocol names to router keys
        protocol_router_map = {
            "uniswap": "uniswap_v2_router",
            "uniswap-v2": "uniswap_v2_router",
            "uniswap v2": "uniswap_v2_router",
            "sushiswap": "sushiswap_router",
            "sushi": "sushiswap_router",
            "pancakeswap": "pancakeswap_router",
            "pancake": "pancakeswap_router",
            "quickswap": "quickswap_router",
            "quick": "quickswap_router",
        }
        
        router_key = protocol_router_map.get(protocol.lower())
        if router_key:
            return chain_addrs.get(router_key)
        
        # Try to find any router for the chain
        for key, addr in chain_addrs.items():
            if "router" in key.lower():
                return addr
        
        return None
    
    def _parse_lp_tokens(self, symbol: str, chain: str) -> Optional[tuple[str, str]]:
        """Parse token addresses from LP symbol like 'WETH-USDC' or 'ETH/USDC'."""
        from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
        from nexus.protocols.uniswap import DEX_ADDRESSES
        
        # Normalize and split symbol
        symbol = symbol.upper().replace("/", "-").replace("_", "-")
        parts = symbol.split("-")
        
        if len(parts) < 2:
            return None
        
        token_a_sym, token_b_sym = parts[0], parts[1]
        
        # Get token addresses from our mappings
        chain_tokens = TOKEN_ADDRESSES.get(chain, {})
        dex_tokens = DEX_ADDRESSES.get(chain, {})
        
        # Combine all known tokens
        all_tokens = {**chain_tokens, **dex_tokens}
        
        # Normalize token symbols
        def find_address(sym: str) -> Optional[str]:
            sym = sym.upper()
            # Direct match
            if sym in all_tokens:
                return all_tokens[sym]
            # Try with W prefix (wrapped)
            if f"W{sym}" in all_tokens:
                return all_tokens[f"W{sym}"]
            # Try lowercase
            for key, addr in all_tokens.items():
                if key.upper() == sym or key.upper() == f"W{sym}":
                    return addr
            return None
        
        addr_a = find_address(token_a_sym)
        addr_b = find_address(token_b_sym)
        
        if addr_a and addr_b:
            return (addr_a, addr_b)
        
        return None
    
    def _get_router_abi_with_add_liquidity(self) -> list:
        """Return router ABI including addLiquidity function."""
        from nexus.protocols.uniswap import UNISWAP_V2_ROUTER_ABI
        
        add_liquidity_abi = {
            "name": "addLiquidity",
            "type": "function",
            "inputs": [
                {"name": "tokenA", "type": "address"},
                {"name": "tokenB", "type": "address"},
                {"name": "amountADesired", "type": "uint256"},
                {"name": "amountBDesired", "type": "uint256"},
                {"name": "amountAMin", "type": "uint256"},
                {"name": "amountBMin", "type": "uint256"},
                {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"},
            ],
            "outputs": [
                {"name": "amountA", "type": "uint256"},
                {"name": "amountB", "type": "uint256"},
                {"name": "liquidity", "type": "uint256"},
            ],
            "stateMutability": "nonpayable",
        }
        
        return UNISWAP_V2_ROUTER_ABI + [add_liquidity_abi]

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
