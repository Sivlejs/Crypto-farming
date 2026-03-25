"""
PoW Mining Strategy for Nexus AI.

Implements Proof-of-Work cryptocurrency mining similar to dedicated mining machines:
- Connects to mining pools via Stratum protocol
- Supports multiple algorithms: SHA-256, Scrypt, Ethash, RandomX, KawPow
- CPU mining implementation (GPU requires external miners like CGMiner/XMRig)
- Automatic difficulty adjustment
- Mining stats tracking and profitability estimation
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
    CPU mining implementation.
    
    Performs hash computation using Python (for demonstration).
    For production mining, use dedicated miners like:
    - Bitcoin/SHA256: CGMiner, BFGMiner
    - Scrypt: CGMiner
    - Ethash: Ethminer (obsolete since ETH PoS)
    - RandomX: XMRig
    - KawPow: Kawpowminer
    """
    
    def __init__(
        self,
        stratum_client: StratumClient,
        threads: int = 0,
        intensity: int = 50,
        algorithm: str = "sha256",
    ):
        self.client = stratum_client
        self.threads = threads or multiprocessing.cpu_count()
        self.intensity = max(1, min(100, intensity))
        self.algorithm = algorithm.lower()
        
        self._running = False
        self._workers: list[threading.Thread] = []
        
        # Stats per thread
        self._hashes_computed = 0
        self._hashes_lock = threading.Lock()
        self._start_time = 0.0
        self._last_hashrate_time = 0.0
        self._last_hashes = 0
    
    def start(self):
        """Start mining workers."""
        if self._running:
            return
        
        self._running = True
        self._start_time = time.time()
        self._last_hashrate_time = time.time()
        self._hashes_computed = 0
        self._last_hashes = 0
        
        logger.info("Starting CPU miner: %d threads, intensity=%d%%, algorithm=%s",
                   self.threads, self.intensity, self.algorithm)
        
        for i in range(self.threads):
            worker = threading.Thread(
                target=self._mine_worker,
                args=(i,),
                daemon=True,
                name=f"miner-{i}"
            )
            worker.start()
            self._workers.append(worker)
    
    def stop(self):
        """Stop all mining workers."""
        self._running = False
        for worker in self._workers:
            worker.join(timeout=2)
        self._workers.clear()
        logger.info("CPU miner stopped")
    
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
        
        return {
            "running": self._running,
            "threads": self.threads,
            "intensity": self.intensity,
            "algorithm": self.algorithm,
            "hashrate": hashrate,
            "hashrate_formatted": self._format_hashrate(hashrate),
            "total_hashes": self._hashes_computed,
            "uptime_seconds": round(uptime),
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
        """Mining worker thread."""
        # Each thread uses different extranonce2 range
        extranonce2_base = thread_id * (2 ** 24)  # Split nonce space
        
        # Intensity affects sleep time between batches
        sleep_time = (100 - self.intensity) / 1000.0  # 0ms to 100ms
        
        while self._running:
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
        """Mine a single job."""
        # Build coinbase transaction
        extranonce2_counter = extranonce2_base
        nonce = 0
        batch_size = 1000  # Hashes per batch
        
        while self._running:
            # Check if job changed
            current_job = self.client.get_job()
            if not current_job or current_job.job_id != job.job_id:
                break
            
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
    Proof-of-Work mining strategy.
    
    Connects to mining pools and performs CPU mining for cryptocurrencies:
    - Bitcoin (SHA-256)
    - Litecoin/Dogecoin (Scrypt)
    - Monero (RandomX) - requires external miner
    - Ravencoin (KawPow) - requires external miner
    
    This strategy runs continuously and generates "mining opportunities"
    that represent mining session status and estimated earnings.
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
        
        # Initialize if configured
        if self._is_configured():
            self._initialize()
    
    def _is_configured(self) -> bool:
        """Check if mining is configured."""
        return bool(
            self.config.MINING_POOL_URL and
            self.config.MINING_POOL_USER
        )
    
    def _initialize(self):
        """Initialize stratum client and miner."""
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
    
    def status(self) -> dict:
        """Get mining strategy status."""
        with self._lock:
            stratum_stats = self._stratum.stats() if self._stratum else {}
            miner_stats = self._miner.stats() if self._miner else {}
            
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
            }
