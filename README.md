# hf_cxr_agent

An agentic chest X-ray (CXR) analysis pipeline. A Claude orchestrator (built on
the OpenAI Agents SDK) runs the workflow and delegates the vision work to
specialized models served over FastMCP: triage an image, reason over findings,
summarize them, localize them, and optionally render an overlay or read the
impression aloud.

## Pipeline

```
triage → encode → reason → summarize → localize → visualize / speak
```

1. **Triage** — confirm it's a CXR, classify the projection (PA/AP/lateral), grade quality. Stops here if invalid.
2. **Encode** — resize and base64-encode for the reasoning model.
3. **Reason** — NV-Reason-CXR-3B produces detailed free-text findings.
4. **Summarize** — distill into a structured verdict (normal/abnormal) with a salience-ranked, location-keyed finding map.
5. **Localize** — for abnormal studies, NV-Locate-Anything-3B grounds each finding with bounding boxes.
6. **Visualize / speak** — optional interactive HTML overlay and/or ElevenLabs voice.

Follow-up questions are answered from the findings already in context.

## Components

- **Orchestrator** (`cxr_orchestrator/`) — OpenAI Agents SDK agent running Claude via LiteLLM; exposes each stage as a tool. Ships as a base agent and a `SandboxAgent` that can author custom visualizations.
- **Triage agent** (`cxr_triage/`) — standalone or sub-agent image validation.
- **Skills** (`skills/`) — triage, reasoning, summary, localization, qa, orchestrator, in the AgentSkills.io progressive-disclosure format; discovered by `registry.py`.
- **Tools** (`tools/`) — the stage implementations plus the sandbox overlay helpers.

## Models & services

| Role | Model / service |
|------|-----------------|
| Orchestration & triage | Claude (via Anthropic SDK / LiteLLM) |
| Summarization | Claude |
| CXR reasoning | NV-Reason-CXR-3B — `https://mcp-nv-reason-cxr-3b.fastmcp.app/mcp` |
| Localization | NV-Locate-Anything-3B — `https://nv-locate-anything-3b.fastmcp.app/mcp` |
| Voice (optional) | ElevenLabs TTS |

## Usage

```bash
export ANTHROPIC_API_KEY=...        # required
export ELEVENLABS_API_KEY=...       # optional, for voice

# Full pipeline
python cxr_agents/cxr_orchestrator/orchestrator_agent.py \
  --image path/to/cxr.jpg --query "Describe all findings."

# Triage only
python cxr_agents/cxr_triage/triage_agent.py path/to/cxr.jpg
```

Results are returned as structured JSON and can be saved as paired JSON + Markdown reports.

## Status

Early-stage. The agent and tool layer are in place; shared data models
(`models/pipeline.py`), a runtime entry point, and packaging
(`requirements.txt`) are not yet committed.

## Disclaimer

For research and educational use only. Not a medical device; not for clinical
diagnosis or treatment.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
