"""ICP lookups against `config/companies.yml` and `config/topics.yml`."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import yaml

from signals.settings import CONFIG_DIR

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_companies() -> list[dict[str, Any]]:
    path = CONFIG_DIR / "companies.yml"
    with path.open() as f:
        return (yaml.safe_load(f) or {}).get("companies", [])


@lru_cache(maxsize=1)
def load_topics() -> list[dict[str, Any]]:
    path = CONFIG_DIR / "topics.yml"
    with path.open() as f:
        return (yaml.safe_load(f) or {}).get("topics", [])


def _normalize_cik(cik: str) -> str:
    return cik.lstrip("0") or "0"


def cik_to_company(cik: str) -> dict[str, Any] | None:
    target = _normalize_cik(cik)
    for c in load_companies():
        if _normalize_cik(c["cik"]) == target:
            return c
    return None


def state_to_companies(state: str) -> list[dict[str, Any]]:
    state_upper = state.upper()
    return [c for c in load_companies() if state_upper in c.get("states_of_operation", [])]


def registrant_alias_to_company(alias: str) -> dict[str, Any] | None:
    candidate = alias.strip().lower()
    for c in load_companies():
        variants = list(c.get("lda_registrant_aliases", [])) + [c["name"]]
        if any(v.strip().lower() == candidate for v in variants):
            return c
    return None


def topic_by_id(topic_id: str) -> dict[str, Any] | None:
    for t in load_topics():
        if t["id"] == topic_id:
            return t
    return None
