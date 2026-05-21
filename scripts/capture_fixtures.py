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
            "recent_bills_drug_pricing": lambda: _capture_recent_bills_drug_pricing(),
            # One detailed bill for the get_bill_detail fixture path.
            # Bill ID resolved on first capture and pinned manually if it churns.
            "bill_detail_sample": lambda: _capture_sample_bill_detail(),
            # Historical-with-actions captures for Signal E4. Limited to a few
            # states to stay under OpenStates daily quota; expand as needed.
            "historical_bills_ca": lambda: _import_openstates().get_historical_bills_with_actions(
                "ca", since="2023-01-01", max_pages=2),
            "historical_bills_or": lambda: _import_openstates().get_historical_bills_with_actions(
                "or", since="2023-01-01", max_pages=2),
            "historical_bills_md": lambda: _import_openstates().get_historical_bills_with_actions(
                "md", since="2023-01-01", max_pages=2),
        },
        "lda": {
            # Recent LD-1 registrations on pharma-relevant issue codes
            "recent_registrations_pharma": lambda: _import_lda().get_recent_registrations(
                issue_codes=["HCR", "PHA", "MMM"],  # Health Care Reform, Pharmacy, Medicare/Medicaid
                since="2026-03-01",  # within 60d of today (2026-05-21) so Signal A LDA gate fires
                max_pages=10,
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
            # Risk factor topic extraction for Pfizer's 10-K Item 1A
            "risk_factor_topics_78003": lambda: _capture_risk_factor_topics_pfizer(),
            # 8-K state regulation extraction for each of Pfizer's trigger 8-Ks
            "eight_k_state_regulation_0000078003-25-000167": lambda: _capture_8k_extraction(
                "0000078003-25-000167"
            ),
            "eight_k_state_regulation_0001193125-25-291406": lambda: _capture_8k_extraction(
                "0001193125-25-291406"
            ),
            "eight_k_state_regulation_0000078003-25-000159": lambda: _capture_8k_extraction(
                "0000078003-25-000159"
            ),
        },
    }


def _import_edgar():
    from signals.sources import edgar
    return edgar


def _import_lda():
    from signals.sources import lda
    return lda


def _import_openstates():
    from signals.sources import openstates
    return openstates


def _capture_risk_factor_topics_pfizer() -> Any:
    """Read the pre-captured Pfizer 10-K fixture, run live Claude extraction."""
    from signals.enrich import extraction, icp
    from signals.fixtures import load_fixture
    rf = load_fixture("edgar", "risk_factors_78003")  # mode-agnostic file read
    return extraction.extract_risk_factor_topics(
        rf["text"], icp.load_topics(),
        fixture_scenario="risk_factor_topics_78003",
    )


def _capture_8k_extraction(accession: str) -> Any:
    from signals.enrich import extraction
    from signals.fixtures import load_fixture
    eight_ks = load_fixture("edgar", "recent_8ks_78003")
    match = next((e for e in eight_ks if e["accession"] == accession), None)
    if not match:
        raise RuntimeError(f"8-K {accession} not in fixture; run edgar capture first")
    return extraction.extract_8k_state_regulation(
        match["text"],
        fixture_scenario=f"eight_k_state_regulation_{accession}",
    )


def _capture_sample_bill_detail() -> Any:
    """Pick the first bill from the recent_bills query and fetch its detail."""
    from signals.sources import openstates

    bills = openstates.search_bills(
        query='"drug pricing"', updated_since="2026-03-01", max_pages=1
    )
    if not bills:
        raise RuntimeError("No bills returned; can't seed bill_detail_sample fixture")
    return openstates.get_bill_detail(bills[0]["id"])


def _capture_recent_bills_drug_pricing() -> Any:
    """Per-state capture across transparency-active jurisdictions.

    The default free-text query returns a Kentucky-heavy bag (KY has lots of
    short-titled drug-related bills). For Signal A clustering we want variety
    across states with active transparency / PBM / affordability legislation.
    """
    import time
    from signals.sources import openstates

    JURISDICTIONS = ["ca", "or", "wa", "me", "md", "tx", "co", "ny"]
    QUERY = "prescription drug pricing OR pharmacy benefit manager OR drug affordability"

    merged: list = []
    for i, jur in enumerate(JURISDICTIONS):
        if i > 0:
            time.sleep(8.0)  # ultra-conservative — OpenStates free tier 429s on bursts
        page = openstates.search_bills(
            query=QUERY,
            jurisdiction=jur,
            updated_since="2024-06-01",  # wider window catches more bills per state
            max_pages=1,
        )
        merged.extend(page)
        logger.info("captured %d bills from %s (running total %d)", len(page), jur, len(merged))
    return merged


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
