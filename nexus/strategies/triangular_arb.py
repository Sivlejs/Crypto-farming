"""Triangular Arbitrage — A→B→C→A on a single DEX."""
from __future__ import annotations
from typing import List
from web3 import Web3
from nexus.strategies.base import BaseStrategy, Opportunity, OpportunityType
from nexus.protocols.uniswap import UniswapV2Client, DEX_ADDRESSES
from nexus.protocols.dex_aggregator import TOKEN_ADDRESSES
from nexus.utils.config import Config
from nexus.utils.logger import get_logger
logger = get_logger(__name__)

TRIANGLES = {
    "ethereum": [
        ("WETH","USDC","WBTC"), ("WETH","DAI","USDC"), ("WETH","USDT","DAI"),
        ("WETH","LINK","USDC"), ("WETH","UNI","USDC"),
    ],
    "bsc": [
        ("WBNB","BUSD","CAKE"), ("WBNB","USDT","BUSD"), ("WBNB","ETH","USDT"),
    ],
    "polygon": [
        ("WMATIC","USDC","WETH"), ("WMATIC","USDT","USDC"), ("WETH","USDC","WMATIC"),
    ],
}
TRADE_USD = 10_000
GAS_COST  = 25.0

class TriangularArbStrategy(BaseStrategy):
    name = "triangular_arb"

    def find_opportunities(self) -> List[Opportunity]:
        opps: List[Opportunity] = []
        for chain in self.bm.connected_chains():
            w3    = self.bm.get_web3(chain)
            addrs = DEX_ADDRESSES.get(chain, {})
            toks  = TOKEN_ADDRESSES.get(chain, {})
            router = addrs.get("uniswap_v2_router") or addrs.get("pancakeswap_router") or addrs.get("quickswap_router")
            factory= addrs.get("uniswap_v2_factory") or addrs.get("pancakeswap_factory") or addrs.get("quickswap_factory")
            if not router or not factory or not w3:
                continue
            try:
                client = UniswapV2Client(w3, router, factory)
            except Exception:
                continue
            for (a, b, c) in TRIANGLES.get(chain, []):
                try:
                    ta, tb, tc = toks.get(a), toks.get(b), toks.get(c)
                    if not all([ta, tb, tc]):
                        continue
                    p1 = client.get_price(ta, tb)
                    p2 = client.get_price(tb, tc)
                    p3 = client.get_price(tc, ta)
                    if not all([p1, p2, p3]):
                        continue
                    cycle = p1 * p2 * p3
                    if cycle <= 1.0:
                        continue
                    profit_pct = (cycle - 1.0) * 100
                    profit_usd = TRADE_USD * (cycle - 1.0) - GAS_COST
                    if profit_usd < Config.MIN_PROFIT_USD:
                        continue
                    opps.append(self._make_opportunity(
                        opp_type=OpportunityType.ARBITRAGE,
                        chain=chain,
                        description=f"△ {a}→{b}→{c}→{a} cycle={cycle:.6f} (+{profit_pct:.3f}%)",
                        profit_usd=profit_usd,
                        confidence=0.65,
                        details={"strategy":"triangular_arb","path":[a,b,c],"cycle":cycle,"trade_usd":TRADE_USD},
                    ))
                except Exception as e:
                    logger.debug("tri_arb %s/%s/%s: %s", a, b, c, e)
        opps.sort(key=lambda o: o.score(), reverse=True)
        return opps[:3]
