---
name: cxr_summary
description: >
  Distil the free-text findings from cxr_reasoning into a structured summary:
  a normal/abnormal verdict, a salience-ranked list of findings, and a concise
  plain-language clinical summary (FindingsSummary). The orchestrator generates
  this ITSELF (no separate summariser model call) and records it via the
  record_findings_summary tool as an INTERMEDIATE step — the salient findings
  then guide NV-Locate-Anything localization. A standalone tool
  (tools/summarize.py) is also available for offline re-summarisation.
license: Proprietary
compatibility: >
  Native path: the orchestrator model (claude-opus-4-6 today; claude-sonnet-4-6
  when switched) emits the summary as record_findings_summary tool arguments —
  no extra model call. Standalone path: requires anthropic>=0.45 and
  ANTHROPIC_API_KEY; model default claude-sonnet-4-6 (CXR_SUMMARY_MODEL).
metadata:
  author: hmgill
  version: "2.1"
  produced_by: orchestrator-tool-args
---

# CXR Summary Skill

Turns the reasoning model's free-text output into a structured, machine-readable
summary (FindingsSummary). It is the bridge between free-text reasoning and the
localization step: the salient findings recorded here are the targets passed to
NV-Locate-Anything.

## How it is produced (no extra model)

The orchestrator already holds `reason_cxr`'s `answer` and `thinking` in context,
so it distils the summary itself and passes the structured fields as arguments to
the `record_findings_summary` tool. That tool runs no model — it validates the
fields into a FindingsSummary and echoes them back. The summary is therefore an
intermediate produced by the orchestrator's own turn, not a separate call. When
the orchestrator runs on Sonnet 4.6, a single Sonnet instance covers
triage-gating, reasoning orchestration, this summary, and the localization
decision.

## Tool: record_findings_summary

| Argument           | Type                   | Description |
|--------------------|------------------------|-------------|
| `is_normal`        | bool                   | True for an unremarkable study |
| `salient_findings` | dict[str, list[str]]   | location -> list of findings, most-salient first |
| `summary`          | string                 | 1-3 sentence clinical summary |

`salient_findings` maps anatomical location -> finding (e.g.
`{"right lung": "fluid"}`), inserted most-to-least salient, one entry per
location. Returns the JSON-serialised FindingsSummary. See
[assets/summary_schema.json](assets/summary_schema.json).

## Feeding localization

For an abnormal study, the orchestrator passes the recorded `salient_findings`
as `targets_json` to `localize_findings`, so NV-Locate-Anything grounds the
findings the summary judged most salient. Normal studies skip localization.

## Location vocabulary (for NV-Locate-Anything)

NV-Locate-Anything is open-vocabulary but grounds GENERAL anatomical structures
much more reliably than hyperspecific landmarks. So every location KEY in
`salient_findings` must be a general region drawn from a controlled list, and
the orchestrator maps each finding to the closest one:

- "right lower lobe" / "right costophrenic angle" -> "right lower lung"
- "aortic arch" / "ascending aorta" -> "aorta"

The finding (the VALUE) stays as descriptive as needed, e.g.
`{"right lower lung": ["round opacity", "possible effusion"]}` (multiple findings per region go in the list). Keys are
normalised to lowercase. The controlled list lives in `orchestrator_agent.py`
as `ANATOMY_LOCATIONS` — edit it to match the label space your nv-locate
deployment actually recognises.

## Grounding constraint

Findings are derived ONLY from the reasoning `answer`. The `thinking` block
informs salience ranking but is never a source of new findings, and is never
surfaced to users. This constraint lives in the orchestrator system prompt.

## Standalone re-summarisation (optional)

`tools/summarize.py` exposes `run_summarize(findings_text, reasoning_trace="",
model=None)` for re-summarising saved reports offline without re-running image
inference. This path DOES make its own Anthropic call (default
claude-sonnet-4-6); it is not used by the live pipeline.

## Pipeline Position

```
cxr_triage (valid=True)
    -> cxr_reasoning  (free-text answer + thinking)
        -> record_findings_summary  <- this skill (intermediate, no extra model)
            -> cxr_localization  (guided by salient_findings)
```

## References

See [references/REFERENCE.md](references/REFERENCE.md) for model selection and
parsing notes.
