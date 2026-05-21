"""SEC EDGAR client built on `edgartools`.

API surface used by the pipeline:
- get_10k_risk_factors(cik)                        — Item 1A text for the latest 10-K
- get_recent_8ks(cik, item_codes, days)            — recent 8-Ks filtered by item codes, with body text

In fixture mode, both dispatch through `load_fixture("edgar", "<scenario>_<cik>")`.

Notes from `docs/source-research/03-sec-edgar.md`:
- UA required (Akamai 403 if omitted)
- edgartools handles the TOC/anchor/iXBRL mess for Item 1A extraction — don't roll our own
- 8-K item codes are pre-parsed and exposed as `Filing.items` (comma-joined string)
- 10 req/sec official; we run at 8 req/sec via setting EDGAR_RPS
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import edgar

from signals.fixtures import load_fixture
from signals.settings import EDGAR_USER_AGENT, USE_LIVE_APIS

logger = logging.getLogger(__name__)

_identity_set = False


def _ensure_identity() -> None:
    """edgartools requires a one-time identity declaration before any request."""
    global _identity_set
    if _identity_set:
        return
    if not EDGAR_USER_AGENT:
        raise RuntimeError("EDGAR_USER_AGENT must be set for live mode")
    edgar.set_identity(EDGAR_USER_AGENT)
    _identity_set = True


def _parse_items(raw: str | None) -> list[str]:
    """`Filing.items` is a comma-joined string like '2.02,9.01'. Split + strip."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def get_10k_risk_factors(cik: str) -> dict[str, Any]:
    """Return the latest 10-K Item 1A risk factors text for `cik` (padded or unpadded).

    Returns: `{cik, accession, filing_date, text}`.
    """
    cik_clean = cik.lstrip("0") or "0"
    if not USE_LIVE_APIS:
        return load_fixture("edgar", f"risk_factors_{cik_clean}")

    _ensure_identity()
    company = edgar.Company(cik_clean)
    filings = company.get_filings(form="10-K").head(1)
    if not filings:
        raise RuntimeError(f"No 10-K filings found for CIK {cik}")
    filing = filings[0]
    tenk = filing.obj()
    text = tenk.risk_factors or ""
    return {
        "cik": cik_clean,
        "accession": filing.accession_number,
        "filing_date": str(filing.filing_date),
        "text": text,
    }


def get_recent_8ks(
    cik: str,
    *,
    item_codes: list[str] | None = None,
    days: int = 14,
    max_filings: int = 25,
) -> list[dict[str, Any]]:
    """Return recent 8-Ks for `cik` filtered to those whose `items` overlap `item_codes`.

    Each returned dict: `{cik, accession, filing_date, items, text, url}`. In fixture
    mode, the canonical list is loaded as-is (already filtered).
    """
    cik_clean = cik.lstrip("0") or "0"
    if not USE_LIVE_APIS:
        return load_fixture("edgar", f"recent_8ks_{cik_clean}")

    _ensure_identity()
    company = edgar.Company(cik_clean)
    cutoff = date.today() - timedelta(days=days)
    filings = company.get_filings(form="8-K").head(max_filings)

    wanted = set(item_codes) if item_codes else None
    results: list[dict[str, Any]] = []
    for filing in filings:
        if filing.filing_date < cutoff:
            continue
        items = _parse_items(filing.items)
        if wanted is not None and not (set(items) & wanted):
            continue
        try:
            text = filing.text()
        except Exception as exc:
            logger.warning("Failed to fetch text for 8-K %s: %s", filing.accession_number, exc)
            text = ""
        results.append({
            "cik": cik_clean,
            "accession": filing.accession_number,
            "filing_date": str(filing.filing_date),
            "items": items,
            "text": text,
            "url": filing.filing_url,
        })
    logger.info("EDGAR get_recent_8ks(cik=%s, items=%s): %d matches",
                cik_clean, item_codes, len(results))
    return results
