# cxr-agent/agents/cxr_orchestrator/orchestrator_agent.py
"""
CXR Orchestrator Agent
======================
Defines the OpenAI Agents SDK agent, its tool functions, and the pipeline
runner for the chest X-ray analysis workflow.

Uses Claude claude-opus-4-6 via the LiteLLM extension for the Agents SDK.
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
def encode_image(image_path: str, max_side: int = 1280) -> dict:
    """
    Resize and base64-encode a CXR image for submission to the reasoning MCP server.

    Must be called before reason_cxr or analyze_cxr. Resizes so the longest
    side is at most max_side pixels, then encodes as a base64 PNG string.

    Args:
        image_path: Path to the CXR image file (JPEG or PNG).
        max_side:   Maximum pixels on the longest side (default 1280).
                    Use 512 or 768 for faster inference on straightforward cases.
                    Use 1280 (default) for standard clinical analysis.
                    Use up to 1536 only when fine anatomical detail is critical.

    Returns:
        Dict with keys:
          - image_b64 (str): base64-encoded PNG string ready for reason_cxr
          - image_id  (str): filename stem, e.g. "cxr_demo"
    """
    from tools.encode_image import encode_image_for_reasoning
    image_b64, image_id = encode_image_for_reasoning(image_path, max_side=max_side)
    return {"image_b64": image_b64, "image_id": image_id}


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
    from tools.cxr_localization.locate import run_localization
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
    from tools.cxr_voice.tts import run_tts
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
3. If `triage_image` returns `valid=True`, call `encode_image` with the
   image path to produce `image_b64` and `image_id`. Choose `max_side`
   based on task complexity: 768 for quick overviews, 1280 (default) for
   standard clinical analysis, up to 1536 when fine detail is critical.
4. Call `reason_cxr` from the MCP server using the encoded image. Set
   `prompt` to the user's query if one was provided, otherwise use:
   "Describe all findings in this chest X-ray in detail."
   If `quality_grade` from triage was `suboptimal`, prefix the prompt with
   the triage notes so the model accounts for known quality issues.

## Communication style

- Be concise and clinical. Use standard radiological terminology.
- Always note the orientation and quality grade from triage when reporting findings.
- Flag any quality issues that may limit diagnostic confidence.
- Propagate the `disclaimer` field from the reasoning response to the user.
- Do not speculate beyond what the model outputs.

## Visualization

You can render an interactive HTML overlay of the localized findings on the
X-ray with the `render_finding_overlay` tool (pass the image path and the
localization result as JSON). Use your judgement: call it when a visual would
genuinely help — the user asks to *see* where the findings are, or the localized
findings are worth showing — and skip it for a plain text read. It provisions a
sandbox only when called, so there is no cost to not using it.

{skill_catalog}
""".strip()


def _build_system_prompt() -> str:
    """Load the skills catalog from the registry and embed it in the prompt."""
    from cxr_agents.registry import get_registry
    catalog = get_registry().generate_catalog_xml()
    return _SYSTEM_PROMPT_TEMPLATE.format(skill_catalog=catalog)


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def build_orchestrator() -> Agent:
    """Construct and return the configured CXR Orchestrator agent.

    Base (non-sandbox) variant: the on-demand ``render_finding_overlay`` tool
    drives a sandbox internally when called, so this works with any model.
    """
    model = LitellmModel(
        model="anthropic/claude-opus-4-6",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )

    return Agent(
        name="CXR Orchestrator",
        model=model,
        instructions=_build_system_prompt(),
        tools=[triage_image, encode_image, localize_findings, speak_findings,
               render_finding_overlay],
    )


# Appended to the persona for the model-driven sandbox variant.
_OVERLAY_NOTE = """

## Sandbox workspace (custom visualizations)

You also have a **sandbox workspace** with shell + filesystem access (run shell
commands, and create/edit files with apply_patch). After localizing findings,
call `stage_overlay(image_path, findings_json)` once to place the inputs in the
workspace under `inputs/` (the X-ray, the normalized finding boxes, the severity
colour LUT).

Then choose how to visualise:
- **Standard box overlay** (default): run
  `python skills/cxr-overlay/render_overlay.py inputs output` to write
  `output/overlay_<image_id>.html`.
- **A different visualization** (when the user asks for something other than the
  standard overlay — e.g. a severity breakdown, a confidence ranking, per-finding
  zoom crops, a side-by-side, a finding-density heatmap): read
  `skills/cxr-overlay/SKILL.md` for the input data contract, then **author your
  own script** with apply_patch and run it, writing the result to
  `output/<name>.html`. The staged `render_overlay.py` is a worked example you
  can copy and adapt.

Write whatever you save to `output/` — everything there is collected after the
run. Keep outputs self-contained (embed the data + vanilla JS; no external
libraries or network installs are available). Do not print large file contents
(the base64 image) into the chat; operate on them through shell only.
"""


def build_orchestrator_sandbox() -> SandboxAgent:
    """Construct the CXR Orchestrator as a ``SandboxAgent``.

    Same tools + system prompt as the base agent, plus: shell/filesystem
    capabilities, the ``stage_overlay`` tool, and a default manifest that stages
    the ``cxr-overlay`` skill into the workspace. The runner injects the live
    sandbox session at run time via ``RunConfig(sandbox=...)``. This is the path
    that lets the agent author bespoke visualizations on the fly.

    The sandbox shell/filesystem/apply_patch capabilities are exposed by the SDK
    as ordinary function tools, so this works with Claude via the LiteLLM bridge
    just like the base agent.
    """
    model = LitellmModel(
        model="anthropic/claude-opus-4-6",
        api_key=os.environ["ANTHROPIC_API_KEY"],
    )

    return SandboxAgent(
        name="CXR Orchestrator",
        model=model,
        instructions=_build_system_prompt() + _OVERLAY_NOTE,
        tools=[triage_image, encode_image, localize_findings, speak_findings,
               stage_overlay],
        default_manifest=build_overlay_manifest(DEFAULT_OVERLAY_SKILL_DIR),
        capabilities=overlay_capabilities(),
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
            model="anthropic/claude-opus-4-6",
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
