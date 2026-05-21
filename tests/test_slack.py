"""Slack distributor produces well-formed Block Kit and stdout output."""
from __future__ import annotations

import json

from signals.detectors import Signal
from signals.distribute.slack import build_block_kit, render_alert_text
from signals.score.scoring import ScoreBreakdown


def _sample_signal_a() -> Signal:
    return Signal(
        signal_type="A",
        company_cik="78003",
        company_name="Pfizer Inc",
        title="Coordinated multistate bills",
        why_now="Pfizer flagged X. 5 bills in 3 states. LDA registration 1 day ago.",
        evidence={
            "cluster_id": "A-cluster-1",
            "topic": "drug_affordability_boards",
            "topic_label": "Drug Affordability Boards",
            "states": ["CO", "ME", "MD"],
            "bills": [{"identifier": "SB 140", "jurisdiction": "Colorado",
                        "openstates_url": "https://example.com/sb140"}],
            "lda_filing": {"registrant": "X", "client": "Y", "dt_posted": "2026-05-20", "url": "https://example.com/lda"},
        },
        score_inputs={"cluster_size": 5, "lda_recency_days": 1,
                       "icp_company_count": 1, "cluster_cohesion": 0.35},
    )


def test_block_kit_is_json_serializable():
    sig = _sample_signal_a()
    breakdown = ScoreBreakdown(total=77, components={"cluster_size": 30, "lda_recency": 19.7,
                                                       "icp_company_count": 10, "cluster_cohesion": 17.5},
                                confidence="medium")
    payload = build_block_kit(sig, breakdown)
    # Round-trip through JSON without errors
    s = json.dumps(payload)
    rt = json.loads(s)
    assert "blocks" in rt and isinstance(rt["blocks"], list)
    assert rt["blocks"][0]["type"] == "header"
    assert "Pfizer Inc" in rt["blocks"][0]["text"]["text"]


def test_render_alert_text_contains_key_facts():
    sig = _sample_signal_a()
    breakdown = ScoreBreakdown(total=77, components={"cluster_size": 30},
                                confidence="medium")
    text = render_alert_text(sig, breakdown)
    assert "Pfizer Inc" in text
    assert "Score 77" in text
    assert "Why now" in text
    assert "Drug Affordability Boards" in text
    assert "CO" in text and "ME" in text and "MD" in text


def test_action_buttons_have_urls():
    sig = _sample_signal_a()
    breakdown = ScoreBreakdown(total=77, components={}, confidence="medium")
    payload = build_block_kit(sig, breakdown)
    action_blocks = [b for b in payload["blocks"] if b["type"] == "actions"]
    assert action_blocks
    for btn in action_blocks[0]["elements"]:
        assert btn["type"] == "button"
        assert btn["url"].startswith("https://")
