---
name: cxr_qa
description: >
  Answer follow-up questions about an already-analysed chest X-ray. After the
  pipeline (triage -> reasoning -> summary -> localization) has run, the
  orchestrator holds the vetted findings in context and answers further
  questions grounded in them, re-inspecting the image via reason_cxr ONLY when a
  question needs a visual detail not already on record. Never surfaces the
  reasoning model's chain-of-thought. Exposed through CXRSession.ask() and the
  `--chat` CLI loop.
license: Proprietary
compatibility: >
  Native path: the orchestrator model (claude-opus-4-6 today; claude-sonnet-4-6
  when switched) answers from the retained conversation and may re-call
  reason_cxr (NV-Reason-CXR-3B via FastMCP) for targeted re-inspection. No new
  model or server is introduced by this skill.
metadata:
  author: hmgill
  version: "1.0"
  produced_by: orchestrator-followup-turn
---

# CXR Q&A / Follow-up Skill

Turns the one-shot pipeline into a short conversation. Once a study has been
analysed, the user can ask follow-up questions ("Is the effusion layering?",
"Could this opacity be a nipple shadow?", "What's the cardiothoracic ratio?")
and the orchestrator answers them in the context of the findings it already
established.

## How it works

`CXRSession.analyze()` runs the full pipeline and retains the conversation
(`agent_result.to_input_list()`). `CXRSession.ask(question)` replays that history
plus the new question, so the model answers grounded in the vetted findings
already on record — no re-triage, no re-summarising, no re-localising.

Crucially, the base64 image never lives in the retained history: it was created
and consumed inside `reason_cxr` on the initial run. So follow-ups that can be
answered from the findings text cost only a small text turn. A follow-up that
needs a fresh look at the pixels (e.g. "look again at the left apex") triggers
another `reason_cxr` call with a targeted `prompt`; that tool re-encodes the
image internally, keeping the payload out of the model context as always.

## Grounding and safety constraints

- Answers are grounded in the reasoning `answer` and the recorded
  `FindingsSummary`. The model does not invent findings beyond what the image
  models reported.
- The chain-of-thought (`reasoning.thinking`) is NEVER surfaced or quoted, in
  follow-ups exactly as in the initial summary.
- Out-of-scope questions (anything a CXR cannot answer) are declined with a
  recommendation for the appropriate next step, not guessed at.

These constraints live in the `## Follow-up questions` section of the
orchestrator system prompt.

## Voice interplay

When voice mode is on (`--voice`), `--voice-scope` controls whether follow-up
answers are spoken:

| scope      | initial spoken_summary | follow-up answers |
|------------|------------------------|-------------------|
| `summary`  | spoken (default)       | silent            |
| `followup` | silent                 | spoken            |
| `all`      | spoken                 | spoken            |

Only the orchestrator-authored `spoken_summary` and follow-up answer text are
ever synthesised — never the thinking block. The initial narration is prefixed
with a spoken AI/not-a-diagnosis disclaimer (audio loses the visual caveats);
follow-up answers are spoken without the prefix to stay conversational.

## Usage

```
# one-shot (unchanged)
python3 ./cxr-agent/main.py --image ./cxr2.jpg

# follow-up Q&A
python3 ./cxr-agent/main.py --image ./cxr2.jpg --chat

# analysis + spoken summary, then a spoken Q&A conversation
python3 ./cxr-agent/main.py --image ./cxr2.jpg --chat --voice --voice-scope all
```

## Pipeline Position

```
cxr_triage -> cxr_reasoning -> record_findings_summary -> cxr_localization
                                          |
                                          +-- CXRSession retains context
                                                  -> cxr_qa  <- this skill (ask())
```

## Registration

Register this skill the same way as the others so it appears in the system
prompt's `{skill_catalog}` (the registry's `generate_catalog_xml()`); if the
registry auto-discovers `skills/<name>/SKILL.md`, dropping this folder in is
enough. No new tool is registered — the capability rides on the existing
`reason_cxr` tool plus the retained conversation.
