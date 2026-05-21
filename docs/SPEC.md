# GTM Signal Pipeline — Build Spec

**Status:** Canonical spec for this repo.
**Author:** Sydney Watkins
**Last updated:** 2026-05-21
**Reference materials:** `docs/source-research/01-openstates.md`, `02-lda.md`, `03-sec-edgar.md`, `04-gdelt.md`

---

## 0. Read before starting

Take-home exercise for the GTM Automation Engineer role at State Affairs. Ships in 2–3 days. Quality and clarity beat completeness.

**Before any code:**

1. Verify all credentials are present in `.env`. If anything required is missing, stop and ask — do not assume defaults, do not invent values, do not skip a source because a key is missing.
2. Read the source-research files. They are more recent and more specific than training data.
3. Read this entire spec before starting work.
4. Create the public GitHub repo `signals` on Sydney's account and push an initial commit before building. Commit at the end of each section.

**Working style:** test as you build; flag deviations from the research before adapting; pick simple over elegant; commit frequently; ask when in doubt.

---

## 1. Project context

State Affairs sells state-level legislative intelligence to corporate Government Affairs teams, trade associations, and lobbying firms. The pipeline here is a **GTM signal engine for State Affairs' own sales team** — not a product feature for their customers. It detects buying moments and routes prioritized leads to sales via Slack.

Rubric weights: signal value 35%, working pipeline 25%, code quality 15%, distribution 15%, communication 10%.

---

## 2. Target ICP

US-domiciled mid-market pharmaceutical manufacturers ($500M–$5B revenue, SIC 2834). State GA teams of 3–8 people tracking legislation in 50 jurisdictions. v1 scopes to 10-K filers (US-domiciled) only; foreign pharma (20-F filers — AstraZeneca, Novartis, GSK, Sanofi, Novo Nordisk) is v2 scope.

Target list: hand-curated ~30 companies in `config/companies.yml`.

---

## 3. The three signals

All three must satisfy: multi-source, non-obvious, load-bearing enrichment.

### Signal A — Coordinated multistate legislative waves

3+ near-identical bills introduced across 3+ states in a 14-day window, where:
(a) a new LDA registration on the bill's issue area appeared in the prior 60 days, AND
(b) one or more ICP companies' most recent 10-K Item 1A flags exposure to the same topic.

**Sources:** OpenStates (bill text), LDA (registrations), SEC EDGAR (10-K), Anthropic (embeddings).

**Detection:**
1. Fetch bills introduced in last 14 days, filter to pharma topic taxonomy.
2. Embed bill text with Claude.
3. Cluster on cosine similarity > 0.85.
4. For each cluster with 3+ bills in 3+ unique states: query LDA for new registrations on matching issue codes; cross-reference ICP companies whose 10-K mentions the topic.

**Scoring inputs:** cluster size, LDA recency, ICP company count, cluster cohesion.

### Signal C — SEC 8-K material event + active state bill match

ICP company files an 8-K with item codes 8.01, 7.01, or 1.05 in the last 14 days; the 8-K mentions a specific state regulatory topic; a bill on that topic is currently active in the named state.

**Sources:** SEC EDGAR (8-K + pre-parsed item codes), OpenStates (active bills), Claude (topic extraction).

**Detection:**
1. For each ICP company, fetch `submissions.json`; identify 8-Ks in last 14 days with `recent.items` containing "8.01", "7.01", or "1.05".
2. Fetch primary HTML for matching 8-Ks only.
3. Claude extracts `{ mentions_state_regulation: bool, states: [...], topics: [...] }`.
4. If true, query OpenStates for active bills in those states on those topics.

**Scoring inputs:** filing recency, bill count, bill stage, match specificity.

### Signal D3 — Model legislation propagation velocity

A bill in a target state whose text is substantively similar to a known model bill from ALEC (or NCSL/Brennan Center), where that model bill has appeared in 3+ states in the prior 12 months.

**Sources:** OpenStates, scraped/static ALEC model bill corpus, Claude embeddings.

**Detection:**
1. Maintain model-bill corpus with embeddings, weekly refresh.
2. For each new state bill in topic taxonomy: embed, check cosine similarity vs corpus.
3. If similarity > 0.80 to any model bill, count prior states where similar bills appeared in last 12 months.
4. If ≥ 3 prior states, emit signal.

**Scoring inputs:** propagation count, acceleration, similarity, ICP topic overlap.

---

## 4. Architecture

### 4.1 Repository structure

```
signals/
├── README.md
├── .env.example
├── pyproject.toml
├── .github/workflows/{ci.yml,pipeline.yml}
├── config/{companies.yml,topics.yml,settings.yml}
├── docs/{SPEC.md,ARCHITECTURE.md,DECISION_MEMO.md,source-research/}
├── scripts/{build_target_list.py,build_model_corpus.py,capture_fixtures.py,trigger_run.py}
├── src/signals/
│   ├── main.py, settings.py, logging_config.py
│   ├── sources/{openstates.py,lda.py,edgar.py,alec.py}
│   ├── enrich/{embeddings.py,extraction.py,icp.py,prompts/}
│   ├── detectors/{signal_a.py,signal_c.py,signal_d.py}
│   ├── score/scoring.py
│   ├── distribute/{slack.py,templates/}
│   └── http/{client.py,rate_limit.py}
├── tests/{conftest.py,fixtures/,test_*.py}
└── app/server.py
```

### 4.2 Data flow

GitHub Actions cron (every 6h) → `main.py` → sources (parallel fetch) → enrich (embed, extract, ICP resolve) → detect (A, C, D3) → score (with breakdown) → filter (threshold) → distribute (Slack or stdout dry-run).

### 4.3 Deployment

- **Scheduled:** GitHub Actions cron every 6h, runs `python -m signals.main` with secrets from repo settings.
- **Live URL:** FastAPI service on Modal (free tier). `GET /health` returns last-run status; `POST /trigger` requires `X-Trigger-Secret` header to start an out-of-cycle run.

---

## 5. Configuration files

### 5.1 `config/companies.yml`

```yaml
companies:
  - cik: "0000078003"
    name: "Pfizer Inc"
    ticker: "PFE"
    states_of_operation: ["NY", "MI", ...]   # from 10-K Properties
    lda_registrant_aliases: ["Pfizer Inc.", "Pfizer Inc"]
    notes: ""
  # ~30 entries total
```

### 5.2 `config/topics.yml`

Pharmaceutical regulatory taxonomy. Each topic has `id`, `label`, `description`, `risk_factor_keywords`, `openstates_search_terms`, `lda_issue_codes`, `state_specific_statutes`. Initial set: drug price transparency, PBM regulation, Prop 65, drug take-back, drug affordability boards, 340B disputes. Phrase seed list in `docs/source-research/03-sec-edgar.md`.

### 5.3 `config/settings.yml`

```yaml
pipeline:
  schedule_hours: 6
  lookback_days:
    bills: 14
    filings_8k: 14
    lda_registrations: 60
    model_bill_propagation: 365
  similarity_thresholds:
    bill_clustering: 0.85
    model_bill_match: 0.80
  scoring:
    alert_threshold: 70
    watchlist_threshold: 50
  weights:
    signal_a: { cluster_size: 0.3, lda_recency: 0.2, icp_company_count: 0.3, cluster_cohesion: 0.2 }
    signal_c: { filing_recency: 0.3, bill_count: 0.2, bill_stage: 0.3, match_specificity: 0.2 }
    signal_d: { propagation_count: 0.4, acceleration: 0.3, similarity: 0.15, icp_topic_match: 0.15 }
```

---

## 6. Source clients

### 6.1 Shared HTTP client (`src/signals/http/client.py`)

`httpx`-based, per-host token-bucket rate limiting, retry + jitter on 429/5xx, configurable UA, DEBUG-level request logging.

### 6.2 OpenStates

Auth: `apikey` query or `X-API-KEY` header. Rate limit: 1 req/sec (we run at 0.8). 500 daily budget. `updated_since` watermark for incremental sync. Cache bill text aggressively. API surface: `search_bills(state, query, since)`, `get_bill_text(bill_id)`, `get_recent_bills(states, topics, days)`.

### 6.3 LDA

Auth: `Authorization: Token <key>` header. Rate limit: 120 req/min. Page size silently capped at 25 — paginate explicitly. Watermark on `dt_posted`. Host configurable via `LDA_BASE_URL` for the 2026-06-30 `lda.gov` cutover. API surface: `get_recent_registrations(issue_codes, since)`, `get_registrant_aliases(registrant_id)`.

### 6.4 SEC EDGAR

Use `edgartools` library — do not roll your own DOM walker. UA required (Akamai 403 without). Throttle to 8 req/sec. Raw `submissions.json` for 8-K item-code pre-filtering. API surface: `get_10k_risk_factors(cik)`, `get_recent_8ks(cik, item_codes, since)`, `extract_risk_topics(text, taxonomy)`.

### 6.5 ALEC

Scrape model legislation library; cache locally; weekly refresh job. **Fall back to a static `config/model_bills.yml` with 10–15 hand-curated bills if scraping is too brittle.** Better to ship D3 on a small static corpus than burn a day debugging selectors.

### 6.6 Development mode: fixtures-first

The pipeline must support two modes via env var:

**`USE_LIVE_APIS=false` (default):**
- All source clients read from `tests/fixtures/<source>/<scenario>.json`
- No external API calls of any kind
- All Anthropic calls read from `tests/fixtures/anthropic/<scenario>.json`

**`USE_LIVE_APIS=true`:**
- All source clients hit real APIs
- Anthropic API calls go to live API
- Use sparingly: capture/refresh fixtures, integration tests, final demo

The fixture loader resolves a scenario name to a JSON file under `tests/fixtures/<source>/`. Scenario names map to real test cases (e.g., `recent_bills_pharma_ca_2026-05.json`).

**`scripts/capture_fixtures.py`:** takes a list of scenarios; sets `USE_LIVE_APIS=true` internally; calls the real API and writes responses to fixture files; redacts API keys appearing in URLs before saving. Run manually only — not in CI.

**Required seed coverage:**
- OpenStates: ≥1 `recent_bills` and ≥1 `bill_text`
- LDA: ≥1 `recent_registrations`
- EDGAR: ≥1 `submissions.json` (Pfizer or other ICP), ≥1 10-K Item 1A text, ≥1 8-K with item codes
- Anthropic: ≥1 embedding and ≥1 extraction per prompt template

**Contract:** the pipeline must work end-to-end against fixtures alone. A reviewer pulling the repo with no API keys can run `USE_LIVE_APIS=false python -m signals.main` and see the pipeline complete, posting alerts to stdout (or Slack if `SLACK_WEBHOOK_URL` is set). Live mode validates production wiring, not logic.

---

## 7. Enrichment

### 7.1 Embeddings (`enrich/embeddings.py`)

Anthropic SDK embeddings. Disk cache keyed by content hash. Batch where possible.

### 7.2 LLM extraction (`enrich/extraction.py`)

Two tasks:
- **Risk factor topic extraction (Signal A):** input 10-K Item 1A + taxonomy; output structured JSON of topics mentioned with supporting text. Model `claude-sonnet-4-6`. Prompt: `prompts/risk_factor_topics.md`.
- **8-K state regulation extraction (Signal C):** input 8-K text + US state list; output `{mentions_state_regulation, states, topics, supporting_text}`. Model `claude-sonnet-4-6`. Prompt: `prompts/eight_k_state_regulation.md`.

Prompts as template files, not hardcoded strings.

### 7.3 ICP matching (`enrich/icp.py`)

`cik_to_company`, `company_topics`, `state_to_companies`, `registrant_alias_to_company`.

---

## 8. Scoring

`score/scoring.py`. Each signal yields a `ScoreBreakdown { total: int 0-100, components: dict, confidence: high|medium|low }`. Breakdown surfaces in the Slack alert.

Routing:
- `total ≥ 70` → Slack alert
- `total ∈ [50, 70)` → `data/watchlist.jsonl`
- `total < 50` → suppressed

Precision > recall. Rep trust is the binding constraint.

---

## 9. Distribution: Slack

`distribute/slack.py`. Block Kit alerts with header (score + company + ICP context), why-now narrative, key facts table, talking point, score breakdown footnote, action buttons (bill / 10-K / LDA filing links).

If `SLACK_WEBHOOK_URL` unset, prints formatted alert to stdout. Same code path otherwise.

---

## 10. Testing

`pytest`. Coverage bar:
- One parse test per source against fixture
- Two cases per detector (positive + negative)
- Per-component scoring tests
- Block Kit validity test
- End-to-end smoke against fixtures

Fixtures live in `tests/fixtures/`.

---

## 11. Deployment

### GitHub Actions `pipeline.yml`

Cron `0 */6 * * *` + `workflow_dispatch`. Runs `python -m signals.main` with secrets injected (`OPENSTATES_API_KEY`, `LDA_API_KEY`, `LDA_BASE_URL`, `SLACK_WEBHOOK_URL`, `ANTHROPIC_API_KEY`, `EDGAR_USER_AGENT`).

### Modal `app/server.py`

Minimal FastAPI. `GET /health` returns `{status, last_run}`. `POST /trigger` requires `X-Trigger-Secret: $TRIGGER_SECRET`.

---

## 12. Docs

- `README.md` — what, quick-start (both modes), architecture, signals, configs, deployment, live URL, limits, next.
- `DECISION_MEMO.md` — stub with TODO markers (Sydney fills in).
- `ARCHITECTURE.md` — mermaid diagram, design rationale.

---

## 13. Build order (fixtures-first, amended)

1. **Repo + scaffolding** — pyproject, .env.example, .gitignore, module skeleton, GH repo, initial commit.
2. **Settings + logging** — env loader with required-key validation.
3. **Shared HTTP client** — rate limit, retry, logging.
4. **Fixture infrastructure** — loader pattern (§6.6). `scripts/capture_fixtures.py`. Loader dispatches live/fixture cleanly.
5. **OpenStates client** — live path. Run `capture_fixtures.py` once to seed. All further dev against fixtures.
6. **SEC EDGAR client** — `edgartools` wrapper. Capture Pfizer 10-K + 8-K fixtures. Verify Item 1A extraction works on the fixture.
7. **LDA client** — capture recent registrations fixture. Verify token during capture; everything else against fixture.
8. **ALEC scraper or static fallback** — if static, the "fixture" is just the YAML.
9. **ICP loader** — load `companies.yml`, basic lookups.
10. **Embeddings + LLM extraction** — live + fixture paths. Capture seeds for each prompt template. Iterate prompts against fixtures.
11. **Signal A detector** — implement against fixtures. Positive + negative case.
12. **Signal C detector** — same.
13. **Signal D detector** — same.
14. **Scoring** — weighted scoring with breakdown.
15. **Slack distributor** — Block Kit + stdout dry-run mode.
16. **Main entrypoint** — wire everything. End-to-end fixture run must produce ≥1 stdout alert.
17. **Tests** — coverage to minimum bar. Same fixtures.
18. **GitHub Actions workflow** — cron + secrets. Workflow runs with `USE_LIVE_APIS=true`.
19. **Modal deployment** — `/health` + `/trigger`.
20. **First live integration run** — `USE_LIVE_APIS=true python -m signals.main` locally with all credentials set. Verify Slack receives a real alert. Only place live mode is mandatory.
21. **README** — both modes documented.

If running short after step 16, prioritize 18–20 (deployment + live run) over additional tests.

---

## 14. Hand-off deliverables

- Public GitHub repo URL
- Live Modal URL
- README enabling external reviewer to run locally
- ≥1 real Slack alert in Sydney's #signals channel
- Stub `DECISION_MEMO.md` ready for Sydney

Report back: repo URL, live URL, deviations from research, spec items that turned out wrong.

---

## 15. Do NOT do

No database (JSON + in-memory state is fine). No UI beyond FastAPI health/trigger. No OAuth, multi-tenancy, rep feedback loop. No >3 signals. Not Vercel — use Modal (justify in README). Don't pad README. Never commit `.env` or any key.

---

## 16. Open questions to surface during build

- "ALEC scraping harder than expected — fall back to static corpus?"
- "Signal X firing too often / too rarely — tune threshold?"
- "Source Y returned different shape than research — adapt or skip?"
- "Modal deployment hit a snag — Fly.io / Railway instead?"
- "Need [thing not in env vars] — can you provide it?"

The cost of asking is small. The cost of guessing wrong is large.
