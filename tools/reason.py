# cxr-agent/tools/reason.py
"""
tools/reason.py
===============
Chest-X-ray reasoning via **NV-Reason-CXR-3B**, called *host-side* so the
base64 image never enters the model's context window.

Why this exists
---------------
The reasoning MCP tool (`reason_cxr` / `analyze_cxr`) takes a base64-encoded PNG.
If that were an agent-facing MCP tool, the orchestrator LLM would have to (1)
receive the base64 from an `encode_image` tool result and (2) pass it straight
back as a `reason_cxr` argument — putting *two* copies of a multi-megabyte
string into the conversation and blowing the context window ("Your input exceeds
the context window of this model").

Instead, `run_reasoning` resizes + encodes the image and calls the MCP through
the Agents SDK's own MCP client, returning only the **text** report. The model
passes a file path in and gets words back; the pixels are never tokenized. This
mirrors `tools/localize.py`.

Transport / contract (from skills/cxr_reasoning)
------------------------------------------------
Streamable-HTTP FastMCP server exposing `reason_cxr` (full) and `analyze_cxr`
(quick). Both take ``image_b64``, ``image_id``, ``prompt`` and return JSON
``{success, answer, thinking, raw_text, disclaimer}`` (or ``{success:false,
error}``). Images must be resized to ≤1280px before encoding or Qwen2.5-VL
produces garbled output.

Environment:
    CXR_REASON_MCP_URL   reasoning MCP URL (else built-in default)
    CXR_REASON_TIMEOUT   per-call read timeout, seconds (default 120)
    CXR_REASON_MAX_SIDE  longest-side resize cap before encoding (default 1280)
    CXR_REASON_THINK_CHARS  cap on returned thinking text (default 6000)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REASON_MCP_URL = "https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp"
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_SIDE = 1280
DEFAULT_THINK_CHARS = 6000
DEFAULT_PROMPT = "Find abnormalities and support devices."


def _extract_tool_text(result: Any) -> str:
    """Concatenate text from an MCP CallToolResult's content blocks."""
    content = getattr(result, "content", None) or []
    out: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            out.append(text)
    return "\n".join(out).strip()


def _cap(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + " …"


async def run_reasoning(
    image_path: str,
    prompt: str | None = None,
    *,
    quick: bool = False,
) -> dict:
    """
    Run NV-Reason-CXR-3B on a CXR and return a *text-only* result dict.

    Args:
        image_path: Path to the (already triaged) CXR image.
        prompt: Clinical question / instruction. Falls back to a safe default.
        quick: Use the lighter ``analyze_cxr`` tool instead of ``reason_cxr``.

    Returns:
        ``{"success": bool, "answer": str, "thinking": str, "disclaimer": str,
           "image_id": str, "error"?: str}``. No base64 — safe to return to the
        model. On any infra error ``success`` is False and ``error`` is set.
    """
    url = os.environ.get("CXR_REASON_MCP_URL", DEFAULT_REASON_MCP_URL)
    try:
        timeout = float(os.environ.get("CXR_REASON_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    try:
        max_side = int(os.environ.get("CXR_REASON_MAX_SIDE", DEFAULT_MAX_SIDE))
    except ValueError:
        max_side = DEFAULT_MAX_SIDE
    try:
        think_chars = int(os.environ.get("CXR_REASON_THINK_CHARS", DEFAULT_THINK_CHARS))
    except ValueError:
        think_chars = DEFAULT_THINK_CHARS

    prompt = (prompt or "").strip() or DEFAULT_PROMPT
    tool_name = "analyze_cxr" if quick else "reason_cxr"

    # Encode host-side (never returned to the model).
    try:
        from tools.encode_image import encode_image_for_reasoning
        image_b64, image_id = encode_image_for_reasoning(image_path, max_side=max_side)
    except Exception as e:  # noqa: BLE001
        logger.warning("reasoning: could not encode %s — %s", image_path, e)
        return {"success": False, "error": f"encode failed: {e}",
                "answer": "", "thinking": "", "disclaimer": "",
                "image_id": Path(image_path).stem}

    try:
        from agents.mcp import MCPServerStreamableHttp
    except Exception as e:  # noqa: BLE001
        logger.warning("reasoning: Agents SDK MCP client unavailable — %s", e)
        return {"success": False, "error": f"mcp client unavailable: {e}",
                "answer": "", "thinking": "", "disclaimer": "", "image_id": image_id}

    server = MCPServerStreamableHttp(
        name="cxr-reasoning",
        params={"url": url},
        cache_tools_list=True,
        client_session_timeout_seconds=timeout,
        max_retry_attempts=2,
    )
    try:
        await server.connect()
        try:
            result = await server.call_tool(
                tool_name,
                {"image_b64": image_b64, "image_id": image_id, "prompt": prompt},
            )
        finally:
            await server.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.warning("reasoning: MCP call failed (%s) — %s", url, e)
        return {"success": False, "error": f"reasoning call failed: {e}",
                "answer": "", "thinking": "", "disclaimer": "", "image_id": image_id}

    text = _extract_tool_text(result)
    if not text:
        return {"success": False, "error": "empty tool output",
                "answer": "", "thinking": "", "disclaimer": "", "image_id": image_id}

    try:
        payload = json.loads(text)
    except Exception:  # noqa: BLE001
        # Not JSON — treat the whole text as the answer.
        return {"success": True, "answer": _cap(text, 20000), "thinking": "",
                "disclaimer": "", "image_id": image_id}

    if isinstance(payload, dict) and payload.get("success") is False:
        return {"success": False,
                "error": str(payload.get("error") or "reasoning failed"),
                "answer": "", "thinking": "", "disclaimer": "", "image_id": image_id}

    answer = payload.get("answer") or payload.get("raw_text") or ""
    return {
        "success": True,
        "answer": _cap(answer, 20000),
        "thinking": _cap(payload.get("thinking") or "", think_chars),
        "disclaimer": _cap(payload.get("disclaimer") or "", 2000),
        "image_id": image_id,
    }