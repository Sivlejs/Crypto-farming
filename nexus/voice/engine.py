"""
Nexus AI Voice Engine.

Architecture
------------
- **Input (Speech → Text)**: Handled entirely in the browser using the
  Web Speech API (SpeechRecognition). No server-side dependency needed.
  The transcript is sent to ``/api/chat`` as a regular JSON POST.

- **Output (Text → Speech)**: Two-tier system:
    1. ElevenLabs neural TTS (if ``ELEVENLABS_API_KEY`` is set) — high quality
    2. Browser speechSynthesis fallback (zero API key, works everywhere)
  The TTS endpoint returns either audio bytes or a plain-text marker so
  the client knows to use browser synthesis.

Usage
-----
The voice engine is intentionally lightweight on the server side.  All
real-time speech capture and most TTS happen in the browser; this module
provides the server-side TTS integration for premium voice output.
"""
from __future__ import annotations

import io
import time
from typing import Optional

import requests

from nexus.utils.config import Config
from nexus.utils.logger import get_logger

logger = get_logger(__name__)

# ── ElevenLabs constants ──────────────────────────────────────────────────────

_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# Default voice: "Rachel" — clear, neutral, professional
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# ── Simple rate-limit guard (max 1 TTS req / second) ─────────────────────────
_last_tts_time: float = 0.0
_TTS_MIN_GAP = 1.0


class VoiceEngine:
    """
    Server-side voice engine for Nexus AI.

    Responsibilities:
      - text_to_speech(text) → bytes | None
          Returns raw MP3 audio bytes from ElevenLabs, or None when
          falling back to browser synthesis.
      - status() → dict
          Returns current TTS configuration info for the dashboard.
    """

    def __init__(self):
        self._elevenlabs_key = Config.ELEVENLABS_API_KEY
        self._voice_id = Config.ELEVENLABS_VOICE_ID or _DEFAULT_VOICE_ID
        if self._elevenlabs_key:
            logger.info("VoiceEngine: ElevenLabs TTS enabled (voice=%s)", self._voice_id)
        else:
            logger.info(
                "VoiceEngine: ElevenLabs not configured — browser speechSynthesis will be used. "
                "Set ELEVENLABS_API_KEY for premium neural TTS."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def text_to_speech(self, text: str) -> Optional[bytes]:
        """
        Convert *text* to speech audio.

        Returns:
            MP3 bytes from ElevenLabs, or ``None`` to signal the caller to
            use browser-side synthesis instead.
        """
        if not self._elevenlabs_key:
            return None  # client will use browser speechSynthesis

        # Trim very long text to keep latency low
        if len(text) > 500:
            text = text[:497] + "…"

        # Rate-limit guard
        global _last_tts_time
        gap = time.monotonic() - _last_tts_time
        if gap < _TTS_MIN_GAP:
            time.sleep(_TTS_MIN_GAP - gap)

        try:
            url = _ELEVENLABS_TTS_URL.format(voice_id=self._voice_id)
            resp = requests.post(
                url,
                json={
                    "text": text,
                    "model_id": "eleven_turbo_v2",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                        "style": 0.2,
                        "use_speaker_boost": True,
                    },
                },
                headers={
                    "xi-api-key": self._elevenlabs_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                timeout=15,
            )
            resp.raise_for_status()
            _last_tts_time = time.monotonic()
            logger.debug("ElevenLabs TTS: %d bytes for %d chars", len(resp.content), len(text))
            return resp.content
        except Exception as exc:
            logger.warning("ElevenLabs TTS failed (%s) — client fallback", exc)
            return None

    def status(self) -> dict:
        return {
            "tts_provider":   "elevenlabs" if self._elevenlabs_key else "browser",
            "voice_id":       self._voice_id if self._elevenlabs_key else "browser-default",
            "elevenlabs_key": bool(self._elevenlabs_key),
            "wake_word":      Config.VOICE_WAKE_WORD,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_engine: Optional[VoiceEngine] = None


def get_voice_engine() -> VoiceEngine:
    global _engine
    if _engine is None:
        _engine = VoiceEngine()
    return _engine
