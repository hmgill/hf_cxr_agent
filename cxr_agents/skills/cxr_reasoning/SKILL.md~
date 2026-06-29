---
name: cxr_reasoning
description: >
  Analyse a validated chest X-ray using the NV-Reason-CXR-3B model via the
  FastMCP server at https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp. Use after
  cxr_triage returns valid=True. Exposes two tools: reason_cxr for detailed
  structured reasoning and analyze_cxr for a faster general description.
  Always pre-resize the image to a maximum of 1280px on the longest side
  before encoding, or the model will produce garbled output.
license: Proprietary
compatibility: >
  Requires network access to https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp.
  MCP transport: Streamable HTTP (MCPServerStreamableHttp).
  Image must be base64-encoded PNG, max 1280px longest side.
  No local GPU or NVIDIA NIM API key required — inference runs server-side.
metadata:
  author: hmgill
  version: "1.0"
  endpoint: https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp
  transport: streamable-http
allowed-tools: Bash(python:*)
---

# CXR Reasoning Skill

This skill calls the NV-Reason-CXR-3B model through a FastMCP server to
produce a structured radiology reasoning report for a validated chest X-ray.

## MCP Server

**Endpoint:** `https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp`
**Transport:** Streamable HTTP — use `MCPServerStreamableHttp` from the
OpenAI Agents SDK. The server is stateless; no session management required.

The SDK connects to this server at agent startup via `async with mcp_server:`
and automatically discovers and registers all available tools. Do not call
the endpoint directly — always go through the SDK's MCP interface.

## Available Tools

The MCP server exposes two tools. Both accept the same arguments.

### `reason_cxr` — preferred for clinical use
Runs the full NV-Reason-CXR-3B reasoning pipeline. Returns structured output
with a `thinking` chain-of-thought block, a detailed `answer`, and a
`disclaimer`. Slower but more thorough.

### `analyze_cxr` — faster general description
Runs a lighter analysis pass. Returns a descriptive `answer` without extended
thinking. Use when a quick overview is sufficient.

### `health` — server health check
No arguments. Returns server status. Use to verify connectivity before
submitting images.

## Tool Arguments

Both `reason_cxr` and `analyze_cxr` accept exactly these three arguments:

| Argument    | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `image_b64` | string | Yes      | Base64-encoded PNG of the CXR image |
| `image_id`  | string | Yes      | Identifier string for the image (e.g. filename stem) |
| `prompt`    | string | Yes      | Clinical question or instruction for the model |

## Response Schema

Both tools return a JSON object with this structure:

```json
{
  "success":    true,
  "answer":     "<structured radiology findings text>",
  "thinking":   "<chain-of-thought reasoning, may be empty for analyze_cxr>",
  "raw_text":   "<verbatim model output before parsing>",
  "disclaimer": "<optional safety/liability disclaimer>"
}
```

On failure:
```json
{
  "success": false,
  "error":   "<error message>"
}
```

Always check `success` before using `answer`. If `success=false`, log the
error in `pipeline_meta.errors` and do not proceed to localization.

## Image Preparation — Critical

The underlying model is Qwen2.5-VL. Its `smart_resize` function will
**upscale** images that are smaller than its token budget, producing garbled
output for large CXRs. Always resize before encoding:

```python
from PIL import Image
import io, base64

img = Image.open(image_path).convert("RGB")
w, h = img.size
max_side = 1280
if max(w, h) > max_side:
    scale = max_side / max(w, h)
    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

buf = io.BytesIO()
img.save(buf, format="PNG")
image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
```

## Prompt Guidelines

The model responds well to specific clinical questions. Examples:

| Use case | Prompt |
|---|---|
| General report | `"Describe all findings in this chest X-ray."` |
| Targeted finding | `"Is there evidence of pleural effusion?"` |
| Cardiac assessment | `"Describe the cardiac silhouette and mediastinum."` |
| Post-triage context | Pass the `triage_notes` from `TriageResult` as context prefix |

Avoid vague prompts like `"What do you see?"` — they produce less structured
output. Prefer explicit clinical framing.

## Wiring in the Orchestrator

The MCP server is registered on the `Agent` object — the orchestrator does
not need a `reason_cxr` `@function_tool`. The SDK discovers the tools
automatically at connection time:

```python
from agents.mcp import MCPServerStreamableHttp

mcp_server = MCPServerStreamableHttp(
    name="cxr-reasoning",
    params={"url": "https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp"},
)

agent = Agent(
    name="CXR Orchestrator",
    model=model,
    instructions=instructions,
    tools=[triage_image, localize_findings, speak_findings],  # no reason_cxr here
    mcp_servers=[mcp_server],
)

async with mcp_server:
    result = await Runner.run(agent, input=input_message)
```

## Pipeline Position

```
cxr_triage (valid=True)
    ↓
cxr_reasoning  ← this skill
    ├── reason_cxr   (full report)
    └── analyze_cxr  (quick description)
    ↓
cxr_localization
```

Only call this skill after `cxr_triage` returns `valid=True`. If
`quality_grade` is `suboptimal`, include the triage notes in the prompt
so the model can account for known quality issues.

## References

See [references/REFERENCE.md](references/REFERENCE.md) for MCP protocol
details, response parsing notes, and known model behaviour.
