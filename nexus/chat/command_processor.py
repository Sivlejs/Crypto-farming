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
_PATTERNS: list[tuple[str, list[str]]] = [
    ("start",        [r"\bstart\b", r"\brun\b", r"\blaunch\b", r"\bbegin\b", r"\bturn on\b"]),
    ("stop",         [r"\bstop\b", r"\bpause\b", r"\bhalt\b", r"\bturn off\b", r"\bshutdown\b"]),
    ("profit",       [r"\bprofit\b", r"\bearning\b", r"\bearned\b", r"\bmoney\b", r"\bhow much\b", r"\bbalance\b"]),
    ("trades",       [r"\btrades?\b", r"\btransaction\b", r"\bhistory\b", r"\bexecut"]),
    ("opportunities",[ r"\bopportunit", r"\bfind\b", r"\bscanning\b", r"\bwhat.*see\b"]),
    ("payout",       [r"\bpayout\b", r"\bsweep\b", r"\bwithdraw\b", r"\bsend.*coinbase\b", r"\bsend.*cash app\b", r"\btransfer\b"]),
    ("prices",       [r"\bprice\b", r"\bmarket\b", r"\beth\b.*\bworth\b", r"\bbtc\b", r"\bcrypto.*price"]),
    ("regime",       [r"\bregime\b", r"\bmarket.*condition\b", r"\bvolatil", r"\btrend"]),
    ("brain",        [r"\bbrain\b", r"\blearn", r"\bmodel\b", r"\baccurac", r"\bml\b", r"\bai.*status\b"]),
    ("status",       [r"\bstatus\b", r"\bhow.*going\b", r"\bwhat.*doing\b", r"\bupdate\b", r"\bcheck in\b"]),
    ("set_dry_run",  [r"\bsimulat", r"\bdry.?run\b", r"\btest mode\b", r"\blive mode\b", r"\breal trade"]),
    ("help",         [r"\bhelp\b", r"\bwhat can\b", r"\bcommands?\b", r"\bwhat.*do\b"]),
]

_COMPILED = [
    (intent, [re.compile(p, re.I) for p in patterns])
    for intent, patterns in _PATTERNS
]


def parse_command(text: str) -> ParsedCommand:
    """Parse a natural language string into a structured command."""
    text = text.strip()
    for intent, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(text):
                params = {}
                if intent == "set_dry_run":
                    params["dry_run"] = not bool(re.search(r"live|real", text, re.I))
                return ParsedCommand(intent=intent, params=params, raw=text)
    return ParsedCommand(intent="unknown", raw=text)
