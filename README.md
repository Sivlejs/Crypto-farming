# ⬡ Nexus AI – Autonomous Crypto Farmer

> A fully autonomous, self-learning DeFi farming AI that finds, executes, and earns from crypto opportunities across **7 blockchains** using **13 trading strategies** — with real-time voice chat, automatic profit payouts, and a live dashboard. Deploys in one click on [Render](https://render.com).

---

## �� Features at a Glance

### 13 Farming Strategies
| Strategy | Type | Description |
|---|---|---|
| **DEX Arbitrage** | ⚡ Instant | Price gaps between Uniswap/SushiSwap/PancakeSwap |
| **Flash Arbitrage** | ⚡ Instant | Capital-free arb using Aave V3 flash loans (zero capital required) |
| **Triangular Arbitrage** | ⚡ Instant | A→B→C→A single-DEX arbitrage |
| **Cross-Chain Arbitrage** | 🔶 Normal | Same token on different chains (Ethereum vs Arbitrum vs Polygon) |
| **Stablecoin Arbitrage** | ⚡ Instant | USDC/USDT/DAI de-peg opportunities |
| **Liquidation Bot** | ⚡ Instant | Earn liquidation bonuses on Aave/Compound undercollateralized positions |
| **Yield Farming** | ✅ Deferred | Auto-deposit into highest-APY DeFi Llama pools |
| **Liquidity Mining** | ✅ Deferred | LP position management for reward token accumulation |
| **Staking** | ✅ Deferred | Lido/Rocket Pool liquid staking (stETH, rETH) |
| **Lending Rate Optimizer** | ✅ Deferred | Automatically move capital to highest lending rate |
| **Perpetuals Funding Rate** | 🔶 Normal | Earn funding rates on GMX/dYdX when positive |
| **Vault Optimizer** | ✅ Deferred | Yearn/Beefy/Convex best-APY auto-compounder |
| **Governance Farming** | ✅ Deferred | CRV/CVX/veToken lock-and-earn strategies |

### 7 Supported Blockchains
Ethereum · BNB Smart Chain · Polygon · **Arbitrum** · **Optimism** · **Base** · **Avalanche**

### Speed & MEV Protection
- **Flashbots private bundles** — transactions never appear in the public mempool; no front-running
- **EIP-1559 dynamic gas** — always pays the minimum required
- **Multicall3 batch reads** — 10-50× faster opportunity scanning
- **Block-triggered scanner** — reacts to every new block instantly
- **Trade scheduler & gas oracle** — defers non-urgent trades to cheap gas windows
- **4-endpoint RPC failover** per chain — zero downtime if one node goes down

### 🧠 Self-Learning AI
- **RandomForest ML model** — scores every opportunity with a success probability
- **Bayesian parameter optimizer** — auto-tunes `min_profit_usd`, `slippage`, `max_trade_usd`, etc.
- **Market regime classifier** — detects trending/volatile/calm conditions and shifts strategy weights
- **Trade memory database** — every trade outcome is recorded and used to improve future decisions
- Activates automatically after 30 executed trades

### 💸 Automatic Profit Payouts
Profits sweep automatically to your accounts when the threshold is reached:
- **Coinbase** (via Coinbase API — USD or crypto)
- **Cash App Bitcoin / Lightning** (via lightning address or LNURL)
- **Any EVM wallet** (on-chain transfer: USDC, ETH, BNB, MATIC, AVAX)

### 🎙️ Voice & Chat Interface
- **Talk to Nexus** — ask questions, start/stop the bot, request payouts, all via voice or text
- **Browser-native speech recognition** — no API key needed (Chrome/Edge)
- **ElevenLabs neural TTS** — Nexus talks back in a premium voice (optional; falls back to browser TTS)
- **GPT-4o intelligence** — rich, accurate answers about live bot data (optional; falls back to rule-based engine)
- **Floating chat panel** — always accessible on every page of the dashboard

### 📊 Live Dashboard (8 Tabs)
1. **Overview** — KPIs, chain connections, live opportunity ticker, profit chart
2. **Strategies** — all 13 strategy cards with status and metrics
3. **Trades** — full trade history with chain/type filters
4. **Payout** — pending balance, progress bar, payout history, destination config
5. **Markets** — live token prices, DeFi yield table
6. **🧠 AI Brain** — ML model status, win rate, market regime, adaptive parameters
7. **⏱ Timing** — gas oracle stats, trade scheduler queue, cheapest gas hours
8. **Settings** — live config from environment variables

---

## ⚡ Quick Start (Local)

### Option 1: Interactive Setup Wizard (Recommended for Beginners)
```bash
git clone https://github.com/Sivlejs/Crypto-farming.git
cd Crypto-farming
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the interactive setup wizard - it will guide you through everything!
python setup_wizard.py

# After setup is complete, start the bot
python app.py
# Open http://localhost:5000
```

The **Setup Wizard** walks you through:
- ✅ Wallet configuration (address & private key)
- ✅ Blockchain RPC connections (Ethereum, Polygon, BSC, L2s)
- ✅ Trading parameters (profit thresholds, gas limits, slippage)
- ✅ Strategy selection (arbitrage, yield farming, liquidations, etc.)
- ✅ Payout configuration (Coinbase, Lightning/Cash App, on-chain)
- ✅ Optional AI features (GPT-4o chat, ElevenLabs voice)

### Option 2: Manual Configuration
```bash
git clone https://github.com/Sivlejs/Crypto-farming.git
cd Crypto-farming
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add your wallet address/key and RPC URLs at minimum
python app.py
# Open http://localhost:5000
```

📖 **See [SETUP_GUIDE.md](SETUP_GUIDE.md) for complete step-by-step instructions.**

---

## 🌐 Deploy to Render (Recommended)

1. Fork this repo to your GitHub account
2. Go to [render.com](https://render.com) → **New** → **Blueprint** → connect your fork
3. Render auto-detects `render.yaml` and creates a **web service** + **background worker**
4. Set environment variables in the Render dashboard (see below)
5. Click **Deploy** — your dashboard will be live in ~2 minutes

---

## ⚙️ Environment Variables

Copy `.env.example` to `.env` and fill in your values. Critical variables:

### Required (Bot Won't Trade Without These)
| Variable | Description |
|---|---|
| `WALLET_ADDRESS` | Your EVM wallet address (0x…) |
| `WALLET_PRIVATE_KEY` | Wallet private key — **keep secret**, never commit to git |
| `ETH_RPC_URL` | Ethereum RPC (Alchemy/Infura — `https://mainnet.infura.io/v3/YOUR_KEY`) |

### Bot Behaviour
| Variable | Default | Description |
|---|---|---|
| `DRY_RUN` | `true` | `true` = simulate only. Set `false` to go **LIVE** |
| `MIN_PROFIT_USD` | `2.00` | Minimum estimated profit to execute a trade |
| `MAX_GAS_GWEI` | `80` | Never pay more than this gas price |
| `MAX_TRADE_USD` | `10000` | Max USD per single trade |
| `SCAN_INTERVAL_SECONDS` | `10` | Fallback scan interval (block-triggered is faster) |

### Payout — Coinbase
| Variable | Description |
|---|---|
| `COINBASE_API_KEY` | Coinbase API key (from coinbase.com/settings/api) |
| `COINBASE_API_SECRET` | Coinbase API secret |
| `COINBASE_ACCOUNT_ID` | Your Coinbase USD or BTC account UUID (auto-detected if blank) |

### Payout — Cash App / Lightning Bitcoin
| Variable | Description |
|---|---|
| `LIGHTNING_ADDRESS` | Your Cash App Lightning address (e.g. `yourname@cashapp.com`) |
| `ALBY_API_KEY` | Alby or LNbits API key for programmatic Lightning sends |
| `PAYOUT_WALLET_ADDRESS` | On-chain EVM address as fallback |

### Payout Settings
| Variable | Default | Description |
|---|---|---|
| `PAYOUT_THRESHOLD_USD` | `10.00` | Auto-sweep when pending profits hit this amount |
| `PAYOUT_CHAIN` | `ethereum` | Chain to collect profits on |
| `PAYOUT_TOKEN` | `USDC` | Token to accumulate (USDC, USDT, WETH, etc.) |

### Voice & AI Chat
| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Enables GPT-4o intelligent responses (optional — rule-based fallback works without it) |
| `OPENAI_MODEL` | Model to use (default: `gpt-4o`) |
| `ELEVENLABS_API_KEY` | Neural TTS for Nexus voice (optional — browser TTS fallback) |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice ID (default: "Rachel") |

### Additional Chains (Enable L2s)
| Variable | Default | Description |
|---|---|---|
| `CHAIN_ARBITRUM` | `false` | Enable Arbitrum One |
| `CHAIN_OPTIMISM` | `false` | Enable Optimism |
| `CHAIN_BASE` | `false` | Enable Base |
| `CHAIN_AVALANCHE` | `false` | Enable Avalanche C-Chain |

### MEV / Speed
| Variable | Description |
|---|---|
| `FLASHBOTS_SIGNING_KEY` | Separate key for Flashbots bundle signing (NOT your wallet key) |
| `FLASH_CONTRACT_ETH` | Deployed `FlashArbitrage.sol` address on Ethereum |
| `FLASH_CONTRACT_POLYGON` | Deployed `FlashArbitrage.sol` address on Polygon |

---

## 🔐 Security

- **Never commit** `WALLET_PRIVATE_KEY` or any API keys to git — use environment variables only
- Use `DRY_RUN=true` until you've verified the bot is operating correctly
- Set `MAX_TRADE_USD` to a safe limit while testing
- The `FlashArbitrage.sol` contract has an owner-only withdraw function — deploy with your wallet
- Use a **dedicated trading wallet** with only the capital you're willing to risk

---

## 🏗️ Architecture

```
app.py (Flask + SocketIO)
│
├── nexus/agent.py          ← Main orchestrator
├── nexus/monitor.py        ← Block-triggered opportunity scanner
├── nexus/executor.py       ← Transaction execution (EIP-1559, Flashbots)
├── nexus/rewards.py        ← Trade outcome tracking
├── nexus/payout.py         ← Automatic profit distribution
│
├── nexus/blockchain.py     ← Multi-chain Web3 manager (7 chains, 4 RPC fallbacks each)
├── nexus/feeds/            ← Real-time price feeds (CoinGecko WebSocket)
│
├── nexus/strategies/       ← 13 farming strategies
├── nexus/protocols/        ← Uniswap V2/V3, Aave V3, Flash loans, DEX aggregator
│
├── nexus/execution/        ← NonceManager, Multicall3, Flashbots bundler
├── nexus/timing/           ← Gas oracle, trade scheduler (cheapest-window timing)
│
├── nexus/learning/         ← ML brain (RandomForest, Bayesian optimizer, market classifier)
│
├── nexus/chat/             ← NexusChat (GPT-4o + rule-based fallback)
├── nexus/voice/            ← ElevenLabs TTS + browser speech recognition
│
├── contracts/              ← FlashArbitrage.sol (Aave V3 flash loan arbitrage)
│
├── templates/dashboard.html ← 8-tab live dashboard
├── static/                  ← CSS + JavaScript
│
├── render.yaml              ← Render deployment (web + worker + Redis)
├── docker-compose.yml       ← Local Docker stack
└── gunicorn.conf.py         ← Production server config
```

---

## 🎤 Talking to Nexus

Click the **💬** button (bottom-right of dashboard) to open Nexus Chat. Type or click 🎤 to speak:

| Command | Example |
|---|---|
| Status | *"Give me a status update"* |
| Profits | *"What's my total profit?"* |
| Opportunities | *"What are you seeing right now?"* |
| Payout | *"Sweep my profits to Coinbase"* |
| Market | *"What's the market doing?"* |
| AI brain | *"How's your ML model doing?"* |
| Start / Stop | *"Start trading"* / *"Stop the bot"* |
| Switch mode | *"Switch to simulation mode"* / *"Go live"* |
| Help | *"What can you do?"* |

---

## 📜 License

MIT — use freely, trade responsibly.

> ⚠️ **Disclaimer**: Crypto trading involves significant financial risk. This software is provided as-is without any warranty. Always use `DRY_RUN=true` first. Never trade with funds you cannot afford to lose.
