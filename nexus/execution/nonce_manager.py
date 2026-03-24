"""
Concurrent Nonce Manager for Nexus AI.

Prevents nonce collision when multiple transactions are sent in rapid
succession from the same wallet.  Thread-safe; reserves nonces
optimistically and releases them on failure.
"""
from __future__ import annotations

import threading
from typing import Optional

from web3 import Web3

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


class NonceManager:
    """
    Per-wallet, per-chain nonce tracker.

    Usage:
        nonce = manager.next(chain, address)
        try:
            send_tx(nonce=nonce, ...)
        except NonceTooLow:
            manager.sync(chain, address)   # force re-read from chain
    """

    def __init__(self):
        self._lock = threading.Lock()
        # { (chain, address_lower): current_nonce }
        self._nonces: dict[tuple, int] = {}

    def next(self, chain: str, address: str, w3: Web3) -> int:
        """Return the next available nonce and increment the counter."""
        key = (chain, address.lower())
        with self._lock:
            if key not in self._nonces:
                self._nonces[key] = w3.eth.get_transaction_count(
                    Web3.to_checksum_address(address), "pending"
                )
            nonce = self._nonces[key]
            self._nonces[key] += 1
            return nonce

    def sync(self, chain: str, address: str, w3: Web3) -> int:
        """Force-refresh nonce from chain (call after nonce errors)."""
        key = (chain, address.lower())
        on_chain = w3.eth.get_transaction_count(
            Web3.to_checksum_address(address), "pending"
        )
        with self._lock:
            self._nonces[key] = on_chain
        logger.info("Nonce synced for %s on %s: %d", address[:10], chain, on_chain)
        return on_chain

    def peek(self, chain: str, address: str) -> Optional[int]:
        key = (chain, address.lower())
        return self._nonces.get(key)


# Singleton
_manager = NonceManager()


def get_nonce_manager() -> NonceManager:
    return _manager
