# cxr-agent/tools/localize.py
"""
tools/localize.py
=================
Spatial grounding for CXR findings via **NV-Locate-Anything-3B**.

The orchestrator's ``localize_findings`` tool calls :func:`run_localization`
with the image path and the reasoning report; this module turns the report's
finding labels into open-vocabulary *categories*, asks the LocateAnything-3B
service to locate them, and returns a typed :class:`LocalizationResult` whose
boxes are in **original-image pixel space**.

Transport
---------
LocateAnything-3B is deployed as a FastMCP server (``locate_objects``,
``ground_phrase``, ``detect_text``, ``ground_gui``, ``point_at``) in front of a
Modal A10G worker. The server resizes the image and scales boxes back to the
original resolution, so the caller gets pixel coordinates directly. We reach it
through the Agents SDK's own MCP client (``MCPServerStreamableHttp``) — the same
streamable-HTTP transport the reasoning server uses — rather than hand-rolling
JSON-RPC/SSE.

Design notes
------------
* **Flat module.** Kept as a single ``tools/localize.py`` (the project keeps
  ``tools/`` flat), matching ``tools/voice.py`` and ``tools/triage.py``.
* **Never fatal.** Any infra error (URL down, cold-start timeout, unparseable
  payload) degrades to an *empty* ``LocalizationResult`` and a log line — the
  ``@function_tool`` still returns a valid object so the agent can proceed (the
  overlay simply has nothing to draw). It does not raise.

Environment:
    CXR_LOCALIZE_MCP_URL     localization MCP URL (else built-in default)
    CXR_LOCALIZE_TIMEOUT     per-call read timeout, seconds (default 60)
    CXR_LOCALIZE_MAX_CATS    max categories, one inference each (default 8)
    CXR_LOCALIZE_GEN_MODE    generation mode: hybrid|fast|slow (default hybrid)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from models.pipeline import (
    BoundingBox,
    LocalizationResult,
    LocalizedRegion,
    ReasoningReport,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCALIZE_MCP_URL = "https://nv-locate-anything-3b.fastmcp.app/mcp"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_CATEGORIES = 8
DEFAULT_GEN_MODE = "hybrid"
LOCATE_TOOL = "locate_objects"


# ── Inputs: report → categories, image → base64 ──────────────────────────────

def _derive_categories(reasoning_report_json: str | dict | None,
                       limit: int) -> list[str]:
    """Pull unique finding labels from a reasoning report to localize.

    Tolerant of: a ``ReasoningReport`` JSON object, a bare ``{"findings": [...]}``
    dict, a bare list of findings, or a comma/newline-separated string. Returns a
    de-duplicated, order-preserving list of category phrases (capped at ``limit``).
    """
    findings: list = []
    data: Any = reasoning_report_json

    if isinstance(data, str):
        s = data.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                data = json.loads(s)
            except Exception:  # noqa: BLE001
                data = s
        if isinstance(data, str):
            # Plain text → split on commas / newlines as a last resort.
            parts = [p.strip() for chunk in data.splitlines() for p in chunk.split(",")]
            return _dedup([p for p in parts if p])[:limit]

    if isinstance(data, dict):
        findings = data.get("findings") or []
    elif isinstance(data, list):
        findings = data

    labels: list[str] = []
    for f in findings:
        if isinstance(f, dict):
            label = (f.get("label") or "").strip()
        else:
            label = str(getattr(f, "label", "") or "").strip()
        if label:
            labels.append(label)
    return _dedup(labels)[:limit]


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _encode_image_b64(image_path: str) -> tuple[str, str]:
    """Return (base64 of the original image bytes, image_id stem)."""
    p = Path(image_path)
    data = base64.b64encode(p.read_bytes()).decode("utf-8")
    return data, p.stem


# ── Output: tool JSON → LocalizationResult ───────────────────────────────────

def _as_xyxy(box: Any) -> tuple[float, float, float, float] | None:
    """Coerce a box in various shapes to (x1, y1, x2, y2)."""
    if isinstance(box, dict):
        if all(k in box for k in ("x1", "y1", "x2", "y2")):
            return float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
        if all(k in box for k in ("x", "y", "w", "h")):
            x, y, w, h = (float(box["x"]), float(box["y"]),
                          float(box["w"]), float(box["h"]))
            return x, y, x + w, y + h
        # {"left":..,"top":..,"right":..,"bottom":..}
        if all(k in box for k in ("left", "top", "right", "bottom")):
            return (float(box["left"]), float(box["top"]),
                    float(box["right"]), float(box["bottom"]))
    if isinstance(box, (list, tuple)) and len(box) == 4:
        return float(box[0]), float(box[1]), float(box[2]), float(box[3])
    return None


def _iter_detections(payload: Any):
    """Yield (label, box, score) tuples from a variety of response shapes."""
    # Unwrap common envelopes.
    if isinstance(payload, dict):
        for key in ("detections", "boxes", "objects", "regions", "results"):
            if key in payload:
                inner = payload[key]
                # {"results": {category: [box, ...]}}
                if isinstance(inner, dict):
                    for label, boxes in inner.items():
                        if isinstance(boxes, list):
                            for b in boxes:
                                yield label, _box_of(b), _score_of(b)
                        else:
                            yield label, _box_of(boxes), _score_of(boxes)
                    return
                if isinstance(inner, list):
                    for d in inner:
                        yield _label_of(d), _box_of(d), _score_of(d)
                    return
    if isinstance(payload, list):
        for d in payload:
            yield _label_of(d), _box_of(d), _score_of(d)


def _label_of(d: Any) -> str:
    if isinstance(d, dict):
        return str(d.get("label") or d.get("category") or d.get("phrase")
                   or d.get("name") or "finding").strip()
    return "finding"


def _box_of(d: Any) -> Any:
    if isinstance(d, dict):
        return d.get("box") or d.get("bbox") or d.get("xyxy") or d
    return d


def _score_of(d: Any) -> float:
    if isinstance(d, dict):
        for k in ("score", "confidence", "conf"):
            if k in d:
                try:
                    return max(0.0, min(1.0, float(d[k])))
                except (TypeError, ValueError):
                    pass
    return 0.5  # neutral default; LocalizedRegion.score is ge=0,le=1


def _to_regions(payload: Any) -> list[LocalizedRegion]:
    regions: list[LocalizedRegion] = []
    for label, box, score in _iter_detections(payload):
        xyxy = _as_xyxy(box)
        if xyxy is None:
            continue
        x1, y1, x2, y2 = xyxy
        # Normalize ordering and clamp to non-negative width/height.
        left, right = sorted((x1, x2))
        top, bottom = sorted((y1, y2))
        bbox = BoundingBox(
            x=int(round(left)),
            y=int(round(top)),
            w=max(0, int(round(right - left))),
            h=max(0, int(round(bottom - top))),
        )
        regions.append(LocalizedRegion(finding=label or "finding",
                                       bbox=bbox, score=score))
    return regions


def _extract_tool_text(result: Any) -> str:
    """Concatenate text from an MCP CallToolResult's content blocks."""
    content = getattr(result, "content", None) or []
    out: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            out.append(text)
    return "\n".join(out).strip()


# ── Public entrypoint ────────────────────────────────────────────────────────

async def run_localization(
    image_path: str,
    reasoning_report_json: str,
    *,
    categories: list[str] | None = None,
) -> LocalizationResult:
    """
    Localize the findings from a reasoning report onto the CXR image.

    Each finding category is grounded in its **own** ``locate_objects`` call so
    every returned box carries the correct finding label. (A single multi-category
    call returns a flat, *unlabeled* box list — the category↔box association lives
    only in the raw model text — which would force every box to a generic
    "finding" label.) Calls share one MCP connection and run sequentially.

    Args:
        image_path: Path to the CXR image (JPEG/PNG).
        reasoning_report_json: JSON-serialised ``ReasoningReport`` (or any shape
            ``_derive_categories`` understands).
        categories: Optional explicit category override. When omitted, categories
            are derived from the report's finding labels.

    Returns:
        ``LocalizationResult`` with one region per located box, labeled by finding.
        ``score`` is None — LocateAnything-3B does not emit a detection confidence.
        Empty on infrastructure error (logged, never raised).
    """
    url = os.environ.get("CXR_LOCALIZE_MCP_URL", DEFAULT_LOCALIZE_MCP_URL)
    try:
        timeout = float(os.environ.get("CXR_LOCALIZE_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    try:
        max_cats = int(os.environ.get("CXR_LOCALIZE_MAX_CATS", DEFAULT_MAX_CATEGORIES))
    except ValueError:
        max_cats = DEFAULT_MAX_CATEGORIES
    gen_mode = os.environ.get("CXR_LOCALIZE_GEN_MODE", DEFAULT_GEN_MODE)

    cats = categories if categories else _derive_categories(reasoning_report_json, max_cats)
    cats = _dedup([c for c in (cats or []) if c.strip()])[:max_cats]
    if not cats:
        logger.info("localization: no categories derived from report; nothing to locate")
        return LocalizationResult(regions=[])

    try:
        image_b64, image_id = _encode_image_b64(image_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("localization: could not read image %s — %s", image_path, e)
        return LocalizationResult(regions=[])

    # Reach the service through the SDK's MCP client (lazy import keeps this
    # module importable without the SDK present, e.g. for unit tests).
    try:
        from agents.mcp import MCPServerStreamableHttp
    except Exception as e:  # noqa: BLE001
        logger.warning("localization: Agents SDK MCP client unavailable — %s", e)
        return LocalizationResult(regions=[])

    server = MCPServerStreamableHttp(
        name="cxr-localization",
        params={"url": url},
        cache_tools_list=True,
        client_session_timeout_seconds=timeout,
        max_retry_attempts=2,
    )

    regions: list[LocalizedRegion] = []
    try:
        await server.connect()
        try:
            for cat in cats:
                try:
                    result = await server.call_tool(
                        LOCATE_TOOL,
                        {
                            "image_b64": image_b64,
                            "image_id": image_id,
                            "categories": [cat],   # one category → labeled boxes
                            "generation_mode": gen_mode,
                        },
                    )
                except Exception as e:  # noqa: BLE001 — skip this finding, keep going
                    logger.warning("localization: '%s' call failed — %s", cat, e)
                    continue
                regions.extend(_regions_for_category(result, cat))
        finally:
            await server.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.warning("localization: MCP connection failed (%s) — %s", url, e)
        return LocalizationResult(regions=[])

    logger.info("localization: %d region(s) across %d categor(y/ies)", len(regions), len(cats))
    return LocalizationResult(regions=regions)


def _regions_for_category(result: Any, category: str) -> list[LocalizedRegion]:
    """Parse one ``locate_objects`` result and label every box with ``category``."""
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        return []
    text = _extract_tool_text(result)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(payload, dict) and payload.get("success") is False:
        return []

    # Known shape: {"boxes": [{x1,y1,x2,y2}, ...], ...}. Fall back to the tolerant
    # multi-shape walker for anything else.
    raw_boxes = payload.get("boxes") if isinstance(payload, dict) else None
    out: list[LocalizedRegion] = []
    if isinstance(raw_boxes, list):
        for b in raw_boxes:
            bbox = _bbox_from(b)
            if bbox is not None:
                out.append(LocalizedRegion(finding=category, bbox=bbox, score=None))
        return out
    # Fallback: tolerate other envelopes; force the known category as the label.
    for _label, box, _score in _iter_detections(payload):
        bbox = _bbox_from(box)
        if bbox is not None:
            out.append(LocalizedRegion(finding=category, bbox=bbox, score=None))
    return out


def _bbox_from(box: Any) -> "BoundingBox | None":
    """Coerce a box of any supported shape to a pixel-space ``BoundingBox``."""
    xyxy = _as_xyxy(box)
    if xyxy is None:
        return None
    x1, y1, x2, y2 = xyxy
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return BoundingBox(
        x=int(round(left)),
        y=int(round(top)),
        w=max(0, int(round(right - left))),
        h=max(0, int(round(bottom - top))),
    )