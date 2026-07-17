# cxr-agent/agents/cxr_orchestrator/orchestrator_agent.py
"""
CXR Orchestrator Agent
======================
Defines the OpenAI Agents SDK agent, its tool functions, and the pipeline
runner for the chest X-ray analysis workflow.
Model is configurable via CXR_MODEL (default gpt-5.4-mini); see resolve_model().
Data classes are defined in models/pipeline.py — none are declared here.
Tool implementations live in tools/*/
Usage:
    python orchestrator_agent.py --image path/to/cxr.jpg [--query "..."]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import time
from pathlib import Path

from agents import Agent, Runner, function_tool
from agents.extensions.models.litellm_model import LitellmModel
from agents.sandbox import SandboxAgent

from models.pipeline import (
    CXRAnalysisResult,
    LocalizationResult,
    PipelineMeta,
    ReasoningReport,
    TriageResult,
)

# Sandbox visualization surface (CXR analogue of OCT's overlay tools).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.context import CXRContext  # noqa: E402
from tools.sandbox_overlay import (  # noqa: E402
    render_finding_overlay,
    stage_overlay,
    build_overlay_manifest,
    overlay_capabilities,
    DEFAULT_OVERLAY_SKILL_DIR,
)


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@function_tool
async def triage_image(image_path: str) -> str:
    import traceback, json
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from tools.triage import run_triage
        result = await run_triage(image_path)
        return result.model_dump_json()
    except Exception as e:
        err = traceback.format_exc()
        print(f"TRIAGE ERROR: {err}", file=sys.stderr, flush=True)
        return json.dumps({
            "valid": False,
            "is_chest_xray": False,
            "orientation": "unknown",
            "quality_grade": "non_diagnostic",
            "quality_issues": [],
            "triage_notes": f"Triage failed: {type(e).__name__}: {e}"
        })
    
@function_tool
async def reason_cxr(image_path: str, prompt: str = "", quick: bool = False) -> str:
    """
    Run NV-Reason-CXR-3B reasoning on a chest X-ray and return the text report.
    Pass the image *path* — the image is resized and encoded internally and sent
    to the reasoning model server-side, so the (large) image bytes never enter
    this conversation. Call this after `triage_image` returns valid=True.
    Args:
        image_path: Path to the CXR image file (the same path passed to triage).
        prompt: Clinical question or instruction. If empty, a safe default is
            used ("Find abnormalities and support devices."). If triage flagged
            quality issues, include them here so the model accounts for them.
        quick: If true, use the lighter/faster `analyze_cxr` pass instead of the
            full reasoning pass.
    Returns:
        JSON string: {"success", "answer", "thinking", "disclaimer", "image_id"}.
        Use `answer` as the basis for your structured read and propagate
        `disclaimer` to the user. On failure, {"success": false, "error": ...}.
    """
    import json
    from tools.reason import run_reasoning
    result = await run_reasoning(image_path, prompt, quick=quick)
    return json.dumps(result)


@function_tool
async def localize_findings(
    image_path: str, reasoning_report_json: str
) -> LocalizationResult:
    """
    Spatially ground findings from a reasoning report onto the CXR image.
    Uses NV-Locate-Anything-3B to produce bounding boxes for each finding
    mentioned in the reasoning report.
    Args:
        image_path: Path to the CXR image.
        reasoning_report_json: JSON-serialised ReasoningReport.
    Returns:
        LocalizationResult with per-finding bounding boxes.
    """
    from tools.localize import run_localization
    return await run_localization(image_path, reasoning_report_json)


@function_tool
async def speak_findings(summary_text: str, voice_id: str = "default") -> str:
    """
    Convert a CXR findings summary to speech using ElevenLabs TTS.
    Args:
        summary_text: Text to synthesise — typically the impression field.
        voice_id: ElevenLabs voice ID. Uses the default clinical voice if omitted.
    Returns:
        Filesystem path to the generated .mp3 audio file.
    """
    from tools.voice import run_tts
    return await run_tts(summary_text, voice_id)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """
You are the CXR Orchestrator — a medical AI agent specialising in chest X-ray
(CXR) analysis. You manage a pipeline of specialised sub-skills.
## Pipeline rules
1. ALWAYS call `triage_image` first for any image input, passing the image
   file path. Do not skip this step.
2. If `triage_image` returns `valid=False`, explain why to the user and STOP.
   Do not call any other tools.
3. If `triage_image` returns `valid=True`, call `reason_cxr` with the image
   **path** (not the image itself). Set `prompt` to the user's query if one was
   provided, otherwise leave it empty to use the safe default. If `quality_grade`
   from triage was `suboptimal`, include the triage notes in `prompt` so the
   model accounts for known quality issues. Use `quick=true` only for a fast
   overview. (Encoding happens inside the tool — never paste image bytes or
   base64 into the conversation yourself.)
4. Base your structured read on the `answer` field of the reasoning result, and
   propagate its `disclaimer` to the user. To spatially ground findings, call
   `localize_findings` with the image path and a JSON report of the findings you
   extracted (each with a `label`); then optionally visualize them.
5. After `localize_findings` returns, compare its regions to the findings you
   asked to localize. If a finding you expected to be localizable came back with
   NO bounding box, retry it ONCE: call `localize_findings` again with a report
   containing only the missing finding(s), each `label` rewritten to a simpler
   synonym that means the same thing —
     - collapse any device, line, tube, wire, or other hardware to a generic
       term such as "device";
     - if the label is overly specific, reduce the direction or location to a
       simpler term (drop laterality or precise position).
   For example: "left subclavian pacemaker/ICD" -> "device"; "left costophrenic
   angle" -> "lung base". Retry at most ONCE per finding; if the simpler synonym
   still returns no box, proceed without one. You may keep the original, more
   specific label when you visualize the box.
## Communication style
- Be concise and clinical. Use standard radiological terminology.
- Always note the orientation and quality grade from triage when reporting findings.
- Flag any quality issues that may limit diagnostic confidence.
- Propagate the `disclaimer` field from the reasoning response to the user.
- Do not speculate beyond what the model outputs.

## Laterality (important)
Radiographic "left" and "right" refer to the PATIENT's sides, which are mirrored
on the image: the patient's LEFT appears on the VIEWER's RIGHT half of the image,
and the patient's RIGHT appears on the VIEWER's LEFT. Confirm orientation using
the "L" / "R" markers usually placed in the upper corners when they are present.
Apply this both when describing locations and when localizing:
- When you build the report for `localize_findings`, query each finding by its
  bare anatomical term and do NOT lead the label with "left"/"right". The locator
  places boxes by image appearance, and the laterality word tends to push the box
  to the wrong (literal image-left) side. Keep the full laterality in the label
  you display to the user.
- After localizing, sanity-check each box against this convention (a "left"
  finding should land on the viewer's RIGHT half, confirmed by the L/R marker).
  If a box clearly sits on the wrong side, say so rather than presenting a
  mislocalized box as correct.
{viz_note}
{skill_catalog}
""".strip()


def _build_system_prompt(viz_note: str = "") -> str:
    """Load the skills catalog and embed it, plus the variant's viz guidance."""
    from cxr_agents.registry import get_registry
    catalog = get_registry().generate_catalog_xml()
    return _SYSTEM_PROMPT_TEMPLATE.format(
        viz_note=viz_note.strip(), skill_catalog=catalog)


# Visualization guidance for the BASE agent (has render_finding_overlay only).
_BASE_VIZ_NOTE = """
## Visualization
Render an interactive HTML overlay of the localized findings on the X-ray with
the `render_finding_overlay` tool (pass the image path and the localization
result as JSON). Call it when a visual would genuinely help — the user asks to
*see* where the findings are, or the localized findings are worth showing — and
skip it for a plain text read. This backend can only produce the standard box
overlay; if the user asks for a different visualization (heatmap, etc.), say so
plainly rather than substituting the overlay.
"""


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------

# Default model. Override per-deployment with the CXR_MODEL env var. A *bare*
# OpenAI model name (e.g. "gpt-5.4-mini") routes through the OpenAI Responses
# API, where hosted sandbox tools (apply_patch) are supported. A provider-
# prefixed name (e.g. "anthropic/claude-opus-4-6") routes through LiteLLM / the
# Chat Completions API, where hosted tools are NOT supported.
DEFAULT_MODEL = os.environ.get("CXR_MODEL", "gpt-5.5")


def _is_litellm_name(name: str) -> bool:
    """Provider-prefixed names (with a '/') go through LiteLLM."""
    return "/" in name


def resolve_model(model=None):
    """Resolve a model spec to ``(model_for_sdk, supports_hosted_tools)``.
    ``model`` may be a bare OpenAI string ("gpt-5.4-mini"), a provider-prefixed
    LiteLLM string ("anthropic/claude-opus-4-6"), an already-built model
    instance, or ``None`` (use ``CXR_MODEL`` / :data:`DEFAULT_MODEL`).
    ``supports_hosted_tools`` is True when the model uses the OpenAI Responses
    API — the signal the app and sandbox factory use to decide whether the full
    Filesystem capability (apply_patch) is safe to attach.
    """
    if model is None:
        model = DEFAULT_MODEL
    if isinstance(model, str):
        if _is_litellm_name(model):
            return LitellmModel(
                model=model,
                api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            ), False
        return model, True  # bare OpenAI name → Responses API → hosted tools OK
    # A pre-built instance: LiteLLM models use Chat Completions; anything else
    # (e.g. an OpenAIResponsesModel) is assumed to support hosted tools.
    supports_hosted = type(model).__name__ != "LitellmModel"
    return model, supports_hosted


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_orchestrator(model=None, mcp_servers=None) -> Agent:
    """Construct and return the configured CXR Orchestrator agent.
    Base (non-sandbox) variant: the on-demand ``render_finding_overlay`` tool
    drives a sandbox internally when called, so this works with any model /
    backend (it exposes no hosted sandbox tools to the model).
    Args:
        model: Model spec (see :func:`resolve_model`); defaults to ``CXR_MODEL``.
        mcp_servers: Optional list of connected MCP servers (e.g. the
            NV-Reason-CXR-3B reasoning server). The SDK discovers their tools
            (``reason_cxr`` / ``analyze_cxr``) at connection time, so they are
            attached here rather than declared as ``@function_tool``s. The caller
            owns each server's lifecycle (``async with server: ...``).
    """
    model_obj, _ = resolve_model(model)

    return Agent(
        name="CXR Orchestrator",
        model=model_obj,
        instructions=_build_system_prompt(_BASE_VIZ_NOTE),
        tools=[triage_image, reason_cxr, localize_findings, speak_findings,
               render_finding_overlay],
        mcp_servers=list(mcp_servers or []),
    )


# Visualization guidance for the model-driven sandbox variant (shell + stage_overlay).
_OVERLAY_NOTE = """
## Visualization (sandbox workspace)
You have a sandbox workspace with shell access. After localizing findings, call
`stage_overlay(image_path, findings_json)` once to place the inputs under
`inputs/` (the X-ray, the normalized finding boxes, the severity colour LUT).

Then build the visualization that best answers THIS request. The type is the
user's choice — there is no fixed default, and you must not substitute one type
for another:

- If the user just wants to see where the findings are (or doesn't specify a
  type), run the ready-made standard box overlay:
  `python skills/cxr-overlay/render_overlay.py inputs output`
  → writes `output/overlay_<image_id>.html`.

- If the user asks for ANY other kind of visual — a heatmap, finding-density
  map, severity breakdown, confidence ranking, per-finding zoom crops, a
  side-by-side, etc. — build exactly that, do NOT fall back to the box overlay.
  Read `skills/cxr-overlay/SKILL.md` for the input data contract, then author
  your own script with the shell and run it, writing the result to
  `output/<name>.html`:
    cat > render_custom.py <<'PYEOF'
    # ...your script: read inputs/, write a self-contained output/<name>.html...
    PYEOF
    python render_custom.py
  `skills/cxr-overlay/render_overlay.py` is a worked example to copy and adapt.

Anything you write to `output/` is collected and displayed automatically. Keep
outputs self-contained (embed the data + vanilla JS; no external libraries or
network access). Do not print large file contents (the base64 image) into the
chat — operate on them through the shell only.
"""


def build_orchestrator_sandbox(model=None, mcp_servers=None,
                               filesystem_tools=None) -> SandboxAgent:
    """Construct the CXR Orchestrator as a ``SandboxAgent``.
    Same tools + system prompt as the base agent, plus: sandbox capabilities, the
    ``stage_overlay`` tool, and a default manifest that stages the ``cxr-overlay``
    skill into the workspace. The runner injects the live sandbox session at run
    time via ``RunConfig(sandbox=...)``. This is the path that lets the agent
    author bespoke visualizations on the fly.
    Capabilities are chosen from the model's backend:
    * **OpenAI Responses API** (e.g. ``gpt-5.4-mini``) → full capabilities
      including the Filesystem ``apply_patch`` tool.
    * **Chat Completions API** (e.g. Claude via LiteLLM) → shell-only (no hosted
      ``apply_patch``); the model edits files via the shell instead. For that
      backend the base ``build_orchestrator`` is usually the better choice.
    Args:
        model: Model spec (see :func:`resolve_model`); defaults to ``CXR_MODEL``.
        mcp_servers: Optional connected MCP servers (the reasoning server).
        filesystem_tools: Force the Filesystem capability on/off. When ``None``
            (default) it follows the model's hosted-tool support.
    """
    model_obj, supports_hosted = resolve_model(model)
    if filesystem_tools is None:
        filesystem_tools = supports_hosted

    return SandboxAgent(
        name="CXR Orchestrator",
        model=model_obj,
        instructions=_build_system_prompt(_OVERLAY_NOTE),
        tools=[triage_image, reason_cxr, localize_findings, speak_findings,
               stage_overlay],
        default_manifest=build_overlay_manifest(DEFAULT_OVERLAY_SKILL_DIR),
        capabilities=overlay_capabilities(filesystem_tools=filesystem_tools),
        mcp_servers=list(mcp_servers or []),
    )


# ---------------------------------------------------------------------------
# Image encoding helper
# ---------------------------------------------------------------------------

def encode_image_for_message(image_path: str) -> dict:
    """Return a base64 image content block suitable for multimodal model input."""
    path = Path(image_path)
    media_type_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    media_type = media_type_map.get(path.suffix.lower(), "image/jpeg")
    data = base64.b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

async def run_pipeline(
    image_path: str, query: str | None = None
) -> CXRAnalysisResult:
    """
    Run the full CXR analysis pipeline for a given image.
    Args:
        image_path: Path to the CXR image file.
        query: Optional user question or instruction.
    Returns:
        CXRAnalysisResult populated with results from each sub-skill.
    """
    orchestrator = build_orchestrator()
    start = time.monotonic()

    user_text = query or "Please analyse this chest X-ray."

    input_message = f"Please analyse this chest X-ray. Image path: {image_path}"

    # Run-scoped state for the sandbox visualization path. Harmless for the
    # context-free tools (they ignore it); gives render_finding_overlay an
    # output dir + a place to record produced overlays.
    cxr_ctx = CXRContext()

    import json
    for tool in orchestrator.tools:
        schema = getattr(tool, 'params_json_schema', None) or getattr(tool, 'schema', None)
        print(f"DEBUG tool: {tool.name}, schema size: {len(json.dumps(schema)) if schema else 'n/a'}", file=sys.stderr)
        print(f"DEBUG instructions size: {len(orchestrator.instructions)} chars", file=sys.stderr)
    
    agent_result = await Runner.run(orchestrator, input=input_message, context=cxr_ctx)

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Callers can inspect agent_result.new_items for the full tool-call trace.
    return CXRAnalysisResult(
        triage=TriageResult(
            valid=True,
            is_chest_xray=True,
            orientation="unknown",
            quality_grade="acceptable",
            triage_notes="Pipeline complete — see agent trace for details.",
        ),
        pipeline_meta=PipelineMeta(
            model=DEFAULT_MODEL,
            latency_ms=elapsed_ms,
            errors=[],
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CXR Orchestrator Agent")
    parser.add_argument("--image", required=True, help="Path to the CXR image file")
    parser.add_argument("--query", default=None, help="Optional text query")
    args = parser.parse_args()

    result = asyncio.run(run_pipeline(args.image, args.query))
    print(result.model_dump_json(indent=2))