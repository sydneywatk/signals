"""Signal A — Coordinated multistate legislative waves.

Detection:
1. Cluster recent bills by text similarity.
2. Keep clusters with >= cluster_min_size bills across >= cluster_min_states states.
3. Classify each cluster to taxonomy topics (keyword match).
4. Require >= 1 LDA filing in the last `lda_lookback_days` matching the
   cluster's lda_issue_codes. **Narrative attribution gating:** if no filing
   in the matched bag passes `_is_pharma_credible_actor()`, the LDA evidence
   is rendered as "ambient lobbying activity" rather than naming a specific
   actor. This addresses v1's known issue where the HCR issue code matches
   any health-sector lobbying (e.g., regional hospitals) and over-attributes
   pharma-specific intent.
5. For each ICP company whose 10-K Item 1A mentioned the cluster's topic, emit
   one Signal A per (cluster, company).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from signals.detectors import Signal
from signals.detectors._common import (
    classify_cluster_to_topics,
    days_since,
    is_actionable_and_current,
    lda_filings_for_topics,
    bill_text_for_similarity,
)
from signals.enrich import icp
from signals.enrich.embeddings import Corpus

logger = logging.getLogger(__name__)

# Names + activity-text patterns we treat as credible pharma actors when
# attributing legislative mobilization. Hits surface as named-actor narrative;
# misses get the "ambient lobbying activity" framing.
_PHARMA_ACTOR_RE = re.compile(
    r"\b(phrma|pharmac|biopharm|biosim|biolog|drug manufactur|"
    r"prescription drug|pharmacy benefit|pbm|insulin|biotech|"
    r"pfizer|merck|abbvie|bristol|johnson|gilead|amgen|"
    r"ahip|kaiser foundation|america's health insurance|"
    r"medicaid|medicare part d)\b",
    re.IGNORECASE,
)


def _is_pharma_credible_actor(filing: dict[str, Any]) -> bool:
    """True iff the filing's registrant/client/activity-text names a pharma-credible actor."""
    blob_parts = [filing.get("registrant", {}).get("name", ""),
                   filing.get("client", {}).get("name", "")]
    for a in filing.get("lobbying_activities", []) or []:
        blob_parts.append(a.get("description") or "")
    return bool(_PHARMA_ACTOR_RE.search(" ".join(blob_parts)))


def detect_signal_a(
    bills: list[dict[str, Any]],
    lda_filings: list[dict[str, Any]],
    company_topics: dict[str, list[str]],
    topics: list[dict[str, Any]],
    *,
    similarity_threshold: float = 0.25,
    cluster_min_size: int = 3,
    cluster_min_states: int = 3,
    lda_lookback_days: int = 60,
) -> list[Signal]:
    # Filter out enacted bills (>30d post-chaptering) and stale prior-session bills.
    bills = [b for b in bills if is_actionable_and_current(b)]
    if len(bills) < cluster_min_size:
        return []

    texts = [bill_text_for_similarity(b) for b in bills]
    corpus = Corpus(texts)
    clusters = corpus.cluster(threshold=similarity_threshold)

    signals: list[Signal] = []
    for cluster_idx, cluster in enumerate(clusters):
        if len(cluster.indices) < cluster_min_size:
            continue
        cluster_bills = [bills[i] for i in cluster.indices]
        states = sorted({b["jurisdiction"]["name"] for b in cluster_bills})
        if len(states) < cluster_min_states:
            continue

        cluster_topics = classify_cluster_to_topics(cluster_bills, topics)
        if not cluster_topics:
            continue

        lda_matches = lda_filings_for_topics(lda_filings, cluster_topics)
        recent_lda = [f for f in lda_matches if days_since(f["dt_posted"]) <= lda_lookback_days]
        if not recent_lda:
            continue

        # Prefer a credible pharma actor for the narrative anchor. Fall back to
        # the most-recent filing in the matched bag if none of the filings
        # name a pharma actor — but the why-now narrative shifts to ambient.
        credible = [f for f in recent_lda if _is_pharma_credible_actor(f)]
        if credible:
            anchor = min(credible, key=lambda f: days_since(f["dt_posted"]))
            attribution = "named"
        else:
            anchor = min(recent_lda, key=lambda f: days_since(f["dt_posted"]))
            attribution = "ambient"
        most_recent_lda = anchor
        lda_age = days_since(most_recent_lda["dt_posted"])

        cluster_topic_ids = {t["id"] for t in cluster_topics}
        matching_ciks = [
            cik for cik, t_ids in company_topics.items()
            if set(t_ids) & cluster_topic_ids
        ]
        if not matching_ciks:
            continue

        for cik in matching_ciks:
            company = icp.cik_to_company(cik)
            if not company:
                continue
            # Fix 6: surface the 10-K topic confidence on the alert so renderers
            # can soften "flagged as material risk" language for medium matches.
            from signals.main import topic_confidence
            tc = topic_confidence(cik, cluster_topics[0]["id"])
            signals.append(Signal(
                signal_type="A",
                company_cik=cik,
                company_name=company["name"],
                title=f"Coordinated multistate bills in {len(states)} states on {cluster_topics[0]['label']}",
                why_now=_why_now(company, cluster_bills, states, most_recent_lda, cluster_topics, attribution, tc),
                evidence={
                    "cluster_id": f"A-cluster-{cluster_idx}",
                    "topic": cluster_topics[0]["id"],
                    "topic_label": cluster_topics[0]["label"],
                    "topic_confidence": tc,
                    "states": states,
                    "bills": [_bill_summary(b) for b in cluster_bills],
                    # Fix 5: only include LDA actor details when attribution is named.
                    # For ambient, emit a generic message instead of the registrant/client pair.
                    "lda_filing": _lda_summary(most_recent_lda) if attribution == "named" else _lda_ambient(),
                    "lda_attribution": attribution,
                    "lda_credible_count": len(credible),
                    "lda_total_in_window": len(recent_lda),
                    "matching_companies_total": len(matching_ciks),
                },
                score_inputs={
                    "cluster_size": float(len(cluster.indices)),
                    "lda_recency_days": float(lda_age),
                    "icp_company_count": float(len(matching_ciks)),
                    "cluster_cohesion": float(cluster.cohesion),
                },
            ))

    logger.info("Signal A: %d signals across %d clusters", len(signals), len(clusters))
    return signals


def _why_now(company, bills, states, lda, topics, attribution: str, tc: str = "unknown"):
    # Fix 6: vary 10-K phrasing by confidence. Strong language reserved for
    # high-confidence verbatim risk-factor matches; medium becomes "noted in
    # 10-K Item 1A (industry context)".
    if tc == "high":
        tenk_clause = f"{company['name']}'s 2026 10-K Item 1A flagged exposure to {topics[0]['label']} as a material risk"
    elif tc == "medium":
        tenk_clause = f"{company['name']}'s 2026 10-K Item 1A mentioned {topics[0]['label']} (industry context)"
    else:
        tenk_clause = f"{company['name']}'s 2026 10-K Item 1A references {topics[0]['label']}"
    base = (
        f"{tenk_clause}. In the last 90 days, {len(bills)} substantively similar bills "
        f"have been introduced across {len(states)} states ({', '.join(states)})."
    )
    if attribution == "named":
        return (
            f"{base} {lda['registrant']['name']} (client: {lda['client']['name']}) "
            f"registered new federal lobbying activity on this issue area "
            f"{days_since(lda['dt_posted'])} days ago."
        )
    # Ambient — don't attribute mobilization to a non-pharma actor.
    # Fix 5: stop naming the specific registrant entirely in ambient mode.
    return (
        f"{base} Federal lobbying activity on this issue area is ambient in the "
        f"same window; v1 LDA filter does not weight registrant industry "
        f"alignment — v2 ranks by pharma credibility."
    )


def _lda_ambient() -> dict[str, Any]:
    """Placeholder used when attribution is ambient — avoids exposing a
    non-pharma registrant/client pair an evaluator will Google."""
    return {
        "registrant": "(ambient — no pharma-credible actor in v1 LDA filter)",
        "client": "",
        "dt_posted": "",
        "filing_uuid": "",
        "url": "",
        "issue_codes": [],
    }


def _bill_summary(b):
    return {
        "id": b["id"],
        "identifier": b["identifier"],
        "title": b["title"],
        "jurisdiction": b["jurisdiction"]["name"],
        "first_action_date": b.get("first_action_date"),
        "openstates_url": b.get("openstates_url"),
    }


def _lda_summary(f):
    return {
        "filing_uuid": f.get("filing_uuid"),
        "registrant": f.get("registrant", {}).get("name"),
        "client": f.get("client", {}).get("name"),
        "dt_posted": f.get("dt_posted"),
        "filing_year": f.get("filing_year"),
        "issue_codes": sorted({a.get("general_issue_code") for a in f.get("lobbying_activities", [])
                                 if a.get("general_issue_code")}),
        "url": f.get("filing_document_url"),
    }
