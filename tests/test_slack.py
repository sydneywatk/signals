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


# ---------- Account-level alerts ----------

from signals.distribute.slack import (  # noqa: E402
    aggregate_by_company, build_account_block_kit,
)


def _d3_signal(cik: str, model_id: str, score_total: int) -> tuple[Signal, ScoreBreakdown]:
    sig = Signal(
        signal_type="D3", company_cik=cik, company_name="Pfizer Inc",
        title=f"Model bill '{model_id}' spreading",
        why_now="why-now narrative for D3.",
        evidence={"model_bill_id": model_id,
                   "matched_bill": {"jurisdiction": "Texas", "identifier": "HB 100",
                                     "openstates_url": "https://openstates.org/tx/bills/HB100/"}},
        score_inputs={},
    )
    return sig, ScoreBreakdown(total=score_total, components={"propagation_count": 30, "similarity": 8}, confidence="high")


def test_composite_score_formula():
    # 3 signals: max=84, +5 per additional firing -> 84 + 10 = 94
    scored = [
        _d3_signal("78003", "alec_pbm", 84),
        _d3_signal("78003", "nashp_pdab", 80),
        _d3_signal("78003", "alec_freedom", 77),
    ]
    accounts = aggregate_by_company(scored)
    assert len(accounts) == 1
    assert accounts[0].composite_score == 94
    assert accounts[0].num_signals == 3
    # Sorted by score desc
    assert accounts[0].signals[0][1].total == 84


def test_composite_capped_at_100():
    # 5 signals: max=84, +5*4 = 104 -> capped at 100
    scored = [_d3_signal("78003", f"model_{i}", 84 - i) for i in range(5)]
    accounts = aggregate_by_company(scored)
    assert accounts[0].composite_score == 100


def test_account_block_kit_shows_n_signals_firing():
    scored = [
        _d3_signal("78003", "alec_pbm", 84),
        _d3_signal("78003", "nashp_pdab", 80),
    ]
    payload = build_account_block_kit(aggregate_by_company(scored)[0])
    import json
    json.dumps(payload)  # serializable
    text_blob = json.dumps(payload)
    assert "Pfizer Inc" in text_blob
    # New structure: Signals Firing field with value "2"
    assert "Signals Firing" in text_blob and '\\n2' in text_blob
    assert "Other firing signals" in text_blob
    assert "Score breakdown" in text_blob
    # Visual polish: divider blocks present
    assert any(b.get("type") == "divider" for b in payload["blocks"])
    # Fields block in second slot
    assert payload["blocks"][1]["type"] == "section"
    assert "fields" in payload["blocks"][1]
    # Suggested opener prominently placed
    opener_text = json.dumps(payload["blocks"][2])
    assert "Suggested opener" in opener_text


def test_separate_companies_separate_accounts():
    scored = [
        _d3_signal("78003", "alec_pbm", 84),
        _d3_signal("1369568", "nashp_pdab", 80),
    ]
    accounts = aggregate_by_company(scored)
    assert len(accounts) == 2
    # Higher composite ranks first
    assert accounts[0].company_cik == "78003"
