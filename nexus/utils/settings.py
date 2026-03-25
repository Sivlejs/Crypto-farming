"""
Nexus AI Settings Manager.

Provides runtime-configurable settings that can be modified via:
  1. Dashboard UI (Settings tab)
  2. Nexus Chat commands ("set min profit to $5")
  3. REST API (/api/settings)

Settings are stored in Redis (when available) for persistence across
web and worker services, with fallback to in-memory storage.

Note: Secrets (API keys, private keys) should still be set via environment
variables for security. This module handles runtime tuning parameters only.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import redis, but don't fail if unavailable
try:
    import redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False
    redis = None


# ── Setting definitions ───────────────────────────────────────────────────────

# Each setting has: key, type, default, min, max, description, category
SETTING_DEFINITIONS = {
    # Bot behavior
    "dry_run": {
        "type": "bool",
        "default": True,
        "description": "Simulation mode - no real transactions",
        "category": "bot",
    },
    "min_profit_usd": {
        "type": "float",
        "default": 2.0,
        "min": 0.01,
        "max": 1000.0,
        "description": "Minimum profit threshold (USD) to execute a trade",
        "category": "bot",
    },
    "max_gas_gwei": {
        "type": "float",
        "default": 80.0,
        "min": 5.0,
        "max": 500.0,
        "description": "Maximum gas price in Gwei",
        "category": "bot",
    },
    "slippage_percent": {
        "type": "float",
        "default": 0.5,
        "min": 0.1,
        "max": 5.0,
        "description": "Maximum slippage tolerance (%)",
        "category": "bot",
    },
    "max_trade_usd": {
        "type": "float",
        "default": 10000.0,
        "min": 10.0,
        "max": 1000000.0,
        "description": "Maximum trade size (USD)",
        "category": "bot",
    },
    "scan_interval_seconds": {
        "type": "int",
        "default": 10,
        "min": 5,
        "max": 300,
        "description": "Fallback scan interval in seconds",
        "category": "bot",
    },

    # Payout settings
    "payout_threshold_usd": {
        "type": "float",
        "default": 10.0,
        "min": 1.0,
        "max": 10000.0,
        "description": "Auto-sweep threshold (USD)",
        "category": "payout",
    },
    "payout_chain": {
        "type": "str",
        "default": "ethereum",
        "options": ["ethereum", "bsc", "polygon", "arbitrum", "optimism", "base"],
        "description": "Chain for payout transactions",
        "category": "payout",
    },
    "payout_token": {
        "type": "str",
        "default": "USDC",
        "options": ["USDC", "USDT", "ETH", "BNB", "MATIC", "NATIVE"],
        "description": "Token for payouts",
        "category": "payout",
    },
    "payout_address": {
        "type": "str",
        "default": "",
        "description": "Destination wallet address (Coinbase deposit address, etc.)",
        "category": "payout",
        "sensitive": False,  # Address is not sensitive
    },

    # Coinbase API (sensitive - show masked in UI)
    "coinbase_api_key": {
        "type": "str",
        "default": "",
        "description": "Coinbase API Key",
        "category": "coinbase",
        "sensitive": True,
    },
    "coinbase_api_secret": {
        "type": "str",
        "default": "",
        "description": "Coinbase API Secret",
        "category": "coinbase",
        "sensitive": True,
    },
    "coinbase_account_id": {
        "type": "str",
        "default": "",
        "description": "Coinbase Account ID (auto-detected if blank)",
        "category": "coinbase",
        "sensitive": False,
    },

    # Lightning / Cash App
    "lightning_address": {
        "type": "str",
        "default": "",
        "description": "Lightning address or $cashtag (e.g. $YourCashTag)",
        "category": "lightning",
        "sensitive": False,
    },

    # Strategies
    "strategy_arbitrage": {
        "type": "bool",
        "default": True,
        "description": "Enable arbitrage strategy",
        "category": "strategies",
    },
    "strategy_yield_farming": {
        "type": "bool",
        "default": True,
        "description": "Enable yield farming strategy",
        "category": "strategies",
    },
    "strategy_liquidity_mining": {
        "type": "bool",
        "default": True,
        "description": "Enable liquidity mining strategy",
        "category": "strategies",
    },
    "strategy_liquidation": {
        "type": "bool",
        "default": True,
        "description": "Enable liquidation strategy",
        "category": "strategies",
    },

    # Chains
    "chain_eth": {
        "type": "bool",
        "default": True,
        "description": "Enable Ethereum",
        "category": "chains",
    },
    "chain_bsc": {
        "type": "bool",
        "default": True,
        "description": "Enable BNB Smart Chain",
        "category": "chains",
    },
    "chain_polygon": {
        "type": "bool",
        "default": True,
        "description": "Enable Polygon",
        "category": "chains",
    },
    "chain_arbitrum": {
        "type": "bool",
        "default": False,
        "description": "Enable Arbitrum",
        "category": "chains",
    },
    "chain_optimism": {
        "type": "bool",
        "default": False,
        "description": "Enable Optimism",
        "category": "chains",
    },
    "chain_base": {
        "type": "bool",
        "default": False,
        "description": "Enable Base",
        "category": "chains",
    },

    # Voice settings
    "voice_enabled": {
        "type": "bool",
        "default": True,
        "description": "Enable voice commands",
        "category": "voice",
    },
    "voice_wake_word": {
        "type": "str",
        "default": "nexus",
        "description": "Wake word for voice activation",
        "category": "voice",
    },
}

REDIS_SETTINGS_KEY = "nexus:settings"


class SettingsManager:
    """
    Runtime settings manager with Redis persistence.

    Settings can be get/set at runtime and are synced across web and worker
    services via Redis.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._redis: Optional[Any] = None
        self._init_redis()
        self._load_defaults()

    def _init_redis(self):
        """Initialize Redis connection if available."""
        if not _REDIS_AVAILABLE:
            logger.info("Redis not available - using in-memory settings only")
            return

        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            logger.info("REDIS_URL not set - using in-memory settings only")
            return

        try:
            self._redis = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            self._redis.ping()
            logger.info("SettingsManager connected to Redis")
        except Exception as exc:
            logger.warning("Redis connection failed: %s - using in-memory settings", exc)
            self._redis = None

    def _load_defaults(self):
        """Load defaults from Config and environment, then overlay Redis values."""
        from nexus.utils.config import Config

        # Map Config class attributes to settings
        config_mapping = {
            "dry_run": "DRY_RUN",
            "min_profit_usd": "MIN_PROFIT_USD",
            "max_gas_gwei": "MAX_GAS_GWEI",
            "slippage_percent": "SLIPPAGE_PERCENT",
            "max_trade_usd": "MAX_TRADE_USD",
            "scan_interval_seconds": "SCAN_INTERVAL_SECONDS",
            "payout_threshold_usd": "PAYOUT_THRESHOLD_USD",
            "payout_chain": "PAYOUT_CHAIN",
            "payout_token": "PAYOUT_TOKEN",
            "payout_address": "PAYOUT_ADDRESS",
            "coinbase_api_key": "COINBASE_API_KEY",
            "coinbase_api_secret": "COINBASE_API_SECRET",
            "coinbase_account_id": "COINBASE_ACCOUNT_ID",
            "lightning_address": "PAYOUT_LIGHTNING_ADDRESS",
            "strategy_arbitrage": "STRATEGY_ARBITRAGE",
            "strategy_yield_farming": "STRATEGY_YIELD_FARMING",
            "strategy_liquidity_mining": "STRATEGY_LIQUIDITY_MINING",
            "strategy_liquidation": "STRATEGY_LIQUIDATION",
            "chain_eth": "CHAIN_ETH",
            "chain_bsc": "CHAIN_BSC",
            "chain_polygon": "CHAIN_POLYGON",
            "chain_arbitrum": "CHAIN_ARBITRUM",
            "chain_optimism": "CHAIN_OPTIMISM",
            "chain_base": "CHAIN_BASE",
            "voice_wake_word": "VOICE_WAKE_WORD",
        }

        # Load from Config
        with self._lock:
            for setting_key, config_attr in config_mapping.items():
                if hasattr(Config, config_attr):
                    self._cache[setting_key] = getattr(Config, config_attr)

            # Load from Redis (overrides Config)
            if self._redis:
                try:
                    stored = self._redis.get(REDIS_SETTINGS_KEY)
                    if stored:
                        redis_settings = json.loads(stored)
                        self._cache.update(redis_settings)
                        logger.debug("Loaded %d settings from Redis", len(redis_settings))
                except Exception as exc:
                    logger.warning("Failed to load settings from Redis: %s", exc)

    def _save_to_redis(self):
        """Persist current settings to Redis."""
        if not self._redis:
            return
        try:
            self._redis.set(REDIS_SETTINGS_KEY, json.dumps(self._cache))
        except Exception as exc:
            logger.warning("Failed to save settings to Redis: %s", exc)

    def _apply_to_config(self, key: str, value: Any):
        """Apply a setting change to the Config class (runtime only)."""
        from nexus.utils.config import Config

        config_mapping = {
            "dry_run": "DRY_RUN",
            "min_profit_usd": "MIN_PROFIT_USD",
            "max_gas_gwei": "MAX_GAS_GWEI",
            "slippage_percent": "SLIPPAGE_PERCENT",
            "max_trade_usd": "MAX_TRADE_USD",
            "scan_interval_seconds": "SCAN_INTERVAL_SECONDS",
            "payout_threshold_usd": "PAYOUT_THRESHOLD_USD",
            "payout_chain": "PAYOUT_CHAIN",
            "payout_token": "PAYOUT_TOKEN",
            "payout_address": "PAYOUT_ADDRESS",
            "coinbase_api_key": "COINBASE_API_KEY",
            "coinbase_api_secret": "COINBASE_API_SECRET",
            "coinbase_account_id": "COINBASE_ACCOUNT_ID",
            "lightning_address": "PAYOUT_LIGHTNING_ADDRESS",
            "strategy_arbitrage": "STRATEGY_ARBITRAGE",
            "strategy_yield_farming": "STRATEGY_YIELD_FARMING",
            "strategy_liquidity_mining": "STRATEGY_LIQUIDITY_MINING",
            "strategy_liquidation": "STRATEGY_LIQUIDATION",
            "chain_eth": "CHAIN_ETH",
            "chain_bsc": "CHAIN_BSC",
            "chain_polygon": "CHAIN_POLYGON",
            "chain_arbitrum": "CHAIN_ARBITRUM",
            "chain_optimism": "CHAIN_OPTIMISM",
            "chain_base": "CHAIN_BASE",
            "voice_wake_word": "VOICE_WAKE_WORD",
        }

        if key in config_mapping:
            config_attr = config_mapping[key]
            try:
                setattr(Config, config_attr, value)
                logger.debug("Applied setting %s = %s to Config.%s", key, value, config_attr)
            except Exception as exc:
                logger.warning("Failed to apply setting %s to Config: %s", key, exc)

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        defn = SETTING_DEFINITIONS.get(key, {})
        with self._lock:
            return self._cache.get(key, defn.get("default", default))

    def set(self, key: str, value: Any) -> dict:
        """
        Set a setting value.

        Returns: {"success": bool, "key": str, "value": Any, "error": str|None}
        """
        defn = SETTING_DEFINITIONS.get(key)
        if not defn:
            return {"success": False, "key": key, "value": value, "error": f"Unknown setting: {key}"}

        # Type coercion and validation
        try:
            value = self._coerce_value(key, value, defn)
        except ValueError as exc:
            return {"success": False, "key": key, "value": value, "error": str(exc)}

        # Store
        with self._lock:
            old_value = self._cache.get(key)
            self._cache[key] = value
            self._save_to_redis()

        # Apply to Config
        self._apply_to_config(key, value)

        logger.info("Setting changed: %s = %s (was: %s)", key, value, old_value)
        return {"success": True, "key": key, "value": value, "old_value": old_value}

    def get_all(self, include_sensitive: bool = False) -> dict:
        """Get all settings with their current values and metadata."""
        result = {}
        with self._lock:
            for key, defn in SETTING_DEFINITIONS.items():
                value = self._cache.get(key, defn.get("default"))
                is_sensitive = defn.get("sensitive", False)

                # Mask sensitive values unless explicitly requested
                if is_sensitive and not include_sensitive and value:
                    display_value = "••••••••"
                else:
                    display_value = value

                result[key] = {
                    "value": display_value,
                    "actual_set": bool(value) if is_sensitive else None,
                    "type": defn["type"],
                    "default": defn["default"],
                    "description": defn["description"],
                    "category": defn["category"],
                    "sensitive": is_sensitive,
                    "min": defn.get("min"),
                    "max": defn.get("max"),
                    "options": defn.get("options"),
                }
        return result

    def get_by_category(self, category: str, include_sensitive: bool = False) -> dict:
        """Get settings filtered by category."""
        all_settings = self.get_all(include_sensitive)
        return {k: v for k, v in all_settings.items() if v["category"] == category}

    def categories(self) -> list[str]:
        """Get list of all setting categories."""
        return list(set(d["category"] for d in SETTING_DEFINITIONS.values()))

    def reset(self, key: str) -> dict:
        """Reset a setting to its default value."""
        defn = SETTING_DEFINITIONS.get(key)
        if not defn:
            return {"success": False, "key": key, "error": f"Unknown setting: {key}"}
        return self.set(key, defn["default"])

    def reset_all(self) -> dict:
        """Reset all settings to defaults."""
        with self._lock:
            self._cache = {}
            for key, defn in SETTING_DEFINITIONS.items():
                self._cache[key] = defn["default"]
                self._apply_to_config(key, defn["default"])
            self._save_to_redis()
        return {"success": True, "message": "All settings reset to defaults"}

    def _coerce_value(self, key: str, value: Any, defn: dict) -> Any:
        """Coerce and validate a value based on its definition."""
        dtype = defn["type"]

        if dtype == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)

        elif dtype == "int":
            value = int(value)
            if "min" in defn and value < defn["min"]:
                raise ValueError(f"{key} must be >= {defn['min']}")
            if "max" in defn and value > defn["max"]:
                raise ValueError(f"{key} must be <= {defn['max']}")
            return value

        elif dtype == "float":
            value = float(value)
            if "min" in defn and value < defn["min"]:
                raise ValueError(f"{key} must be >= {defn['min']}")
            if "max" in defn and value > defn["max"]:
                raise ValueError(f"{key} must be <= {defn['max']}")
            return value

        elif dtype == "str":
            value = str(value).strip()
            if "options" in defn and value and value not in defn["options"]:
                raise ValueError(f"{key} must be one of: {defn['options']}")
            return value

        return value


# ── Singleton ─────────────────────────────────────────────────────────────────

_settings: Optional[SettingsManager] = None


def get_settings() -> SettingsManager:
    global _settings
    if _settings is None:
        _settings = SettingsManager()
    return _settings
