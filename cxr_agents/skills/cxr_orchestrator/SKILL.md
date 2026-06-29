---
name: cxr-orchestrator
description: >
  Master orchestrator for chest X-ray (CXR) analysis workflows. Use when
  a user submits a medical image or asks about a CXR finding. Manages the
  full analysis pipeline: image triage, AI-powered reasoning, anatomical
  localization, and voice I/O. Delegates to sub-skills based on task type.
  Entry point for all CXR-related requests.
license: Proprietary
compatibility: >
  Requires Python 3.11+. Requires openai-agents[litellm] and anthropic API
  key set as ANTHROPIC_API_KEY. Model: claude-opus-4-6 via LiteLLM bridge.
metadata:
  author: hmgill
  version: "1.0"
  framework: openai-agents-sdk
  model: anthropic/claude-opus-4-6
allowed-tools: Bash(python:*)
---

# CXR Orchestrator

The orchestrator is the central controller for all chest X-ray analysis tasks.
It receives user input (image + optional text or voice query), decides which
sub-skills to invoke, sequences their execution, and synthesises results into
a coherent response.

## Responsibilities

1. **Receive** an image (file path or base64) and an optional user query.
2. **Invoke `cxr-triage`** first — always — to validate the image before any
   downstream processing. If triage fails (not a CXR, or unsalvageable quality)
   stop and return the triage report directly.
3. **Invoke `cxr-reasoning`** (NV-Reason-CXR-3B) when triage passes — produces
   a structured radiology findings report.
4. **Invoke `cxr-localization`** (NV-Locate-Anything-3B) to spatially ground
   findings from the reasoning report onto the image.
5. **Invoke `cxr-voice`** when the user's input arrived as audio, or when the
   user explicitly requests spoken output.
6. **Return** a unified `CXRAnalysisResult` containing all sub-results.

## Decision Logic

```
receive(image, query)
  → always: triage(image)
      → if triage.valid == False → return triage_report STOP
      → if triage.valid == True:
          → reasoning(image, triage)
          → localization(image, reasoning_report)
          → if voice_requested: tts(summary)
          → return unified_result
```

## Tool Definitions

The orchestrator exposes the following tools to the OpenAI Agents SDK runner.
Each tool wraps one sub-skill's tool module. See `agents/cxr_orchestrator/orchestrator_agent.py`.

| Tool name             | Maps to skill       | Input                        | Output                    |
|-----------------------|---------------------|------------------------------|---------------------------|
| `triage_image`        | `cxr-triage`        | image path/bytes             | `TriageResult`            |
| `reason_cxr`          | `cxr-reasoning`     | image + triage context       | `ReasoningReport`         |
| `localize_findings`   | `cxr-localization`  | image + reasoning report     | `LocalizationResult`      |
| `speak_findings`      | `cxr-voice`         | summary text                 | audio bytes / stream URL  |

## Output Contract

Always return a `CXRAnalysisResult` typed dict:

```python
{
  "triage":        TriageResult,           # always present
  "reasoning":     ReasoningReport | None, # None if triage failed
  "localization":  LocalizationResult | None,
  "voice_audio":   bytes | None,
  "pipeline_meta": { "model": str, "latency_ms": int, "tokens_used": int }
}
```

## Error Handling

- Tool failures should be caught, logged, and surfaced in `pipeline_meta`.
- Never silently skip a failed sub-skill — mark it None and include an error
  message in `pipeline_meta`.
- On total failure, return a minimal result with only `triage` populated and
  an error field explaining what went wrong.

## References

- See [references/REFERENCE.md](references/REFERENCE.md) for agent architecture
  details and OpenAI Agents SDK wiring notes.
- See [agents/cxr_orchestrator/orchestrator_agent.py](../../agents/cxr_orchestrator/orchestrator_agent.py) for the runnable agent.
