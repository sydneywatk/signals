"""Signal C — SEC 8-K material event + active state bill match.

Detection:
1. For each ICP company's recent 8-K (with trigger items 7.01/8.01/1.05/2.05),
   run Claude extraction to detect state-regulation discussion.
2. If extraction returns mentions_state_regulation=true with one or more states
   and topics, query the recent state bill bag for matching active bills.
3. Emit one Signal C per (company, 8-K) where at least one matching bill exists.
"""
from __future__ import annotations

import logging
from typing import Any

from signals.detectors import Signal
from signals.detectors._common import (
    classify_bill_to_topics,
    days_since,
    state_name_to_code,
)
from signals.enrich import icp

logger = logging.getLogger(__name__)


def detect_signal_c(
    eight_ks_by_cik: dict[str, list[dict[str, Any]]],
    extractions_by_accession: dict[str, dict[str, Any]],
    active_bills: list[dict[str, Any]],
    topics: list[dict[str, Any]],
) -> list[Signal]:
    signals: list[Signal] = []

    for cik, eight_ks in eight_ks_by_cik.items():
        company = icp.cik_to_company(cik)
        if not company:
            continue

        for ek in eight_ks:
            extraction = extractions_by_accession.get(ek["accession"])
            if not extraction or not extraction.get("mentions_state_regulation"):
                continue

            states_in_8k = {s.upper() for s in extraction.get("states", [])}
            topic_ids_in_8k = set(extraction.get("topics", []))
            if not states_in_8k or not topic_ids_in_8k:
                continue

            matching_bills = []
            for bill in active_bills:
                bill_state_code = state_name_to_code(bill["jurisdiction"]["name"])
                if bill_state_code not in states_in_8k:
                    continue
                bill_topic_ids = {t["id"] for t in classify_bill_to_topics(bill, topics)}
                if bill_topic_ids & topic_ids_in_8k:
                    matching_bills.append(bill)

            if not matching_bills:
                continue

            filing_age = days_since(ek["filing_date"])

            signals.append(Signal(
                signal_type="C",
                company_cik=cik,
                company_name=company["name"],
                title=f"{company['name']} 8-K flags {len(states_in_8k)} states; {len(matching_bills)} active bills found",
                why_now=_why_now(company, ek, extraction, matching_bills),
                evidence={
                    "filing": {
                        "accession": ek["accession"],
                        "filing_date": ek["filing_date"],
                        "items": ek["items"],
                        "url": ek.get("url"),
                        "is_synthetic_demo": ek.get("is_synthetic_demo", False),
                    },
                    "supporting_text": extraction.get("supporting_text", ""),
                    "states": sorted(states_in_8k),
                    "topics": sorted(topic_ids_in_8k),
                    "active_bills": [{
                        "id": b["id"],
                        "identifier": b["identifier"],
                        "title": b["title"],
                        "jurisdiction": b["jurisdiction"]["name"],
                        "first_action_date": b.get("first_action_date"),
                        "openstates_url": b.get("openstates_url"),
                    } for b in matching_bills],
                },
                score_inputs={
                    "filing_recency_days": float(filing_age),
                    "bill_count": float(len(matching_bills)),
                    "bill_stage": float(_bill_stage_signal(matching_bills)),
                    "match_specificity": float(_match_specificity(extraction)),
                },
            ))

    logger.info("Signal C: %d signals across %d companies", len(signals), len(eight_ks_by_cik))
    return signals


def _why_now(company, ek, extraction, bills):
    snippet = (extraction.get("supporting_text") or "").strip()
    if len(snippet) > 220:
        snippet = snippet[:220].rsplit(" ", 1)[0] + "..."
    bill_refs = ", ".join(f"{b['jurisdiction']['name'][:2].upper()} {b['identifier']}"
                          for b in bills[:4])
    return (
        f"{company['name']} filed an 8-K ({', '.join(ek['items'])}) on {ek['filing_date']} "
        f"disclosing exposure to {', '.join(sorted(set(extraction['states'])))} state "
        f"regulation. Public quote: \"{snippet}\" "
        f"Active matching bills: {bill_refs}."
    )


def _bill_stage_signal(bills: list[dict]) -> int:
    """Rough proxy: count bills with a latest_action_date in the last 30 days."""
    recent = sum(1 for b in bills if (b.get("latest_action_date") and days_since(b["latest_action_date"]) <= 30))
    return recent


def _match_specificity(extraction: dict) -> int:
    """1 if exactly one state is named (very specific), else fall off."""
    n = len(extraction.get("states", []))
    if n == 0: return 0
    if n == 1: return 3
    if n <= 3: return 2
    return 1
