# Signals — Technical Reference

For engineers. Per signal: sources, detection algorithm, scoring weights,
known limits and false-positive risks. Reference design in
[`SPEC.md`](SPEC.md); architecture in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Common substrate

- **Embeddings:** Voyage AI `voyage-3` (1024-dim) via `signals.enrich.embeddings.Embedder`. Disk cache at `tests/fixtures/voyage/embeddings_cache.json`; cold cache fills on first live run, persists thereafter.
- **Topic classification:** `signals.detectors._common.classify_bill_to_topics` matches `risk_factor_keywords` + `openstates_search_terms` from `config/topics.yml` against bill title + abstracts (case-insensitive substring).
- **ICP enrichment:** `enrich.icp.cik_to_company`, `state_to_companies`, `topic_by_id`. 10-K topic extraction via Claude tool-use (`enrich.extraction.extract_risk_factor_topics`).

## Signal A — Coordinated multistate legislative wave

**Sources:** OpenStates `/bills` (search), LDA `/filings/?filing_type=RR`, EDGAR 10-K Item 1A.

**Algorithm:**
1. Pull recent bills (14d window) via OpenStates per-state query.
2. Voyage-embed `title + abstracts` for each bill; build `Corpus`.
3. Single-link cluster at `similarity_thresholds.bill_clustering` (0.70).
4. Keep clusters with `>=3` bills across `>=3` jurisdictions.
5. Classify cluster to topics via keyword match.
6. Require `>=1` LDA filing in last 60d whose `lobbying_activities[].general_issue_code` overlaps the topic's `lda_issue_codes`.
7. Require `>=1` ICP company whose 10-K Item 1A flagged a matching topic.
8. Emit one signal per (cluster, matching company).

**Scoring weights** (`signal_a` in `config/settings.yml`):
- `cluster_size: 0.30` (3 bills → 60, 5+ → 100)
- `lda_recency: 0.20` (0d → 100, 60d → 0)
- `icp_company_count: 0.30` (1 → 33, 3+ → 100)
- `cluster_cohesion: 0.20` (Voyage mean pairwise; 0.70 → 70, 1.0 → 100)

**Known limits:**
- LDA filter is too broad: `HCR` matches any healthcare lobbying, not specifically the cluster's topic. The Ephraim McDowell Health filing on "healthcare issues" matched our PDAB cluster because both touch HCR. v2: grep `filing_specific_lobbying_issues` text for topic-specific terms.
- `icp_company_count` capped at 1 in v1 because only Pfizer's 10-K has cached topic extraction. Pre-computing all 13 ICPs is the highest-ROI scaling step.
- TF-IDF baseline missed paraphrased cross-state bills; Voyage catches them.

## Signal C — SEC 8-K material event + active state bill

**Sources:** EDGAR `submissions.json` (8-K item codes pre-parsed as comma-joined string), EDGAR 8-K HTML body, Claude extraction, OpenStates active bills.

**Algorithm:**
1. For each ICP company, fetch `submissions.json`; filter 8-Ks in last 14d to those with `recent.items` containing 7.01/8.01/1.05/2.05.
2. For each matching 8-K, fetch the body via `edgartools.Filing.text()`.
3. Claude tool-use extracts `{mentions_state_regulation, states[], topics[], supporting_text}`.
4. If `mentions_state_regulation=true`, query OpenStates for active bills in those states matching those topics.
5. Emit one signal per (company, 8-K) when at least one matching active bill exists.

**Scoring weights:**
- `filing_recency: 0.30` (0d → 100, 14d → 0)
- `bill_count: 0.20` (1 → 20, 5+ → 100)
- `bill_stage: 0.30` (`latest_action_date <= 30d` → 33 per bill)
- `match_specificity: 0.20` (1 state → 99, 2-3 → 66, 4+ → 33)

**Known limits:**
- **Empirically broken on real data.** Scanned 195 most-recent 8-Ks across the 13 ICP companies for state regulatory keywords: zero hits. State regulatory discussion lives in 10-Q MD&A and 8-K Exhibit 99 attached press releases. v1 ships with one synthetic 8-K fixture flagged `is_synthetic_demo: true` to demonstrate the detector wiring. v2 must read 8-K Exhibit 99 text.

## Signal D3 — Model legislation propagation velocity

**Sources:** OpenStates bills, `config/model_bills.yml` (12 hand-curated ALEC + NASHP models with `known_propagation` lists). v2 scrapes ALEC/NASHP corpora live.

**Algorithm:**
1. For each candidate bill, Voyage cosine vs each model bill summary.
2. If best match `>= similarity_thresholds.model_bill_match` (0.65), pull the matched model.
3. If `len(model.known_propagation) >= 3`, emit a signal.
4. ICP topic match boosts: if any ICP company's 10-K topics include the model's topic, anchor the signal to that company.

**Scoring weights:**
- `propagation_count: 0.40` (3 → 30, 10+ → 100)
- `acceleration: 0.30` (proxy: states / (year_introduced − 2010))
- `similarity: 0.15` (Voyage cosine; 0.65 → 65, 1.0 → 100)
- `icp_topic_match: 0.15` (binary: 1 if matched ICP company exists, else 0)

**Known limits:**
- Static corpus. The "ALEC-aligned PBM Oversight Model" is a hand-curated representation of the spirit of ALEC's PBM model legislation, not a direct quote of the canonical bill (ALEC's public archive has 54 pages of model policies and doesn't surface a canonically-named PBM model in search). DECISION_MEMO documents this.
- Propagation lists were partially fact-checked against public records during build (NASHP PDAB was corrected from NY→NJ + added OH). Periodic re-curation needed.

## Signal E4 — Governor signing track record predictor

**Sources:** OpenStates bills + bill actions (`include=actions`), `config/governors.yml` (hand-curated current governor + term-start date per state).

**Algorithm:**
1. Classify candidate bill to topic(s) via keyword match.
2. Look up state's current governor + term-start.
3. Pull historical state bills with action data; filter to those whose `actions[]` contains `executive-signature` (signed) or `executive-veto` (vetoed) with action date `>= term_start`.
4. Voyage cosine candidate vs each historical bill; keep those `>=` similarity threshold (0.70).
5. Compute `sign_rate = signed / (signed + vetoed)` over the topic-similar bag.
6. Require `>= 3` historical samples and `sign_rate >= 0.70`. Emit per ICP company with topic match.

**Scoring weights** (`signal_e`):
- `sign_rate: 0.45` (0.70 → 70, 1.0 → 100)
- `sample_size: 0.15` (3 → 30, 10+ → 100)
- `bill_stage: 0.20` (0 = intro → 0, 3 = passed both chambers → 100)
- `icp_topic_match: 0.20` (binary)

**Known limits:**
- Requires historical bag of acted-on bills per state (`tests/fixtures/openstates/historical_bills_<state>.json`). State coverage in v1 limited to ~4 states. Cold-capture each state hits OpenStates rate limits — practical max 8–10 states.
- `governors.yml` is hand-curated; v2 source = scrape National Governors Association.
- Sign rate is unweighted by topic specificity. Two PBM bills from the same governor count equally even if one is a major reform and one is a definitional update.
- Bill `latest_action_date` and OpenStates action classification can lag the actual gubernatorial action by a few days.

## Cross-cutting limits

- **Within-run dedup only** (`score.scoring.dedup_within_run`). Same alert can fire on consecutive cron runs until the underlying evidence ages out of its lookback window. v2: cross-run dedup keyed `(signal_type, company_cik, evidence_anchor)` with 7d TTL.
- **No rep feedback loop.** v2: 👍/👎 reactions in Slack route into a feedback file that re-tunes weights.
- **20-F filers excluded.** v1 scopes to 10-K filers (US-domiciled). AstraZeneca, Novartis, GSK, Sanofi, Novo Nordisk drop out. v2: add a second 10-K-equivalent extractor for Item 3.D of 20-F.
