# cxr-agent/main.py
import os
import sys
from pathlib import Path

os.environ["LITELLM_LOG"] = "ERROR"
os.environ["LITELLM_TELEMETRY"] = "False"

import logging
logging.basicConfig(
    filename="cxr_agent.log",
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Redirect fd 1 to /dev/null before importing the SDK
_real_stdout_fd = os.dup(1)
_devnull = os.open(os.devnull, os.O_WRONLY)
os.dup2(_devnull, 1)
os.close(_devnull)

# SDK import happens here — all stdout noise is swallowed
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "orchestrator_agent",
    PROJECT_ROOT / "cxr_agents" / "cxr_orchestrator" / "orchestrator_agent.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
run_pipeline = _mod.run_pipeline

# Restore real stdout now that the SDK is loaded
os.dup2(_real_stdout_fd, 1)
os.close(_real_stdout_fd)

import asyncio

async def main(image_path: str, query: str | None = None) -> None:
    # Suppress again during the run
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(1)
    os.dup2(devnull, 1)
    os.close(devnull)
    try:
        result = await run_pipeline(image_path, query)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    print(result.model_dump_json(indent=2))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CXR Agent — full analysis pipeline")
    parser.add_argument("--image", required=True, help="Path to the CXR image")
    parser.add_argument("--query", default=None, help="Optional user query")
    args = parser.parse_args()
    asyncio.run(main(args.image, args.query))
