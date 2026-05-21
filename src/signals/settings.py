"""Environment + runtime configuration for the signals pipeline.

Loads `.env` once at import. Required-key validation is deferred to live mode
via `validate_for_live_mode()` — fixture-mode runs work with everything unset.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "y", "on")


USE_LIVE_APIS: bool = _as_bool(os.getenv("USE_LIVE_APIS"), default=False)

OPENSTATES_API_KEY: str = os.getenv("OPENSTATES_API_KEY", "")
LDA_API_KEY: str = os.getenv("LDA_API_KEY", "")
LDA_BASE_URL: str = os.getenv("LDA_BASE_URL", "https://lda.senate.gov/api/v1").rstrip("/")
EDGAR_USER_AGENT: str = os.getenv("EDGAR_USER_AGENT", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
TRIGGER_SECRET: str = os.getenv("TRIGGER_SECRET", "")

OPENSTATES_RPS: float = float(os.getenv("OPENSTATES_RPS", "0.8"))
LDA_RPM: int = int(os.getenv("LDA_RPM", "120"))
EDGAR_RPS: float = float(os.getenv("EDGAR_RPS", "8"))

FIXTURES_DIR: Path = REPO_ROOT / "tests" / "fixtures"
DATA_DIR: Path = REPO_ROOT / "data"
CONFIG_DIR: Path = REPO_ROOT / "config"


def load_pipeline_config() -> dict:
    """Load config/settings.yml. Returns the `pipeline` block as a dict."""
    import yaml
    path = CONFIG_DIR / "settings.yml"
    if not path.exists():
        return {}
    with path.open() as f:
        return (yaml.safe_load(f) or {}).get("pipeline", {})

_REQUIRED_FOR_LIVE = (
    "OPENSTATES_API_KEY",
    "LDA_API_KEY",
    "EDGAR_USER_AGENT",
    "ANTHROPIC_API_KEY",
)


def validate_for_live_mode() -> list[str]:
    """Return env var names that must be set for live mode but currently aren't.

    Slack webhook and TRIGGER_SECRET are intentionally not required — Slack
    falls back to stdout when unset, and TRIGGER_SECRET only matters for the
    Modal /trigger endpoint.
    """
    return [name for name in _REQUIRED_FOR_LIVE if not os.getenv(name)]
