"""
Blockchain Service for Nexus AI.

Dedicated microservice for low-latency blockchain operations:
  - Connection pooling and management
  - Real-time block monitoring
  - Gas price tracking
  - Transaction broadcasting
  - Multi-chain RPC failover

This service provides fast, reliable blockchain access for trading operations.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

from flask import Flask, jsonify, request

# Import core blockchain modules
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from nexus.execution.low_latency import get_low_latency_manager, LowLatencyBlockchainManager
from nexus.blockchain import get_blockchain_manager, BlockchainManager, CHAIN_INFO
from nexus.timing.gas_oracle import get_gas_oracle
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Flask app
app = Flask(__name__)

# Service configuration
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8002"))
MAX_CONNECTIONS_PER_CHAIN = int(os.getenv("MAX_CONNECTIONS_PER_CHAIN", "5"))
LATENCY_CHECK_INTERVAL = int(os.getenv("LATENCY_CHECK_INTERVAL", "15"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))
ENABLE_WEBSOCKET = os.getenv("ENABLE_WEBSOCKET", "true").lower() == "true"

# Singleton instances
_ll_manager: Optional[LowLatencyBlockchainManager] = None
_blockchain_manager: Optional[BlockchainManager] = None
_gas_oracle = None
_startup_complete = False


def initialize_services():
    """Initialize blockchain services."""
    global _ll_manager, _blockchain_manager, _gas_oracle, _startup_complete

    logger.info("Initializing Blockchain Service...")

    try:
        # Initialize low-latency manager
        _ll_manager = get_low_latency_manager()
        _ll_manager.start()
        logger.info("LowLatencyBlockchainManager started")

        # Initialize standard blockchain manager (for compatibility)
        _blockchain_manager = get_blockchain_manager()
        logger.info("BlockchainManager initialized")

        # Initialize gas oracle
        _gas_oracle = get_gas_oracle()
        _gas_oracle.start()
        logger.info("GasOracle started")

        _startup_complete = True
        logger.info("Blockchain Service ready ✓")

    except Exception as exc:
        logger.error("Blockchain Service initialization failed: %s", exc)
        raise


# ══════════════════════════════════════════════════════════════
# HEALTH & STATUS ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy" if _startup_complete else "starting",
        "service": "blockchain",
        "timestamp": time.time(),
    })


@app.route("/status")
def status():
    """Detailed service status."""
    ll_status = _ll_manager.status() if _ll_manager else {}
    bc_status = _blockchain_manager.status() if _blockchain_manager else {}
    gas_status = _gas_oracle.stats() if _gas_oracle else {}

    return jsonify({
        "service": "blockchain",
        "startup_complete": _startup_complete,
        "low_latency": ll_status,
        "blockchain": bc_status,
        "gas_oracle": gas_status,
        "config": {
            "max_connections_per_chain": MAX_CONNECTIONS_PER_CHAIN,
            "latency_check_interval": LATENCY_CHECK_INTERVAL,
            "request_timeout": REQUEST_TIMEOUT,
            "websocket_enabled": ENABLE_WEBSOCKET,
        },
    })


# ══════════════════════════════════════════════════════════════
# CHAIN CONNECTION ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/chains")
def get_chains():
    """Get status of all chain connections."""
    if not _blockchain_manager:
        return jsonify({"error": "Service not ready"}), 503

    chain_status = _blockchain_manager.status()
    return jsonify({
        "chains": chain_status,
        "connected": _blockchain_manager.connected_chains(),
    })


@app.route("/api/chains/<chain>")
def get_chain(chain: str):
    """Get detailed status for a specific chain."""
    if not _blockchain_manager:
        return jsonify({"error": "Service not ready"}), 503

    if chain not in CHAIN_INFO:
        return jsonify({"error": f"Unknown chain: {chain}"}), 404

    chain_status = _blockchain_manager.status().get(chain, {})
    endpoint_stats = []

    if _ll_manager:
        endpoint_stats = _ll_manager.get_endpoint_stats(chain=chain)

    return jsonify({
        "chain": chain,
        "info": CHAIN_INFO.get(chain, {}),
        "status": chain_status,
        "endpoints": endpoint_stats,
    })


@app.route("/api/chains/<chain>/block")
def get_block_number(chain: str):
    """Get current block number for a chain."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    block = _ll_manager.get_block_number(chain)
    return jsonify({
        "chain": chain,
        "block_number": block,
        "timestamp": time.time(),
    })


@app.route("/api/chains/<chain>/gas")
def get_gas_price(chain: str):
    """Get current gas price for a chain."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    from web3 import Web3
    gas_wei = _ll_manager.get_gas_price(chain)
    gas_gwei = float(Web3.from_wei(gas_wei, "gwei")) if gas_wei else 0

    # Get oracle data if available
    oracle_data = {}
    if _gas_oracle:
        oracle_data = {
            "is_cheap": _gas_oracle.is_cheap_now(),
            "should_wait": _gas_oracle.should_wait(),
            "percentile_25": _gas_oracle.get_percentile(25),
            "percentile_50": _gas_oracle.get_percentile(50),
            "percentile_75": _gas_oracle.get_percentile(75),
        }

    return jsonify({
        "chain": chain,
        "gas_wei": gas_wei,
        "gas_gwei": round(gas_gwei, 2),
        "oracle": oracle_data,
        "timestamp": time.time(),
    })


# ══════════════════════════════════════════════════════════════
# RPC PERFORMANCE ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/endpoints")
def get_endpoints():
    """Get performance stats for all RPC endpoints."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    chain = request.args.get("chain")
    stats = _ll_manager.get_endpoint_stats(chain=chain)

    return jsonify({
        "endpoints": stats,
        "count": len(stats),
        "filter_chain": chain,
    })


@app.route("/api/endpoints/best/<chain>")
def get_best_endpoint(chain: str):
    """Get the best (lowest latency) endpoint for a chain."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    stats = _ll_manager.get_endpoint_stats(chain=chain)
    if not stats:
        return jsonify({"error": f"No endpoints for chain: {chain}"}), 404

    # Find best by avg_latency_ms
    best = min(stats, key=lambda s: s.get("avg_latency_ms", float("inf")))

    return jsonify({
        "chain": chain,
        "best_endpoint": best,
    })


# ══════════════════════════════════════════════════════════════
# TRANSACTION ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/balance/<chain>/<address>")
def get_balance(chain: str, address: str):
    """Get native token balance for an address."""
    if not _blockchain_manager:
        return jsonify({"error": "Service not ready"}), 503

    balance = _blockchain_manager.get_native_balance(chain, address)

    return jsonify({
        "chain": chain,
        "address": address,
        "balance": balance,
        "symbol": CHAIN_INFO.get(chain, {}).get("symbol", "?"),
    })


@app.route("/api/execute", methods=["POST"])
def execute_call():
    """Execute a read-only Web3 call."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    chain = data.get("chain", "ethereum")
    method = data.get("method", "")
    params = data.get("params", [])

    if not method:
        return jsonify({"error": "method required"}), 400

    def make_call(w3):
        # Support common read methods
        if method == "eth_blockNumber":
            return w3.eth.block_number
        elif method == "eth_gasPrice":
            return w3.eth.gas_price
        elif method == "eth_getBalance":
            if len(params) >= 1:
                return w3.eth.get_balance(params[0])
        elif method == "eth_getTransactionCount":
            if len(params) >= 1:
                return w3.eth.get_transaction_count(params[0])
        elif method == "eth_call":
            if len(params) >= 1:
                return w3.eth.call(params[0])
        raise ValueError(f"Unsupported method: {method}")

    success, result = _ll_manager.execute_with_retry(chain, make_call)

    if success:
        # Convert bytes to hex string if needed
        if isinstance(result, bytes):
            result = result.hex()
        return jsonify({
            "success": True,
            "result": result,
            "chain": chain,
        })
    else:
        return jsonify({
            "success": False,
            "error": str(result) if result else "Call failed",
            "chain": chain,
        }), 500


@app.route("/api/batch", methods=["POST"])
def batch_calls():
    """Execute multiple read-only calls in batch."""
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    data = request.get_json() or {}
    chain = data.get("chain", "ethereum")
    calls = data.get("calls", [])

    if not calls:
        return jsonify({"error": "calls required"}), 400

    # Limit batch size
    calls = calls[:50]

    def make_call_fn(method, params):
        def fn(w3):
            if method == "eth_blockNumber":
                return w3.eth.block_number
            elif method == "eth_gasPrice":
                return w3.eth.gas_price
            elif method == "eth_getBalance" and params:
                return w3.eth.get_balance(params[0])
            raise ValueError(f"Unsupported: {method}")
        return fn

    call_fns = [
        make_call_fn(c.get("method", ""), c.get("params", []))
        for c in calls
    ]

    results = _ll_manager.batch_call(chain, call_fns)

    return jsonify({
        "results": [
            {"success": s, "result": r if not isinstance(r, bytes) else r.hex()}
            for s, r in results
        ],
        "count": len(results),
        "chain": chain,
    })


# ══════════════════════════════════════════════════════════════
# GAS ORACLE ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.route("/api/gas")
def get_gas_oracle_status():
    """Get gas oracle status and recommendations."""
    if not _gas_oracle:
        return jsonify({"error": "Service not ready"}), 503

    stats = _gas_oracle.stats()
    return jsonify({
        "current_base_fee": _gas_oracle.current_base_fee(),
        "is_cheap": _gas_oracle.is_cheap_now(),
        "should_wait": _gas_oracle.should_wait(),
        "stats": stats,
    })


@app.route("/api/gas/history")
def get_gas_history():
    """Get historical gas prices."""
    if not _gas_oracle:
        return jsonify({"error": "Service not ready"}), 503

    hours = int(request.args.get("hours", 24))

    # Get samples from oracle (simplified - would need implementation)
    stats = _gas_oracle.stats()

    return jsonify({
        "hours": hours,
        "stats": stats,
    })


@app.route("/api/gas/optimal")
def get_optimal_gas():
    """Get optimal gas price for transaction."""
    if not _gas_oracle:
        return jsonify({"error": "Service not ready"}), 503

    urgency = request.args.get("urgency", "normal")

    # Base gas price
    current = _gas_oracle.current_base_fee() or 30

    # Adjust based on urgency
    multipliers = {
        "urgent": 1.3,
        "normal": 1.1,
        "low": 0.9,
    }
    multiplier = multipliers.get(urgency, 1.1)

    optimal = current * multiplier

    return jsonify({
        "urgency": urgency,
        "current_gwei": round(current, 2),
        "optimal_gwei": round(optimal, 2),
        "multiplier": multiplier,
        "recommendation": "submit" if _gas_oracle.is_cheap_now() else "wait",
    })


# ══════════════════════════════════════════════════════════════
# WEBSOCKET SUBSCRIPTION (via polling for now)
# ══════════════════════════════════════════════════════════════

@app.route("/api/subscribe/blocks/<chain>")
def subscribe_blocks(chain: str):
    """
    Get latest block info for a chain.
    In production, this would be a WebSocket subscription.
    """
    if not _ll_manager:
        return jsonify({"error": "Service not ready"}), 503

    block_number = _ll_manager.get_block_number(chain)
    gas_wei = _ll_manager.get_gas_price(chain)

    from web3 import Web3
    gas_gwei = float(Web3.from_wei(gas_wei, "gwei")) if gas_wei else 0

    return jsonify({
        "chain": chain,
        "block_number": block_number,
        "gas_gwei": round(gas_gwei, 2),
        "timestamp": time.time(),
    })


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    """Main entry point for Blockchain Service."""
    logger.info("Starting Blockchain Service on port %d", SERVICE_PORT)

    # Initialize in background thread
    init_thread = threading.Thread(target=initialize_services, daemon=True)
    init_thread.start()

    # Run Flask app
    app.run(
        host="0.0.0.0",
        port=SERVICE_PORT,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
