"""Capture or refresh fixtures by hitting live APIs.

Usage:
    python scripts/capture_fixtures.py                       # all scenarios
    python scripts/capture_fixtures.py openstates            # one source
    python scripts/capture_fixtures.py openstates:recent_bills_pharma_ca_2026-05  # one scenario

Forces USE_LIVE_APIS=true internally regardless of the .env value. Each
captured response is redacted of API keys appearing in URLs / headers before
write. Not run in CI — manual operator tool only.

Scenario registry lives in `CAPTURES` below. Add a new entry whenever you add
a new code path that consumes fixtures.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

# Force live mode BEFORE any signals.* imports load settings
os.environ["USE_LIVE_APIS"] = "true"

# ruff: noqa: E402
from signals.fixtures import save_fixture
from signals.logging_config import setup_logging
from signals.settings import validate_for_live_mode

logger = logging.getLogger(__name__)


# Registry: source -> { scenario: callable returning JSON-serializable data }
# Populated lazily inside _build_registry() so source modules aren't imported
# until needed (lets `--help` work without all deps installed).
def _build_registry() -> dict[str, dict[str, Callable[[], Any]]]:
    from signals.sources import edgar, lda, openstates  # noqa: F401  # filled in later steps

    return {
        "openstates": {
            # TODO: enable once openstates.get_recent_bills is implemented (build task 5)
            # "recent_bills_pharma_ca_2026-05": lambda: openstates.get_recent_bills(
            #     states=["CA"], topics=["drug_price_transparency"], days=14
            # ),
        },
        "lda": {
            # TODO: enable once lda.get_recent_registrations is implemented (build task 7)
        },
        "edgar": {
            # TODO: enable once edgar.get_10k_risk_factors is implemented (build task 6)
        },
        "anthropic": {
            # TODO: enable once enrich/extraction + embeddings are implemented (build task 10)
        },
    }


def parse_targets(args: list[str], registry: dict[str, dict[str, Callable]]) -> list[tuple[str, str]]:
    """Resolve CLI args (none / source / source:scenario) to (source, scenario) pairs."""
    if not args:
        return [(s, sc) for s, scenarios in registry.items() for sc in scenarios]

    targets: list[tuple[str, str]] = []
    for raw in args:
        if ":" in raw:
            source, scenario = raw.split(":", 1)
            if source not in registry:
                raise SystemExit(f"Unknown source: {source}")
            if scenario not in registry[source]:
                raise SystemExit(f"Unknown scenario for {source}: {scenario}")
            targets.append((source, scenario))
        else:
            if raw not in registry:
                raise SystemExit(f"Unknown source: {raw}")
            targets.extend((raw, sc) for sc in registry[raw])
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", nargs="*",
                        help="source or source:scenario (default: all registered)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be captured without making API calls")
    args = parser.parse_args()

    setup_logging()
    missing = validate_for_live_mode()
    if missing and not args.dry_run:
        logger.error("Cannot capture: missing env vars %s", missing)
        return 2

    registry = _build_registry()
    targets = parse_targets(args.targets, registry)

    if not targets:
        logger.warning("No scenarios registered yet. Add entries to CAPTURES in this file.")
        return 0

    for source, scenario in targets:
        if args.dry_run:
            logger.info("[dry-run] would capture %s:%s", source, scenario)
            continue
        try:
            data = registry[source][scenario]()
            save_fixture(source, scenario, data)
        except Exception as exc:
            logger.exception("Failed to capture %s:%s: %s", source, scenario, exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
