# =============================================================================
# CXR Orchestrator — Hugging Face *Docker* Space (Chainlit chat UI)
# =============================================================================
# Model-driven sandbox variant: the agent gets shell + filesystem inside a
# UnixLocalSandboxClient and can author its own visualizations. No GPU here —
# reasoning is dispatched to the NV-Reason-CXR-3B worker behind the MCP server.
#
# Build context = the Space repo root, which must contain:
#   Dockerfile  app.py  requirements.txt  README.md  chainlit.md
#   .chainlit/config.toml  public/elements/OverlayFrame.jsx
#   cxr_agents/  tools/  skills/  models/  sandbox/
# =============================================================================
# The Agents SDK sandbox module requires Python >=3.12 (<3.14); use 3.12-slim.
FROM python:3.12-slim

# System libs for Pillow image decode.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# HF Docker Spaces run the container as uid 1000. Create a matching user with a
# writable home so pip --user installs and runtime temp writes (Chainlit's
# .files/ and .chainlit/ live under the working dir) work.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CXR_OUT_DIR=/tmp/cxr_out \
    CHAINLIT_HOST=0.0.0.0 \
    CHAINLIT_PORT=7860

WORKDIR $HOME/app

# Install deps first for layer caching.
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bring in app.py, the Chainlit assets (.chainlit/, public/, chainlit.md), and
# the unchanged project packages (cxr_agents/, tools/, skills/, models/, sandbox/).
COPY --chown=user . .

# HF Docker Spaces expose 7860 by convention (see app_port in README.md).
EXPOSE 7860

# Chainlit must be launched via its CLI (not `python app.py`). --headless keeps
# it from trying to open a browser; host/port also come from the env above.
CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "7860", "--headless"]
