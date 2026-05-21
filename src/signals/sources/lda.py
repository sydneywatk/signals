"""Lobbying Disclosure Act (LDA) REST client.

API surface used by the pipeline:
- get_recent_registrations(issue_codes, since, ...)  — LD-1 filings filtered by issue area

In fixture mode dispatches through `load_fixture("lda", "<scenario>")`.

Notes from `docs/source-research/02-lda.md`:
- Auth: `Authorization: Token <key>` header
- Rate limit: 120 req/min on authenticated tier
- `page_size` silently capped at 25 — paginate explicitly
- Watermark on `dt_posted` (filings can arrive late; never use `filing_period`)
- HOST CUTOVER: lda.senate.gov sunsets 2026-06-30 → lda.gov/api/v1/. Configurable via LDA_BASE_URL.
- The `/filings/` endpoint exposes NO structured `general_issue_code` filter (verified against OpenAPI spec). Issue codes live inside `lobbying_activities[].general_issue_code` and are filtered client-side. Server-side filters available: `filing_type`, `filing_year`, `filing_dt_posted_after/before`, `filing_specific_lobbying_issues` (free text), registrant/client/lobbyist fields.
- Filing types: RR (LD-1 registration), Q1-Q4 (quarterly LD-2), A (amendment).
"""
from __future__ import annotations

import logging
from typing import Any

from signals.fixtures import load_fixture
from signals.http.client import HostConfig, get_http_client
from signals.settings import LDA_API_KEY, LDA_BASE_URL, LDA_RPM, USE_LIVE_APIS

logger = logging.getLogger(__name__)

_HOST_KEY = "lda"
_PAGE_SIZE = 25
_host_registered = False


def _ensure_host_registered() -> None:
    global _host_registered
    if _host_registered:
        return
    headers = {"Accept": "application/json"}
    if LDA_API_KEY:
        headers["Authorization"] = f"Token {LDA_API_KEY}"
    get_http_client().register_host(_HOST_KEY, HostConfig(
        rate_per_sec=LDA_RPM / 60.0,
        extra_headers=headers,
    ))
    _host_registered = True


def get_recent_registrations(
    *,
    issue_codes: list[str] | None = None,
    text_search: str | None = None,
    since: str | None = None,
    filing_year: int | None = None,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """Recent LD-1 registrations (`filing_type=RR`).

    Server-side filters: `since` (-> filing_dt_posted_after), `filing_year`,
    `text_search` (-> filing_specific_lobbying_issues, supports phrase/OR/NOT).
    Issue-code filtering happens client-side after fetch — `issue_codes` keeps
    filings where any `lobbying_activities[].general_issue_code` matches.
    """
    if not USE_LIVE_APIS:
        return load_fixture("lda", "recent_registrations_pharma")

    _ensure_host_registered()
    client = get_http_client()

    params: dict[str, Any] = {
        "filing_type": "RR",
        "ordering": "dt_posted",
        "page_size": _PAGE_SIZE,
    }
    if filing_year:
        params["filing_year"] = filing_year
    if since:
        params["filing_dt_posted_after"] = since
    if text_search:
        params["filing_specific_lobbying_issues"] = text_search

    raw: list[dict[str, Any]] = []
    page = 1
    while page <= max_pages:
        params["page"] = page
        resp = client.get(_HOST_KEY, f"{LDA_BASE_URL}/filings/", params=params)
        body = resp.json()
        raw.extend(body.get("results", []))
        if not body.get("next"):
            break
        page += 1

    # Client-side issue code filter
    if issue_codes:
        wanted = set(issue_codes)
        filtered = [
            f for f in raw
            if any(a.get("general_issue_code") in wanted for a in f.get("lobbying_activities", []))
        ]
    else:
        filtered = raw

    logger.info("LDA get_recent_registrations(issue_codes=%s, text=%r, since=%s): %d/%d after filter",
                issue_codes, text_search, since, len(filtered), len(raw))
    return filtered
