# Orchestrator Architecture Reference

## Framework: OpenAI Agents SDK

The orchestrator uses the [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
with Claude as the underlying model via the LiteLLM extension.

### Model Wiring

```python
from agents.extensions.models.litellm_model import LitellmModel

model = LitellmModel(
    model="anthropic/claude-opus-4-6",
    api_key=os.environ["ANTHROPIC_API_KEY"],
)
```

The SDK's `LitellmModel` bridges the OpenAI Agents SDK tool-call protocol to
Anthropic's API. Vision inputs (base64 image content blocks) are passed as
multimodal message content; Claude Opus 4.6 supports this natively.

### Agent Definition Pattern

```python
from agents import Agent, Runner

orchestrator = Agent(
    name="CXR Orchestrator",
    model=model,
    instructions=SYSTEM_PROMPT,        # loaded from SKILL.md body
    tools=[triage_image, reason_cxr, localize_findings, speak_findings],
)

result = await Runner.run(orchestrator, input_message)
```

### Tool Registration

Each sub-skill is wrapped as a `@function_tool`. The SDK auto-generates the
JSON schema from Python type annotations and docstrings. Pydantic models are
used for structured inputs/outputs.

### Session & State

Use `Runner.run()` with an explicit `session` for multi-turn conversation
support. The session preserves the full message history so the agent can
refer back to previous triage or reasoning results within the same case.

### Tracing

Enable SDK tracing in development:

```python
from agents import enable_verbose_stdout_logging
enable_verbose_stdout_logging()
```

In production, wire to OpenTelemetry or the SDK's built-in trace exporter.

## Sub-Skill Invocation Order

```
cxr-triage  →  cxr-reasoning  →  cxr-localization  →  (cxr-voice)
    ↑               ↑                   ↑                    ↑
  always         on pass            on reasoning          on request
```

## AgentSkills.io Catalog Injection

At startup, `agent/registry.py` scans `skills/` and generates an XML catalog
injected into the orchestrator's system prompt. This gives the model awareness
of all available skills for progressive disclosure.

```xml
<available_skills>
  <skill>
    <name>cxr-triage</name>
    <description>...</description>
    <location>skills/cxr-triage/SKILL.md</location>
  </skill>
  <!-- ... -->
</available_skills>
```
