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
    from signals.sources import openstates

    return {
        "openstates": {
            # Bag of recent bills matching the pharma-pricing query across all states.
            # Used by Signal A clustering and by smoke tests.
            "recent_bills_drug_pricing": lambda: openstates.search_bills(
                query='"drug pricing" OR "prescription drug" OR "pharmacy benefit manager"',
                updated_since="2026-03-01",
                max_pages=3,
            ),
            # One detailed bill for the get_bill_detail fixture path.
            # Bill ID resolved on first capture and pinned manually if it churns.
            "bill_detail_sample": lambda: _capture_sample_bill_detail(),
        },
        "lda": {
            # Recent LD-1 registrations on pharma-relevant issue codes
            "recent_registrations_pharma": lambda: _import_lda().get_recent_registrations(
                issue_codes=["HCR", "PHA", "MMM"],  # Health Care Reform, Pharmacy, Medicare/Medicaid
                since="2026-01-01",
                max_pages=8,  # large window since issue filter runs client-side
            ),
        },
        "edgar": {
            # Pfizer 10-K Item 1A — proves edgartools extraction on the canonical case
            "risk_factors_78003": lambda: _import_edgar().get_10k_risk_factors("78003"),
            # Pfizer recent 8-Ks filtered to the GTM-relevant item codes.
            # 90 day window so we catch a 7.01 / 8.01 example for Signal C.
            # Fixture window is 365d so we catch trigger items (Pfizer's most recent
            # 7.01/8.01 filings are ~5-6 months back). Pipeline still uses 14d live.
            "recent_8ks_78003": lambda: _import_edgar().get_recent_8ks(
                "78003", item_codes=["7.01", "8.01", "1.05", "2.05"], days=365, max_filings=50,
            ),
        },
        "anthropic": {
            # populated in build task 10
        },
    }


def _import_edgar():
    from signals.sources import edgar
    return edgar


def _import_lda():
    from signals.sources import lda
    return lda


def _capture_sample_bill_detail() -> Any:
    """Pick the first bill from the recent_bills query and fetch its detail."""
    from signals.sources import openstates

    bills = openstates.search_bills(
        query='"drug pricing"', updated_since="2026-03-01", max_pages=1
    )
    if not bills:
        raise RuntimeError("No bills returned; can't seed bill_detail_sample fixture")
    return openstates.get_bill_detail(bills[0]["id"])


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
