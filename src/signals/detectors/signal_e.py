"""Signal E4 — Governor signing-track-record predictor.

Detection:
1. For each recent bill, classify to topic(s) via the taxonomy.
2. Look up the current governor + term-start date for that bill's state.
3. From historical bills in that state, find ones acted on during the current
   term whose Voyage cosine to the candidate bill is >= similarity_threshold.
4. Count executive-signature vs executive-veto outcomes on the matched
   historical bag.
5. If sign rate >= 0.70 with >= MIN_SAMPLE acted-on bills, emit a signal.
6. Boost for ICP company topic exposure (mirrors Signals A/D3).

Why a signal: governors have asymmetric incentives by party + state — knowing
the historical signing rate on similar bills predicts forward-pass probability.
A pharma GA team can deprioritize state X if the governor reliably vetoes
PBM bills, and load up resources on state Y where the governor signs them.

Data source: OpenStates exposes bill actions with `executive-signature` /
`executive-veto` / `became-law` classifications. We require the historical bag
fixture to include `actions` so this detector can work offline.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from signals.detectors import Signal
from signals.detectors._common import bill_text_for_similarity, classify_bill_to_topics, state_name_to_code
from signals.enrich import icp
from signals.enrich.embeddings import similarity_to_corpus
from signals.fixtures import FixtureMissing, load_fixture
from signals.settings import CONFIG_DIR

logger = logging.getLogger(__name__)

MIN_SAMPLE = 3
DEFAULT_SIMILARITY = 0.70
DEFAULT_SIGN_RATE = 0.70


@lru_cache(maxsize=1)
def load_governors() -> dict[str, dict[str, Any]]:
    """state-code -> {name, party, term_start}."""
    path = CONFIG_DIR / "governors.yml"
    if not path.exists():
        return {}
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {g["state"]: g for g in data.get("governors", [])}


def _action_outcome(actions: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return (outcome, action_date_iso). Outcome is 'signed' | 'vetoed' | None."""
    for a in actions or []:
        classes = a.get("classification") or []
        desc_low = (a.get("description") or "").lower()
        action_date = a.get("date")
        if "executive-signature" in classes or "became-law" in classes:
            return "signed", action_date
        if "executive-veto" in classes or "vetoed" in desc_low:
            return "vetoed", action_date
    return None, None


def _in_current_term(action_date: str | None, term_start: str) -> bool:
    if not action_date:
        return False
    try:
        ad = date.fromisoformat(action_date[:10])
        ts = date.fromisoformat(term_start[:10])
    except ValueError:
        return False
    return ad >= ts


def _historical_bills_for_state(state_code: str) -> list[dict[str, Any]]:
    try:
        return load_fixture("openstates", f"historical_bills_{state_code.lower()}")
    except FixtureMissing:
        return []


def detect_signal_e4(
    bills: list[dict[str, Any]],
    company_topics: dict[str, list[str]],
    topics: list[dict[str, Any]],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY,
    min_sign_rate: float = DEFAULT_SIGN_RATE,
) -> list[Signal]:
    governors = load_governors()
    signals: list[Signal] = []

    for bill in bills:
        bill_state_name = bill["jurisdiction"]["name"]
        state_code = state_name_to_code(bill_state_name)
        if not state_code:
            continue
        gov = governors.get(state_code)
        if not gov:
            continue

        bill_topics = classify_bill_to_topics(bill, topics)
        if not bill_topics:
            continue
        bill_topic_ids = {t["id"] for t in bill_topics}

        historical = _historical_bills_for_state(state_code)
        if not historical:
            continue

        # Restrict historical bag to bills with a sign/veto outcome during this term
        term_start = gov["term_start"]
        in_term_outcomes: list[tuple[dict, str]] = []  # (historical_bill, outcome)
        for hb in historical:
            outcome, action_date = _action_outcome(hb.get("actions") or [])
            if outcome and _in_current_term(action_date, term_start):
                in_term_outcomes.append((hb, outcome))

        if len(in_term_outcomes) < MIN_SAMPLE:
            continue

        # Filter to topic-similar bills via Voyage cosine
        candidate_text = bill_text_for_similarity(bill)
        historical_texts = [bill_text_for_similarity(hb) for hb, _ in in_term_outcomes]
        sims = similarity_to_corpus(candidate_text, historical_texts)
        similar = [(hb, outcome, sim)
                    for (hb, outcome), sim in zip(in_term_outcomes, sims)
                    if sim >= similarity_threshold]

        if len(similar) < MIN_SAMPLE:
            continue

        signed = sum(1 for _, outcome, _ in similar if outcome == "signed")
        vetoed = sum(1 for _, outcome, _ in similar if outcome == "vetoed")
        total = signed + vetoed
        if total == 0:
            continue
        sign_rate = signed / total
        if sign_rate < min_sign_rate:
            continue

        # ICP topic exposure
        matching_ciks = [
            cik for cik, t_ids in company_topics.items()
            if bill_topic_ids & set(t_ids)
        ]
        targets = matching_ciks if matching_ciks else [None]

        bill_stage_score = _stage_score(bill)

        for cik in targets:
            company = icp.cik_to_company(cik) if cik else None
            signals.append(Signal(
                signal_type="E4",
                company_cik=cik or "",
                company_name=company["name"] if company else "(no ICP match)",
                title=f"{bill_state_name} {bill['identifier']} likely to pass "
                       f"({gov['name']} signed {signed}/{total} similar bills)",
                why_now=_why_now(bill, gov, signed, total, sign_rate, bill_topics),
                evidence={
                    "bill": {
                        "id": bill["id"],
                        "identifier": bill["identifier"],
                        "title": bill["title"],
                        "jurisdiction": bill_state_name,
                        "first_action_date": bill.get("first_action_date"),
                        "latest_action_date": bill.get("latest_action_date"),
                        "openstates_url": bill.get("openstates_url"),
                    },
                    "governor": gov["name"],
                    "governor_party": gov["party"],
                    "term_start": gov["term_start"],
                    "topic": bill_topics[0]["id"],
                    "topic_label": bill_topics[0]["label"],
                    "sign_count": signed,
                    "veto_count": vetoed,
                    "total_acted_on": total,
                    "sign_rate": round(sign_rate, 2),
                    "similar_sample_size": len(similar),
                },
                score_inputs={
                    "sign_rate": float(sign_rate),
                    "sample_size": float(total),
                    "bill_stage": float(bill_stage_score),
                    "icp_topic_match": 1.0 if cik else 0.0,
                },
            ))
    logger.info("Signal E4: %d signals from %d candidate bills", len(signals), len(bills))
    return signals


def _stage_score(bill: dict[str, Any]) -> int:
    """Bigger number = closer to passage. 0=pre-introduction, 1=in committee,
    2=passed one chamber, 3=passed both chambers. Heuristic on latest action."""
    actions = bill.get("actions") or []
    if not actions:
        return 0
    classes = set()
    for a in actions:
        classes.update(a.get("classification") or [])
    if "passage" in classes or "executive-receipt" in classes:
        return 3
    if "first-reading" in classes and "committee-passage" in classes:
        return 2
    if "committee-referral" in classes or "committee-passage" in classes:
        return 1
    return 0


def _why_now(bill, gov, signed, total, rate, topics):
    pct = int(rate * 100)
    return (
        f"{bill['jurisdiction']['name']} introduced {bill['identifier']} "
        f"({bill['title'][:80]}). Governor {gov['name']} ({gov['party']}) has "
        f"signed {signed} of {total} similar {topics[0]['label']} bills since "
        f"taking office on {gov['term_start']} ({pct}% sign rate). "
        f"High-probability passage lead."
    )
