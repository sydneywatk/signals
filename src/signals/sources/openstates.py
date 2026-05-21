"""OpenStates v3 client.

API surface used by the pipeline:
- search_bills(query, jurisdiction=, updated_since=, ...)  — paginated bill search
- get_recent_bills_for_topic(query, days, jurisdictions=)  — convenience over search_bills
- get_bill_detail(bill_id, include=)                       — single bill with sources/abstracts

In fixture mode (USE_LIVE_APIS=false) every public function dispatches through
`load_fixture("openstates", <scenario>)`. The same canonical scenario is used
across pipeline runs; tests load fixtures directly for finer-grained cases.

Notes from source research (`docs/source-research/01-openstates.md`):
- Auth: `X-API-KEY` header (also accepts `apikey` query param)
- Rate limit: not formally documented; default tier ~1 req/sec, 500/day; we run at 0.8/sec
- Pagination via `?page=N`; response.pagination.{page, per_page, max_page, total_items}
- Bill text is link-only via the API. For embedding we use title + abstracts.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from signals.fixtures import load_fixture
from signals.http.client import HostConfig, get_http_client
from signals.settings import OPENSTATES_API_KEY, OPENSTATES_RPS, USE_LIVE_APIS

logger = logging.getLogger(__name__)

_HOST_KEY = "openstates"
_BASE_URL = "https://v3.openstates.org"
_DEFAULT_INCLUDES = ("abstracts", "sponsorships", "sources")
_host_registered = False


def _ensure_host_registered() -> None:
    global _host_registered
    if _host_registered:
        return
    client = get_http_client()
    client.register_host(_HOST_KEY, HostConfig(
        rate_per_sec=OPENSTATES_RPS,
        extra_headers={"X-API-KEY": OPENSTATES_API_KEY} if OPENSTATES_API_KEY else None,
    ))
    _host_registered = True


def search_bills(
    query: str,
    *,
    jurisdiction: str | None = None,
    updated_since: str | None = None,
    classification: str | None = None,
    include: list[str] | None = None,
    sort: str = "updated_desc",
    per_page: int = 20,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Search bills. Live mode hits OpenStates; fixture mode returns the canonical bag."""
    if not USE_LIVE_APIS:
        return load_fixture("openstates", "recent_bills_drug_pricing")

    _ensure_host_registered()
    client = get_http_client()
    includes = include if include is not None else list(_DEFAULT_INCLUDES)

    params: dict[str, Any] = {
        "q": query,
        "sort": sort,
        "per_page": per_page,
        "include": includes,
    }
    if jurisdiction:
        params["jurisdiction"] = jurisdiction
    if updated_since:
        params["updated_since"] = updated_since
    if classification:
        params["classification"] = classification

    results: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        params["page"] = page
        resp = client.get(_HOST_KEY, f"{_BASE_URL}/bills", params=params)
        body = resp.json()
        page_results = body.get("results", [])
        results.extend(page_results)
        pagination = body.get("pagination", {})
        if page >= pagination.get("max_page", 0):
            break
    logger.info("OpenStates search_bills(%r): %d results", query, len(results))
    return results


def get_recent_bills_for_topic(
    query: str,
    *,
    days: int = 14,
    jurisdictions: list[str] | None = None,
    max_pages: int = 5,
) -> list[dict[str, Any]]:
    """Bills updated in the last `days` matching `query`, optionally per jurisdiction.

    OpenStates' updated_since accepts ISO date. If `jurisdictions` is given,
    we call search_bills once per jurisdiction (state filter is single-valued)
    and merge.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    if not jurisdictions:
        return search_bills(query, updated_since=since, max_pages=max_pages)

    merged: list[dict[str, Any]] = []
    for jur in jurisdictions:
        merged.extend(search_bills(query, jurisdiction=jur, updated_since=since, max_pages=max_pages))
    return merged


def get_bill_detail(
    bill_id: str,
    *,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch a single bill by OpenStates ID (e.g. `ocd-bill/...`)."""
    if not USE_LIVE_APIS:
        return load_fixture("openstates", "bill_detail_sample")

    _ensure_host_registered()
    client = get_http_client()
    includes = include if include is not None else list(_DEFAULT_INCLUDES) + ["versions", "actions"]
    resp = client.get(_HOST_KEY, f"{_BASE_URL}/bills/{bill_id}", params={"include": includes})
    return resp.json()
