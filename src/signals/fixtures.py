"""Fixture loader for fixtures-first development.

When `USE_LIVE_APIS=false` (default), every source module routes through
`load_fixture()` instead of hitting the network. When `USE_LIVE_APIS=true`,
`scripts/capture_fixtures.py` uses `save_fixture()` to seed/refresh.

Layout: `tests/fixtures/<source>/<scenario>.json`. Scenario names should be
human-readable (e.g., `recent_bills_pharma_ca_2026-05.json`).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from signals.settings import FIXTURES_DIR

logger = logging.getLogger(__name__)


class FixtureMissing(FileNotFoundError):
    """Raised when a requested fixture file does not exist.

    Surfaces with the expected path so the operator knows what to capture.
    """


def fixture_path(source: str, scenario: str) -> Path:
    return FIXTURES_DIR / source / f"{scenario}.json"


def load_fixture(source: str, scenario: str) -> Any:
    path = fixture_path(source, scenario)
    if not path.exists():
        raise FixtureMissing(
            f"Fixture not found: {path}. "
            f"Run `python scripts/capture_fixtures.py {source}:{scenario}` to seed it."
        )
    with path.open() as f:
        data = json.load(f)
    logger.debug("Loaded fixture %s/%s (%d bytes)", source, scenario, path.stat().st_size)
    return data


def save_fixture(source: str, scenario: str, data: Any) -> Path:
    path = fixture_path(source, scenario)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, default=str, sort_keys=True)
    logger.info("Saved fixture %s/%s -> %s", source, scenario, path)
    return path


def fixture_exists(source: str, scenario: str) -> bool:
    return fixture_path(source, scenario).exists()
