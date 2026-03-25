"""
Real-Time Price & Block Feed for Nexus AI.

Maintains live price data and block events via:
  1. WebSocket subscriptions (newHeads, pending txs) from the RPC node
  2. CoinGecko polling (60s refresh) as price baseline
  3. In-memory cache with microsecond access

Consumers simply call feed.price("ETH") and get the latest USD price
without any network call — the background thread keeps it fresh.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import requests

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# CoinGecko IDs
CG_IDS = {
    "ETH":   "ethereum",
    "BNB":   "binancecoin",
    "MATIC": "matic-network",
    "BTC":   "bitcoin",
    "USDC":  "usd-coin",
    "USDT":  "tether",
    "DAI":   "dai",
    "AAVE":  "aave",
    "UNI":   "uniswap",
    "SUSHI": "sushi",
    "CAKE":  "pancakeswap-token",
    "LINK":  "chainlink",
    "WBTC":  "wrapped-bitcoin",
    "COMP":  "compound-governance-token",
    "CRV":   "curve-dao-token",
    "CVX":   "convex-finance",
}

PRICE_REFRESH_SECONDS  = 60   # Increased from 20s to reduce CoinGecko rate limiting
BLOCK_LISTENERS_MAX    = 50   # max registered block callbacks
RETRY_BACKOFF_BASE     = 2    # exponential backoff base in seconds
MAX_RETRIES            = 3    # max retry attempts for failed API calls


class PriceFeed:
    """
    Background price-feed daemon.

    Thread-safe in-memory price cache refreshed every PRICE_REFRESH_SECONDS.
    """

    def __init__(self):
        self._prices: dict[str, float]    = {}
        self._timestamps: dict[str, float] = {}
        self._lock      = threading.Lock()
        self._running   = False
        self._thread: Optional[threading.Thread] = None
        self._block_listeners: list[Callable[[dict], None]] = []
        self._latest_block: dict = {}

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        # Immediate first fetch
        self._refresh_prices()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="price-feed"
        )
        self._thread.start()
        logger.info("PriceFeed started")

    def stop(self):
        self._running = False

    # ── Public API ────────────────────────────────────────────

    def price(self, symbol: str) -> Optional[float]:
        """Return latest USD price for a symbol (instant, no network call)."""
        with self._lock:
            return self._prices.get(symbol.upper())

    def all_prices(self) -> dict[str, float]:
        with self._lock:
            return dict(self._prices)

    def age(self, symbol: str) -> float:
        """Seconds since last price update for symbol."""
        ts = self._timestamps.get(symbol.upper(), 0)
        return time.time() - ts if ts else float("inf")

    def on_new_block(self, callback: Callable[[dict], None]):
        """Register a callback invoked whenever a new block is detected."""
        if len(self._block_listeners) < BLOCK_LISTENERS_MAX:
            self._block_listeners.append(callback)

    def latest_block(self) -> dict:
        return self._latest_block

    def status(self) -> dict:
        with self._lock:
            return {
                "running":       self._running,
                "price_count":   len(self._prices),
                "prices":        {k: round(v, 4) for k, v in self._prices.items()},
                "oldest_update": min(self._timestamps.values(), default=0),
            }

    # ── Internal loop ─────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._refresh_prices()
            except Exception as exc:
                logger.warning("PriceFeed refresh error: %s", exc)
            time.sleep(PRICE_REFRESH_SECONDS)

    def _refresh_prices(self):
        ids = list(CG_IDS.values())
        base = (
            "https://pro-api.coingecko.com"
            if Config.COINGECKO_API_KEY
            else "https://api.coingecko.com"
        )
        headers = {}
        if Config.COINGECKO_API_KEY:
            headers["x-cg-pro-api-key"] = Config.COINGECKO_API_KEY

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(
                    f"{base}/api/v3/simple/price",
                    params={"ids": ",".join(ids), "vs_currencies": "usd"},
                    headers=headers,
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                now = time.time()

                reverse = {v: k for k, v in CG_IDS.items()}
                with self._lock:
                    for cg_id, prices in data.items():
                        symbol = reverse.get(cg_id)
                        if symbol and "usd" in prices:
                            self._prices[symbol]     = float(prices["usd"])
                            self._timestamps[symbol] = now

                logger.debug("Prices refreshed: %d tokens", len(data))
                return  # Success, exit retry loop
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    # Rate limited - apply exponential backoff
                    last_error = exc
                    retry_delay = RETRY_BACKOFF_BASE ** attempt  # 2^0=1, 2^1=2, 2^2=4 → ~1s, 2s, 4s
                    logger.debug(
                        "CoinGecko rate limited (attempt %d/%d), retrying in %ds...",
                        attempt + 1, MAX_RETRIES, retry_delay
                    )
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(retry_delay)
                    continue
                else:
                    logger.warning("CoinGecko fetch failed: %s", exc)
                    return  # Non-retryable HTTP error
            except Exception as exc:
                logger.warning("CoinGecko fetch failed: %s", exc)
                return  # Non-retryable error

        # All retries exhausted
        if last_error:
            logger.warning("CoinGecko fetch failed after %d retries: %s", MAX_RETRIES, last_error)

    # ── Block feed (HTTP polling fallback) ────────────────────

    def poll_blocks(self, w3, chain: str):
        """
        Poll for new blocks on a chain (fallback when WebSocket unavailable).
        Call this from the blockchain manager's monitoring loop.
        """
        try:
            block = w3.eth.get_block("latest")
            block_num = block.number
            if self._latest_block.get(chain) != block_num:
                self._latest_block[chain] = block_num
                info = {
                    "chain":       chain,
                    "number":      block_num,
                    "timestamp":   block.timestamp,
                    "gas_used":    block.gasUsed,
                    "gas_limit":   block.gasLimit,
                    "base_fee":    getattr(block, "baseFeePerGas", None),
                    "tx_count":    len(block.transactions),
                }
                for cb in self._block_listeners:
                    try:
                        cb(info)
                    except Exception as exc:
                        logger.debug("Block listener error: %s", exc)
        except Exception as exc:
            logger.debug("Block poll error on %s: %s", chain, exc)


# Singleton
_feed: Optional[PriceFeed] = None


def get_price_feed() -> PriceFeed:
    global _feed
    if _feed is None:
        _feed = PriceFeed()
    return _feed
