"""Single source of truth for logging setup.

Called from main, capture_fixtures, and the FastAPI app. Honors LOG_LEVEL env
var; defaults to INFO. Logs to stdout — GitHub Actions and Modal both ship
stdout to their respective log viewers without extra config.
"""
from __future__ import annotations

import logging
import os
import sys


def setup_logging(level: str | None = None) -> None:
    resolved = (level or os.getenv("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=resolved,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    # httpx is chatty at DEBUG; pin it down unless the operator opts in
    if resolved != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
