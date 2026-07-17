# cxr-agent/tools/__init__.py
"""Local tools and run context for the CXR agent.

Re-exports the sandbox visualization surface so callers can do
``from tools import render_finding_overlay, CXRContext`` without reaching into
submodules. The thin per-tool modules (``triage``, ``encode_image``) are still
imported directly where needed (e.g. inside the orchestrator's function tools)
to keep their heavy deps lazy.
"""

from .context import CXRContext
from .sandbox_overlay import (
    render_finding_overlay,
    stage_overlay,
    SandboxManager,
    build_overlay_manifest,
    overlay_capabilities,
    collect_overlay_outputs,
    severity_lut,
    DEFAULT_OVERLAY_SKILL_DIR,
)

__all__ = [
    "CXRContext",
    "render_finding_overlay",
    "stage_overlay",
    "SandboxManager",
    "build_overlay_manifest",
    "overlay_capabilities",
    "collect_overlay_outputs",
    "severity_lut",
    "DEFAULT_OVERLAY_SKILL_DIR",
]
