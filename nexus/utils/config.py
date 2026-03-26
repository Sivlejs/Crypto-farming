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

    # Trading limits (lowered for realistic opportunity detection)
    MIN_PROFIT_USD: float = _float("MIN_PROFIT_USD", 1.0)
    MAX_GAS_GWEI: float = _float("MAX_GAS_GWEI", 50.0)
    SLIPPAGE_PERCENT: float = _float("SLIPPAGE_PERCENT", 0.5)
    MAX_TRADE_USD: float = _float("MAX_TRADE_USD", 500.0)

    # Pool discovery settings
    # Minimum TVL (in USD) for pools to be considered (lowered from 50k for better discovery)
    MIN_TVL_USD: float = _float("MIN_TVL_USD", 10000.0)
    # Path for persistent pool cache (used when APIs fail)
    POOL_CACHE_FILE: str = os.getenv("POOL_CACHE_FILE", "/tmp/nexus_pools_cache.json")

    # Scan interval in seconds
    SCAN_INTERVAL_SECONDS: int = _int("SCAN_INTERVAL_SECONDS", 15)

    # Dry-run (simulate only, no real transactions)
    # Set to False for live production mode with real transactions
    DRY_RUN: bool = _bool("DRY_RUN", False)

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
    # PoW Mining enabled by default for GPU mining operations
    STRATEGY_POW_MINING: bool = _bool("STRATEGY_POW_MINING", True)

    # ── PoW Mining settings ───────────────────────────────────
    # Mining pool URL (Stratum protocol) - default to 2Miners ETC pool for live operation
    MINING_POOL_URL: str = os.getenv("MINING_POOL_URL", "stratum+tcp://pool.2miners.com:2020")
    # Mining pool username/worker name
    MINING_POOL_USER: str = os.getenv("MINING_POOL_USER", "")
    # Mining pool password (usually 'x' or empty)
    MINING_POOL_PASSWORD: str = os.getenv("MINING_POOL_PASSWORD", "x")
    # Mining algorithm: etchash recommended for GPU mining (ETC is profitable)
    MINING_ALGORITHM: str = os.getenv("MINING_ALGORITHM", "etchash")
    # Number of CPU threads for mining (0 = auto-detect)
    MINING_THREADS: int = _int("MINING_THREADS", 0)
    # Mining intensity (1-100, max intensity for live production)
    MINING_INTENSITY: int = _int("MINING_INTENSITY", 100)
    # Payout address for mining rewards
    MINING_PAYOUT_ADDRESS: str = os.getenv("MINING_PAYOUT_ADDRESS", "")
    # ── Virtual Server Mining Optimizations ──────────────────
    # Enable adaptive resource management (auto-adjusts threads/intensity)
    MINING_ADAPTIVE_MODE: bool = _bool("MINING_ADAPTIVE_MODE", True)
    # Maximum CPU usage percentage for mining (increased for live production)
    MINING_MAX_CPU_PERCENT: float = _float("MINING_MAX_CPU_PERCENT", 95.0)
    
    # ── GPU Mining Settings ───────────────────────────────────
    # Enable GPU mining (requires OpenCL/CUDA and external miner)
    MINING_USE_GPU: bool = _bool("MINING_USE_GPU", True)
    # GPU device IDs to use (comma-separated, empty = all)
    MINING_GPU_DEVICES: str = os.getenv("MINING_GPU_DEVICES", "")
    # Expected GPU hashrate for profitability calculations (MH/s) - increased for modern GPUs
    MINING_EXPECTED_HASHRATE_MHS: float = _float("MINING_EXPECTED_HASHRATE_MHS", 100.0)
    # GPU power consumption estimate (watts)
    MINING_GPU_POWER_WATTS: float = _float("MINING_GPU_POWER_WATTS", 250.0)
    # Electricity cost per kWh for profit calculations
    MINING_ELECTRICITY_COST_KWH: float = _float("MINING_ELECTRICITY_COST_KWH", 0.10)
    # Backup mining pools (comma-separated stratum URLs) - default to 2Miners backup
    MINING_BACKUP_POOLS: str = os.getenv("MINING_BACKUP_POOLS", "stratum+tcp://etc.2miners.com:1010,stratum+tcp://us-etc.2miners.com:1010")
    # Enable automatic profit switching to most profitable coin
    MINING_PROFIT_SWITCHING: bool = _bool("MINING_PROFIT_SWITCHING", True)
    # Minimum profit improvement % to trigger coin switch (lowered for responsiveness)
    MINING_PROFIT_SWITCH_THRESHOLD: float = _float("MINING_PROFIT_SWITCH_THRESHOLD", 5.0)
    # Enable AI-powered mining optimization for maximum efficiency
    MINING_AI_OPTIMIZATION: bool = _bool("MINING_AI_OPTIMIZATION", True)

    # ── Speed / MEV settings ──────────────────────────────────

    # Flashbots: dedicated signing key (NOT your trading wallet key)
    FLASHBOTS_SIGNING_KEY: str = os.getenv("FLASHBOTS_SIGNING_KEY", "")
    # bloXroute: auth header for ultra-fast bundle propagation
    BLOXROUTE_AUTH_HEADER: str = os.getenv("BLOXROUTE_AUTH_HEADER", "")
    # Deployed FlashArbitrage contract address (per chain, comma-sep: eth:0x...,polygon:0x...)
    FLASH_CONTRACT_ETH: str = os.getenv("FLASH_CONTRACT_ETH", "")
    FLASH_CONTRACT_POLYGON: str = os.getenv("FLASH_CONTRACT_POLYGON", "")
    FLASH_CONTRACT_ARBITRUM: str = os.getenv("FLASH_CONTRACT_ARBITRUM", "")

    # Enabled chains (original three)
    CHAIN_ETH: bool = _bool("CHAIN_ETH", True)
    CHAIN_BSC: bool = _bool("CHAIN_BSC", True)
    CHAIN_POLYGON: bool = _bool("CHAIN_POLYGON", True)

    # ── Additional chains ─────────────────────────────────────
    CHAIN_ARBITRUM: bool = _bool("CHAIN_ARBITRUM", False)
    CHAIN_OPTIMISM: bool = _bool("CHAIN_OPTIMISM", False)
    CHAIN_BASE: bool = _bool("CHAIN_BASE", False)
    CHAIN_AVALANCHE: bool = _bool("CHAIN_AVALANCHE", False)
    CHAIN_FANTOM: bool = _bool("CHAIN_FANTOM", False)
    CHAIN_GNOSIS: bool = _bool("CHAIN_GNOSIS", False)

    ARBITRUM_RPC_URL: str = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
    OPTIMISM_RPC_URL: str = os.getenv("OPTIMISM_RPC_URL", "https://mainnet.optimism.io")
    BASE_RPC_URL: str = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
    AVALANCHE_RPC_URL: str = os.getenv("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc")
    FANTOM_RPC_URL: str = os.getenv("FANTOM_RPC_URL", "https://rpc.ftm.tools/")
    GNOSIS_RPC_URL: str = os.getenv("GNOSIS_RPC_URL", "https://rpc.gnosischain.com/")

    # ── OpenAI / Chat settings ────────────────────────────────
    # Used by NexusChat for intelligent conversational responses
    # Get key at: https://platform.openai.com/api-keys
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    # Override base URL for OpenAI-compatible APIs (e.g. LM Studio, Groq, Together)
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

    # ── Voice / TTS settings ──────────────────────────────────
    # Voice recognition: uses browser Web Speech API (no API key needed)
    # TTS: uses browser speechSynthesis by default; set ELEVENLABS_API_KEY for
    # high-quality neural voice
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "")
    # Voice wake word (optional; triggered by holding mic button by default)
    VOICE_WAKE_WORD: str = os.getenv("VOICE_WAKE_WORD", "nexus")

    # ── Timing / scheduling settings ─────────────────────────
    # How many historical gas samples to keep for the gas oracle
    GAS_ORACLE_SAMPLES: int = _int("GAS_ORACLE_SAMPLES", 100)
    # Seconds between gas price samples
    GAS_SAMPLE_INTERVAL: int = _int("GAS_SAMPLE_INTERVAL", 60)

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
                "arbitrage":          cls.STRATEGY_ARBITRAGE,
                "yield_farming":      cls.STRATEGY_YIELD_FARMING,
                "liquidity_mining":   cls.STRATEGY_LIQUIDITY_MINING,
                "liquidation":        cls.STRATEGY_LIQUIDATION,
                "pow_mining":         cls.STRATEGY_POW_MINING,
            },
            "chains": {
                "ethereum":  cls.CHAIN_ETH,
                "bsc":       cls.CHAIN_BSC,
                "polygon":   cls.CHAIN_POLYGON,
                "arbitrum":  cls.CHAIN_ARBITRUM,
                "optimism":  cls.CHAIN_OPTIMISM,
                "base":      cls.CHAIN_BASE,
                "avalanche": cls.CHAIN_AVALANCHE,
                "fantom":    cls.CHAIN_FANTOM,
                "gnosis":    cls.CHAIN_GNOSIS,
            },
            "wallet_configured": cls.is_configured(),
            "openai": {
                "configured": bool(cls.OPENAI_API_KEY),
                "model":      cls.OPENAI_MODEL,
            },
            "voice": {
                "elevenlabs": bool(cls.ELEVENLABS_API_KEY),
                "wake_word":  cls.VOICE_WAKE_WORD,
            },
            "payout": {
                "threshold_usd": cls.PAYOUT_THRESHOLD_USD,
                "address": cls.PAYOUT_ADDRESS or "not set",
                "chain": cls.PAYOUT_CHAIN,
                "token": cls.PAYOUT_TOKEN,
                "coinbase_api": bool(cls.COINBASE_API_KEY and cls.COINBASE_API_SECRET),
                "lightning_address": cls.PAYOUT_LIGHTNING_ADDRESS or "not set",
                "lightning_node": bool(cls.LIGHTNING_NODE_URL),
            },
            "mining": {
                "enabled": cls.STRATEGY_POW_MINING,
                "pool_configured": bool(cls.MINING_POOL_URL and cls.MINING_POOL_USER),
                "algorithm": cls.MINING_ALGORITHM,
                "threads": cls.MINING_THREADS or "auto",
                "intensity": cls.MINING_INTENSITY,
                "payout_address": cls.MINING_PAYOUT_ADDRESS or "not set",
                "adaptive_mode": cls.MINING_ADAPTIVE_MODE,
                "max_cpu_percent": cls.MINING_MAX_CPU_PERCENT,
                # GPU mining configuration
                "gpu": {
                    "enabled": cls.MINING_USE_GPU,
                    "devices": cls.MINING_GPU_DEVICES or "all",
                    "expected_hashrate_mhs": cls.MINING_EXPECTED_HASHRATE_MHS,
                    "power_watts": cls.MINING_GPU_POWER_WATTS,
                    "electricity_cost_kwh": cls.MINING_ELECTRICITY_COST_KWH,
                },
                "profit_switching": {
                    "enabled": cls.MINING_PROFIT_SWITCHING,
                    "threshold_percent": cls.MINING_PROFIT_SWITCH_THRESHOLD,
                },
                "ai_optimization": cls.MINING_AI_OPTIMIZATION,
                "backup_pools": bool(cls.MINING_BACKUP_POOLS),
            },
        }
