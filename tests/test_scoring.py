"""Scoring math: clamping, weighting, confidence banding."""
from __future__ import annotations

from signals.detectors import Signal
from signals.score.scoring import score_signal
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
