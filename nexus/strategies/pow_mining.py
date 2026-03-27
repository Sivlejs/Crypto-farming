"""
PoW Mining Strategy for Nexus AI.

Implements Proof-of-Work cryptocurrency mining competitive with dedicated hardware:
- Connects to mining pools via Stratum protocol
- Supports multiple algorithms: SHA-256, Scrypt, Ethash, RandomX, KawPow
- CPU mining with built-in implementation
- GPU mining via OpenCL/CUDA detection and external miner integration
- External miner support: XMRig, T-Rex, lolMiner, GMiner, BzMiner
- Mining pool failover for high availability
- Automatic profit switching to most profitable coin
- Hardware monitoring (temp, power, hashrate per device)
- Automatic difficulty adjustment
- Mining stats tracking and profitability estimation

Virtual Server & Cloud GPU Optimizations:
- Adaptive thread/intensity management based on CPU/GPU load
- Memory-efficient mining modes for cloud environments
- Dynamic batch sizing for optimal performance
- Auto-detection of optimal mining parameters
- Resource throttling to prevent provider throttling/termination
- Multi-GPU support for cloud GPU instances (AWS, GCP, Azure)
- External miner auto-detection and configuration
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.logger import get_logger
from nexus.utils.threading_utils import catch_thread_exceptions

# Import GPU mining components
try:
    from nexus.strategies.gpu_mining import (
        GPUDetector,
        GPUDevice,
        GPUVendor,
        MiningAlgorithm,
        ExternalMinerManager,
        ExternalMinerConfig,
        ExternalMinerType,
        PoolFailoverManager,
        ProfitSwitcher,
        get_gpu_detector,
        get_miner_manager,
        get_gpu_mining_info,
    )
    GPU_MINING_AVAILABLE = True
except ImportError:
    GPU_MINING_AVAILABLE = False

# Import GPU optimizer for maximum efficiency
try:
    from nexus.strategies.gpu_optimizer import (
        GPUOptimizer,
        HashrateTuner,
        MultiGPUOrchestrator,
        GPUMiningProfile,
        GPU_MINING_PROFILES,
        get_gpu_optimizer,
        get_hashrate_tuner,
        get_multi_gpu_orchestrator,
        get_optimal_settings_for_gpu,
        get_all_gpu_profiles,
    )
    GPU_OPTIMIZER_AVAILABLE = True
except ImportError:
    GPU_OPTIMIZER_AVAILABLE = False

# Import AI mining optimizer for intelligent optimization
try:
    from nexus.strategies.ai_mining_optimizer import (
        AIMiningOptimizer,
        MiningSnapshot,
        MiningDecision,
        OptimizationResult,
        get_ai_mining_optimizer,
        create_mining_snapshot,
        # v2 Enhanced components
        EnhancedAIMiningOptimizer,
        get_enhanced_ai_mining_optimizer,
    )
    AI_OPTIMIZER_AVAILABLE = True
    ENHANCED_AI_AVAILABLE = True
except ImportError:
    AI_OPTIMIZER_AVAILABLE = False
    ENHANCED_AI_AVAILABLE = False

# Import simulated pool client for fallback mode
try:
    from nexus.strategies.pool_manager import SimulatedStratumClient
    SIMULATION_AVAILABLE = True
except ImportError:
    SIMULATION_AVAILABLE = False

# Import real vGPU compute for actual hash computations
try:
    from nexus.strategies.real_vgpu_compute import (
        RealVGPUComputeManager,
        VGPUComputeMode,
        initialize_real_compute,
        get_compute_manager,
    )
    REAL_COMPUTE_AVAILABLE = True
except ImportError:
    REAL_COMPUTE_AVAILABLE = False


logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Virtual Server Resource Monitor
# ══════════════════════════════════════════════════════════════════════════════

class ResourceMonitor:
    """
    Monitor system resources for adaptive mining on virtual servers.
    
    Key features:
    - CPU load monitoring to prevent provider throttling
    - Memory usage tracking for memory-hard algorithms
    - Automatic detection of virtual server environment
    - Resource-aware parameter adjustment
    """
    
    def __init__(self):
        self._cpu_count = multiprocessing.cpu_count()
        self._last_cpu_check = 0.0
        self._last_cpu_percent = 0.0
        self._is_virtual = self._detect_virtual_environment()
        self._memory_total = self._get_total_memory()
        self._sample_interval = 5.0  # Seconds between samples
    
    def _detect_virtual_environment(self) -> bool:
        """Detect if running in a virtual/cloud environment."""
        indicators = [
            os.path.exists('/sys/hypervisor/type'),
            os.path.exists('/proc/xen'),
            os.path.exists('/.dockerenv'),
            os.getenv('KUBERNETES_SERVICE_HOST') is not None,
            os.getenv('RENDER') is not None,  # Render.com
            os.getenv('RAILWAY_ENVIRONMENT') is not None,  # Railway
            os.getenv('HEROKU_APP_NAME') is not None,  # Heroku
            os.getenv('DYNO') is not None,  # Heroku dyno
            os.getenv('FLY_APP_NAME') is not None,  # Fly.io
            os.getenv('VERCEL') is not None,  # Vercel
        ]
        return any(indicators)
    
    def _get_total_memory(self) -> int:
        """Get total system memory in bytes."""
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        return int(line.split()[1]) * 1024  # KB to bytes
        except Exception:
            pass
        return 4 * 1024 * 1024 * 1024  # Default 4GB
    
    def get_cpu_percent(self) -> float:
        """Get current CPU usage percentage (0-100)."""
        now = time.time()
        if now - self._last_cpu_check < self._sample_interval:
            return self._last_cpu_percent
        
        try:
            # Read from /proc/stat for accurate CPU measurement
            with open('/proc/stat', 'r') as f:
                line = f.readline()
            
            fields = line.split()[1:]  # Skip 'cpu' label
            idle = int(fields[3])
            total = sum(int(x) for x in fields[:8])
            
            if hasattr(self, '_prev_idle') and hasattr(self, '_prev_total'):
                idle_delta = idle - self._prev_idle
                total_delta = total - self._prev_total
                if total_delta > 0:
                    self._last_cpu_percent = 100.0 * (1.0 - idle_delta / total_delta)
            
            self._prev_idle = idle
            self._prev_total = total
            self._last_cpu_check = now
            
        except Exception:
            self._last_cpu_percent = 50.0  # Default if can't read
        
        return self._last_cpu_percent
    
    def get_available_memory(self) -> int:
        """Get available memory in bytes."""
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        return int(line.split()[1]) * 1024
        except Exception:
            pass
        return self._memory_total // 2  # Default 50% of total
    
    def get_optimal_threads(self, max_cpu_percent: float = 80.0) -> int:
        """
        Calculate optimal thread count based on current load.
        
        For virtual servers, we want to stay under CPU quotas to avoid:
        - Being throttled by the provider
        - Triggering cost overruns
        - Getting the container killed
        
        Args:
            max_cpu_percent: Target maximum CPU usage
        
        Returns:
            Optimal number of mining threads
        """
        current_cpu = self.get_cpu_percent()
        headroom = max(0, max_cpu_percent - current_cpu)
        
        # Calculate threads based on available headroom
        # Each thread at 100% intensity uses ~100% of one core
        available_threads = int(headroom / 100.0 * self._cpu_count)
        
        # For virtual servers, cap at 75% of cores to leave room for system
        if self._is_virtual:
            max_threads = max(1, int(self._cpu_count * 0.75))
        else:
            max_threads = self._cpu_count
        
        return max(1, min(available_threads, max_threads))
    
    def get_optimal_batch_size(self) -> int:
        """
        Calculate optimal batch size based on available resources.
        
        Larger batches are more efficient but use more memory.
        Virtual servers often have limited memory.
        """
        available_mem = self.get_available_memory()
        
        # Each hash operation uses roughly 80 bytes (header) + overhead
        # Estimate 200 bytes per hash in batch for safety
        # Use 25% of available memory for batch storage (divide total by 4)
        bytes_per_hash = 200
        max_batch_by_memory = available_mem // (bytes_per_hash * 4)
        
        # Performance sweet spot is typically 1000-10000
        if self._is_virtual:
            # Smaller batches for virtual servers (better responsiveness)
            return max(100, min(2000, max_batch_by_memory))
        else:
            return max(1000, min(10000, max_batch_by_memory))
    
    def get_recommended_intensity(self) -> int:
        """
        Get recommended mining intensity for current environment.
        
        Returns intensity 1-100 (percent).
        """
        cpu_percent = self.get_cpu_percent()
        
        if self._is_virtual:
            # Virtual servers: stay conservative to avoid throttling
            if cpu_percent > 70:
                return 30  # Low intensity when system is busy
            elif cpu_percent > 50:
                return 50  # Medium intensity
            else:
                return 70  # Higher intensity when idle
        else:
            # Physical hardware: can be more aggressive
            if cpu_percent > 80:
                return 50
            else:
                return 80
    
    def stats(self) -> dict:
        """Get resource monitoring stats."""
        return {
            "cpu_count": self._cpu_count,
            "cpu_percent": round(self.get_cpu_percent(), 1),
            "memory_total_gb": round(self._memory_total / (1024**3), 2),
            "memory_available_gb": round(self.get_available_memory() / (1024**3), 2),
            "is_virtual_server": self._is_virtual,
            "optimal_threads": self.get_optimal_threads(),
            "optimal_batch_size": self.get_optimal_batch_size(),
            "recommended_intensity": self.get_recommended_intensity(),
        }


# Global resource monitor instance
_resource_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Get the singleton resource monitor instance."""
    global _resource_monitor
    if _resource_monitor is None:
        _resource_monitor = ResourceMonitor()
    return _resource_monitor


# ══════════════════════════════════════════════════════════════════════════════
# Mining Algorithm Implementations
# ══════════════════════════════════════════════════════════════════════════════

def sha256d(data: bytes) -> bytes:
    """Double SHA-256 hash (used by Bitcoin, Litecoin headers)."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def scrypt_hash(data: bytes, n: int = 1024, r: int = 1, p: int = 1) -> bytes:
    """
    Scrypt hash (used by Litecoin, Dogecoin).
    
    Raises RuntimeError if scrypt is not available on this system.
    """
    try:
        import hashlib
        return hashlib.scrypt(data, salt=data, n=n, r=r, p=p, dklen=32)
    except (ValueError, AttributeError) as e:
        raise RuntimeError(
            "Scrypt algorithm not available on this system. "
            "Scrypt mining requires Python 3.6+ with OpenSSL support. "
            "Please use SHA256 algorithm or install a system with Scrypt support."
        ) from e


def reverse_bytes(data: bytes) -> bytes:
    """Reverse byte order (little-endian <-> big-endian)."""
    return data[::-1]


def target_to_difficulty(target: int) -> float:
    """Convert target to difficulty value."""
    if target <= 0:
        return float('inf')
    # Bitcoin difficulty 1 target
    diff1_target = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
    return diff1_target / target


def difficulty_to_target(difficulty: float) -> int:
    """Convert difficulty to target value."""
    diff1_target = 0x00000000FFFF0000000000000000000000000000000000000000000000000000
    return int(diff1_target / difficulty)


# ══════════════════════════════════════════════════════════════════════════════
# Stratum Protocol Client
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class MiningJob:
    """Represents a mining job received from the pool."""
    job_id: str
    prevhash: str
    coinbase1: str
    coinbase2: str
    merkle_branches: list[str]
    version: str
    nbits: str
    ntime: str
    clean_jobs: bool
    extranonce1: str = ""
    extranonce2_size: int = 4
    target: int = 0
    difficulty: float = 1.0


class StratumClient:
    """
    Stratum protocol client for mining pool communication.
    
    Implements the stratum mining protocol for:
    - Pool connection and authentication
    - Job subscription and notification
    - Share submission
    - Difficulty adjustment
    """
    
    def __init__(
        self,
        pool_url: str,
        username: str,
        password: str = "x",
        algorithm: str = "sha256",
    ):
        self.pool_url = pool_url
        self.username = username
        self.password = password
        self.algorithm = algorithm.lower()
        
        self._socket: Optional[socket.socket] = None
        self._connected = False
        self._subscribed = False
        self._authorized = False
        
        self._extranonce1 = ""
        self._extranonce2_size = 4
        self._current_job: Optional[MiningJob] = None
        self._job_lock = threading.Lock()
        
        self._message_id = 0
        self._pending_responses: dict[int, Callable] = {}
        
        # Stats
        self._shares_submitted = 0
        self._shares_accepted = 0
        self._shares_rejected = 0
        self._current_difficulty = 1.0
        self._hashrate = 0.0
        self._start_time = 0.0
        
        self._recv_thread: Optional[threading.Thread] = None
        self._running = False
    
    def connect(self, max_retries: int = 3, retry_delay: float = 2.0) -> bool:
        """
        Connect to the mining pool with retry logic.
        
        Args:
            max_retries: Maximum number of connection attempts (default: 3)
            retry_delay: Delay between retries in seconds (default: 2.0)
            
        Returns:
            True if connected successfully, False otherwise
        """
        for attempt in range(max_retries):
            try:
                # Parse pool URL (format: stratum+tcp://host:port or host:port)
                url = self.pool_url
                if url.startswith("stratum+tcp://"):
                    url = url[14:]
                elif url.startswith("stratum://"):
                    url = url[10:]
                elif url.startswith("stratum+ssl://"):
                    url = url[14:]  # SSL support
                
                if ":" in url:
                    host, port_str = url.rsplit(":", 1)
                    port = int(port_str)
                else:
                    host = url
                    port = 3333  # Default stratum port
                
                if attempt > 0:
                    logger.info("Retry %d/%d: Connecting to mining pool: %s:%d", 
                               attempt + 1, max_retries, host, port)
                else:
                    logger.info("Connecting to mining pool: %s:%d", host, port)
                
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Use shorter timeout for faster startup (10 seconds)
                self._socket.settimeout(10)
                self._socket.connect((host, port))
                self._connected = True
                self._start_time = time.time()
                
                # Start receive thread
                self._running = True
                self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
                self._recv_thread.start()
                
                logger.info("Connected to mining pool %s:%d", host, port)
                return True
                
            except socket.timeout:
                logger.warning("Connection timeout to %s (attempt %d/%d)", 
                              self.pool_url, attempt + 1, max_retries)
            except socket.gaierror as e:
                logger.warning("DNS resolution failed for %s: %s (attempt %d/%d)", 
                              self.pool_url, e, attempt + 1, max_retries)
            except ConnectionRefusedError:
                logger.warning("Connection refused by %s (attempt %d/%d)", 
                              self.pool_url, attempt + 1, max_retries)
            except Exception as e:
                logger.warning("Failed to connect to mining pool: %s (attempt %d/%d)", 
                              e, attempt + 1, max_retries)
            
            self._connected = False
            if attempt < max_retries - 1:
                logger.info("Waiting %.1f seconds before retry...", retry_delay)
                time.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff
        
        logger.error("Failed to connect to mining pool %s after %d attempts", 
                    self.pool_url, max_retries)
        return False
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to the pool after a connection loss."""
        logger.info("Attempting to reconnect to pool...")
        self.disconnect()
        time.sleep(1)  # Brief pause before reconnecting
        
        if self.connect(max_retries=5, retry_delay=10.0):
            if self.subscribe() and self.authorize():
                logger.info("Successfully reconnected and re-authorized")
                return True
            else:
                logger.error("Reconnected but failed to re-subscribe/re-authorize")
                self.disconnect()
        return False
    
    def subscribe(self) -> bool:
        """Subscribe to mining notifications."""
        if not self._connected:
            return False
        
        try:
            response = self._send_request("mining.subscribe", [f"NexusAI/1.0"])
            if response and isinstance(response, list) and len(response) >= 2:
                # Parse subscription response
                # Format: [[["mining.set_difficulty", "subscription id"], ["mining.notify", "subscription id"]], extranonce1, extranonce2_size]
                if isinstance(response[0], list):
                    self._extranonce1 = response[1] if len(response) > 1 else ""
                    self._extranonce2_size = response[2] if len(response) > 2 else 4
                else:
                    self._extranonce1 = response[0] if response else ""
                    self._extranonce2_size = response[1] if len(response) > 1 else 4
                
                self._subscribed = True
                logger.info("Subscribed to pool (extranonce1=%s, extranonce2_size=%d)",
                           self._extranonce1, self._extranonce2_size)
                return True
        except Exception as e:
            logger.error("Subscribe failed: %s", e)
        
        return False
    
    def authorize(self) -> bool:
        """Authorize worker with the pool."""
        if not self._connected:
            return False
        
        try:
            response = self._send_request("mining.authorize", [self.username, self.password])
            if response is True:
                self._authorized = True
                logger.info("Authorized as worker: %s", self.username)
                return True
            else:
                logger.error("Authorization failed: %s", response)
        except Exception as e:
            logger.error("Authorization error: %s", e)
        
        return False
    
    def submit_share(self, job_id: str, extranonce2: str, ntime: str, nonce: str) -> bool:
        """Submit a share to the pool."""
        if not self._authorized:
            return False
        
        try:
            self._shares_submitted += 1
            response = self._send_request(
                "mining.submit",
                [self.username, job_id, extranonce2, ntime, nonce]
            )
            if response is True:
                self._shares_accepted += 1
                logger.info("Share accepted! (accepted=%d, rejected=%d)",
                           self._shares_accepted, self._shares_rejected)
                return True
            else:
                self._shares_rejected += 1
                logger.warning("Share rejected: %s", response)
                return False
        except Exception as e:
            self._shares_rejected += 1
            logger.error("Share submission error: %s", e)
            return False
    
    def get_job(self) -> Optional[MiningJob]:
        """Get the current mining job."""
        with self._job_lock:
            return self._current_job
    
    def disconnect(self):
        """Disconnect from the pool."""
        self._running = False
        self._connected = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
        self._socket = None
    
    def stats(self) -> dict:
        """Get mining statistics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
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
                if self._shares_submitted > 0 else 0
            ),
            "hashrate": self._hashrate,
            "uptime_seconds": round(uptime),
        }
    
    def _send_request(self, method: str, params: list) -> Any:
        """Send a JSON-RPC request and wait for response."""
        if not self._socket:
            return None
        
        self._message_id += 1
        msg_id = self._message_id
        
        request = {
            "id": msg_id,
            "method": method,
            "params": params,
        }
        
        # Create a response event
        response_event = threading.Event()
        response_data = [None]
        
        def on_response(result, error):
            if error:
                response_data[0] = {"error": error}
            else:
                response_data[0] = result
            response_event.set()
        
        self._pending_responses[msg_id] = on_response
        
        try:
            message = json.dumps(request) + "\n"
            self._socket.sendall(message.encode())
            
            # Wait for response (10 second timeout)
            if response_event.wait(timeout=10):
                return response_data[0]
            else:
                logger.warning("Request timeout: %s", method)
                return None
        except Exception as e:
            logger.error("Send request error: %s", e)
            return None
        finally:
            self._pending_responses.pop(msg_id, None)
    
    @catch_thread_exceptions
    def _receive_loop(self):
        """Background thread to receive pool messages with auto-reconnection."""
        buffer = b""
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        
        while self._running:
            if not self._socket or not self._connected:
                # Connection lost, attempt to reconnect
                if reconnect_attempts < max_reconnect_attempts:
                    reconnect_attempts += 1
                    # Use exponential backoff for consistent behavior with initial connection
                    backoff_delay = 5.0 * (1.5 ** (reconnect_attempts - 1))
                    logger.info("Connection lost, attempting reconnect (%d/%d) in %.1fs...",
                               reconnect_attempts, max_reconnect_attempts, backoff_delay)
                    time.sleep(backoff_delay)
                    if self.reconnect():
                        reconnect_attempts = 0  # Reset on successful reconnect
                        buffer = b""
                        continue
                else:
                    logger.error("Max reconnection attempts reached, stopping receive loop")
                    break
                continue
            
            try:
                data = self._socket.recv(4096)
                if not data:
                    logger.warning("Pool connection closed by server")
                    self._connected = False
                    continue  # Will trigger reconnection attempt
                
                # Reset reconnect counter on successful receive
                reconnect_attempts = 0
                buffer += data
                
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line:
                        self._handle_message(line.decode().strip())
                        
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError) as e:
                logger.warning("Connection reset: %s", e)
                self._connected = False
                continue  # Will trigger reconnection attempt
            except Exception as e:
                if self._running:
                    logger.error("Receive error: %s", e)
                    self._connected = False
                break
    
    def _handle_message(self, message: str):
        """Handle incoming pool message."""
        try:
            data = json.loads(message)
            
            if "id" in data and data["id"] is not None:
                # Response to our request
                msg_id = data["id"]
                if msg_id in self._pending_responses:
                    callback = self._pending_responses[msg_id]
                    callback(data.get("result"), data.get("error"))
            
            elif "method" in data:
                # Notification from pool
                method = data["method"]
                params = data.get("params", [])
                
                if method == "mining.notify":
                    self._handle_notify(params)
                elif method == "mining.set_difficulty":
                    self._handle_set_difficulty(params)
                elif method == "mining.set_extranonce":
                    self._handle_set_extranonce(params)
                    
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from pool: %s", e)
    
    def _handle_notify(self, params: list):
        """Handle mining.notify - new job from pool."""
        if len(params) < 9:
            logger.warning("Invalid notify params: %s", params)
            return
        
        job = MiningJob(
            job_id=params[0],
            prevhash=params[1],
            coinbase1=params[2],
            coinbase2=params[3],
            merkle_branches=params[4],
            version=params[5],
            nbits=params[6],
            ntime=params[7],
            clean_jobs=params[8] if len(params) > 8 else False,
            extranonce1=self._extranonce1,
            extranonce2_size=self._extranonce2_size,
            difficulty=self._current_difficulty,
            target=difficulty_to_target(self._current_difficulty),
        )
        
        with self._job_lock:
            self._current_job = job
        
        logger.debug("New mining job: %s (difficulty=%.4f)", job.job_id, job.difficulty)
    
    def _handle_set_difficulty(self, params: list):
        """Handle mining.set_difficulty - difficulty adjustment."""
        if params:
            self._current_difficulty = float(params[0])
            logger.info("Pool difficulty set to: %.4f", self._current_difficulty)
    
    def _handle_set_extranonce(self, params: list):
        """Handle mining.set_extranonce - extranonce update."""
        if len(params) >= 2:
            self._extranonce1 = params[0]
            self._extranonce2_size = params[1]
            logger.info("Extranonce updated: %s (size=%d)", 
                       self._extranonce1, self._extranonce2_size)


# ══════════════════════════════════════════════════════════════════════════════
# CPU Miner Implementation
# ══════════════════════════════════════════════════════════════════════════════

class CPUMiner:
    """
    CPU mining implementation optimized for virtual servers.
    
    Performs hash computation using Python (for demonstration).
    For production mining, use dedicated miners like:
    - Bitcoin/SHA256: CGMiner, BFGMiner
    - Scrypt: CGMiner
    - Ethash: Ethminer (obsolete since ETH PoS)
    - RandomX: XMRig
    - KawPow: Kawpowminer
    
    Virtual Server Optimizations:
    - Adaptive thread scaling based on CPU load
    - Dynamic batch sizing for memory efficiency
    - Intensity auto-adjustment to prevent throttling
    - Resource monitoring integration
    """
    
    def __init__(
        self,
        stratum_client: StratumClient,
        threads: int = 0,
        intensity: int = 50,
        algorithm: str = "sha256",
        adaptive_mode: bool = True,
        max_cpu_percent: float = 80.0,
        vcpu_scaling: bool = True,
    ):
        self.client = stratum_client
        self.algorithm = algorithm.lower()
        self._adaptive_mode = adaptive_mode
        self._max_cpu_percent = max_cpu_percent
        self._vcpu_scaling = vcpu_scaling
        
        # Resource monitor for adaptive optimization
        self._resource_monitor = get_resource_monitor()
        
        # vCPU scaling configuration
        self._vcpu_workers = int(os.getenv("MINING_VCPU_WORKERS", "0"))
        self._vcpu_max_usage = float(os.getenv("MINING_VCPU_MAX_USAGE", "95.0"))
        
        # Auto-detect optimal thread count based on vCPU scaling
        cpu_count = multiprocessing.cpu_count()
        if self._vcpu_workers == 0:
            # Use 75% of CPUs for mining when vCPU scaling is enabled
            self._vcpu_workers = max(1, int(cpu_count * 0.75))
        
        # Initialize threads/intensity - use adaptive values if not specified
        if threads == 0 and adaptive_mode:
            # With vCPU scaling enabled, use more threads
            base_threads = self._resource_monitor.get_optimal_threads(max_cpu_percent)
            if vcpu_scaling:
                # Scale up threads based on vCPU configuration
                self.threads = max(base_threads, self._vcpu_workers)
            else:
                self.threads = base_threads
        else:
            self.threads = threads or cpu_count
        
        if intensity == 50 and adaptive_mode:  # Default value means auto-detect
            self.intensity = self._resource_monitor.get_recommended_intensity()
            # Boost intensity if vCPU scaling is enabled
            if vcpu_scaling:
                self.intensity = min(100, int(self.intensity * 1.2))  # 20% boost
        else:
            self.intensity = max(1, min(100, intensity))
        
        # Dynamic batch size - larger batches for vCPU scaling
        self._batch_size = self._resource_monitor.get_optimal_batch_size()
        if vcpu_scaling:
            # Increase batch size for better throughput with vCPU scaling
            self._batch_size = int(self._batch_size * 1.5)
        
        self._running = False
        self._paused = False  # For dynamic throttling
        self._workers: list[threading.Thread] = []
        self._resource_thread: Optional[threading.Thread] = None
        
        # Stats per thread
        self._hashes_computed = 0
        self._hashes_lock = threading.Lock()
        self._start_time = 0.0
        self._last_hashrate_time = 0.0
        self._last_hashes = 0
        
        # Adaptive mode stats
        self._thread_adjustments = 0
        self._intensity_adjustments = 0
        self._throttle_events = 0
        
        # vCPU scaling stats
        self._vcpu_active_workers = 0
        self._vcpu_total_hashes = 0
    
    def start(self):
        """Start mining workers with adaptive resource management and vCPU scaling."""
        if self._running:
            return
        
        self._running = True
        self._paused = False
        self._start_time = time.time()
        self._last_hashrate_time = time.time()
        self._hashes_computed = 0
        self._last_hashes = 0
        
        # Log environment info
        resource_stats = self._resource_monitor.stats()
        env_type = "virtual server" if resource_stats["is_virtual_server"] else "physical hardware"
        
        scaling_info = ""
        if self._vcpu_scaling:
            scaling_info = f", vCPU_workers={self._vcpu_workers}"
        
        logger.info(
            "Starting CPU miner on %s: %d threads, intensity=%d%%, algorithm=%s, batch_size=%d%s",
            env_type, self.threads, self.intensity, self.algorithm, self._batch_size, scaling_info
        )
        
        for i in range(self.threads):
            worker = threading.Thread(
                target=self._mine_worker,
                args=(i,),
                daemon=True,
                name=f"miner-{i}"
            )
            worker.start()
            self._workers.append(worker)
        
        # Start resource monitoring thread for adaptive mode
        if self._adaptive_mode:
            self._resource_thread = threading.Thread(
                target=self._resource_monitor_loop,
                daemon=True,
                name="miner-resource-monitor"
            )
            self._resource_thread.start()
            logger.info("Adaptive resource monitoring enabled (max CPU: %.0f%%)", self._max_cpu_percent)
    
    @catch_thread_exceptions
    def _resource_monitor_loop(self):
        """Background thread to monitor resources and adjust mining parameters."""
        adjustment_interval = 10.0  # Check every 10 seconds
        throttle_pause_seconds = 5  # Additional pause when CPU is critically overloaded
        
        while self._running:
            time.sleep(adjustment_interval)
            
            if not self._running:
                break
            
            try:
                cpu_percent = self._resource_monitor.get_cpu_percent()
                
                # Pause mining if CPU is critically overloaded
                if cpu_percent > 95:
                    if not self._paused:
                        self._paused = True
                        self._throttle_events += 1
                        logger.warning(
                            "CPU critically high (%.1f%%), pausing mining temporarily",
                            cpu_percent
                        )
                    time.sleep(throttle_pause_seconds)
                    continue
                elif self._paused and cpu_percent < 80:
                    self._paused = False
                    logger.info("CPU load normalized (%.1f%%), resuming mining", cpu_percent)
                
                # Adjust intensity based on current load
                if cpu_percent > self._max_cpu_percent + 10:
                    # Reduce intensity
                    new_intensity = max(10, self.intensity - 10)
                    if new_intensity != self.intensity:
                        self.intensity = new_intensity
                        self._intensity_adjustments += 1
                        logger.info(
                            "Reducing mining intensity to %d%% (CPU at %.1f%%)",
                            self.intensity, cpu_percent
                        )
                elif cpu_percent < self._max_cpu_percent - 20:
                    # Increase intensity if we have headroom
                    new_intensity = min(100, self.intensity + 5)
                    if new_intensity != self.intensity:
                        self.intensity = new_intensity
                        self._intensity_adjustments += 1
                        logger.debug(
                            "Increasing mining intensity to %d%% (CPU at %.1f%%)",
                            self.intensity, cpu_percent
                        )
                
                # Update batch size based on available memory
                optimal_batch = self._resource_monitor.get_optimal_batch_size()
                if abs(optimal_batch - self._batch_size) > self._batch_size * 0.2:
                    self._batch_size = optimal_batch
                    logger.debug("Adjusted batch size to %d", self._batch_size)
                    
            except Exception as e:
                logger.warning("Resource monitor error: %s", e)
    
    def stop(self):
        """Stop all mining workers."""
        self._running = False
        for worker in self._workers:
            worker.join(timeout=2)
        self._workers.clear()
        if self._resource_thread:
            self._resource_thread.join(timeout=2)
            self._resource_thread = None
        logger.info("CPU miner stopped")
    
    def pause(self):
        """Temporarily pause mining (for manual throttling)."""
        self._paused = True
        logger.info("Mining paused")
    
    def resume(self):
        """Resume mining after pause."""
        self._paused = False
        logger.info("Mining resumed")
    
    def get_hashrate(self) -> float:
        """Calculate current hashrate (hashes per second)."""
        now = time.time()
        elapsed = now - self._last_hashrate_time
        
        if elapsed < 1.0:
            return 0.0
        
        with self._hashes_lock:
            hashes = self._hashes_computed - self._last_hashes
            self._last_hashes = self._hashes_computed
            self._last_hashrate_time = now
        
        return hashes / elapsed
    
    def stats(self) -> dict:
        """Get miner statistics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        hashrate = self.get_hashrate()
        resource_stats = self._resource_monitor.stats()
        
        return {
            "running": self._running,
            "paused": self._paused,
            "threads": self.threads,
            "intensity": self.intensity,
            "algorithm": self.algorithm,
            "batch_size": self._batch_size,
            "hashrate": hashrate,
            "hashrate_formatted": self._format_hashrate(hashrate),
            "total_hashes": self._hashes_computed,
            "uptime_seconds": round(uptime),
            # Adaptive mode stats
            "adaptive_mode": self._adaptive_mode,
            "max_cpu_percent": self._max_cpu_percent,
            "thread_adjustments": self._thread_adjustments,
            "intensity_adjustments": self._intensity_adjustments,
            "throttle_events": self._throttle_events,
            # Resource stats
            "resources": resource_stats,
        }
    
    @staticmethod
    def _format_hashrate(hashrate: float) -> str:
        """Format hashrate with appropriate unit."""
        if hashrate >= 1e12:
            return f"{hashrate / 1e12:.2f} TH/s"
        elif hashrate >= 1e9:
            return f"{hashrate / 1e9:.2f} GH/s"
        elif hashrate >= 1e6:
            return f"{hashrate / 1e6:.2f} MH/s"
        elif hashrate >= 1e3:
            return f"{hashrate / 1e3:.2f} KH/s"
        else:
            return f"{hashrate:.2f} H/s"
    
    @catch_thread_exceptions
    def _mine_worker(self, thread_id: int):
        """Mining worker thread with adaptive resource management."""
        # Each thread uses different extranonce2 range
        extranonce2_base = thread_id * (2 ** 24)  # Split nonce space
        
        while self._running:
            # Handle pause state
            if self._paused:
                time.sleep(0.5)
                continue
            
            # Dynamic sleep time based on current intensity (may change during runtime)
            sleep_time = (100 - self.intensity) / 1000.0  # 0ms to 100ms
            
            job = self.client.get_job()
            if not job:
                time.sleep(0.1)
                continue
            
            try:
                self._mine_job(job, thread_id, extranonce2_base, sleep_time)
            except Exception as e:
                logger.error("Mining error (thread %d): %s", thread_id, e)
                time.sleep(1)
    
    def _mine_job(
        self,
        job: MiningJob,
        thread_id: int,
        extranonce2_base: int,
        sleep_time: float,
    ):
        """Mine a single job with adaptive batch sizing."""
        # Build coinbase transaction
        extranonce2_counter = extranonce2_base
        nonce = 0
        
        while self._running and not self._paused:
            # Check if job changed
            current_job = self.client.get_job()
            if not current_job or current_job.job_id != job.job_id:
                break
            
            # Use dynamic batch size (can change during runtime)
            batch_size = self._batch_size
            
            # Build extranonce2
            extranonce2 = format(extranonce2_counter, f'0{job.extranonce2_size * 2}x')
            
            # Build coinbase
            coinbase = job.coinbase1 + job.extranonce1 + extranonce2 + job.coinbase2
            coinbase_bytes = bytes.fromhex(coinbase)
            coinbase_hash = sha256d(coinbase_bytes)
            
            # Build merkle root
            merkle_root = coinbase_hash
            for branch in job.merkle_branches:
                merkle_root = sha256d(merkle_root + bytes.fromhex(branch))
            
            # Build block header (80 bytes)
            version = struct.pack("<I", int(job.version, 16))
            prev_hash = bytes.fromhex(job.prevhash)
            merkle = merkle_root
            ntime = struct.pack("<I", int(job.ntime, 16))
            nbits = struct.pack("<I", int(job.nbits, 16))
            
            header_prefix = version + prev_hash + merkle + ntime + nbits
            
            # Hash batch
            for i in range(batch_size):
                nonce_bytes = struct.pack("<I", nonce)
                header = header_prefix + nonce_bytes
                
                # Compute hash based on algorithm
                if self.algorithm in ("sha256", "sha256d"):
                    block_hash = sha256d(header)
                elif self.algorithm == "scrypt":
                    block_hash = scrypt_hash(header)
                elif self.algorithm in ("ethash", "randomx", "kawpow"):
                    # These algorithms require external miners (XMRig, T-Rex, etc.)
                    # CPU implementation is not practical for these memory-hard algorithms
                    raise RuntimeError(
                        f"Algorithm '{self.algorithm}' requires an external miner. "
                        f"Built-in CPU mining only supports: sha256, sha256d, scrypt. "
                        f"For {self.algorithm}, please use XMRig, T-Rex, or similar external mining software."
                    )
                else:
                    raise RuntimeError(
                        f"Unsupported mining algorithm: '{self.algorithm}'. "
                        f"Supported algorithms: sha256, sha256d, scrypt. "
                        f"For ethash/randomx/kawpow, use an external miner."
                    )
                
                with self._hashes_lock:
                    self._hashes_computed += 1
                
                # Check if hash meets target (little-endian comparison)
                hash_int = int.from_bytes(block_hash, 'little')
                if hash_int < job.target:
                    # Found a valid share!
                    nonce_hex = format(nonce, '08x')
                    logger.info("Share found! nonce=%s hash=%s", 
                               nonce_hex, block_hash.hex())
                    self.client.submit_share(
                        job.job_id,
                        extranonce2,
                        job.ntime,
                        nonce_hex,
                    )
                
                nonce += 1
                if nonce >= 0xFFFFFFFF:
                    nonce = 0
                    extranonce2_counter += 1
            
            # Rate limiting based on intensity
            if sleep_time > 0:
                time.sleep(sleep_time)


# ══════════════════════════════════════════════════════════════════════════════
# PoW Mining Strategy
# ══════════════════════════════════════════════════════════════════════════════

class PoWMiningStrategy(BaseStrategy):
    """
    Proof-of-Work mining strategy optimized for virtual servers and GPU instances.
    
    Supports both CPU and GPU mining to compete with dedicated hardware:
    
    CPU Mining (built-in):
    - Bitcoin (SHA-256)
    - Litecoin/Dogecoin (Scrypt)
    
    GPU Mining (via external miners):
    - Ethereum Classic (Etchash)
    - Ravencoin (KawPow)
    - Ergo (Autolykos2)
    - Kaspa (kHeavyHash)
    - Monero (RandomX)
    
    Hardware Competitive Features:
    - Multi-GPU support with per-device monitoring
    - External miner integration (T-Rex, lolMiner, XMRig, GMiner)
    - Mining pool failover for high availability
    - Automatic profit switching to most profitable coin
    - Hardware monitoring (temp, power, hashrate)
    
    Virtual Server & Cloud GPU Features:
    - Adaptive thread/intensity management to prevent throttling
    - Memory-efficient batch processing
    - Auto-detection of cloud environment (AWS, GCP, Azure GPU instances)
    - Resource monitoring and automatic adjustment
    """
    
    name = "pow_mining"
    
    def __init__(self, blockchain_manager: Any, config: Any):
        super().__init__(blockchain_manager, config)
        
        self._stratum: Optional[StratumClient] = None
        self._miner: Optional[CPUMiner] = None
        self._running = False
        self._lock = threading.Lock()
        
        # Mining session stats
        self._session_start = 0.0
        self._estimated_earnings_usd = 0.0
        self._last_share_time = 0.0
        
        # Resource monitor for environment detection
        self._resource_monitor = get_resource_monitor()
        
        # GPU mining components (if available)
        self._gpu_detector: Optional["GPUDetector"] = None
        self._external_miner: Optional["ExternalMinerManager"] = None
        self._pool_failover: Optional["PoolFailoverManager"] = None
        self._profit_switcher: Optional["ProfitSwitcher"] = None
        self._gpu_mining_active = False
        self._current_mining_coin = ""
        
        # GPU optimizer for maximum efficiency
        self._gpu_optimizer: Optional["GPUOptimizer"] = None
        self._multi_gpu_orchestrator: Optional["MultiGPUOrchestrator"] = None
        
        # AI optimizer for intelligent optimization
        # AI optimization enabled by default for maximum performance
        self._ai_optimizer: Optional["AIMiningOptimizer"] = None
        self._ai_optimization_enabled = getattr(self.config, 'MINING_AI_OPTIMIZATION', True)
        self._ai_optimization_thread: Optional[threading.Thread] = None
        self._last_ai_optimization: float = 0.0
        self._last_snapshot: Optional["MiningSnapshot"] = None
        
        # Pool discovery for auto-configuration
        self._pool_discovery = None
        self._auto_configured_pool = None  # Store auto-selected pool info
        # Instance variables for auto-configured values (separate from shared config)
        self._auto_pool_url: Optional[str] = None
        self._auto_pool_user: Optional[str] = None
        self._auto_algorithm: Optional[str] = None
        
        # Initialize GPU mining if module available
        if GPU_MINING_AVAILABLE:
            self._init_gpu_mining()
        
        # Initialize GPU optimizer if available
        if GPU_OPTIMIZER_AVAILABLE:
            self._init_gpu_optimizer()
        
        # Initialize AI optimizer if available
        if AI_OPTIMIZER_AVAILABLE:
            self._init_ai_optimizer()
        
        # Initialize pool discovery service
        self._init_pool_discovery()
        
        # Initialize if configured
        if self._is_configured():
            self._initialize()
    
    def _init_gpu_optimizer(self):
        """Initialize GPU optimizer for maximum efficiency."""
        try:
            self._gpu_optimizer = get_gpu_optimizer()
            self._multi_gpu_orchestrator = get_multi_gpu_orchestrator()
            logger.info("GPU optimizer initialized")
            
            # Apply optimal profiles to detected GPUs
            if self._gpu_detector:
                devices = self._gpu_detector.detect_devices()
                for dev in devices:
                    profile = self._gpu_optimizer.get_profile_for_gpu(dev.name, dev.memory_mb)
                    self._gpu_optimizer.apply_profile(dev.device_id, profile)
                    logger.info("Applied optimization profile for GPU %d: %s", dev.device_id, dev.name)
                    
        except Exception as e:
            logger.warning("Failed to initialize GPU optimizer: %s", e)
    
    def _init_ai_optimizer(self):
        """Initialize AI mining optimizer (uses enhanced v2 if available)."""
        try:
            # Try enhanced v2 optimizer first
            if ENHANCED_AI_AVAILABLE:
                self._ai_optimizer = get_enhanced_ai_mining_optimizer()
                logger.info("Enhanced AI mining optimizer v2 initialized - ensemble learning enabled")
            else:
                self._ai_optimizer = get_ai_mining_optimizer()
                logger.info("AI mining optimizer v1 initialized - learning from mining performance")
        except Exception as e:
            logger.warning("Failed to initialize AI optimizer: %s", e)
    
    def _init_pool_discovery(self):
        """Initialize pool discovery service."""
        try:
            from nexus.strategies.mining_pool_discovery import get_pool_discovery
            self._pool_discovery = get_pool_discovery()
            self._pool_discovery.start()
            logger.info("Pool discovery service initialized")
        except Exception as e:
            logger.warning("Failed to initialize pool discovery: %s", e)
            self._pool_discovery = None
    
    def _init_gpu_mining(self):
        """Initialize GPU mining components."""
        try:
            self._gpu_detector = get_gpu_detector()
            self._external_miner = get_miner_manager()
            self._pool_failover = PoolFailoverManager()
            
            # Initialize profit switcher with config values
            hashrate_mhs = getattr(self.config, 'MINING_EXPECTED_HASHRATE_MHS', 30.0)
            power_watts = getattr(self.config, 'MINING_GPU_POWER_WATTS', 120.0)
            electricity_cost = getattr(self.config, 'MINING_ELECTRICITY_COST_KWH', 0.10)
            
            self._profit_switcher = ProfitSwitcher(
                hashrate_mhs=hashrate_mhs,
                power_watts=power_watts,
                electricity_cost_kwh=electricity_cost,
            )
            
            # Detect GPUs
            devices = self._gpu_detector.detect_devices()
            if devices:
                logger.info("GPU mining available: %d device(s) detected", len(devices))
                for dev in devices:
                    logger.info("  [%d] %s - %d MB VRAM", dev.device_id, dev.name, dev.memory_mb)
            
            # Add backup pools if configured
            backup_pools = getattr(self.config, 'MINING_BACKUP_POOLS', '')
            if backup_pools:
                for i, pool_url in enumerate(backup_pools.split(',')):
                    pool_url = pool_url.strip()
                    if pool_url:
                        self._pool_failover.add_pool(
                            url=pool_url,
                            username=self.config.MINING_POOL_USER,
                            password=self.config.MINING_POOL_PASSWORD,
                            algorithm=self.config.MINING_ALGORITHM,
                            priority=i + 1,
                            is_backup=True
                        )
                        
        except Exception as e:
            logger.warning("Failed to initialize GPU mining: %s", e)
    
    def _is_configured(self) -> bool:
        """Check if mining is configured or can auto-configure."""
        # If explicitly configured, use those settings
        if self.config.MINING_POOL_URL and self.config.MINING_POOL_USER:
            return True
        
        # If pool discovery is available, try to auto-configure
        if self._pool_discovery:
            return self._try_auto_configure()
        
        return False
    
    def _try_auto_configure(self) -> bool:
        """
        Attempt to auto-configure mining from discovered pools.
        
        This is called when MINING_POOL_URL or MINING_POOL_USER is not set,
        allowing the system to automatically select a profitable pool.
        
        Returns:
            True if auto-configuration succeeded, False otherwise
        """
        if not self._pool_discovery:
            return False
        
        try:
            # Get the best discovered pool (sorted by profitability internally)
            best_pool = self._pool_discovery.get_best_pool()
            if not best_pool:
                logger.warning("No mining pools discovered for auto-configuration")
                return False
            
            # Check if pool is online or unknown status
            if best_pool.status.value in ("online", "unknown"):
                # Use this pool
                self._auto_configured_pool = best_pool
                
                # Create a worker name from wallet address or generate one
                wallet = getattr(self.config, 'MINING_PAYOUT_ADDRESS', '') or \
                         getattr(self.config, 'WALLET_ADDRESS', '')
                if wallet:
                    worker_name = f"{wallet[:8]}.nexus"
                else:
                    import secrets
                    worker_name = f"nexus_{secrets.token_hex(4)}.worker"
                
                # Store auto-configured values in instance variables instead of modifying shared config
                # This prevents unexpected side effects from config mutation
                self._auto_pool_url = best_pool.url
                self._auto_pool_user = worker_name
                self._auto_algorithm = best_pool.algorithm.value
                
                # Also update config for backward compatibility
                self.config.MINING_POOL_URL = best_pool.url
                self.config.MINING_POOL_USER = worker_name
                self.config.MINING_ALGORITHM = best_pool.algorithm.value
                
                logger.info(
                    "Auto-configured mining: pool=%s, coin=%s, est_daily=$%.2f",
                    best_pool.name, best_pool.coin, best_pool.estimated_daily_usd
                )
                return True
            
            logger.warning("Best pool is offline: %s", best_pool.name)
            return False
            
        except Exception as e:
            logger.warning("Auto-configuration failed: %s", e)
            return False
    
    def _get_adaptive_mode(self) -> bool:
        """Determine if adaptive mode should be enabled."""
        # Enable adaptive mode by default on virtual servers
        return getattr(self.config, 'MINING_ADAPTIVE_MODE', True)
    
    def _get_max_cpu_percent(self) -> float:
        """Get maximum CPU usage percentage."""
        return getattr(self.config, 'MINING_MAX_CPU_PERCENT', 80.0)
    
    def _should_use_gpu(self) -> bool:
        """Determine if GPU mining should be used."""
        # Check if GPU mining is enabled in config
        gpu_enabled = getattr(self.config, 'MINING_USE_GPU', True)
        if not gpu_enabled:
            return False
        
        # Check if GPU module is available and GPUs are detected
        if not GPU_MINING_AVAILABLE or not self._gpu_detector:
            return False
        
        # Check if we have GPUs
        if not self._gpu_detector.has_gpu:
            return False
        
        # Check if algorithm is GPU-compatible
        algo = self.config.MINING_ALGORITHM.lower()
        gpu_algorithms = {'ethash', 'etchash', 'kawpow', 'autolykos2', 'kheavyhash', 
                         'blake3', 'octopus', 'randomx', 'dynex'}
        return algo in gpu_algorithms
    
    def _initialize(self):
        """Initialize stratum client and miner with adaptive optimization."""
        with self._lock:
            if self._stratum:
                return  # Already initialized
            
            # Check if we should use simulation mode
            # Simulation mode is used when:
            # 1. DRY_RUN is enabled
            # 2. Pool URL is not configured or starts with "simulated://"
            # 3. Pool URL is explicitly set to test mode
            use_simulation = (
                hasattr(self.config, 'DRY_RUN') and self.config.DRY_RUN or
                not self.config.MINING_POOL_URL or
                self.config.MINING_POOL_URL.startswith("simulated://") or
                self.config.MINING_POOL_URL == "test"
            )
            
            # Add primary pool to failover manager
            if self._pool_failover and self.config.MINING_POOL_URL:
                self._pool_failover.add_pool(
                    url=self.config.MINING_POOL_URL,
                    username=self.config.MINING_POOL_USER,
                    password=self.config.MINING_POOL_PASSWORD,
                    algorithm=self.config.MINING_ALGORITHM,
                    priority=0,
                    is_backup=False
                )
            
            # Create stratum client (real or simulated)
            if use_simulation:
                try:
                    from nexus.strategies.pool_manager import SimulatedStratumClient
                    self._stratum = SimulatedStratumClient(
                        pool_url=self.config.MINING_POOL_URL or "simulated://localhost:3333",
                        username=self.config.MINING_POOL_USER or "test.worker",
                        password=self.config.MINING_POOL_PASSWORD or "x",
                        algorithm=self.config.MINING_ALGORITHM,
                    )
                    logger.info("Using SIMULATED pool (dry-run/test mode)")
                except ImportError:
                    # Fallback to regular client
                    self._stratum = StratumClient(
                        pool_url=self.config.MINING_POOL_URL or "",
                        username=self.config.MINING_POOL_USER or "test",
                        password=self.config.MINING_POOL_PASSWORD or "x",
                        algorithm=self.config.MINING_ALGORITHM,
                    )
            else:
                self._stratum = StratumClient(
                    pool_url=self.config.MINING_POOL_URL,
                    username=self.config.MINING_POOL_USER,
                    password=self.config.MINING_POOL_PASSWORD,
                    algorithm=self.config.MINING_ALGORITHM,
                )
            
            self._miner = CPUMiner(
                stratum_client=self._stratum,
                threads=self.config.MINING_THREADS,
                intensity=self.config.MINING_INTENSITY,
                algorithm=self.config.MINING_ALGORITHM,
                adaptive_mode=self._get_adaptive_mode(),
                max_cpu_percent=self._get_max_cpu_percent(),
            )
            
            # Log environment info
            resource_stats = self._resource_monitor.stats()
            env_type = "virtual server" if resource_stats["is_virtual_server"] else "physical"
            
            gpu_info = ""
            if self._gpu_detector and self._gpu_detector.has_gpu:
                devices = self._gpu_detector.detect_devices()
                gpu_info = f", {len(devices)} GPU(s)"
            
            mode_info = " (SIMULATION MODE)" if use_simulation else ""
            
            logger.info(
                "Mining initialized on %s: %d CPUs, %.1fGB RAM%s, adaptive mode=%s%s",
                env_type,
                resource_stats["cpu_count"],
                resource_stats["memory_total_gb"],
                gpu_info,
                self._get_adaptive_mode(),
                mode_info
            )
    
    def start_mining(self) -> bool:
        """Start the mining session (CPU or GPU based on hardware)."""
        if not self._is_configured():
            logger.warning("PoW mining not configured. Set MINING_POOL_URL and MINING_POOL_USER.")
            return False
        
        with self._lock:
            if self._running:
                return True
            
            if not self._stratum:
                self._initialize()
            
            # Determine mining mode
            use_gpu = self._should_use_gpu()
            
            if use_gpu and self._external_miner:
                # Start GPU mining with external miner
                return self._start_gpu_mining()
            else:
                # Start CPU mining
                return self._start_cpu_mining()
    
    def _start_cpu_mining(self) -> bool:
        """Start CPU-based mining with automatic fallback to simulation mode."""
        # Try to connect to pool
        pool_connected = False
        try:
            pool_connected = self._stratum.connect()
        except Exception as e:
            logger.warning("Pool connection error: %s", e)
        
        if not pool_connected:
            # Fall back to simulation mode for testing/development
            logger.warning("Pool connection failed, switching to simulation mode for vGPU mining")
            if not SIMULATION_AVAILABLE:
                logger.error("Simulation mode not available")
                return False
            self._stratum = SimulatedStratumClient(
                pool_url="simulated://vgpu-mining:3333",
                username=self.config.MINING_POOL_USER or "nexus.worker",
                password=self.config.MINING_POOL_PASSWORD or "x",
                algorithm=self.config.MINING_ALGORITHM,
            )
            if not self._stratum.connect():
                logger.error("Even simulated pool failed")
                return False
        
        # Subscribe and authorize
        if not self._stratum.subscribe():
            self._stratum.disconnect()
            logger.warning("Pool subscription failed, attempting simulation mode")
            return self._start_simulated_mining()
        
        if not self._stratum.authorize():
            self._stratum.disconnect()
            logger.warning("Pool authorization failed, attempting simulation mode")
            return self._start_simulated_mining()
        
        # Start miner
        self._miner.start()
        self._running = True
        self._gpu_mining_active = False
        self._session_start = time.time()
        
        # Start AI optimization loop if enabled
        if self._ai_optimization_enabled and self._ai_optimizer:
            self._start_ai_optimization_loop()
        
        logger.info("CPU mining started: pool=%s, algorithm=%s",
                   self.config.MINING_POOL_URL,
                   self.config.MINING_ALGORITHM)
        return True
    
    def _start_simulated_mining(self) -> bool:
        """
        Start mining when pool connection is unavailable.
        
        IMPORTANT: This now uses REAL computation instead of simulation!
        If MINING_VGPU_REAL_COMPUTE is enabled (default), real hashes are computed.
        """
        # Check if real compute is enabled and available
        use_real_compute = getattr(self.config, 'MINING_VGPU_REAL_COMPUTE', True)
        
        if use_real_compute and REAL_COMPUTE_AVAILABLE:
            return self._start_real_vgpu_compute()
        
        # Fall back to legacy simulation mode if real compute is disabled
        if not SIMULATION_AVAILABLE:
            logger.error("Neither real compute nor simulation mode available")
            return False
        
        logger.warning("Using SIMULATION mode - no real hashes computed!")
        logger.warning("Set MINING_VGPU_REAL_COMPUTE=true for real computation")
        
        try:
            self._stratum = SimulatedStratumClient(
                pool_url="simulated://vgpu-mining:3333",
                username=self.config.MINING_POOL_USER or "nexus.worker",
                password=self.config.MINING_POOL_PASSWORD or "x",
                algorithm=self.config.MINING_ALGORITHM,
            )
            
            if not self._stratum.connect():
                return False
            if not self._stratum.subscribe():
                return False
            if not self._stratum.authorize():
                return False
            
            # Recreate miner with simulated stratum
            self._miner = CPUMiner(
                stratum_client=self._stratum,
                threads=self.config.MINING_THREADS,
                intensity=self.config.MINING_INTENSITY,
                algorithm=self.config.MINING_ALGORITHM,
                adaptive_mode=self._get_adaptive_mode(),
                max_cpu_percent=self._get_max_cpu_percent(),
            )
            
            self._miner.start()
            self._running = True
            self._gpu_mining_active = False
            self._session_start = time.time()
            
            logger.info("SIMULATION MODE: vGPU mining started (no real pool connection)")
            return True
            
        except Exception as e:
            logger.error("Failed to start simulated mining: %s", e)
            return False
    
    def _start_real_vgpu_compute(self) -> bool:
        """
        Start REAL vGPU computation.
        
        This performs ACTUAL hash computations using:
        1. CPU threads (built-in, always available)
        2. XMRig (if installed)
        3. External GPU miners (if GPU available)
        4. Cloud GPU rental (if API configured)
        
        Unlike simulation, every hash counted here is a REAL cryptographic operation.
        """
        if not REAL_COMPUTE_AVAILABLE:
            logger.error("Real vGPU compute module not available")
            return False
        
        try:
            logger.info("=" * 60)
            logger.info("STARTING REAL vGPU COMPUTATION")
            logger.info("=" * 60)
            logger.info("This performs ACTUAL hash computations - not simulation!")
            
            # Initialize real compute manager
            compute_mode_str = getattr(self.config, 'MINING_VGPU_COMPUTE_MODE', 'auto')
            try:
                compute_mode = VGPUComputeMode(compute_mode_str)
            except ValueError:
                compute_mode = VGPUComputeMode.AUTO
            
            self._real_compute = initialize_real_compute(
                pool_url=self.config.MINING_POOL_URL or "",
                wallet_address=self.config.MINING_PAYOUT_ADDRESS or self.config.WALLET_ADDRESS or "",
                algorithm=self.config.MINING_ALGORITHM,
                mode=compute_mode,
            )
            
            # Start computation
            if not self._real_compute.start():
                logger.error("Failed to start real compute")
                return False
            
            self._running = True
            self._gpu_mining_active = True  # Real compute counts as GPU mining
            self._session_start = time.time()
            
            logger.info("REAL vGPU compute started successfully!")
            logger.info("  Mode: %s", self._real_compute.active_engine)
            logger.info("  Algorithm: %s", self.config.MINING_ALGORITHM)
            logger.info("  Computing REAL cryptographic hashes")
            
            # Start a monitoring thread
            self._real_compute_monitor = threading.Thread(
                target=self._monitor_real_compute,
                daemon=True,
                name="real-compute-monitor"
            )
            self._real_compute_monitor.start()
            
            return True
            
        except Exception as e:
            logger.error("Failed to start real vGPU compute: %s", e)
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _monitor_real_compute(self):
        """Monitor and log real compute stats."""
        while self._running and hasattr(self, '_real_compute') and self._real_compute:
            try:
                stats = self._real_compute.get_stats()
                if stats.hashrate > 0:
                    logger.info(
                        "REAL COMPUTE: %s @ %s | Hashes: %d | Shares: %d",
                        stats.algorithm,
                        stats.to_dict().get('hashrate_formatted', '0 H/s'),
                        stats.hashes_computed,
                        stats.shares_submitted,
                    )
            except Exception as e:
                logger.debug("Monitor error: %s", e)
            
            time.sleep(30)  # Log every 30 seconds
    
    def _start_gpu_mining(self) -> bool:
        """Start GPU-based mining with external miner."""
        if not GPU_MINING_AVAILABLE or not self._external_miner:
            logger.warning("GPU mining not available, falling back to CPU")
            return self._start_cpu_mining()
        
        # Get GPU devices
        devices = self._gpu_detector.detect_devices() if self._gpu_detector else []
        if not devices:
            logger.warning("No GPU devices found, falling back to CPU")
            return self._start_cpu_mining()
        
        # Apply GPU optimizer profiles for maximum efficiency
        if self._gpu_optimizer:
            for dev in devices:
                profile = self._gpu_optimizer.get_profile_for_gpu(dev.name, dev.memory_mb)
                self._gpu_optimizer.apply_profile(dev.device_id, profile)
                expected_hashrate = profile.expected_hashrates.get(self.config.MINING_ALGORITHM.lower(), 0)
                logger.info(
                    "GPU %d optimized: expected %.2f MH/s for %s",
                    dev.device_id, expected_hashrate / 1e6, self.config.MINING_ALGORITHM
                )
        
        # Determine best miner for the algorithm
        algo_str = self.config.MINING_ALGORITHM.lower()
        algo_map = {
            'ethash': MiningAlgorithm.ETHASH,
            'etchash': MiningAlgorithm.ETCHASH,
            'kawpow': MiningAlgorithm.KAWPOW,
            'autolykos2': MiningAlgorithm.AUTOLYKOS2,
            'kheavyhash': MiningAlgorithm.KHEAVYHASH,
            'blake3': MiningAlgorithm.BLAKE3,
            'randomx': MiningAlgorithm.RANDOMX,
            'octopus': MiningAlgorithm.OCTOPUS,
        }
        
        algorithm = algo_map.get(algo_str)
        if not algorithm:
            logger.warning("Algorithm %s not supported for GPU mining, using CPU", algo_str)
            return self._start_cpu_mining()
        
        # Get best miner for this algorithm and GPU
        primary_gpu = devices[0]
        miner_type = self._external_miner.get_best_miner_for_algorithm(
            algorithm, primary_gpu.vendor
        )
        
        if not miner_type:
            logger.warning("No external miner available for %s, using CPU", algo_str)
            return self._start_cpu_mining()
        
        # Configure external miner with optimized settings
        device_ids = [d.device_id for d in devices]
        
        # Get optimized intensity from GPU optimizer
        optimized_intensity = self.config.MINING_INTENSITY
        if self._gpu_optimizer:
            profile = self._gpu_optimizer._profiles.get(devices[0].device_id)
            if profile:
                optimized_intensity = profile.intensity
        
        config = ExternalMinerConfig(
            miner_type=miner_type,
            pool_url=self.config.MINING_POOL_URL,
            wallet_address=self.config.MINING_PAYOUT_ADDRESS or self.config.MINING_POOL_USER,
            worker_name=self.config.MINING_POOL_USER.split('.')[-1] if '.' in self.config.MINING_POOL_USER else "nexus",
            algorithm=algo_str,
            devices=device_ids,
            intensity=optimized_intensity,
        )
        
        # Add GPU optimizer arguments if available
        if self._gpu_optimizer:
            extra_args = self._gpu_optimizer.get_miner_args(devices[0].device_id, algo_str)
            config.extra_args.extend(extra_args)
        
        # Start external miner
        if self._external_miner.start(config):
            self._running = True
            self._gpu_mining_active = True
            self._session_start = time.time()
            self._current_mining_coin = algo_str.upper()
            
            # Start AI optimization loop
            if self._ai_optimization_enabled and self._ai_optimizer:
                self._start_ai_optimization_loop()
            
            # Start GPU monitoring
            if self._gpu_optimizer:
                self._gpu_optimizer.start_monitoring()
            
            logger.info(
                "GPU mining started: %s miner, %d GPU(s), pool=%s, algorithm=%s, AI optimization=%s",
                miner_type.value, len(devices), self.config.MINING_POOL_URL, algo_str,
                "enabled" if self._ai_optimization_enabled else "disabled"
            )
            return True
        else:
            logger.error("Failed to start external miner, falling back to CPU")
            return self._start_cpu_mining()
    
    def _start_ai_optimization_loop(self):
        """Start the AI optimization background loop."""
        if self._ai_optimization_thread and self._ai_optimization_thread.is_alive():
            return
        
        self._ai_optimization_thread = threading.Thread(
            target=self._ai_optimization_loop,
            daemon=True,
            name="ai-mining-optimizer"
        )
        self._ai_optimization_thread.start()
        logger.info("AI mining optimization loop started")
    
    @catch_thread_exceptions
    def _ai_optimization_loop(self):
        """Background loop for AI-driven mining optimization."""
        optimization_interval = 30.0  # Optimize every 30 seconds
        
        while self._running:
            time.sleep(optimization_interval)
            
            if not self._running or not self._ai_optimizer:
                break
            
            try:
                # Create mining snapshot from current state
                snapshot = self._create_mining_snapshot()
                if not snapshot:
                    continue
                
                # Record snapshot for learning
                self._ai_optimizer.record_snapshot(snapshot)
                
                # Get AI optimization recommendation
                result = self._ai_optimizer.optimize(snapshot)
                
                # Apply optimization if confidence is high enough
                if result.confidence >= 0.6 and result.decision != MiningDecision.CONTINUE:
                    self._apply_ai_optimization(result, snapshot)
                
                # Store snapshot for learning feedback
                self._last_snapshot = snapshot
                
            except Exception as e:
                logger.warning("AI optimization error: %s", e)
    
    def _create_mining_snapshot(self) -> Optional["MiningSnapshot"]:
        """Create a snapshot of current mining state for AI."""
        if not AI_OPTIMIZER_AVAILABLE:
            return None
        
        # Get miner stats
        miner_stats = self._miner.stats() if self._miner else {}
        stratum_stats = self._stratum.stats() if self._stratum else {}
        resource_stats = self._resource_monitor.stats()
        
        # Get GPU stats if available
        gpu_temp = 0.0
        gpu_power = 0.0
        gpu_fan = 0.0
        gpu_name = "CPU"
        memory_used = 0
        
        if self._gpu_detector and self._gpu_mining_active:
            devices = self._gpu_detector.update_device_stats()
            if devices:
                dev = devices[0]
                gpu_temp = dev.temperature
                gpu_power = dev.power_usage_watts
                gpu_fan = dev.fan_speed_percent
                gpu_name = dev.name
                memory_used = dev.memory_mb
        else:
            # Use CPU stats
            gpu_temp = resource_stats.get("cpu_percent", 50)  # Proxy temp from CPU load
            gpu_power = resource_stats.get("cpu_count", 4) * 10  # Estimate
        
        # Get external miner stats if GPU mining
        ext_stats = {}
        if self._gpu_mining_active and self._external_miner:
            ext_stats = self._external_miner.get_stats()
        
        hashrate = ext_stats.get("hashrate", miner_stats.get("hashrate", 0))
        accepted = ext_stats.get("shares_accepted", stratum_stats.get("shares_accepted", 0))
        rejected = ext_stats.get("shares_rejected", stratum_stats.get("shares_rejected", 0))
        
        # Get intensity
        intensity = miner_stats.get("intensity", self.config.MINING_INTENSITY)
        if self._gpu_optimizer:
            profile = self._gpu_optimizer._profiles.get(0)
            if profile:
                intensity = profile.intensity
        
        return create_mining_snapshot(
            gpu_id=0,
            gpu_name=gpu_name,
            temperature_c=gpu_temp,
            power_watts=gpu_power,
            fan_speed_percent=gpu_fan,
            memory_used_mb=memory_used,
            algorithm=self.config.MINING_ALGORITHM,
            coin=self._current_mining_coin or self.config.MINING_ALGORITHM.upper(),
            intensity=intensity,
            hashrate=hashrate,
            accepted_shares=accepted,
            rejected_shares=rejected,
            coin_price_usd=0.0,  # Would be fetched from price feed
            network_difficulty=stratum_stats.get("difficulty", 1.0),
            estimated_daily_usd=self._estimated_earnings_usd * 24,
            electricity_cost_kwh=getattr(self.config, 'MINING_ELECTRICITY_COST_KWH', 0.10),
        )
    
    def _apply_ai_optimization(self, result: "OptimizationResult", snapshot: "MiningSnapshot"):
        """Apply AI optimization recommendation."""
        logger.info(
            "AI optimization: %s (confidence: %.1f%%) - %s",
            result.decision.value, result.confidence * 100, result.reasoning
        )
        
        old_snapshot = self._last_snapshot
        
        # Apply recommended settings
        if "intensity" in result.recommended_settings:
            new_intensity = result.recommended_settings["intensity"]
            self.update_intensity(int(new_intensity))
        
        if "power_limit_percent" in result.recommended_settings:
            if self._gpu_optimizer:
                new_power = result.recommended_settings["power_limit_percent"]
                self._gpu_optimizer._set_nvidia_power_limit(snapshot.gpu_id, int(new_power))
        
        # Handle specific decisions
        if result.decision == MiningDecision.COOL_DOWN:
            # Reduce intensity and increase fan
            self.update_intensity(max(50, snapshot.intensity - 20))
            logger.warning("AI initiated cool-down - reducing mining intensity")
        
        elif result.decision == MiningDecision.SWITCH_COIN:
            if self._profit_switcher:
                best = self._profit_switcher.get_most_profitable()
                if best:
                    logger.info("AI recommends switching to %s", best.coin)
                    # Would trigger coin switch
        
        # Learn from the result (will be evaluated on next snapshot)
        if old_snapshot and self._ai_optimizer:
            self._ai_optimizer.learn_from_result(old_snapshot, snapshot, result.decision)
    
    def stop_mining(self):
        """Stop the mining session (CPU and/or GPU)."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            
            # Stop real vGPU compute if active
            if hasattr(self, '_real_compute') and self._real_compute:
                self._real_compute.stop()
                self._real_compute = None
            
            # Stop GPU mining if active
            if self._gpu_mining_active and self._external_miner:
                self._external_miner.stop()
                self._gpu_mining_active = False
            
            # Stop CPU mining
            if self._miner:
                self._miner.stop()
            
            if self._stratum:
                self._stratum.disconnect()
            
            logger.info("PoW mining stopped")
    
    def find_opportunities(self) -> list[Opportunity]:
        """
        Return mining status as an opportunity.
        
        Unlike DeFi strategies that find external opportunities,
        PoW mining is a continuous process. This method returns
        the current mining session as an "opportunity" with
        estimated earnings based on hashrate and pool stats.
        
        Note: Mining must be explicitly started via start_mining().
        This method is read-only and does not start mining automatically.
        """
        opportunities = []
        
        if not self._is_configured():
            return opportunities
        
        # Mining must be explicitly started - do not auto-start
        # This prevents unexpected CPU consumption
        if not self._running or not self._stratum:
            return opportunities
        
        # Get stats
        stratum_stats = self._stratum.stats()
        miner_stats = self._miner.stats() if self._miner else {}
        
        # Earnings estimation - THIS IS FOR DEMONSTRATION ONLY
        # Real mining profitability depends on:
        # 1. Current cryptocurrency market price
        # 2. Network difficulty (constantly changing)
        # 3. Block reward (halves over time for BTC)
        # 4. Pool fees (typically 1-3%)
        # 5. Electricity costs (not factored here)
        # The $0.001/share estimate is purely illustrative and NOT accurate!
        hashrate = miner_stats.get("hashrate", 0)
        accepted_shares = stratum_stats.get("shares_accepted", 0)
        estimated_usd = accepted_shares * 0.001  # DEMO VALUE ONLY
        self._estimated_earnings_usd = estimated_usd
        
        # Create opportunity representing mining session
        if stratum_stats.get("connected") and miner_stats.get("running"):
            opp = self._make_opportunity(
                opp_type=OpportunityType.POW_MINING,
                chain="pow",  # Not blockchain-specific
                description=(
                    f"Mining {self.config.MINING_ALGORITHM.upper()} @ "
                    f"{miner_stats.get('hashrate_formatted', '0 H/s')} | "
                    f"Shares: {accepted_shares}/{stratum_stats.get('shares_submitted', 0)}"
                ),
                profit_usd=estimated_usd,
                confidence=stratum_stats.get("accept_rate", 0) / 100.0,
                details={
                    "type": "pow_mining",
                    "algorithm": self.config.MINING_ALGORITHM,
                    "pool_url": self.config.MINING_POOL_URL,
                    "worker": self.config.MINING_POOL_USER,
                    "stratum": stratum_stats,
                    "miner": miner_stats,
                    "session_duration": time.time() - self._session_start,
                },
            )
            opportunities.append(opp)
        
        return opportunities
    
    def pause_mining(self):
        """Temporarily pause mining (keeps connection but stops hashing)."""
        if self._miner:
            self._miner.pause()
    
    def resume_mining(self):
        """Resume mining after pause."""
        if self._miner:
            self._miner.resume()
    
    def update_intensity(self, intensity: int):
        """Update mining intensity (1-100)."""
        if self._miner:
            self._miner.intensity = max(1, min(100, intensity))
            logger.info("Mining intensity updated to %d%%", self._miner.intensity)
    
    def update_threads(self, threads: int):
        """Update number of mining threads (requires restart to take effect)."""
        if threads > 0 and self._miner:
            logger.info("Thread count update queued (%d threads) - requires restart", threads)
    
    def status(self) -> dict:
        """Get mining strategy status with virtual server and GPU optimization details."""
        with self._lock:
            stratum_stats = self._stratum.stats() if self._stratum else {}
            miner_stats = self._miner.stats() if self._miner else {}
            resource_stats = self._resource_monitor.stats()
            
            # Get GPU mining stats if active
            gpu_stats = {}
            if self._gpu_mining_active and self._external_miner:
                gpu_stats = self._external_miner.get_stats()
            
            # Fix connection status for GPU mining and real vGPU compute modes
            # These modes don't use the stratum client directly, so we need to
            # reflect the actual connection status based on the active mining mode
            if self._running:
                # Check if real vGPU compute is active
                real_compute_active = (
                    hasattr(self, '_real_compute') and 
                    self._real_compute is not None and
                    hasattr(self._real_compute, 'is_running') and
                    self._real_compute.is_running
                )
                
                # Check if external GPU miner is active
                external_miner_active = (
                    self._gpu_mining_active and 
                    self._external_miner is not None and
                    self._external_miner.is_running
                )
                
                # If either GPU mode or real compute is running, we're connected
                if real_compute_active or external_miner_active:
                    stratum_stats = stratum_stats.copy() if stratum_stats else {}
                    stratum_stats['connected'] = True
            
            # Get GPU device info
            gpu_devices = []
            if GPU_MINING_AVAILABLE and self._gpu_detector:
                devices = self._gpu_detector.update_device_stats()
                gpu_devices = [d.to_dict() for d in devices]
            
            # Get profit switching info
            profitability = []
            current_best_coin = None
            if self._profit_switcher:
                profitability = self._profit_switcher.get_all_profitability()
                best = self._profit_switcher.get_most_profitable()
                if best:
                    current_best_coin = best.coin
            
            # Get pool failover info
            pools = []
            if self._pool_failover:
                pools = self._pool_failover.get_all_pools()
            
            return {
                "name": self.name,
                "configured": self._is_configured(),
                "running": self._running,
                "pool_url": self.config.MINING_POOL_URL,
                "algorithm": self.config.MINING_ALGORITHM,
                "stratum": stratum_stats,
                "miner": miner_stats,
                "estimated_earnings_usd": round(self._estimated_earnings_usd, 6),
                "session_duration": (
                    time.time() - self._session_start
                    if self._session_start else 0
                ),
                # Virtual server optimization info
                "environment": {
                    "is_virtual_server": resource_stats["is_virtual_server"],
                    "cpu_count": resource_stats["cpu_count"],
                    "cpu_percent": resource_stats["cpu_percent"],
                    "memory_total_gb": resource_stats["memory_total_gb"],
                    "memory_available_gb": resource_stats["memory_available_gb"],
                    "adaptive_mode": self._get_adaptive_mode(),
                    "max_cpu_percent": self._get_max_cpu_percent(),
                },
                # GPU mining info
                "gpu_mining": {
                    "available": GPU_MINING_AVAILABLE and self._gpu_detector and self._gpu_detector.has_gpu,
                    "active": self._gpu_mining_active,
                    "devices": gpu_devices,
                    "external_miner_stats": gpu_stats,
                    "available_miners": self._get_available_miners_list(),
                },
                # Profit switching info
                "profit_switching": {
                    "enabled": self._profit_switcher is not None,
                    "current_coin": self._current_mining_coin,
                    "most_profitable_coin": current_best_coin,
                    "profitability_data": profitability,
                },
                # Pool failover info
                "pool_failover": {
                    "enabled": len(pools) > 1,
                    "pools": pools,
                },
                # Pool discovery info
                "pool_discovery": self._get_pool_discovery_status(),
            }
    
    # ── Helper Methods for Status Reporting ─────────────────────────────────────
    
    def _get_available_miners_list(self) -> list[str]:
        """Get list of available mining software including built-in CPU miner."""
        miners = []
        
        # Always include built-in CPU miner
        miners.append("cpu_builtin")
        
        # Add external miners if available
        if self._external_miner:
            for m in self._external_miner.available_miners:
                miners.append(m.value)
        
        return miners
    
    def _get_pool_discovery_status(self) -> dict:
        """Get status of pool discovery service."""
        try:
            if self._pool_discovery:
                stats = self._pool_discovery.get_stats()
                return {
                    "active": self._pool_discovery._running,
                    "total_pools": stats.get("total_pools", 0),
                    "online_pools": stats.get("online_pools", 0),
                    "algorithms": stats.get("algorithms", []),
                    "coins": stats.get("coins", []),
                    "auto_select_enabled": stats.get("auto_select_enabled", False),
                    "selected_pool": stats.get("selected_pool"),
                }
        except Exception as e:
            logger.debug("Error getting pool discovery status: %s", e)
        
        return {
            "active": False,
            "total_pools": 0,
            "online_pools": 0,
            "algorithms": [],
            "coins": [],
            "auto_select_enabled": False,
            "selected_pool": None,
        }
    
    # ── GPU Mining Control Methods ────────────────────────────────────────────
    
    def get_gpu_devices(self) -> list[dict]:
        """Get information about available GPU devices."""
        if not GPU_MINING_AVAILABLE or not self._gpu_detector:
            return []
        devices = self._gpu_detector.detect_devices()
        self._gpu_detector.update_device_stats()
        return [d.to_dict() for d in devices]
    
    def switch_coin(self, coin: str) -> bool:
        """
        Switch mining to a different coin (requires restart).
        
        Args:
            coin: Coin symbol (e.g., 'RVN', 'ETC', 'ERG')
        
        Returns:
            True if switch was initiated
        """
        if not self._profit_switcher:
            logger.warning("Profit switcher not available")
            return False
        
        # Get coin configuration from profit switcher
        profitability = self._profit_switcher.get_all_profitability()
        coin_data = next((c for c in profitability if c["coin"] == coin.upper()), None)
        
        if not coin_data:
            logger.warning("Coin %s not found in profitability data", coin)
            return False
        
        # Stop current mining
        was_running = self._running
        if was_running:
            self.stop_mining()
        
        # Update configuration (in memory, config file would need separate update)
        self._current_mining_coin = coin.upper()
        logger.info("Switching mining to %s", coin.upper())
        
        # Restart if was running
        if was_running:
            return self.start_mining()
        
        return True
    
    def enable_profit_switching(self, threshold_percent: float = 10.0):
        """
        Enable automatic profit switching.
        
        Args:
            threshold_percent: Minimum profit improvement to trigger switch
        """
        if not self._profit_switcher:
            logger.warning("Profit switcher not available")
            return
        
        # Start background thread for profit monitoring
        if not hasattr(self, '_profit_switch_thread') or not self._profit_switch_thread.is_alive():
            self._profit_switch_enabled = True
            self._profit_switch_threshold = threshold_percent
            self._profit_switch_thread = threading.Thread(
                target=self._profit_switch_loop,
                daemon=True,
                name="profit-switcher"
            )
            self._profit_switch_thread.start()
            logger.info("Profit switching enabled (threshold: %.1f%%)", threshold_percent)
    
    def disable_profit_switching(self):
        """Disable automatic profit switching."""
        self._profit_switch_enabled = False
        logger.info("Profit switching disabled")
    
    @catch_thread_exceptions
    def _profit_switch_loop(self):
        """Background loop for profit-based coin switching."""
        check_interval = 300.0  # Check every 5 minutes
        
        while getattr(self, '_profit_switch_enabled', False) and self._running:
            time.sleep(check_interval)
            
            if not self._profit_switcher or not self._running:
                continue
            
            try:
                new_coin = self._profit_switcher.should_switch(
                    self._current_mining_coin,
                    getattr(self, '_profit_switch_threshold', 10.0)
                )
                
                if new_coin:
                    logger.info("Profit switch recommended: %s -> %s", 
                               self._current_mining_coin, new_coin)
                    self.switch_coin(new_coin)
                    
            except Exception as e:
                logger.warning("Profit switch check failed: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# Module-level helper functions for external access
# ══════════════════════════════════════════════════════════════════════════════

def get_mining_environment_info() -> dict:
    """
    Get comprehensive mining environment information.
    
    Includes both CPU and GPU capabilities for the dashboard.
    """
    monitor = get_resource_monitor()
    cpu_stats = monitor.stats()
    
    # Add GPU info if available
    gpu_info = {}
    if GPU_MINING_AVAILABLE:
        try:
            gpu_info = get_gpu_mining_info()
        except Exception as e:
            logger.debug("Failed to get GPU info: %s", e)
            gpu_info = {"has_gpu": False, "error": str(e)}
    else:
        gpu_info = {"has_gpu": False, "gpu_mining_module": False}
    
    return {
        **cpu_stats,
        "gpu": gpu_info,
    }
