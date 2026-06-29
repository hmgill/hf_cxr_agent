# CXR Reasoning — Technical Reference

## Model

**NV-Reason-CXR-3B** — NVIDIA's chest X-ray reasoning model, served via a
FastMCP endpoint. The underlying vision backbone is **Qwen2.5-VL**.

## Endpoint

| Property | Value |
|---|---|
| URL | `https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp` |
| Transport | Streamable HTTP (MCP spec 2024-11-05) |
| Auth | None required |
| Timeout | Allow up to 300s — cold starts can be slow |

## MCP Protocol

The server speaks JSON-RPC 2.0 over Streamable HTTP. The OpenAI Agents SDK
handles the protocol entirely via `MCPServerStreamableHttp` — you do not
need to construct JSON-RPC payloads manually.

Under the hood, each tool call sends:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "reason_cxr",
    "arguments": {
      "image_b64": "<base64 PNG>",
      "image_id":  "case_001",
      "prompt":    "Describe all findings."
    }
  }
}
```

The response arrives as either plain JSON or SSE (`data: {...}` lines).
The SDK handles both transparently.

## Response Parsing

The tool result is nested at `result.content[0].text` in the raw JSON-RPC
response. The SDK unwraps this and returns the text string directly as the
tool output. Parse it as JSON:

```python
import json
raw = tool_result  # string returned by the SDK
data = json.loads(raw)
answer   = data["answer"]
thinking = data.get("thinking", "")
success  = data.get("success", False)
```

## Known Model Behaviours

- **Cold start latency:** First call after inactivity may take 60–90s.
  Subsequent calls in the same session are faster.
- **Garbled output on large images:** Caused by Qwen2.5-VL's `smart_resize`
  upscaling. Always pre-resize to max 1280px longest side client-side.
- **Thinking block:** `reason_cxr` returns a `thinking` field containing
  chain-of-thought reasoning. This is useful for debugging but should not
  be surfaced directly to end users.
- **Disclaimer field:** The model appends a liability disclaimer to clinical
  outputs. Always propagate this to the user if displaying results.
- **Empty `thinking` from `analyze_cxr`:** Normal — `analyze_cxr` does not
  run the extended reasoning pass.

## `image_id` Convention

Use a stable, human-readable identifier — typically the image filename stem:

```python
from pathlib import Path
image_id = Path(image_path).stem  # e.g. "patient_001_pa"
```

This is logged server-side and appears in any error messages, making
debugging easier.

## Error Handling

| Condition | `success` | `error` field |
|---|---|---|
| Model inference error | `false` | Description of failure |
| Invalid base64 | `false` | Decoding error message |
| Image too large (unresized) | `true` | Garbled `answer` — no error raised |
| Server cold start timeout | — | `requests.exceptions.Timeout` |

On `success=false`, do not proceed to `cxr_localization`. Record the error
in `PipelineMeta.errors` and return a partial `CXRAnalysisResult`.
