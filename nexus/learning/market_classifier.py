"""
Market Classifier for Nexus AI.

Detects the current market regime so strategies can be
prioritised accordingly:

  VOLATILE  — high volatility, best for flash arbitrage + liquidations
  TRENDING  — directional movement, best for yield + LP farming
  CALM      — low volatility, best for yield farming / stable LP pools
  UNKNOWN   — insufficient data
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

REGIMES = ("volatile", "trending", "calm", "unknown")

# Rolling window of price snapshots
PRICE_WINDOW = 20


class MarketClassifier:
    """
    Classifies market condition from rolling price + volatility data.
    No external model required — pure statistical classification.
    """

    def __init__(self):
        # {symbol: deque of (timestamp, price)}
        self._prices: dict[str, deque] = {}
        self._current_regime = "unknown"
        self._regime_ts = 0.0

    # ── Feed ──────────────────────────────────────────────────

    def update(self, prices: dict[str, float]):
        """
        Push latest price snapshot.  Call on every price feed update.
        prices: {symbol: usd_price}
        """
        now = time.time()
        for sym, price in prices.items():
            if price and price > 0:
                if sym not in self._prices:
                    self._prices[sym] = deque(maxlen=PRICE_WINDOW)
                self._prices[sym].append((now, float(price)))

        self._classify()

    # ── Public API ────────────────────────────────────────────

    def regime(self) -> str:
        return self._current_regime

    def strategy_weights(self) -> dict[str, float]:
        """
        Return multipliers per strategy for the current regime.
        Monitor uses these to prioritise strategy scans.
        """
        return {
            "volatile":  {"flash_arbitrage": 2.0, "arbitrage": 1.5, "liquidation": 2.0, "yield_farming": 0.5,  "liquidity_mining": 0.5},
            "trending":  {"flash_arbitrage": 1.2, "arbitrage": 1.0, "liquidation": 0.8, "yield_farming": 1.8,  "liquidity_mining": 1.5},
            "calm":      {"flash_arbitrage": 0.8, "arbitrage": 0.7, "liquidation": 0.4, "yield_farming": 2.0,  "liquidity_mining": 1.8},
            "unknown":   {"flash_arbitrage": 1.0, "arbitrage": 1.0, "liquidation": 1.0, "yield_farming": 1.0,  "liquidity_mining": 1.0},
        }.get(self._current_regime, {})

    def volatility_pct(self, symbol: str = "ETH") -> float:
        """Return recent price volatility as % of mean price."""
        prices = self._get_prices(symbol)
        if len(prices) < 3:
            return 0.0
        vals = [p for _, p in prices]
        mean = sum(vals) / len(vals)
        if mean == 0:
            return 0.0
        variance = sum((v - mean) ** 2 for v in vals) / len(vals)
        return (variance ** 0.5) / mean * 100

    def status(self) -> dict:
        return {
            "regime":            self._current_regime,
            "eth_volatility_pct": round(self.volatility_pct("ETH"), 3),
            "btc_volatility_pct": round(self.volatility_pct("BTC"), 3),
            "strategy_weights":   self.strategy_weights(),
        }

    # ── Internal ──────────────────────────────────────────────

    def _get_prices(self, symbol: str) -> list:
        return list(self._prices.get(symbol, []))

    def _classify(self):
        eth_vol = self.volatility_pct("ETH")
        btc_vol = self.volatility_pct("BTC")
        avg_vol = (eth_vol + btc_vol) / 2 if btc_vol else eth_vol

        # Need at least a few price points
        eth_prices = self._get_prices("ETH")
        if len(eth_prices) < 5:
            self._current_regime = "unknown"
            return

        vals  = [p for _, p in eth_prices]
        first = vals[0]
        last  = vals[-1]
        drift = abs(last - first) / max(first, 1) * 100   # % drift

        if avg_vol > 3.0:
            regime = "volatile"
        elif drift > 2.0:
            regime = "trending"
        elif avg_vol < 1.0:
            regime = "calm"
        else:
            regime = "calm"

        if regime != self._current_regime:
            logger.info(
                "Market regime: %s → %s (vol=%.2f%% drift=%.2f%%)",
                self._current_regime, regime, avg_vol, drift,
            )
        self._current_regime = regime
        self._regime_ts = time.time()
