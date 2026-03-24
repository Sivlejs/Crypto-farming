"""
Configuration management for Nexus AI.
Loads settings from environment variables / .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


class Config:
    # Flask
    SECRET_KEY: str = os.getenv("SECRET_KEY", "nexus-ai-secret-change-me")
    FLASK_ENV: str = os.getenv("FLASK_ENV", "development")

    # Wallet
    WALLET_ADDRESS: str = os.getenv("WALLET_ADDRESS", "")
    WALLET_PRIVATE_KEY: str = os.getenv("WALLET_PRIVATE_KEY", "")

    # RPC
    ETH_RPC_URL: str = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
    BSC_RPC_URL: str = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
    POLYGON_RPC_URL: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com/")

    # API keys
    COINGECKO_API_KEY: str = os.getenv("COINGECKO_API_KEY", "")

    # Trading limits
    MIN_PROFIT_USD: float = _float("MIN_PROFIT_USD", 5.0)
    MAX_GAS_GWEI: float = _float("MAX_GAS_GWEI", 50.0)
    SLIPPAGE_PERCENT: float = _float("SLIPPAGE_PERCENT", 0.5)
    MAX_TRADE_USD: float = _float("MAX_TRADE_USD", 500.0)

    # Scan interval in seconds
    SCAN_INTERVAL_SECONDS: int = _int("SCAN_INTERVAL_SECONDS", 15)

    # Dry-run (simulate only, no real transactions)
    DRY_RUN: bool = _bool("DRY_RUN", True)

    # ── Payout settings ───────────────────────────────────────

    # Minimum USD accumulated before triggering a payout sweep
    PAYOUT_THRESHOLD_USD: float = _float("PAYOUT_THRESHOLD_USD", 10.0)

    # On-chain destination address (Coinbase ETH/USDC deposit address,
    # or Cash App deposit address, or any EVM wallet address)
    PAYOUT_ADDRESS: str = os.getenv("PAYOUT_ADDRESS", "")

    # Which chain to use for on-chain payouts
    PAYOUT_CHAIN: str = os.getenv("PAYOUT_CHAIN", "ethereum")

    # Which token to pay out in: USDC (default), ETH, BNB, MATIC, or NATIVE
    PAYOUT_TOKEN: str = os.getenv("PAYOUT_TOKEN", "USDC")

    # ── Coinbase API (optional) ───────────────────────────────
    # Get keys at: https://www.coinbase.com/settings/api
    COINBASE_API_KEY: str = os.getenv("COINBASE_API_KEY", "")
    COINBASE_API_SECRET: str = os.getenv("COINBASE_API_SECRET", "")
    # Optional: Coinbase account UUID (auto-detected if not set)
    COINBASE_ACCOUNT_ID: str = os.getenv("COINBASE_ACCOUNT_ID", "")

    # ── Cash App Bitcoin / Lightning (optional) ───────────────
    # Lightning address: either user@domain.com format or Cash App $cashtag
    # e.g. "$YourCashTag" or "you@cash.app"
    PAYOUT_LIGHTNING_ADDRESS: str = os.getenv("PAYOUT_LIGHTNING_ADDRESS", "")

    # Self-hosted Lightning node for automatic invoice payment (optional)
    # Leave blank to receive invoices as logs for manual payment
    LIGHTNING_NODE_URL: str = os.getenv("LIGHTNING_NODE_URL", "")
    LIGHTNING_NODE_MACAROON: str = os.getenv("LIGHTNING_NODE_MACAROON", "")  # LND
    LIGHTNING_NODE_API_KEY: str = os.getenv("LIGHTNING_NODE_API_KEY", "")   # LNbits

    # Enabled strategies
    STRATEGY_ARBITRAGE: bool = _bool("STRATEGY_ARBITRAGE", True)
    STRATEGY_YIELD_FARMING: bool = _bool("STRATEGY_YIELD_FARMING", True)
    STRATEGY_LIQUIDITY_MINING: bool = _bool("STRATEGY_LIQUIDITY_MINING", True)
    STRATEGY_LIQUIDATION: bool = _bool("STRATEGY_LIQUIDATION", True)

    # ── Speed / MEV settings ──────────────────────────────────

    # Flashbots: dedicated signing key (NOT your trading wallet key)
    FLASHBOTS_SIGNING_KEY: str = os.getenv("FLASHBOTS_SIGNING_KEY", "")
    # bloXroute: auth header for ultra-fast bundle propagation
    BLOXROUTE_AUTH_HEADER: str = os.getenv("BLOXROUTE_AUTH_HEADER", "")
    # Deployed FlashArbitrage contract address (per chain, comma-sep: eth:0x...,polygon:0x...)
    FLASH_CONTRACT_ETH: str = os.getenv("FLASH_CONTRACT_ETH", "")
    FLASH_CONTRACT_POLYGON: str = os.getenv("FLASH_CONTRACT_POLYGON", "")

    # Enabled chains
    CHAIN_ETH: bool = _bool("CHAIN_ETH", True)
    CHAIN_BSC: bool = _bool("CHAIN_BSC", True)
    CHAIN_POLYGON: bool = _bool("CHAIN_POLYGON", True)

    @classmethod
    def is_configured(cls) -> bool:
        """Return True only when wallet credentials are present."""
        return bool(cls.WALLET_ADDRESS and cls.WALLET_PRIVATE_KEY)

    @classmethod
    def to_dict(cls) -> dict:
        """Expose safe (non-secret) config values."""
        return {
            "dry_run": cls.DRY_RUN,
            "min_profit_usd": cls.MIN_PROFIT_USD,
            "max_gas_gwei": cls.MAX_GAS_GWEI,
            "slippage_percent": cls.SLIPPAGE_PERCENT,
            "max_trade_usd": cls.MAX_TRADE_USD,
            "scan_interval_seconds": cls.SCAN_INTERVAL_SECONDS,
            "strategies": {
                "arbitrage": cls.STRATEGY_ARBITRAGE,
                "yield_farming": cls.STRATEGY_YIELD_FARMING,
                "liquidity_mining": cls.STRATEGY_LIQUIDITY_MINING,
            },
            "chains": {
                "ethereum": cls.CHAIN_ETH,
                "bsc": cls.CHAIN_BSC,
                "polygon": cls.CHAIN_POLYGON,
            },
            "wallet_configured": cls.is_configured(),
            "payout": {
                "threshold_usd": cls.PAYOUT_THRESHOLD_USD,
                "address": cls.PAYOUT_ADDRESS or "not set",
                "chain": cls.PAYOUT_CHAIN,
                "token": cls.PAYOUT_TOKEN,
                "coinbase_api": bool(cls.COINBASE_API_KEY and cls.COINBASE_API_SECRET),
                "lightning_address": cls.PAYOUT_LIGHTNING_ADDRESS or "not set",
                "lightning_node": bool(cls.LIGHTNING_NODE_URL),
            },
        }
