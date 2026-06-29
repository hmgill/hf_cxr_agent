#!/usr/bin/env python3
"""
render_overlay.py — runs INSIDE the sandbox.

Assembles a single self-contained HTML overlay from the three staged input files
(no third-party deps — all drawing happens in the browser, so the sandbox needs
no image libraries):

    inputs/cxr.png         the (possibly downscaled) chest X-ray
    inputs/findings.json   {"image_id":..., "image_width":W, "image_height":H,
                            "coords":"normalized",
                            "regions":[{"finding","x","y","w","h","score",
                                        "severity","location","confidence"}],
                            "impression":"...", "findings":[...]}
    inputs/lut.json        {"names":[severities], "colors":{severity:[r,g,b]},
                            "default":[r,g,b]}

Region x/y/w/h are fractions of the image in [0, 1] (the host normalizes them),
so the viewer simply multiplies by the displayed canvas size.

Usage:
    python skills/cxr-overlay/render_overlay.py <inputs_dir> <output_dir> [template]

Writes <output_dir>/overlay_<image_id>.html and prints its path.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _media_type(png_bytes: bytes) -> str:
    if png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if png_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


def _esc(text: str) -> str:
    """Minimal HTML escape for the impression text injected into markup."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: render_overlay.py <inputs_dir> <output_dir> [template]", file=sys.stderr)
        return 2

    inputs = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    template_path = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / "overlay_template.html"
    out_dir.mkdir(parents=True, exist_ok=True)

    cxr = (inputs / "cxr.png").read_bytes()
    meta = json.loads((inputs / "findings.json").read_text())
    lut = json.loads((inputs / "lut.json").read_text())

    image_id = str(meta.get("image_id", "scan"))
    img_w = int(meta.get("image_width") or 0)
    img_h = int(meta.get("image_height") or 0)
    regions = meta.get("regions", [])
    impression = meta.get("impression", "") or ""

    b64 = base64.b64encode(cxr).decode("ascii")
    html = template_path.read_text()
    repl = {
        "__IMAGE_ID__": image_id,
        "__MEDIA_TYPE__": _media_type(cxr),
        "__CXR_B64__": b64,
        "__IMG_W__": str(img_w),
        "__IMG_H__": str(img_h),
        "__REGIONS_JSON__": json.dumps(regions),
        "__LUT_JSON__": json.dumps(lut),
        "__N_REGIONS__": str(len(regions)),
        "__IMPRESSION__": _esc(impression),
    }
    for k, v in repl.items():
        html = html.replace(k, v)

    out_path = out_dir / f"overlay_{image_id}.html"
    out_path.write_text(html)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
