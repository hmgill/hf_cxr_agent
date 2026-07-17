# cxr-agent/tools/context.py
"""
tools/context.py
================
Run-scoped state for the CXR agent's sandbox visualization path.

Why this exists
---------------
The CXR pipeline tools (`triage_image`, `encode_image`, `localize_findings`,
`speak_findings`) are self-contained: they take a file path / JSON string and
return a value, so they never needed a shared context object. The sandbox
visualization path is different — it provisions a sandbox **once per
conversation** and reuses it across turns, and it needs somewhere to record the
output directory and which overlay files have already been produced.

``CXRContext`` is that shared state. It is deliberately tiny: it holds only the
sandbox-relevant fields (mirroring the subset of OCT's ``OCTContext`` that the
overlay path actually uses). The image itself stays on disk and is addressed by
path, and the finding boxes are small enough to pass as tool arguments, so —
unlike OCT — there is no image/artifact handle store here.

Pass it to the runner once::

    octx = CXRContext(output_dir="/tmp/cxr_out/<session>")
    await Runner.run(agent, input=..., context=octx)

Inside any ``@function_tool`` it is reachable as ``ctx.context``. The sandbox
tools tolerate ``ctx.context is None`` (they fall back to a default output dir
and a fresh on-demand sandbox), so adding ``context=`` never breaks the existing
context-free tools — they simply ignore it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional


def _default_out_dir() -> str:
    return os.environ.get("CXR_OUT_DIR", "/tmp/cxr_out")


@dataclass
class CXRContext:
    """
    The object handed to ``Runner.run(..., context=...)`` for the sandbox path.

    Attributes:
        output_dir: Host directory where collected overlay HTML is written.
        sandbox: A lazily-provisioned ``SandboxManager`` for the on-demand
            ``render_finding_overlay`` tool (created on first use).
        sandbox_session: A live sandbox session injected by the runner for the
            advanced, model-driven ``SandboxAgent`` path (set via ``RunConfig``).
        overlays: Filenames of overlay HTML produced so far this run.
    """

    output_dir: str = field(default_factory=_default_out_dir)
    sandbox: Optional[Any] = None           # SandboxManager (lazy, on-demand path)
    sandbox_session: Optional[Any] = None   # live session (model-driven path)
    overlays: list = field(default_factory=list)
