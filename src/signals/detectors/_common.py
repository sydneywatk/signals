"""Helpers shared across detectors."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
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
