---
name: cxr_localization
description: >
  Ground salient findings onto a chest X-ray using NV-Locate-Anything-3B via
  its FastMCP endpoint at https://nv-locate-anything-3b.fastmcp.app/mcp. Use
  after the orchestrator has recorded a FindingsSummary for an ABNORMAL study;
  the salient_findings location keys become detection categories. Returns a
  LocalizationResult with one bounding box per returned region, in original
  pixel space. Skipped for normal studies.
license: Proprietary
compatibility: >
  Requires network access to https://nv-locate-anything-3b.fastmcp.app/mcp.
  MCP transport: Streamable HTTP via the SDK's MCPServerStreamableHttp
  (openai-agents; `pip install mcp`). Called programmatically, not registered
  on the agent (the tools take image_b64). No local GPU required.
metadata:
  author: hmgill
  version: "1.0"
  endpoint: https://nv-locate-anything-3b.fastmcp.app/mcp
  transport: streamable-http
  model: NV-Locate-Anything-3B
allowed-tools: Bash(python:*)
---

# CXR Localization Skill

Spatially grounds findings on a validated CXR using NV-Locate-Anything-3B,
served over a FastMCP endpoint. It is the step after `cxr_summary`: the
location keys of the recorded `salient_findings` are used as detection
categories, and each returned box is mapped back to its finding.

## MCP server

**Endpoint:** `https://nv-locate-anything-3b.fastmcp.app/mcp`
**Transport:** Streamable HTTP.

> Called PROGRAMMATICALLY inside the `localize_findings` function tool with the
> SDK's `MCPServerStreamableHttp` client — NOT registered via the agent's
> `mcp_servers=[...]`. The server's tools take the image as `image_b64`;
> registering it natively would force the LLM to emit the whole image as a
> tool argument and exhaust the context window (same rule as `cxr_reasoning`).

## Image handling — send the ORIGINAL

Unlike `cxr_reasoning` (which pre-resizes for the Qwen backbone), this server
resizes internally and returns coordinates already in ORIGINAL pixel space, so
the tool sends the full-resolution image (JPEG). Do not resize client-side, or
the returned boxes will be in the wrong coordinate frame.

## Server-side tools

| Tool             | Key arguments                                  | Use |
|------------------|------------------------------------------------|-----|
| `locate_objects` | `image_b64`, `image_id`, `categories` (list)   | Detect boxes for each category — the pipeline default |
| `ground_phrase`  | `image_b64`, `image_id`, `phrase`, `single`    | Ground one referring expression |
| `point_at`       | `image_b64`, `image_id`, `phrase`              | Return point(s) for a phrase |
| `health`         | (none)                                         | Connectivity check |

All accept `generation_mode` (`hybrid` default, `fast`, `slow`) and
`max_new_tokens`. The pipeline uses `locate_objects` with the salient-finding
locations as `categories` (one call grounds all targets).

## Response shape

`locate_objects` returns JSON with:

```json
{
  "success": true,
  "image_width": 1024, "image_height": 1024,
  "categories": ["right lower lung", "aorta"],
  "boxes": [{"x1": 600, "y1": 700, "x2": 820, "y2": 900}],
  "points": [],
  "raw_answer": "..."
}
```

`boxes` are `{x1, y1, x2, y2}` in original pixel space. The tool converts each
to a `BoundingBox{x, y, w, h}` and aligns it with `categories[i]` to recover
the finding. NV-Locate does not return per-box confidence, so `score` is set to
1.0 as a placeholder.

## Pipeline position

```
cxr_summary (is_normal=false, salient_findings populated)
    -> cxr_localization  <- this skill
         locate_objects(categories = list(salient_findings.keys()))
    -> LocalizationResult (per-finding boxes)
```

The orchestrator passes the `salient_findings` map as `targets_json`; the tool
uses the keys as categories and maps boxes back to finding text via the map.

## Implementation

See [tools/locate.py](../../tools/locate.py)
for `run_localization(image_path, targets_json, generation_mode="hybrid")`.
See [references/REFERENCE.md](references/REFERENCE.md) for endpoint/timeout notes.
