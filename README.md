# signals

GTM signal pipeline for State Affairs. Detects coordinated state-regulatory
pressure on US pharma manufacturers and surfaces one prioritized account
alert per company per run via Slack.

The pipeline runs every 6 hours, joins legislative tracking, SEC filings,
federal lobbying, governor signing history, and a static model-bill corpus,
and emits four signal patterns (internal codes in parens):

- **Multistate Convergence** (A) — 3+ near-identical bills introduced across
  3+ states in 14 days + federal lobbying activity on the same issue + an
  ICP company whose 10-K Item 1A already flagged the topic.
- **Public Risk Disclosure** (C) — an ICP company files an 8-K (item codes
  7.01 / 8.01 / 1.05 / 2.05) naming a specific state regulatory topic AND a
  bill on that topic is currently active in the named state. Ships as a
  clearly-labeled synthetic fixture in v1 (`is_synthetic_demo: true`) because
  state regulatory discussion empirically lives in 10-Q MD&A and 8-K
  Exhibit 99, not 8-K body text. v2 extends detection to Exhibit 99.
- **Model Bill Spread** (D3) — a new state bill substantively matches a
  known ALEC / NASHP model bill that has already propagated to 3+ prior
  states.
- **Governor Track Record** (E4) — a topic-relevant bill is introduced in a
  state whose current governor has signed ≥70% of similar bills this term.

See [`docs/SIGNALS_BUSINESS.md`](docs/SIGNALS_BUSINESS.md) for AE / CRO
framing, [`docs/SIGNALS_TECHNICAL.md`](docs/SIGNALS_TECHNICAL.md) for
algorithm + sources per signal, [`docs/SCORING.md`](docs/SCORING.md) for
weights + composite score math, [`docs/SPEC.md`](docs/SPEC.md) for the full
build spec, [`docs/DECISION_MEMO.md`](docs/DECISION_MEMO.md) for tradeoffs
and roadmap, and [`docs/source-research/`](docs/source-research/) for the
per-API research that informed the build.

## Quick start

```bash
git clone https://github.com/sydneywatk/signals.git
cd signals
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Fixture mode (default): no API keys required.
python -m signals.main
```

You'll see an account alert print to stdout, a watchlist entry written to
`data/watchlist.jsonl`, and a per-account markdown brief written to
`briefs/<cik>_<YYYY-MM-DD>.md`.

For live runs, copy `.env.example` to `.env`, fill in real keys, and:

```bash
USE_LIVE_APIS=true python -m signals.main
```

If `SLACK_WEBHOOK_URL` is set (either mode), the alert posts to Slack
instead of stdout. Each Slack alert links to a long-form brief on GitHub.

## Architecture

```
GitHub Actions cron (every 6h)              POST /trigger on Modal
        |                                            |
        +------------------> main.run_pipeline ------+
                                      |
        +---> Sources (parallel fetch)
        |       openstates.py  -- bill search + historical bills with actions
        |       lda.py         -- LD-1 registrations
        |       edgar.py       -- 10-K + 8-K via edgartools
        |       alec.py        -- static model-bill corpus
        |
        +---> Enrich
        |       icp.py         -- ICP company / topic lookups
        |       embeddings.py  -- sentence-transformers all-MiniLM-L6-v2 (dense, local)
        |       extraction.py  -- Claude tool-use for 10-K topics, 8-K state-regs,
        |                          and opener-variant generation
        |
        +---> Detect (each detector applies the bill-status filter inline
        |              via detectors/_common.is_actionable_and_current())
        |       signal_a.py   -- Multistate Convergence
        |       signal_c.py   -- Public Risk Disclosure (synthetic demo in v1)
        |       signal_d.py   -- Model Bill Spread
        |       signal_e.py   -- Governor Track Record
        |
        |       Filter behavior (in _common.py, applied inside each detector):
        |         • drops bills enacted >30 days ago (chaptered, became law,
        |           signed by governor, approved by governor, etc.)
        |         • drops dead bills regardless of date (postpone indefinitely,
        |           withdrawn, vetoed without override, died in committee, failed)
        |         • drops bills with no action in >180 days (prior-session stale)
        |
        +---> Score (per-signal weighted breakdown, composite account score)
        |
        +---> Dedup (within-run, collapses same-buying-moment alerts)
        |
        +---> Distribute
                aggregate_by_company  -- one alert per company per run
                brief.py              -- writes per-account markdown brief
                slack.py              -- Block Kit alert + "View full brief →" button
```

Diagram + design rationale in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Verification discipline

Before submitting, I audited every claim in the demo brief against
authoritative sources (OpenStates bill text + action histories, SEC EDGAR
10-K passages). The audit reports are committed at:

- [`verification/2026-05-21_brief_audit.md`](verification/2026-05-21_brief_audit.md) — initial audit covering 7 checks across the lead alert, propagation claims, governor track record sample, 10-K topic extraction quality, and cosine sanity.
- [`verification/2026-05-21_final_check.md`](verification/2026-05-21_final_check.md) — follow-up audit after applying initial fixes.

The audits surfaced three classes of v1 failure mode that have been
addressed or scoped:

1. **Stale enacted and dead bills firing as current alerts** (CA SB 40
   chaptered 2025-10-13; OR HB 4040 signed 2026-04-07; CO SB 140 and CO
   HB 1056 *Postponed Indefinitely* months ago). **Fixed in v1** via the
   `is_actionable_and_current()` filter in `detectors/_common.py`, applied
   inline inside each detector. Catches enacted-stale bills (>30d post
   enactment) and dead bills (postpone indefinitely / withdrawn / vetoed
   without override / died in committee / failed) regardless of date.
2. **Self-match bug in Signal E4 historical bag** (candidate bill matching
   itself at cosine 1.000). **Fixed in v1** via candidate-bill exclusion.
3. **Topic-aware signals without posture awareness** — v1 detects topic
   match + ICP 10-K exposure but doesn't classify whether a bill expands
   or narrows manufacturer obligations. Pharma-favorable bills can fire as
   alerts under current logic. **Documented as highest-priority v2 work**
   in `DECISION_MEMO.md` (posture classifier returning
   `adverse | favorable | neutral` per bill × ICP company, gated before
   alert eligibility). Not shipped in v1 because doing it well requires a
   validation set and prompt engineering the time budget didn't allow.

The discipline matters more than any individual finding: a sales-tooling
system that can't audit its own output before reps see alerts is one that
erodes trust on contact.

## Fixtures-first development

The pipeline supports two modes via `USE_LIVE_APIS`:

- `false` (default) — every source module reads from
  `tests/fixtures/<source>/<scenario>.json`. No external calls. A reviewer
  with zero API keys can run the pipeline end-to-end.
- `true` — source modules hit real APIs; Anthropic calls go live.

Embeddings (sentence-transformers) always compute locally; results cache to
`data/embeddings_cache.json` keyed by content hash.

To refresh fixtures (requires keys):

```bash
python scripts/capture_fixtures.py                            # all
python scripts/capture_fixtures.py openstates                 # one source
python scripts/capture_fixtures.py edgar:risk_factors_78003   # one scenario
```

See [`docs/SPEC.md`](docs/SPEC.md) §6.6 for the full fixture contract.

## Configuration

- `config/companies.yml` — ICP target list (13 US-domiciled pharma issuers, SIC 2834 and adjacent 2835/2836).
- `config/topics.yml` — pharma regulatory taxonomy (8 topics: drug price transparency, PBM regulation, Prop 65, drug take-back, drug affordability boards, 340B disputes, supply chain, prescribing authority).
- `config/model_bills.yml` — 12 hand-curated ALEC + NASHP model bills with `known_propagation` lists.
- `config/governors.yml` — current governor + term-start date per state (13 states curated).
- `config/settings.yml` — lookback windows, similarity thresholds, scoring weights, alert thresholds, bill status filter terms.

## Alert format

One Slack message per company per run:

- **Header** — emoji + composite score + company name
- **Fields block** (4-up) — Company / Account Score / Signals Firing / Top Signal
- **Suggested opener** — one Claude-generated sentence the AE can read aloud;
  positions State Affairs as additive coverage / pattern detection /
  bandwidth, assuming the buyer already knows the legislation
- **Why now** — top signal's narrative
- **Key facts** — monospaced table of bill IDs, states, topic, LDA filing
- **Other firing signals** — supporting alerts with human-readable labels
- **Actions** — up to 5 buttons including "View full brief →", bill links, 8-K link, LDA filing link, 10-K history

The score breakdown lives in the brief (collapsible `<details>` section),
not in Slack — Slack stays AE-readable. The brief at
`briefs/<cik>_<YYYY-MM-DD>.md` contains all firing signals + supporting
quotes + bill links + LDA details + score breakdown + 3 opener variants +
account context.

## Deployment

### GitHub Actions cron

`.github/workflows/pipeline.yml` runs every 6 hours via cron and on demand
via `workflow_dispatch`. Required repo secrets (all set via `gh secret set`):

| Secret | Required for |
|---|---|
| `OPENSTATES_API_KEY` | Bill ingestion |
| `LDA_API_KEY` | Lobbying registrations |
| `LDA_BASE_URL` | Migration to `lda.gov` on 2026-06-30 |
| `ANTHROPIC_API_KEY` | 10-K + 8-K extraction + opener generation |
| `EDGAR_USER_AGENT` | SEC EDGAR access (mandatory per SEC policy) |
| `SLACK_WEBHOOK_URL` | Distribution (optional — falls back to stdout if unset) |
| `VOYAGE_API_KEY` | Configured but unused — see "Embeddings" below |

### Modal — live URL

Modal over Vercel because Vercel imposes a 60s function timeout; live
pipeline runs take 3–4 minutes.

```bash
pip install modal
modal token new                     # first-time browser auth
modal secret create signals-secrets \
  OPENSTATES_API_KEY=... LDA_API_KEY=... LDA_BASE_URL=... \
  ANTHROPIC_API_KEY=... EDGAR_USER_AGENT=... \
  SLACK_WEBHOOK_URL=... TRIGGER_SECRET=...
modal deploy app/server.py
```

Endpoints:

- `GET /health` — returns `{status, last_run}` summary
- `POST /trigger` — runs an out-of-cycle pipeline. Requires
  `X-Trigger-Secret: $TRIGGER_SECRET` header.

The cron is the real runtime; the URL exists for the exercise's live-URL
requirement and as an integration entry point.

## Embeddings (v1 decision)

Spec called for Claude embeddings; Anthropic doesn't expose one directly
(Voyage AI is the recommended path). We attempted **Voyage `voyage-3`**
first — the free tier rate-limits at 3 RPM / 10K TPM, which our ~150-doc
cold runs exceed even with aggressive throttling. To preserve the
fixtures-first "zero keys required" contract, v1 ships on
**sentence-transformers `all-MiniLM-L6-v2`** (384-dim, local, deterministic,
no API costs). Voyage swap path is preserved in
[`docs/DECISION_MEMO.md`](docs/DECISION_MEMO.md) §Tradeoffs.

## Source coverage + known limits

- **OpenStates** rate-limits aggressively on the free tier (~1 req/sec
  stated; empirically 429s under bursts). Capture script uses 8s spacing.
- **LDA** sunsets `lda.senate.gov` on **2026-06-30**, migrating to
  `lda.gov/api/v1/`. Host is configurable via `LDA_BASE_URL`. The
  `/filings/` endpoint has no structured `general_issue_code` filter — we
  pull by `filing_type=RR` then filter client-side. Signal A's narrative
  uses an attribution gate (`_is_pharma_credible_actor`) to avoid claiming
  a non-pharma actor is mobilizing on the topic.
- **SEC EDGAR** requires a declared User-Agent (Akamai 403s without).
  `edgartools` handles Item 1A extraction's edge cases (TOC false matches,
  missing Item 1B, iXBRL wrappers).
- **8-K body text rarely mentions state regulations.** Empirical: 195 8-Ks
  scanned across the 13 ICP companies returned zero hits for state
  regulatory keywords. v1 ships with one synthetic 8-K fixture clearly
  flagged `is_synthetic_demo: true`. v2 reads 8-K Exhibit 99 press releases.
- **Bill posture is not classified in v1.** Topic-aware detection fires on
  any bill matching a topic + ICP 10-K exposure, regardless of whether the
  bill expands or narrows manufacturer obligations. See verification
  reports and decision memo for the v2 posture-classifier scope.
- **Model bill propagation lists are state-only (no bill IDs)** in v1.
  `known_propagation` is a flat state-code list per model bill; bill-level
  provenance is v2.

## What I'd build next

1. **Posture classifier** — Claude-based `adverse | favorable | neutral`
   classification per (bill × ICP company) pair, gated before alert
   eligibility. Closes the failure mode the verification audit surfaced.
2. **Cross-run dedup** with 7-day TTL keyed on
   `(signal_type, company, evidence_anchor)`. v1 only dedups within a run.
3. **10-K topic extraction for all 13 ICP companies** (currently only
   Pfizer is cached). ~$0.25 in Claude calls; unlocks accurate Signal A
   `icp_company_count` scoring across the full ICP.
4. **Signal C v2** — read 8-K Exhibit 99 press releases instead of just
   8-K body text. The actual state regulatory discussion lives there.
5. **Voyage embeddings** once a payment method unblocks rate limits — would
   improve paraphrase matching.
6. **20-F + 40-F support** — adds AstraZeneca, Novartis, GSK, Sanofi, Novo
   Nordisk to ICP. Risk-factor section is Item 3.D in 20-F, not Item 1A.
7. **Rep feedback loop** — `:fire:` / `:zzz:` reactions feed
   `data/feedback.jsonl`; weekly job re-tunes weights.

Detailed scoping in [`docs/DECISION_MEMO.md`](docs/DECISION_MEMO.md).

## Repo layout

```
signals/
├── README.md
├── .env.example
├── pyproject.toml
├── .github/workflows/{ci.yml, pipeline.yml}
├── app/server.py                    # FastAPI for Modal
├── briefs/                          # per-account markdown briefs (linked from Slack)
├── verification/                    # audit reports against authoritative sources
├── config/
│   ├── companies.yml
│   ├── topics.yml
│   ├── model_bills.yml
│   ├── governors.yml
│   └── settings.yml
├── docs/
│   ├── SPEC.md
│   ├── ARCHITECTURE.md
│   ├── SIGNALS_BUSINESS.md
│   ├── SIGNALS_TECHNICAL.md
│   ├── SCORING.md
│   ├── DECISION_MEMO.md
│   └── source-research/
├── scripts/capture_fixtures.py
├── src/signals/
│   ├── main.py, settings.py, fixtures.py, logging_config.py
│   ├── sources/{openstates.py, lda.py, edgar.py, alec.py}
│   ├── enrich/
│   │   ├── icp.py, embeddings.py, extraction.py
│   │   └── prompts/{risk_factor_topics.md, eight_k_state_regulation.md}
│   ├── detectors/
│   │   ├── _common.py              # bill-status filter helpers used by all detectors
│   │   └── signal_{a,c,d,e}.py
│   ├── score/scoring.py
│   ├── distribute/
│   │   ├── slack.py, brief.py
│   │   └── prompts/suggested_opener.md
│   └── http/{client.py, rate_limit.py}
├── tests/
│   ├── conftest.py, fixtures/
│   └── test_{sources,detectors,scoring,slack}.py  # filter behavior exercised
│                                                  # via detector tests
└── data/                            # runtime: watchlist.jsonl, last_run.json, embeddings_cache.json
```
