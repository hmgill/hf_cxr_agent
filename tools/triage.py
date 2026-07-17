# cxr-agent/tools/triage.py
"""
CXR Triage Tool
===============
Validates a medical image before downstream CXR analysis.

Checks:
  1. Whether the image is a chest X-ray (vs other modalities / photographs)
  2. CXR projection / orientation (PA, AP, lateral, oblique)
  3. Image quality issues (rotation, exposure, motion, clipping, etc.)

Uses Claude claude-opus-4-6 vision via the Anthropic SDK.
Data classes are defined in models/pipeline.py — none are declared here.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import anthropic

from models.pipeline import TriageResult


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TRIAGE_SYSTEM_PROMPT = """\
You are a radiology quality-control AI. Your only task is to triage a medical \
image before analysis. You must respond with ONLY a valid JSON object — no \
markdown fences, no preamble, no explanation outside the JSON.

Evaluate the image on three dimensions:

1. MODALITY — Is this a chest X-ray? If not (CT, MRI, ultrasound, photograph, \
other radiograph type), set is_chest_xray=false, valid=false, \
orientation="not_applicable".

2. ORIENTATION — If it is a CXR, classify the projection:
   - "PA": posteroanterior (patient upright, X-ray from back to front, \
heart normal size, scapulae outside lung fields)
   - "AP": anteroposterior (portable/supine, heart appears magnified, \
scapulae overlap lungs)
   - "lateral": side view, spine posterior, sternum anterior
   - "oblique": intermediate projection
   - "unknown": cannot be determined

3. QUALITY — Identify any of these issues (use the exact tag strings):
   rotation, low_exposure, overexposure, motion_blur, clipping, \
foreign_object, poor_inspiration, artefact

   Then assign quality_grade:
   - "acceptable": no significant issues
   - "suboptimal": one or more minor issues; analysis can proceed \
with reduced confidence
   - "non_diagnostic": image cannot be meaningfully analysed

Set valid=true only if is_chest_xray=true AND quality_grade != "non_diagnostic".

Respond with exactly this JSON structure (no extra fields):
{
  "valid": <bool>,
  "is_chest_xray": <bool>,
  "orientation": "<string>",
  "quality_issues": [<string>, ...],
  "quality_grade": "<string>",
  "triage_notes": "<string>"
}
"""

TRIAGE_USER_PROMPT = "Please triage this medical image and return the JSON result."


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def _load_image_b64(image_path: str) -> tuple[str, str]:
    """Return (base64_data, media_type) for the given image path."""
    path = Path(image_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_type_map.get(path.suffix.lower(), "image/jpeg")
    b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return b64, media_type


# ---------------------------------------------------------------------------
# Core triage function
# ---------------------------------------------------------------------------

async def run_triage(image_path: str) -> TriageResult:
    """
    Run image triage using Claude claude-opus-4-6 vision.

    Args:
        image_path: Path to the image file (JPEG or PNG).

    Returns:
        TriageResult with validity, orientation, quality, and notes.

    Raises:
        FileNotFoundError: If the image file does not exist.
        ValueError: If the model returns malformed JSON.
    """
    b64_data, media_type = _load_image_b64(image_path)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        system=TRIAGE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": TRIAGE_USER_PROMPT,
                    },
                ],
            }
        ],
    )

    raw_text = message.content[0].text.strip()

    # Strip accidental markdown fences (defensive)
    if raw_text.startswith("```"):
        raw_text = "\n".join(
            line for line in raw_text.splitlines()
            if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Triage model returned non-JSON response: {raw_text!r}"
        ) from exc

    return TriageResult(**data)


# ---------------------------------------------------------------------------
# Sync wrapper (for use from non-async contexts)
# ---------------------------------------------------------------------------

def run_triage_sync(image_path: str) -> TriageResult:
    """Synchronous wrapper around run_triage."""
    import asyncio
    return asyncio.run(run_triage(image_path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="CXR Triage — image validation")
    parser.add_argument("image", help="Path to the image file")
    args = parser.parse_args()

    try:
        result = run_triage_sync(args.image)
        print(result.model_dump_json(indent=2))
        sys.exit(0 if result.valid else 1)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
