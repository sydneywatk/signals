"""Weighted scoring with transparent component breakdown.

Each scorer normalizes signal-specific inputs to a 0-100 scale, applies the
weight from settings.yml, and surfaces both the total and the per-component
contribution. The Slack alert prints the breakdown so reps can see why a
signal scored what it did.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from signals.detectors import Signal


Confidence = Literal["high", "medium", "low"]


@dataclass
class ScoreBreakdown:
    total: int
    components: dict[str, float] = field(default_factory=dict)
    confidence: Confidence = "medium"


def _clamp_0_100(x: float) -> float:
    return max(0.0, min(100.0, x))


# (signal_type, weight_name) -> function(score_inputs) -> normalized 0-100
_NORMALIZERS: dict[tuple[str, str], Callable[[dict[str, float]], float]] = {
    # Signal A
    # cluster gated at >=3 bills already, so 3->60, 5->100 cap is the working range
    ("A", "cluster_size"):
        lambda i: _clamp_0_100(i.get("cluster_size", 0) / 5.0 * 100),
    ("A", "lda_recency"):
        lambda i: _clamp_0_100(100 - i.get("lda_recency_days", 90) * (100/60)),  # 0d->100, 60d->0
    # ICP count is naturally small in fixture mode (only Pfizer's 10-K extracted); 1->50, 3->100
    ("A", "icp_company_count"):
        lambda i: _clamp_0_100(i.get("icp_company_count", 0) / 3.0 * 100),
    ("A", "cluster_cohesion"):
        # TF-IDF cohesion caps well below 1.0 for cross-state bills (~0.35 is strong); x250
        lambda i: _clamp_0_100(i.get("cluster_cohesion", 0) * 250),

    # Signal C
    ("C", "filing_recency"):
        lambda i: _clamp_0_100(100 - i.get("filing_recency_days", 14) * (100/14)),  # 0d->100, 14d->0
    ("C", "bill_count"):
        lambda i: _clamp_0_100(i.get("bill_count", 0) / 5.0 * 100),
    ("C", "bill_stage"):
        lambda i: _clamp_0_100(i.get("bill_stage", 0) * 33),
    ("C", "match_specificity"):
        lambda i: _clamp_0_100(i.get("match_specificity", 0) * 33),

    # Signal D3
    # 3 states is gate; 8 states is strong; cap at 10
    ("D3", "propagation_count"):
        lambda i: _clamp_0_100(i.get("propagation_count", 0) / 10.0 * 100),
    ("D3", "acceleration"):
        lambda i: _clamp_0_100(i.get("acceleration", 0) * 100),
    ("D3", "similarity"):
        # TF-IDF similarity for model bills tops out around 0.4-0.5; x250 to compress to 0-100
        lambda i: _clamp_0_100(i.get("similarity", 0) * 250),
    ("D3", "icp_topic_match"):
        lambda i: 100.0 if i.get("icp_topic_match", 0) else 0.0,
}


def score_signal(signal: Signal, pipeline_cfg: dict[str, Any]) -> ScoreBreakdown:
    """Compute a 0-100 score for a Signal using the configured weights."""
    weights = pipeline_cfg.get("weights", {}).get(_signal_weight_key(signal.signal_type), {})
    if not weights:
        return ScoreBreakdown(total=0, components={}, confidence="low")

    contributions: dict[str, float] = {}
    for weight_name, weight_value in weights.items():
        normalizer = _NORMALIZERS.get((signal.signal_type, weight_name))
        if normalizer is None:
            continue
        component_raw = normalizer(signal.score_inputs)
        contributions[weight_name] = round(component_raw * float(weight_value), 1)

    total = round(sum(contributions.values()))
    return ScoreBreakdown(
        total=total,
        components=contributions,
        confidence=_confidence_band(total),
    )


def _signal_weight_key(signal_type: str) -> str:
    return {"A": "signal_a", "C": "signal_c", "D3": "signal_d"}.get(signal_type, "")


def _confidence_band(total: int) -> Confidence:
    if total >= 80:
        return "high"
    if total >= 50:
        return "medium"
    return "low"


def _group_key(signal: Signal) -> tuple:
    """Within-run dedup group: same key = same buying moment, surface once."""
    if signal.signal_type == "A":
        return (signal.company_cik, "A", signal.evidence.get("topic"))
    if signal.signal_type == "C":
        return (signal.company_cik, "C",
                signal.evidence.get("filing", {}).get("accession"))
    if signal.signal_type == "D3":
        return (signal.company_cik, "D3", signal.evidence.get("model_bill_id"))
    return (signal.company_cik, signal.signal_type, None)


def dedup_within_run(scored: list[tuple[Signal, ScoreBreakdown]]) -> list[tuple[Signal, ScoreBreakdown]]:
    """Group by (company, signal_type, anchor). Keep top score per group; attach
    a list of suppressed bill identifiers to the winner's evidence so the Slack
    renderer can show '+N related bills'."""
    groups: dict[tuple, list[tuple[Signal, ScoreBreakdown]]] = {}
    for pair in scored:
        groups.setdefault(_group_key(pair[0]), []).append(pair)

    out: list[tuple[Signal, ScoreBreakdown]] = []
    for items in groups.values():
        items.sort(key=lambda x: -x[1].total)
        winner_sig, winner_score = items[0]
        suppressed = items[1:]
        if suppressed:
            related = []
            for sig, _ in suppressed:
                anchor = _bill_anchor(sig)
                if anchor:
                    related.append(anchor)
            if related:
                # Attach to a shallow copy of evidence so we don't mutate the original
                winner_sig.evidence["related_suppressed"] = related
        out.append((winner_sig, winner_score))
    return out


def _bill_anchor(signal: Signal) -> str | None:
    """Compact identifier for a suppressed signal — used in the '+N related' footer."""
    if signal.signal_type == "D3":
        mb = signal.evidence.get("matched_bill", {})
        if mb.get("jurisdiction") and mb.get("identifier"):
            return f"{mb['jurisdiction']} {mb['identifier']}"
    if signal.signal_type == "A":
        # A groups by (company, topic); cluster id distinguishes distinct waves
        return signal.evidence.get("cluster_id")
    if signal.signal_type == "C":
        return signal.evidence.get("filing", {}).get("accession")
    return None
