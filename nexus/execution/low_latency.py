"""
Low-Latency Blockchain Connection Manager for Nexus AI.

Provides optimized blockchain connectivity for fast, reliable trades:
  1. WebSocket connections for real-time block/mempool data
  2. Connection pooling with intelligent failover
  3. Priority RPC selection based on latency measurements
  4. Health monitoring and automatic reconnection
  5. Request batching and caching for efficiency

This module extends the base BlockchainManager with performance optimizations
designed for high-frequency trading scenarios.
"""
from __future__ import annotations

import asyncio
import heapq
import json
import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Latency measurement settings
LATENCY_WINDOW_SIZE = 20  # Keep last N latency measurements
LATENCY_CHECK_INTERVAL = 30  # Seconds between latency checks
MAX_ACCEPTABLE_LATENCY_MS = 500  # Max acceptable latency in milliseconds
MIN_SUCCESS_RATE = 0.9  # Minimum success rate to consider RPC healthy

# Connection pool settings
MAX_CONNECTIONS_PER_RPC = 3
CONNECTION_TIMEOUT = 10  # seconds
REQUEST_TIMEOUT = 15  # seconds

# WebSocket settings
WS_RECONNECT_DELAY = 5  # seconds
WS_PING_INTERVAL = 30  # seconds


@dataclass
class RPCEndpoint:
    """RPC endpoint with performance metrics."""
    url: str
    chain: str
    is_websocket: bool = False
    # Performance metrics
    latencies: deque = field(default_factory=lambda: deque(maxlen=LATENCY_WINDOW_SIZE))
    success_count: int = 0
    failure_count: int = 0
    last_check: float = 0.0
    last_success: float = 0.0
    is_healthy: bool = True
    # Connection state
    connections: List[Web3] = field(default_factory=list)
    active_requests: int = 0

    @property
    def avg_latency_ms(self) -> float:
        """Average latency in milliseconds."""
        if not self.latencies:
            return float("inf")
        return statistics.mean(self.latencies)

    @property
    def success_rate(self) -> float:
        """Success rate as a fraction."""
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0
        return self.success_count / total

    @property
    def score(self) -> float:
        """
        Composite score for RPC selection.
        Lower = better (used for heap ordering).
        """
        # Combine latency, success rate, and load
        latency_factor = self.avg_latency_ms / 100  # Normalize
        failure_factor = (1 - self.success_rate) * 10
        load_factor = self.active_requests * 0.5

        return latency_factor + failure_factor + load_factor


@dataclass
class BlockSubscription:
    """Subscription to new block events."""
    chain: str
    callback: Callable[[dict], None]
    active: bool = True


@dataclass
class MempoolTransaction:
    """Pending transaction from mempool."""
    tx_hash: str
    chain: str
    to: Optional[str]
    value: int
    gas_price: int
    timestamp: float


class ConnectionPool:
    """
    Thread-safe connection pool for Web3 connections.
    Provides connection reuse and automatic recovery.
    """

    def __init__(self, endpoint: RPCEndpoint, max_size: int = MAX_CONNECTIONS_PER_RPC):
        self.endpoint = endpoint
        self.max_size = max_size
        self._pool: List[Web3] = []
        self._in_use: Set[int] = set()
        self._lock = threading.Lock()

    def acquire(self) -> Optional[Web3]:
        """Acquire a connection from the pool."""
        with self._lock:
            # Try to reuse existing connection
            for i, w3 in enumerate(self._pool):
                if i not in self._in_use:
                    self._in_use.add(i)
                    return w3

            # Create new connection if under limit
            if len(self._pool) < self.max_size:
                w3 = self._create_connection()
                if w3:
                    idx = len(self._pool)
                    self._pool.append(w3)
                    self._in_use.add(idx)
                    return w3

            return None

    def release(self, w3: Web3):
        """Release a connection back to the pool."""
        with self._lock:
            try:
                idx = self._pool.index(w3)
                self._in_use.discard(idx)
            except ValueError:
                pass

    def _create_connection(self) -> Optional[Web3]:
        """Create a new Web3 connection."""
        try:
            if self.endpoint.is_websocket:
                w3 = Web3(Web3.LegacyWebSocketProvider(
                    self.endpoint.url,
                    websocket_timeout=CONNECTION_TIMEOUT,
                ))
            else:
                w3 = Web3(Web3.HTTPProvider(
                    self.endpoint.url,
                    request_kwargs={"timeout": CONNECTION_TIMEOUT},
                ))

            # Apply POA middleware for compatible chains
            from nexus.blockchain import CHAIN_INFO
            chain_info = CHAIN_INFO.get(self.endpoint.chain, {})
            if chain_info.get("poa", False):
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            if w3.is_connected():
                return w3
        except Exception as exc:
            logger.debug("Connection creation failed: %s", exc)
        return None

    def health_check(self) -> bool:
        """Check if pool has healthy connections."""
        with self._lock:
            for w3 in self._pool:
                try:
                    if w3.is_connected():
                        return True
                except Exception:
                    pass
        return False


class LatencyMonitor:
    """
    Monitors RPC endpoint latency and health.
    Runs in background thread.
    """

    def __init__(self, endpoints: List[RPCEndpoint]):
        self.endpoints = {e.url: e for e in endpoints}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=10)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="latency-monitor"
        )
        self._thread.start()
        logger.info("LatencyMonitor started")

    def stop(self):
        self._running = False
        self._executor.shutdown(wait=False)

    def _monitor_loop(self):
        while self._running:
            try:
                self._check_all_endpoints()
            except Exception as exc:
                logger.debug("Latency check error: %s", exc)
            time.sleep(LATENCY_CHECK_INTERVAL)

    def _check_all_endpoints(self):
        """Check latency for all endpoints in parallel."""
        futures = []
        for endpoint in self.endpoints.values():
            futures.append(
                self._executor.submit(self._check_endpoint, endpoint)
            )

        # Wait for all checks to complete
        for future in futures:
            try:
                future.result(timeout=REQUEST_TIMEOUT)
            except Exception:
                pass

    def _check_endpoint(self, endpoint: RPCEndpoint):
        """Measure latency for a single endpoint."""
        try:
            w3 = Web3(Web3.HTTPProvider(
                endpoint.url,
                request_kwargs={"timeout": REQUEST_TIMEOUT},
            ))

            start = time.time()
            block = w3.eth.block_number
            latency_ms = (time.time() - start) * 1000

            endpoint.latencies.append(latency_ms)
            endpoint.success_count += 1
            endpoint.last_success = time.time()
            endpoint.last_check = time.time()

            # Update health status
            endpoint.is_healthy = (
                endpoint.avg_latency_ms < MAX_ACCEPTABLE_LATENCY_MS and
                endpoint.success_rate >= MIN_SUCCESS_RATE
            )

            logger.debug(
                "RPC %s: latency=%.0fms, success_rate=%.1f%%, healthy=%s",
                endpoint.url[:40], latency_ms, endpoint.success_rate * 100, endpoint.is_healthy
            )

        except Exception as exc:
            endpoint.failure_count += 1
            endpoint.last_check = time.time()
            endpoint.is_healthy = endpoint.success_rate >= MIN_SUCCESS_RATE
            logger.debug("RPC %s check failed: %s", endpoint.url[:40], exc)

    def get_best_endpoint(self, chain: str) -> Optional[RPCEndpoint]:
        """Get the best (lowest latency, healthiest) endpoint for a chain."""
        candidates = [
            e for e in self.endpoints.values()
            if e.chain == chain and e.is_healthy
        ]

        if not candidates:
            # Fall back to any endpoint for this chain
            candidates = [e for e in self.endpoints.values() if e.chain == chain]

        if not candidates:
            return None

        # Return lowest score (best)
        return min(candidates, key=lambda e: e.score)


class LowLatencyBlockchainManager:
    """
    Enhanced blockchain manager with low-latency optimizations.

    Features:
      - Priority-based RPC selection
      - Connection pooling
      - Latency monitoring and failover
      - Request batching
      - Block subscription (WebSocket where available)
    """

    def __init__(self):
        from nexus.blockchain import CHAIN_INFO, _RPC_FALLBACKS, _ENABLED_MAP

        self._chain_info = CHAIN_INFO
        self._enabled_chains = {k for k, v in _ENABLED_MAP.items() if v}

        # Build endpoint list
        self._endpoints: List[RPCEndpoint] = []
        self._endpoint_heap: Dict[str, List[Tuple[float, RPCEndpoint]]] = defaultdict(list)

        for chain, rpcs in _RPC_FALLBACKS.items():
            if chain not in self._enabled_chains:
                continue
            for rpc_url in rpcs:
                if not rpc_url:
                    continue
                endpoint = RPCEndpoint(
                    url=rpc_url,
                    chain=chain,
                    is_websocket=rpc_url.startswith("ws"),
                )
                self._endpoints.append(endpoint)

        # Initialize components
        self._latency_monitor = LatencyMonitor(self._endpoints)
        self._connection_pools: Dict[str, ConnectionPool] = {}
        self._block_subscribers: List[BlockSubscription] = []

        # Primary connections (one per chain)
        self._primary_connections: Dict[str, Web3] = {}
        self._primary_pools: Dict[str, ConnectionPool] = {}

        # Request cache (simple in-memory cache)
        self._cache: Dict[str, Tuple[float, any]] = {}
        self._cache_ttl = 2.0  # seconds

        # Metrics
        self._total_requests = 0
        self._cache_hits = 0
        self._failovers = 0

        self._lock = threading.Lock()
        self._running = False

        # Initialize connections
        self._initialize_connections()

    def _initialize_connections(self):
        """Initialize primary connections for each chain."""
        for chain in self._enabled_chains:
            # Find best initial endpoint
            chain_endpoints = [e for e in self._endpoints if e.chain == chain]
            if not chain_endpoints:
                continue

            for endpoint in chain_endpoints:
                pool = ConnectionPool(endpoint)
                w3 = pool.acquire()
                if w3:
                    self._primary_connections[chain] = w3
                    self._primary_pools[chain] = pool
                    self._connection_pools[endpoint.url] = pool
                    logger.info(
                        "Connected to %s via %s",
                        self._chain_info[chain]["name"],
                        endpoint.url[:40],
                    )
                    break

    def start(self):
        """Start background monitoring and optimization."""
        if self._running:
            return
        self._running = True
        self._latency_monitor.start()
        logger.info("LowLatencyBlockchainManager started")

    def stop(self):
        """Stop all background processes."""
        self._running = False
        self._latency_monitor.stop()

    # ── Public API ────────────────────────────────────────────

    def get_web3(self, chain: str, require_fast: bool = False) -> Optional[Web3]:
        """
        Get a Web3 connection for a chain.

        Parameters
        ----------
        chain : str
            Chain name (e.g., "ethereum")
        require_fast : bool
            If True, only return if latency is acceptable

        Returns
        -------
        Web3 or None
        """
        # Check primary connection first
        w3 = self._primary_connections.get(chain)
        if w3:
            try:
                if w3.is_connected():
                    return w3
            except Exception:
                pass

        # Fall back to best available endpoint
        endpoint = self._latency_monitor.get_best_endpoint(chain)
        if endpoint and (not require_fast or endpoint.avg_latency_ms < MAX_ACCEPTABLE_LATENCY_MS):
            pool = self._connection_pools.get(endpoint.url)
            if pool:
                return pool.acquire()
            else:
                pool = ConnectionPool(endpoint)
                self._connection_pools[endpoint.url] = pool
                return pool.acquire()

        return None

    def get_block_number(self, chain: str, use_cache: bool = True) -> int:
        """Get current block number with optional caching."""
        cache_key = f"block_{chain}"

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached

        self._total_requests += 1
        w3 = self.get_web3(chain)
        if not w3:
            return 0

        try:
            block = w3.eth.block_number
            self._set_cached(cache_key, block)
            return block
        except Exception as exc:
            logger.debug("Block number fetch failed for %s: %s", chain, exc)
            self._handle_failure(chain)
            return 0

    def get_gas_price(self, chain: str, use_cache: bool = True) -> int:
        """Get current gas price with optional caching."""
        cache_key = f"gas_{chain}"

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached

        self._total_requests += 1
        w3 = self.get_web3(chain)
        if not w3:
            return 0

        try:
            gas = w3.eth.gas_price
            self._set_cached(cache_key, gas)
            return gas
        except Exception as exc:
            logger.debug("Gas price fetch failed for %s: %s", chain, exc)
            self._handle_failure(chain)
            return 0

    def execute_with_retry(
        self,
        chain: str,
        call_fn: Callable[[Web3], any],
        max_retries: int = 3,
    ) -> Tuple[bool, any]:
        """
        Execute a Web3 call with automatic retry and failover.

        Parameters
        ----------
        chain : str
            Target chain
        call_fn : Callable
            Function that takes Web3 and returns result
        max_retries : int
            Maximum retry attempts

        Returns
        -------
        Tuple[bool, any]
            (success, result)
        """
        self._total_requests += 1
        tried_endpoints = set()

        for attempt in range(max_retries):
            # Get best available endpoint
            endpoint = self._latency_monitor.get_best_endpoint(chain)
            if not endpoint or endpoint.url in tried_endpoints:
                # Try any endpoint we haven't tried
                available = [
                    e for e in self._endpoints
                    if e.chain == chain and e.url not in tried_endpoints
                ]
                if not available:
                    break
                endpoint = available[0]

            tried_endpoints.add(endpoint.url)

            pool = self._connection_pools.get(endpoint.url)
            if not pool:
                pool = ConnectionPool(endpoint)
                self._connection_pools[endpoint.url] = pool

            w3 = pool.acquire()
            if not w3:
                continue

            try:
                start = time.time()
                result = call_fn(w3)
                latency_ms = (time.time() - start) * 1000

                # Record success
                endpoint.latencies.append(latency_ms)
                endpoint.success_count += 1
                endpoint.last_success = time.time()

                pool.release(w3)
                return True, result

            except Exception as exc:
                endpoint.failure_count += 1
                pool.release(w3)
                logger.debug(
                    "Call failed on %s (attempt %d): %s",
                    endpoint.url[:40], attempt + 1, exc
                )

                if attempt < max_retries - 1:
                    self._failovers += 1
                    time.sleep(0.1 * (attempt + 1))  # Exponential backoff

        return False, None

    def batch_call(
        self,
        chain: str,
        calls: List[Callable[[Web3], any]],
    ) -> List[Tuple[bool, any]]:
        """
        Execute multiple calls efficiently.
        Uses same connection for all calls when possible.
        """
        results = []
        w3 = self.get_web3(chain)

        if not w3:
            return [(False, None) for _ in calls]

        for call_fn in calls:
            try:
                result = call_fn(w3)
                results.append((True, result))
            except Exception as exc:
                results.append((False, str(exc)))

        return results

    def subscribe_blocks(self, chain: str, callback: Callable[[dict], None]):
        """Subscribe to new block events for a chain."""
        sub = BlockSubscription(chain=chain, callback=callback)
        self._block_subscribers.append(sub)
        logger.debug("Block subscription added for %s", chain)

    def get_endpoint_stats(self, chain: Optional[str] = None) -> List[dict]:
        """Get performance stats for all endpoints."""
        stats = []
        for endpoint in self._endpoints:
            if chain and endpoint.chain != chain:
                continue
            stats.append({
                "url": endpoint.url[:50],
                "chain": endpoint.chain,
                "avg_latency_ms": round(endpoint.avg_latency_ms, 1),
                "success_rate": round(endpoint.success_rate * 100, 1),
                "is_healthy": endpoint.is_healthy,
                "requests": endpoint.success_count + endpoint.failure_count,
            })
        return sorted(stats, key=lambda x: x["avg_latency_ms"])

    def status(self) -> dict:
        """Return comprehensive status."""
        connected_chains = list(self._primary_connections.keys())
        healthy_endpoints = sum(1 for e in self._endpoints if e.is_healthy)

        return {
            "running": self._running,
            "connected_chains": connected_chains,
            "total_endpoints": len(self._endpoints),
            "healthy_endpoints": healthy_endpoints,
            "total_requests": self._total_requests,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": round(
                self._cache_hits / max(1, self._total_requests) * 100, 1
            ),
            "failovers": self._failovers,
            "endpoint_stats": self.get_endpoint_stats(),
        }

    # ── Internal ──────────────────────────────────────────────

    def _get_cached(self, key: str) -> Optional[any]:
        """Get value from cache if not expired."""
        if key in self._cache:
            ts, value = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return value
        return None

    def _set_cached(self, key: str, value: any):
        """Set value in cache."""
        self._cache[key] = (time.time(), value)
        # Clean old entries periodically
        if len(self._cache) > 1000:
            now = time.time()
            self._cache = {
                k: v for k, v in self._cache.items()
                if now - v[0] < self._cache_ttl * 2
            }

    def _handle_failure(self, chain: str):
        """Handle a connection failure for a chain."""
        # Mark primary connection as potentially stale
        if chain in self._primary_connections:
            # Try to reconnect with best available endpoint
            endpoint = self._latency_monitor.get_best_endpoint(chain)
            if endpoint:
                pool = ConnectionPool(endpoint)
                w3 = pool.acquire()
                if w3:
                    self._primary_connections[chain] = w3
                    self._primary_pools[chain] = pool
                    self._connection_pools[endpoint.url] = pool
                    self._failovers += 1
                    logger.info(
                        "Failover for %s to %s",
                        chain, endpoint.url[:40]
                    )


# Singleton
_ll_manager: Optional[LowLatencyBlockchainManager] = None


def get_low_latency_manager() -> LowLatencyBlockchainManager:
    global _ll_manager
    if _ll_manager is None:
        _ll_manager = LowLatencyBlockchainManager()
    return _ll_manager
