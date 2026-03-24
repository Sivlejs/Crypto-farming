# ⬡ Nexus AI – Crypto Farmer

> Autonomous DeFi farming agent that finds high-yield opportunities across Ethereum, BNB Smart Chain, and Polygon, then executes them to earn rewards — deployed on [Render](https://render.com).

---

## Features

| Strategy | What it does |
|---|---|
| **DEX Arbitrage** | Detects price discrepancies between Uniswap, SushiSwap, PancakeSwap, QuickSwap and executes profitable swap sequences |
| **Yield Farming** | Monitors DeFi Llama for the highest-APY lending/farming pools and recommends (or auto-supplies to) Aave |
| **Liquidity Mining** | Finds high-reward LP positions on major DEXes, favoring low-impermanent-loss pairs |

Additional highlights:
- **Real-time dashboard** – live opportunities, trades, chain status, yield table
- **Safety-first** – gas price limits, slippage protection, wallet balance checks, dry-run mode
- **Multi-chain** – Ethereum, BSC, Polygon (extendable)
- **One-click Render deploy** – `render.yaml` included

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/Sivlejs/Crypto-farming.git
cd Crypto-farming
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and fill in:
#   WALLET_ADDRESS, WALLET_PRIVATE_KEY
#   ETH_RPC_URL, BSC_RPC_URL, POLYGON_RPC_URL
#   Leave DRY_RUN=true until you're confident
```

### 3. Run locally

```bash
python app.py
# Open http://localhost:5000
```

---

## Deploy to Render

1. Fork / push this repo to GitHub
2. Go to [render.com](https://render.com) → **New Web Service** → connect your repo
3. Render will auto-detect `render.yaml`
4. In the Render dashboard, add these **Environment Variables**:
   - `WALLET_ADDRESS`
   - `WALLET_PRIVATE_KEY` *(mark as secret)*
   - `ETH_RPC_URL` (e.g. from [Infura](https://infura.io) or [Alchemy](https://alchemy.com))
   - `BSC_RPC_URL`
   - `POLYGON_RPC_URL`
   - `DRY_RUN=false` *(only when you're ready for real trades)*
5. Deploy!

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `WALLET_ADDRESS` | – | Your public wallet address |
| `WALLET_PRIVATE_KEY` | – | Private key (keep secret!) |
| `ETH_RPC_URL` | llamarpc | Ethereum JSON-RPC endpoint |
| `BSC_RPC_URL` | binance | BSC JSON-RPC endpoint |
| `POLYGON_RPC_URL` | polygon-rpc | Polygon JSON-RPC endpoint |
| `MIN_PROFIT_USD` | 5.0 | Minimum estimated profit before executing |
| `MAX_GAS_GWEI` | 50 | Maximum gas price (protects against gas spikes) |
| `SLIPPAGE_PERCENT` | 0.5 | Allowed slippage per swap |
| `MAX_TRADE_USD` | 500 | Maximum USD value per trade |
| `DRY_RUN` | true | Simulate without sending real transactions |
| `STRATEGY_ARBITRAGE` | true | Enable/disable DEX arbitrage |
| `STRATEGY_YIELD_FARMING` | true | Enable/disable yield farming scanner |
| `STRATEGY_LIQUIDITY_MINING` | true | Enable/disable LP mining scanner |
| `SCAN_INTERVAL_SECONDS` | 15 | How often to scan for opportunities |

---

## Architecture

```
app.py                    Flask + Socket.IO web server
nexus/
  agent.py                Main orchestrator (NexusAgent)
  blockchain.py           Web3 connections (ETH / BSC / Polygon)
  monitor.py              Opportunity scanner (runs strategies)
  executor.py             Transaction builder & signer
  rewards.py              SQLite trade history & stats
  strategies/
    arbitrage.py          Cross-DEX arbitrage
    yield_farming.py      DeFi Llama yield scanner
    liquidity_mining.py   LP reward scanner
  protocols/
    uniswap.py            Uniswap V2-compatible DEX client
    aave.py               Aave V3 client
    dex_aggregator.py     CoinGecko + DeFi Llama price feeds
  utils/
    config.py             Environment variable config
    logger.py             Logging
templates/
  dashboard.html          Real-time web UI
static/
  css/dashboard.css
  js/dashboard.js
render.yaml               Render deployment config
Dockerfile                Docker container
```

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Web dashboard |
| `GET /health` | Health check |
| `GET /api/status` | Full agent status JSON |
| `GET /api/opportunities` | Recent opportunities |
| `GET /api/trades` | Trade history |
| `GET /api/prices` | Current token prices |
| `GET /api/yields` | Top DeFi yield rates |
| `GET /api/config` | Current configuration |

---

## ⚠️ Risk Disclaimer

- DeFi trading involves **significant financial risk**. You can lose money.
- Always start with `DRY_RUN=true` and monitor the bot's behaviour before enabling real trades.
- Never commit or share your `WALLET_PRIVATE_KEY`.
- Gas costs, slippage, and smart contract risks are not fully mitigated by this software.
- This software is provided as-is with no warranty.

