# cxr-agent/tools/sandbox_overlay.py
"""
tools/sandbox_overlay.py
========================
On-demand CXR finding-overlay rendering in a sandbox.

This is the CXR analogue of OCT's ``tools/sandbox_overlay.py``. Where OCT
overlays a 13-class retinal layer map on a B-scan, this overlays the
**localized findings** (bounding boxes from NV-Locate-Anything-3B, colour-coded
by severity) on the chest X-ray, with per-finding toggles and an opacity slider.

Two paths, exactly as in OCT:

* **On-demand** (``render_finding_overlay``): a normal ``@function_tool`` the
  agent calls when a visual is warranted. It provisions a sandbox lazily on
  first use, stages the inputs, runs ``render_overlay.py`` *inside* the sandbox,
  and collects the resulting HTML back to the host. Because the tool drives the
  sandbox internally, this path works with **any** model — including Claude via
  the LiteLLM bridge — with no sandbox/model-compatibility concerns.

* **Model-driven** (``stage_overlay`` + ``build_orchestrator_sandbox``): the
  agent itself gets shell + filesystem access and authors its own
  visualization scripts. ``stage_overlay`` only places the inputs in the live
  workspace; the agent then runs the standard renderer *or* writes a bespoke
  one. This is what delivers "custom visualizations on the fly".

The CXR image stays on disk and is addressed by path; the finding boxes are
small and pass as a JSON string. Box coordinates are **normalized to [0, 1]
host-side** before staging, so the in-browser renderer never has to reason about
pixel spaces — it just multiplies by the displayed canvas size.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from agents import RunContextWrapper, function_tool
from agents.sandbox import Manifest
from agents.sandbox.capabilities import Capabilities
from agents.sandbox.entries import Dir, LocalDir
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient

from .context import CXRContext

logger = logging.getLogger("cxr.overlay")

# ── Severity colour LUT ──────────────────────────────────────────────────────
# Per-severity RGB used for box stroke + translucent fill. "unknown" is a muted
# slate so unscored boxes stay visible without implying a grade.
SEVERITY_COLORS: dict[str, list[int]] = {
    "mild": [88, 178, 96],       # green
    "moderate": [240, 170, 60],  # amber
    "severe": [235, 75, 75],     # red
    "unknown": [128, 148, 168],  # slate
}
SEVERITY_ORDER = ["severe", "moderate", "mild", "unknown"]
DEFAULT_COLOR = [128, 148, 168]

WORKSPACE_SKILL_PATH = "skills/cxr-overlay"
INPUTS_DIR = "inputs"
OUTPUT_DIR = "output"

# Default location of the overlay skill (works even without explicit wiring).
DEFAULT_OVERLAY_SKILL_DIR = Path(__file__).resolve().parents[1] / "sandbox" / "cxr-overlay"

# Cap the size of the staged PNG so the self-contained HTML stays light. Boxes
# are normalized, so the display size is cosmetic — this only bounds file size.
OVERLAY_IMAGE_MAX_SIDE = 1024


# ── LUT / manifest / capabilities ────────────────────────────────────────────

def severity_lut() -> dict[str, Any]:
    """Colour lookup table consumed by the renderer (names + per-severity RGB)."""
    return {
        "names": SEVERITY_ORDER,
        "colors": SEVERITY_COLORS,
        "default": DEFAULT_COLOR,
    }


def build_overlay_manifest(skill_dir: Path) -> Manifest:
    """Manifest staging the overlay skill + empty inputs/output dirs."""
    return Manifest(entries={
        WORKSPACE_SKILL_PATH: LocalDir(src=Path(skill_dir)),
        INPUTS_DIR: Dir(),
        OUTPUT_DIR: Dir(),
    })


def overlay_capabilities() -> list:
    """Capabilities for the (advanced) model-driven SandboxAgent path."""
    return Capabilities.default()


# ── Lazy sandbox lifecycle ───────────────────────────────────────────────────

class SandboxManager:
    """Lazily provisions a sandbox session the first time it's needed.

    No session (and on hosted providers, no container) is created until
    ``ensure()`` is first awaited. Swap ``client_factory`` to target Docker or a
    hosted provider; the rest is unchanged. Mirrors OCT's ``SandboxManager``.
    """

    def __init__(self, skill_dir: Path = DEFAULT_OVERLAY_SKILL_DIR,
                 client_factory: Callable[[], Any] = UnixLocalSandboxClient):
        self.skill_dir = Path(skill_dir)
        self._client_factory = client_factory
        self._client: Any = None
        self._session: Any = None

    @property
    def active(self) -> bool:
        return self._session is not None

    async def ensure(self) -> Any:
        if self._session is None:
            self._client = self._client_factory()
            self._session = await self._client.create(
                manifest=build_overlay_manifest(self.skill_dir))
            await self._session.apply_manifest()
            logger.info("sandbox session provisioned (%s)", type(self._client).__name__)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.aclose()
            self._session = None


# ── Region normalization + input staging ─────────────────────────────────────

def _coerce_payload(findings: Any) -> dict[str, Any]:
    """Accept a JSON string or dict; return a dict with at least ``regions``.

    Tolerated shapes:
      * LocalizationResult-like: {"regions": [{"finding","bbox":{x,y,w,h},"score"}], ...}
      * A bare list of region dicts.
      * A combined payload that also carries "impression"/"findings"/"image_id".
    """
    if isinstance(findings, str):
        try:
            data = json.loads(findings) if findings.strip() else {}
        except json.JSONDecodeError:
            data = {}
    else:
        data = findings or {}
    if isinstance(data, list):
        data = {"regions": data}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("regions", [])
    return data


def _looks_normalized(vals: list[float]) -> bool:
    """Heuristic: treat boxes as already normalized if every value is in [0, ~1.5]."""
    return all(0.0 <= v <= 1.5 for v in vals)


def _normalize_regions(regions: list, img_w: int, img_h: int,
                       coord_space: str = "auto") -> list[dict[str, Any]]:
    """Return regions with bbox expressed as fractions of the image in [0, 1].

    ``coord_space``: "auto" (detect), "absolute" (pixels rel. to the image), or
    "normalized" (already 0–1). Carries severity/score/label/location through.
    """
    out: list[dict[str, Any]] = []
    iw = float(img_w or 1)
    ih = float(img_h or 1)
    for r in regions or []:
        if not isinstance(r, dict):
            continue
        bbox = r.get("bbox") or r          # tolerate flat x/y/w/h on the region
        try:
            x = float(bbox.get("x", 0)); y = float(bbox.get("y", 0))
            w = float(bbox.get("w", 0)); h = float(bbox.get("h", 0))
        except (TypeError, ValueError):
            continue

        space = coord_space
        if space == "auto":
            space = "normalized" if _looks_normalized([x, y, w, h]) else "absolute"
        if space == "absolute":
            x, y, w, h = x / iw, y / ih, w / iw, h / ih

        # Clamp to the frame so a stray box can't paint off-canvas.
        x = min(max(x, 0.0), 1.0); y = min(max(y, 0.0), 1.0)
        w = max(min(w, 1.0 - x), 0.0); h = max(min(h, 1.0 - y), 0.0)

        sev = str(r.get("severity", "unknown") or "unknown").lower()
        if sev not in SEVERITY_COLORS:
            sev = "unknown"
        out.append({
            "finding": str(r.get("finding") or r.get("label") or "finding"),
            "x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5),
            "score": r.get("score"),
            "severity": sev,
            "location": r.get("location"),
            "confidence": r.get("confidence"),
        })
    return out


def _encode_overlay_png(image_path: str) -> tuple[bytes, int, int]:
    """Load + (optionally) downscale the CXR, return (png_bytes, width, height).

    PIL is already a CXR dependency. If PIL is somehow unavailable we fall back
    to the raw file bytes and unknown dimensions (the renderer still works; box
    placement just uses whatever dims we recorded).
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        raw = Path(image_path).read_bytes()
        return raw, 0, 0

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if max(w, h) > OVERLAY_IMAGE_MAX_SIDE:
        scale = OVERLAY_IMAGE_MAX_SIDE / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), img.size[0], img.size[1]


async def _stage_inputs(session: Any, image_path: str, findings: Any,
                        image_id: Optional[str] = None,
                        coord_space: str = "auto") -> dict[str, Any]:
    """Write inputs/{cxr.png,findings.json,lut.json} into the sandbox workspace.

    Returns the staged findings payload (for the caller to learn image_id etc.).
    """
    payload = _coerce_payload(findings)
    png_bytes, png_w, png_h = _encode_overlay_png(image_path)

    # Box coordinates are normalized against the *original* image dimensions when
    # absolute; for normalized inputs the source dims don't matter. We record the
    # staged PNG's dims for the viewer's aspect ratio.
    src_w = int(payload.get("image_width") or png_w or 0)
    src_h = int(payload.get("image_height") or png_h or 0)
    regions = _normalize_regions(payload.get("regions", []), src_w, src_h, coord_space)

    iid = image_id or payload.get("image_id") or Path(image_path).stem
    staged = {
        "image_id": str(iid),
        "image_width": png_w,
        "image_height": png_h,
        "coords": "normalized",
        "regions": regions,
        "impression": payload.get("impression", ""),
        "findings": payload.get("findings", []),
    }

    await session.mkdir(INPUTS_DIR, parents=True)
    await session.write(f"{INPUTS_DIR}/cxr.png", io.BytesIO(png_bytes))
    await session.write(f"{INPUTS_DIR}/findings.json", io.BytesIO(json.dumps(staged).encode()))
    await session.write(f"{INPUTS_DIR}/lut.json", io.BytesIO(json.dumps(severity_lut()).encode()))
    return staged


async def collect_overlay_outputs(session: Any, out_dir: Path) -> list[str]:
    """Read every HTML file from the sandbox ``output/`` dir back to the host."""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    try:
        entries = await session.ls(OUTPUT_DIR)
    except Exception as e:  # noqa: BLE001
        logger.info("no overlay output to collect: %s", e)
        return saved
    for entry in entries:
        name = Path(entry.path).name
        if name.endswith(".html"):
            data = await session.read(f"{OUTPUT_DIR}/{name}")
            (out_dir / name).write_bytes(data.read())
            saved.append(name)
    return saved


def _resolve_out_dir(octx: Optional[CXRContext]) -> Path:
    import os
    if octx is not None and getattr(octx, "output_dir", None):
        return Path(octx.output_dir)
    return Path(os.environ.get("CXR_OUT_DIR", "/tmp/cxr_out"))


# ── The on-demand tool (agent decides) ───────────────────────────────────────

@function_tool
async def render_finding_overlay(
    ctx: RunContextWrapper[CXRContext],
    image_path: str,
    findings_json: str,
    coord_space: str = "auto",
) -> str:
    """Render an interactive HTML overlay of localized findings on the chest X-ray.

    Call this ONLY when a visual would genuinely help — e.g. the user asked to
    *see* where the findings are, or the localized findings are worth showing.
    It provisions a sandbox on first use, renders inside it, and writes
    output_dir/overlay_<image_id>.html. Returns the saved file path(s) as JSON.

    Args:
        image_path: Path to the CXR image file (the same path passed to triage).
        findings_json: JSON string of the localization result. Either a
            LocalizationResult ({"regions":[{"finding","bbox":{x,y,w,h},"score",
            "severity"?}], "impression"?}) or a bare list of such regions. Box
            coordinates may be absolute pixels or normalized [0,1].
        coord_space: "auto" (default — detect), "absolute", or "normalized".
    """
    octx: Optional[CXRContext] = ctx.context
    mgr: Optional[SandboxManager] = getattr(octx, "sandbox", None) if octx else None
    if mgr is None:
        mgr = SandboxManager()
        if octx is not None:
            octx.sandbox = mgr

    try:
        session = await mgr.ensure()
        staged = await _stage_inputs(session, image_path, findings_json,
                                     coord_space=coord_space)
        r = await session.exec(
            "python", f"{WORKSPACE_SKILL_PATH}/render_overlay.py", INPUTS_DIR, OUTPUT_DIR)
        if getattr(r, "exit_code", 1) != 0:
            return json.dumps({"success": False,
                               "error": (r.stderr or b"").decode("utf-8", "replace")[:500]})
        out_dir = _resolve_out_dir(octx)
        saved = await collect_overlay_outputs(session, out_dir)
        if octx is not None:
            for name in saved:
                if name not in octx.overlays:
                    octx.overlays.append(name)
        return json.dumps({
            "success": True,
            "image_id": staged["image_id"],
            "n_regions": len(staged["regions"]),
            "overlay_files": saved,
            "output_dir": str(out_dir),
        })
    except Exception as e:  # noqa: BLE001
        logger.error("render_finding_overlay failed: %s", e, exc_info=True)
        return json.dumps({"success": False, "error": str(e)})


# ── Advanced path: stage-only tool for the model-driven SandboxAgent ─────────

@function_tool
async def stage_overlay(
    ctx: RunContextWrapper[CXRContext],
    image_path: str,
    findings_json: str,
    coord_space: str = "auto",
) -> str:
    """(Advanced/SandboxAgent path) Stage overlay inputs into the live workspace.

    Writes inputs/{cxr.png,findings.json,lut.json}. Then, via shell, run the
    standard renderer:
        python skills/cxr-overlay/render_overlay.py inputs output
    or author your own script against the same inputs (see skills/cxr-overlay/
    SKILL.md for the data contract) and write it to output/<name>.html.

    Args mirror ``render_finding_overlay``.
    """
    octx: Optional[CXRContext] = ctx.context
    session = getattr(octx, "sandbox_session", None) if octx else None
    if session is None:
        return json.dumps({"success": False,
                           "error": "No live sandbox session (model-driven path)."})
    staged = await _stage_inputs(session, image_path, findings_json, coord_space=coord_space)
    return json.dumps({
        "success": True,
        "image_id": staged["image_id"],
        "n_regions": len(staged["regions"]),
        "render_command": f"python {WORKSPACE_SKILL_PATH}/render_overlay.py {INPUTS_DIR} {OUTPUT_DIR}",
        "expected_output": f"{OUTPUT_DIR}/overlay_{staged['image_id']}.html",
    })
