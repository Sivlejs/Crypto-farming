"""
Blockchain connection manager for Nexus AI.
Manages Web3 connections to Ethereum, BSC, and Polygon.
"""
from __future__ import annotations

from typing import Dict, Optional

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
        "rpc_env": "ETH_RPC_URL",
        "explorer": "https://etherscan.io",
        "poa": False,
    },
    "bsc": {
        "chain_id": 56,
        "name": "BNB Smart Chain",
        "symbol": "BNB",
        "rpc_env": "BSC_RPC_URL",
        "explorer": "https://bscscan.com",
        "poa": True,  # BSC uses PoA middleware
    },
    "polygon": {
        "chain_id": 137,
        "name": "Polygon",
        "symbol": "MATIC",
        "rpc_env": "POLYGON_RPC_URL",
        "explorer": "https://polygonscan.com",
        "poa": True,
    },
}

# RPC endpoints mapped by chain key
_RPC_MAP = {
    "ethereum": Config.ETH_RPC_URL,
    "bsc": Config.BSC_RPC_URL,
    "polygon": Config.POLYGON_RPC_URL,
}

_ENABLED_MAP = {
    "ethereum": Config.CHAIN_ETH,
    "bsc": Config.CHAIN_BSC,
    "polygon": Config.CHAIN_POLYGON,
}


class BlockchainManager:
    """Maintains Web3 connections to all configured chains."""

    def __init__(self):
        self._connections: Dict[str, Web3] = {}
        self._connect_all()

    # ── Connection setup ──────────────────────────────────────

    def _connect(self, chain: str) -> Optional[Web3]:
        rpc = _RPC_MAP.get(chain, "")
        if not rpc:
            logger.warning("No RPC URL configured for %s", chain)
            return None
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if CHAIN_INFO[chain]["poa"]:
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected():
                block = w3.eth.block_number
                logger.info(
                    "Connected to %s (chain_id=%s, block=%s)",
                    CHAIN_INFO[chain]["name"],
                    CHAIN_INFO[chain]["chain_id"],
                    block,
                )
                return w3
            else:
                logger.warning("Could not connect to %s at %s", chain, rpc)
                return None
        except Exception as exc:
            logger.error("Error connecting to %s: %s", chain, exc)
            return None

    def _connect_all(self):
        for chain, enabled in _ENABLED_MAP.items():
            if enabled:
                w3 = self._connect(chain)
                if w3:
                    self._connections[chain] = w3

    # ── Public API ────────────────────────────────────────────

    def get_web3(self, chain: str) -> Optional[Web3]:
        return self._connections.get(chain)

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
        """Return native token balance in ether units."""
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
        w3 = self.get_web3(chain)
        if not w3:
            return 0
        try:
            return w3.eth.block_number
        except Exception:
            return 0

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
