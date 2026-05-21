"""FastAPI service for the live URL requirement.

Two endpoints:
- GET  /health   -> {status, last_run}, served from data/last_run.json
- POST /trigger  -> kicks off an out-of-cycle pipeline run; protected by
                    `X-Trigger-Secret` header matching `TRIGGER_SECRET` env var.

Modal deployment: see README §Deployment. The same module can run under plain
uvicorn for local dev: `uvicorn app.server:web --reload`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

# Repo root resolution works for both `uvicorn app.server:web` from repo root
# and Modal's flattened deploy layout.
REPO_ROOT = Path(__file__).resolve().parents[1]
LAST_RUN_PATH = REPO_ROOT / "data" / "last_run.json"

logger = logging.getLogger(__name__)
web = FastAPI(title="signals", version="0.1.0")


@web.get("/health")
def health() -> dict:
    last_run: dict | None = None
    if LAST_RUN_PATH.exists():
        try:
            last_run = json.loads(LAST_RUN_PATH.read_text())
        except Exception as exc:
            logger.warning("Failed to parse last_run.json: %s", exc)
    return {"status": "ok", "last_run": last_run}


@web.post("/trigger")
def trigger(x_trigger_secret: str = Header(default="")) -> dict:
    expected = os.environ.get("TRIGGER_SECRET", "")
    if not expected:
        raise HTTPException(status_code=503, detail="TRIGGER_SECRET not configured")
    if x_trigger_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid X-Trigger-Secret header")

    from signals.main import run_pipeline  # lazy import keeps cold start light
    try:
        summary = run_pipeline()
    except Exception as exc:
        logger.exception("Pipeline run failed via /trigger")
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {exc}") from exc
    return {"status": "ok", "summary": summary}


# ---------------------------------------------------------------------------
# Modal deployment glue. Lives in the same file so deploy = `modal deploy app/server.py`.
# Skipped at import time if `modal` isn't installed (e.g. CI lint job).
# ---------------------------------------------------------------------------
try:
    import modal  # type: ignore
except ImportError:
    modal = None  # CI doesn't need modal; only deploys need it

if modal is not None:
    # Build the image from this repo's pyproject.toml so dep drift can't bite us.
    _image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install_from_pyproject(str(REPO_ROOT / "pyproject.toml"))
        .add_local_dir(str(REPO_ROOT / "src"), remote_path="/root/src")
        .add_local_dir(str(REPO_ROOT / "config"), remote_path="/root/config")
        .add_local_dir(str(REPO_ROOT / "tests" / "fixtures"),
                        remote_path="/root/tests/fixtures")
    )
    _modal_app = modal.App("signals")

    @_modal_app.function(
        image=_image,
        secrets=[modal.Secret.from_name("signals-secrets")],
        timeout=600,
    )
    @modal.asgi_app()
    def fastapi_app():
        return web
