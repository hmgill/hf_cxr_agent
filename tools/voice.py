# cxr-agent/tools/voice.py
"""
tools/voice.py
==============
ElevenLabs text-to-speech for the CXR agent — a single flat module (the project
keeps ``tools/`` flat, no subpackages).

The orchestrator's ``speak_findings`` tool calls :func:`run_tts` to turn a short
findings summary (typically the *impression*) into an ``.mp3`` the UI can play.

Design notes
------------
* **Free-tier friendly.** Defaults to a *premade* ElevenLabs voice (no voice
  cloning, which the free tier disallows) and the multilingual v2 model. Both
  work on a free key. Override the voice with a name from :data:`PREMADE_VOICES`
  or a raw voice ID, and the model via ``ELEVENLABS_MODEL_ID``.
* **Lazy + optional.** The ``elevenlabs`` SDK is imported inside the function so
  the dependency stays optional; importing this module never requires it. If the
  package or ``ELEVENLABS_API_KEY`` is missing, :func:`run_tts` raises a
  ``RuntimeError`` with a clear message — the calling tool turns that into a
  friendly note rather than crashing the turn.
* **Non-blocking.** The SDK call is synchronous, so it runs in a worker thread
  via ``anyio.to_thread`` to avoid stalling the event loop.

Environment:
    ELEVENLABS_API_KEY    required for synthesis (free key is fine)
    ELEVENLABS_MODEL_ID   optional, default "eleven_multilingual_v2"
    ELEVENLABS_VOICE_ID   optional default voice (name or raw ID)
    CXR_OUT_DIR           optional output dir (default /tmp/cxr_out)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Long-standing ElevenLabs *premade* voices (available on the free tier). Map a
# few friendly names to their stable voice IDs; callers may also pass a raw ID.
PREMADE_VOICES: dict[str, str] = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",   # calm, neutral — default clinical read
    "aria":   "9BWtsMINqrJLrRacOk9x",
    "sarah":  "EXAVITQu4vr4xnSDxMaL",
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "bill":   "pqHfZKP75CvOlQylNhV4",
}

DEFAULT_VOICE_NAME = "rachel"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
# Standard MP3 output supported on the free tier.
OUTPUT_FORMAT = "mp3_44100_128"


def _resolve_voice_id(voice_id: str | None) -> str:
    """Map a friendly name / sentinel / raw ID to an ElevenLabs voice ID."""
    # Caller passed nothing or the sentinel "default" → env override or default.
    if not voice_id or voice_id == "default":
        env = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
        voice_id = env or DEFAULT_VOICE_NAME
    key = voice_id.strip().lower()
    if key in PREMADE_VOICES:
        return PREMADE_VOICES[key]
    # Assume it's already a raw ElevenLabs voice ID.
    return voice_id.strip()


def _resolve_out_dir(out_dir: str | None) -> Path:
    base = out_dir or os.environ.get("CXR_OUT_DIR", "/tmp/cxr_out")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _synthesize(text: str, voice_id: str, model_id: str, api_key: str) -> bytes:
    """Blocking ElevenLabs call → MP3 bytes. Runs in a worker thread."""
    try:
        from elevenlabs.client import ElevenLabs
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "The 'elevenlabs' package is not installed. Install the voice extra "
            "(pip install 'cxr-agent[voice]' or pip install elevenlabs)."
        ) from e

    client = ElevenLabs(api_key=api_key)
    # convert() returns an iterator of byte chunks; join into a single buffer.
    stream = client.text_to_speech.convert(
        voice_id=voice_id,
        model_id=model_id,
        text=text,
        output_format=OUTPUT_FORMAT,
    )
    return b"".join(chunk for chunk in stream if chunk)


async def run_tts(
    summary_text: str,
    voice_id: str = "default",
    out_dir: str | None = None,
) -> str:
    """
    Synthesize ``summary_text`` to an MP3 and return its filesystem path.

    Args:
        summary_text: The text to speak (typically the impression / summary).
        voice_id: A name from :data:`PREMADE_VOICES`, a raw ElevenLabs voice ID,
            or ``"default"`` to use ``ELEVENLABS_VOICE_ID`` / the built-in default.
        out_dir: Directory to write the MP3 into (default ``CXR_OUT_DIR``).

    Returns:
        Path to the written ``.mp3`` file.

    Raises:
        RuntimeError: if the text is empty, the SDK is missing, or no API key is
            configured. Callers should catch this and degrade gracefully.
    """
    text = (summary_text or "").strip()
    if not text:
        raise RuntimeError("Nothing to speak — the summary text was empty.")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set — voice synthesis is unavailable."
        )

    resolved_voice = _resolve_voice_id(voice_id)
    model_id = os.environ.get("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID).strip() \
        or DEFAULT_MODEL_ID

    import anyio
    audio = await anyio.to_thread.run_sync(
        _synthesize, text, resolved_voice, model_id, api_key
    )
    if not audio:
        raise RuntimeError("ElevenLabs returned no audio for the given text.")

    # Deterministic-ish filename keyed on the text + voice so repeats don't pile up.
    stem = hashlib.sha1(f"{resolved_voice}:{text}".encode("utf-8")).hexdigest()[:12]
    path = _resolve_out_dir(out_dir) / f"cxr_speech_{stem}.mp3"
    path.write_bytes(audio)
    return str(path)
