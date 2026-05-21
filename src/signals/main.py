"""Pipeline entrypoint: collect -> enrich -> detect -> score -> distribute.

End-to-end run in fixture mode requires zero API keys. Live mode iterates the
same code path against real APIs. Companies whose 10-K Item 1A topics aren't
cached as fixtures are skipped gracefully in fixture mode.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from signals.detectors import Signal, detect_signal_a, detect_signal_c, detect_signal_d3
from signals.distribute.slack import post_alert
from signals.enrich import extraction, icp
from signals.fixtures import FixtureMissing, load_fixture
from signals.logging_config import setup_logging
from signals.score.scoring import ScoreBreakdown, score_signal
from signals.settings import DATA_DIR, USE_LIVE_APIS, load_pipeline_config
from signals.sources import alec, edgar, lda as lda_src, openstates

logger = logging.getLogger(__name__)


def collect_bills(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not USE_LIVE_APIS:
        return load_fixture("openstates", "recent_bills_drug_pricing")
    days = cfg.get("lookback_days", {}).get("bills", 14)
    return openstates.get_recent_bills_for_topic(
        query='"prescription drug" OR "drug pricing" OR "pharmacy benefit manager" OR "drug affordability"',
        days=days,
    )


def collect_lda(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not USE_LIVE_APIS:
        return load_fixture("lda", "recent_registrations_pharma")
    days = cfg.get("lookback_days", {}).get("lda_registrations", 60)
    since = (date.today() - timedelta(days=days)).isoformat()
    return lda_src.get_recent_registrations(
        issue_codes=["HCR", "PHA", "MMM"],
        since=since,
    )


def collect_eight_ks(cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    days = cfg.get("lookback_days", {}).get("filings_8k", 14)
    result: dict[str, list[dict[str, Any]]] = {}
    for company in icp.load_companies():
        cik = company["cik"].lstrip("0") or "0"
        try:
            ek_list = edgar.get_recent_8ks(
                cik, item_codes=["7.01", "8.01", "1.05", "2.05"], days=days,
            )
        except FixtureMissing:
            continue
        if ek_list:
            result[cik] = ek_list
    return result


def extract_company_topics() -> dict[str, list[str]]:
    """For each ICP company, return list of topic ids flagged in its 10-K Item 1A."""
    result: dict[str, list[str]] = {}
    topics_cfg = icp.load_topics()
    for company in icp.load_companies():
        cik = company["cik"].lstrip("0") or "0"
        try:
            if USE_LIVE_APIS:
                rf = edgar.get_10k_risk_factors(cik)
                extracted = extraction.extract_risk_factor_topics(
                    rf["text"], topics_cfg, fixture_scenario=f"risk_factor_topics_{cik}",
                )
            else:
                extracted = load_fixture("anthropic", f"risk_factor_topics_{cik}")
        except FixtureMissing:
            logger.debug("no 10-K topic fixture for cik=%s; skipping", cik)
            continue
        except Exception as exc:
            logger.warning("10-K extraction failed for cik=%s: %s", cik, exc)
            continue
        topics_raw = extracted.get("topics", [])
        if not isinstance(topics_raw, list):
            logger.warning("cik=%s extraction returned non-list topics field (type=%s); skipping",
                           cik, type(topics_raw).__name__)
            result[cik] = []
            continue
        topic_ids: list[str] = []
        malformed = 0
        for t in topics_raw:
            if isinstance(t, dict) and isinstance(t.get("id"), str):
                topic_ids.append(t["id"])
            else:
                malformed += 1
        if malformed:
            logger.warning("cik=%s skipped %d malformed topic entries", cik, malformed)
        result[cik] = topic_ids
    logger.info("Company topic extraction complete: %d of %d ICP companies",
                len(result), len(icp.load_companies()))
    return result


def extract_8k_topics(eight_ks_by_cik: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for cik, eight_ks in eight_ks_by_cik.items():
        for ek in eight_ks:
            try:
                if USE_LIVE_APIS:
                    extracted = extraction.extract_8k_state_regulation(
                        ek["text"],
                        fixture_scenario=f"eight_k_state_regulation_{ek['accession']}",
                    )
                else:
                    extracted = load_fixture(
                        "anthropic", f"eight_k_state_regulation_{ek['accession']}",
                    )
            except FixtureMissing:
                continue
            except Exception as exc:
                logger.warning("8-K extraction failed for %s: %s", ek["accession"], exc)
                continue
            result[ek["accession"]] = extracted
    return result


def run_pipeline() -> dict[str, Any]:
    cfg = load_pipeline_config()
    topics = icp.load_topics()
    model_bills = alec.load_model_bills()

    # ---- Collect ----
    bills = collect_bills(cfg)
    logger.info("Collected %d bills from OpenStates", len(bills))
    lda_filings = collect_lda(cfg)
    logger.info("Collected %d LDA registrations", len(lda_filings))
    eight_ks_by_cik = collect_eight_ks(cfg)
    logger.info("Collected 8-Ks for %d companies", len(eight_ks_by_cik))

    # ---- Enrich ----
    company_topics = extract_company_topics()
    eight_k_extractions = extract_8k_topics(eight_ks_by_cik)

    # ---- Detect ----
    sim_cfg = cfg.get("similarity_thresholds", {})
    signals: list[Signal] = []
    signals.extend(detect_signal_a(
        bills, lda_filings, company_topics, topics,
        similarity_threshold=sim_cfg.get("bill_clustering", 0.25),
        lda_lookback_days=cfg.get("lookback_days", {}).get("lda_registrations", 60),
    ))
    signals.extend(detect_signal_c(eight_ks_by_cik, eight_k_extractions, bills, topics))
    signals.extend(detect_signal_d3(
        bills, model_bills, company_topics,
        similarity_threshold=sim_cfg.get("model_bill_match", 0.20),
    ))

    # ---- Score + route ----
    scoring_cfg = cfg.get("scoring", {})
    alert_threshold = scoring_cfg.get("alert_threshold", 70)
    watchlist_threshold = scoring_cfg.get("watchlist_threshold", 50)

    alerts: list[tuple[Signal, ScoreBreakdown]] = []
    watchlist: list[tuple[Signal, ScoreBreakdown]] = []
    for sig in signals:
        score = score_signal(sig, cfg)
        if score.total >= alert_threshold:
            alerts.append((sig, score))
        elif score.total >= watchlist_threshold:
            watchlist.append((sig, score))

    alerts.sort(key=lambda x: -x[1].total)
    watchlist.sort(key=lambda x: -x[1].total)

    # ---- Distribute ----
    for sig, score in alerts:
        post_alert(sig, score)

    if watchlist:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        wl_path = DATA_DIR / "watchlist.jsonl"
        with wl_path.open("a") as f:
            for sig, score in watchlist:
                f.write(json.dumps({
                    "timestamp": datetime.utcnow().isoformat(),
                    "signal": asdict(sig),
                    "score": asdict(score),
                }) + "\n")
        logger.info("Wrote %d watchlist entries to %s", len(watchlist), wl_path)

    summary = {
        "total_signals": len(signals),
        "alerts_fired": len(alerts),
        "watchlist": len(watchlist),
        "dropped": len(signals) - len(alerts) - len(watchlist),
        "last_run": datetime.utcnow().isoformat(),
    }
    logger.info("Pipeline complete: %s", summary)

    # Write last-run summary for FastAPI /health
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "last_run.json").write_text(json.dumps(summary, indent=2))

    return summary


def main() -> int:
    setup_logging()
    try:
        summary = run_pipeline()
    except Exception:
        logger.exception("Pipeline run failed")
        return 1
    print(f"\nSummary: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
