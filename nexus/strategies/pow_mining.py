"""
PoW Mining Strategy for Nexus AI.

Implements Proof-of-Work cryptocurrency mining similar to dedicated mining machines:
- Connects to mining pools via Stratum protocol
- Supports multiple algorithms: SHA-256, Scrypt, Ethash, RandomX, KawPow
- CPU mining implementation (GPU requires external miners like CGMiner/XMRig)
- Automatic difficulty adjustment
- Mining stats tracking and profitability estimation

Virtual Server Optimizations:
- Adaptive thread management based on CPU load
- Memory-efficient mining modes for cloud environments
- Dynamic batch sizing for optimal performance
- Auto-detection of optimal mining parameters
- Resource throttling to prevent provider throttling/termination
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
from typing import Any, Callable, Optional

from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.logger import get_logger

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
    
    def connect(self) -> bool:
        """Connect to the mining pool."""
        try:
            # Parse pool URL (format: stratum+tcp://host:port or host:port)
            url = self.pool_url
            if url.startswith("stratum+tcp://"):
                url = url[14:]
            elif url.startswith("stratum://"):
                url = url[10:]
            
            if ":" in url:
                host, port_str = url.rsplit(":", 1)
                port = int(port_str)
            else:
                host = url
                port = 3333  # Default stratum port
            
            logger.info("Connecting to mining pool: %s:%d", host, port)
            
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(30)
            self._socket.connect((host, port))
            self._connected = True
            self._start_time = time.time()
            
            # Start receive thread
            self._running = True
            self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._recv_thread.start()
            
            logger.info("Connected to mining pool %s:%d", host, port)
            return True
            
        except Exception as e:
            logger.error("Failed to connect to mining pool: %s", e)
            self._connected = False
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
    
    def _receive_loop(self):
        """Background thread to receive pool messages."""
        buffer = b""
        
        while self._running and self._socket:
            try:
                data = self._socket.recv(4096)
                if not data:
                    logger.warning("Pool connection closed")
                    self._connected = False
                    break
                
                buffer += data
                
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line:
                        self._handle_message(line.decode().strip())
                        
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.error("Receive error: %s", e)
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
    ):
        self.client = stratum_client
        self.algorithm = algorithm.lower()
        self._adaptive_mode = adaptive_mode
        self._max_cpu_percent = max_cpu_percent
        
        # Resource monitor for adaptive optimization
        self._resource_monitor = get_resource_monitor()
        
        # Initialize threads/intensity - use adaptive values if not specified
        if threads == 0 and adaptive_mode:
            self.threads = self._resource_monitor.get_optimal_threads(max_cpu_percent)
        else:
            self.threads = threads or multiprocessing.cpu_count()
        
        if intensity == 50 and adaptive_mode:  # Default value means auto-detect
            self.intensity = self._resource_monitor.get_recommended_intensity()
        else:
            self.intensity = max(1, min(100, intensity))
        
        # Dynamic batch size
        self._batch_size = self._resource_monitor.get_optimal_batch_size()
        
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
    
    def start(self):
        """Start mining workers with adaptive resource management."""
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
        logger.info(
            "Starting CPU miner on %s: %d threads, intensity=%d%%, algorithm=%s, batch_size=%d",
            env_type, self.threads, self.intensity, self.algorithm, self._batch_size
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
    Proof-of-Work mining strategy optimized for virtual servers.
    
    Connects to mining pools and performs CPU mining for cryptocurrencies:
    - Bitcoin (SHA-256)
    - Litecoin/Dogecoin (Scrypt)
    - Monero (RandomX) - requires external miner
    - Ravencoin (KawPow) - requires external miner
    
    This strategy runs continuously and generates "mining opportunities"
    that represent mining session status and estimated earnings.
    
    Virtual Server Features:
    - Adaptive thread/intensity management to prevent throttling
    - Memory-efficient batch processing
    - Auto-detection of cloud environment characteristics
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
        
        # Initialize if configured
        if self._is_configured():
            self._initialize()
    
    def _is_configured(self) -> bool:
        """Check if mining is configured."""
        return bool(
            self.config.MINING_POOL_URL and
            self.config.MINING_POOL_USER
        )
    
    def _get_adaptive_mode(self) -> bool:
        """Determine if adaptive mode should be enabled."""
        # Enable adaptive mode by default on virtual servers
        return getattr(self.config, 'MINING_ADAPTIVE_MODE', True)
    
    def _get_max_cpu_percent(self) -> float:
        """Get maximum CPU usage percentage."""
        return getattr(self.config, 'MINING_MAX_CPU_PERCENT', 80.0)
    
    def _initialize(self):
        """Initialize stratum client and miner with adaptive optimization."""
        with self._lock:
            if self._stratum:
                return  # Already initialized
            
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
            if resource_stats["is_virtual_server"]:
                logger.info(
                    "Mining initialized on virtual server: %d CPUs, %.1fGB RAM, adaptive mode=%s",
                    resource_stats["cpu_count"],
                    resource_stats["memory_total_gb"],
                    self._get_adaptive_mode()
                )
    
    def start_mining(self) -> bool:
        """Start the mining session."""
        if not self._is_configured():
            logger.warning("PoW mining not configured. Set MINING_POOL_URL and MINING_POOL_USER.")
            return False
        
        with self._lock:
            if self._running:
                return True
            
            if not self._stratum:
                self._initialize()
            
            # Connect to pool
            if not self._stratum.connect():
                return False
            
            # Subscribe and authorize
            if not self._stratum.subscribe():
                self._stratum.disconnect()
                return False
            
            if not self._stratum.authorize():
                self._stratum.disconnect()
                return False
            
            # Start miner
            self._miner.start()
            self._running = True
            self._session_start = time.time()
            
            logger.info("PoW mining started: pool=%s, algorithm=%s",
                       self.config.MINING_POOL_URL,
                       self.config.MINING_ALGORITHM)
            return True
    
    def stop_mining(self):
        """Stop the mining session."""
        with self._lock:
            if not self._running:
                return
            
            self._running = False
            
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
        """Get mining strategy status with virtual server optimization details."""
        with self._lock:
            stratum_stats = self._stratum.stats() if self._stratum else {}
            miner_stats = self._miner.stats() if self._miner else {}
            resource_stats = self._resource_monitor.stats()
            
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
            }


# ══════════════════════════════════════════════════════════════════════════════
# Module-level helper functions for external access
# ══════════════════════════════════════════════════════════════════════════════

def get_mining_environment_info() -> dict:
    """
    Get information about the current mining environment.
    
    Useful for displaying environment info in the dashboard
    without needing to start mining.
    """
    monitor = get_resource_monitor()
    return monitor.stats()
