#!/usr/bin/env python3
"""
Nexus AI Setup Wizard
Interactive command-line wizard to help you configure your crypto farming bot.
Run: python setup_wizard.py
"""

import os
import sys
import re
import getpass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    """Print the setup wizard banner."""
    banner = f"""
{Colors.CYAN}╔══════════════════════════════════════════════════════════════════════╗
║                                                                      ║
║{Colors.BOLD}            🚀 NEXUS AI CRYPTO FARMER - SETUP WIZARD 🚀              {Colors.CYAN}║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝{Colors.ENDC}
"""
    print(banner)


def print_section(title: str):
    """Print a section header."""
    print(f"\n{Colors.BLUE}{'═' * 60}")
    print(f"  {Colors.BOLD}{title}{Colors.ENDC}")
    print(f"{Colors.BLUE}{'═' * 60}{Colors.ENDC}\n")


def print_warning(msg: str):
    """Print a warning message."""
    print(f"{Colors.YELLOW}⚠️  {msg}{Colors.ENDC}")


def print_success(msg: str):
    """Print a success message."""
    print(f"{Colors.GREEN}✅ {msg}{Colors.ENDC}")


def print_error(msg: str):
    """Print an error message."""
    print(f"{Colors.RED}❌ {msg}{Colors.ENDC}")


def print_info(msg: str):
    """Print an info message."""
    print(f"{Colors.CYAN}ℹ️  {msg}{Colors.ENDC}")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question."""
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_str}]: ").strip().lower()
        if response == "":
            return default
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'")


def ask_input(prompt: str, default: str = "", required: bool = False, is_secret: bool = False) -> str:
    """Ask for user input with optional default and validation."""
    default_display = f" [{default}]" if default and not is_secret else ""
    prompt_str = f"{prompt}{default_display}: "
    
    while True:
        if is_secret:
            value = getpass.getpass(prompt_str)
        else:
            value = input(prompt_str).strip()
        
        if not value and default:
            return default
        if not value and required:
            print_error("This field is required. Please enter a value.")
            continue
        if value or not required:
            return value


def ask_choice(prompt: str, choices: list, default: int = 0) -> int:
    """Ask user to select from a list of choices."""
    print(f"\n{prompt}")
    for i, choice in enumerate(choices):
        marker = "→" if i == default else " "
        print(f"  {marker} {i + 1}. {choice}")
    
    while True:
        response = input(f"\nEnter your choice [1-{len(choices)}] (default: {default + 1}): ").strip()
        if not response:
            return default
        try:
            idx = int(response) - 1
            if 0 <= idx < len(choices):
                return idx
        except ValueError:
            pass
        print(f"Please enter a number between 1 and {len(choices)}")


def validate_wallet_address(address: str) -> bool:
    """Validate an Ethereum-style wallet address."""
    return bool(re.match(r'^0x[a-fA-F0-9]{40}$', address))


def validate_private_key(key: str) -> bool:
    """Validate a private key format."""
    # Private key can be with or without 0x prefix
    key = key.lower()
    if key.startswith('0x'):
        key = key[2:]
    return bool(re.match(r'^[a-f0-9]{64}$', key))


def validate_rpc_url(url: str) -> bool:
    """Validate an RPC URL format."""
    return url.startswith('http://') or url.startswith('https://')


class SetupWizard:
    """Interactive setup wizard for Nexus AI."""
    
    def __init__(self):
        self.config: Dict[str, Any] = {}
        self.env_path = Path(__file__).parent / '.env'
        
    def run(self):
        """Run the setup wizard."""
        clear_screen()
        print_banner()
        
        print(f"""
{Colors.BOLD}Welcome to the Nexus AI Setup Wizard!{Colors.ENDC}

This wizard will help you configure your crypto farming bot to connect
to live blockchain networks and start farming.

{Colors.YELLOW}⚠️  IMPORTANT WARNINGS:{Colors.ENDC}
  • Real trading involves REAL MONEY - you can lose your funds
  • Always start in SIMULATION mode (DRY_RUN=true) first
  • Use a DEDICATED wallet - never your main savings wallet
  • Start with SMALL amounts you can afford to lose

""")
        
        if not ask_yes_no("I understand the risks. Continue with setup?"):
            print("\nSetup cancelled. Stay safe! 🙏")
            return
        
        # Phase 1: Mode selection
        self._setup_mode()
        
        # Phase 2: Wallet configuration
        self._setup_wallet()
        
        # Phase 3: Blockchain RPCs
        self._setup_blockchains()
        
        # Phase 4: Trading parameters
        self._setup_trading_params()
        
        # Phase 5: Strategies
        self._setup_strategies()
        
        # Phase 6: Payout configuration
        self._setup_payouts()
        
        # Phase 7: Optional features
        self._setup_optional_features()
        
        # Phase 8: Review and save
        self._review_and_save()
    
    def _setup_mode(self):
        """Set up simulation vs live mode."""
        print_section("STEP 1: Trading Mode")
        
        print(f"""
Choose how you want to run the bot:

{Colors.GREEN}SIMULATION MODE (Recommended for beginners){Colors.ENDC}
  • No real trades executed
  • Test your configuration safely
  • See what the bot would do
  • Run for 1-2 hours before going live

{Colors.YELLOW}LIVE MODE (Real trading){Colors.ENDC}
  • Executes REAL trades with REAL crypto
  • You can make profits OR losses
  • Requires funded wallet
""")
        
        mode = ask_choice("Select trading mode:", [
            "Simulation Mode (DRY_RUN=true) - Recommended to start",
            "Live Mode (DRY_RUN=false) - Real trading"
        ], default=0)
        
        self.config['DRY_RUN'] = 'true' if mode == 0 else 'false'
        
        if mode == 1:
            print_warning("\nYou selected LIVE MODE. Please confirm you understand:")
            print("  • Real cryptocurrency will be used for trades")
            print("  • You can lose money")
            print("  • Gas fees will be charged for transactions")
            if not ask_yes_no("Are you sure you want LIVE MODE?", default=False):
                self.config['DRY_RUN'] = 'true'
                print_info("Switched to simulation mode for safety.")
    
    def _setup_wallet(self):
        """Set up wallet configuration."""
        print_section("STEP 2: Wallet Configuration")
        
        print(f"""
You need to provide your trading wallet details.

{Colors.YELLOW}How to get your wallet address and private key:{Colors.ENDC}

  1. Open MetaMask (or your wallet)
  2. Wallet Address: Click on your account name to copy
     - Starts with 0x and is 42 characters long
  3. Private Key:
     - Click the ⋮ menu → Account details → Show private key
     - {Colors.RED}NEVER SHARE THIS WITH ANYONE!{Colors.ENDC}
     - It's 64 hex characters (with or without 0x prefix)

{Colors.RED}⚠️  SECURITY WARNING:{Colors.ENDC}
{Colors.RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.ENDC}
{Colors.RED}Your private key will be stored in plaintext in the .env file.{Colors.ENDC}
{Colors.RED}To protect your funds:{Colors.ENDC}
{Colors.RED}  1. NEVER commit .env to git (it's in .gitignore){Colors.ENDC}
{Colors.RED}  2. Set file permissions: chmod 600 .env{Colors.ENDC}
{Colors.RED}  3. Use a DEDICATED wallet with limited funds{Colors.ENDC}
{Colors.RED}  4. Cloud deployments carry additional risk - use at your own risk{Colors.ENDC}
{Colors.RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.ENDC}
""")
        
        # Get wallet address
        while True:
            address = ask_input("Enter your wallet address (0x...)", required=True)
            if validate_wallet_address(address):
                self.config['WALLET_ADDRESS'] = address
                print_success(f"Wallet address: {address[:10]}...{address[-4:]}")
                break
            print_error("Invalid wallet address. Must be 42 characters starting with 0x")
        
        # Get private key
        print(f"\n{Colors.YELLOW}Your private key will not be displayed as you type.{Colors.ENDC}")
        while True:
            key = ask_input("Enter your wallet private key", required=True, is_secret=True)
            if validate_private_key(key):
                # Normalize to include 0x prefix
                if not key.startswith('0x'):
                    key = '0x' + key
                self.config['WALLET_PRIVATE_KEY'] = key
                print_success("Private key accepted and secured.")
                break
            print_error("Invalid private key format. Should be 64 hex characters.")
    
    def _setup_blockchains(self):
        """Set up blockchain RPC connections."""
        print_section("STEP 3: Blockchain Connections")
        
        print(f"""
The bot needs to connect to blockchain networks via RPC endpoints.

{Colors.CYAN}Where to get FREE RPC URLs:{Colors.ENDC}
  • Alchemy: https://www.alchemy.com/ (recommended)
  • Infura: https://infura.io/
  • Public endpoints (less reliable, but free)

You can enable multiple chains for more opportunities.
""")
        
        # Ethereum
        print(f"\n{Colors.BOLD}Ethereum Mainnet{Colors.ENDC}")
        if ask_yes_no("Enable Ethereum?", default=True):
            self.config['CHAIN_ETH'] = 'true'
            eth_rpc = ask_input(
                "Ethereum RPC URL",
                default="https://eth.llamarpc.com",
                required=True
            )
            if validate_rpc_url(eth_rpc):
                self.config['ETH_RPC_URL'] = eth_rpc
                print_success("Ethereum RPC configured")
            else:
                print_warning("Using default public RPC")
                self.config['ETH_RPC_URL'] = "https://eth.llamarpc.com"
        else:
            self.config['CHAIN_ETH'] = 'false'
        
        # Polygon
        print(f"\n{Colors.BOLD}Polygon (Low fees, recommended){Colors.ENDC}")
        if ask_yes_no("Enable Polygon?", default=True):
            self.config['CHAIN_POLYGON'] = 'true'
            polygon_rpc = ask_input(
                "Polygon RPC URL",
                default="https://polygon-rpc.com",
                required=True
            )
            self.config['POLYGON_RPC_URL'] = polygon_rpc if validate_rpc_url(polygon_rpc) else "https://polygon-rpc.com"
            print_success("Polygon RPC configured")
        else:
            self.config['CHAIN_POLYGON'] = 'false'
        
        # BSC
        print(f"\n{Colors.BOLD}BNB Smart Chain{Colors.ENDC}")
        if ask_yes_no("Enable BNB Smart Chain?", default=True):
            self.config['CHAIN_BSC'] = 'true'
            bsc_rpc = ask_input(
                "BSC RPC URL",
                default="https://bsc-dataseed1.binance.org/",
                required=True
            )
            self.config['BSC_RPC_URL'] = bsc_rpc if validate_rpc_url(bsc_rpc) else "https://bsc-dataseed1.binance.org/"
            print_success("BSC RPC configured")
        else:
            self.config['CHAIN_BSC'] = 'false'
        
        # Layer 2 chains (optional)
        print(f"\n{Colors.BOLD}Layer 2 Chains (Optional - Lower fees){Colors.ENDC}")
        
        # Arbitrum
        if ask_yes_no("Enable Arbitrum?", default=False):
            self.config['CHAIN_ARBITRUM'] = 'true'
            arb_rpc = ask_input(
                "Arbitrum RPC URL",
                default="https://arb1.arbitrum.io/rpc"
            )
            self.config['ARBITRUM_RPC_URL'] = arb_rpc or "https://arb1.arbitrum.io/rpc"
        else:
            self.config['CHAIN_ARBITRUM'] = 'false'
        
        # Base
        if ask_yes_no("Enable Base?", default=False):
            self.config['CHAIN_BASE'] = 'true'
            base_rpc = ask_input(
                "Base RPC URL",
                default="https://mainnet.base.org"
            )
            self.config['BASE_RPC_URL'] = base_rpc or "https://mainnet.base.org"
        else:
            self.config['CHAIN_BASE'] = 'false'
        
        # Optimism
        if ask_yes_no("Enable Optimism?", default=False):
            self.config['CHAIN_OPTIMISM'] = 'true'
            op_rpc = ask_input(
                "Optimism RPC URL",
                default="https://mainnet.optimism.io"
            )
            self.config['OPTIMISM_RPC_URL'] = op_rpc or "https://mainnet.optimism.io"
        else:
            self.config['CHAIN_OPTIMISM'] = 'false'
        
        # Avalanche
        if ask_yes_no("Enable Avalanche?", default=False):
            self.config['CHAIN_AVALANCHE'] = 'true'
            avax_rpc = ask_input(
                "Avalanche RPC URL",
                default="https://api.avax.network/ext/bc/C/rpc"
            )
            self.config['AVALANCHE_RPC_URL'] = avax_rpc or "https://api.avax.network/ext/bc/C/rpc"
        else:
            self.config['CHAIN_AVALANCHE'] = 'false'
    
    def _setup_trading_params(self):
        """Set up trading parameters."""
        print_section("STEP 4: Trading Parameters")
        
        print(f"""
Configure your trading limits and thresholds.

{Colors.YELLOW}Recommendations for beginners:{Colors.ENDC}
  • Start with LOW values to minimize risk
  • Increase gradually as you gain confidence
""")
        
        # Minimum profit
        print(f"\n{Colors.BOLD}Minimum Profit per Trade{Colors.ENDC}")
        print("Only execute trades with at least this much profit (in USD)")
        min_profit = ask_input("Minimum profit (USD)", default="2.00")
        try:
            self.config['MIN_PROFIT_USD'] = f"{float(min_profit):.2f}"
        except ValueError:
            self.config['MIN_PROFIT_USD'] = "2.00"
        
        # Maximum trade size
        print(f"\n{Colors.BOLD}Maximum Trade Size{Colors.ENDC}")
        print("Maximum amount to use per single trade (in USD)")
        print_warning("Start small! You can always increase later.")
        max_trade = ask_input("Maximum trade size (USD)", default="100")
        try:
            self.config['MAX_TRADE_USD'] = str(int(float(max_trade)))
        except ValueError:
            self.config['MAX_TRADE_USD'] = "100"
        
        # Maximum gas price
        print(f"\n{Colors.BOLD}Maximum Gas Price{Colors.ENDC}")
        print("Maximum gas price (Gwei) to pay for transactions")
        print("Higher = faster but more expensive, Lower = cheaper but might fail")
        max_gas = ask_input("Maximum gas (Gwei)", default="80")
        try:
            self.config['MAX_GAS_GWEI'] = str(int(float(max_gas)))
        except ValueError:
            self.config['MAX_GAS_GWEI'] = "80"
        
        # Slippage tolerance
        print(f"\n{Colors.BOLD}Slippage Tolerance{Colors.ENDC}")
        print("Maximum price slippage allowed (%)")
        print("0.5% is standard, increase to 1-2% if trades fail often")
        slippage = ask_input("Slippage tolerance (%)", default="0.5")
        try:
            self.config['SLIPPAGE_PERCENT'] = str(float(slippage))
        except ValueError:
            self.config['SLIPPAGE_PERCENT'] = "0.5"
        
        print_success(f"Trading parameters configured:")
        print(f"  • Min profit: ${self.config['MIN_PROFIT_USD']}")
        print(f"  • Max trade: ${self.config['MAX_TRADE_USD']}")
        print(f"  • Max gas: {self.config['MAX_GAS_GWEI']} Gwei")
        print(f"  • Slippage: {self.config['SLIPPAGE_PERCENT']}%")
    
    def _setup_strategies(self):
        """Set up farming strategies."""
        print_section("STEP 5: Farming Strategies")
        
        print(f"""
Choose which farming strategies to enable.

{Colors.CYAN}Available Strategies:{Colors.ENDC}

  {Colors.BOLD}1. DEX Arbitrage{Colors.ENDC}
     Buy low on one DEX, sell high on another
     Risk: Medium | Profit: Medium-High
     
  {Colors.BOLD}2. Yield Farming{Colors.ENDC}
     Provide liquidity to earn yield
     Risk: Low-Medium | Profit: Low-Medium
     
  {Colors.BOLD}3. Liquidity Mining{Colors.ENDC}
     Earn LP rewards by providing liquidity
     Risk: Medium | Profit: Medium
     
  {Colors.BOLD}4. Liquidation Farming{Colors.ENDC}
     Liquidate undercollateralized positions
     Risk: Medium-High | Profit: High
""")
        
        print("Enable strategies (you can enable multiple):\n")
        
        self.config['STRATEGY_ARBITRAGE'] = 'true' if ask_yes_no("Enable DEX Arbitrage?", default=True) else 'false'
        self.config['STRATEGY_YIELD_FARMING'] = 'true' if ask_yes_no("Enable Yield Farming?", default=True) else 'false'
        self.config['STRATEGY_LIQUIDITY_MINING'] = 'true' if ask_yes_no("Enable Liquidity Mining?", default=False) else 'false'
        self.config['STRATEGY_LIQUIDATION'] = 'true' if ask_yes_no("Enable Liquidation Farming?", default=False) else 'false'
        
        enabled = [k.replace('STRATEGY_', '') for k, v in self.config.items() if k.startswith('STRATEGY_') and v == 'true']
        print_success(f"Enabled strategies: {', '.join(enabled)}")
    
    def _setup_payouts(self):
        """Set up payout configuration."""
        print_section("STEP 6: Profit Payouts")
        
        print(f"""
Configure how you want to receive your profits.

{Colors.CYAN}Payout Options:{Colors.ENDC}
  1. Keep in trading wallet (default)
  2. Coinbase API (auto-sweep to Coinbase)
  3. Lightning/Cash App (Bitcoin)
  4. Separate wallet (on-chain transfer)
""")
        
        payout_method = ask_choice("Select payout method:", [
            "Keep profits in trading wallet",
            "Coinbase API - Auto-sweep to Coinbase account",
            "Lightning/Cash App - Bitcoin payouts",
            "Separate wallet - On-chain transfer"
        ], default=0)
        
        if payout_method == 0:
            print_info("Profits will accumulate in your trading wallet.")
        
        elif payout_method == 1:  # Coinbase
            print(f"\n{Colors.CYAN}Coinbase API Setup:{Colors.ENDC}")
            print("Create an API key at: https://www.coinbase.com/settings/api")
            print("Enable permissions: wallet:accounts:read, wallet:transactions:send")
            
            api_key = ask_input("Coinbase API Key")
            if api_key:
                self.config['COINBASE_API_KEY'] = api_key
                api_secret = ask_input("Coinbase API Secret", is_secret=True)
                if api_secret:
                    self.config['COINBASE_API_SECRET'] = api_secret
                account_id = ask_input("Coinbase Account ID (optional)")
                if account_id:
                    self.config['COINBASE_ACCOUNT_ID'] = account_id
        
        elif payout_method == 2:  # Lightning
            print(f"\n{Colors.CYAN}Lightning/Cash App Setup:{Colors.ENDC}")
            print("Enter your Lightning address (e.g., yourname@cashapp.com)")
            
            lightning_addr = ask_input("Lightning Address")
            if lightning_addr:
                self.config['LIGHTNING_ADDRESS'] = lightning_addr
            
            alby_key = ask_input("Alby/LNBITS API Key (optional)")
            if alby_key:
                self.config['ALBY_API_KEY'] = alby_key
        
        elif payout_method == 3:  # Separate wallet
            print(f"\n{Colors.CYAN}On-Chain Payout Setup:{Colors.ENDC}")
            
            payout_addr = ask_input("Payout wallet address (0x...)")
            if payout_addr and validate_wallet_address(payout_addr):
                self.config['PAYOUT_WALLET_ADDRESS'] = payout_addr
            
            chain_choice = ask_choice("Payout chain:", [
                "Ethereum", "Polygon (Low fees)", "BNB Smart Chain", "Arbitrum", "Base"
            ], default=1)
            chains = ['ethereum', 'polygon', 'bsc', 'arbitrum', 'base']
            self.config['PAYOUT_CHAIN'] = chains[chain_choice]
            
            token_choice = ask_choice("Payout token:", [
                "USDC (Stablecoin)", "USDT (Stablecoin)", "ETH", "WETH"
            ], default=0)
            tokens = ['USDC', 'USDT', 'ETH', 'WETH']
            self.config['PAYOUT_TOKEN'] = tokens[token_choice]
        
        # Payout threshold
        if payout_method > 0:
            threshold = ask_input("Payout threshold (USD) - sweep when profits reach this amount", default="10.00")
            try:
                self.config['PAYOUT_THRESHOLD_USD'] = f"{float(threshold):.2f}"
            except ValueError:
                self.config['PAYOUT_THRESHOLD_USD'] = "10.00"
    
    def _setup_optional_features(self):
        """Set up optional features."""
        print_section("STEP 7: Optional Features")
        
        print("Configure optional AI and voice features.\n")
        
        # OpenAI
        if ask_yes_no("Enable AI Chat (requires OpenAI API key)?", default=False):
            print("\nGet your API key at: https://platform.openai.com/api-keys")
            openai_key = ask_input("OpenAI API Key")
            if openai_key:
                self.config['OPENAI_API_KEY'] = openai_key
                model = ask_input("OpenAI Model", default="gpt-4o")
                self.config['OPENAI_MODEL'] = model
        
        # ElevenLabs
        if ask_yes_no("Enable Voice (requires ElevenLabs API key)?", default=False):
            print("\nGet your API key at: https://elevenlabs.io")
            eleven_key = ask_input("ElevenLabs API Key")
            if eleven_key:
                self.config['ELEVENLABS_API_KEY'] = eleven_key
        
        # Advanced MEV
        if ask_yes_no("Enable Flashbots (advanced MEV protection)?", default=False):
            print("\nGenerate a signing key:")
            print('  python -c "from eth_account import Account; a=Account.create(); print(a.key.hex())"')
            flashbots_key = ask_input("Flashbots signing key (different from wallet key)")
            if flashbots_key and validate_private_key(flashbots_key):
                self.config['FLASHBOTS_SIGNING_KEY'] = flashbots_key
    
    def _review_and_save(self):
        """Review configuration and save to .env file."""
        print_section("STEP 8: Review & Save")
        
        print(f"{Colors.BOLD}Configuration Summary:{Colors.ENDC}\n")
        
        # Display summary (hide secrets)
        print(f"  {Colors.CYAN}Trading Mode:{Colors.ENDC} {'SIMULATION' if self.config.get('DRY_RUN') == 'true' else 'LIVE'}")
        print(f"  {Colors.CYAN}Wallet:{Colors.ENDC} {self.config.get('WALLET_ADDRESS', 'Not set')[:10]}...{self.config.get('WALLET_ADDRESS', '')[-4:] if self.config.get('WALLET_ADDRESS') else ''}")
        
        # Chains
        chains = []
        if self.config.get('CHAIN_ETH') == 'true':
            chains.append('Ethereum')
        if self.config.get('CHAIN_POLYGON') == 'true':
            chains.append('Polygon')
        if self.config.get('CHAIN_BSC') == 'true':
            chains.append('BSC')
        if self.config.get('CHAIN_ARBITRUM') == 'true':
            chains.append('Arbitrum')
        if self.config.get('CHAIN_BASE') == 'true':
            chains.append('Base')
        if self.config.get('CHAIN_OPTIMISM') == 'true':
            chains.append('Optimism')
        if self.config.get('CHAIN_AVALANCHE') == 'true':
            chains.append('Avalanche')
        print(f"  {Colors.CYAN}Chains:{Colors.ENDC} {', '.join(chains) if chains else 'None'}")
        
        # Trading params
        print(f"  {Colors.CYAN}Min Profit:{Colors.ENDC} ${self.config.get('MIN_PROFIT_USD', '1.00')}")
        print(f"  {Colors.CYAN}Max Trade:{Colors.ENDC} ${self.config.get('MAX_TRADE_USD', '100')}")
        print(f"  {Colors.CYAN}Max Gas:{Colors.ENDC} {self.config.get('MAX_GAS_GWEI', '80')} Gwei")
        
        # Strategies
        strategies = []
        if self.config.get('STRATEGY_ARBITRAGE') == 'true':
            strategies.append('Arbitrage')
        if self.config.get('STRATEGY_YIELD_FARMING') == 'true':
            strategies.append('Yield')
        if self.config.get('STRATEGY_LIQUIDITY_MINING') == 'true':
            strategies.append('LP Mining')
        if self.config.get('STRATEGY_LIQUIDATION') == 'true':
            strategies.append('Liquidation')
        print(f"  {Colors.CYAN}Strategies:{Colors.ENDC} {', '.join(strategies) if strategies else 'None'}")
        
        print(f"\n{Colors.YELLOW}The configuration will be saved to: {self.env_path}{Colors.ENDC}")
        
        if self.env_path.exists():
            print_warning(f"A .env file already exists and will be OVERWRITTEN!")
        
        if not ask_yes_no("\nSave this configuration?", default=True):
            print("\nConfiguration NOT saved. Run the wizard again when ready.")
            return
        
        # Generate .env content
        env_content = self._generate_env_content()
        
        # Save to file
        try:
            with open(self.env_path, 'w') as f:
                f.write(env_content)
            print_success(f"Configuration saved to {self.env_path}")
        except IOError as e:
            print_error(f"Failed to save configuration: {e}")
            return
        
        # Final instructions
        print(f"""
{Colors.GREEN}{'═' * 60}
  🎉 SETUP COMPLETE! 🎉
{'═' * 60}{Colors.ENDC}

{Colors.BOLD}Next Steps:{Colors.ENDC}

  1. {'Start in simulation mode to test' if self.config.get('DRY_RUN') == 'true' else Colors.YELLOW + 'You are in LIVE mode - be careful!' + Colors.ENDC}
  
  2. Start the bot:
     {Colors.CYAN}python app.py{Colors.ENDC}
     
  3. Open the dashboard:
     {Colors.CYAN}http://localhost:5000{Colors.ENDC}
     (or port 10000 if deploying to Render)
     
  4. Monitor the bot and check for any errors
  
  5. {'When ready, change DRY_RUN=false in .env to go live' if self.config.get('DRY_RUN') == 'true' else 'Monitor your trades carefully!'}

{Colors.YELLOW}⚠️  Remember:{Colors.ENDC}
  • NEVER share your .env file or private key
  • The .env file is in .gitignore - keep it that way
  • Start with small amounts and increase gradually
  • Monitor your bot regularly

{Colors.CYAN}Need help? Check SETUP_GUIDE.md or ask in the GitHub issues.{Colors.ENDC}
""")
    
    def _generate_env_content(self) -> str:
        """Generate the .env file content."""
        lines = [
            "# ══════════════════════════════════════════════════════════════",
            "#  Nexus AI — Environment Configuration",
            "#  Generated by setup_wizard.py",
            "# ══════════════════════════════════════════════════════════════",
            "",
            "# ── Bot Behaviour ─────────────────────────────────────────────",
        ]
        
        # Bot behavior
        lines.append(f"DRY_RUN={self.config.get('DRY_RUN', 'true')}")
        lines.append(f"MIN_PROFIT_USD={self.config.get('MIN_PROFIT_USD', '1.00')}")
        lines.append(f"MAX_GAS_GWEI={self.config.get('MAX_GAS_GWEI', '80')}")
        lines.append(f"SLIPPAGE_PERCENT={self.config.get('SLIPPAGE_PERCENT', '0.5')}")
        lines.append(f"MAX_TRADE_USD={self.config.get('MAX_TRADE_USD', '100')}")
        lines.append("SCAN_INTERVAL_SECONDS=10")
        lines.append("")
        
        # Wallet
        lines.append("# ── Wallet ────────────────────────────────────────────────────")
        lines.append(f"WALLET_ADDRESS={self.config.get('WALLET_ADDRESS', '')}")
        lines.append(f"WALLET_PRIVATE_KEY={self.config.get('WALLET_PRIVATE_KEY', '')}")
        lines.append("")
        
        # Blockchain RPCs
        lines.append("# ── Blockchain RPCs ───────────────────────────────────────────")
        if self.config.get('ETH_RPC_URL'):
            lines.append(f"ETH_RPC_URL={self.config.get('ETH_RPC_URL')}")
        if self.config.get('BSC_RPC_URL'):
            lines.append(f"BSC_RPC_URL={self.config.get('BSC_RPC_URL')}")
        if self.config.get('POLYGON_RPC_URL'):
            lines.append(f"POLYGON_RPC_URL={self.config.get('POLYGON_RPC_URL')}")
        if self.config.get('ARBITRUM_RPC_URL'):
            lines.append(f"ARBITRUM_RPC_URL={self.config.get('ARBITRUM_RPC_URL')}")
        if self.config.get('BASE_RPC_URL'):
            lines.append(f"BASE_RPC_URL={self.config.get('BASE_RPC_URL')}")
        if self.config.get('OPTIMISM_RPC_URL'):
            lines.append(f"OPTIMISM_RPC_URL={self.config.get('OPTIMISM_RPC_URL')}")
        if self.config.get('AVALANCHE_RPC_URL'):
            lines.append(f"AVALANCHE_RPC_URL={self.config.get('AVALANCHE_RPC_URL')}")
        lines.append("")
        
        # Chain enables
        lines.append("# ── Enable/Disable Chains ─────────────────────────────────────")
        lines.append(f"CHAIN_ETH={self.config.get('CHAIN_ETH', 'false')}")
        lines.append(f"CHAIN_BSC={self.config.get('CHAIN_BSC', 'false')}")
        lines.append(f"CHAIN_POLYGON={self.config.get('CHAIN_POLYGON', 'false')}")
        lines.append(f"CHAIN_ARBITRUM={self.config.get('CHAIN_ARBITRUM', 'false')}")
        lines.append(f"CHAIN_BASE={self.config.get('CHAIN_BASE', 'false')}")
        lines.append(f"CHAIN_OPTIMISM={self.config.get('CHAIN_OPTIMISM', 'false')}")
        lines.append(f"CHAIN_AVALANCHE={self.config.get('CHAIN_AVALANCHE', 'false')}")
        lines.append("")
        
        # Strategies
        lines.append("# ── Strategies ────────────────────────────────────────────────")
        lines.append(f"STRATEGY_ARBITRAGE={self.config.get('STRATEGY_ARBITRAGE', 'true')}")
        lines.append(f"STRATEGY_YIELD_FARMING={self.config.get('STRATEGY_YIELD_FARMING', 'true')}")
        lines.append(f"STRATEGY_LIQUIDITY_MINING={self.config.get('STRATEGY_LIQUIDITY_MINING', 'false')}")
        lines.append(f"STRATEGY_LIQUIDATION={self.config.get('STRATEGY_LIQUIDATION', 'false')}")
        lines.append("")
        
        # MEV / Speed
        lines.append("# ── MEV / Speed ───────────────────────────────────────────────")
        if self.config.get('FLASHBOTS_SIGNING_KEY'):
            lines.append(f"FLASHBOTS_SIGNING_KEY={self.config.get('FLASHBOTS_SIGNING_KEY')}")
        else:
            lines.append("FLASHBOTS_SIGNING_KEY=")
        lines.append("BLOXROUTE_AUTH_HEADER=")
        lines.append("FLASH_CONTRACT_ETH=")
        lines.append("FLASH_CONTRACT_POLYGON=")
        lines.append("")
        
        # Payout - Coinbase
        lines.append("# ── Payout — Coinbase API ─────────────────────────────────────")
        lines.append(f"COINBASE_API_KEY={self.config.get('COINBASE_API_KEY', '')}")
        lines.append(f"COINBASE_API_SECRET={self.config.get('COINBASE_API_SECRET', '')}")
        lines.append(f"COINBASE_ACCOUNT_ID={self.config.get('COINBASE_ACCOUNT_ID', '')}")
        lines.append("")
        
        # Payout - Lightning
        lines.append("# ── Payout — Cash App / Lightning ───────────────────────────────")
        lines.append(f"LIGHTNING_ADDRESS={self.config.get('LIGHTNING_ADDRESS', '')}")
        lines.append(f"ALBY_API_KEY={self.config.get('ALBY_API_KEY', '')}")
        lines.append("")
        
        # Payout Settings
        lines.append("# ── Payout Settings ───────────────────────────────────────────")
        lines.append(f"PAYOUT_THRESHOLD_USD={self.config.get('PAYOUT_THRESHOLD_USD', '10.00')}")
        lines.append(f"PAYOUT_CHAIN={self.config.get('PAYOUT_CHAIN', 'ethereum')}")
        lines.append(f"PAYOUT_TOKEN={self.config.get('PAYOUT_TOKEN', 'USDC')}")
        lines.append(f"PAYOUT_WALLET_ADDRESS={self.config.get('PAYOUT_WALLET_ADDRESS', '')}")
        lines.append("")
        
        # Prices
        lines.append("# ── Prices ────────────────────────────────────────────────────")
        lines.append("COINGECKO_API_KEY=")
        lines.append("")
        
        # OpenAI
        lines.append("# ── OpenAI / Nexus Chat ───────────────────────────────────────")
        lines.append(f"OPENAI_API_KEY={self.config.get('OPENAI_API_KEY', '')}")
        lines.append(f"OPENAI_MODEL={self.config.get('OPENAI_MODEL', 'gpt-4o')}")
        lines.append("OPENAI_BASE_URL=")
        lines.append("")
        
        # Voice
        lines.append("# ── Voice / TTS ───────────────────────────────────────────────")
        lines.append(f"ELEVENLABS_API_KEY={self.config.get('ELEVENLABS_API_KEY', '')}")
        lines.append("ELEVENLABS_VOICE_ID=")
        lines.append("VOICE_WAKE_WORD=nexus")
        lines.append("")
        
        # Infrastructure
        lines.append("# ── Infrastructure ────────────────────────────────────────────")
        lines.append("REDIS_URL=redis://localhost:6379/0")
        lines.append("PORT=5000")
        lines.append("")
        
        # Logging
        lines.append("# ── Logging ───────────────────────────────────────────────────")
        lines.append("LOG_LEVEL=INFO")
        lines.append("")
        
        return '\n'.join(lines)


def main():
    """Main entry point."""
    try:
        wizard = SetupWizard()
        wizard.run()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Setup cancelled by user. Goodbye! 👋{Colors.ENDC}")
        sys.exit(0)
    except Exception as e:
        print(f"\n{Colors.RED}An error occurred: {e}{Colors.ENDC}")
        sys.exit(1)


if __name__ == "__main__":
    main()
