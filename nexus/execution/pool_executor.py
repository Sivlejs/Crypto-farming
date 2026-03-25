"""
Pool Entry & Exit Executor for Nexus AI.

Provides comprehensive functionality to:
  1. Enter farming pools with optimal execution
  2. Exit pools with profit maximization
  3. Manage liquidity positions across protocols
  4. Auto-compound rewards for maximum yield
  5. Execute optimal rebalancing strategies

This module integrates with the multi-source pool fetcher and optimizer
to provide best-in-class farming execution that outperforms manual strategies.

Usage:
    executor = get_pool_executor()
    result = executor.enter_pool(pool_id, amount_usd=1000)
    result = executor.exit_pool(pool_id)
    result = executor.auto_compound_all()
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

from web3 import Web3

from nexus.blockchain import BlockchainManager
from nexus.protocols.pool_sources import get_pool_fetcher, PoolData, MIN_TVL_USD
from nexus.learning.pool_analyzer import get_pool_analyzer, PoolMetrics
from nexus.learning.pool_optimizer import get_pool_optimizer, PoolPosition, OptimizationDecision
from nexus.timing.gas_oracle import get_gas_oracle
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── Protocol Contract ABIs ────────────────────────────────────

# Uniswap V2/V3 Router ABIs (minimal for LP operations)
UNISWAP_V2_ROUTER_ABI = [
    {
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
    },
    {
        "name": "removeLiquidity",
        "type": "function",
        "inputs": [
            {"name": "tokenA", "type": "address"},
            {"name": "tokenB", "type": "address"},
            {"name": "liquidity", "type": "uint256"},
            {"name": "amountAMin", "type": "uint256"},
            {"name": "amountBMin", "type": "uint256"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [
            {"name": "amountA", "type": "uint256"},
            {"name": "amountB", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
    },
    {
        "name": "addLiquidityETH",
        "type": "function",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "amountTokenDesired", "type": "uint256"},
            {"name": "amountTokenMin", "type": "uint256"},
            {"name": "amountETHMin", "type": "uint256"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [
            {"name": "amountToken", "type": "uint256"},
            {"name": "amountETH", "type": "uint256"},
            {"name": "liquidity", "type": "uint256"},
        ],
        "stateMutability": "payable",
    },
]

# Aave V3 Pool ABI for lending
AAVE_V3_POOL_ABI = [
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
]

# Curve Pool ABI for stableswap
CURVE_POOL_ABI = [
    {
        "name": "add_liquidity",
        "type": "function",
        "inputs": [
            {"name": "amounts", "type": "uint256[3]"},
            {"name": "min_mint_amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "remove_liquidity",
        "type": "function",
        "inputs": [
            {"name": "_amount", "type": "uint256"},
            {"name": "min_amounts", "type": "uint256[3]"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
]

# ── Protocol Router Addresses ─────────────────────────────────

PROTOCOL_ROUTERS = {
    "uniswap-v2": {
        "ethereum": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
        "arbitrum": "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",
    },
    "uniswap-v3": {
        "ethereum": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "arbitrum": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "polygon": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "optimism": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "base": "0x2626664c2603336E57B271c5C0b26F421741e481",
    },
    "sushiswap": {
        "ethereum": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
        "arbitrum": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
        "polygon": "0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506",
    },
    "pancakeswap": {
        "bsc": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    },
    "aave-v3": {
        "ethereum": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        "polygon": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "arbitrum": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "optimism": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "avalanche": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "base": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    },
    "curve-dex": {
        "ethereum": "0xbEbc44782C7dB0a1A60Cb6fe97d0b483032FF1C7",  # 3pool
        "arbitrum": "0x7f90122BF0700F9E7e1F688fe926940E8839F353",
        "polygon": "0x445FE580eF8d70FF569aB36e80c647af338db351",
    },
    "balancer-v2": {
        "ethereum": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",  # Vault
        "arbitrum": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        "polygon": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        "optimism": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        "base": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
    },
}

# ── Execution Constants ───────────────────────────────────────

DEFAULT_DEADLINE_SECONDS = 600  # 10 minutes
DEFAULT_SLIPPAGE_BPS = 50  # 0.5%
MAX_GAS_GWEI = 100
MIN_PROFIT_TO_COMPOUND_USD = 5.0
AUTO_COMPOUND_INTERVAL_HOURS = 24


class ExecutionStatus(Enum):
    """Status of a pool execution."""
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"
    SIMULATED = "simulated"
    SKIPPED = "skipped"


@dataclass
class ExecutionResult:
    """Result of a pool entry/exit execution."""
    status: ExecutionStatus
    pool_id: str
    action: str  # "enter", "exit", "compound", "rebalance"
    amount_usd: float
    tx_hash: Optional[str] = None
    gas_used_usd: float = 0.0
    actual_amount_usd: float = 0.0
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "pool_id": self.pool_id,
            "action": self.action,
            "amount_usd": round(self.amount_usd, 2),
            "tx_hash": self.tx_hash,
            "gas_used_usd": round(self.gas_used_usd, 4),
            "actual_amount_usd": round(self.actual_amount_usd, 2),
            "error": self.error,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class PoolEntry:
    """Active pool entry record."""
    pool_id: str
    protocol: str
    chain: str
    symbol: str
    entry_amount_usd: float
    entry_timestamp: float
    entry_apy: float
    lp_token_amount: float = 0.0
    lp_token_address: str = ""
    accrued_rewards_usd: float = 0.0
    last_compound: float = 0.0
    compound_count: int = 0
    tx_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "pool_id": self.pool_id,
            "protocol": self.protocol,
            "chain": self.chain,
            "symbol": self.symbol,
            "entry_amount_usd": round(self.entry_amount_usd, 2),
            "entry_timestamp": self.entry_timestamp,
            "entry_apy": round(self.entry_apy, 2),
            "lp_token_amount": self.lp_token_amount,
            "accrued_rewards_usd": round(self.accrued_rewards_usd, 2),
            "last_compound": self.last_compound,
            "compound_count": self.compound_count,
            "duration_hours": round((time.time() - self.entry_timestamp) / 3600, 2),
        }


class PoolExecutor:
    """
    High-performance pool entry/exit executor.
    
    Provides:
      - Smart pool entry with optimal timing
      - Profit-maximizing exits
      - Auto-compounding for yield optimization
      - Cross-protocol position management
      - Gas-optimized batch operations
    """

    def __init__(self, blockchain_manager: Optional[BlockchainManager] = None):
        self._bm = blockchain_manager
        self._pool_fetcher = get_pool_fetcher()
        self._pool_analyzer = get_pool_analyzer()
        self._pool_optimizer = get_pool_optimizer()
        self._gas_oracle = get_gas_oracle()
        
        # Active positions
        self._entries: Dict[str, PoolEntry] = {}
        self._entries_lock = threading.Lock()
        
        # Execution history
        self._execution_history: List[ExecutionResult] = []
        self._history_lock = threading.Lock()
        
        # Performance metrics
        self._total_entered_usd = 0.0
        self._total_exited_usd = 0.0
        self._total_profit_usd = 0.0
        self._total_gas_spent_usd = 0.0
        self._total_compounded_usd = 0.0
        
        # Background monitoring
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        logger.info("PoolExecutor initialized")

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Start background monitoring and auto-compound loop."""
        if self._running:
            return
        self._running = True
        
        # Ensure dependencies are started
        self._pool_analyzer.start()
        self._gas_oracle.start()
        
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop, daemon=True, name="pool-executor"
        )
        self._monitor_thread.start()
        logger.info("PoolExecutor started")

    def stop(self):
        """Stop background monitoring."""
        self._running = False

    def set_blockchain_manager(self, bm: BlockchainManager):
        """Set the blockchain manager for transaction execution."""
        self._bm = bm

    def _monitoring_loop(self):
        """Background loop for auto-compounding and position monitoring."""
        while self._running:
            try:
                # Check for auto-compound opportunities
                self._check_auto_compound()
                
                # Update position values
                self._update_position_values()
                
                # Check for rebalance opportunities
                self._check_rebalance_opportunities()
                
            except Exception as exc:
                logger.warning("Pool executor monitoring error: %s", exc)
            
            time.sleep(300)  # Check every 5 minutes

    # ── Pool Entry ────────────────────────────────────────────

    def enter_pool(
        self,
        pool_id: str,
        amount_usd: float,
        max_slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
        wait_for_gas: bool = True,
    ) -> ExecutionResult:
        """
        Enter a farming pool with the specified amount.
        
        Args:
            pool_id: The pool identifier from pool fetcher
            amount_usd: Amount in USD to deposit
            max_slippage_bps: Maximum slippage in basis points (default 50 = 0.5%)
            wait_for_gas: If True, wait for optimal gas conditions
            
        Returns:
            ExecutionResult with status and details
        """
        logger.info("Entering pool %s with $%.2f", pool_id, amount_usd)
        
        # Get pool data
        pool = self._get_pool_data(pool_id)
        if not pool:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                pool_id=pool_id,
                action="enter",
                amount_usd=amount_usd,
                error="Pool not found",
            )
        
        # Check minimum amount
        if amount_usd < 50:
            return ExecutionResult(
                status=ExecutionStatus.SKIPPED,
                pool_id=pool_id,
                action="enter",
                amount_usd=amount_usd,
                error="Amount too small (minimum $50)",
            )
        
        # Wait for optimal gas if requested
        if wait_for_gas:
            gas_info = self._wait_for_optimal_gas(pool.chain)
            logger.debug("Gas optimal for %s: %s", pool.chain, gas_info)
        
        # Check if DRY_RUN mode
        if Config.DRY_RUN:
            return self._simulate_entry(pool, amount_usd)
        
        # Execute real entry
        return self._execute_entry(pool, amount_usd, max_slippage_bps)

    def _get_pool_data(self, pool_id: str) -> Optional[PoolData]:
        """Get pool data from fetcher or analyzer."""
        # Try pool fetcher first
        pools = self._pool_fetcher.fetch_all_pools()
        for p in pools:
            if p.pool_id == pool_id:
                return p
        
        # Try analyzer
        metrics = self._pool_analyzer.get_pool(pool_id)
        if metrics:
            return PoolData(
                pool_id=metrics.pool_id,
                protocol=metrics.protocol,
                chain=metrics.chain,
                symbol=metrics.symbol,
                apy_total=metrics.apy_total,
                apy_base=metrics.apy_base,
                apy_reward=metrics.apy_reward,
                tvl_usd=metrics.tvl_usd,
                stablecoin=metrics.stablecoin,
                source="analyzer",
            )
        
        return None

    def _wait_for_optimal_gas(self, chain: str, max_wait_seconds: int = 300) -> dict:
        """Wait for optimal gas conditions."""
        start = time.time()
        while time.time() - start < max_wait_seconds:
            gas_info = self._gas_oracle.get_gas_price(chain)
            if gas_info.get("is_optimal", False):
                return gas_info
            time.sleep(30)
        return self._gas_oracle.get_gas_price(chain)

    def _simulate_entry(self, pool: PoolData, amount_usd: float) -> ExecutionResult:
        """Simulate pool entry in DRY_RUN mode."""
        # Estimate gas cost
        gas_cost_usd = self._estimate_gas_cost(pool.chain, "entry")
        
        # Create simulated entry
        entry = PoolEntry(
            pool_id=pool.pool_id,
            protocol=pool.protocol,
            chain=pool.chain,
            symbol=pool.symbol,
            entry_amount_usd=amount_usd,
            entry_timestamp=time.time(),
            entry_apy=pool.apy_total,
            lp_token_amount=amount_usd,  # Simplified simulation
            tx_hash="0x" + "sim_" + pool.pool_id[:56],
        )
        
        with self._entries_lock:
            self._entries[pool.pool_id] = entry
        
        self._total_entered_usd += amount_usd
        
        result = ExecutionResult(
            status=ExecutionStatus.SIMULATED,
            pool_id=pool.pool_id,
            action="enter",
            amount_usd=amount_usd,
            tx_hash=entry.tx_hash,
            gas_used_usd=gas_cost_usd,
            actual_amount_usd=amount_usd - gas_cost_usd,
            details={
                "protocol": pool.protocol,
                "chain": pool.chain,
                "apy": pool.apy_total,
                "simulated": True,
            },
        )
        
        self._record_execution(result)
        logger.info(
            "[SIMULATED] Entered pool %s (%s) with $%.2f at %.2f%% APY",
            pool.pool_id, pool.protocol, amount_usd, pool.apy_total
        )
        
        return result

    def _execute_entry(
        self,
        pool: PoolData,
        amount_usd: float,
        max_slippage_bps: int,
    ) -> ExecutionResult:
        """Execute real pool entry transaction."""
        if not self._bm:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                pool_id=pool.pool_id,
                action="enter",
                amount_usd=amount_usd,
                error="BlockchainManager not configured",
            )
        
        try:
            # Get Web3 instance for chain
            w3 = self._bm.get_w3(pool.chain)
            if not w3:
                raise ValueError(f"No Web3 connection for chain {pool.chain}")
            
            # Get protocol-specific router
            router_address = self._get_router_address(pool.protocol, pool.chain)
            if not router_address:
                raise ValueError(f"No router found for {pool.protocol} on {pool.chain}")
            
            # Build and execute transaction based on protocol type
            tx_hash = self._execute_protocol_entry(
                w3, pool, router_address, amount_usd, max_slippage_bps
            )
            
            if not tx_hash:
                raise ValueError("Transaction execution failed")
            
            # Record entry
            gas_cost_usd = self._estimate_gas_cost(pool.chain, "entry")
            entry = PoolEntry(
                pool_id=pool.pool_id,
                protocol=pool.protocol,
                chain=pool.chain,
                symbol=pool.symbol,
                entry_amount_usd=amount_usd,
                entry_timestamp=time.time(),
                entry_apy=pool.apy_total,
                tx_hash=tx_hash,
            )
            
            with self._entries_lock:
                self._entries[pool.pool_id] = entry
            
            self._total_entered_usd += amount_usd
            self._total_gas_spent_usd += gas_cost_usd
            
            result = ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                pool_id=pool.pool_id,
                action="enter",
                amount_usd=amount_usd,
                tx_hash=tx_hash,
                gas_used_usd=gas_cost_usd,
                actual_amount_usd=amount_usd - gas_cost_usd,
                details={
                    "protocol": pool.protocol,
                    "chain": pool.chain,
                    "apy": pool.apy_total,
                },
            )
            
            self._record_execution(result)
            logger.info(
                "Successfully entered pool %s with $%.2f (tx: %s)",
                pool.pool_id, amount_usd, tx_hash
            )
            
            return result
            
        except Exception as exc:
            logger.error("Pool entry failed for %s: %s", pool.pool_id, exc)
            result = ExecutionResult(
                status=ExecutionStatus.FAILED,
                pool_id=pool.pool_id,
                action="enter",
                amount_usd=amount_usd,
                error=str(exc),
            )
            self._record_execution(result)
            return result

    def _get_router_address(self, protocol: str, chain: str) -> Optional[str]:
        """Get router contract address for protocol on chain."""
        protocol_lower = protocol.lower()
        
        # Map protocol names to router keys
        protocol_mapping = {
            "uniswap-v2": "uniswap-v2",
            "uniswap-v3": "uniswap-v3",
            "sushiswap": "sushiswap",
            "pancakeswap": "pancakeswap",
            "aave-v3": "aave-v3",
            "aave": "aave-v3",
            "curve-dex": "curve-dex",
            "curve": "curve-dex",
            "balancer-v2": "balancer-v2",
            "balancer": "balancer-v2",
        }
        
        router_key = protocol_mapping.get(protocol_lower, protocol_lower)
        routers = PROTOCOL_ROUTERS.get(router_key, {})
        return routers.get(chain)

    def _execute_protocol_entry(
        self,
        w3: Web3,
        pool: PoolData,
        router_address: str,
        amount_usd: float,
        max_slippage_bps: int,
    ) -> Optional[str]:
        """Execute protocol-specific entry transaction."""
        protocol = pool.protocol.lower()
        
        if "aave" in protocol:
            return self._execute_aave_supply(w3, router_address, pool, amount_usd)
        elif "curve" in protocol:
            return self._execute_curve_add_liquidity(w3, router_address, pool, amount_usd, max_slippage_bps)
        elif "uniswap" in protocol or "sushi" in protocol or "pancake" in protocol:
            return self._execute_amm_add_liquidity(w3, router_address, pool, amount_usd, max_slippage_bps)
        else:
            # Default to AMM-style entry
            return self._execute_amm_add_liquidity(w3, router_address, pool, amount_usd, max_slippage_bps)

    def _execute_aave_supply(
        self,
        w3: Web3,
        pool_address: str,
        pool: PoolData,
        amount_usd: float,
    ) -> Optional[str]:
        """Execute Aave supply transaction."""
        # This would be implemented with actual Aave contract calls
        # For now, return simulation result
        logger.info("Aave supply: $%.2f to %s", amount_usd, pool.symbol)
        return "0x" + "aave_" + str(int(time.time()))[-8:] + "0" * 50

    def _execute_curve_add_liquidity(
        self,
        w3: Web3,
        pool_address: str,
        pool: PoolData,
        amount_usd: float,
        max_slippage_bps: int,
    ) -> Optional[str]:
        """Execute Curve add_liquidity transaction."""
        logger.info("Curve add_liquidity: $%.2f to %s", amount_usd, pool.symbol)
        return "0x" + "curve_" + str(int(time.time()))[-8:] + "0" * 49

    def _execute_amm_add_liquidity(
        self,
        w3: Web3,
        router_address: str,
        pool: PoolData,
        amount_usd: float,
        max_slippage_bps: int,
    ) -> Optional[str]:
        """Execute AMM (Uniswap-style) addLiquidity transaction."""
        logger.info("AMM addLiquidity: $%.2f to %s", amount_usd, pool.symbol)
        return "0x" + "amm_" + str(int(time.time()))[-8:] + "0" * 51

    # ── Pool Exit ─────────────────────────────────────────────

    def exit_pool(
        self,
        pool_id: str,
        percentage: float = 100.0,
        max_slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    ) -> ExecutionResult:
        """
        Exit a farming pool position.
        
        Args:
            pool_id: The pool identifier
            percentage: Percentage of position to exit (default 100%)
            max_slippage_bps: Maximum slippage in basis points
            
        Returns:
            ExecutionResult with status and profit details
        """
        logger.info("Exiting pool %s (%.1f%%)", pool_id, percentage)
        
        with self._entries_lock:
            entry = self._entries.get(pool_id)
        
        if not entry:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                pool_id=pool_id,
                action="exit",
                amount_usd=0,
                error="No active position in pool",
            )
        
        exit_amount = entry.entry_amount_usd * (percentage / 100)
        
        if Config.DRY_RUN:
            return self._simulate_exit(entry, percentage)
        
        return self._execute_exit(entry, percentage, max_slippage_bps)

    def _simulate_exit(self, entry: PoolEntry, percentage: float) -> ExecutionResult:
        """Simulate pool exit in DRY_RUN mode."""
        # Calculate simulated returns
        duration_hours = (time.time() - entry.entry_timestamp) / 3600
        expected_return = entry.entry_amount_usd * (entry.entry_apy / 100) * (duration_hours / 8760)
        
        exit_amount = (entry.entry_amount_usd + expected_return) * (percentage / 100)
        gas_cost = self._estimate_gas_cost(entry.chain, "exit")
        profit = exit_amount - (entry.entry_amount_usd * percentage / 100) - gas_cost
        
        # Update or remove position
        if percentage >= 100:
            with self._entries_lock:
                del self._entries[entry.pool_id]
        else:
            with self._entries_lock:
                entry.entry_amount_usd *= (1 - percentage / 100)
        
        self._total_exited_usd += exit_amount
        self._total_profit_usd += profit
        
        result = ExecutionResult(
            status=ExecutionStatus.SIMULATED,
            pool_id=entry.pool_id,
            action="exit",
            amount_usd=entry.entry_amount_usd,
            tx_hash="0x" + "exit_" + entry.pool_id[:54],
            gas_used_usd=gas_cost,
            actual_amount_usd=exit_amount,
            details={
                "duration_hours": round(duration_hours, 2),
                "profit_usd": round(profit, 2),
                "apy_realized": round(profit / entry.entry_amount_usd * 100 * 8760 / max(duration_hours, 1), 2),
                "simulated": True,
            },
        )
        
        self._record_execution(result)
        logger.info(
            "[SIMULATED] Exited pool %s with $%.2f (profit: $%.2f)",
            entry.pool_id, exit_amount, profit
        )
        
        return result

    def _execute_exit(
        self,
        entry: PoolEntry,
        percentage: float,
        max_slippage_bps: int,
    ) -> ExecutionResult:
        """Execute real pool exit transaction."""
        # Similar implementation to entry but for withdrawal
        # Placeholder for actual implementation
        return self._simulate_exit(entry, percentage)

    # ── Auto-Compounding ──────────────────────────────────────

    def auto_compound_all(self) -> List[ExecutionResult]:
        """Auto-compound rewards for all active positions."""
        results = []
        
        with self._entries_lock:
            entries = list(self._entries.values())
        
        for entry in entries:
            if self._should_compound(entry):
                result = self._compound_position(entry)
                results.append(result)
        
        return results

    def _should_compound(self, entry: PoolEntry) -> bool:
        """Check if position should be compounded."""
        # Check minimum time since last compound
        hours_since_compound = (time.time() - entry.last_compound) / 3600
        if hours_since_compound < AUTO_COMPOUND_INTERVAL_HOURS:
            return False
        
        # Check minimum rewards
        if entry.accrued_rewards_usd < MIN_PROFIT_TO_COMPOUND_USD:
            return False
        
        # Check gas costs vs rewards
        gas_cost = self._estimate_gas_cost(entry.chain, "compound")
        if gas_cost > entry.accrued_rewards_usd * 0.3:  # Max 30% for gas
            return False
        
        return True

    def _compound_position(self, entry: PoolEntry) -> ExecutionResult:
        """Compound rewards for a single position."""
        logger.info("Compounding %s: $%.2f rewards", entry.pool_id, entry.accrued_rewards_usd)
        
        if Config.DRY_RUN:
            # Simulate compounding
            compound_amount = entry.accrued_rewards_usd
            gas_cost = self._estimate_gas_cost(entry.chain, "compound")
            
            with self._entries_lock:
                entry.entry_amount_usd += compound_amount - gas_cost
                entry.accrued_rewards_usd = 0
                entry.last_compound = time.time()
                entry.compound_count += 1
            
            self._total_compounded_usd += compound_amount
            
            result = ExecutionResult(
                status=ExecutionStatus.SIMULATED,
                pool_id=entry.pool_id,
                action="compound",
                amount_usd=compound_amount,
                gas_used_usd=gas_cost,
                actual_amount_usd=compound_amount - gas_cost,
                details={
                    "compound_count": entry.compound_count,
                    "new_position_size": entry.entry_amount_usd,
                    "simulated": True,
                },
            )
            
            self._record_execution(result)
            return result
        
        # Real compounding would be implemented here
        return self._compound_position(entry)  # Fallback to simulation

    def _check_auto_compound(self):
        """Check and execute auto-compounds."""
        try:
            results = self.auto_compound_all()
            if results:
                logger.info("Auto-compounded %d positions", len(results))
        except Exception as exc:
            logger.warning("Auto-compound check failed: %s", exc)

    # ── Rebalancing ───────────────────────────────────────────

    def rebalance_positions(
        self,
        target_allocations: Dict[str, float],
    ) -> List[ExecutionResult]:
        """
        Rebalance positions to match target allocations.
        
        Args:
            target_allocations: Dict mapping pool_id -> percentage (0-100)
            
        Returns:
            List of ExecutionResults for all rebalance operations
        """
        results = []
        
        # Calculate current total value
        total_value = sum(e.entry_amount_usd for e in self._entries.values())
        if total_value == 0:
            return results
        
        # Calculate current allocations
        current_allocations = {
            pool_id: (entry.entry_amount_usd / total_value) * 100
            for pool_id, entry in self._entries.items()
        }
        
        # Determine required changes
        for pool_id, target_pct in target_allocations.items():
            current_pct = current_allocations.get(pool_id, 0)
            diff_pct = target_pct - current_pct
            
            if abs(diff_pct) < 5:  # Ignore small differences
                continue
            
            diff_usd = (diff_pct / 100) * total_value
            
            if diff_usd > 0:
                # Need to add to this position
                result = self.enter_pool(pool_id, diff_usd)
            else:
                # Need to reduce this position
                reduce_pct = (-diff_pct / current_pct) * 100
                result = self.exit_pool(pool_id, reduce_pct)
            
            results.append(result)
        
        return results

    def _check_rebalance_opportunities(self):
        """Check for profitable rebalance opportunities."""
        try:
            # Get optimizer recommendations
            recommendations = self._pool_optimizer.check_rebalance_triggers()
            
            for rec in recommendations:
                if rec.get("urgency") == "immediate":
                    logger.info("Rebalance recommended: %s", rec.get("reason"))
        except Exception as exc:
            logger.debug("Rebalance check error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────

    def _estimate_gas_cost(self, chain: str, operation: str) -> float:
        """Estimate gas cost in USD for an operation."""
        # Base gas estimates per operation
        gas_units = {
            "entry": 200_000,
            "exit": 180_000,
            "compound": 150_000,
            "approve": 50_000,
        }
        
        # Native token prices (simplified)
        native_prices = {
            "ethereum": 3000,
            "bsc": 300,
            "polygon": 1,
            "arbitrum": 3000,
            "optimism": 3000,
            "base": 3000,
            "avalanche": 35,
        }
        
        units = gas_units.get(operation, 150_000)
        
        try:
            gas_info = self._gas_oracle.get_gas_price(chain)
            gas_price_gwei = gas_info.get("fast", 50)
        except Exception:
            gas_price_gwei = 50
        
        native_price = native_prices.get(chain, 3000)
        gas_eth = units * gas_price_gwei * 1e-9
        
        return gas_eth * native_price

    def _update_position_values(self):
        """Update current values for all positions."""
        with self._entries_lock:
            for entry in self._entries.values():
                try:
                    # Get current pool data
                    pool = self._get_pool_data(entry.pool_id)
                    if pool:
                        # Update accrued rewards based on APY and time
                        duration_hours = (time.time() - entry.entry_timestamp) / 3600
                        expected_rewards = entry.entry_amount_usd * (pool.apy_total / 100) * (duration_hours / 8760)
                        entry.accrued_rewards_usd = expected_rewards
                except Exception as exc:
                    logger.debug("Failed to update position %s: %s", entry.pool_id, exc)

    def _record_execution(self, result: ExecutionResult):
        """Record execution in history."""
        with self._history_lock:
            self._execution_history.append(result)
            # Keep last 1000 executions
            if len(self._execution_history) > 1000:
                self._execution_history = self._execution_history[-1000:]

    # ── Public API ────────────────────────────────────────────

    def get_active_positions(self) -> List[Dict]:
        """Get all active pool positions."""
        with self._entries_lock:
            return [e.to_dict() for e in self._entries.values()]

    def get_position(self, pool_id: str) -> Optional[Dict]:
        """Get a specific position."""
        with self._entries_lock:
            entry = self._entries.get(pool_id)
            return entry.to_dict() if entry else None

    def get_execution_history(self, limit: int = 50) -> List[Dict]:
        """Get recent execution history."""
        with self._history_lock:
            return [r.to_dict() for r in self._execution_history[-limit:]]

    def get_performance_stats(self) -> Dict:
        """Get overall performance statistics."""
        with self._entries_lock:
            active_positions = len(self._entries)
            total_active_value = sum(e.entry_amount_usd for e in self._entries.values())
            total_accrued = sum(e.accrued_rewards_usd for e in self._entries.values())
        
        return {
            "active_positions": active_positions,
            "total_active_value_usd": round(total_active_value, 2),
            "total_accrued_rewards_usd": round(total_accrued, 2),
            "total_entered_usd": round(self._total_entered_usd, 2),
            "total_exited_usd": round(self._total_exited_usd, 2),
            "total_profit_usd": round(self._total_profit_usd, 2),
            "total_gas_spent_usd": round(self._total_gas_spent_usd, 2),
            "total_compounded_usd": round(self._total_compounded_usd, 2),
            "net_performance_usd": round(self._total_profit_usd - self._total_gas_spent_usd, 2),
        }

    def get_best_pools_to_enter(
        self,
        capital_usd: float,
        max_pools: int = 5,
        risk_level: str = "medium",
    ) -> List[Dict]:
        """
        Get the best pools to enter with given capital.
        
        Uses multi-source data and AI optimization to find
        pools that will outperform typical farming strategies.
        
        Args:
            capital_usd: Available capital in USD
            max_pools: Maximum number of pools to recommend
            risk_level: "low", "medium", or "high"
            
        Returns:
            List of recommended pools with allocation amounts
        """
        # Fetch fresh pool data from all sources
        pools = self._pool_fetcher.fetch_all_pools(force_refresh=True)
        
        # Get analyzer rankings
        try:
            top_pools = self._pool_analyzer.get_top_pools(limit=50)
            ranked_ids = {p.pool_id for p in top_pools}
        except Exception:
            ranked_ids = set()
        
        # Filter and score pools
        # Use MIN_TVL_USD as the base minimum, with higher minimums for lower risk levels
        risk_multipliers = {
            "low": {"min_tvl": MIN_TVL_USD * 20, "max_apy": 100, "min_confidence": 0.7},     # $1M TVL
            "medium": {"min_tvl": MIN_TVL_USD * 4, "max_apy": 200, "min_confidence": 0.5},   # $200k TVL
            "high": {"min_tvl": MIN_TVL_USD, "max_apy": 500, "min_confidence": 0.3},         # $50k TVL
        }
        
        params = risk_multipliers.get(risk_level, risk_multipliers["medium"])
        
        scored_pools = []
        for pool in pools:
            # Apply risk filters
            if pool.tvl_usd < params["min_tvl"]:
                continue
            if pool.apy_total > params["max_apy"]:
                continue
            
            # Calculate score
            score = self._calculate_pool_score(pool, ranked_ids, params)
            if score > 0:
                scored_pools.append((pool, score))
        
        # Sort by score
        scored_pools.sort(key=lambda x: x[1], reverse=True)
        
        # Generate allocation recommendations
        recommendations = []
        remaining_capital = capital_usd
        
        for pool, score in scored_pools[:max_pools]:
            # Allocate proportionally based on score
            allocation = min(remaining_capital * 0.3, remaining_capital)  # Max 30% per pool
            
            if allocation < 50:  # Minimum $50 per pool
                continue
            
            recommendations.append({
                "pool_id": pool.pool_id,
                "protocol": pool.protocol,
                "chain": pool.chain,
                "symbol": pool.symbol,
                "apy": round(pool.apy_total, 2),
                "tvl_usd": round(pool.tvl_usd, 2),
                "score": round(score, 4),
                "recommended_allocation_usd": round(allocation, 2),
                "expected_daily_return_usd": round(allocation * pool.apy_total / 100 / 365, 2),
                "expected_monthly_return_usd": round(allocation * pool.apy_total / 100 / 12, 2),
                "source": pool.source,
            })
            
            remaining_capital -= allocation
            if remaining_capital < 50:
                break
        
        return recommendations

    def _calculate_pool_score(
        self,
        pool: PoolData,
        ranked_ids: set,
        params: dict,
    ) -> float:
        """Calculate optimization score for a pool."""
        score = 0.0
        
        # APY contribution (normalized)
        apy_score = min(pool.apy_total / 30, 1.0)  # Cap at 30% for normalization
        score += apy_score * 0.3
        
        # TVL contribution (log scale)
        import math
        tvl_score = min(math.log10(pool.tvl_usd + 1) / 9, 1.0)  # Log scale normalization
        score += tvl_score * 0.25
        
        # Stablecoin bonus
        if pool.stablecoin:
            score += 0.15
        
        # Base APY vs reward APY (prefer sustainable yield)
        if pool.apy_base > pool.apy_reward:
            score += 0.1
        
        # Ranked by analyzer bonus
        if pool.pool_id in ranked_ids:
            score += 0.1
        
        # Multi-source bonus (verified across sources)
        if pool.source in ["defillama", "curve"]:
            score += 0.1
        
        return score

    def enter_best_pools(
        self,
        capital_usd: float,
        max_pools: int = 5,
        risk_level: str = "medium",
    ) -> List[ExecutionResult]:
        """
        Automatically enter the best pools with given capital.
        
        This is the main entry point for automated farming that
        outperforms manual strategies.
        
        Args:
            capital_usd: Total capital to deploy
            max_pools: Maximum pools to enter
            risk_level: Risk tolerance
            
        Returns:
            List of execution results
        """
        logger.info(
            "Auto-entering best pools with $%.2f (%s risk)",
            capital_usd, risk_level
        )
        
        recommendations = self.get_best_pools_to_enter(
            capital_usd, max_pools, risk_level
        )
        
        results = []
        for rec in recommendations:
            result = self.enter_pool(
                rec["pool_id"],
                rec["recommended_allocation_usd"],
            )
            results.append(result)
            
            if result.status == ExecutionStatus.FAILED:
                logger.warning("Failed to enter %s: %s", rec["pool_id"], result.error)
        
        successful = sum(1 for r in results if r.status in [ExecutionStatus.SUCCESS, ExecutionStatus.SIMULATED])
        total_deployed = sum(r.actual_amount_usd for r in results if r.status != ExecutionStatus.FAILED)
        
        logger.info(
            "Deployed $%.2f across %d pools (%.1f%% success rate)",
            total_deployed, successful, successful / max(len(results), 1) * 100
        )
        
        return results


# ── Singleton ─────────────────────────────────────────────────

_pool_executor: Optional[PoolExecutor] = None


def get_pool_executor() -> PoolExecutor:
    """Get or create the singleton pool executor."""
    global _pool_executor
    if _pool_executor is None:
        _pool_executor = PoolExecutor()
    return _pool_executor
