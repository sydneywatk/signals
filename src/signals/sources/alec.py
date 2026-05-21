"""ALEC model bill corpus loader.

Per spec §6.5, we ship Signal D3 against a static hand-curated YAML corpus
rather than attempting fragile scraping. If scraping is added later, it would
fill the same shape (`config/model_bills.yml`).

This module has no USE_LIVE_APIS dispatch: the YAML *is* the source of truth.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import yaml

from signals.settings import CONFIG_DIR

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_model_bills() -> list[dict[str, Any]]:
    path = CONFIG_DIR / "model_bills.yml"
    if not path.exists():
        logger.warning("Model bill corpus not found at %s; returning empty list", path)
        return []
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    bills = data.get("model_bills", [])
    logger.info("Loaded %d model bills from %s", len(bills), path)
    return bills
