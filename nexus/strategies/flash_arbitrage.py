"""
Flash Loan Arbitrage Strategy for Nexus AI.

Executes capital-free arbitrage using Aave V3 or Balancer flash loans:

  1. Borrow token A (zero capital required)
  2. Swap A → B on DEX with lower price
  3. Swap B → A on DEX with higher price
  4. Repay flash loan + fee
  5. Keep profit

Profit floor: spread must exceed flash loan fee + gas + slippage.
  • Aave fee:    0.09%
  • Balancer fee: 0%
  • Minimum net: Config.MIN_PROFIT_USD
"""
from __future__ import annotations

from typing import List, Optional

from web3 import Web3

from nexus.blockchain import BlockchainManager
from nexus.protocols.flashloan import FlashLoanProvider, AAVE_FLASH_FEE_BPS
from nexus.protocols.uniswap import UniswapV2Client, DEX_ADDRESSES, ERC20_ABI
from nexus.protocols.uniswap_v3 import UniswapV3Client, V3_ADDRESSES
from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
from nexus.feeds.price_feed import get_price_feed
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Gas for a full flash-loan arbitrage cycle (borrow + 2 swaps + repay)
FLASH_ARB_GAS_UNITS = 450_000

# Minimum spread in % before we even calculate full profitability
MIN_SPREAD_PCT = 0.15

# Token pairs to scan per chain
FLASH_PAIRS: dict[str, list[tuple[str, str]]] = {
    "ethereum": [
        ("WETH",  "USDC"),
        ("WETH",  "USDT"),
        ("WETH",  "DAI"),
        ("WBTC",  "WETH"),
        ("WBTC",  "USDC"),
    ],
    "polygon": [
        ("WMATIC", "USDC"),
        ("WETH",   "USDC"),
        ("WETH",   "USDT"),
        ("WMATIC", "USDT"),
    ],
}

# DEX clients per chain: (router_key, factory_key, label)
DEX_CFGS: dict[str, list] = {
    "ethereum": [
        ("uniswap_v2_router", "uniswap_v2_factory", "Uniswap V2"),
        ("sushiswap_router",  "sushiswap_factory",  "SushiSwap"),
    ],
    "bsc": [
        ("pancakeswap_router", "pancakeswap_factory", "PancakeSwap"),
    ],
    "polygon": [
        ("quickswap_router",  "quickswap_factory",  "QuickSwap"),
        ("sushiswap_router",  "sushiswap_factory",  "SushiSwap"),
    ],
}

# Flash loan borrow amounts in USD
FLASH_BORROW_USD = 50_000   # Large capital — zero risk since it's a flash loan


class FlashArbitrageStrategy(BaseStrategy):
    """
    Capital-free cross-DEX arbitrage using flash loans.
    Far more profitable than regular arbitrage because the borrow amount
    is not limited by the bot's own capital.
    """

    name = "flash_arbitrage"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []
        feed = get_price_feed()

        for chain in self.bm.connected_chains():
            # Flash loans only available on Ethereum and Polygon (Aave V3)
            if chain not in ("ethereum", "polygon"):
                continue

            w3 = self.bm.get_web3(chain)
            if not w3:
                continue

            gas_gwei = self.bm.get_gas_price_gwei(chain)
            if gas_gwei > Config.MAX_GAS_GWEI:
                continue

            addrs  = DEX_ADDRESSES.get(chain, {})
            tokens = TOKEN_ADDRESSES.get(chain, {})
            fl     = FlashLoanProvider(w3, chain)

            if fl.best_provider() == "none":
                continue

            # Build V2 clients
            v2_clients: list[tuple[str, UniswapV2Client]] = []
            for rkey, fkey, name in DEX_CFGS.get(chain, []):
                router  = addrs.get(rkey)
                factory = addrs.get(fkey)
                if router and factory:
                    try:
                        v2_clients.append((name, UniswapV2Client(w3, router, factory)))
                    except Exception:
                        pass

            # Build V3 client
            v3_client: Optional[UniswapV3Client] = None
            if chain in V3_ADDRESSES:
                try:
                    v3_client = UniswapV3Client(w3, chain)
                    if v3_client.is_available():
                        v3_clients_label = "Uniswap V3"
                    else:
                        v3_client = None
                except Exception:
                    pass

            all_sources: list[tuple[str, object]] = list(v2_clients)
            if v3_client:
                all_sources.append(("Uniswap V3", v3_client))

            if len(all_sources) < 2:
                continue

            native_sym   = {"ethereum": "ETH", "polygon": "MATIC"}.get(chain, "ETH")
            native_price = feed.price(native_sym) or 2_000.0

            for sym_a, sym_b in FLASH_PAIRS.get(chain, []):
                addr_a = tokens.get(sym_a)
                addr_b = tokens.get(sym_b)
                if not addr_a or not addr_b:
                    continue

                prices: dict[str, float] = {}
                for src_name, client in all_sources:
                    try:
                        if isinstance(client, UniswapV2Client):
                            p = client.get_price(addr_a, addr_b)
                        else:
                            # V3: get raw amounts, normalise separately
                            tok_a_dec = w3.eth.contract(
                                address=Web3.to_checksum_address(addr_a),
                                abi=ERC20_ABI,
                            ).functions.decimals().call()
                            raw = client.get_price(addr_a, addr_b, 1.0, tok_a_dec)
                            tok_b_dec = w3.eth.contract(
                                address=Web3.to_checksum_address(addr_b),
                                abi=ERC20_ABI,
                            ).functions.decimals().call()
                            p = raw / 10**tok_b_dec if raw else None
                        if p and p > 0:
                            prices[src_name] = p
                    except Exception:
                        pass

                if len(prices) < 2:
                    continue

                price_vals = list(prices.values())
                max_price  = max(price_vals)
                min_price  = min(price_vals)
                spread_pct = (max_price - min_price) / min_price * 100

                if spread_pct < MIN_SPREAD_PCT:
                    continue

                # Use Balancer (0 fee) if available, else Aave (0.09%)
                provider   = fl.best_provider()
                fee_pct    = AAVE_FLASH_FEE_BPS / 100 if provider == "aave" else 0

                # Gas cost
                gas_cost_usd = (gas_gwei * 1e-9) * FLASH_ARB_GAS_UNITS * native_price

                # Profit on FLASH_BORROW_USD
                gross_profit = FLASH_BORROW_USD * (spread_pct / 100)
                flash_fee    = FLASH_BORROW_USD * (fee_pct / 100)
                slippage     = FLASH_BORROW_USD * (Config.SLIPPAGE_PERCENT / 100) * 2
                net_profit   = gross_profit - flash_fee - gas_cost_usd - slippage

                if net_profit < Config.MIN_PROFIT_USD:
                    continue

                buy_dex  = list(prices.keys())[price_vals.index(min_price)]
                sell_dex = list(prices.keys())[price_vals.index(max_price)]
                confidence = min(0.92, 0.5 + spread_pct / 5)

                opp = self._make_opportunity(
                    opp_type=OpportunityType.ARBITRAGE,
                    chain=chain,
                    description=(
                        f"⚡FLASH {sym_a}/{sym_b}: buy {buy_dex}, "
                        f"sell {sell_dex} (+{spread_pct:.3f}% spread, "
                        f"${FLASH_BORROW_USD:,} loan)"
                    ),
                    profit_usd=net_profit,
                    confidence=confidence,
                    details={
                        "strategy":        "flash_arbitrage",
                        "token_a":         sym_a,
                        "token_b":         sym_b,
                        "buy_dex":         buy_dex,
                        "sell_dex":        sell_dex,
                        "spread_pct":      round(spread_pct, 5),
                        "flash_provider":  provider,
                        "flash_fee_pct":   fee_pct,
                        "borrow_usd":      FLASH_BORROW_USD,
                        "gas_cost_usd":    round(gas_cost_usd, 4),
                        "prices":          {k: round(v, 6) for k, v in prices.items()},
                    },
                )
                opportunities.append(opp)
                logger.info(
                    "[%s] Flash arb: %s/%s spread=%.3f%% net=$%.4f",
                    chain, sym_a, sym_b, spread_pct, net_profit,
                )

        opportunities.sort(key=lambda o: o.score(), reverse=True)
        return opportunities[:5]
