"""
Blockchain connection manager for Nexus AI.

Upgrades over v1:
  • Multi-endpoint failover per chain (primary + fallback RPCs)
  • Block-polling loop that fires registered callbacks on every new block
  • Per-chain GasManager integration
  • MultiCall client per chain for batch reads
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Chain metadata
CHAIN_INFO: Dict[str, dict] = {
    "ethereum": {
        "chain_id": 1,
        "name": "Ethereum",
        "symbol": "ETH",
        "explorer": "https://etherscan.io",
        "poa": False,
    },
    "bsc": {
        "chain_id": 56,
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "explorer": "https://bscscan.com",
        "poa": True,
    },
    "polygon": {
        "chain_id": 137,
        "name": "Polygon",
        "symbol": "MATIC",
        "explorer": "https://polygonscan.com",
        "poa": True,
    },
    "arbitrum": {
        "chain_id": 42161,
        "name": "Arbitrum One",
        "symbol": "ETH",
        "explorer": "https://arbiscan.io",
        "poa": False,
    },
    "optimism": {
        "chain_id": 10,
        "name": "Optimism",
        "symbol": "ETH",
        "explorer": "https://optimistic.etherscan.io",
        "poa": False,
    },
    "base": {
        "chain_id": 8453,
        "name": "Base",
        "symbol": "ETH",
        "explorer": "https://basescan.org",
        "poa": False,
    },
    "avalanche": {
        "chain_id": 43114,
        "name": "Avalanche C-Chain",
        "symbol": "AVAX",
        "explorer": "https://snowtrace.io",
        "poa": False,
    },
}

# Primary + fallback RPC endpoints per chain
_RPC_FALLBACKS: Dict[str, List[str]] = {
    "ethereum": [
        Config.ETH_RPC_URL,
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://cloudflare-eth.com",
    ],
    "bsc": [
        Config.BSC_RPC_URL,
        "https://bsc-dataseed1.binance.org/",
        "https://bsc-dataseed2.binance.org/",
        "https://rpc.ankr.com/bsc",
    ],
    "polygon": [
        Config.POLYGON_RPC_URL,
        "https://polygon-rpc.com/",
        "https://rpc.ankr.com/polygon",
        "https://matic-mainnet.chainstacklabs.com",
    ],
    "arbitrum": [
        Config.ARBITRUM_RPC_URL,
        "https://arb1.arbitrum.io/rpc",
        "https://rpc.ankr.com/arbitrum",
        "https://arbitrum.llamarpc.com",
    ],
    "optimism": [
        Config.OPTIMISM_RPC_URL,
        "https://mainnet.optimism.io",
        "https://rpc.ankr.com/optimism",
        "https://optimism.llamarpc.com",
    ],
    "base": [
        Config.BASE_RPC_URL,
        "https://mainnet.base.org",
        "https://rpc.ankr.com/base",
        "https://base.llamarpc.com",
    ],
    "avalanche": [
        Config.AVALANCHE_RPC_URL,
        "https://api.avax.network/ext/bc/C/rpc",
        "https://rpc.ankr.com/avalanche",
        "https://avalanche.public-rpc.com",
    ],
}

_ENABLED_MAP = {
    "ethereum":  Config.CHAIN_ETH,
    "bsc":       Config.CHAIN_BSC,
    "polygon":   Config.CHAIN_POLYGON,
    "arbitrum":  Config.CHAIN_ARBITRUM,
    "optimism":  Config.CHAIN_OPTIMISM,
    "base":      Config.CHAIN_BASE,
    "avalanche": Config.CHAIN_AVALANCHE,
}

# Block poll interval when WebSocket is not available
BLOCK_POLL_SECONDS = 3


class BlockchainManager:
    """
    Maintains Web3 connections to all configured chains.

    Upgrades:
      • Tries multiple fallback RPC endpoints automatically
      • Runs a background block-poller and fires registered callbacks
      • Exposes per-chain GasManager and MulticallClient
    """

    def __init__(self):
        self._connections: Dict[str, Web3] = {}
        self._gas_managers: Dict[str, object] = {}
        self._multicall_clients: Dict[str, object] = {}
        self._block_listeners: List[Callable[[dict], None]] = []
        self._latest_blocks: Dict[str, int] = {}
        self._poll_thread: Optional[threading.Thread] = None
        self._running = False
        self._connect_all()

    # ── Connection setup ──────────────────────────────────────

    def _try_connect(self, chain: str, rpc: str) -> Optional[Web3]:
        """Try a single RPC endpoint with graceful error handling."""
        if not rpc:
            return None
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if CHAIN_INFO[chain]["poa"]:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            # Simple connectivity check - is_connected() is lightweight
            if w3.is_connected():
                return w3
        except Exception as exc:
            logger.debug("RPC connection attempt failed for %s (%s): %s", chain, rpc[:30], exc)
        return None

    def _connect(self, chain: str) -> Optional[Web3]:
        """Connect using primary RPC, falling back through alternatives."""
        for rpc in _RPC_FALLBACKS.get(chain, []):
            if not rpc:
                continue
            w3 = self._try_connect(chain, rpc)
            if w3:
                # Try to get block number, but don't fail if RPC rejects it
                block = "unknown"
                try:
                    block = w3.eth.block_number
                except Exception as exc:
                    # RPC might be connected but rejecting some requests (rate limited)
                    logger.debug("Block number fetch failed for %s (but connected): %s", chain, exc)
                
                logger.info(
                    "Connected to %s via %s (block=%s)",
                    CHAIN_INFO[chain]["name"],
                    rpc[:40],
                    block,
                )
                return w3
        logger.warning("All RPC endpoints failed for %s - will retry in background", chain)
        return None

    def _connect_all(self):
        from nexus.utils.gas import GasManager
        from nexus.execution.multicall import MulticallClient

        connected_count = 0
        failed_chains = []

        for chain, enabled in _ENABLED_MAP.items():
            if not enabled:
                continue
            try:
                w3 = self._connect(chain)
                if w3:
                    self._connections[chain] = w3
                    try:
                        self._gas_managers[chain] = GasManager(w3, chain)
                    except Exception as exc:
                        logger.debug("GasManager init failed for %s: %s", chain, exc)
                    try:
                        self._multicall_clients[chain] = MulticallClient(w3)
                    except Exception as exc:
                        logger.debug("MulticallClient init failed for %s: %s", chain, exc)
                    connected_count += 1
                else:
                    failed_chains.append(chain)
            except Exception as exc:
                logger.warning("Chain %s connection error: %s", chain, exc)
                failed_chains.append(chain)

        if failed_chains:
            logger.warning(
                "Some chains failed to connect: %s (will retry in background)",
                failed_chains,
            )

        # Log connection summary - don't fail if no chains connected
        if connected_count > 0:
            logger.info(
                "BlockchainManager initialized: %d/%d chains connected",
                connected_count,
                connected_count + len(failed_chains),
            )
        else:
            logger.warning(
                "BlockchainManager: No chains connected yet. "
                "Bot will operate in limited mode until RPCs are available."
            )

        # Start block poller (will retry failed chains)
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._block_poll_loop, daemon=True, name="block-poller"
        )
        self._poll_thread.start()

    # ── Block polling ─────────────────────────────────────────

    def on_new_block(self, callback: Callable[[dict], None]):
        self._block_listeners.append(callback)

    def _block_poll_loop(self):
        """Background block poller with automatic reconnection for failed chains."""
        reconnect_interval = 30  # Seconds between reconnection attempts
        last_reconnect_attempt = 0
        
        while self._running:
            now = time.time()
            
            # Poll connected chains
            for chain, w3 in list(self._connections.items()):
                try:
                    block_num = w3.eth.block_number
                    if self._latest_blocks.get(chain) != block_num:
                        self._latest_blocks[chain] = block_num
                        info = {
                            "chain":  chain,
                            "number": block_num,
                        }
                        for cb in self._block_listeners:
                            try:
                                cb(info)
                            except Exception:
                                pass
                except Exception as exc:
                    logger.debug("Block poll error on %s: %s", chain, exc)
                    # Try to reconnect
                    try:
                        w3_new = self._connect(chain)
                        if w3_new:
                            self._connections[chain] = w3_new
                    except Exception as reconn_exc:
                        logger.debug("Reconnection failed for %s: %s", chain, reconn_exc)

            # Periodically try to reconnect failed chains
            if now - last_reconnect_attempt > reconnect_interval:
                last_reconnect_attempt = now
                self._retry_failed_chains()

            time.sleep(BLOCK_POLL_SECONDS)

    def _retry_failed_chains(self):
        """Try to reconnect chains that previously failed."""
        from nexus.utils.gas import GasManager
        from nexus.execution.multicall import MulticallClient
        
        for chain, enabled in _ENABLED_MAP.items():
            if not enabled:
                continue
            if chain in self._connections:
                continue  # Already connected
            
            try:
                w3 = self._connect(chain)
                if w3:
                    self._connections[chain] = w3
                    try:
                        self._gas_managers[chain] = GasManager(w3, chain)
                    except Exception:
                        pass
                    try:
                        self._multicall_clients[chain] = MulticallClient(w3)
                    except Exception:
                        pass
                    logger.info("Successfully reconnected to %s", chain)
            except Exception as exc:
                logger.debug("Retry connection failed for %s: %s", chain, exc)

    def stop(self):
        self._running = False

    # ── Public API ────────────────────────────────────────────

    def get_web3(self, chain: str) -> Optional[Web3]:
        return self._connections.get(chain)

    def get_gas_manager(self, chain: str):
        return self._gas_managers.get(chain)

    def get_multicall(self, chain: str):
        return self._multicall_clients.get(chain)

    def connected_chains(self) -> list[str]:
        return list(self._connections.keys())

    def get_gas_price_gwei(self, chain: str) -> float:
        w3 = self.get_web3(chain)
        if not w3:
            return 0.0
        try:
            return float(Web3.from_wei(w3.eth.gas_price, "gwei"))
        except Exception:
            return 0.0

    def get_native_balance(self, chain: str, address: str) -> float:
        w3 = self.get_web3(chain)
        if not w3 or not address:
            return 0.0
        try:
            raw = w3.eth.get_balance(Web3.to_checksum_address(address))
            return float(Web3.from_wei(raw, "ether"))
        except Exception as exc:
            logger.error("Error getting balance on %s: %s", chain, exc)
            return 0.0

    def get_block_number(self, chain: str) -> int:
        return self._latest_blocks.get(chain, 0)

    def status(self) -> dict:
        result = {}
        for chain, info in CHAIN_INFO.items():
            if not _ENABLED_MAP.get(chain, False):
                result[chain] = {"enabled": False}
                continue
            w3 = self._connections.get(chain)
            connected = w3 is not None and w3.is_connected()
            result[chain] = {
                "enabled": True,
                "connected": connected,
                "name": info["name"],
                "symbol": info["symbol"],
                "chain_id": info["chain_id"],
                "block": self.get_block_number(chain) if connected else 0,
                "gas_gwei": self.get_gas_price_gwei(chain) if connected else 0,
            }
        return result


# Singleton
_manager: Optional[BlockchainManager] = None


def get_blockchain_manager() -> BlockchainManager:
    global _manager
    if _manager is None:
        _manager = BlockchainManager()
    return _manager
