"""
Nexus AI Command Processor.

Parses natural-language voice or text commands into structured intents
without requiring an LLM API key.  Used as a fast first-pass before
routing to the LLM for complex queries.

Supported intents
-----------------
  status          → get bot status
  start           → start bot
  stop            → stop bot
  profit          → show total profit
  trades          → show trade history
  opportunities   → show current opportunities
  payout          → trigger payout / show payout info
  prices          → show current market prices
  regime          → show market regime
  brain           → show AI brain / learning status
  set_dry_run     → toggle simulation mode
  set_min_profit  → set minimum profit threshold
  set_gas_limit   → set maximum gas price
  set_slippage    → set slippage tolerance
  set_threshold   → set payout threshold
  set_coinbase    → configure Coinbase credentials
  set_payout_addr → set payout address
  settings        → show current settings
  help            → list commands
  unknown         → fallback
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ParsedCommand:
    intent: str
    params: dict = field(default_factory=dict)
    raw: str = ""


# Intent patterns: (intent_name, [regex patterns that trigger it])
# NOTE: Order matters! More specific patterns should come before general ones.
_PATTERNS: list[tuple[str, list[str]]] = [
    # Settings commands (most specific - check first)
    ("set_min_profit", [r"\bset.*(?:min|minimum).*profit\b", r"\bmin.*profit.*(?:to|\d)", r"\bprofit.*threshold.*(?:to|\d)"]),
    ("set_gas_limit", [r"\bset.*gas\b", r"\bgas.*(?:limit|max|price).*(?:to|\d)", r"\bmax.*gas.*(?:to|\d)"]),
    ("set_slippage", [r"\bset.*slippage\b", r"\bslippage.*(?:to|\d)", r"\bslip.*tolerance"]),
    ("set_threshold", [r"\bset.*threshold\b", r"\bpayout.*threshold.*(?:to|\d)", r"\bsweep.*threshold"]),
    ("set_coinbase", [r"\bcoinbase.*(?:key|api|cred|set)", r"\bsetup.*coinbase\b", r"\bconnect.*coinbase\b", r"\bconfigure.*coinbase\b"]),
    ("set_payout_addr", [r"\bpayout.*address\b", r"\bwallet.*address\b", r"\bdestination.*address\b"]),
    ("set_dry_run",  [r"\bsimulat", r"\bdry.?run\b", r"\btest mode\b", r"\blive mode\b", r"\breal trade"]),
    ("settings",     [r"\bsettings?\b", r"\bshow.*config", r"\bshow.*option", r"\bcurrent.*settings?"]),
    
    # Bot control commands
    ("start",        [r"\bstart\b", r"\brun\b", r"\blaunch\b", r"\bbegin\b", r"\bturn on\b"]),
    ("stop",         [r"\bstop\b", r"\bpause\b", r"\bhalt\b", r"\bturn off\b", r"\bshutdown\b"]),
    
    # Info queries (general patterns - check last)
    ("profit",       [r"\b(?:my|show|what.*s).*profit\b", r"\bearning\b", r"\bearned\b", r"\bmoney\b", r"\bhow much\b"]),
    ("trades",       [r"\btrades?\b", r"\btransaction\b", r"\bhistory\b", r"\bexecut"]),
    ("opportunities",[ r"\bopportunit", r"\bfind\b", r"\bscanning\b", r"\bwhat.*see\b"]),
    ("payout",       [r"\bpayout\b", r"\bsweep\b", r"\bwithdraw\b", r"\bsend.*coinbase\b", r"\bsend.*cash app\b", r"\btransfer\b"]),
    ("prices",       [r"\bprice\b", r"\bmarket\b", r"\beth\b.*\bworth\b", r"\bbtc\b", r"\bcrypto.*price"]),
    ("regime",       [r"\bregime\b", r"\bmarket.*condition\b", r"\bvolatil", r"\btrend"]),
    ("brain",        [r"\bbrain\b", r"\blearn", r"\bmodel\b", r"\baccurac", r"\bml\b", r"\bai.*status\b"]),
    ("status",       [r"\bstatus\b", r"\bhow.*going\b", r"\bwhat.*doing\b", r"\bupdate\b", r"\bcheck in\b"]),
    ("help",         [r"\bhelp\b", r"\bwhat can\b", r"\bcommands?\b", r"\bwhat.*do\b"]),
]

_COMPILED = [
    (intent, [re.compile(p, re.I) for p in patterns])
    for intent, patterns in _PATTERNS
]

# Regex to extract numbers from text
_NUMBER_PATTERN = re.compile(r'\$?(\d+(?:\.\d+)?)')


def parse_command(text: str) -> ParsedCommand:
    """Parse a natural language string into a structured command."""
    text = text.strip()
    for intent, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(text):
                params = _extract_params(intent, text)
                return ParsedCommand(intent=intent, params=params, raw=text)
    return ParsedCommand(intent="unknown", raw=text)


def _extract_params(intent: str, text: str) -> dict:
    """Extract parameters from the command text based on intent."""
    params = {}
    text_lower = text.lower()

    if intent == "set_dry_run":
        # Check if user wants live mode or dry run
        params["dry_run"] = not bool(re.search(r"live|real|production", text_lower))

    elif intent in ("set_min_profit", "set_gas_limit", "set_slippage", "set_threshold"):
        # Extract numeric value
        match = _NUMBER_PATTERN.search(text)
        if match:
            params["value"] = float(match.group(1))

    elif intent == "set_payout_addr":
        # Extract Ethereum address
        addr_match = re.search(r'(0x[a-fA-F0-9]{40})', text)
        if addr_match:
            params["address"] = addr_match.group(1)

    elif intent == "set_coinbase":
        # This will be handled interactively - just flag the intent
        params["needs_input"] = True

    return params

