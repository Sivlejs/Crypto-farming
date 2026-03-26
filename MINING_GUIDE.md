# 🔨 Complete PoW Mining Guide: Go Live with Nexus AI

This guide provides **step-by-step instructions** for setting up and running cryptocurrency mining with Nexus AI's advanced AI-powered mining system.

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Hardware Requirements](#hardware-requirements)
3. [Supported Algorithms & Coins](#supported-algorithms--coins)
4. [Quick Start](#quick-start)
5. [Detailed Setup](#detailed-setup)
6. [Mining Pool Configuration](#mining-pool-configuration)
7. [Dashboard Pool Selection](#dashboard-pool-selection)
8. [AI Auto-Mining](#ai-auto-mining)
9. [Wallet Setup](#wallet-setup)
10. [Monitoring & Optimization](#monitoring--optimization)
11. [Cloud Mining Setup](#cloud-mining-setup)
12. [Troubleshooting](#troubleshooting)

---

## Overview

Nexus AI's PoW (Proof of Work) mining system provides:

- **AI-Powered Optimization**: Automatic tuning of mining parameters
- **Multi-Algorithm Support**: SHA256, Scrypt, Ethash, KawPow, RandomX, and more
- **Pool Discovery**: Automatic discovery and ranking of mining pools
- **Profit Switching**: AI-controlled switching to most profitable coins
- **Dashboard Control**: Start/stop mining from the web UI
- **Auto-Mining Mode**: Let the AI decide when and what to mine

---

## Hardware Requirements

### Minimum Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 2 GB | 8+ GB |
| Storage | 1 GB | 10 GB |
| Network | 1 Mbps | 10+ Mbps |

### For GPU Mining (Optional)

| GPU Type | VRAM | Best For |
|----------|------|----------|
| NVIDIA RTX 3060+ | 8+ GB | Ethash, KawPow, Autolykos |
| AMD RX 6600+ | 8+ GB | Ethash, KawPow |
| Any Modern GPU | 4+ GB | Most algorithms |

### For CPU Mining

| CPU Type | Best Algorithm |
|----------|----------------|
| Modern AMD Ryzen | RandomX (Monero) |
| Intel Core i5/i7/i9 | RandomX (Monero) |
| Server CPUs | RandomX, Cryptonight |

---

## Supported Algorithms & Coins

| Algorithm | Coins | Hardware |
|-----------|-------|----------|
| **SHA256** | Bitcoin (BTC), Bitcoin Cash (BCH) | ASIC only |
| **Scrypt** | Litecoin (LTC), Dogecoin (DOGE) | ASIC recommended |
| **Ethash/Etchash** | Ethereum Classic (ETC) | GPU |
| **KawPow** | Ravencoin (RVN) | GPU |
| **RandomX** | Monero (XMR) | CPU |
| **Autolykos** | Ergo (ERG) | GPU |
| **KHeavyHash** | Kaspa (KAS) | GPU |
| **Blake3** | Alephium (ALPH) | GPU |
| **Equihash** | Flux (FLUX) | GPU |

---

## Quick Start

### Option 1: Use the Dashboard (Recommended)

1. **Open the Dashboard**: Navigate to your Nexus AI web interface
2. **Go to Mining Tab**: Click on "Mining" in the sidebar
3. **View Available Pools**: See the list of discovered mining pools
4. **Select a Pool**: Click "Select" on your preferred pool
5. **Enter Wallet Address**: Input your wallet address for payouts
6. **Click Start Mining**: The AI will begin mining automatically

### Option 2: AI Auto-Mining

1. **Open the Dashboard**
2. **Go to Mining Settings**
3. **Enable "AI Auto-Mine"**
4. **Set Minimum Profitability** (e.g., $0.10/day)
5. **Save Settings**

The AI will automatically:
- Discover and evaluate mining pools
- Select the most profitable pool
- Start mining when profitable
- Switch coins as profitability changes

### Option 3: Command Line

```bash
# Navigate to the project
cd Crypto-farming

# Run the mining wizard
python mining_wizard.py

# Follow the interactive prompts
```

---

## Detailed Setup

### Step 1: Environment Configuration

Create or edit your `.env` file with mining settings:

```bash
# ═══════════════════════════════════════════════════════════════
# MINING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Enable mining strategy
ENABLE_POW_MINING=true

# Your wallet address for mining payouts
MINING_WALLET_ADDRESS=0xYourWalletAddressHere

# Mining algorithm (sha256, scrypt, ethash, etchash, kawpow, randomx, autolykos)
MINING_ALGORITHM=randomx

# Pool URL (will be auto-selected if not set)
MINING_POOL_URL=stratum+tcp://xmr.2miners.com:2222

# Pool username (usually your wallet address)
MINING_POOL_USER=YourWalletAddress

# Pool password (usually 'x' or worker name)
MINING_POOL_PASS=x

# Mining intensity (1-100, higher = more CPU/GPU usage)
MINING_INTENSITY=75

# Maximum CPU percentage to use
MINING_MAX_CPU_PERCENT=80

# Electricity cost (for profitability calculations)
MINING_ELECTRICITY_COST_KWH=0.10
```

### Step 2: Choose Your Mining Hardware

#### For CPU Mining (Easiest Start)

Best for: **Monero (XMR)** using RandomX algorithm

```bash
MINING_ALGORITHM=randomx
MINING_POOL_URL=stratum+tcp://xmr.2miners.com:2222
```

#### For GPU Mining

Best for: **Ethereum Classic (ETC)**, **Ravencoin (RVN)**, **Ergo (ERG)**

```bash
# For Ethereum Classic
MINING_ALGORITHM=etchash
MINING_POOL_URL=stratum+tcp://etc.2miners.com:1010

# For Ravencoin
MINING_ALGORITHM=kawpow
MINING_POOL_URL=stratum+tcp://rvn.2miners.com:6060

# For Ergo
MINING_ALGORITHM=autolykos
MINING_POOL_URL=stratum+tcp://erg.2miners.com:8888
```

### Step 3: Start Mining

#### Via Dashboard

1. Navigate to Mining → Pools
2. Select your pool
3. Click "Start Mining"

#### Via API

```bash
# Start mining with a specific pool
curl -X POST http://localhost:5000/api/mining/start-with-pool \
  -H "Content-Type: application/json" \
  -d '{
    "pool_id": "abc123def456",
    "wallet_address": "YourWalletAddress"
  }'
```

#### Via Python

```python
from nexus.strategies.pow_mining import PoWMiningStrategy

strategy = PoWMiningStrategy()
strategy.start_mining()
```

---

## Mining Pool Configuration

### Viewing Available Pools

The dashboard shows all discovered pools with:

- **Pool Name**: The mining pool's name
- **Coin**: What cryptocurrency you'll mine
- **Algorithm**: Mining algorithm used
- **Fee**: Pool fee percentage
- **Estimated Daily USD**: Estimated earnings based on your hashrate
- **Status**: Online/Offline/Unknown
- **Latency**: Network latency to pool
- **Score**: AI-calculated overall score

### Adding a Custom Pool

```bash
curl -X POST http://localhost:5000/api/mining/pools/add \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Custom Pool",
    "url": "stratum+tcp://mypool.com:3333",
    "algorithm": "randomx",
    "coin": "XMR",
    "coin_name": "Monero",
    "fee_percent": 1.0
  }'
```

### Pool Selection Strategies

| Strategy | Best For |
|----------|----------|
| **Lowest Fee** | Long-term mining |
| **Highest Profitability** | Maximum earnings |
| **Lowest Latency** | Stable hashrate |
| **AI Recommended** | Balanced approach |

---

## Dashboard Pool Selection

### Step-by-Step Guide

1. **Open Dashboard**: Go to your Nexus AI web interface

2. **Navigate to Mining**: Click "Mining" in the sidebar menu

3. **View Pools by Algorithm**: 
   - Pools are grouped by algorithm (RandomX, KawPow, etc.)
   - Each pool shows: name, coin, fee, estimated profit, score

4. **Filter Pools**:
   - By Algorithm: SHA256, Scrypt, RandomX, etc.
   - By Coin: XMR, RVN, ETC, etc.
   - By Status: Online only

5. **Select a Pool**:
   - Click the "Select" button on your chosen pool
   - Or click "AI Select" to let the AI choose

6. **Configure Mining**:
   - Enter your wallet address
   - Set mining intensity (1-100)
   - Choose CPU or GPU mining

7. **Start Mining**:
   - Click "Start Mining"
   - Monitor hashrate and earnings on the dashboard

### Reading Pool Data

```
┌─────────────────────────────────────────────────────────────┐
│ 2Miners XMR                                    Score: 0.85  │
├─────────────────────────────────────────────────────────────┤
│ Coin: XMR (Monero)                                          │
│ Algorithm: RandomX                                          │
│ Fee: 1.0%                                                   │
│ Status: ● Online (45ms latency)                             │
│ Estimated: $0.42/day @ 1 KH/s                               │
│ Min Payout: 0.01 XMR                                        │
├─────────────────────────────────────────────────────────────┤
│ [Select Pool]  [View Details]  [Test Connection]            │
└─────────────────────────────────────────────────────────────┘
```

---

## AI Auto-Mining

### How It Works

1. **Pool Discovery**: AI continuously scans and evaluates mining pools
2. **Profitability Analysis**: AI calculates expected earnings for each pool
3. **Automatic Selection**: AI selects the most profitable pool
4. **Auto-Start**: Mining starts automatically when profitable
5. **Profit Switching**: AI switches pools when a better option is found

### Enabling AI Auto-Mining

#### Via Dashboard

1. Go to Mining → Settings
2. Toggle "AI Auto-Mining" ON
3. Set minimum profitability threshold
4. Save settings

#### Via API

```bash
curl -X POST http://localhost:5000/api/mining/auto-mine \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "min_profitability_usd": 0.10,
    "algorithms": ["randomx", "kawpow", "autolykos"]
  }'
```

### AI Decision Factors

The AI considers:

| Factor | Weight | Description |
|--------|--------|-------------|
| **Profitability** | 60% | Estimated USD earnings |
| **Reliability** | 30% | Pool uptime & latency |
| **Fees** | 10% | Pool fee percentage |

### AI Mining Modes

| Mode | Description |
|------|-------------|
| **Conservative** | Only mines when highly profitable, lower risk |
| **Balanced** | Default mode, good balance of risk/reward |
| **Aggressive** | Mines whenever marginally profitable |

---

## Wallet Setup

### Recommended Wallets by Coin

| Coin | Recommended Wallet |
|------|-------------------|
| XMR (Monero) | [Monero GUI](https://getmonero.org/downloads/), [Cake Wallet](https://cakewallet.com/) |
| RVN (Ravencoin) | [Ravencoin Wallet](https://ravencoin.org/wallet/), [Exodus](https://exodus.com/) |
| ETC (Ethereum Classic) | [MetaMask](https://metamask.io/), [Trust Wallet](https://trustwallet.com/) |
| ERG (Ergo) | [Nautilus](https://nautiluswallet.io/), [SAFEW](https://ergoplatform.org/en/get-erg/) |
| KAS (Kaspa) | [Kaspa Wallet](https://kaspa.org/), [Tangem](https://tangem.com/) |

### Getting Your Wallet Address

1. Open your wallet application
2. Navigate to "Receive" or "Deposit"
3. Copy your wallet address
4. Paste it in the Nexus AI mining configuration

### Important Wallet Tips

⚠️ **Security Best Practices**:
- Never share your private keys
- Use a dedicated mining wallet
- Enable 2FA where available
- Regularly backup your wallet

---

## Monitoring & Optimization

### Dashboard Metrics

| Metric | Description |
|--------|-------------|
| **Hashrate** | Your current mining speed |
| **Shares** | Accepted/Rejected shares |
| **Estimated Earnings** | Projected daily/monthly earnings |
| **Pool Status** | Connection status to mining pool |
| **Temperature** | GPU/CPU temperature (if available) |

### AI Optimization Features

The Enhanced AI Mining Optimizer (v2) provides:

- **Deep Q-Network (DQN)**: Advanced decision making
- **Ensemble Learning**: Combines multiple AI models
- **Anomaly Detection**: Monitors hardware health
- **Automatic Tuning**: Optimizes intensity, power limits
- **Profit Forecasting**: Predicts optimal mining times

### Viewing AI Stats

```bash
curl http://localhost:5000/api/mining/ai/stats
```

Response:
```json
{
  "ok": true,
  "version": "v2_enhanced",
  "total_optimizations": 1234,
  "successful_optimizations": 1100,
  "success_rate_percent": 89.17,
  "ensemble_stats": {
    "model_weights": {
      "nn": 0.25,
      "dqn": 0.35,
      "transformer": 0.25,
      "basic_rl": 0.15
    }
  },
  "hardware_health_scores": {
    "gpu_0": 95.5
  }
}
```

---

## Cloud Mining Setup

### Supported Cloud Providers

| Provider | Best For | Notes |
|----------|----------|-------|
| **Render** | Easy deployment | Free tier available |
| **Railway** | Auto-scaling | Pay-per-use |
| **AWS EC2** | GPU instances | Higher cost |
| **Google Cloud** | GPU instances | Free credits available |

### Render Deployment

1. **Fork the Repository**
2. **Connect to Render**
3. **Add Environment Variables**:

```
ENABLE_POW_MINING=true
MINING_WALLET_ADDRESS=YourAddress
MINING_ALGORITHM=randomx
MINING_INTENSITY=70
MINING_MAX_CPU_PERCENT=75
```

4. **Deploy**

### Cloud Mining Tips

- ⚠️ Check provider terms of service
- Start with low intensity (50-70%)
- Monitor CPU usage to avoid throttling
- Use CPU mining (RandomX) for best results on cloud

---

## Troubleshooting

### Common Issues

#### "Pool connection failed"

```bash
# Test pool connectivity
curl -X POST http://localhost:5000/api/mining/pools/test \
  -H "Content-Type: application/json" \
  -d '{"pool_url": "stratum+tcp://xmr.2miners.com:2222"}'
```

Possible causes:
- Pool URL incorrect
- Firewall blocking connection
- Pool temporarily offline

#### "Low hashrate"

Possible causes:
- Intensity too low (increase in settings)
- High CPU usage from other processes
- Thermal throttling (check temperatures)

#### "High rejection rate"

Possible causes:
- Network latency issues (try closer pool)
- Intensity too high
- Incorrect algorithm selected

#### "No pools found"

Possible causes:
- Network connectivity issues
- Pool discovery service not started

Solution:
```bash
# Refresh pool discovery
curl -X POST http://localhost:5000/api/mining/pools/refresh
```

### Logs

Check logs for detailed error information:

```bash
# View mining logs
tail -f logs/nexus.log | grep -i mining
```

### Getting Help

1. Check the [GitHub Issues](https://github.com/Sivlejs/Crypto-farming/issues)
2. Review the FAQ in SETUP_GUIDE.md
3. Open a new issue with:
   - Error message
   - Mining configuration
   - Hardware specs

---

## API Reference

### Mining Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/mining/status` | GET | Get mining status |
| `/api/mining/start` | POST | Start mining |
| `/api/mining/stop` | POST | Stop mining |
| `/api/mining/pools/discover` | GET | Get all pools |
| `/api/mining/pools/select` | POST | Select a pool |
| `/api/mining/auto-mine` | POST | Enable/disable auto-mining |
| `/api/mining/ai/stats` | GET | Get AI optimizer stats |
| `/api/mining/start-with-pool` | POST | Start with specific pool |

### Example Workflows

#### Full Setup Flow

```bash
# 1. Discover pools
curl http://localhost:5000/api/mining/pools/discover

# 2. Select best pool
curl -X POST http://localhost:5000/api/mining/pools/select \
  -d '{"pool_id": "abc123"}'

# 3. Start mining
curl -X POST http://localhost:5000/api/mining/start-with-pool \
  -d '{"pool_id": "abc123", "wallet_address": "YourWallet"}'

# 4. Monitor status
curl http://localhost:5000/api/mining/status
```

#### Enable Full AI Control

```bash
# Enable AI auto-mining
curl -X POST http://localhost:5000/api/mining/auto-mine \
  -d '{"enabled": true, "min_profitability_usd": 0.05}'

# Configure AI settings
curl -X POST http://localhost:5000/api/mining/ai/configure \
  -d '{"use_ensemble": true, "auto_tune": true}'
```

---

## Conclusion

You now have all the information needed to start mining with Nexus AI. Remember:

1. ✅ Start with CPU mining (RandomX/Monero) for easiest setup
2. ✅ Use AI auto-mining for hands-off operation
3. ✅ Monitor your earnings and hardware health
4. ✅ Consider electricity costs in profitability calculations

**Happy Mining! ⛏️**

---

*Last updated: 2024*
