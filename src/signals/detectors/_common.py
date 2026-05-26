"""Helpers shared across detectors."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}


def state_name_to_code(name: str) -> str | None:
    return _STATE_NAME_TO_CODE.get(name.strip().lower())


def bill_text_for_similarity(bill: dict[str, Any]) -> str:
    """Title + concatenated abstracts. The unit of comparison for clustering."""
    abstracts = " ".join(a.get("abstract", "") for a in bill.get("abstracts", []))
    return f"{bill.get('title', '')} {abstracts}".strip()


def days_since(iso_str: str) -> int:
    """Days between today and an ISO date or ISO datetime."""
    s = iso_str.split("T")[0]
    return (date.today() - date.fromisoformat(s)).days


# Lower-cased substrings in `latest_action_description` that indicate a bill
# has been enacted (signed into law, chaptered, etc.). After enactment, a
# bill is no longer an alert candidate beyond a short grace window.
_ENACTED_MARKERS = (
    "chaptered",
    "became law",
    "became a law",
    "signed by governor",
    "signed by the governor",
    "approved by the governor",
    "approved by governor",
    "filed with secretary of state",
    "act effective",
    "effective date",  # e.g. "Chapter X, effective date Y"
)

# Substrings indicating the bill has died — either explicitly killed by a
# chamber, withdrawn by sponsor, or vetoed without override. Unlike enacted
# bills (which stay alert-eligible for 30 days post-enactment), dead bills
# are always non-actionable: a corpse doesn't drive a buying moment.
_DEAD_MARKERS = (
    "postpone indefinitely",
    "postponed indefinitely",
    "withdrawn",
    "vetoed",
    "died in committee",
    "failed",
)
# Subphrases that look like dead-markers but actually mean the bill survived.
_DEAD_MARKER_OVERRIDES = (
    "veto override",   # legislature overrode the veto -> bill became law
    "override veto",
    "override of veto",
)


def is_bill_actionable(bill: dict[str, Any], *, max_post_enactment_days: int = 30) -> bool:
    """False iff the bill's latest action looks like enactment AND the enactment
    is older than `max_post_enactment_days`. Defends against alerting on bills
    that have already become law (Fix 1 from the 2026-05-21 audit)."""
    desc = (bill.get("latest_action_description") or "").lower()
    if not any(m in desc for m in _ENACTED_MARKERS):
        return True
    last = bill.get("latest_action_date") or bill.get("first_action_date")
    if not last:
        return True
    try:
        return days_since(last) <= max_post_enactment_days
    except (ValueError, TypeError):
        return True


def is_bill_alive(bill: dict[str, Any]) -> bool:
    """False iff the bill's latest action looks like a definitive death:
    postpone indefinitely, withdrawn, vetoed (without override), died in
    committee, or failed.

    Unlike enacted-bill detection, dead bills get no grace window — a killed
    bill is killed regardless of when the action happened. Pharma GA teams
    don't mobilize against dead legislation.
    """
    desc = (bill.get("latest_action_description") or "").lower()
    if not desc:
        return True
    # Veto override means the bill survived — short-circuit before the dead check
    if any(o in desc for o in _DEAD_MARKER_OVERRIDES):
        return True
    return not any(m in desc for m in _DEAD_MARKERS)


def is_current_session(bill: dict[str, Any], *, max_stale_days: int = 180) -> bool:
    """False iff the bill's latest action is older than `max_stale_days` — proxy
    for "bill is from a prior session that has gone quiet" (Fix 4 from the
    2026-05-21 audit)."""
    last = bill.get("latest_action_date") or bill.get("first_action_date")
    if not last:
        return True
    try:
        return days_since(last) <= max_stale_days
    except (ValueError, TypeError):
        return True


def is_actionable_and_current(bill: dict[str, Any]) -> bool:
    """Convenience: applies all three filters at once. Use to prune candidate
    bill lists. A bill must be (a) not stale-enacted, (b) within the active
    session window, AND (c) not killed (postponed indefinitely / vetoed /
    withdrawn / failed / died in committee)."""
    return is_bill_actionable(bill) and is_current_session(bill) and is_bill_alive(bill)


def classify_bill_to_topics(bill: dict[str, Any], topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match a bill's title + abstracts against each topic's keywords and search terms."""
    haystack = bill_text_for_similarity(bill).lower()
    matched = []
    for topic in topics:
        keywords = topic.get("risk_factor_keywords", []) + topic.get("openstates_search_terms", [])
        if any(kw.lower() in haystack for kw in keywords):
            matched.append(topic)
    return matched


def classify_cluster_to_topics(bills: list[dict[str, Any]], topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of topics matched by any bill in the cluster, ranked by mention count."""
    counts: dict[str, int] = {}
    by_id = {t["id"]: t for t in topics}
    for bill in bills:
        for topic in classify_bill_to_topics(bill, topics):
            counts[topic["id"]] = counts.get(topic["id"], 0) + 1
    sorted_ids = sorted(counts, key=lambda x: -counts[x])
    return [by_id[i] for i in sorted_ids]


def lda_filings_for_topics(lda_filings: list[dict[str, Any]],
                            topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter LDA filings to those touching any of the topic's `lda_issue_codes`."""
    wanted_codes = set()
    for t in topics:
        wanted_codes.update(t.get("lda_issue_codes", []))
    if not wanted_codes:
        return []
    matched = []
    for f in lda_filings:
        f_codes = {a.get("general_issue_code") for a in f.get("lobbying_activities", [])}
        if f_codes & wanted_codes:
            matched.append(f)
    return matched
