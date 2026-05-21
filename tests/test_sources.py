"""Source clients return well-shaped data in fixture mode."""
from __future__ import annotations

from signals.sources import alec, edgar, lda, openstates


def test_openstates_search_bills_shape():
    bills = openstates.search_bills(query="any", updated_since="ignored")
    assert isinstance(bills, list) and len(bills) > 0
    sample = bills[0]
    for key in ("id", "identifier", "title", "jurisdiction"):
        assert key in sample, f"missing {key}"


def test_openstates_bill_detail_shape():
    detail = openstates.get_bill_detail("any_id")
    assert isinstance(detail, dict)
    for key in ("id", "title", "jurisdiction"):
        assert key in detail


def test_lda_recent_registrations_shape():
    filings = lda.get_recent_registrations()
    assert isinstance(filings, list) and len(filings) > 0
    sample = filings[0]
    assert "registrant" in sample and "client" in sample
    assert "dt_posted" in sample
    # Issue codes filter ran client-side at capture; every filing has >=1 matching activity
    assert sample.get("lobbying_activities"), "expected lobbying_activities"


def test_edgar_risk_factors_shape():
    rf = edgar.get_10k_risk_factors("78003")
    assert set(rf.keys()) >= {"cik", "accession", "filing_date", "text"}
    assert len(rf["text"]) > 1000  # 10-K Item 1A is substantial


def test_edgar_recent_8ks_shape():
    eks = edgar.get_recent_8ks("78003")
    assert isinstance(eks, list) and len(eks) > 0
    sample = eks[0]
    for key in ("cik", "accession", "filing_date", "items", "text", "url"):
        assert key in sample


def test_alec_model_bills_loaded():
    bills = alec.load_model_bills()
    assert len(bills) >= 10
    sample = bills[0]
    for key in ("id", "title", "source", "topic", "known_propagation", "summary"):
        assert key in sample
