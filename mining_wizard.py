"""
Mining Machine Setup Wizard for Nexus AI.

Interactive wizard to configure and launch a complete crypto mining operation:
- Hardware detection and optimization
- GPU configuration and tuning
- Mining pool setup with failover
- Wallet and payout configuration
- Algorithm selection based on profitability
- AI optimization setup
- Final system test and launch

This wizard ensures your mining machine is optimally configured to
compete with dedicated hardware and start earning immediately.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def colored(text: str, color: str) -> str:
    """Apply color to text if terminal supports it."""
    if sys.stdout.isatty():
        return f"{color}{text}{Colors.END}"
    return text


def print_header(text: str):
    """Print a formatted header."""
    print("\n" + "═" * 60)
    print(colored(f"  {text}", Colors.BOLD + Colors.CYAN))
    print("═" * 60 + "\n")


def print_success(text: str):
    """Print success message."""
    print(colored(f"  ✓ {text}", Colors.GREEN))


def print_warning(text: str):
    """Print warning message."""
    print(colored(f"  ⚠ {text}", Colors.YELLOW))


def print_error(text: str):
    """Print error message."""
    print(colored(f"  ✗ {text}", Colors.RED))


def print_info(text: str):
    """Print info message."""
    print(colored(f"  ℹ {text}", Colors.BLUE))


def prompt(text: str, default: str = "") -> str:
    """Prompt user for input."""
    if default:
        result = input(f"  {text} [{default}]: ").strip()
        return result if result else default
    return input(f"  {text}: ").strip()


def prompt_yes_no(text: str, default: bool = True) -> bool:
    """Prompt user for yes/no answer."""
    default_str = "Y/n" if default else "y/N"
    result = input(f"  {text} [{default_str}]: ").strip().lower()
    if not result:
        return default
    return result in ('y', 'yes', '1', 'true')


def prompt_choice(text: str, choices: List[str], default: int = 0) -> int:
    """Prompt user to choose from a list."""
    print(f"\n  {text}")
    for i, choice in enumerate(choices):
        marker = "→" if i == default else " "
        print(f"    {marker} [{i + 1}] {choice}")
    
    while True:
        result = input(f"\n  Enter choice [1-{len(choices)}] (default: {default + 1}): ").strip()
        if not result:
            return default
        try:
            idx = int(result) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print_error(f"Please enter a number between 1 and {len(choices)}")


# ══════════════════════════════════════════════════════════════════════════════
# Hardware Detection
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SystemInfo:
    """Detected system information."""
    cpu_count: int
    cpu_model: str
    memory_gb: float
    is_virtual: bool
    cloud_provider: Optional[str]
    gpus: List[Dict[str, Any]]
    available_miners: List[str]
    python_version: str
    os_info: str


def detect_system() -> SystemInfo:
    """Detect system hardware and capabilities."""
    import multiprocessing
    import platform
    
    # CPU info
    cpu_count = multiprocessing.cpu_count()
    cpu_model = "Unknown"
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_model = line.split(":")[1].strip()
                        break
    except Exception:
        pass
    
    # Memory
    memory_gb = 4.0
    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        memory_gb = kb / 1024 / 1024
                        break
    except Exception:
        pass
    
    # Virtual/cloud detection
    is_virtual = False
    cloud_provider = None
    
    virtual_indicators = [
        os.path.exists('/sys/hypervisor/type'),
        os.path.exists('/proc/xen'),
        os.path.exists('/.dockerenv'),
        os.getenv('KUBERNETES_SERVICE_HOST') is not None,
    ]
    is_virtual = any(virtual_indicators)
    
    # Cloud provider detection
    if os.getenv('RENDER'):
        cloud_provider = "Render"
    elif os.getenv('RAILWAY_ENVIRONMENT'):
        cloud_provider = "Railway"
    elif os.getenv('HEROKU_APP_NAME') or os.getenv('DYNO'):
        cloud_provider = "Heroku"
    elif os.getenv('FLY_APP_NAME'):
        cloud_provider = "Fly.io"
    elif os.path.exists("/sys/hypervisor/uuid"):
        try:
            with open("/sys/hypervisor/uuid") as f:
                if f.read().strip().startswith("ec2"):
                    cloud_provider = "AWS"
        except Exception:
            pass
    elif os.getenv('GOOGLE_CLOUD_PROJECT'):
        cloud_provider = "Google Cloud"
    elif os.getenv('AZURE_SUBSCRIPTION_ID'):
        cloud_provider = "Azure"
    
    # GPU detection
    gpus = detect_gpus()
    
    # Available miners
    miners = detect_miners()
    
    return SystemInfo(
        cpu_count=cpu_count,
        cpu_model=cpu_model,
        memory_gb=round(memory_gb, 1),
        is_virtual=is_virtual,
        cloud_provider=cloud_provider,
        gpus=gpus,
        available_miners=miners,
        python_version=platform.python_version(),
        os_info=f"{platform.system()} {platform.release()}",
    )


def detect_gpus() -> List[Dict[str, Any]]:
    """Detect available GPUs."""
    gpus = []
    
    # NVIDIA GPUs
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        gpus.append({
                            "id": int(parts[0]),
                            "name": parts[1],
                            "memory_mb": int(float(parts[2])),
                            "vendor": "NVIDIA",
                            "driver": parts[3] if len(parts) > 3 else "",
                        })
        except Exception:
            pass
    
    # AMD GPUs
    if shutil.which("rocm-smi"):
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                # Parse AMD GPU output
                gpu_id = len(gpus)
                for line in result.stdout.split("\n"):
                    if "GPU" in line and ":" in line:
                        name = line.split(":")[-1].strip()
                        # Use conservative 4GB estimate for unknown AMD GPUs
                        # Actual memory should be queried via rocm-smi --showmeminfo
                        memory_mb = 4096
                        # Try to get actual memory
                        try:
                            mem_result = subprocess.run(
                                ["rocm-smi", "-d", str(gpu_id), "--showmeminfo", "vram"],
                                capture_output=True, text=True, timeout=5
                            )
                            if mem_result.returncode == 0:
                                # Look for memory size in output
                                import re
                                mem_match = re.search(r"(\d+)\s*MB", mem_result.stdout, re.IGNORECASE)
                                if mem_match:
                                    memory_mb = int(mem_match.group(1))
                        except Exception:
                            pass
                        
                        gpus.append({
                            "id": gpu_id,
                            "name": name or "AMD GPU",
                            "memory_mb": memory_mb,
                            "vendor": "AMD",
                            "driver": "",
                        })
                        gpu_id += 1
        except Exception:
            pass
    
    return gpus


def detect_miners() -> List[str]:
    """Detect available mining software."""
    miners = []
    
    miner_executables = {
        "xmrig": "XMRig",
        "t-rex": "T-Rex",
        "lolMiner": "lolMiner",
        "miner": "GMiner",
        "bzminer": "BzMiner",
        "teamredminer": "TeamRedMiner",
        "nbminer": "NBMiner",
    }
    
    for exe, name in miner_executables.items():
        if shutil.which(exe):
            miners.append(name)
    
    return miners


# ══════════════════════════════════════════════════════════════════════════════
# Pool Configuration
# ══════════════════════════════════════════════════════════════════════════════

POPULAR_POOLS = {
    "2miners": {
        "name": "2Miners",
        "algorithms": {
            "etchash": "stratum+tcp://etc.2miners.com:1010",
            "kawpow": "stratum+tcp://rvn.2miners.com:6060",
            "autolykos2": "stratum+tcp://erg.2miners.com:8888",
            "kheavyhash": "stratum+tcp://kas.2miners.com:2020",
            "blake3": "stratum+tcp://alph.2miners.com:2020",
        },
        "fee": "1%",
    },
    "ethermine": {
        "name": "Ethermine/Flexpool",
        "algorithms": {
            "etchash": "stratum+tcp://etc.ethermine.org:4444",
        },
        "fee": "1%",
    },
    "f2pool": {
        "name": "F2Pool",
        "algorithms": {
            "etchash": "stratum+tcp://etc.f2pool.com:8118",
            "kawpow": "stratum+tcp://rvn.f2pool.com:3636",
        },
        "fee": "2.5%",
    },
    "hiveon": {
        "name": "Hiveon Pool",
        "algorithms": {
            "etchash": "stratum+tcp://etc.hiveon.com:4444",
            "kawpow": "stratum+tcp://rvn.hiveon.com:4444",
        },
        "fee": "0%",
    },
    "nanopool": {
        "name": "Nanopool",
        "algorithms": {
            "etchash": "stratum+tcp://etc-eu1.nanopool.org:19999",
            "autolykos2": "stratum+tcp://ergo-eu1.nanopool.org:11111",
        },
        "fee": "1%",
    },
}

COINS_BY_ALGORITHM = {
    "sha256": ["BTC (Bitcoin)", "BCH (Bitcoin Cash)"],
    "scrypt": ["LTC (Litecoin)", "DOGE (Dogecoin)"],
    "etchash": ["ETC (Ethereum Classic)"],
    "kawpow": ["RVN (Ravencoin)"],
    "autolykos2": ["ERG (Ergo)"],
    "kheavyhash": ["KAS (Kaspa)"],
    "blake3": ["ALPH (Alephium)"],
    "randomx": ["XMR (Monero)"],
    "octopus": ["CFX (Conflux)"],
}


# ══════════════════════════════════════════════════════════════════════════════
# Setup Wizard Steps
# ══════════════════════════════════════════════════════════════════════════════

class MiningSetupWizard:
    """Interactive setup wizard for mining configuration."""
    
    def __init__(self):
        self.system_info: Optional[SystemInfo] = None
        self.config: Dict[str, Any] = {}
        self.env_file = Path(".env")
    
    def run(self):
        """Run the complete setup wizard."""
        self._print_welcome()
        
        # Step 1: System Detection
        if not self._step_detect_system():
            return False
        
        # Step 2: Mining Mode Selection
        if not self._step_select_mining_mode():
            return False
        
        # Step 3: Algorithm and Coin Selection
        if not self._step_select_algorithm():
            return False
        
        # Step 4: Pool Configuration
        if not self._step_configure_pool():
            return False
        
        # Step 5: Wallet Setup
        if not self._step_configure_wallet():
            return False
        
        # Step 6: GPU Optimization (if applicable)
        if self.system_info.gpus:
            if not self._step_configure_gpu():
                return False
        
        # Step 7: AI Optimization
        if not self._step_configure_ai():
            return False
        
        # Step 8: Payout Configuration
        if not self._step_configure_payout():
            return False
        
        # Step 9: Review and Save
        if not self._step_review_and_save():
            return False
        
        # Step 10: Test and Launch
        if not self._step_test_and_launch():
            return False
        
        self._print_completion()
        return True
    
    def _print_welcome(self):
        """Print welcome message."""
        print("\n")
        print(colored("╔════════════════════════════════════════════════════════════╗", Colors.CYAN))
        print(colored("║                                                            ║", Colors.CYAN))
        print(colored("║      🚀 NEXUS AI MINING MACHINE SETUP WIZARD 🚀           ║", Colors.CYAN + Colors.BOLD))
        print(colored("║                                                            ║", Colors.CYAN))
        print(colored("║   Configure your mining operation to compete with          ║", Colors.CYAN))
        print(colored("║   dedicated hardware and start earning cryptocurrency!     ║", Colors.CYAN))
        print(colored("║                                                            ║", Colors.CYAN))
        print(colored("╚════════════════════════════════════════════════════════════╝", Colors.CYAN))
        print("\n")
        
        print("  This wizard will guide you through:")
        print("    1. Hardware detection and optimization")
        print("    2. Mining pool and algorithm selection")
        print("    3. Wallet and payout configuration")
        print("    4. AI-powered optimization setup")
        print("    5. System test and launch")
        print("\n")
        
        if not prompt_yes_no("Ready to begin?", default=True):
            print_info("Setup cancelled. Run this wizard again when ready.")
            sys.exit(0)
    
    def _step_detect_system(self) -> bool:
        """Step 1: Detect system hardware."""
        print_header("STEP 1: System Detection")
        
        print("  Scanning your system...\n")
        time.sleep(1)
        
        self.system_info = detect_system()
        
        # Display results
        print(colored("  System Information:", Colors.BOLD))
        print(f"    • OS: {self.system_info.os_info}")
        print(f"    • Python: {self.system_info.python_version}")
        print(f"    • CPU: {self.system_info.cpu_model}")
        print(f"    • CPU Cores: {self.system_info.cpu_count}")
        print(f"    • Memory: {self.system_info.memory_gb} GB")
        
        if self.system_info.is_virtual:
            print(f"    • Environment: Virtual/Cloud", end="")
            if self.system_info.cloud_provider:
                print(f" ({self.system_info.cloud_provider})")
            else:
                print()
        else:
            print(f"    • Environment: Physical Machine")
        
        print()
        
        # GPU info
        if self.system_info.gpus:
            print_success(f"Found {len(self.system_info.gpus)} GPU(s)!")
            for gpu in self.system_info.gpus:
                print(f"    • [{gpu['id']}] {gpu['vendor']} {gpu['name']} - {gpu['memory_mb']} MB")
        else:
            print_warning("No GPUs detected. CPU mining will be used.")
            print_info("GPU mining is more profitable. Consider adding a GPU or using a cloud GPU instance.")
        
        print()
        
        # Available miners
        if self.system_info.available_miners:
            print_success(f"Found mining software: {', '.join(self.system_info.available_miners)}")
        else:
            print_warning("No external mining software detected.")
            print_info("Built-in CPU miner will be used. For GPU mining, install T-Rex, lolMiner, or XMRig.")
        
        print()
        return prompt_yes_no("Continue with this configuration?", default=True)
    
    def _step_select_mining_mode(self) -> bool:
        """Step 2: Select mining mode."""
        print_header("STEP 2: Mining Mode")
        
        modes = []
        if self.system_info.gpus:
            modes.append("GPU Mining (Recommended - Higher hashrate)")
        modes.append("CPU Mining (Lower hashrate, works on any system)")
        modes.append("Hybrid (Both CPU and GPU)")
        
        choice = prompt_choice("Select mining mode:", modes, default=0)
        
        if "GPU" in modes[choice] and not "CPU" in modes[choice]:
            self.config["mining_mode"] = "gpu"
            print_success("GPU mining selected - Maximum performance!")
        elif "CPU" in modes[choice] and not "GPU" in modes[choice]:
            self.config["mining_mode"] = "cpu"
            print_info("CPU mining selected")
        else:
            self.config["mining_mode"] = "hybrid"
            print_success("Hybrid mining selected - Using all available hardware!")
        
        return True
    
    def _step_select_algorithm(self) -> bool:
        """Step 3: Select mining algorithm and coin."""
        print_header("STEP 3: Algorithm & Coin Selection")
        
        # Determine available algorithms based on hardware
        algorithms = []
        descriptions = []
        
        if self.config.get("mining_mode") in ("gpu", "hybrid") and self.system_info.gpus:
            # GPU-optimized algorithms
            gpu_algos = [
                ("etchash", "Ethereum Classic (ETC) - Stable, well-established"),
                ("kawpow", "Ravencoin (RVN) - ASIC-resistant, good for GPUs"),
                ("autolykos2", "Ergo (ERG) - Memory-hard, efficient"),
                ("kheavyhash", "Kaspa (KAS) - Fast blocks, growing ecosystem"),
                ("blake3", "Alephium (ALPH) - Energy efficient"),
            ]
            for algo, desc in gpu_algos:
                algorithms.append(algo)
                descriptions.append(desc)
        
        # CPU algorithms (always available)
        cpu_algos = [
            ("sha256", "Bitcoin/Bitcoin Cash - Classic PoW (low profit on CPU)"),
            ("scrypt", "Litecoin/Dogecoin - Memory-hard"),
            ("randomx", "Monero (XMR) - CPU-optimized, private"),
        ]
        
        for algo, desc in cpu_algos:
            if algo not in algorithms:
                algorithms.append(algo)
                descriptions.append(desc)
        
        print("  Available algorithms based on your hardware:\n")
        
        # Recommend based on hardware
        recommended = 0
        if self.system_info.gpus:
            # Recommend based on GPU memory
            gpu_mem = self.system_info.gpus[0]["memory_mb"]
            if gpu_mem >= 6144:
                recommended = algorithms.index("etchash") if "etchash" in algorithms else 0
            else:
                recommended = algorithms.index("kawpow") if "kawpow" in algorithms else 0
        else:
            recommended = algorithms.index("randomx") if "randomx" in algorithms else 0
        
        choice = prompt_choice("Select algorithm:", descriptions, default=recommended)
        selected_algo = algorithms[choice]
        
        self.config["algorithm"] = selected_algo
        
        # Show coins for this algorithm
        coins = COINS_BY_ALGORITHM.get(selected_algo, [])
        if coins:
            print(f"\n  Coins using {selected_algo}: {', '.join(coins)}")
        
        print_success(f"Selected: {selected_algo.upper()}")
        
        return True
    
    def _step_configure_pool(self) -> bool:
        """Step 4: Configure mining pool."""
        print_header("STEP 4: Mining Pool Configuration")
        
        algo = self.config["algorithm"]
        
        # Find pools that support this algorithm
        available_pools = []
        pool_urls = []
        
        for pool_id, pool_info in POPULAR_POOLS.items():
            if algo in pool_info["algorithms"]:
                available_pools.append(f"{pool_info['name']} (Fee: {pool_info['fee']})")
                pool_urls.append(pool_info["algorithms"][algo])
        
        if available_pools:
            print(f"  Popular pools for {algo.upper()}:\n")
            choice = prompt_choice("Select a pool or enter custom:", 
                                   available_pools + ["Custom pool URL"], default=0)
            
            if choice < len(pool_urls):
                self.config["pool_url"] = pool_urls[choice]
                print_success(f"Selected: {available_pools[choice]}")
            else:
                # Custom pool
                self.config["pool_url"] = prompt("Enter pool URL (stratum+tcp://...)")
        else:
            print_warning(f"No pre-configured pools for {algo}. Please enter custom pool.")
            self.config["pool_url"] = prompt("Enter pool URL (stratum+tcp://...)")
        
        # Worker name
        print()
        default_worker = os.getenv("USER", "worker1")
        self.config["pool_user"] = prompt("Enter pool username/worker name", default_worker)
        self.config["pool_password"] = prompt("Enter pool password (usually 'x')", "x")
        
        # Backup pool
        print()
        if prompt_yes_no("Configure backup pool for failover?", default=False):
            self.config["backup_pool"] = prompt("Enter backup pool URL")
        
        return True
    
    def _step_configure_wallet(self) -> bool:
        """Step 5: Configure wallet for mining rewards."""
        print_header("STEP 5: Wallet Configuration")
        
        print("  Your mining rewards will be sent to this wallet address.")
        print("  Make sure to use a wallet address compatible with your selected coin.\n")
        
        # Wallet address
        while True:
            wallet = prompt("Enter your wallet address")
            if len(wallet) >= 20:  # Basic validation
                self.config["payout_address"] = wallet
                break
            print_error("Invalid wallet address. Please enter a valid address.")
        
        print_success("Wallet configured!")
        
        # Optional: Trading wallet for bot operations
        print()
        print_info("Optional: Configure a separate wallet for DeFi/trading operations.")
        if prompt_yes_no("Set up trading wallet? (for arbitrage, yield farming)", default=False):
            self.config["wallet_address"] = prompt("Enter EVM wallet address (0x...)")
            
            print()
            print_warning("⚠️  SECURITY WARNING ⚠️")
            print("  The private key should NEVER be shared or stored insecurely.")
            print("  Only enter it if you understand the risks.\n")
            
            if prompt_yes_no("Enter private key now?", default=False):
                self.config["wallet_private_key"] = prompt("Private key (will be stored in .env)")
        
        return True
    
    def _step_configure_gpu(self) -> bool:
        """Step 6: Configure GPU optimization."""
        print_header("STEP 6: GPU Optimization")
        
        print("  Configuring your GPU(s) for maximum mining efficiency.\n")
        
        for gpu in self.system_info.gpus:
            print(f"  GPU {gpu['id']}: {gpu['name']}")
            
            # Show recommended settings
            print(f"    Memory: {gpu['memory_mb']} MB")
            
            # Estimate hashrate
            algo = self.config["algorithm"]
            estimated_hashrate = self._estimate_hashrate(gpu, algo)
            if estimated_hashrate > 0:
                print(f"    Estimated hashrate: {self._format_hashrate(estimated_hashrate)}")
        
        print()
        
        # Mining intensity
        print("  Mining intensity affects power usage and hashrate.")
        print("  Higher = more hashrate but more heat/power.\n")
        
        intensities = [
            "Low (50%) - Cooler, lower power, background mining",
            "Medium (75%) - Balanced performance",
            "High (90%) - Maximum performance, more heat",
            "Maximum (100%) - Full power (watch temperatures!)",
        ]
        
        intensity_values = [50, 75, 90, 100]
        choice = prompt_choice("Select mining intensity:", intensities, default=2)
        self.config["intensity"] = intensity_values[choice]
        
        # Power limit
        print()
        if prompt_yes_no("Enable power optimization (reduces electricity cost)?", default=True):
            self.config["power_limit_percent"] = 80
            print_success("Power limit set to 80% - optimizes efficiency!")
        else:
            self.config["power_limit_percent"] = 100
        
        # Temperature limit
        print()
        self.config["temp_limit"] = int(prompt("Maximum GPU temperature (°C)", "83"))
        
        return True
    
    def _step_configure_ai(self) -> bool:
        """Step 7: Configure AI optimization."""
        print_header("STEP 7: AI Optimization")
        
        print("  Nexus AI can automatically optimize your mining for maximum profit.\n")
        print("  Features include:")
        print("    • Real-time hashrate optimization")
        print("    • Automatic parameter tuning")
        print("    • Temperature-based throttling")
        print("    • Profit-based coin switching")
        print("    • Learning from mining performance\n")
        
        if prompt_yes_no("Enable AI mining optimization?", default=True):
            self.config["ai_optimization"] = True
            print_success("AI optimization enabled!")
            
            # Profit switching
            print()
            if prompt_yes_no("Enable automatic profit switching?", default=False):
                self.config["profit_switching"] = True
                self.config["profit_switch_threshold"] = int(prompt(
                    "Minimum profit improvement % to switch coins", "10"
                ))
                print_success("Profit switching enabled!")
            else:
                self.config["profit_switching"] = False
        else:
            self.config["ai_optimization"] = False
        
        # Adaptive mode
        print()
        if prompt_yes_no("Enable adaptive resource management?", default=True):
            self.config["adaptive_mode"] = True
            self.config["max_cpu_percent"] = int(prompt("Maximum CPU usage %", "80"))
            print_success("Adaptive mode enabled - prevents system throttling!")
        else:
            self.config["adaptive_mode"] = False
        
        return True
    
    def _step_configure_payout(self) -> bool:
        """Step 8: Configure payout settings."""
        print_header("STEP 8: Payout Configuration")
        
        print("  Configure how you want to receive your mining earnings.\n")
        
        # Payout threshold
        self.config["payout_threshold"] = float(prompt("Minimum USD to trigger payout", "10.00"))
        
        # Payout method
        print()
        payout_methods = [
            "Direct to wallet (default)",
            "Coinbase (automatic conversion to USD)",
            "Cash App / Lightning (Bitcoin)",
        ]
        
        choice = prompt_choice("Select payout method:", payout_methods, default=0)
        
        if choice == 1:
            # Coinbase
            print()
            print_info("Get API keys at: https://www.coinbase.com/settings/api")
            self.config["coinbase_api_key"] = prompt("Coinbase API Key")
            self.config["coinbase_api_secret"] = prompt("Coinbase API Secret")
        elif choice == 2:
            # Lightning
            print()
            print_info("Enter your Lightning address or Cash App $cashtag")
            self.config["lightning_address"] = prompt("Lightning address (e.g., $cashtag or user@wallet.com)")
        
        return True
    
    def _step_review_and_save(self) -> bool:
        """Step 9: Review configuration and save."""
        print_header("STEP 9: Review Configuration")
        
        print(colored("  Mining Configuration Summary:", Colors.BOLD))
        print()
        print(f"    Mode:           {self.config.get('mining_mode', 'cpu').upper()}")
        print(f"    Algorithm:      {self.config.get('algorithm', 'sha256').upper()}")
        print(f"    Pool:           {self.config.get('pool_url', 'Not set')}")
        print(f"    Worker:         {self.config.get('pool_user', 'Not set')}")
        print(f"    Payout Address: {self._mask_address(self.config.get('payout_address', ''))}")
        print(f"    Intensity:      {self.config.get('intensity', 50)}%")
        print(f"    AI Optimization: {'Enabled' if self.config.get('ai_optimization') else 'Disabled'}")
        print(f"    Profit Switch:  {'Enabled' if self.config.get('profit_switching') else 'Disabled'}")
        
        if self.system_info.gpus:
            print()
            print(colored("  GPU Settings:", Colors.BOLD))
            print(f"    Power Limit:    {self.config.get('power_limit_percent', 100)}%")
            print(f"    Temp Limit:     {self.config.get('temp_limit', 83)}°C")
        
        print()
        
        if not prompt_yes_no("Save this configuration?", default=True):
            print_info("Configuration not saved. You can restart the wizard.")
            return False
        
        # Save to .env file
        self._save_config()
        
        print_success("Configuration saved to .env file!")
        return True
    
    def _step_test_and_launch(self) -> bool:
        """Step 10: Test configuration and launch mining."""
        print_header("STEP 10: Test & Launch")
        
        print("  Running pre-flight checks...\n")
        time.sleep(1)
        
        checks_passed = True
        
        # Check pool connectivity
        print("  • Testing pool connection...", end=" ")
        pool_url = self.config.get("pool_url", "")
        if pool_url:
            print_success("OK")
        else:
            print_error("No pool configured")
            checks_passed = False
        
        # Check wallet
        print("  • Validating wallet address...", end=" ")
        if self.config.get("payout_address"):
            print_success("OK")
        else:
            print_error("No wallet configured")
            checks_passed = False
        
        # Check GPU (if GPU mode)
        if self.config.get("mining_mode") in ("gpu", "hybrid"):
            print("  • Checking GPU access...", end=" ")
            if self.system_info.gpus:
                print_success(f"OK ({len(self.system_info.gpus)} GPU(s))")
            else:
                print_warning("No GPUs (will use CPU)")
        
        # Check mining software
        print("  • Checking mining software...", end=" ")
        if self.system_info.available_miners or self.config.get("mining_mode") == "cpu":
            print_success("OK")
        else:
            print_warning("External miner not found (using built-in)")
        
        print()
        
        if not checks_passed:
            print_error("Some checks failed. Please review your configuration.")
            return False
        
        print_success("All checks passed!")
        print()
        
        # Launch options
        launch_options = [
            "Start mining now (foreground)",
            "Start mining in background",
            "Generate start command only",
            "Exit (start manually later)",
        ]
        
        choice = prompt_choice("What would you like to do?", launch_options, default=0)
        
        if choice == 0:
            # Start in foreground
            print()
            print_info("Starting mining... Press Ctrl+C to stop.")
            print()
            return self._start_mining(background=False)
        elif choice == 1:
            # Start in background
            print()
            return self._start_mining(background=True)
        elif choice == 2:
            # Show command
            print()
            print("  To start mining, run:")
            print(colored(f"    python -c \"from nexus.strategies.pow_mining import PoWMiningStrategy; ...\"", Colors.CYAN))
            print()
            print("  Or start the Nexus AI dashboard:")
            print(colored("    python app.py", Colors.CYAN))
            return True
        else:
            print_info("Setup complete. Start mining when ready.")
            return True
    
    def _print_completion(self):
        """Print completion message."""
        print("\n")
        print(colored("╔════════════════════════════════════════════════════════════╗", Colors.GREEN))
        print(colored("║                                                            ║", Colors.GREEN))
        print(colored("║        🎉 MINING SETUP COMPLETE! 🎉                        ║", Colors.GREEN + Colors.BOLD))
        print(colored("║                                                            ║", Colors.GREEN))
        print(colored("╚════════════════════════════════════════════════════════════╝", Colors.GREEN))
        print()
        print("  Your mining machine is configured and ready to earn!")
        print()
        print("  Next steps:")
        print("    1. Monitor your mining at the dashboard: http://localhost:10000")
        print("    2. Check your pool's website for hashrate and payouts")
        print("    3. Monitor GPU temperatures (keep below 83°C)")
        print()
        print("  Useful commands:")
        print(colored("    python app.py", Colors.CYAN) + "          - Start the Nexus AI dashboard")
        print(colored("    python mining_wizard.py", Colors.CYAN) + " - Re-run this setup wizard")
        print()
        print(colored("  Happy mining! 💰", Colors.GREEN + Colors.BOLD))
        print()
    
    def _save_config(self):
        """Save configuration to .env file."""
        env_lines = []
        
        # Read existing .env if it exists
        if self.env_file.exists():
            with open(self.env_file) as f:
                env_lines = f.readlines()
        
        # Configuration mapping
        config_map = {
            "STRATEGY_POW_MINING": "true",
            "MINING_POOL_URL": self.config.get("pool_url", ""),
            "MINING_POOL_USER": self.config.get("pool_user", ""),
            "MINING_POOL_PASSWORD": self.config.get("pool_password", "x"),
            "MINING_ALGORITHM": self.config.get("algorithm", "sha256"),
            "MINING_INTENSITY": str(self.config.get("intensity", 50)),
            "MINING_PAYOUT_ADDRESS": self.config.get("payout_address", ""),
            "MINING_USE_GPU": "true" if self.config.get("mining_mode") in ("gpu", "hybrid") else "false",
            "MINING_ADAPTIVE_MODE": "true" if self.config.get("adaptive_mode") else "false",
            "MINING_MAX_CPU_PERCENT": str(self.config.get("max_cpu_percent", 80)),
            "MINING_AI_OPTIMIZATION": "true" if self.config.get("ai_optimization") else "false",
            "MINING_PROFIT_SWITCHING": "true" if self.config.get("profit_switching") else "false",
            "MINING_PROFIT_SWITCH_THRESHOLD": str(self.config.get("profit_switch_threshold", 10)),
            "PAYOUT_THRESHOLD_USD": str(self.config.get("payout_threshold", 10.0)),
        }
        
        # Add optional configs
        if self.config.get("backup_pool"):
            config_map["MINING_BACKUP_POOLS"] = self.config["backup_pool"]
        if self.config.get("wallet_address"):
            config_map["WALLET_ADDRESS"] = self.config["wallet_address"]
        if self.config.get("wallet_private_key"):
            config_map["WALLET_PRIVATE_KEY"] = self.config["wallet_private_key"]
        if self.config.get("coinbase_api_key"):
            config_map["COINBASE_API_KEY"] = self.config["coinbase_api_key"]
            config_map["COINBASE_API_SECRET"] = self.config.get("coinbase_api_secret", "")
        if self.config.get("lightning_address"):
            config_map["PAYOUT_LIGHTNING_ADDRESS"] = self.config["lightning_address"]
        
        # Update or add each config
        for key, value in config_map.items():
            found = False
            for i, line in enumerate(env_lines):
                if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                    env_lines[i] = f"{key}={value}\n"
                    found = True
                    break
            if not found:
                env_lines.append(f"{key}={value}\n")
        
        # Write .env file
        with open(self.env_file, "w") as f:
            f.writelines(env_lines)
    
    def _start_mining(self, background: bool = False) -> bool:
        """Start the mining operation."""
        try:
            if background:
                # Start in background
                subprocess.Popen(
                    [sys.executable, "app.py"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                print_success("Mining started in background!")
                print_info("Dashboard available at: http://localhost:10000")
                return True
            else:
                # Start in foreground
                os.execv(sys.executable, [sys.executable, "app.py"])
                return True
        except Exception as e:
            print_error(f"Failed to start mining: {e}")
            return False
    
    def _estimate_hashrate(self, gpu: dict, algorithm: str) -> float:
        """Estimate hashrate for a GPU and algorithm."""
        # Rough estimates based on GPU memory and algorithm
        memory_mb = gpu.get("memory_mb", 0)
        
        # Base estimates (H/s)
        estimates = {
            "etchash": memory_mb * 6000,      # ~6 MH/s per GB
            "kawpow": memory_mb * 2500,       # ~2.5 MH/s per GB
            "autolykos2": memory_mb * 15000,  # ~15 MH/s per GB
            "kheavyhash": memory_mb * 50000,  # ~50 MH/s per GB
            "blake3": memory_mb * 100000,     # ~100 MH/s per GB
        }
        
        return estimates.get(algorithm, 0)
    
    def _format_hashrate(self, hashrate: float) -> str:
        """Format hashrate with appropriate unit."""
        if hashrate >= 1e12:
            return f"{hashrate / 1e12:.2f} TH/s"
        elif hashrate >= 1e9:
            return f"{hashrate / 1e9:.2f} GH/s"
        elif hashrate >= 1e6:
            return f"{hashrate / 1e6:.2f} MH/s"
        elif hashrate >= 1e3:
            return f"{hashrate / 1e3:.2f} KH/s"
        return f"{hashrate:.2f} H/s"
    
    def _mask_address(self, address: str) -> str:
        """Mask wallet address for display."""
        if not address:
            return "Not set"
        if len(address) > 12:
            return f"{address[:6]}...{address[-4:]}"
        return address


# ══════════════════════════════════════════════════════════════════════════════
# Quick Setup Functions
# ══════════════════════════════════════════════════════════════════════════════

def quick_setup_cpu_mining(pool_url: str, wallet: str, worker: str = "nexus") -> dict:
    """Quick setup for CPU mining without interactive wizard."""
    config = {
        "STRATEGY_POW_MINING": "true",
        "MINING_POOL_URL": pool_url,
        "MINING_POOL_USER": wallet if "." in wallet else f"{wallet}.{worker}",
        "MINING_POOL_PASSWORD": "x",
        "MINING_ALGORITHM": "randomx",
        "MINING_INTENSITY": "80",
        "MINING_PAYOUT_ADDRESS": wallet.split(".")[0] if "." in wallet else wallet,
        "MINING_USE_GPU": "false",
        "MINING_ADAPTIVE_MODE": "true",
        "MINING_AI_OPTIMIZATION": "true",
    }
    return config


def quick_setup_gpu_mining(pool_url: str, wallet: str, algorithm: str = "etchash", worker: str = "nexus") -> dict:
    """Quick setup for GPU mining without interactive wizard."""
    config = {
        "STRATEGY_POW_MINING": "true",
        "MINING_POOL_URL": pool_url,
        "MINING_POOL_USER": wallet if "." in wallet else f"{wallet}.{worker}",
        "MINING_POOL_PASSWORD": "x",
        "MINING_ALGORITHM": algorithm,
        "MINING_INTENSITY": "90",
        "MINING_PAYOUT_ADDRESS": wallet.split(".")[0] if "." in wallet else wallet,
        "MINING_USE_GPU": "true",
        "MINING_ADAPTIVE_MODE": "true",
        "MINING_AI_OPTIMIZATION": "true",
        "MINING_PROFIT_SWITCHING": "false",
    }
    return config


# ══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """Main entry point for the mining setup wizard."""
    wizard = MiningSetupWizard()
    
    try:
        success = wizard.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n")
        print_info("Setup cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print_error(f"Setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
