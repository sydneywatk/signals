"""Signal A — Coordinated multistate legislative waves.

Detection:
1. Cluster recent bills by text similarity.
2. Keep clusters with >= cluster_min_size bills across >= cluster_min_states states.
3. Classify each cluster to taxonomy topics (keyword match).
4. Require >= 1 LDA registration in the last `lda_lookback_days` matching the
   cluster's lda_issue_codes (the actor signal).
5. For each ICP company whose 10-K Item 1A mentioned the cluster's topic, emit
   one Signal A per (cluster, company).
"""
from __future__ import annotations

import logging
from typing import Any

from signals.detectors import Signal
from signals.detectors._common import (
    classify_cluster_to_topics,
    days_since,
    lda_filings_for_topics,
    bill_text_for_similarity,
)
from signals.enrich import icp
from signals.enrich.embeddings import TfidfCorpus

logger = logging.getLogger(__name__)


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
    if len(bills) < cluster_min_size:
        return []

    texts = [bill_text_for_similarity(b) for b in bills]
    corpus = TfidfCorpus(texts)
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

        most_recent_lda = min(recent_lda, key=lambda f: days_since(f["dt_posted"]))
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
            signals.append(Signal(
                signal_type="A",
                company_cik=cik,
                company_name=company["name"],
                title=f"Coordinated multistate bills in {len(states)} states on {cluster_topics[0]['label']}",
                why_now=_why_now(company, cluster_bills, states, most_recent_lda, cluster_topics),
                evidence={
                    "cluster_id": f"A-cluster-{cluster_idx}",
                    "topic": cluster_topics[0]["id"],
                    "topic_label": cluster_topics[0]["label"],
                    "states": states,
                    "bills": [_bill_summary(b) for b in cluster_bills],
                    "lda_filing": _lda_summary(most_recent_lda),
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


def _why_now(company, bills, states, lda, topics):
    return (
        f"{company['name']}'s 2026 10-K Item 1A flagged exposure to {topics[0]['label']}. "
        f"In the last 90 days, {len(bills)} substantively similar bills have been introduced "
        f"across {len(states)} states ({', '.join(states)}). "
        f"{lda['registrant']['name']} (client: {lda['client']['name']}) registered new federal "
        f"lobbying activity on the same issue area {days_since(lda['dt_posted'])} days ago."
    )


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
