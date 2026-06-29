# cxr-agent/tools/encode_image.py
"""
CXR Image Encoding Tool
=======================
Prepares a CXR image for submission to the NV-Reason-CXR-3B MCP server.

Resizes to a specified maximum side length and encodes as base64 PNG.
The default of 1280px prevents garbled output from Qwen2.5-VL's smart_resize,
but the agent can pass a smaller value for faster inference or a larger value
if fine detail is required.

Data classes are defined in models/pipeline.py — none are declared here.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

DEFAULT_MAX_SIDE = 1280


def encode_image_for_reasoning(
    image_path: str,
    max_side: int = DEFAULT_MAX_SIDE,
) -> tuple[str, str]:
    """
    Load, resize, and base64-encode a CXR image for the reasoning MCP tool.

    Resizes the image so the longest side is at most max_side pixels, converts
    to RGB PNG, and returns the base64 string alongside the image_id (filename
    stem), ready to pass directly to reason_cxr or analyze_cxr.

    Args:
        image_path: Path to the image file (JPEG or PNG).
        max_side:   Maximum pixels on the longest side. Default 1280.
                    Use smaller values (e.g. 512, 768) for faster inference.
                    Use larger values (e.g. 1536) only if fine detail is needed
                    and the model version supports it.

    Returns:
        (image_b64, image_id) where image_b64 is a base64-encoded PNG string
        and image_id is the filename stem (e.g. "cxr_demo").

    Raises:
        FileNotFoundError: If the image file does not exist.
        ValueError: If max_side is less than 64 or greater than 2048.
    """
    if not 64 <= max_side <= 2048:
        raise ValueError(f"max_side must be between 64 and 2048, got {max_side}")

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    img = Image.open(path).convert("RGB")
    w, h = img.size

    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    image_id = path.stem

    return image_b64, image_id
