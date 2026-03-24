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
}

_ENABLED_MAP = {
    "ethereum": Config.CHAIN_ETH,
    "bsc": Config.CHAIN_BSC,
    "polygon": Config.CHAIN_POLYGON,
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
        """Try a single RPC endpoint."""
        if not rpc:
            return None
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
            if CHAIN_INFO[chain]["poa"]:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected():
                return w3
        except Exception:
            pass
        return None

    def _connect(self, chain: str) -> Optional[Web3]:
        """Connect using primary RPC, falling back through alternatives."""
        for rpc in _RPC_FALLBACKS.get(chain, []):
            if not rpc:
                continue
            w3 = self._try_connect(chain, rpc)
            if w3:
                block = w3.eth.block_number
                logger.info(
                    "Connected to %s via %s (block=%s)",
                    CHAIN_INFO[chain]["name"],
                    rpc[:40],
                    block,
                )
                return w3
        logger.warning("All RPC endpoints failed for %s", chain)
        return None

    def _connect_all(self):
        from nexus.utils.gas import GasManager
        from nexus.execution.multicall import MulticallClient

        for chain, enabled in _ENABLED_MAP.items():
            if not enabled:
                continue
            w3 = self._connect(chain)
            if w3:
                self._connections[chain] = w3
                self._gas_managers[chain] = GasManager(w3, chain)
                try:
                    self._multicall_clients[chain] = MulticallClient(w3)
                except Exception:
                    pass

        # Start block poller
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._block_poll_loop, daemon=True, name="block-poller"
        )
        self._poll_thread.start()

    # ── Block polling ─────────────────────────────────────────

    def on_new_block(self, callback: Callable[[dict], None]):
        self._block_listeners.append(callback)

    def _block_poll_loop(self):
        while self._running:
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
                    w3_new = self._connect(chain)
                    if w3_new:
                        self._connections[chain] = w3_new
            time.sleep(BLOCK_POLL_SECONDS)

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
