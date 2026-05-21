# signals

GTM signal pipeline for State Affairs. Detects coordinated multistate
regulatory pressure on US pharma manufacturers and routes prioritized leads to
the sales team via Slack.

The pipeline runs every 6 hours, joins legislative tracking data with SEC
filings and federal lobbying registrations, and surfaces three buying-signal
patterns:

- **Signal A** — Coordinated multistate legislative waves: 3+ near-identical
  bills introduced across 3+ states + a recent federal lobbying registration
  on the same issue area + an ICP company whose 10-K Item 1A already flagged
  the topic.
- **Signal C** — SEC 8-K material event + active state bill match: an ICP
  company files an 8-K naming a specific state regulatory topic AND a bill on
  that topic is currently active in the named state.
- **Signal D3** — Model legislation propagation velocity: a new state bill is
  substantively similar to a known model bill (ALEC, NASHP) that has already
  propagated to 3+ prior states.

Full design in [`docs/SPEC.md`](docs/SPEC.md). Source-by-source research that
informed the build is in [`docs/source-research/`](docs/source-research/).

## Quick start

```bash
git clone https://github.com/sydneywatk/signals.git
cd signals
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Fixture mode (default): no API keys required.
python -m signals.main
```

You'll see 8+ alerts print to stdout, plus a watchlist entry written to
`data/watchlist.jsonl`.

To run against live APIs, copy `.env.example` to `.env`, fill in keys, and:

```bash
USE_LIVE_APIS=true python -m signals.main
```

If `SLACK_WEBHOOK_URL` is set, alerts post to Slack. Otherwise they print to
stdout regardless of mode.

## Architecture

```
GitHub Actions cron (every 6h)
        |
        v
   main.py — run_pipeline()
        |
        +---> Sources (parallel fetch)
        |       openstates.py  -- bill search
        |       lda.py         -- LD-1 registrations
        |       edgar.py       -- 10-K + 8-K via edgartools
        |       alec.py        -- static model-bill corpus (config/model_bills.yml)
        |
        +---> Enrich
        |       icp.py         -- ICP company / topic lookups
        |       embeddings.py  -- TF-IDF cosine (sklearn)
        |       extraction.py  -- Claude tool-use for 10-K topic + 8-K state-reg extraction
        |
        +---> Detect
        |       signal_a.py  -- coordinated multistate bills
        |       signal_c.py  -- 8-K + state bill match
        |       signal_d.py  -- model bill propagation
        |
        +---> Score (transparent component breakdown per signal)
        |
        +---> Distribute (Slack Block Kit, or stdout if no webhook)
```

Diagram in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Fixtures-first development

The pipeline supports two modes via `USE_LIVE_APIS`:

- `false` (default) — every source module reads from `tests/fixtures/<source>/<scenario>.json`.
  No external calls. A reviewer with zero API keys can run the pipeline
  end-to-end and see alerts to stdout.
- `true` — source modules hit real APIs; Anthropic calls go live.

To refresh fixtures (requires keys):

```bash
python scripts/capture_fixtures.py            # all
python scripts/capture_fixtures.py openstates # one source
python scripts/capture_fixtures.py edgar:risk_factors_78003  # one scenario
```

See [`docs/SPEC.md`](docs/SPEC.md) §6.6 for the full fixture contract.

## Configuration

- `config/companies.yml` — ICP target list (13 US-domiciled pharma issuers,
  SIC 2834 and adjacent 2835/2836).
- `config/topics.yml` — pharma regulatory taxonomy (8 topics: drug price
  transparency, PBM regulation, Prop 65, drug take-back, drug affordability
  boards, 340B disputes, supply chain, prescribing authority).
- `config/model_bills.yml` — 12 hand-curated model bills (ALEC + NASHP) used
  by Signal D3.
- `config/settings.yml` — lookback windows, similarity thresholds, scoring
  weights, alert thresholds.

## Deployment

### GitHub Actions cron (live runtime)

`.github/workflows/pipeline.yml` runs every 6 hours via cron and on demand via
`workflow_dispatch`. Required repo secrets:

| Secret | Required for |
|---|---|
| `OPENSTATES_API_KEY` | Bill ingestion |
| `LDA_API_KEY` | Lobbying registrations |
| `LDA_BASE_URL` | Migration to `lda.gov` on 2026-06-30 |
| `ANTHROPIC_API_KEY` | 10-K + 8-K extraction |
| `EDGAR_USER_AGENT` | SEC EDGAR access (mandatory per SEC policy) |
| `SLACK_WEBHOOK_URL` | Distribution (optional — falls back to stdout if unset) |

Run a manual cycle from the Actions tab → "Pipeline run" → "Run workflow".

### Modal — live URL

The exercise asks for a hosted URL. We use **Modal** rather than Vercel because
Modal natively supports Python and doesn't impose Vercel's 60s function timeout
(pipeline runs are 2–4 minutes in live mode).

```bash
pip install modal
modal token new                     # first-time auth
modal secret create signals-secrets \
  OPENSTATES_API_KEY=... LDA_API_KEY=... LDA_BASE_URL=... \
  ANTHROPIC_API_KEY=... EDGAR_USER_AGENT=... \
  SLACK_WEBHOOK_URL=... TRIGGER_SECRET=...
modal deploy app/server.py
```

Endpoints:

- `GET  /health` — returns `{status, last_run}` where `last_run` is the most
  recent successful run summary.
- `POST /trigger` — runs an out-of-cycle pipeline. Requires
  `X-Trigger-Secret: $TRIGGER_SECRET` header.

The cron is the real runtime; the URL exists to satisfy the exercise's live-URL
requirement and to provide an integration entry point.

## Source coverage & known limits

- **OpenStates** rate-limits aggressively on the free tier (~1 req/sec stated;
  empirically 429s under bursts). Capture script uses 8s spacing.
- **LDA** sunsets `lda.senate.gov` on **2026-06-30**, migrating to `lda.gov/api/v1/`.
  The host is configurable via `LDA_BASE_URL`. Issue-code filtering happens
  client-side (the `/filings/` endpoint has no structured filter on
  `general_issue_code`; only free-text via `filing_specific_lobbying_issues`).
- **SEC EDGAR** requires a declared User-Agent (Akamai 403s without).
  `edgartools` handles the Item 1A extraction (TOC false-positives, missing
  Item 1B, iXBRL wrappers).
- **8-K body text rarely mentions state regulations** — across 195 8-Ks
  scanned for state regulatory keywords, zero hits. State regulatory
  discussion lives in 10-Q MD&A and 8-K Exhibit 99 press releases. Signal C
  in v1 ships with one synthetic fixture clearly labeled
  `is_synthetic_demo: true`; v2 would extend Signal C to read 8-K exhibits.
- **Embeddings** use scikit-learn TF-IDF rather than Claude/Voyage dense
  embeddings. Tuning rationale + swap path in
  [`docs/DECISION_MEMO.md`](docs/DECISION_MEMO.md).

## What I'd build next

Beyond v1 (this build):

1. Voyage AI dense embeddings replacing TF-IDF — paraphrased multistate bills
   would match where lexical similarity misses them.
2. 10-K Item 1A extraction for all 13 ICP companies (not just Pfizer).
   ~$0.25 in Claude calls one-time + weekly refresh.
3. Signal C v2 — read 8-K Exhibit 99 press releases instead of just 8-K body
   text. State regulatory discussion lives in attached press releases.
4. Cross-run dedup — alert state in SQLite or `data/alerted.jsonl` so the
   same `(signal_type, company, evidence_anchor)` isn't fired twice.
5. 20-F support — adds AstraZeneca, Novartis, GSK, Sanofi, Novo Nordisk to the
   ICP. Risk-factor section is Item 3.D, not Item 1A.

Detailed scoping in [`docs/DECISION_MEMO.md`](docs/DECISION_MEMO.md).

## Repo layout

See [`docs/SPEC.md`](docs/SPEC.md) §4.1.
