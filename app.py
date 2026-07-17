"""
app.py — CXR Agent as a Hugging Face *Docker* Space (Chainlit chat UI)
=============================================================================
Chainlit chat frontend for the chest-X-ray agent, mirroring the OCT-B Space.
The user attaches a CXR to a message and asks for a read; follow-up questions
reuse the same loaded image and any localization / visualization already
produced.

Dashboard behaviour
-------------------
When a turn produces visualizations, each one becomes a **selector button** on
that turn's reply, and the most recent is rendered in a **right-hand panel**
(Chainlit's ``ElementSidebar``). Clicking any selector — including ones from
earlier turns — swaps the panel to that visualization, keeping a "read on the
left, picture on the right" dashboard feel inside the chat. The newest
visualization opens automatically after the model's first turn.

Voice
-----
When the agent calls ``speak_findings`` (ElevenLabs TTS), the resulting MP3 is
surfaced inline as a playable audio element on the reply. A session switch nudges
the agent to narrate the impression; the user can also just ask for audio.

Agent
-----
The model is set by ``CXR_MODEL`` (default ``gpt-5.4-mini``). The backend decides
the visualization path:

* **OpenAI Responses API** (e.g. ``gpt-5.4-mini``): the model-driven
  ``SandboxAgent`` with full sandbox capabilities (shell + filesystem incl.
  ``apply_patch``). A live ``UnixLocalSandboxClient`` session is provisioned per
  chat and injected via ``RunConfig``; the agent can author its own visualization
  scripts and run them.
* **Chat Completions API** (e.g. ``anthropic/claude-opus-4-6`` via LiteLLM): the
  base agent plus the on-demand ``render_finding_overlay`` tool, which drives a
  sandbox from host code (the model gets no hosted tools, which that API rejects).

Reasoning is dispatched to NV-Reason-CXR-3B via a streamable-HTTP MCP server;
findings are localized via LocateAnything-3B. Each user turn runs one agent turn,
collects whatever overlay HTML was produced, and exposes it via selector buttons +
the right-hand panel (plus a downloadable file).

Repo layout expected (this file at the Space root):
    Dockerfile
    app.py                 <- this file
    requirements.txt
    README.md              <- carries the HF Docker Space header (sdk: docker)
    chainlit.md            <- Chainlit splash/readme (optional)
    .chainlit/config.toml  <- Chainlit config (enables image attachments)
    public/elements/OverlayFrame.jsx
    cxr_agents/  tools/  skills/  models/  sandbox/   <- the unchanged project

Secrets (Space -> Settings -> Secrets):
    OPENAI_API_KEY       required when CXR_MODEL is an OpenAI model (the default)
    ANTHROPIC_API_KEY    required when CXR_MODEL is an anthropic/... model
    ELEVENLABS_API_KEY   optional (voice; free key is fine)

Variables (Space -> Settings -> Variables; all optional, sensible defaults):
    CXR_MODEL            main agent model (default gpt-5.4-mini). A bare OpenAI
                         name uses the Responses API (full sandbox, apply_patch);
                         a provider-prefixed name like anthropic/claude-opus-4-6
                         uses LiteLLM / Chat Completions (on-demand overlays).
    CXR_REASON_MCP_URL   reasoning MCP URL (used by tools/reason.py, per call)
    CXR_LOCALIZE_MCP_URL localization MCP URL (connected per call in tools/localize.py)
    ELEVENLABS_VOICE_ID  default voice (name or raw ID)
"""

from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

# Flat-layout note: the project packages (cxr_agents/, tools/, ...) live at the
# Space root, so putting the root on sys.path lets their imports resolve. There
# is no top-level `agents/` package, so `from agents import ...` still binds to
# the OpenAI Agents SDK.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chainlit as cl  # noqa: E402
from chainlit.input_widget import Switch  # noqa: E402

from agents import Runner  # noqa: E402
from agents.run import RunConfig  # noqa: E402
from agents.sandbox import SandboxRunConfig  # noqa: E402
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient  # noqa: E402

from cxr_agents.cxr_orchestrator.orchestrator_agent import (  # noqa: E402
    build_orchestrator,
    build_orchestrator_sandbox,
    resolve_model,
    DEFAULT_MODEL,
)
from tools import (  # noqa: E402
    CXRContext,
    SandboxManager,
    build_overlay_manifest,
    collect_overlay_outputs,
)

# ── Config (resolved once at startup) ─────────────────────────────────────────
OVERLAY_SKILL_DIR = ROOT / "sandbox" / "cxr-overlay"

# Persistent per-session output dir so produced HTML/MP3 can be read back + served.
OUT_ROOT = Path(os.environ.get("CXR_OUT_DIR", "/tmp/cxr_out"))
OUT_ROOT.mkdir(parents=True, exist_ok=True)

# Name of the action that selects a visualization into the right-hand panel.
VIEW_VIZ_ACTION = "view_viz"

# Name of the action fired by a gallery example card.
# Name of the action a gallery card fires. It *arms* an example (prefills the
# composer client-side, remembers the image server-side) but does NOT run the
# agent — the user edits the prompt and presses send themselves.
ARM_EXAMPLE_ACTION = "arm_example"

# Fired by the gallery's "Clear" button to disarm a selected example.
CLEAR_EXAMPLE_ACTION = "clear_example"

# Example gallery: bundled images + a CSV of (id,title,image,prompt,source) rows.
# The CSV lives next to its images at <Space root>/examples/.
EXAMPLES_DIR = ROOT / "examples"
EXAMPLES_CSV = EXAMPLES_DIR / "examples.csv"

# Hard cap on a remote CXR download (defends against a hostile/huge URL).
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


INTRO = (
    "### CXR Agent — chest X-ray interpretation\n\n"
    "Attach a **chest X-ray** image to your message and ask for a read "
    "(e.g. *“Interpret this chest X-ray”* or *“show me where the findings are”*). "
    "You can also **paste a direct image URL** instead of attaching a file, or "
    "**pick a case from the example gallery** below. "
    "Follow-up questions reuse the same image and any findings already produced — "
    "no need to re-attach.\n\n"
    "Any visualization the agent produces shows up as a button on its reply and "
    "opens in the panel on the right; use the buttons to switch between them. If "
    "you turn on the voice option (or ask for it), a spoken summary plays inline.\n\n"
    "_Decision support only — not a diagnostic device. Always confirm with a "
    "qualified radiologist._"
)


# ── Right-hand panel helpers ──────────────────────────────────────────────────

async def _show_in_sidebar(viz: dict, vid: str) -> None:
    """Render one visualization in the right-hand ElementSidebar panel."""
    await cl.ElementSidebar.set_title(viz["title"])
    await cl.ElementSidebar.set_elements(
        [cl.CustomElement(
            name="OverlayFrame",
            props={"html": viz["html"], "title": viz["title"]},
            display="inline",
        )],
        key=f"viz-{vid}",
    )


@cl.action_callback(VIEW_VIZ_ACTION)
async def on_view_viz(action: cl.Action):
    """A selector button was clicked — swap the right-hand panel to that viz."""
    registry: dict = cl.user_session.get("viz_registry") or {}
    vid = (action.payload or {}).get("viz_id")
    viz = registry.get(vid)
    if not viz:
        await cl.ElementSidebar.set_title("Visualization unavailable")
        await cl.ElementSidebar.set_elements(
            [cl.Text(content="This visualization is no longer available in this session.")]
        )
        return
    await _show_in_sidebar(viz, vid)


# ── Examples gallery + remote images ──────────────────────────────────────────

def _load_examples() -> list[dict]:
    """Read examples/examples.csv once and cache it.

    Each row becomes a dict with an absolute ``image_path`` resolved against the
    Space root. Rows whose image is missing are still returned (the caller filters
    them) so a half-filled CSV degrades gracefully.
    """
    cached = getattr(_load_examples, "_cache", None)
    if cached is not None:
        return cached

    rows: list[dict] = []
    if EXAMPLES_CSV.exists():
        with EXAMPLES_CSV.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rel = (row.get("image") or "").strip()
                rows.append({
                    "id": (row.get("id") or "").strip(),
                    "title": (row.get("title") or row.get("id") or "Example").strip(),
                    "prompt": (row.get("prompt")
                               or "Interpret this chest X-ray and describe any findings.").strip(),
                    "image": rel,
                    "image_path": str((ROOT / rel).resolve()) if rel else "",
                    "source": (row.get("source") or "").strip(),
                })
    _load_examples._cache = rows  # type: ignore[attr-defined]
    return rows


def _gallery_items() -> list[dict]:
    """Examples that have a real image, each with a base64 data-URI thumbnail.

    Thumbnails are inlined (rather than served) so the gallery renders without
    depending on Chainlit's static-file paths; the three demo images are small.
    """
    items: list[dict] = []
    for e in _load_examples():
        p = Path(e["image_path"])
        if not (e["image_path"] and p.exists()):
            continue
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        items.append({"id": e["id"], "title": e["title"],
                      "prompt": e["prompt"],
                      "src": f"data:{mime};base64,{b64}"})
    return items


_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_CTYPE_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}
_IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}


def _first_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0) if m else None


def _host_is_public(host: str | None) -> bool:
    """True only if every resolved address for ``host`` is a public IP.

    Blocks loopback/private/link-local/reserved targets so a pasted URL can't be
    used to probe the container's own metadata or internal network (SSRF).
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


def _ext_for(ctype: str, url: str) -> str | None:
    """Pick a sane file extension from Content-Type, falling back to the URL path."""
    if ctype in _CTYPE_EXT:
        return _CTYPE_EXT[ctype]
    suf = Path(urlparse(url).path).suffix.lower()
    if suf in _IMG_SUFFIXES:
        return ".jpg" if suf == ".jpeg" else suf
    if ctype.startswith("image/"):
        return ".jpg"
    return None


def _download_image(url: str, dest_dir: Path) -> str:
    """Download a direct image URL to ``dest_dir`` and return the local path.

    Synchronous (run via ``asyncio.to_thread``). Validates scheme + host, requires
    an image payload, and enforces ``MAX_DOWNLOAD_BYTES``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http(s) image URLs are supported")
    if not _host_is_public(parsed.hostname):
        raise ValueError("refusing to fetch from a non-public address")

    req = urllib.request.Request(url, headers={"User-Agent": "cxr-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — scheme checked above
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = _ext_for(ctype, url)
        if ext is None:
            raise ValueError(
                f"that URL doesn't look like a direct image (Content-Type: {ctype or 'unknown'})"
            )
        data = resp.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("image exceeds the size limit")

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"url_{uuid.uuid4().hex[:10]}{ext}"
    out.write_bytes(data)
    return str(out)


async def _resolve_cxr_path(message: cl.Message, out_dir) -> str | None:
    """Resolve this turn's CXR: an attached image wins, else a pasted image URL."""
    path = _first_cxr_path(message)
    if path:
        return path
    url = _first_url(message.content or "")
    if not url:
        return None
    try:
        return await asyncio.to_thread(_download_image, url, Path(out_dir))
    except Exception as e:  # noqa: BLE001 — surface the reason, keep the chat alive
        await cl.Message(content=f"⚠️ Couldn't load image from that URL: {e}").send()
        return None


# ── Per-session setup ─────────────────────────────────────────────────────────

@cl.on_chat_start
async def on_chat_start():
    # Session switches mirroring the old form checkboxes. The agent always *can*
    # render / narrate on its own judgement; these just nudge it for the session.
    # Both default ON so the overlay and the spoken impression appear by default.
    settings = await cl.ChatSettings([
        Switch(
            id="want_visual",
            label="Produce a visualization (overlay / chart)",
            initial=True,
        ),
        Switch(
            id="want_voice",
            label="Speak the impression aloud (ElevenLabs)",
            initial=True,
        ),
    ]).send()
    cl.user_session.set("want_visual", bool(settings.get("want_visual", True)))
    cl.user_session.set("want_voice", bool(settings.get("want_voice", True)))

    out_dir = OUT_ROOT / uuid.uuid4().hex
    out_dir.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    async with cl.Step(name="Preparing the CXR agent…", type="tool"):
        # Reasoning (NV-Reason-CXR-3B) and localization (LocateAnything-3B) run as
        # host-side tools that connect to their MCP servers per call and return
        # only text/boxes — the image bytes never enter the model context. So no
        # MCP server is attached to the agent here.
        octx = CXRContext(output_dir=str(out_dir))

        # Pick the agent path from the model's backend:
        #  • OpenAI Responses API (e.g. gpt-5.4-mini) → model-driven SandboxAgent
        #    with full capabilities (incl. apply_patch); a live sandbox session is
        #    provisioned here and injected via RunConfig so the model can author
        #    its own visualizations.
        #  • Chat Completions API (e.g. Claude via LiteLLM) → base agent + the
        #    on-demand render_finding_overlay tool (hosted tools like apply_patch
        #    are rejected there). The tool drives a sandbox from host code.
        _model_obj, supports_hosted = resolve_model()  # reads CXR_MODEL
        sandbox_session = None
        run_config = None
        if supports_hosted:
            try:
                sb_client = UnixLocalSandboxClient()
                sandbox_session = await sb_client.create(
                    manifest=build_overlay_manifest(OVERLAY_SKILL_DIR))
                await sandbox_session.apply_manifest()   # stage skills/cxr-overlay + inputs/output
                octx.sandbox_session = sandbox_session
                run_config = RunConfig(sandbox=SandboxRunConfig(session=sandbox_session))
                agent = build_orchestrator_sandbox()
            except Exception as e:  # noqa: BLE001 — degrade rather than failing the chat
                notes.append(f"⚠️ Sandbox unavailable — using on-demand overlays ({e}).")
                octx.sandbox = SandboxManager(OVERLAY_SKILL_DIR)
                agent = build_orchestrator()
        else:
            octx.sandbox = SandboxManager(OVERLAY_SKILL_DIR)  # lazily provisioned on first render
            agent = build_orchestrator()

    cl.user_session.set("octx", octx)
    cl.user_session.set("agent", agent)
    cl.user_session.set("run_config", run_config)
    cl.user_session.set("sandbox_session", sandbox_session)
    cl.user_session.set("out_dir", out_dir)
    cl.user_session.set("conversation", None)     # None until the first turn runs
    cl.user_session.set("seen_overlays", {})      # name -> (mtime_ns, size) signature
    cl.user_session.set("started_at", time.time())  # for output-dir mtime filtering
    cl.user_session.set("pending_example", None)  # armed gallery example (image only)
    cl.user_session.set("seen_audio", set())      # audio paths already surfaced
    cl.user_session.set("viz_registry", {})       # viz_id -> {name,title,html,path}

    content = INTRO + (("\n\n" + "\n".join(notes)) if notes else "")
    await cl.Message(content=content).send()

    # Example gallery — clickable cards. A click arms the case's image and
    # prefills its prompt into the composer (via arm_example); it does NOT run.
    items = _gallery_items()
    if items:
        await cl.Message(
            content="**Example gallery** — pick a case to prefill its prompt below. "
                    "Edit the prompt if you like, then press send to run.",
            elements=[cl.CustomElement(
                name="CXRGallery", props={"examples": items}, display="inline")],
        ).send()


@cl.on_settings_update
async def on_settings_update(settings):
    cl.user_session.set("want_visual", bool(settings.get("want_visual", False)))
    cl.user_session.set("want_voice", bool(settings.get("want_voice", False)))


# ── Live "thinking" + tool-output helpers ─────────────────────────────────────

# Friendly labels for the agent's tools, shown as live steps while it works.
# Anything not listed falls back to the raw tool name.
FRIENDLY_TOOL = {
    "triage_image":          "Triaging image",
    "encode_image":          "Encoding image",
    "reason_cxr":            "NV-Reason-CXR-3B reasoning",
    "analyze_cxr":           "NV-Reason-CXR-3B (quick)",
    "localize_findings":     "Localizing findings",
    "speak_findings":        "Synthesizing voice (ElevenLabs)",
    "render_finding_overlay":"Rendering finding overlay",
    "stage_overlay":         "Staging overlay inputs",
    "get_skill_body":        "Reading skill instructions",
    "get_skill_metadata":    "Reading skill metadata",
    "get_skill_reference":   "Reading skill reference",
    "get_skill_asset":       "Reading skill asset",
    "get_skill_script":      "Reading skill script",
    "run_skill_script":      "Running skill script",
    # Sandbox capabilities surface as ordinary function tools:
    "shell":                 "Running shell command",
    "apply_patch":           "Editing a file",
    "read_file":             "Reading a file",
    "write_file":            "Writing a file",
}


def _compact(value, limit: int = 800) -> str:
    """Stringify + trim a tool arg/output blob for display inside a Step."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    s = s.strip()
    return s if len(s) <= limit else s[:limit] + " …"


def _extract_audio_path(output) -> str | None:
    """Pull an .mp3 path out of a speak_findings tool result, if present."""
    val = output
    if isinstance(output, str):
        s = output.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                val = json.loads(s)
            except Exception:  # noqa: BLE001
                val = s
        else:
            val = s
    if isinstance(val, dict):
        val = val.get("path") or val.get("audio_path") or val.get("mp3")
    if isinstance(val, str) and val.strip().lower().endswith(".mp3"):
        p = val.strip()
        if Path(p).exists():
            return p
    return None


def _extract_narration(output) -> str | None:
    """Pull NV-Reason-CXR-3B's free-text reasoning ('thinking') out of a tool result."""
    val = output
    if isinstance(output, str):
        s = output.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                val = json.loads(s)
            except Exception:  # noqa: BLE001
                return s or None
        else:
            return s or None
    if isinstance(val, dict):
        text = val.get("thinking") or val.get("reasoning") or val.get("narration")
        text = (text or "").strip()
        return text or None
    return None


# Lines the model emits as artifact bookkeeping — redundant once the app surfaces
# overlays as buttons and audio as a player. Stripped from the reply text.
_ARTIFACT_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\*{0,2}\s*"
    r"(?:visualizations?|audio|overlays?|files?|outputs?|artifacts?)"
    r"\s*\*{0,2}\s*:?\s*$",
    re.IGNORECASE)
_ARTIFACT_TOKEN_RE = re.compile(
    r"(output/\S+\.html|\S+\.mp3|/tmp/\S+|cxr_speech|overlay_\w+\.html"
    r"|spoken summary (?:generated|saved)|narration saved)",
    re.IGNORECASE)


def _declutter_reply(text: str) -> str:
    """Drop the model's file-path bookkeeping lines; the UI conveys those instead."""
    out_lines: list[str] = []
    for line in (text or "").splitlines():
        if _ARTIFACT_HEADER_RE.match(line):
            continue
        if _ARTIFACT_TOKEN_RE.search(line):
            continue
        out_lines.append(line)
    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)  # collapse gaps left behind
    return cleaned.strip()


# Section headers in the read → a distinct icon + a real markdown header (bigger
# than the model's plain/bold line). Only known sections are touched; anything
# else is left exactly as written.
_SECTION_EMOJI = {
    "findings": "🔎", "impression": "📋", "location": "📍", "locations": "📍",
    "note": "📝", "notes": "📝", "disclaimer": "⚠️", "quality": "🎚️",
    "technical": "🎚️", "recommendation": "➡️", "recommendations": "➡️",
    "summary": "🧾", "comparison": "🔁", "limitations": "⚠️",
}
_SECTION_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s*)?\*{0,2}\s*([A-Za-z][A-Za-z ]{2,24}?)\*{0,2}\s*:?\s*$")


def _emojify_sections(text: str) -> str:
    """Rewrite known section headers as '### <icon> <Section>' for clearer structure."""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _SECTION_LINE_RE.match(line)
        if m:
            word = m.group(1).strip()
            emoji = _SECTION_EMOJI.get(word.lower())
            if emoji:
                out.append(f"### {emoji} {word.title()}")
                continue
        out.append(line)
    return "\n".join(out)


def _working_md(activity: str) -> str:
    """A prominent in-bubble 'agent is working' placeholder shown until the read lands."""
    return f"🔬 **Reading the chest X-ray…**\n\n⏳ _{activity}_"


# ── One chat turn ─────────────────────────────────────────────────────────────

def _first_cxr_path(message: cl.Message):
    """Return the local path of the first image attached to the message, if any."""
    for el in (message.elements or []):
        mime = (getattr(el, "mime", "") or "")
        if mime.startswith("image") and getattr(el, "path", None):
            return el.path
    return None


def _build_user_text(prompt: str, cxr_path, want_visual: bool, want_voice: bool) -> str:
    parts = [prompt or "Interpret this chest X-ray and produce a structured read."]
    if cxr_path:
        # The agent loads images from a path and never routes bytes through the
        # model context, so we pass the source path, not the image itself.
        parts.append(f"\n\nChest X-ray source: {cxr_path}")
    if want_visual:
        parts.append(
            "\n\nThe user would also like a visualization (the standard finding "
            "overlay, or a more apt chart if the findings call for one)."
        )
    if want_voice:
        parts.append(
            "\n\nAlso narrate the impression: call speak_findings on a concise "
            "spoken summary of the key findings."
        )
    return "".join(parts)


_MISSING = object()

# Where overlay HTML can land. The agent writes paths like ``output/overlay_*.html``
# relative to the app working dir (ROOT), while the private collector may copy into
# the per-session out_dir. We sweep all of these rather than trusting one source.
_APP_OUTPUT_DIR = ROOT / "output"


async def _collect_new_visualizations(octx, sandbox_session, out_dir, seen) -> list[str]:
    """HTML visualizations produced *or updated* this turn.

    Robust to where the overlay actually lands: we (1) still invoke the private
    collector for its sandbox→host copy side effect, then (2) independently sweep
    the per-session out_dir and the app-level ``output/`` dir for ``*.html``,
    copying any straggler into out_dir so downstream code (which reads
    ``out_dir / name``) always finds it. Dedup is by content signature
    (mtime + size) so a rewritten overlay re-surfaces and re-opens the panel.
    """
    out = Path(out_dir)
    started_at = float(cl.user_session.get("started_at") or 0.0)

    # 1. Best-effort: let the private collector run (it may copy sandbox → host).
    collector_names: list[str] = []
    try:
        if sandbox_session is not None:
            res = await collect_overlay_outputs(sandbox_session, out_dir)
            collector_names = list(res or [])
        else:
            collector_names = list(getattr(octx, "overlays", []) or [])
    except Exception as e:  # noqa: BLE001 — never let collection kill the turn
        print(f"[viz] collector error: {e!r}", file=sys.stderr, flush=True)

    # 2. Independent sweep. out_dir is per-session (safe to take wholesale); the
    #    shared app output/ dir is filtered to files touched since session start so
    #    a different session's leftovers don't bleed in. When the same filename
    #    exists in several places, the most recently modified copy wins — that's the
    #    one the agent just (re)wrote.
    found: dict[str, tuple[Path, float]] = {}

    def _consider(fp: Path) -> None:
        try:
            m = fp.stat().st_mtime
        except OSError:
            return
        cur = found.get(fp.name)
        if cur is None or m > cur[1]:
            found[fp.name] = (fp, m)

    for fp in out.rglob("*.html"):
        _consider(fp)
    if _APP_OUTPUT_DIR.is_dir():
        for fp in _APP_OUTPUT_DIR.glob("*.html"):
            try:
                if fp.stat().st_mtime >= started_at - 1.0:
                    _consider(fp)
            except OSError:
                pass
    for n in collector_names:
        p = out / n
        if p.exists():
            _consider(p)

    # 3. Copy stragglers into out_dir so the registry/OverlayFrame can read them.
    names: list[str] = []
    for name, (fp, _m) in found.items():
        dest = out / name
        try:
            if fp.resolve() != dest.resolve():
                shutil.copy2(fp, dest)
        except Exception as e:  # noqa: BLE001
            print(f"[viz] copy {fp} -> {dest} failed: {e!r}", file=sys.stderr, flush=True)
            continue
        names.append(name)

    # 4. Signature dedup.
    new: list[str] = []
    for name in names:
        try:
            st = (out / name).stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None  # not on disk yet; surface once
        if seen.get(name, _MISSING) != sig:
            new.append(name)
            seen[name] = sig

    print(f"[viz] collector={collector_names} swept={list(found)} new={new} "
          f"out_dir={out}", file=sys.stderr, flush=True)
    return new


def _latest_viz_id(registry: dict) -> str | None:
    """Most recently registered visualization id (dicts preserve insertion order)."""
    return next(reversed(registry), None) if registry else None


_SHOW_VIZ_RE = re.compile(
    r"\b(show|display|see|view|open|render|pull up|where|overlay|visuali[sz])", re.IGNORECASE)


def _wants_to_see_viz(text: str) -> bool:
    return bool(_SHOW_VIZ_RE.search(text or ""))


def _vid_for(name: str) -> str:
    """Deterministic id for a visualization filename, so re-emitting the same
    overlay updates one registry entry (and one button) instead of piling up."""
    return "v" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def _register_visualizations(new_files: list[str], out_dir: Path, registry: dict,
                             turn_seen: set):
    """Register/refresh visualizations, returning (actions, files, last_vid).

    Keyed by a stable per-filename id: a regenerated overlay refreshes the same
    entry rather than creating a duplicate. A selector button + download file is
    emitted at most once per filename per turn (``turn_seen`` guards repeats from
    the same file being rewritten several times within one turn), while the panel
    still re-opens to the freshest content.
    """
    actions: list = []
    files: list = []
    last_vid: str | None = None
    for name in new_files:
        fpath = out_dir / name
        if not fpath.exists():
            continue
        vid = _vid_for(name)
        registry[vid] = {   # refresh in place — latest content wins
            "name": name,
            "title": name,
            "html": fpath.read_text(errors="replace"),
            "path": str(fpath),
        }
        last_vid = vid
        if name in turn_seen:
            continue  # already has a button this turn; just refreshed above
        turn_seen.add(name)
        actions.append(cl.Action(
            name=VIEW_VIZ_ACTION,
            payload={"viz_id": vid},
            label=name,
            icon="image",
            tooltip="Show this visualization in the panel on the right",
        ))
        # Set mime explicitly: Chainlit infers mime via `filetype`, which only
        # detects binary signatures and returns None for .html — and the File
        # renderer calls .startsWith on the mime, crashing the UI on null.
        files.append(cl.File(
            name=name, path=str(fpath), mime="text/html", display="inline"))
    return actions, files, last_vid


@cl.on_message
async def on_message(message: cl.Message):
    """Resolve this turn's CXR and run the agent.

    Source precedence: an image the user attached, then a pasted image URL, then
    an armed gallery example (selected earlier; its prompt was prefilled into the
    composer). The armed example is consumed once, so it never leaks into a later
    turn.
    """
    octx = cl.user_session.get("octx")
    out_dir = cl.user_session.get("out_dir")
    if octx is None:
        await cl.Message(content="Session not initialised — please refresh to start a new chat.").send()
        return

    cxr_path = await _resolve_cxr_path(message, out_dir)

    pending = cl.user_session.get("pending_example")
    if cxr_path is None and pending and Path(pending["image_path"]).exists():
        cxr_path = pending["image_path"]
        # Show which example is being read so the transcript has visible context.
        await cl.Message(
            content=f"_Using example image: {pending['title']}_",
            elements=[cl.Image(name=pending["title"], path=pending["image_path"],
                               display="inline")],
        ).send()
    cl.user_session.set("pending_example", None)  # consumed (or unused) — clear it

    await _run_agent_turn((message.content or "").strip(), cxr_path)


@cl.action_callback(ARM_EXAMPLE_ACTION)
async def on_arm_example(action: cl.Action):
    """A gallery card was clicked — remember its image for the next send.

    Deliberately does NOT run the agent. The card's JSX prefills the prompt into
    the composer and focuses it; the agent runs only when the user presses send,
    at which point on_message picks up this armed image.
    """
    ex_id = (action.payload or {}).get("example_id")
    ex = next((e for e in _load_examples() if e["id"] == ex_id), None)
    if not ex or not ex["image_path"] or not Path(ex["image_path"]).exists():
        return
    cl.user_session.set("pending_example", {
        "id": ex["id"], "title": ex["title"], "image_path": ex["image_path"]})


@cl.action_callback(CLEAR_EXAMPLE_ACTION)
async def on_clear_example(action: cl.Action):
    """Disarm a previously selected example (gallery 'Clear' button)."""
    cl.user_session.set("pending_example", None)


async def _run_agent_turn(prompt_text: str, cxr_path: str | None) -> None:
    """Run exactly one agent turn for ``prompt_text`` + an already-resolved CXR path."""
    octx = cl.user_session.get("octx")
    agent = cl.user_session.get("agent")
    run_config = cl.user_session.get("run_config")
    sandbox_session = cl.user_session.get("sandbox_session")
    out_dir = cl.user_session.get("out_dir")
    conversation = cl.user_session.get("conversation")
    seen = cl.user_session.get("seen_overlays")
    seen_audio = cl.user_session.get("seen_audio")
    registry = cl.user_session.get("viz_registry") or {}
    want_visual = cl.user_session.get("want_visual", False)
    want_voice = cl.user_session.get("want_voice", False)

    if octx is None:
        await cl.Message(content="Session not initialised — please refresh to start a new chat.").send()
        return

    # The first turn needs a CXR to load. Later turns reuse it via history.
    if conversation is None and not cxr_path:
        await cl.Message(
            content="Attach a chest X-ray, paste a direct image URL, or pick an "
                    "example from the gallery to begin."
        ).send()
        return

    user_text = _build_user_text(
        prompt_text, cxr_path, want_visual, want_voice)
    run_input = user_text if conversation is None else conversation + [
        {"role": "user", "content": user_text}
    ]

    # Stream one agent turn. run_streamed() returns synchronously; the run is not
    # complete until stream_events() is fully consumed. We surface each tool call
    # as a live Step (the "thinking" view), stream the final answer tokens, and
    # capture any speak_findings audio path so we can play it inline.
    _turn_t0 = time.monotonic()
    _timer_id = uuid.uuid4().hex[:8]
    answer = cl.Message(content="", elements=[cl.CustomElement(
        name="TurnTimer", props={"id": _timer_id}, display="inline")])
    await answer.send()
    # Prominent working indicator (the faint step spinner alone reads as "stuck").
    # Replaced by streamed tokens once they start, or by the final read at the end.
    answer.content = _working_md("preparing the agent")
    await answer.update()
    streaming_started = False

    open_steps: list[list] = []     # [call_id, cl.Step, tool_name] awaiting output
    audio_path: str | None = None
    narration: str | None = None    # NV-Reason-CXR-3B "thinking" text, if any

    def _match_output(call_id):
        # Prefer matching by call_id; fall back to FIFO (calls resolve in order).
        for i, entry in enumerate(open_steps):
            if call_id is not None and entry[0] == call_id:
                return open_steps.pop(i)
        return open_steps.pop(0) if open_steps else None

    # Visualizations are surfaced live (as each is created) and also swept once
    # more after the turn. Actions/files accumulate across both; turn_seen_names
    # keeps a file rewritten several times this turn to a single button.
    turn_actions: list = []
    turn_files: list = []
    turn_seen_names: set = set()
    _sidebar_target: list = [None]  # vid to open once, at end of turn (avoids flicker)

    async def _surface_new_viz():
        """Collect newly-produced overlays and register their buttons.

        Buttons/files are surfaced live, but the panel is NOT opened here — the
        overlay file is rewritten several times within a turn, so opening on each
        rewrite caused the right panel to flicker open/closed. We just record the
        freshest viz and open it once after the turn.
        """
        new = await _collect_new_visualizations(octx, sandbox_session, out_dir, seen)
        if not new:
            return
        acts, fls, last = _register_visualizations(
            new, out_dir, registry, turn_seen_names)
        turn_actions.extend(acts)
        turn_files.extend(fls)
        cl.user_session.set("seen_overlays", seen)
        cl.user_session.set("viz_registry", registry)
        if last:
            _sidebar_target[0] = last

    try:
        result = Runner.run_streamed(
            agent, run_input, context=octx, max_turns=20, run_config=run_config)

        async for ev in result.stream_events():
            # Token-level deltas → stream the final answer as it's generated.
            if ev.type == "raw_response_event":
                data = getattr(ev, "data", None)
                if getattr(data, "type", "") == "response.output_text.delta":
                    if not streaming_started:
                        streaming_started = True
                        answer.content = ""  # drop the working placeholder
                    await answer.stream_token(getattr(data, "delta", "") or "")
                continue

            if ev.type != "run_item_stream_event":
                continue

            # A tool was invoked → open a live "thinking" step for it, and reflect
            # it in the prominent in-bubble status until real tokens arrive.
            if ev.name == "tool_called":
                raw = getattr(ev.item, "raw_item", None)
                tname = getattr(raw, "name", None) or "tool"
                cid = getattr(raw, "call_id", None) or getattr(raw, "id", None)
                step = cl.Step(name=FRIENDLY_TOOL.get(tname, tname), type="tool")
                step.input = _compact(getattr(raw, "arguments", None))
                await step.send()
                open_steps.append([cid, step, tname])
                if not streaming_started:
                    answer.content = _working_md(FRIENDLY_TOOL.get(tname, tname) + "…")
                    await answer.update()

            # That tool returned → close its step and (if it was the narrator)
            # capture the produced audio path.
            elif ev.name == "tool_output":
                raw = getattr(ev.item, "raw_item", None)
                cid = (raw.get("call_id") if isinstance(raw, dict)
                       else getattr(raw, "call_id", None))
                output = getattr(ev.item, "output", None)
                if output is None and isinstance(raw, dict):
                    output = raw.get("output")

                match = _match_output(cid)
                if match is not None:
                    _, step, tname = match
                    step.output = _compact(output)
                    await step.update()
                    if tname == "speak_findings":
                        ap = _extract_audio_path(output)
                        if ap and ap not in seen_audio:
                            audio_path = ap
                    elif tname in ("reason_cxr", "analyze_cxr"):
                        narr = _extract_narration(output)
                        if narr:
                            narration = narr

                # A tool just finished — if it produced a visualization, open it
                # immediately rather than waiting for the turn to complete.
                await _surface_new_viz()
    except Exception as e:  # noqa: BLE001 — keep the session alive on a failed turn
        # Graceful recovery: a common failure is the model re-rendering an overlay
        # via the sandbox and erroring (e.g. it improvised an absolute path). If the
        # user just wanted to *see* an overlay and one already exists, show that
        # instead of surfacing a raw sandbox error.
        last = _latest_viz_id(registry)
        if last and _wants_to_see_viz(prompt_text):
            try:
                await _show_in_sidebar(registry[last], last)
                answer.content = (
                    "I've re-opened the most recent overlay in the panel on the "
                    "right — it already shows the localized findings on this image.")
                answer.elements = [cl.CustomElement(
                    name="TimerStop", props={"id": _timer_id}, display="inline")]
                await answer.update()
                return
            except Exception:  # noqa: BLE001 — fall through to the error message
                pass
        answer.content = f"❌ Run failed: {e}"
        answer.elements = [cl.CustomElement(
            name="TimerStop", props={"id": _timer_id}, display="inline")]
        await answer.update()
        return

    # Final sweep for any straggler outputs (and the base-agent path), then use
    # the accumulated selectors/files for the reply.
    await _surface_new_viz()
    actions, files = turn_actions, turn_files

    # Open the right-hand panel exactly once for this turn, to the freshest viz.
    opened = False
    if _sidebar_target[0] and _sidebar_target[0] in registry:
        await _show_in_sidebar(registry[_sidebar_target[0]], _sidebar_target[0])
        opened = True

    # If the user asked to *see* a visualization but nothing new was produced this
    # turn (e.g. the agent answered "I already created the overlay" without
    # re-rendering), re-open the most recent existing one in the right-hand panel.
    reopened = False
    if not opened and not actions and _wants_to_see_viz(prompt_text):
        last = _latest_viz_id(registry)
        if last:
            await _show_in_sidebar(registry[last], last)
            reopened = True

    # Attach the spoken summary as a playable audio element, if one was produced.
    # auto_play starts it as soon as the reply renders (browsers may still gate
    # autoplay until the user has interacted with the page at least once).
    if audio_path:
        seen_audio.add(audio_path)
        cl.user_session.set("seen_audio", seen_audio)
        files.append(cl.Audio(
            name="Spoken summary", path=audio_path, display="inline",
            auto_play=True))

    # Attach a collapsible NV-Reason-CXR-3B narration preview, if one was produced.
    if narration:
        preview = narration.strip().replace("\n", " ")
        if len(preview) > 160:
            preview = preview[:160].rsplit(" ", 1)[0] + "…"
        files.append(cl.CustomElement(
            name="NarrationPreview",
            props={"preview": preview, "text": narration.strip()},
            display="inline",
        ))

    # Compose the final reply: the agent's structured read (decluttered of raw
    # file-path bookkeeping, since the UI surfaces those), then a short footer
    # that says what each artifact is *for*. We rewrite the streamed message once
    # at the end, so the final text is authoritative even if streaming didn't fire.
    content = _emojify_sections(_declutter_reply(str(result.final_output)))
    footer_bits: list[str] = []
    if actions:
        if len(actions) == 1:
            footer_bits.append(
                "📌 *Overlay ready — open it in the panel on the right to see the "
                "findings outlined on the X-ray.*")
        else:
            footer_bits.append(
                f"📌 *{len(actions)} overlays ready — use the buttons below to switch; "
                f"each opens in the panel on the right.*")
    elif reopened:
        footer_bits.append(
            "📌 *Re-opened the latest overlay in the panel on the right.*")
    if audio_path:
        footer_bits.append(
            "🔊 *Spoken summary — press play below to hear the impression.*")
    if narration:
        footer_bits.append(
            "🧠 *Model narration — expand it below to read the step-by-step reasoning.*")
    footer_bits.append(f"⏱ *Took {time.monotonic() - _turn_t0:.0f}s.*")
    if footer_bits:
        content += "\n\n---\n" + "\n\n".join(footer_bits)

    answer.content = content
    # Mount an invisible stopper so the live timer halts even if this Chainlit
    # version doesn't unmount the removed TurnTimer element on update.
    files.append(cl.CustomElement(
        name="TimerStop", props={"id": _timer_id}, display="inline"))
    answer.elements = files          # replaces the live timer element (turn done)
    answer.actions = actions
    await answer.update()

    # Persist history so follow-ups reuse the loaded image + produced artifacts.
    cl.user_session.set("conversation", result.to_input_list())


# ── Teardown ──────────────────────────────────────────────────────────────────

@cl.on_chat_end
async def on_chat_end():
    sandbox_session = cl.user_session.get("sandbox_session")
    octx = cl.user_session.get("octx")

    if sandbox_session is not None:
        try:
            await sandbox_session.aclose()
        except Exception:  # noqa: BLE001
            pass
    if octx is not None and getattr(octx, "sandbox", None) is not None:
        try:
            await octx.sandbox.aclose()
        except Exception:  # noqa: BLE001
            pass