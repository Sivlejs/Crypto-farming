"""
Mining Pool Connection Manager for Nexus AI.

This module provides:
- Pool connection testing and validation
- Simulation mode for testing without real pool connection
- Pre-configured pool URLs for popular coins
- Connection diagnostics and troubleshooting
- Automatic pool selection based on algorithm
"""
from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Pre-configured Mining Pools
# ══════════════════════════════════════════════════════════════════════════════

# These are tested, working pool URLs organized by algorithm
MINING_POOLS = {
    # ═══════════════════════════════════════════════════════════════
    # Etchash (Ethereum Classic)
    # ═══════════════════════════════════════════════════════════════
    "etchash": {
        "2miners": {
            "url": "stratum+tcp://etc.2miners.com:1010",
            "ssl_url": "stratum+ssl://etc.2miners.com:11010",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 0.1,
        },
        "ethermine": {
            "url": "stratum+tcp://etc.ethermine.org:4444",
            "ssl_url": "stratum+ssl://etc.ethermine.org:5555",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 0.1,
        },
        "f2pool": {
            "url": "stratum+tcp://etc.f2pool.com:8118",
            "region": "Global",
            "fee": 2.5,
            "min_payout": 0.1,
        },
        "hiveon": {
            "url": "stratum+tcp://etc.hiveon.com:4444",
            "region": "Global",
            "fee": 0.0,
            "min_payout": 0.1,
        },
        "nanopool": {
            "url": "stratum+tcp://etc-eu1.nanopool.org:19999",
            "region": "Europe",
            "fee": 1.0,
            "min_payout": 0.05,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # KawPow (Ravencoin)
    # ═══════════════════════════════════════════════════════════════
    "kawpow": {
        "2miners": {
            "url": "stratum+tcp://rvn.2miners.com:6060",
            "ssl_url": "stratum+ssl://rvn.2miners.com:16060",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 10,
        },
        "flypool": {
            "url": "stratum+tcp://stratum-ravencoin.flypool.org:3333",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 10,
        },
        "ravenminer": {
            "url": "stratum+tcp://stratum.ravenminer.com:3838",
            "region": "US",
            "fee": 0.5,
            "min_payout": 5,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # Autolykos2 (Ergo)
    # ═══════════════════════════════════════════════════════════════
    "autolykos2": {
        "2miners": {
            "url": "stratum+tcp://erg.2miners.com:8888",
            "ssl_url": "stratum+ssl://erg.2miners.com:18888",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 0.5,
        },
        "nanopool": {
            "url": "stratum+tcp://ergo-eu1.nanopool.org:11111",
            "region": "Europe",
            "fee": 1.0,
            "min_payout": 0.1,
        },
        "herominers": {
            "url": "stratum+tcp://ergo.herominers.com:10250",
            "region": "Global",
            "fee": 0.9,
            "min_payout": 0.5,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # kHeavyHash (Kaspa)
    # ═══════════════════════════════════════════════════════════════
    "kheavyhash": {
        "2miners": {
            "url": "stratum+tcp://kas.2miners.com:2020",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 5,
        },
        "acc-pool": {
            "url": "stratum+tcp://kas.acc-pool.pw:16061",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 1,
        },
        "herominers": {
            "url": "stratum+tcp://kaspa.herominers.com:10350",
            "region": "Global",
            "fee": 0.9,
            "min_payout": 5,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # Blake3 (Alephium)
    # ═══════════════════════════════════════════════════════════════
    "blake3": {
        "2miners": {
            "url": "stratum+tcp://alph.2miners.com:2020",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 0.1,
        },
        "herominers": {
            "url": "stratum+tcp://alephium.herominers.com:10350",
            "region": "Global",
            "fee": 0.9,
            "min_payout": 0.1,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # RandomX (Monero)
    # ═══════════════════════════════════════════════════════════════
    "randomx": {
        "2miners": {
            "url": "stratum+tcp://xmr.2miners.com:2222",
            "ssl_url": "stratum+ssl://xmr.2miners.com:12222",
            "region": "Global",
            "fee": 1.0,
            "min_payout": 0.01,
        },
        "supportxmr": {
            "url": "stratum+tcp://pool.supportxmr.com:3333",
            "region": "Global",
            "fee": 0.6,
            "min_payout": 0.1,
        },
        "nanopool": {
            "url": "stratum+tcp://xmr-eu1.nanopool.org:14444",
            "region": "Europe",
            "fee": 1.0,
            "min_payout": 0.1,
        },
        "hashvault": {
            "url": "stratum+tcp://pool.hashvault.pro:3333",
            "region": "Global",
            "fee": 0.9,
            "min_payout": 0.001,
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # SHA256 (Bitcoin, Bitcoin Cash)
    # Note: CPU mining Bitcoin is not profitable, these are for testing
    # ═══════════════════════════════════════════════════════════════
    "sha256": {
        "slushpool": {
            "url": "stratum+tcp://stratum.slushpool.com:3333",
            "region": "Global",
            "fee": 2.0,
            "min_payout": 0.001,
            "note": "CPU mining not profitable",
        },
        "f2pool": {
            "url": "stratum+tcp://btc.f2pool.com:1314",
            "region": "Global",
            "fee": 2.5,
            "min_payout": 0.001,
            "note": "CPU mining not profitable",
        },
    },
    
    # ═══════════════════════════════════════════════════════════════
    # Scrypt (Litecoin, Dogecoin)
    # ═══════════════════════════════════════════════════════════════
    "scrypt": {
        "litecoinpool": {
            "url": "stratum+tcp://us.litecoinpool.org:3333",
            "region": "US",
            "fee": 0.0,
            "min_payout": 0.01,
        },
        "f2pool_ltc": {
            "url": "stratum+tcp://ltc.f2pool.com:8888",
            "region": "Global",
            "fee": 2.5,
            "min_payout": 0.001,
        },
    },
}


@dataclass
class PoolConnectionResult:
    """Result of a pool connection test."""
    success: bool
    pool_url: str
    latency_ms: float
    error_message: str = ""
    server_version: str = ""
    difficulty: float = 0.0


class PoolConnectionTester:
    """Tests pool connectivity and measures latency."""
    
    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
    
    def test_connection(self, pool_url: str) -> PoolConnectionResult:
        """
        Test connection to a mining pool.
        
        Returns detailed connection result including latency.
        """
        start_time = time.time()
        
        try:
            # Parse URL
            url = pool_url
            if url.startswith("stratum+tcp://"):
                url = url[14:]
            elif url.startswith("stratum+ssl://"):
                url = url[14:]
                # Note: SSL requires additional handling
            elif url.startswith("stratum://"):
                url = url[10:]
            
            if ":" in url:
                host, port_str = url.rsplit(":", 1)
                port = int(port_str)
            else:
                host = url
                port = 3333
            
            # Connect
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((host, port))
            
            connect_time = time.time()
            latency = (connect_time - start_time) * 1000
            
            # Send subscribe request
            subscribe_msg = json.dumps({
                "id": 1,
                "method": "mining.subscribe",
                "params": ["NexusAI-Test/1.0"]
            }) + "\n"
            
            sock.sendall(subscribe_msg.encode())
            
            # Read response
            response = b""
            while b"\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            
            sock.close()
            
            # Parse response
            if response:
                try:
                    data = json.loads(response.decode().strip())
                    if "result" in data:
                        return PoolConnectionResult(
                            success=True,
                            pool_url=pool_url,
                            latency_ms=latency,
                            server_version=str(data.get("result", [])),
                        )
                    elif "error" in data:
                        return PoolConnectionResult(
                            success=False,
                            pool_url=pool_url,
                            latency_ms=latency,
                            error_message=str(data.get("error")),
                        )
                except json.JSONDecodeError:
                    pass
            
            return PoolConnectionResult(
                success=True,
                pool_url=pool_url,
                latency_ms=latency,
            )
            
        except socket.timeout:
            return PoolConnectionResult(
                success=False,
                pool_url=pool_url,
                latency_ms=self.timeout * 1000,
                error_message="Connection timeout",
            )
        except socket.gaierror as e:
            return PoolConnectionResult(
                success=False,
                pool_url=pool_url,
                latency_ms=0,
                error_message=f"DNS resolution failed: {e}",
            )
        except ConnectionRefusedError:
            return PoolConnectionResult(
                success=False,
                pool_url=pool_url,
                latency_ms=(time.time() - start_time) * 1000,
                error_message="Connection refused",
            )
        except Exception as e:
            return PoolConnectionResult(
                success=False,
                pool_url=pool_url,
                latency_ms=(time.time() - start_time) * 1000,
                error_message=str(e),
            )
    
    def find_best_pool(self, algorithm: str) -> Tuple[str, str, float]:
        """
        Find the best (lowest latency) pool for an algorithm.
        
        Returns:
            (pool_name, pool_url, latency_ms)
        """
        pools = MINING_POOLS.get(algorithm.lower(), {})
        
        if not pools:
            return "", "", 0
        
        best_pool = ""
        best_url = ""
        best_latency = float('inf')
        
        logger.info("Testing pools for %s...", algorithm)
        
        for pool_name, pool_info in pools.items():
            url = pool_info.get("url", "")
            if not url:
                continue
            
            result = self.test_connection(url)
            
            if result.success and result.latency_ms < best_latency:
                best_pool = pool_name
                best_url = url
                best_latency = result.latency_ms
                logger.info("  %s: %.1f ms ✓", pool_name, result.latency_ms)
            else:
                logger.info("  %s: %s ✗", pool_name, result.error_message or "Failed")
        
        return best_pool, best_url, best_latency


# ══════════════════════════════════════════════════════════════════════════════
# Simulated Pool for Testing
# ══════════════════════════════════════════════════════════════════════════════

class SimulatedStratumServer:
    """
    Simulated Stratum server for testing mining without real pool connection.
    
    This allows testing the mining infrastructure in dry-run mode
    without connecting to actual pools.
    """
    
    def __init__(self, algorithm: str = "sha256", difficulty: float = 0.001):
        self.algorithm = algorithm
        self.difficulty = difficulty
        self._job_counter = 0
        self._subscribed = False
        self._authorized = False
        self._shares_accepted = 0
        self._shares_rejected = 0
        self._start_time = time.time()
    
    def subscribe(self) -> dict:
        """Simulate mining.subscribe response."""
        self._subscribed = True
        return {
            "result": [
                [["mining.set_difficulty", "1"], ["mining.notify", "1"]],
                "08000000",  # extranonce1
                4,  # extranonce2_size
            ],
            "id": 1,
            "error": None,
        }
    
    def authorize(self, username: str, password: str) -> dict:
        """Simulate mining.authorize response."""
        self._authorized = True
        return {
            "result": True,
            "id": 2,
            "error": None,
        }
    
    def get_job(self) -> dict:
        """Generate a simulated mining job."""
        self._job_counter += 1
        
        import hashlib
        import secrets
        
        # Generate pseudo-random job data
        prevhash = secrets.token_hex(32)
        coinbase1 = "01000000010000000000000000000000000000000000000000000000000000000000000000ffffffff"
        coinbase2 = "ffffffff0100f2052a0100000043410496b538e853519c726a2c91e61ec11600ae1390813a627c66fb8be7947be63c52da7589379515d4e0a604f8141781e62294721166bf621e73a82cbf2342c858eeac00000000"
        
        return {
            "method": "mining.notify",
            "params": [
                f"job_{self._job_counter}",  # job_id
                prevhash,  # prevhash
                coinbase1,  # coinbase1
                coinbase2,  # coinbase2
                [],  # merkle_branches
                "20000000",  # version
                "1d00ffff",  # nbits
                hex(int(time.time()))[2:],  # ntime
                True,  # clean_jobs
            ]
        }
    
    def submit_share(self, job_id: str, extranonce2: str, ntime: str, nonce: str) -> dict:
        """Simulate share submission."""
        # Accept ~95% of shares in simulation
        import random
        if random.random() < 0.95:
            self._shares_accepted += 1
            return {"result": True, "id": 4, "error": None}
        else:
            self._shares_rejected += 1
            return {"result": False, "id": 4, "error": [21, "Job not found", None]}
    
    def set_difficulty(self) -> dict:
        """Generate difficulty notification."""
        return {
            "method": "mining.set_difficulty",
            "params": [self.difficulty],
        }
    
    def stats(self) -> dict:
        """Get simulated pool stats."""
        uptime = time.time() - self._start_time
        return {
            "simulated": True,
            "algorithm": self.algorithm,
            "difficulty": self.difficulty,
            "shares_accepted": self._shares_accepted,
            "shares_rejected": self._shares_rejected,
            "uptime_seconds": uptime,
            "subscribed": self._subscribed,
            "authorized": self._authorized,
        }


class SimulatedStratumClient:
    """
    Drop-in replacement for StratumClient that uses simulation.
    
    Use this for testing without real pool connection.
    """
    
    def __init__(
        self,
        pool_url: str = "simulated://localhost:3333",
        username: str = "test.worker",
        password: str = "x",
        algorithm: str = "sha256",
    ):
        self.pool_url = pool_url
        self.username = username
        self.password = password
        self.algorithm = algorithm.lower()
        
        self._server = SimulatedStratumServer(algorithm=algorithm)
        self._connected = False
        self._subscribed = False
        self._authorized = False
        self._extranonce1 = ""
        self._extranonce2_size = 4
        self._current_difficulty = 0.001
        self._start_time = 0.0
        
        # Job management
        self._current_job = None
        self._job_lock = threading.Lock()
        self._job_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Stats
        self._shares_submitted = 0
        self._shares_accepted = 0
        self._shares_rejected = 0
    
    def connect(self) -> bool:
        """Simulate pool connection."""
        logger.info("Connecting to SIMULATED pool (dry-run mode)")
        self._connected = True
        self._start_time = time.time()
        
        # Start job generation thread
        self._running = True
        self._job_thread = threading.Thread(target=self._job_loop, daemon=True)
        self._job_thread.start()
        
        logger.info("Connected to simulated pool (algorithm: %s)", self.algorithm)
        return True
    
    def subscribe(self) -> bool:
        """Simulate subscription."""
        if not self._connected:
            return False
        
        response = self._server.subscribe()
        result = response.get("result", [])
        
        if isinstance(result, list) and len(result) >= 3:
            self._extranonce1 = result[1]
            self._extranonce2_size = result[2]
        
        self._subscribed = True
        logger.info("Subscribed to simulated pool (extranonce1=%s)", self._extranonce1)
        return True
    
    def authorize(self) -> bool:
        """Simulate authorization."""
        if not self._connected:
            return False
        
        self._server.authorize(self.username, self.password)
        self._authorized = True
        logger.info("Authorized worker (simulated): %s", self.username)
        return True
    
    def submit_share(self, job_id: str, extranonce2: str, ntime: str, nonce: str) -> bool:
        """Simulate share submission."""
        if not self._authorized:
            return False
        
        self._shares_submitted += 1
        response = self._server.submit_share(job_id, extranonce2, ntime, nonce)
        
        if response.get("result"):
            self._shares_accepted += 1
            logger.info("Simulated share accepted! (accepted=%d)", self._shares_accepted)
            return True
        else:
            self._shares_rejected += 1
            logger.info("Simulated share rejected")
            return False
    
    def get_job(self):
        """Get current simulated job."""
        with self._job_lock:
            return self._current_job
    
    def disconnect(self):
        """Disconnect from simulated pool."""
        self._running = False
        self._connected = False
        logger.info("Disconnected from simulated pool")
    
    def stats(self) -> dict:
        """Get simulated pool statistics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "simulated": True,
            "connected": self._connected,
            "subscribed": self._subscribed,
            "authorized": self._authorized,
            "pool_url": self.pool_url,
            "worker": self.username,
            "algorithm": self.algorithm,
            "difficulty": self._current_difficulty,
            "shares_submitted": self._shares_submitted,
            "shares_accepted": self._shares_accepted,
            "shares_rejected": self._shares_rejected,
            "accept_rate": (
                self._shares_accepted / self._shares_submitted * 100
                if self._shares_submitted > 0 else 100
            ),
            "uptime_seconds": round(uptime),
        }
    
    def _job_loop(self):
        """Generate simulated jobs periodically."""
        from nexus.strategies.pow_mining import MiningJob, difficulty_to_target
        
        while self._running:
            time.sleep(30)  # New job every 30 seconds
            
            if not self._running:
                break
            
            job_data = self._server.get_job()
            params = job_data.get("params", [])
            
            if len(params) >= 9:
                job = MiningJob(
                    job_id=params[0],
                    prevhash=params[1],
                    coinbase1=params[2],
                    coinbase2=params[3],
                    merkle_branches=params[4],
                    version=params[5],
                    nbits=params[6],
                    ntime=params[7],
                    clean_jobs=params[8],
                    extranonce1=self._extranonce1,
                    extranonce2_size=self._extranonce2_size,
                    difficulty=self._current_difficulty,
                    target=difficulty_to_target(self._current_difficulty),
                )
                
                with self._job_lock:
                    self._current_job = job
                
                logger.debug("New simulated job: %s", job.job_id)


# ══════════════════════════════════════════════════════════════════════════════
# Pool Manager
# ══════════════════════════════════════════════════════════════════════════════

class MiningPoolManager:
    """
    Manages mining pool connections and provides pool recommendations.
    """
    
    def __init__(self):
        self._tester = PoolConnectionTester()
        self._cached_results: Dict[str, PoolConnectionResult] = {}
    
    def get_pools_for_algorithm(self, algorithm: str) -> List[dict]:
        """Get all configured pools for an algorithm."""
        pools = MINING_POOLS.get(algorithm.lower(), {})
        result = []
        
        for name, info in pools.items():
            result.append({
                "name": name,
                "url": info.get("url", ""),
                "ssl_url": info.get("ssl_url"),
                "region": info.get("region", "Global"),
                "fee": info.get("fee", 0),
                "min_payout": info.get("min_payout", 0),
                "note": info.get("note"),
            })
        
        return result
    
    def test_pool(self, pool_url: str) -> PoolConnectionResult:
        """Test a specific pool connection."""
        result = self._tester.test_connection(pool_url)
        self._cached_results[pool_url] = result
        return result
    
    def find_best_pool(self, algorithm: str) -> Tuple[str, str, float]:
        """Find the best pool for an algorithm based on latency."""
        return self._tester.find_best_pool(algorithm)
    
    def get_recommended_pool(self, algorithm: str, wallet_address: str) -> dict:
        """
        Get a recommended pool configuration.
        
        Returns a complete configuration ready to use.
        """
        pools = MINING_POOLS.get(algorithm.lower(), {})
        
        if not pools:
            return {}
        
        # Default to 2miners if available (reliable, low fees)
        if "2miners" in pools:
            pool_info = pools["2miners"]
        else:
            # Use first available
            pool_name = list(pools.keys())[0]
            pool_info = pools[pool_name]
        
        return {
            "pool_url": pool_info.get("url", ""),
            "pool_user": f"{wallet_address}.nexus",
            "pool_password": "x",
            "algorithm": algorithm,
            "fee": pool_info.get("fee", 0),
            "min_payout": pool_info.get("min_payout", 0),
        }
    
    def create_client(
        self,
        pool_url: str,
        username: str,
        password: str = "x",
        algorithm: str = "sha256",
        simulation_mode: bool = False,
    ):
        """
        Create a Stratum client (real or simulated).
        
        Args:
            pool_url: Pool URL
            username: Worker username
            password: Pool password
            algorithm: Mining algorithm
            simulation_mode: If True, use simulated pool
        
        Returns:
            StratumClient or SimulatedStratumClient
        """
        if simulation_mode or not pool_url or pool_url.startswith("simulated://"):
            logger.info("Using SIMULATED pool for testing (no real connection)")
            return SimulatedStratumClient(
                pool_url=pool_url or "simulated://localhost:3333",
                username=username,
                password=password,
                algorithm=algorithm,
            )
        else:
            from nexus.strategies.pow_mining import StratumClient
            return StratumClient(
                pool_url=pool_url,
                username=username,
                password=password,
                algorithm=algorithm,
            )


# ══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════════

def get_available_algorithms() -> List[str]:
    """Get list of all supported algorithms."""
    return list(MINING_POOLS.keys())


def get_pools_summary() -> Dict[str, int]:
    """Get summary of pools per algorithm."""
    return {algo: len(pools) for algo, pools in MINING_POOLS.items()}


def test_all_pools(algorithm: str) -> List[PoolConnectionResult]:
    """Test all pools for an algorithm."""
    tester = PoolConnectionTester()
    results = []
    
    pools = MINING_POOLS.get(algorithm.lower(), {})
    
    for name, info in pools.items():
        url = info.get("url", "")
        if url:
            result = tester.test_connection(url)
            result.pool_url = f"{name}: {url}"
            results.append(result)
    
    return results


# Global pool manager instance
_pool_manager: Optional[MiningPoolManager] = None


def get_pool_manager() -> MiningPoolManager:
    """Get the singleton pool manager instance."""
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = MiningPoolManager()
    return _pool_manager
