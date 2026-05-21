"""Signal D3 — Model legislation propagation velocity.

Detection:
1. Compare each candidate state bill against the model bill corpus by cosine
   similarity (TF-IDF over title + abstracts vs model bill summary).
2. If best match >= similarity_threshold AND that model bill has propagated to
   >= propagation_min prior states, surface a signal.
3. ICP topic match: prefer companies whose 10-K flagged the model bill's topic.
"""
from __future__ import annotations

import logging
from typing import Any

from signals.detectors import Signal
from signals.detectors._common import bill_text_for_similarity
from signals.enrich import icp
from signals.enrich.embeddings import similarity_to_corpus

logger = logging.getLogger(__name__)


def detect_signal_d3(
    bills: list[dict[str, Any]],
    model_bills: list[dict[str, Any]],
    company_topics: dict[str, list[str]],
    *,
    similarity_threshold: float = 0.20,
    propagation_min: int = 3,
) -> list[Signal]:
    if not bills or not model_bills:
        return []

    model_texts = [f"{m['title']} {m['summary']}" for m in model_bills]
    signals: list[Signal] = []

    for bill in bills:
        bill_text = bill_text_for_similarity(bill)
        if len(bill_text) < 30:
            continue
        sims = similarity_to_corpus(bill_text, model_texts)
        best_idx = max(range(len(sims)), key=sims.__getitem__)
        best_sim = sims[best_idx]
        if best_sim < similarity_threshold:
            continue

        model = model_bills[best_idx]
        prior_states = list(model.get("known_propagation", []))
        if len(prior_states) < propagation_min:
            continue

        bill_state = bill["jurisdiction"]["name"]
        model_topic = model["topic"]

        matching_ciks = [
            cik for cik, t_ids in company_topics.items()
            if model_topic in t_ids
        ]

        # Emit per ICP company. If no ICP match, emit once with no anchor company.
        targets = matching_ciks if matching_ciks else [None]
        for cik in targets:
            company = icp.cik_to_company(cik) if cik else None
            signals.append(Signal(
                signal_type="D3",
                company_cik=cik or "",
                company_name=company["name"] if company else "(no ICP match)",
                title=f"Model bill '{model['title']}' spreading; {bill_state} now {len(prior_states) + 1}th state",
                why_now=_why_now(company, bill, model, prior_states, best_sim),
                evidence={
                    "model_bill_id": model["id"],
                    "model_bill_title": model["title"],
                    "model_bill_source": model.get("source"),
                    "model_bill_topic": model_topic,
                    "matched_bill": {
                        "id": bill["id"],
                        "identifier": bill["identifier"],
                        "title": bill["title"],
                        "jurisdiction": bill_state,
                        "first_action_date": bill.get("first_action_date"),
                        "openstates_url": bill.get("openstates_url"),
                    },
                    "prior_states": prior_states,
                    "similarity": round(best_sim, 3),
                },
                score_inputs={
                    "propagation_count": float(len(prior_states)),
                    "acceleration": float(len(prior_states) / max(1.0, model.get("year_introduced", 2015) - 2010)),
                    "similarity": float(best_sim),
                    "icp_topic_match": 1.0 if cik else 0.0,
                },
            ))

    logger.info("Signal D3: %d signals across %d candidate bills", len(signals), len(bills))
    return signals


def _why_now(company, bill, model, prior_states, similarity):
    co_part = f" {company['name']}'s 10-K flagged {model['topic'].replace('_', ' ')} as a material risk." if company else ""
    return (
        f"{bill['jurisdiction']['name']} introduced {bill['identifier']} ({bill['title']}), "
        f"similar to the {model['source']} model bill '{model['title']}' "
        f"(cosine {similarity:.2f}). This model has already propagated to "
        f"{len(prior_states)} states: {', '.join(prior_states)}.{co_part}"
    )
