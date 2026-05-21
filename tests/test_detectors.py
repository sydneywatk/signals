"""Detectors fire positives on known-good fixtures and reject negatives."""
from __future__ import annotations

from signals.detectors import detect_signal_a, detect_signal_c, detect_signal_d3
from signals.enrich import icp
from signals.fixtures import load_fixture
from signals.sources import alec


# ---------- Signal A ----------

def test_signal_a_positive_case():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    lda_filings = load_fixture("lda", "recent_registrations_pharma")
    rf = load_fixture("anthropic", "risk_factor_topics_78003")
    company_topics = {"78003": [t["id"] for t in rf["topics"]]}
    sigs = detect_signal_a(bills, lda_filings, company_topics, icp.load_topics())
    assert sigs, "expected at least one Signal A from canonical fixtures"
    s = sigs[0]
    assert s.signal_type == "A"
    assert s.company_cik == "78003"
    assert s.score_inputs["cluster_size"] >= 3
    # Topic varies based on which cluster scores highest under current embeddings;
    # any pharma regulatory topic is fine, just confirm it's in our taxonomy
    assert s.evidence["topic"] in {t["id"] for t in icp.load_topics()}


def test_signal_a_no_lda_no_signal():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    rf = load_fixture("anthropic", "risk_factor_topics_78003")
    company_topics = {"78003": [t["id"] for t in rf["topics"]]}
    # Empty LDA list — gating fails
    sigs = detect_signal_a(bills, [], company_topics, icp.load_topics())
    assert sigs == []


# ---------- Signal C ----------

def test_signal_c_positive_case_synthetic():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    eks = load_fixture("edgar", "recent_8ks_78003")
    extractions = {ek["accession"]: load_fixture("anthropic", f"eight_k_state_regulation_{ek['accession']}")
                   for ek in eks}
    sigs = detect_signal_c({"78003": eks}, extractions, bills, icp.load_topics())
    assert sigs, "expected Signal C from synthetic 8-K demo fixture"
    s = sigs[0]
    assert s.signal_type == "C"
    assert s.company_cik == "78003"
    assert s.evidence["filing"]["is_synthetic_demo"] is True
    assert s.evidence["states"]


def test_signal_c_no_extraction_no_signal():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    eks = load_fixture("edgar", "recent_8ks_78003")
    # No extractions provided — signal cannot fire
    sigs = detect_signal_c({"78003": eks}, {}, bills, icp.load_topics())
    assert sigs == []


# ---------- Signal D3 ----------

def test_signal_d3_positive_case():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    rf = load_fixture("anthropic", "risk_factor_topics_78003")
    company_topics = {"78003": [t["id"] for t in rf["topics"]]}
    sigs = detect_signal_d3(bills, alec.load_model_bills(), company_topics)
    assert sigs, "expected D3 signals from canonical fixtures"
    types = {s.signal_type for s in sigs}
    assert types == {"D3"}
    assert all(s.score_inputs["propagation_count"] >= 3 for s in sigs)


def test_signal_d3_threshold_filters():
    bills = load_fixture("openstates", "recent_bills_drug_pricing")
    rf = load_fixture("anthropic", "risk_factor_topics_78003")
    company_topics = {"78003": [t["id"] for t in rf["topics"]]}
    # Threshold near 1.0 means nothing matches
    sigs = detect_signal_d3(bills, alec.load_model_bills(), company_topics,
                              similarity_threshold=0.99)
    assert sigs == []
