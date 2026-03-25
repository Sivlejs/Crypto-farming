# 🚀 Complete Step-by-Step Guide: Go Live with Nexus AI Crypto Farmer

This guide will walk you through **every single step** to get your Nexus AI crypto farming bot running in LIVE mode (not simulation). Follow each step exactly as written.

---

## ⚠️ IMPORTANT WARNINGS BEFORE YOU START

1. **Real Money Risk**: When you set `DRY_RUN=false`, the bot will execute REAL trades with REAL cryptocurrency
2. **Start Small**: Always start with a small amount of crypto you can afford to lose completely
3. **Test First**: Run in simulation mode (`DRY_RUN=true`) for at least a few hours to verify everything works
4. **Dedicated Wallet**: NEVER use your main crypto wallet - create a NEW wallet specifically for trading

---

## 📋 What You'll Need (Checklist)

Before starting, gather these items. I'll show you how to get each one:

- [ ] A computer with internet access
- [ ] A GitHub account (free)
- [ ] A Render.com account (free tier available, paid recommended for 24/7)
- [ ] A crypto wallet (MetaMask or similar)
- [ ] An Alchemy or Infura account (free tier available) for blockchain access
- [ ] Cryptocurrency to trade with (ETH, BNB, MATIC, etc.)
- [ ] (Optional) Coinbase account for profit payouts
- [ ] (Optional) Cash App for Bitcoin payouts

---

## PHASE 1: CREATE YOUR ACCOUNTS (Do This First)

### Step 1.1: Create a GitHub Account

1. Go to https://github.com/signup
2. Enter your email address
3. Create a password
4. Choose a username
5. Verify your email
6. **You now have a GitHub account** ✅

### Step 1.2: Create a Render.com Account

1. Go to https://render.com
2. Click "Get Started for Free"
3. Sign up with your GitHub account (recommended - makes deployment easier)
4. Verify your email if required
5. **You now have a Render account** ✅

### Step 1.3: Create an Alchemy Account (Blockchain Access)

Alchemy provides free access to blockchain networks. This is **REQUIRED**.

1. Go to https://www.alchemy.com/
2. Click "Start for Free"
3. Create an account with your email
4. Once logged in, click "Create new app"
5. Fill in:
   - App Name: `nexus-ai`
   - Description: `Crypto farming bot`
   - Chain: Select **Ethereum**
   - Network: Select **Mainnet**
6. Click "Create app"
7. Click on your new app
8. Click "API Key" at the top right
9. **Copy and save the HTTPS URL** - it will look like:
   ```
   https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY_HERE
   ```
10. **Write this down - you'll need it later** ✅

**Repeat for other chains you want to use:**
- For Polygon: Create another app, select "Polygon" and "Mainnet"
- For Arbitrum: Create another app, select "Arbitrum" and "Mainnet"
- For Base: Create another app, select "Base" and "Mainnet"

---

## PHASE 2: CREATE YOUR TRADING WALLET

### Step 2.1: Install MetaMask

1. Go to https://metamask.io/download/
2. Click "Install MetaMask for Chrome" (or your browser)
3. Add the extension to your browser
4. Click "Create a new wallet"
5. Create a strong password
6. **CRITICAL: Write down your 12-word Secret Recovery Phrase on PAPER**
   - Store it safely offline
   - NEVER share it with anyone
   - NEVER store it digitally (no screenshots, no notes apps)
7. Confirm your recovery phrase
8. **You now have a MetaMask wallet** ✅

### Step 2.2: Get Your Wallet Address

1. Open MetaMask in your browser (click the fox icon)
2. At the top, you'll see "Account 1" and below it your address starting with `0x...`
3. Click on the address to copy it
4. **Save this address** - it looks like: `0x742d35Cc6634C0532925a3b844Bc9e7595f...`
5. This is your `WALLET_ADDRESS` ✅

### Step 2.3: Get Your Private Key (KEEP THIS SECRET!)

1. Open MetaMask
2. Click the three dots (⋮) next to your account name
3. Click "Account details"
4. Click "Show private key"
5. Enter your MetaMask password
6. **Copy your private key** - it's a long string starting with `0x` or just a long hex string
7. **NEVER SHARE THIS WITH ANYONE**
8. This is your `WALLET_PRIVATE_KEY` ✅

---

## PHASE 3: FUND YOUR WALLET (Add Crypto)

You need to add cryptocurrency to your wallet. The minimum recommended amounts:

| Chain | Token | Minimum Recommended | Used For |
|-------|-------|---------------------|----------|
| Ethereum | ETH | 0.1 ETH (~$300) | Trading + Gas fees |
| Polygon | MATIC | 50 MATIC (~$50) | Trading + Gas fees |
| BNB Chain | BNB | 0.1 BNB (~$60) | Trading + Gas fees |

**How to fund your wallet:**

### Option A: Buy on Exchange and Send

1. Go to Coinbase.com or another exchange
2. Buy ETH, MATIC, or BNB
3. Withdraw to your MetaMask wallet address (the one you copied in Step 2.2)
4. Wait for the transaction to confirm (can take 5-30 minutes)

### Option B: Buy Directly in MetaMask

1. Open MetaMask
2. Click "Buy"
3. Choose your payment method
4. Buy the cryptocurrency you want

**Verify your balance:**
1. Open MetaMask
2. Check that you see your crypto balance

---

## PHASE 4: FORK THE REPOSITORY

### Step 4.1: Fork the Repo to Your GitHub

1. Go to https://github.com/Sivlejs/Crypto-farming
2. Click the "Fork" button (top right)
3. Select your GitHub account
4. Wait for the fork to complete
5. You now have your own copy at `https://github.com/YOUR_USERNAME/Crypto-farming` ✅

---

## PHASE 5: DEPLOY TO RENDER.COM

### Step 5.1: Create a New Blueprint

1. Go to https://dashboard.render.com
2. Click "New" (blue button at top)
3. Select "Blueprint"
4. Connect your GitHub account if not already connected
5. Find your forked `Crypto-farming` repository
6. Click "Connect"
7. Render will detect the `render.yaml` file automatically

### Step 5.2: Configure Environment Variables

Render will show you the services it's going to create and ask for environment variables.

**REQUIRED Variables - Enter these exactly:**

| Variable | What to Enter |
|----------|---------------|
| `WALLET_ADDRESS` | Your MetaMask wallet address (starts with `0x`) |
| `WALLET_PRIVATE_KEY` | Your MetaMask private key (KEEP SECRET) |
| `ETH_RPC_URL` | Your Alchemy Ethereum URL (from Step 1.3) |
| `BSC_RPC_URL` | `https://bsc-dataseed1.binance.org/` (free public endpoint) |
| `POLYGON_RPC_URL` | Your Alchemy Polygon URL (if you created one) or `https://polygon-rpc.com/` |

**IMPORTANT SETTINGS:**

| Variable | Value | Description |
|----------|-------|-------------|
| `DRY_RUN` | `true` | **START WITH TRUE** - Test first! |
| `MIN_PROFIT_USD` | `2.00` | Minimum profit to execute a trade |
| `MAX_TRADE_USD` | `100` | **START SMALL** - Max per trade |
| `MAX_GAS_GWEI` | `80` | Maximum gas price |

### Step 5.3: Deploy

1. Click "Apply" or "Deploy Blueprint"
2. Render will create:
   - `nexus-ai-web` (your dashboard)
   - `nexus-ai-worker` (the trading bot)
   - `nexus-ai-inference` (AI analysis)
   - `nexus-ai-blockchain` (chain connections)
   - `nexus-ai-monitoring` (metrics)
   - `nexus-ai-redis` (database)
3. Wait ~5-10 minutes for everything to deploy
4. Click on `nexus-ai-web` service
5. Find the URL at the top (looks like `https://nexus-ai-web.onrender.com`)
6. **Click the URL to open your dashboard** ✅

---

## PHASE 6: VERIFY SIMULATION MODE WORKS

### Step 6.1: Check the Dashboard

1. Open your Nexus AI dashboard URL
2. You should see:
   - Chain connection status (green = connected)
   - Strategy cards showing status
   - "DRY_RUN: true" in settings

### Step 6.2: Test in Simulation

1. Click the chat button (💬) in the bottom-right corner
2. Type: "What's your status?"
3. Nexus should respond with its current state
4. Watch for simulated opportunities and trades
5. Let it run for at least 1-2 hours in simulation mode

### Step 6.3: Check for Errors

1. In Render dashboard, click on `nexus-ai-worker`
2. Click "Logs"
3. Look for any red error messages
4. Common issues:
   - "Invalid API key" → Check your Alchemy URL
   - "Insufficient funds" → Add more crypto to your wallet
   - "Connection failed" → Wait and check again

---

## PHASE 7: GO LIVE (Enable Real Trading)

### ⚠️ ONLY DO THIS AFTER SIMULATION WORKS PERFECTLY

### Step 7.1: Update Environment Variable

1. Go to Render dashboard
2. Click on `nexus-ai-worker` service
3. Click "Environment" tab on the left
4. Find `DRY_RUN`
5. Change the value from `true` to `false`
6. Click "Save Changes"
7. The service will restart automatically

### Step 7.2: Do the Same for Other Services

Repeat for these services:
- `nexus-ai-web`
- `nexus-ai-inference`

### Step 7.3: Verify Live Mode

1. Open your dashboard
2. Check that DRY_RUN shows `false` in settings
3. Or type in chat: "Are you in live mode?"

**🎉 Your bot is now trading with real money!**

---

## PHASE 8: SET UP PROFIT PAYOUTS (Optional)

### Option A: Payout to Coinbase

1. Go to https://www.coinbase.com/settings/api
2. Click "New API Key"
3. Enable permissions:
   - `wallet:accounts:read`
   - `wallet:transactions:send`
4. Copy your API Key and API Secret
5. In Render, add these environment variables:
   - `COINBASE_API_KEY`: Your API key
   - `COINBASE_API_SECRET`: Your API secret
   - `PAYOUT_THRESHOLD_USD`: `10` (payout when profits reach $10)

### Option B: Payout to Your Wallet

1. In Render, add these environment variables:
   - `PAYOUT_WALLET_ADDRESS`: Your wallet address (can be the same trading wallet or different)
   - `PAYOUT_CHAIN`: `ethereum` (or `polygon`, `bsc`)
   - `PAYOUT_TOKEN`: `USDC` (or `ETH`, `WETH`, `USDT`)
   - `PAYOUT_THRESHOLD_USD`: `10`

### Option C: Payout to Cash App (Bitcoin)

1. Open Cash App
2. Go to Bitcoin section
3. Find your Cash App Lightning address (looks like `yourname@cashapp.com`)
4. In Render, add:
   - `LIGHTNING_ADDRESS`: Your Cash App lightning address

---

## PHASE 9: ENABLE MORE CHAINS (Optional)

To trade on more blockchains for more opportunities:

### Enable Arbitrum
1. Create an Alchemy app for Arbitrum (see Step 1.3)
2. In Render environment, add:
   - `CHAIN_ARBITRUM`: `true`
   - `ARBITRUM_RPC_URL`: Your Alchemy Arbitrum URL

### Enable Optimism
- `CHAIN_OPTIMISM`: `true`
- `OPTIMISM_RPC_URL`: `https://mainnet.optimism.io`

### Enable Base
- `CHAIN_BASE`: `true`
- `BASE_RPC_URL`: `https://mainnet.base.org`

### Enable Avalanche
- `CHAIN_AVALANCHE`: `true`
- `AVALANCHE_RPC_URL`: `https://api.avax.network/ext/bc/C/rpc`

---

## PHASE 10: MONITORING YOUR BOT

### Daily Checks
1. Open your dashboard daily
2. Check the "Trades" tab for recent activity
3. Check the "Payout" tab for accumulated profits
4. Check the "Overview" tab for KPIs

### Voice/Chat Commands
Click 💬 and try:
- "What's my total profit?"
- "Give me a status update"
- "What opportunities are you seeing?"
- "What's the market doing?"

### If Something Goes Wrong
1. Type in chat: "Stop the bot"
2. Or in Render, click "Suspend" on the worker service
3. Check logs for errors
4. Fix the issue
5. Resume: "Start trading" or click "Resume" in Render

---

## 🔒 SECURITY BEST PRACTICES

1. **Never share your private key** - With anyone, ever
2. **Use a dedicated trading wallet** - Don't use your main savings wallet
3. **Set MAX_TRADE_USD low** - Start with $100 or less per trade
4. **Monitor regularly** - Check your bot at least daily
5. **Enable 2FA** - On all accounts (GitHub, Render, Coinbase)
6. **Don't commit secrets** - Environment variables only

---

## 🆘 TROUBLESHOOTING

### "Chain not connected"
- Check your RPC URLs are correct
- Try a different RPC endpoint
- Check if you have enough native token for gas

### "No opportunities found"
- This is normal when markets are stable
- The bot constantly scans; it will find opportunities
- Check that strategies are enabled

### "Transaction failed"
- Check gas settings (MAX_GAS_GWEI)
- Check you have enough ETH/MATIC/BNB for gas
- Check SLIPPAGE_PERCENT (try 1.0 instead of 0.5)

### "Dashboard not loading"
- Wait a few minutes for service to start
- Check Render logs for errors
- Make sure all services are running

---

## 📊 EXPECTED RESULTS

- **First week**: The AI is learning, may have low activity
- **After 30 trades**: ML model activates, better opportunity scoring
- **Realistic returns**: Varies with market conditions; not guaranteed

---

## 💰 COST BREAKDOWN

| Service | Cost |
|---------|------|
| Render (Starter plan) | Free / ~$7/month per service |
| Alchemy | Free tier (300M compute units/month) |
| Trading capital | Whatever you fund your wallet with |
| Gas fees | Varies by chain ($0.01-$50 per transaction) |

---

## 🎯 QUICK REFERENCE

**Your Dashboard URL**: `https://nexus-ai-web.onrender.com` (yours will be different)

**Key Environment Variables**:
```
DRY_RUN=false              # true=simulation, false=live
WALLET_ADDRESS=0x...       # Your wallet
WALLET_PRIVATE_KEY=...     # KEEP SECRET
ETH_RPC_URL=https://...    # Alchemy URL
MIN_PROFIT_USD=2.00        # Min profit per trade
MAX_TRADE_USD=100          # Max per trade
```

**Chat Commands**:
- "Start trading" / "Stop the bot"
- "Go live" / "Switch to simulation mode"
- "What's my profit?"
- "Sweep profits to Coinbase"

---

## ✅ FINAL CHECKLIST

Before going live, confirm:
- [ ] MetaMask wallet created with address and private key
- [ ] Alchemy account with RPC URLs
- [ ] Wallet funded with crypto
- [ ] Repository forked to your GitHub
- [ ] Render services deployed and running
- [ ] Simulation mode tested for 1+ hours
- [ ] DRY_RUN set to `false` on all services
- [ ] Payout destination configured
- [ ] MAX_TRADE_USD set to a safe limit

---

**🎉 Congratulations! Your Nexus AI crypto farming bot is now live!**

Monitor it regularly and adjust settings as needed. Remember: crypto trading involves risk. Only trade with what you can afford to lose.

For questions, open an issue on the GitHub repository.
