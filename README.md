# hf_cxr_agent

An agentic chest X-ray (CXR) analysis pipeline. A Claude-driven orchestrator
(built on the OpenAI Agents SDK) coordinates specialized medical vision models —
served over FastMCP endpoints — to triage an image, reason over its findings,
summarize them, ground them spatially, and optionally render an interactive
overlay or speak the impression aloud.

The LLM plans and sequences the workflow; the heavy vision work is delegated to
purpose-built models. Every stage is a discrete, testable tool.

## Pipeline

```
image ─▶ triage ─▶ encode ─▶ reason ─▶ summarize ─▶ localize ─▶ visualize / speak
          │                    │                       │
     is it a CXR?        NV-Reason-CXR-3B         NV-Locate-Anything-3B
     projection?          (via FastMCP)             (via FastMCP)
     quality grade?
```

1. **Triage** — validate the image is a chest X-ray, classify the projection
   (PA / AP / lateral / oblique), and grade quality. If invalid, the pipeline
   stops here.
2. **Encode** — resize and base64-encode the image for the reasoning model.
3. **Reason** — run NV-Reason-CXR-3B for detailed free-text findings.
4. **Summarize** — distill the findings into a structured verdict
   (normal/abnormal) with a salience-ranked, location-keyed finding map.
5. **Localize** — for abnormal studies, ground each finding with
   NV-Locate-Anything-3B to produce bounding boxes in original pixel space.
6. **Visualize / speak** — optionally render an interactive HTML overlay of the
   findings, and/or synthesize the impression to speech with ElevenLabs.

Follow-up questions are answered from the vetted findings already in context
(the QA skill), re-inspecting the image only when needed.

## Architecture

- **Orchestrator** (`cxr_orchestrator/`) — an OpenAI Agents SDK agent running
  Claude (`claude-opus-4-6` via the LiteLLM bridge). Exposes the pipeline stages
  as function tools and enforces the ordering rules. Ships in two variants: a
  base `Agent`, and a `SandboxAgent` that gets shell + filesystem access to
  author bespoke visualizations on the fly.
- **Triage agent** (`cxr_triage/`) — a standalone agent wrapping the triage
  skill; runs on its own for batch preprocessing or as an orchestrator sub-step.
- **Skills** (`skills/`) — six skills (triage, reasoning, summary, localization,
  qa, orchestrator) authored in the AgentSkills.io progressive-disclosure
  format: a catalog of names + descriptions is injected at startup, with each
  skill's full body and resources loaded on demand.
- **Registry** (`registry.py`) — discovers the skills and generates the XML
  catalog embedded in the orchestrator's system prompt.
- **Tools** (`tools/`) — the stage implementations: `triage`, `encode_image`,
  `locate`, `summarize`, `tts`, `report`, plus the sandbox overlay helpers and
  run-scoped `context`.
- **Overlay sandbox** (`sandbox/cxr-overlay/`) — a worked `render_overlay.py` +
  HTML template that draws the localized boxes onto the X-ray; the agent can
  copy and adapt it for custom views (severity breakdowns, zoom crops, etc.).

### Models & services

| Role | Model / service | Access |
|------|-----------------|--------|
| Orchestration & triage | Claude `claude-opus-4-6` | Anthropic SDK / LiteLLM |
| Summarization | Claude `claude-sonnet-4-6` | Anthropic SDK |
| CXR reasoning | NV-Reason-CXR-3B (Qwen2.5-VL backbone) | FastMCP |
| Finding localization | NV-Locate-Anything-3B | FastMCP |
| Voice (optional) | ElevenLabs TTS | REST |

## Getting Started

### Prerequisites

- Python 3.11+
- Network access to the FastMCP model endpoints
- An Anthropic API key (ElevenLabs key only if using voice)

### Install

```bash
git clone https://github.com/hmgill/hf_cxr_agent.git
cd hf_cxr_agent
pip install openai-agents litellm anthropic fastmcp pillow
```

### Configure

```bash
export ANTHROPIC_API_KEY=...        # required
export ELEVENLABS_API_KEY=...       # optional, for voice
export ELEVENLABS_VOICE_ID=...      # optional
export CXR_OUT_DIR=/tmp/cxr_out     # optional, overlay/report output
```

### Run

Full pipeline:

```bash
python cxr_agents/cxr_orchestrator/orchestrator_agent.py \
  --image path/to/cxr.jpg --query "Describe all findings."
```

Triage only:

```bash
python cxr_agents/cxr_triage/triage_agent.py path/to/cxr.jpg
```

Results are returned as structured JSON and can be persisted as paired
JSON + Markdown reports.

## Status

Early-stage and under active development. The agent and tool layer are in place;
the shared data models (`models/pipeline.py`), a runtime entry point, and
packaging (`requirements.txt` / deployment files) are not yet committed. Treat
the CLI commands above as the intended entry points and adjust paths as the
project fills in.

## Disclaimer

Research and educational software only. Not a medical device and not for
clinical or diagnostic use.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
