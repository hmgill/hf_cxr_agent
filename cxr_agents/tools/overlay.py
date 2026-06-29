# cxr-agent/tools/overlay.py
"""
Localization Overlay
====================
Draws NV-Locate-Anything bounding boxes onto the CXR and saves an annotated
image. Uses Pillow only (already a dependency) — no OpenCV.

Resize-aware: boxes are scaled from the coordinate frame the localization
server reported (LocalizationResult.image_width/height) onto the ORIGINAL
image's pixel dimensions. If the server already returned original-space
coordinates, that frame equals the image size and the scale is 1.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Distinct outline colours (RGB), cycled per region.
_PALETTE = [
    (0, 200, 80),    # green
    (255, 140, 0),   # orange
    (60, 120, 255),  # blue
    (220, 40, 40),   # red
    (180, 0, 180),   # purple
]


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_localization_overlay(image_path: str, localization, out_path) -> str | None:
    """
    Draw the localization regions onto the image and save to out_path.

    Args:
        image_path: Path to the ORIGINAL image the boxes were computed against.
        localization: A LocalizationResult-like object with `.regions` (each
            with `.bbox.x/.y/.w/.h` and `.finding`) and optional
            `.image_width` / `.image_height` (the box coordinate frame).
        out_path: Where to write the annotated PNG.

    Returns:
        The output path as a string, or None if there was nothing to draw.
    """
    regions = getattr(localization, "regions", None) or []
    if not regions:
        return None

    img = Image.open(image_path).convert("RGB")
    orig_w, orig_h = img.size

    frame_w = getattr(localization, "image_width", None) or orig_w
    frame_h = getattr(localization, "image_height", None) or orig_h
    sx = orig_w / frame_w if frame_w else 1.0
    sy = orig_h / frame_h if frame_h else 1.0

    if (sx, sy) != (1.0, 1.0):
        print(
            f"OVERLAY: scaling boxes from server frame {frame_w}x{frame_h} "
            f"-> image {orig_w}x{orig_h} (sx={sx:.3f}, sy={sy:.3f})",
            file=sys.stderr,
        )
    else:
        print(
            f"OVERLAY: server frame {frame_w}x{frame_h} matches image "
            f"{orig_w}x{orig_h}; no scaling",
            file=sys.stderr,
        )

    draw = ImageDraw.Draw(img, "RGBA")
    line_w = max(2, orig_w // 400)
    font = _font(max(14, orig_w // 70))

    for i, region in enumerate(regions):
        colour = _PALETTE[i % len(_PALETTE)]
        b = region.bbox
        x1, y1 = b.x * sx, b.y * sy
        x2, y2 = (b.x + b.w) * sx, (b.y + b.h) * sy

        # translucent fill + solid outline
        draw.rectangle([x1, y1, x2, y2], fill=colour + (40,), outline=colour, width=line_w)

        label = (getattr(region, "finding", "") or "").split(";")[0].strip()
        if len(label) > 42:
            label = label[:39] + "..."
        if not label:
            continue

        tb = draw.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pad = 3
        # place label above the box if there's room, else just inside the top
        ty = y1 - th - 2 * pad
        if ty < 0:
            ty = y1 + pad
        draw.rectangle([x1, ty, x1 + tw + 2 * pad, ty + th + 2 * pad], fill=colour + (230,))
        draw.text((x1 + pad, ty + pad), label, fill=(255, 255, 255), font=font)

    out = Path(out_path)
    img.save(out, format="PNG")
    return str(out)
