# cxr-agent/tools/tts.py
"""
ElevenLabs text-to-speech for the optional CXR voice mode.

This is a plain helper, NOT an agent tool: voice is a deterministic post-step
driven by main.py (a runtime/deployment concern), not something the model
decides to call. It synthesises ONE designated piece of text — the orchestrator-
authored `spoken_summary`, or a follow-up answer — and never the chain-of-thought.

Config (all overridable):
    ELEVENLABS_API_KEY    required. Your ElevenLabs API key.
    ELEVENLABS_VOICE_ID   default voice id (else pass voice_id=).
    ELEVENLABS_MODEL_ID   TTS model id; defaults below. Verify the current id on
                          your ElevenLabs dashboard — model names change.

Uses stdlib urllib in a worker thread to avoid blocking the asyncio loop and to
avoid adding an HTTP dependency.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
_DEFAULT_MODEL = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")


def _synthesize_blocking(text: str, voice_id: str, model_id: str, api_key: str) -> bytes:
    body = json.dumps({
        "text": text,
        "model_id": model_id,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }).encode("utf-8")
    req = urllib.request.Request(
        _TTS_URL.format(voice_id=voice_id),
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"ElevenLabs HTTP {e.code}: {detail}") from e


async def run_tts(
    text: str,
    voice_id: str | None = None,
    out_path: str | None = None,
    model_id: str | None = None,
) -> str:
    """
    Synthesise `text` to an MP3 and return the file path.

    Args:
        text: The (already-vetted) text to speak. Caller is responsible for
              what goes in here — never pass the model's `thinking`.
        voice_id: ElevenLabs voice id; falls back to ELEVENLABS_VOICE_ID.
        out_path: Output .mp3 path; defaults to reports/narration_<ts>.mp3.
        model_id: TTS model id; falls back to ELEVENLABS_MODEL_ID / default.

    Returns:
        Filesystem path to the written .mp3.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("run_tts: empty text")

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")

    voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
    if not voice_id:
        raise RuntimeError(
            "No ElevenLabs voice id (pass voice_id= or set ELEVENLABS_VOICE_ID)"
        )
    model_id = model_id or _DEFAULT_MODEL

    audio = await asyncio.to_thread(_synthesize_blocking, text, voice_id, model_id, api_key)

    if out_path is None:
        out_path = f"reports/narration_{int(time.time())}.mp3"
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(audio)
    return str(p)
