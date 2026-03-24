"""
DEX Arbitrage Strategy for Nexus AI.

Finds price discrepancies between DEXes on the same chain and
calculates whether executing a trade sequence would be profitable
after accounting for gas costs and slippage.
"""
from __future__ import annotations

from typing import List

from nexus.blockchain import BlockchainManager
from nexus.protocols.uniswap import UniswapV2Client, DEX_ADDRESSES
from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Estimated gas for a full arbitrage sequence:
# token approval (~60k) + buy swap (~120k) + sell swap (~120k) = ~300k
GAS_UNITS_ESTIMATE = 300_000

# Default native token price used when live price fetch fails
DEFAULT_NATIVE_PRICE_USD = 2_000.0

# Token pairs to check for arbitrage on each chain
ARBI_PAIRS: dict[str, list[tuple[str, str]]] = {
    "ethereum": [
        ("WETH", "USDC"),
        ("WETH", "USDT"),
        ("WETH", "DAI"),
        ("WBTC", "WETH"),
    ],
    "bsc": [
        ("WBNB", "USDT"),
        ("WBNB", "BUSD"),
        ("WETH", "WBNB"),
    ],
    "polygon": [
        ("WMATIC", "USDC"),
        ("WETH", "USDC"),
        ("WMATIC", "USDT"),
    ],
}

# DEX pairs to compare per chain: (router_key, factory_key)
DEX_PAIRS: dict[str, list[tuple[str, str, str]]] = {
    "ethereum": [
        ("uniswap_v2_router", "uniswap_v2_factory", "Uniswap V2"),
        ("sushiswap_router", "sushiswap_factory", "SushiSwap"),
    ],
    "bsc": [
        ("pancakeswap_router", "pancakeswap_factory", "PancakeSwap"),
    ],
    "polygon": [
        ("quickswap_router", "quickswap_factory", "QuickSwap"),
        ("sushiswap_router", "sushiswap_factory", "SushiSwap"),
    ],
}


class ArbitrageStrategy(BaseStrategy):
    """Cross-DEX arbitrage strategy."""

    name = "arbitrage"

    def find_opportunities(self) -> List[Opportunity]:
        opportunities: List[Opportunity] = []

        for chain in self.bm.connected_chains():
            if chain not in DEX_PAIRS or len(DEX_PAIRS[chain]) < 2:
                # Need at least 2 DEXes to compare prices
                continue

            w3 = self.bm.get_web3(chain)
            if not w3:
                continue

            gas_price_gwei = self.bm.get_gas_price_gwei(chain)
            if gas_price_gwei > Config.MAX_GAS_GWEI:
                logger.info(
                    "[%s] Gas too high (%.1f Gwei > %.1f max), skipping arbitrage",
                    chain, gas_price_gwei, Config.MAX_GAS_GWEI,
                )
                continue

            addrs = DEX_ADDRESSES.get(chain, {})
            tokens = TOKEN_ADDRESSES.get(chain, {})
            dex_cfgs = DEX_PAIRS[chain]

            # Build clients for each DEX on this chain
            clients: list[tuple[str, UniswapV2Client]] = []
            for router_key, factory_key, dex_name in dex_cfgs:
                router = addrs.get(router_key)
                factory = addrs.get(factory_key)
                if router and factory:
                    try:
                        client = UniswapV2Client(w3, router, factory)
                        clients.append((dex_name, client))
                    except Exception as exc:
                        logger.debug("Could not init %s client: %s", dex_name, exc)

            if len(clients) < 2:
                continue

            pairs = ARBI_PAIRS.get(chain, [])
            for sym_a, sym_b in pairs:
                addr_a = tokens.get(sym_a)
                addr_b = tokens.get(sym_b)
                if not addr_a or not addr_b:
                    continue

                prices: dict[str, float] = {}
                for dex_name, client in clients:
                    p = client.get_price(addr_a, addr_b)
                    if p and p > 0:
                        prices[dex_name] = p

                if len(prices) < 2:
                    continue

                dex_names = list(prices.keys())
                price_values = list(prices.values())

                max_price = max(price_values)
                min_price = min(price_values)
                spread_pct = (max_price - min_price) / min_price * 100

                # Estimate gas cost in USD (approximate)
                native_price_usd = self._get_native_price(chain)
                gas_cost_usd = (
                    gas_price_gwei * 1e-9 * GAS_UNITS_ESTIMATE * native_price_usd
                )

                # Estimate profit; cap trade size at a sensible maximum to
                # avoid over-estimating on thin liquidity pools
                trade_usd = min(Config.MAX_TRADE_USD, 200)
                gross_profit_usd = trade_usd * (spread_pct / 100)
                slippage_cost = trade_usd * (Config.SLIPPAGE_PERCENT / 100) * 2
                net_profit_usd = gross_profit_usd - gas_cost_usd - slippage_cost

                if spread_pct > 0.3:  # At least 0.3% spread to log
                    logger.info(
                        "[%s] %s/%s spread: %.3f%% (net profit: $%.4f)",
                        chain, sym_a, sym_b, spread_pct, net_profit_usd,
                    )

                if net_profit_usd >= Config.MIN_PROFIT_USD:
                    buy_dex = dex_names[price_values.index(min_price)]
                    sell_dex = dex_names[price_values.index(max_price)]
                    confidence = min(0.95, spread_pct / 2)

                    opp = self._make_opportunity(
                        opp_type=OpportunityType.ARBITRAGE,
                        chain=chain,
                        description=(
                            f"Buy {sym_a}/{sym_b} on {buy_dex}, "
                            f"sell on {sell_dex} (+{spread_pct:.2f}%)"
                        ),
                        profit_usd=net_profit_usd,
                        confidence=confidence,
                        details={
                            "token_a": sym_a,
                            "token_b": sym_b,
                            "buy_dex": buy_dex,
                            "sell_dex": sell_dex,
                            "spread_pct": round(spread_pct, 4),
                            "gas_cost_usd": round(gas_cost_usd, 4),
                            "trade_usd": trade_usd,
                            "prices": {k: round(v, 6) for k, v in prices.items()},
                        },
                    )
                    opportunities.append(opp)

        return opportunities

    def _get_native_price(self, chain: str) -> float:
        """Approximate native token price in USD."""
        try:
            from nexus.protocols.dex_aggregator import PriceAggregator
            native = {"ethereum": "ETH", "bsc": "BNB", "polygon": "MATIC"}
            symbol = native.get(chain, "ETH")
            price = PriceAggregator.get_price(symbol)
            return price or DEFAULT_NATIVE_PRICE_USD
        except Exception:
            return DEFAULT_NATIVE_PRICE_USD
