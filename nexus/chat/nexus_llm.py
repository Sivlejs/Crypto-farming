"""
NexusChat — Conversational AI engine for Nexus AI.

Supports two modes:
  1. OpenAI GPT-4o (or any OpenAI-compatible API) — rich, intelligent responses
  2. Rule-based responder — works with zero API keys using live bot data

Personality: Nexus is a sharp, confident AI trading assistant.
It always has access to the live bot status, trade history, and
market data so every answer is grounded in real information.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from nexus.chat.command_processor import parse_command, ParsedCommand
from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# Maximum chat history to send to LLM (keeps context window small)
MAX_HISTORY = 12

NEXUS_SYSTEM_PROMPT = """You are Nexus, an elite AI crypto trading assistant embedded in an autonomous DeFi farming bot.

Your personality:
- Sharp, confident, and precise — like a seasoned quant trader
- Speak in first person ("I found 3 arbitrage opportunities…")
- Keep responses concise but informative (2-4 sentences unless asked for details)
- Use exact numbers from the data provided
- When uncertain, say so clearly — never fabricate trade data
- You care about the user's financial success and safety

You have real-time access to:
- Bot status (running/stopped, dry run mode, uptime)
- Current opportunities being scanned
- Full trade history and profits
- Market prices and regime (volatile/trending/calm)
- Payout status (pending rewards, Coinbase/Lightning config)
- AI brain learning status (win rate, model accuracy, adaptive parameters)

Always use the [BOT_STATUS] context block provided with each message to answer accurately.
If the user asks to start/stop the bot or trigger a payout, confirm the action and say you've sent the command."""


class NexusChat:
    """
    Conversational engine for Nexus AI.
    Supports OpenAI API or intelligent rule-based fallback.
    """

    def __init__(self):
        self._history: list[dict] = []
        self._has_openai = bool(Config.OPENAI_API_KEY)
        logger.info(
            "NexusChat ready — mode: %s",
            "OpenAI GPT-4o" if self._has_openai else "Rule-based (set OPENAI_API_KEY to upgrade)",
        )

    # ── Public API ────────────────────────────────────────────

    def chat(self, user_message: str, bot_status: Optional[dict] = None) -> dict:
        """
        Process a user message and return a response.

        Returns:
            {
              "reply":   str,          # Nexus's response text
              "intent":  str,          # detected command intent
              "action":  str | None,   # bot action to execute (start/stop/payout/etc.)
              "speak":   bool,         # whether to TTS this reply
            }
        """
        cmd = parse_command(user_message)
        action = self._action_for(cmd)

        context = self._build_context(bot_status or {})

        if self._has_openai:
            reply = self._openai_reply(user_message, context)
        else:
            reply = self._rule_reply(cmd, bot_status or {})

        # Store history (truncate to MAX_HISTORY)
        self._history.append({"role": "user",      "content": user_message})
        self._history.append({"role": "assistant",  "content": reply})
        if len(self._history) > MAX_HISTORY * 2:
            self._history = self._history[-MAX_HISTORY * 2:]

        return {
            "reply":   reply,
            "intent":  cmd.intent,
            "action":  action,
            "speak":   True,
        }

    def clear_history(self):
        self._history = []

    # ── OpenAI ────────────────────────────────────────────────

    def _openai_reply(self, user_message: str, context: str) -> str:
        messages = [
            {"role": "system", "content": NEXUS_SYSTEM_PROMPT},
            {"role": "system", "content": context},
            *self._history[:-2],   # history without the just-appended pair
            {"role": "user",   "content": user_message},
        ]
        try:
            base_url = Config.OPENAI_BASE_URL or "https://api.openai.com"
            resp = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model":       Config.OPENAI_MODEL,
                    "messages":    messages,
                    "max_tokens":  300,
                    "temperature": 0.7,
                },
                headers={
                    "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
                    "Content-Type":  "application/json",
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("OpenAI call failed (%s) — falling back to rule-based", exc)
            return self._rule_reply(parse_command(user_message), {})

    # ── Rule-based fallback ───────────────────────────────────

    def _rule_reply(self, cmd: ParsedCommand, status: dict) -> str:
        rewards     = status.get("rewards", {})
        monitor     = status.get("monitor", {})
        payout      = status.get("payout",  {})
        brain       = status.get("brain",   {})
        prices      = status.get("prices",  {})
        running     = status.get("running", False)
        dry_run     = status.get("dry_run", True)
        uptime      = status.get("uptime_seconds", 0)
        regime      = (brain.get("market_regime") or {}).get("regime", "unknown")

        def fmt_usd(v): return f"${float(v or 0):.4f}"
        def uptime_str(s):
            h, m = divmod(int(s or 0), 3600)
            return f"{h}h {m//60}m"

        intent = cmd.intent

        if intent == "status":
            mode = "LIVE" if not dry_run else "SIMULATION"
            return (
                f"I'm {'running' if running else 'stopped'} in {mode} mode. "
                f"Uptime: {uptime_str(uptime)}. "
                f"I've completed {rewards.get('successful_trades', 0)} successful trades "
                f"with {fmt_usd(rewards.get('estimated_total_profit_usd', 0))} estimated profit. "
                f"Market regime is {regime}."
            )

        elif intent == "profit":
            return (
                f"Total estimated profit: {fmt_usd(rewards.get('estimated_total_profit_usd', 0))}. "
                f"Successful trades: {rewards.get('successful_trades', 0)} out of {rewards.get('total_trades', 0)}. "
                f"Total paid out: {fmt_usd(payout.get('total_paid_usd', 0))}. "
                f"Pending payout: {fmt_usd(payout.get('pending_usd', 0))}."
            )

        elif intent == "opportunities":
            n = monitor.get("total_found", 0)
            scans = monitor.get("scan_count", 0)
            return (
                f"I've run {scans} scans and found {n} total opportunities. "
                f"My scanner is block-triggered — I react to every new block instantly. "
                f"Active strategies: arbitrage, flash loans, yield farming, liquidity mining, and liquidations."
            )

        elif intent == "payout":
            pending = float(payout.get("pending_usd", 0) or 0)
            thresh  = float(payout.get("threshold_usd", 10) or 10)
            return (
                f"You have {fmt_usd(pending)} pending. "
                f"Auto-sweep triggers at {fmt_usd(thresh)}. "
                f"Payout mode: {payout.get('payout_mode', 'not configured')}. "
                + (f"Sending sweep command now." if pending > 0.01 else "Not enough to sweep yet.")
            )

        elif intent == "prices":
            if prices:
                top = [(s, p) for s, p in prices.items() if s in ("ETH","BTC","BNB","MATIC")]
                price_str = "  ".join(f"{s}: ${p:,.2f}" for s, p in top[:4])
                return f"Current prices — {price_str}. Market regime: {regime}."
            return "Price feed is warming up. I update prices every 20 seconds from CoinGecko."

        elif intent == "regime":
            weights = (brain.get("market_regime") or {}).get("strategy_weights", {})
            top_strat = max(weights, key=weights.get) if weights else "unknown"
            return (
                f"Market is currently {regime}. "
                f"I'm weighting {top_strat.replace('_',' ')} highest right now "
                f"({weights.get(top_strat, 1.0)}× priority). "
                f"ETH volatility: {(brain.get('market_regime') or {}).get('eth_volatility_pct', 0):.2f}%."
            )

        elif intent == "brain":
            ml_active = brain.get("ml_active", False)
            win_rate  = brain.get("win_rate", 0)
            trained   = (brain.get("model_info") or {}).get("trained_on", 0)
            until_ml  = brain.get("trades_until_ml", 30)
            if ml_active:
                return (
                    f"My ML model is ACTIVE, trained on {trained} trades with a {win_rate}% win rate. "
                    f"I'm using RandomForest + gradient boosting to score every opportunity. "
                    f"Parameters are auto-tuning every 5 minutes based on performance."
                )
            return (
                f"My ML brain is warming up — I need {until_ml} more executed trades to activate. "
                f"Right now I'm using heuristic scoring. Once active, I'll use machine learning to "
                f"predict success probability for every opportunity."
            )

        elif intent == "start":
            return f"Starting the bot now in {'SIMULATION' if dry_run else 'LIVE'} mode. Scanning all chains for opportunities."

        elif intent == "stop":
            return "Stopping all trading activity. Your positions and pending rewards are safe."

        elif intent == "set_dry_run":
            is_dry = cmd.params.get("dry_run", True)
            return (
                f"Switching to {'SIMULATION' if is_dry else 'LIVE TRADING'} mode. "
                + ("No real transactions will be made." if is_dry else
                   "⚠ Real transactions will now execute. Make sure your wallet is funded.")
            )

        elif intent == "settings":
            return (
                f"Current settings:\n"
                f"• Mode: {'SIMULATION' if dry_run else 'LIVE'}\n"
                f"• Min profit: {fmt_usd(Config.MIN_PROFIT_USD)}\n"
                f"• Max gas: {Config.MAX_GAS_GWEI} Gwei\n"
                f"• Slippage: {Config.SLIPPAGE_PERCENT}%\n"
                f"• Payout threshold: {fmt_usd(Config.PAYOUT_THRESHOLD_USD)}\n"
                f"• Payout mode: {payout.get('payout_mode', 'not configured')}\n"
                f"You can change these by saying 'set min profit to $5' or visiting the Settings tab."
            )

        elif intent == "set_min_profit":
            value = cmd.params.get("value")
            if value:
                return f"Setting minimum profit threshold to ${value:.2f}. I'll only execute trades above this amount."
            return "Tell me the minimum profit amount, like 'set min profit to $5'."

        elif intent == "set_gas_limit":
            value = cmd.params.get("value")
            if value:
                return f"Setting maximum gas price to {value:.0f} Gwei. I'll skip trades when gas exceeds this."
            return "Tell me the max gas price in Gwei, like 'set gas limit to 50'."

        elif intent == "set_slippage":
            value = cmd.params.get("value")
            if value:
                return f"Setting slippage tolerance to {value:.1f}%. Trades will fail if price moves more than this."
            return "Tell me the slippage tolerance, like 'set slippage to 0.5 percent'."

        elif intent == "set_threshold":
            value = cmd.params.get("value")
            if value:
                return f"Setting payout threshold to ${value:.2f}. I'll auto-sweep profits when they reach this amount."
            return "Tell me the payout threshold, like 'set threshold to $50'."

        elif intent == "set_coinbase":
            return (
                "To set up Coinbase payouts, go to the Settings tab in the dashboard and enter your "
                "Coinbase API credentials. You can get API keys at coinbase.com/settings/api. "
                "Make sure to enable 'wallet:transactions:send' permission for the key."
            )

        elif intent == "set_payout_addr":
            addr = cmd.params.get("address")
            if addr:
                return f"Setting payout address to {addr[:10]}...{addr[-6:]}. Profits will be sent here."
            return "Tell me the wallet address, like 'set payout address to 0x...'."

        elif intent == "help":
            return (
                "I understand voice and text commands. Try:\n"
                "• 'What's my profit?' or 'Show status'\n"
                "• 'Start trading' or 'Stop the bot'\n"
                "• 'Sweep my profits' or 'Show payout'\n"
                "• 'Set min profit to $5' or 'Set gas limit to 50'\n"
                "• 'Configure Coinbase' or 'Show settings'\n"
                "• 'What's the market doing?' or 'How's your brain?'"
            )

        else:
            return (
                f"I'm your Nexus AI trading assistant. I'm {'running' if running else 'stopped'} "
                f"and have earned {fmt_usd(rewards.get('estimated_total_profit_usd', 0))} so far. "
                f"Ask me about profits, opportunities, market conditions, or say 'help' for commands."
            )

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _action_for(cmd: ParsedCommand) -> Optional[str]:
        """Map parsed intent to a bot action string."""
        return {
            "start":           "start",
            "stop":            "stop",
            "payout":          "payout",
            "set_dry_run":     "set_dry_run",
            "set_min_profit":  "set_min_profit",
            "set_gas_limit":   "set_gas_limit",
            "set_slippage":    "set_slippage",
            "set_threshold":   "set_threshold",
            "set_payout_addr": "set_payout_addr",
        }.get(cmd.intent)

    @staticmethod
    def _build_context(status: dict) -> str:
        """Build a compact context block injected into the LLM prompt."""
        try:
            rewards = status.get("rewards", {})
            payout  = status.get("payout",  {})
            brain   = status.get("brain",   {})
            prices  = status.get("prices",  {})
            return f"""[BOT_STATUS]
Running: {status.get('running', False)} | Mode: {'LIVE' if not status.get('dry_run', True) else 'SIM'}
Uptime: {status.get('uptime_seconds', 0)}s
Profit: ${rewards.get('estimated_total_profit_usd', 0):.4f} | Trades: {rewards.get('total_trades', 0)} | Wins: {rewards.get('successful_trades', 0)}
Pending payout: ${payout.get('pending_usd', 0):.4f} | Paid out: ${payout.get('total_paid_usd', 0):.4f}
Payout mode: {payout.get('payout_mode', 'not set')}
Market regime: {(brain.get('market_regime') or {}).get('regime', 'unknown')}
ML active: {brain.get('ml_active', False)} | Win rate: {brain.get('win_rate', 0)}%
ETH: ${prices.get('ETH', 0):.2f} | BTC: ${prices.get('BTC', 0):.2f} | BNB: ${prices.get('BNB', 0):.2f}
[/BOT_STATUS]"""
        except Exception:
            return "[BOT_STATUS]Status unavailable[/BOT_STATUS]"


# Singleton
_chat: Optional[NexusChat] = None


def get_chat_engine() -> NexusChat:
    global _chat
    if _chat is None:
        _chat = NexusChat()
    return _chat
