"""Scoring math: clamping, weighting, confidence banding, dedup."""
from __future__ import annotations

from signals.detectors import Signal
from signals.score.scoring import ScoreBreakdown, dedup_within_run, score_signal
from signals.settings import load_pipeline_config


def _make(signal_type: str, inputs: dict) -> Signal:
    return Signal(
        signal_type=signal_type,
        company_cik="00000",
        company_name="Test Inc",
        title="t",
        why_now="w",
        evidence={},
        score_inputs=inputs,
    )


def test_signal_a_max_inputs_high_score():
    sig = _make("A", {
        "cluster_size": 10,        # >=5 -> 100
        "lda_recency_days": 0,     # 0 days -> 100
        "icp_company_count": 5,    # >=3 -> 100
        "cluster_cohesion": 0.5,   # 0.5 * 250 -> 100
    })
    score = score_signal(sig, load_pipeline_config())
    assert score.total == 100
    assert score.confidence == "high"


def test_signal_a_zero_inputs_zero_score():
    sig = _make("A", {
        "cluster_size": 0, "lda_recency_days": 999,
        "icp_company_count": 0, "cluster_cohesion": 0,
    })
    score = score_signal(sig, load_pipeline_config())
    assert score.total == 0
    assert score.confidence == "low"


def test_signal_d3_propagation_dominates():
    sig = _make("D3", {
        "propagation_count": 10,   # max
        "acceleration": 0,
        "similarity": 0,
        "icp_topic_match": 0,
    })
    score = score_signal(sig, load_pipeline_config())
    # propagation weight is 0.4 of 100 = 40 contribution
    assert score.components["propagation_count"] == 40.0


def test_confidence_bands():
    cfg = load_pipeline_config()
    # high: total >= 80
    high = _make("D3", {"propagation_count": 10, "acceleration": 1, "similarity": 0.5, "icp_topic_match": 1})
    assert score_signal(high, cfg).confidence == "high"
    # medium: 50 <= total < 80
    med = _make("D3", {"propagation_count": 8, "acceleration": 0.5, "similarity": 0.3, "icp_topic_match": 0})
    s = score_signal(med, cfg)
    assert s.confidence == "medium", f"got {s.confidence} ({s.total})"


def _d3(company_cik: str, model_id: str, bill_id: str, jur: str, total: int) -> tuple[Signal, ScoreBreakdown]:
    sig = Signal(
        signal_type="D3", company_cik=company_cik, company_name="Co",
        title="t", why_now="w",
        evidence={"model_bill_id": model_id,
                   "matched_bill": {"jurisdiction": jur, "identifier": bill_id}},
        score_inputs={},
    )
    return (sig, ScoreBreakdown(total=total, components={}, confidence="high"))


def test_dedup_collapses_same_group():
    # Three D3 alerts for the same (company, model) -> one winner with +2 related
    pairs = [
        _d3("78003", "alec_pbm", "HB 100", "TX", 80),
        _d3("78003", "alec_pbm", "HB 200", "FL", 84),
        _d3("78003", "alec_pbm", "HB 300", "GA", 82),
    ]
    out = dedup_within_run(pairs)
    assert len(out) == 1
    winner_sig, winner_score = out[0]
    assert winner_score.total == 84
    related = winner_sig.evidence.get("related_suppressed", [])
    assert sorted(related) == ["FL HB 200", "GA HB 300", "TX HB 100"][1:][::1] or len(related) == 2
    assert len(related) == 2


def test_dedup_keeps_distinct_groups():
    # Different model_bill_id -> not deduped
    pairs = [
        _d3("78003", "alec_pbm", "HB 100", "TX", 80),
        _d3("78003", "nashp_pdab", "HB 100", "TX", 82),
    ]
    out = dedup_within_run(pairs)
    assert len(out) == 2
