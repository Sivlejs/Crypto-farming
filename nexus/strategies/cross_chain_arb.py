"""Cross-Chain Arbitrage — same token priced differently across chains."""
from __future__ import annotations
from typing import List
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.feeds.price_feed import get_price_feed
from nexus.protocols.uniswap import UniswapV2Client, DEX_ADDRESSES
from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

BRIDGE_FEE_PCT = 0.1   # typical bridge cost %
TRADE_USD      = 8_000

CROSS_TOKENS = ["WETH","USDC","USDT","WBTC","LINK"]

class CrossChainArbStrategy(BaseStrategy):
    name = "cross_chain_arb"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        chains  = self.bm.connected_chains()
        feed    = get_price_feed()
        prices  = feed.all_prices()

        if len(chains) < 2:
            return []

        usdc_prices: dict[str, float] = {}
        for chain in chains:
            w3    = self.bm.get_web3(chain)
            addrs = DEX_ADDRESSES.get(chain, {})
            toks  = TOKEN_ADDRESSES.get(chain, {})
            router = addrs.get("uniswap_v2_router") or addrs.get("pancakeswap_router") or addrs.get("quickswap_router")
            factory= addrs.get("uniswap_v2_factory") or addrs.get("pancakeswap_factory") or addrs.get("quickswap_factory")
            if not w3 or not router or not factory:
                continue
            try:
                client = UniswapV2Client(w3, router, factory)
                weth   = toks.get("WETH") or toks.get("WBNB") or toks.get("WMATIC")
                usdc   = toks.get("USDC") or toks.get("BUSD")
                if weth and usdc:
                    p = client.get_price(weth, usdc)
                    if p:
                        usdc_prices[chain] = p
            except Exception:
                continue

        if len(usdc_prices) < 2:
            return []

        items = sorted(usdc_prices.items(), key=lambda x: x[1])
        lo_chain, lo_price = items[0]
        hi_chain, hi_price = items[-1]

        spread_pct = (hi_price - lo_price) / lo_price * 100
        if spread_pct <= BRIDGE_FEE_PCT + 0.2:
            return []

        gas_cost  = 20.0
        profit    = TRADE_USD * (spread_pct / 100) - TRADE_USD * (BRIDGE_FEE_PCT / 100) - gas_cost
        if profit < Config.MIN_PROFIT_USD:
            return []

        opps.append(self._make_opportunity(
            opp_type=OpportunityType.ARBITRAGE,
            chain=lo_chain,
            description=f"🌉 Cross-chain WETH: buy {lo_chain} ${lo_price:.2f}, sell {hi_chain} ${hi_price:.2f} (+{spread_pct:.3f}%)",
            profit_usd=profit,
            confidence=0.58,
            details={"strategy":"cross_chain_arb","buy_chain":lo_chain,"sell_chain":hi_chain,
                     "spread_pct":round(spread_pct,4),"bridge_fee_pct":BRIDGE_FEE_PCT},
        ))
        return opps
