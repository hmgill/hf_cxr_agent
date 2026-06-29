# CXR Localization — Technical Reference

## Model / endpoint

| Property  | Value |
|-----------|-------|
| Model     | NV-Locate-Anything-3B |
| Endpoint  | `https://nv-locate-anything-3b.fastmcp.app/mcp` |
| Transport | Streamable HTTP (SDK `MCPServerStreamableHttp`) |
| Auth      | None |

The endpoint is also reachable as a direct Modal HTTP API
(`https://mathgcloud--locate-anything-3b-api.modal.run`), but the pipeline uses
the MCP path so the server handles preprocessing and original-space coordinates.

## Why programmatic, not mcp_servers=[...]

`locate_objects` / `ground_phrase` / `point_at` all take `image_b64`. If the
server were registered on the agent, the orchestrator model would have to emit
the base64 image as a tool argument — the same context blowup that motivated the
`reason_cxr` design. So `run_localization` opens an `MCPServerStreamableHttp`
session itself and calls `call_tool` directly.

## Timeouts

Set `client_session_timeout_seconds=300`: the MCP ClientSession default
per-request timeout is 5s, but NV-Locate cold starts plus generation
(`max_new_tokens=2048`, `hybrid` mode) run well beyond that.

## Coordinate space

In MCP mode the server's `_preprocess_image` resizes internally and returns
boxes in ORIGINAL pixel space. The tool therefore sends the full-resolution
image and does NOT scale coordinates afterwards. (The direct-Modal path resizes
client-side and scales boxes back; that path is not used here.)

## Category alignment

`locate_objects` echoes a `categories` array. Boxes are aligned to it by index
(`categories[i % len(categories)]`), matching the endpoint test harness. Each
location category is mapped back to its finding text via the `salient_findings`
map the orchestrator passed in.

## Failure handling

`run_localization` returns an empty `LocalizationResult` (no regions) on MCP
errors, non-JSON responses, or `success=false`, logging the cause to stderr.
The orchestrator treats localization as best-effort and never blocks the report
on it. NV-Locate returns no per-box confidence, so `LocalizedRegion.score` is a
1.0 placeholder.

## Alternatives

For single-target grounding use `ground_phrase` (phrase = a finding/location);
for landmark points use `point_at`. The pipeline default is `locate_objects`
because it grounds all salient locations in one call.
