"""
Mining Pool Discovery and Profitability Analysis for Nexus AI.

This module provides:
- Automatic discovery of PoW mining pools
- Real-time profitability estimation
- Pool health and reliability monitoring
- AI-powered pool selection
- Dashboard integration for pool display

The system can operate in two modes:
1. Manual: User selects pools from the dashboard
2. Automatic: AI selects and starts mining on most profitable pools
"""
from __future__ import annotations

import hashlib
import json
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ══════════════════════════════════════════════════════════════════════════════

class MiningAlgorithmType(str, Enum):
    """Supported mining algorithms."""
    SHA256 = "sha256"
    SCRYPT = "scrypt"
    ETHASH = "ethash"
    ETCHASH = "etchash"
    KAWPOW = "kawpow"
    RANDOMX = "randomx"
    AUTOLYKOS = "autolykos"
    KHEAVYHASH = "kheavyhash"
    BLAKE3 = "blake3"
    EQUIHASH = "equihash"
    CRYPTONIGHT = "cryptonight"


class PoolStatus(str, Enum):
    """Pool connection status."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class MiningPool:
    """Represents a mining pool with all relevant information."""
    id: str
    name: str
    url: str  # Stratum URL (e.g., stratum+tcp://pool.example.com:3333)
    algorithm: MiningAlgorithmType
    coin: str  # Coin symbol (e.g., BTC, ETH, RVN)
    coin_name: str  # Full coin name
    
    # Pool metrics
    fee_percent: float = 1.0
    min_payout: float = 0.0
    hashrate: float = 0.0  # Pool total hashrate
    miners: int = 0  # Number of active miners
    blocks_found_24h: int = 0
    
    # Profitability (updated periodically)
    estimated_daily_usd: float = 0.0
    estimated_daily_coins: float = 0.0
    coin_price_usd: float = 0.0
    network_difficulty: float = 0.0
    block_reward: float = 0.0
    
    # Connection info
    status: PoolStatus = PoolStatus.UNKNOWN
    latency_ms: float = 0.0
    last_checked: float = 0.0
    
    # Additional info
    region: str = "global"
    ssl_port: Optional[int] = None
    backup_urls: List[str] = field(default_factory=list)
    payout_methods: List[str] = field(default_factory=list)
    website: str = ""
    
    # AI metrics
    reliability_score: float = 0.5  # 0-1, based on uptime and consistency
    profitability_score: float = 0.5  # 0-1, relative to other pools
    overall_score: float = 0.5  # Combined score for AI selection
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API/dashboard."""
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "algorithm": self.algorithm.value,
            "coin": self.coin,
            "coin_name": self.coin_name,
            "fee_percent": self.fee_percent,
            "min_payout": self.min_payout,
            "hashrate": self.hashrate,
            "hashrate_formatted": self._format_hashrate(self.hashrate),
            "miners": self.miners,
            "blocks_found_24h": self.blocks_found_24h,
            "estimated_daily_usd": round(self.estimated_daily_usd, 4),
            "estimated_daily_coins": round(self.estimated_daily_coins, 8),
            "coin_price_usd": round(self.coin_price_usd, 4),
            "network_difficulty": self.network_difficulty,
            "block_reward": self.block_reward,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 1),
            "last_checked": self.last_checked,
            "last_checked_ago": self._time_ago(self.last_checked),
            "region": self.region,
            "ssl_port": self.ssl_port,
            "backup_urls": self.backup_urls,
            "payout_methods": self.payout_methods,
            "website": self.website,
            "reliability_score": round(self.reliability_score, 3),
            "profitability_score": round(self.profitability_score, 3),
            "overall_score": round(self.overall_score, 3),
        }
    
    @staticmethod
    def _format_hashrate(hashrate: float) -> str:
        """Format hashrate with appropriate unit."""
        if hashrate >= 1e18:
            return f"{hashrate / 1e18:.2f} EH/s"
        elif hashrate >= 1e15:
            return f"{hashrate / 1e15:.2f} PH/s"
        elif hashrate >= 1e12:
            return f"{hashrate / 1e12:.2f} TH/s"
        elif hashrate >= 1e9:
            return f"{hashrate / 1e9:.2f} GH/s"
        elif hashrate >= 1e6:
            return f"{hashrate / 1e6:.2f} MH/s"
        elif hashrate >= 1e3:
            return f"{hashrate / 1e3:.2f} KH/s"
        else:
            return f"{hashrate:.2f} H/s"
    
    @staticmethod
    def _time_ago(timestamp: float) -> str:
        """Format timestamp as time ago string."""
        if timestamp == 0:
            return "never"
        delta = time.time() - timestamp
        if delta < 60:
            return f"{int(delta)}s ago"
        elif delta < 3600:
            return f"{int(delta/60)}m ago"
        elif delta < 86400:
            return f"{int(delta/3600)}h ago"
        else:
            return f"{int(delta/86400)}d ago"


@dataclass
class PoolDiscoveryResult:
    """Result from pool discovery."""
    pools: List[MiningPool]
    total_found: int
    discovery_time_ms: float
    source: str
    errors: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# Pool Database (Known Pools)
# ══════════════════════════════════════════════════════════════════════════════

# Pre-configured list of well-known mining pools
KNOWN_POOLS: List[Dict[str, Any]] = [
    # Bitcoin (SHA256)
    {
        "name": "F2Pool Bitcoin",
        "url": "stratum+tcp://btc.f2pool.com:3333",
        "algorithm": MiningAlgorithmType.SHA256,
        "coin": "BTC",
        "coin_name": "Bitcoin",
        "fee_percent": 2.5,
        "min_payout": 0.005,
        "region": "global",
        "website": "https://www.f2pool.com/",
    },
    {
        "name": "ViaBTC Bitcoin",
        "url": "stratum+tcp://btc.viabtc.com:3333",
        "algorithm": MiningAlgorithmType.SHA256,
        "coin": "BTC",
        "coin_name": "Bitcoin",
        "fee_percent": 4.0,
        "min_payout": 0.0005,
        "region": "global",
        "website": "https://www.viabtc.com/",
    },
    {
        "name": "Poolin Bitcoin",
        "url": "stratum+tcp://btc.ss.poolin.me:443",
        "algorithm": MiningAlgorithmType.SHA256,
        "coin": "BTC",
        "coin_name": "Bitcoin",
        "fee_percent": 2.5,
        "min_payout": 0.001,
        "region": "global",
        "website": "https://www.poolin.me/",
    },
    # Litecoin (Scrypt)
    {
        "name": "F2Pool Litecoin",
        "url": "stratum+tcp://ltc.f2pool.com:8888",
        "algorithm": MiningAlgorithmType.SCRYPT,
        "coin": "LTC",
        "coin_name": "Litecoin",
        "fee_percent": 2.5,
        "min_payout": 0.02,
        "region": "global",
        "website": "https://www.f2pool.com/",
    },
    {
        "name": "ViaBTC Litecoin",
        "url": "stratum+tcp://ltc.viabtc.com:3333",
        "algorithm": MiningAlgorithmType.SCRYPT,
        "coin": "LTC",
        "coin_name": "Litecoin",
        "fee_percent": 4.0,
        "min_payout": 0.001,
        "region": "global",
        "website": "https://www.viabtc.com/",
    },
    {
        "name": "LitecoinPool",
        "url": "stratum+tcp://us.litecoinpool.org:3333",
        "algorithm": MiningAlgorithmType.SCRYPT,
        "coin": "LTC",
        "coin_name": "Litecoin",
        "fee_percent": 0.0,
        "min_payout": 0.01,
        "region": "us",
        "website": "https://www.litecoinpool.org/",
    },
    # Dogecoin (Scrypt)
    {
        "name": "F2Pool Dogecoin",
        "url": "stratum+tcp://doge.f2pool.com:5588",
        "algorithm": MiningAlgorithmType.SCRYPT,
        "coin": "DOGE",
        "coin_name": "Dogecoin",
        "fee_percent": 3.0,
        "min_payout": 40,
        "region": "global",
        "website": "https://www.f2pool.com/",
    },
    {
        "name": "ViaBTC Dogecoin",
        "url": "stratum+tcp://doge.viabtc.com:3335",
        "algorithm": MiningAlgorithmType.SCRYPT,
        "coin": "DOGE",
        "coin_name": "Dogecoin",
        "fee_percent": 4.0,
        "min_payout": 50,
        "region": "global",
        "website": "https://www.viabtc.com/",
    },
    # Ethereum Classic (Etchash)
    {
        "name": "F2Pool ETC",
        "url": "stratum+tcp://etc.f2pool.com:8118",
        "algorithm": MiningAlgorithmType.ETCHASH,
        "coin": "ETC",
        "coin_name": "Ethereum Classic",
        "fee_percent": 1.0,
        "min_payout": 0.1,
        "region": "global",
        "website": "https://www.f2pool.com/",
    },
    {
        "name": "2Miners ETC",
        "url": "stratum+tcp://etc.2miners.com:1010",
        "algorithm": MiningAlgorithmType.ETCHASH,
        "coin": "ETC",
        "coin_name": "Ethereum Classic",
        "fee_percent": 1.0,
        "min_payout": 0.05,
        "region": "global",
        "ssl_port": 11010,
        "website": "https://2miners.com/etc-mining-pool",
    },
    {
        "name": "Ethermine ETC",
        "url": "stratum+tcp://etc.ethermine.org:4444",
        "algorithm": MiningAlgorithmType.ETCHASH,
        "coin": "ETC",
        "coin_name": "Ethereum Classic",
        "fee_percent": 1.0,
        "min_payout": 0.1,
        "region": "global",
        "ssl_port": 5555,
        "website": "https://etc.ethermine.org/",
    },
    # Ravencoin (KawPow)
    {
        "name": "2Miners RVN",
        "url": "stratum+tcp://rvn.2miners.com:6060",
        "algorithm": MiningAlgorithmType.KAWPOW,
        "coin": "RVN",
        "coin_name": "Ravencoin",
        "fee_percent": 1.0,
        "min_payout": 10,
        "region": "global",
        "ssl_port": 16060,
        "website": "https://2miners.com/rvn-mining-pool",
    },
    {
        "name": "Flypool RVN",
        "url": "stratum+tcp://stratum-ravencoin.flypool.org:3333",
        "algorithm": MiningAlgorithmType.KAWPOW,
        "coin": "RVN",
        "coin_name": "Ravencoin",
        "fee_percent": 1.0,
        "min_payout": 10,
        "region": "global",
        "ssl_port": 3443,
        "website": "https://ravencoin.flypool.org/",
    },
    {
        "name": "HeroMiners RVN",
        "url": "stratum+tcp://ravencoin.herominers.com:1140",
        "algorithm": MiningAlgorithmType.KAWPOW,
        "coin": "RVN",
        "coin_name": "Ravencoin",
        "fee_percent": 0.9,
        "min_payout": 5,
        "region": "global",
        "ssl_port": 1145,
        "website": "https://ravencoin.herominers.com/",
    },
    # Monero (RandomX) - CPU Mining
    {
        "name": "2Miners XMR",
        "url": "stratum+tcp://xmr.2miners.com:2222",
        "algorithm": MiningAlgorithmType.RANDOMX,
        "coin": "XMR",
        "coin_name": "Monero",
        "fee_percent": 1.0,
        "min_payout": 0.01,
        "region": "global",
        "ssl_port": 12222,
        "website": "https://2miners.com/xmr-mining-pool",
    },
    {
        "name": "SupportXMR",
        "url": "stratum+tcp://pool.supportxmr.com:3333",
        "algorithm": MiningAlgorithmType.RANDOMX,
        "coin": "XMR",
        "coin_name": "Monero",
        "fee_percent": 0.6,
        "min_payout": 0.1,
        "region": "global",
        "ssl_port": 9000,
        "website": "https://supportxmr.com/",
    },
    {
        "name": "MoneroOcean",
        "url": "stratum+tcp://gulf.moneroocean.stream:10128",
        "algorithm": MiningAlgorithmType.RANDOMX,
        "coin": "XMR",
        "coin_name": "Monero",
        "fee_percent": 0.0,
        "min_payout": 0.003,
        "region": "global",
        "website": "https://moneroocean.stream/",
    },
    {
        "name": "HashVault XMR",
        "url": "stratum+tcp://pool.hashvault.pro:3333",
        "algorithm": MiningAlgorithmType.RANDOMX,
        "coin": "XMR",
        "coin_name": "Monero",
        "fee_percent": 0.9,
        "min_payout": 0.01,
        "region": "global",
        "ssl_port": 443,
        "website": "https://monero.hashvault.pro/",
    },
    # Ergo (Autolykos)
    {
        "name": "2Miners ERG",
        "url": "stratum+tcp://erg.2miners.com:8888",
        "algorithm": MiningAlgorithmType.AUTOLYKOS,
        "coin": "ERG",
        "coin_name": "Ergo",
        "fee_percent": 1.0,
        "min_payout": 0.5,
        "region": "global",
        "ssl_port": 18888,
        "website": "https://2miners.com/erg-mining-pool",
    },
    {
        "name": "HeroMiners ERG",
        "url": "stratum+tcp://ergo.herominers.com:1180",
        "algorithm": MiningAlgorithmType.AUTOLYKOS,
        "coin": "ERG",
        "coin_name": "Ergo",
        "fee_percent": 0.9,
        "min_payout": 0.5,
        "region": "global",
        "ssl_port": 1185,
        "website": "https://ergo.herominers.com/",
    },
    {
        "name": "Flypool ERG",
        "url": "stratum+tcp://stratum-ergo.flypool.org:3333",
        "algorithm": MiningAlgorithmType.AUTOLYKOS,
        "coin": "ERG",
        "coin_name": "Ergo",
        "fee_percent": 1.0,
        "min_payout": 0.5,
        "region": "global",
        "ssl_port": 3443,
        "website": "https://ergo.flypool.org/",
    },
    # Kaspa (KHeavyHash)
    {
        "name": "2Miners KAS",
        "url": "stratum+tcp://kas.2miners.com:2020",
        "algorithm": MiningAlgorithmType.KHEAVYHASH,
        "coin": "KAS",
        "coin_name": "Kaspa",
        "fee_percent": 1.0,
        "min_payout": 10,
        "region": "global",
        "ssl_port": 12020,
        "website": "https://2miners.com/kas-mining-pool",
    },
    {
        "name": "HeroMiners KAS",
        "url": "stratum+tcp://kaspa.herominers.com:1206",
        "algorithm": MiningAlgorithmType.KHEAVYHASH,
        "coin": "KAS",
        "coin_name": "Kaspa",
        "fee_percent": 0.9,
        "min_payout": 5,
        "region": "global",
        "ssl_port": 1209,
        "website": "https://kaspa.herominers.com/",
    },
    # Flux (Equihash)
    {
        "name": "2Miners FLUX",
        "url": "stratum+tcp://flux.2miners.com:2020",
        "algorithm": MiningAlgorithmType.EQUIHASH,
        "coin": "FLUX",
        "coin_name": "Flux",
        "fee_percent": 1.0,
        "min_payout": 1,
        "region": "global",
        "ssl_port": 12020,
        "website": "https://2miners.com/flux-mining-pool",
    },
    # Alephium (Blake3)
    {
        "name": "2Miners ALPH",
        "url": "stratum+tcp://alph.2miners.com:2020",
        "algorithm": MiningAlgorithmType.BLAKE3,
        "coin": "ALPH",
        "coin_name": "Alephium",
        "fee_percent": 1.0,
        "min_payout": 0.5,
        "region": "global",
        "ssl_port": 12020,
        "website": "https://2miners.com/alph-mining-pool",
    },
    {
        "name": "HeroMiners ALPH",
        "url": "stratum+tcp://alephium.herominers.com:1199",
        "algorithm": MiningAlgorithmType.BLAKE3,
        "coin": "ALPH",
        "coin_name": "Alephium",
        "fee_percent": 0.9,
        "min_payout": 0.1,
        "region": "global",
        "ssl_port": 1198,
        "website": "https://alephium.herominers.com/",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# Price Fetcher
# ══════════════════════════════════════════════════════════════════════════════

class CoinPriceFetcher:
    """Fetch cryptocurrency prices from public APIs."""
    
    # CoinGecko IDs for coins
    COINGECKO_IDS = {
        "BTC": "bitcoin",
        "LTC": "litecoin",
        "DOGE": "dogecoin",
        "ETC": "ethereum-classic",
        "RVN": "ravencoin",
        "XMR": "monero",
        "ERG": "ergo",
        "KAS": "kaspa",
        "FLUX": "zelcash",
        "ALPH": "alephium",
    }
    
    def __init__(self):
        self._cache: Dict[str, Tuple[float, float]] = {}  # coin -> (price, timestamp)
        self._cache_ttl = 300  # 5 minutes
        self._lock = threading.Lock()
    
    def get_price(self, coin: str) -> float:
        """Get current price for a coin in USD."""
        with self._lock:
            # Check cache
            if coin in self._cache:
                price, ts = self._cache[coin]
                if time.time() - ts < self._cache_ttl:
                    return price
        
        # Fetch fresh price
        try:
            price = self._fetch_price(coin)
            with self._lock:
                self._cache[coin] = (price, time.time())
            return price
        except Exception as e:
            logger.warning("Failed to fetch price for %s: %s", coin, e)
            # Return cached value if available
            with self._lock:
                if coin in self._cache:
                    return self._cache[coin][0]
            return 0.0
    
    def _fetch_price(self, coin: str) -> float:
        """Fetch price from CoinGecko API."""
        coin_id = self.COINGECKO_IDS.get(coin.upper())
        if not coin_id:
            return 0.0
        
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        return data.get(coin_id, {}).get("usd", 0.0)
    
    def get_prices_batch(self, coins: List[str]) -> Dict[str, float]:
        """Get prices for multiple coins at once."""
        # Map to CoinGecko IDs
        ids = []
        coin_map = {}
        for coin in coins:
            coin_id = self.COINGECKO_IDS.get(coin.upper())
            if coin_id:
                ids.append(coin_id)
                coin_map[coin_id] = coin.upper()
        
        if not ids:
            return {}
        
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(ids)}&vs_currencies=usd"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            prices = {}
            for coin_id, price_data in data.items():
                coin = coin_map.get(coin_id)
                if coin:
                    prices[coin] = price_data.get("usd", 0.0)
                    with self._lock:
                        self._cache[coin] = (prices[coin], time.time())
            
            return prices
        except Exception as e:
            logger.warning("Failed to fetch batch prices: %s", e)
            return {}


# ══════════════════════════════════════════════════════════════════════════════
# Pool Discovery Service
# ══════════════════════════════════════════════════════════════════════════════

class MiningPoolDiscovery:
    """
    Discovers and monitors mining pools.
    
    Features:
    - Automatic pool health checking
    - Profitability estimation
    - AI-powered pool ranking
    - Real-time updates for dashboard
    """
    
    def __init__(self, user_hashrate: float = 1e6):
        self._pools: Dict[str, MiningPool] = {}
        self._price_fetcher = CoinPriceFetcher()
        self._user_hashrate = user_hashrate  # User's hashrate in H/s
        
        self._lock = threading.Lock()
        self._running = False
        self._update_thread: Optional[threading.Thread] = None
        self._last_update = 0.0
        self._update_interval = 300  # 5 minutes
        
        # AI selection state
        self._auto_select_enabled = False
        self._selected_pool: Optional[MiningPool] = None
        self._selection_callback: Optional[Callable[[MiningPool], None]] = None
        
        # Initialize with known pools
        self._load_known_pools()
        
        logger.info("Mining pool discovery initialized with %d known pools", len(self._pools))
    
    def _load_known_pools(self):
        """Load known pools from database."""
        for pool_data in KNOWN_POOLS:
            pool_id = hashlib.md5(pool_data["url"].encode()).hexdigest()[:12]
            
            pool = MiningPool(
                id=pool_id,
                name=pool_data["name"],
                url=pool_data["url"],
                algorithm=pool_data["algorithm"],
                coin=pool_data["coin"],
                coin_name=pool_data["coin_name"],
                fee_percent=pool_data.get("fee_percent", 1.0),
                min_payout=pool_data.get("min_payout", 0.0),
                region=pool_data.get("region", "global"),
                ssl_port=pool_data.get("ssl_port"),
                website=pool_data.get("website", ""),
            )
            
            self._pools[pool_id] = pool
    
    def start(self):
        """Start background pool monitoring."""
        if self._running:
            return
        
        self._running = True
        self._update_thread = threading.Thread(
            target=self._update_loop,
            daemon=True,
            name="pool-discovery"
        )
        self._update_thread.start()
        logger.info("Pool discovery service started")
    
    def stop(self):
        """Stop background monitoring."""
        self._running = False
        if self._update_thread:
            self._update_thread.join(timeout=5)
        logger.info("Pool discovery service stopped")
    
    def _update_loop(self):
        """Background loop for pool updates."""
        while self._running:
            try:
                self.refresh_all_pools()
                
                # Check for auto-selection
                if self._auto_select_enabled:
                    self._auto_select_pool()
                
            except Exception as e:
                logger.warning("Pool update error: %s", e)
            
            time.sleep(self._update_interval)
    
    def refresh_all_pools(self):
        """Refresh status and profitability for all pools."""
        logger.info("Refreshing pool data...")
        start = time.time()
        
        # Get prices for all coins
        coins = list(set(p.coin for p in self._pools.values()))
        prices = self._price_fetcher.get_prices_batch(coins)
        
        # Update each pool
        for pool in self._pools.values():
            try:
                self._update_pool(pool, prices.get(pool.coin, 0.0))
            except Exception as e:
                logger.debug("Failed to update pool %s: %s", pool.name, e)
        
        # Recalculate scores
        self._calculate_scores()
        
        self._last_update = time.time()
        elapsed = (time.time() - start) * 1000
        logger.info("Pool refresh complete in %.1fms", elapsed)
    
    def _update_pool(self, pool: MiningPool, coin_price: float):
        """Update a single pool's status and profitability."""
        # Update price
        pool.coin_price_usd = coin_price
        
        # Check connectivity
        pool.status, pool.latency_ms = self._check_pool_connectivity(pool.url)
        pool.last_checked = time.time()
        
        # Estimate profitability (simplified - real implementation would fetch network stats)
        if pool.status == PoolStatus.ONLINE and coin_price > 0:
            pool.estimated_daily_usd = self._estimate_daily_earnings(pool)
            if coin_price > 0:
                pool.estimated_daily_coins = pool.estimated_daily_usd / coin_price
    
    def _check_pool_connectivity(self, url: str) -> Tuple[PoolStatus, float]:
        """Check if a pool is reachable and measure latency."""
        try:
            parsed = urlparse(url)
            host = parsed.hostname
            port = parsed.port or 3333
            
            start = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            latency = (time.time() - start) * 1000
            sock.close()
            
            if result == 0:
                return PoolStatus.ONLINE, latency
            else:
                return PoolStatus.OFFLINE, 0.0
        except Exception as e:
            logger.debug("Pool connectivity check failed for %s: %s", url, e)
            return PoolStatus.UNKNOWN, 0.0
    
    def _estimate_daily_earnings(self, pool: MiningPool) -> float:
        """
        Estimate daily earnings for a pool.
        
        This is a simplified estimation. Real calculations would need:
        - Current network difficulty
        - Block reward
        - Block time
        - Pool's luck factor
        """
        # Simplified estimation based on coin and algorithm
        # Real implementation would fetch network data from blockchain explorers
        
        # Base estimates per MH/s per day (approximate, varies greatly)
        BASE_ESTIMATES = {
            "BTC": 0.000001,    # ~$0.06/day per TH/s at current prices
            "LTC": 0.00001,    # ~$0.001/day per MH/s
            "DOGE": 0.0001,    # ~$0.01/day per MH/s
            "ETC": 0.0001,     # ~$0.01/day per MH/s
            "RVN": 0.001,      # ~$0.02/day per MH/s
            "XMR": 0.001,      # ~$0.15/day per KH/s (CPU mining)
            "ERG": 0.0005,     # ~$0.05/day per MH/s
            "KAS": 0.0002,     # ~$0.02/day per MH/s
            "FLUX": 0.0003,    # ~$0.03/day per KH/s
            "ALPH": 0.0002,    # ~$0.02/day per MH/s
        }
        
        base_rate = BASE_ESTIMATES.get(pool.coin, 0.0001)
        hashrate_mh = self._user_hashrate / 1e6
        
        # Apply pool fee
        fee_multiplier = 1 - (pool.fee_percent / 100)
        
        # Estimate based on coin price
        daily_coins = base_rate * hashrate_mh
        daily_usd = daily_coins * pool.coin_price_usd * fee_multiplier
        
        return daily_usd
    
    def _calculate_scores(self):
        """Calculate AI scores for all pools."""
        if not self._pools:
            return
        
        # Group by algorithm for fair comparison
        by_algorithm: Dict[MiningAlgorithmType, List[MiningPool]] = {}
        for pool in self._pools.values():
            if pool.algorithm not in by_algorithm:
                by_algorithm[pool.algorithm] = []
            by_algorithm[pool.algorithm].append(pool)
        
        for pools in by_algorithm.values():
            # Calculate profitability scores (normalized within algorithm)
            max_profit = max((p.estimated_daily_usd for p in pools), default=1.0) or 1.0
            for pool in pools:
                pool.profitability_score = pool.estimated_daily_usd / max_profit if max_profit > 0 else 0.0
            
            # Calculate reliability scores
            for pool in pools:
                if pool.status == PoolStatus.ONLINE:
                    pool.reliability_score = max(0.5, 1.0 - pool.latency_ms / 1000)
                elif pool.status == PoolStatus.DEGRADED:
                    pool.reliability_score = 0.3
                else:
                    pool.reliability_score = 0.1
            
            # Calculate overall scores
            for pool in pools:
                pool.overall_score = (
                    pool.profitability_score * 0.6 +
                    pool.reliability_score * 0.3 +
                    (1.0 - pool.fee_percent / 10) * 0.1
                )
    
    def _auto_select_pool(self):
        """Auto-select the best pool for mining."""
        best = self.get_best_pool()
        
        if best and best != self._selected_pool:
            self._selected_pool = best
            logger.info("AI auto-selected pool: %s (score: %.3f)", best.name, best.overall_score)
            
            if self._selection_callback:
                self._selection_callback(best)
    
    # ── Public API ────────────────────────────────────────────────────────────
    
    def get_all_pools(self) -> List[MiningPool]:
        """Get all discovered pools."""
        with self._lock:
            return list(self._pools.values())
    
    def get_pools_by_algorithm(self, algorithm: MiningAlgorithmType) -> List[MiningPool]:
        """Get pools filtered by algorithm."""
        with self._lock:
            return [p for p in self._pools.values() if p.algorithm == algorithm]
    
    def get_pools_by_coin(self, coin: str) -> List[MiningPool]:
        """Get pools filtered by coin."""
        with self._lock:
            return [p for p in self._pools.values() if p.coin.upper() == coin.upper()]
    
    def get_pool(self, pool_id: str) -> Optional[MiningPool]:
        """Get a specific pool by ID."""
        with self._lock:
            return self._pools.get(pool_id)
    
    def get_best_pool(self, algorithm: Optional[MiningAlgorithmType] = None) -> Optional[MiningPool]:
        """Get the best scoring pool, optionally filtered by algorithm."""
        pools = self.get_all_pools() if algorithm is None else self.get_pools_by_algorithm(algorithm)
        online_pools = [p for p in pools if p.status == PoolStatus.ONLINE]
        
        if not online_pools:
            return None
        
        return max(online_pools, key=lambda p: p.overall_score)
    
    def get_top_pools(self, limit: int = 10, algorithm: Optional[MiningAlgorithmType] = None) -> List[MiningPool]:
        """Get top scoring pools."""
        pools = self.get_all_pools() if algorithm is None else self.get_pools_by_algorithm(algorithm)
        sorted_pools = sorted(pools, key=lambda p: p.overall_score, reverse=True)
        return sorted_pools[:limit]
    
    def set_user_hashrate(self, hashrate: float):
        """Update user's hashrate for profitability calculations."""
        self._user_hashrate = hashrate
        logger.info("User hashrate updated to %.2f H/s", hashrate)
    
    def enable_auto_select(self, callback: Optional[Callable[[MiningPool], None]] = None):
        """Enable automatic pool selection by AI."""
        self._auto_select_enabled = True
        self._selection_callback = callback
        logger.info("Auto pool selection enabled")
    
    def disable_auto_select(self):
        """Disable automatic pool selection."""
        self._auto_select_enabled = False
        self._selection_callback = None
        logger.info("Auto pool selection disabled")
    
    def get_selected_pool(self) -> Optional[MiningPool]:
        """Get the currently selected pool (manual or auto)."""
        return self._selected_pool
    
    def select_pool(self, pool_id: str) -> Optional[MiningPool]:
        """Manually select a pool."""
        pool = self.get_pool(pool_id)
        if pool:
            self._selected_pool = pool
            logger.info("Pool manually selected: %s", pool.name)
        return pool
    
    def add_custom_pool(
        self,
        name: str,
        url: str,
        algorithm: str,
        coin: str,
        coin_name: str = "",
        fee_percent: float = 1.0,
    ) -> MiningPool:
        """Add a custom pool."""
        pool_id = hashlib.md5(url.encode()).hexdigest()[:12]
        
        try:
            algo = MiningAlgorithmType(algorithm.lower())
        except ValueError:
            algo = MiningAlgorithmType.SHA256
        
        pool = MiningPool(
            id=pool_id,
            name=name,
            url=url,
            algorithm=algo,
            coin=coin.upper(),
            coin_name=coin_name or coin.upper(),
            fee_percent=fee_percent,
        )
        
        with self._lock:
            self._pools[pool_id] = pool
        
        # Update pool status
        self._update_pool(pool, self._price_fetcher.get_price(coin))
        
        logger.info("Custom pool added: %s", name)
        return pool
    
    def remove_pool(self, pool_id: str) -> bool:
        """Remove a pool."""
        with self._lock:
            if pool_id in self._pools:
                del self._pools[pool_id]
                return True
        return False
    
    def get_stats(self) -> dict:
        """Get pool discovery statistics."""
        pools = self.get_all_pools()
        online_count = sum(1 for p in pools if p.status == PoolStatus.ONLINE)
        
        return {
            "total_pools": len(pools),
            "online_pools": online_count,
            "algorithms": list(set(p.algorithm.value for p in pools)),
            "coins": list(set(p.coin for p in pools)),
            "auto_select_enabled": self._auto_select_enabled,
            "selected_pool": self._selected_pool.to_dict() if self._selected_pool else None,
            "user_hashrate": self._user_hashrate,
            "last_update": self._last_update,
            "update_interval": self._update_interval,
        }
    
    def get_dashboard_data(self) -> dict:
        """Get formatted data for the dashboard."""
        pools = self.get_all_pools()
        
        # Group by algorithm
        by_algorithm = {}
        for pool in pools:
            algo = pool.algorithm.value
            if algo not in by_algorithm:
                by_algorithm[algo] = []
            by_algorithm[algo].append(pool.to_dict())
        
        # Sort each group by overall score
        for algo in by_algorithm:
            by_algorithm[algo] = sorted(by_algorithm[algo], key=lambda p: p["overall_score"], reverse=True)
        
        # Get top pools overall
        top_pools = [p.to_dict() for p in self.get_top_pools(10)]
        
        # Best pool recommendation
        best = self.get_best_pool()
        
        return {
            "pools_by_algorithm": by_algorithm,
            "top_pools": top_pools,
            "best_pool": best.to_dict() if best else None,
            "auto_select_enabled": self._auto_select_enabled,
            "selected_pool": self._selected_pool.to_dict() if self._selected_pool else None,
            "stats": self.get_stats(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# Global Instances
# ══════════════════════════════════════════════════════════════════════════════

_pool_discovery: Optional[MiningPoolDiscovery] = None


def get_pool_discovery() -> MiningPoolDiscovery:
    """Get the singleton pool discovery instance."""
    global _pool_discovery
    if _pool_discovery is None:
        _pool_discovery = MiningPoolDiscovery()
    return _pool_discovery


def get_available_algorithms() -> List[str]:
    """Get list of supported algorithms."""
    return [a.value for a in MiningAlgorithmType]


def get_available_coins() -> List[Dict[str, str]]:
    """Get list of available coins with their algorithms."""
    discovery = get_pool_discovery()
    pools = discovery.get_all_pools()
    
    coins = {}
    for pool in pools:
        if pool.coin not in coins:
            coins[pool.coin] = {
                "symbol": pool.coin,
                "name": pool.coin_name,
                "algorithm": pool.algorithm.value,
            }
    
    return list(coins.values())
