# Decision Memo

Stub for Sydney's narrative. The empirical findings below were surfaced during
build and are the raw material for each section — Sydney rewrites in her voice.

## Three biggest tradeoffs

<!-- TODO Sydney: rewrite below into your own framing. -->

**1. TF-IDF embeddings instead of Claude/Voyage dense embeddings.**
The spec called for Claude embeddings, but Anthropic doesn't expose an
embeddings endpoint directly (Voyage AI is the recommended path, which would
require an additional API key). To preserve the fixtures-first "zero keys
required" contract, I shipped with scikit-learn TF-IDF. Similarity thresholds
were tuned accordingly: spec's 0.85 became 0.25 for bill clustering, 0.80
became 0.20 for model-bill matching. TF-IDF captures vocabulary overlap, which
is the dominant signal for near-verbatim coordinated bills and ALEC-style
model bills — but it misses paraphrased same-meaning legislation. Voyage in v2.

**2. Synthetic 8-K fixture for Signal C demo.**
Scanned all 195 most-recent 8-Ks across the 13 ICP companies for state
regulatory keywords (`Proposition 65`, `drug pricing transparency`, `340B`,
`PBM`, etc.). Zero hits. State regulatory discussion lives in 10-Q MD&A and
8-K Exhibit 99 press releases, not 8-K body text. To demonstrate Signal C
firing, I appended one synthetic 8-K to `tests/fixtures/edgar/recent_8ks_78003.json`
flagged with `is_synthetic_demo: true`. The Slack alert renders a warning row
when this fixture is the source. In production Signal C would either (a) read
8-K exhibits, or (b) be rescoped to 10-Q-driven detection.

**3. Pfizer in companies.yml despite being out-of-ICP (large-cap, not mid-cap).**
The fixture demo depends on Pfizer's real 10-K + 8-K shape. Removing Pfizer
would have left the fixture run with zero ICP topic enrichment (the other 12
mid-caps don't have cached 10-K extractions in v1; each adds ~$0.02 in
Claude calls). Pfizer is annotated `notes: "kept for fixture demo. Out of
mid-market ICP."` in the YAML. Live mode should drop it and refresh topic
extractions for the 12 mid-caps weekly.

## What was intentionally not built

<!-- TODO Sydney: explain why each scope cut was the right call. -->

- **No database.** JSON on disk + in-memory state. Adds a state file
  (`data/watchlist.jsonl`, `data/last_run.json`). Pipeline is stateless beyond
  these. SQLite the moment we need cross-run dedup or rep feedback storage.
- **No rep feedback loop.** Spec mentioned this is v2; the v1 distribution is
  one-way (pipeline → Slack). A 👍/👎 button would route into a feedback file
  and influence future scoring weights.
- **Only Pfizer's 10-K topic extraction is cached.** The other 12 ICP
  companies would each need a one-time Claude call (~$0.25 total). Skipping
  this means `icp_company_count` in Signal A scoring is capped at 1 in v1 —
  artificially deflating Signal A's weight. Pre-computing all 13 is the
  highest-ROI scaling step.
- **No ALEC scraping.** Per spec §6.5 fallback, we ship with a hand-curated
  static `config/model_bills.yml`. ALEC's site uses JavaScript-rendered
  navigation and lacks a stable URL pattern; a scraper is a maintenance
  liability for marginal corpus growth.
- **No 20-F support.** Foreign pharma (AstraZeneca, Novartis, GSK, Sanofi,
  Novo Nordisk) would each add ~$1B+ revenue to the addressable ICP. Item 1A
  becomes Item 3.D and the extractor needs a second branch.
- **No cross-run dedup.** Same signal would alert on every cron run until the
  underlying evidence ages out of the window. v1 accepts the duplicate cost.

## Week 2 roadmap (ordered by ROI)

<!-- TODO Sydney: re-order based on what the State Affairs team would prioritize. -->

1. **Pre-compute 10-K topics for all 13 ICP companies.** ~30 minutes work,
   ~$0.25 in Claude calls, unlocks accurate Signal A scoring across the full
   ICP.
2. **Cross-run alert dedup.** `data/alerted.jsonl` keyed on
   `(signal_type, company_cik, evidence_anchor)` with a 7-day TTL. Prevents
   rep alert fatigue.
3. **Voyage AI embeddings.** Replace TF-IDF for both bill clustering and
   model-bill matching. Paraphrased same-meaning bills cluster correctly.
4. **Signal C v2 — 8-K Exhibit 99 reading.** Extend `edgar.get_recent_8ks`
   to also pull Exhibit 99 text. State regulatory discussion actually lives
   there, not in 8-K bodies.
5. **20-F + 40-F support.** Adds foreign and Canadian pharma to ICP.

## Wished-for data

<!-- TODO Sydney: which of these would change the signal model most, and why? -->

- **PhRMA-funded campaign intel** — which trade associations are funding
  state ballot initiatives and amicus briefs. Would gate Signal A with stronger
  actor evidence.
- **State PBM legislator/staff lists** — committee membership + sponsor
  affinity per topic. Would refine Signal A and Signal D3 scoring with stage
  proxies.
- **Real-time committee hearing schedules with witness lists.** OpenStates
  v3 doesn't surface witness data (verified: `EventParticipant` is just name +
  entity type). Per-state scraping required if we want this.
- **Salesforce CRM data on which AE owns which company.** Right now all
  alerts land in a single Slack channel. Per-AE routing requires CRM mapping.

## Pushback question

<!-- TODO Sydney: one specific question that challenges the ICP / signal model. -->

The current ICP definition (US-domiciled mid-market pharma, $500M–$5B, SIC
2834) yields 13 companies. State Affairs' actual paying customers in pharma
GA — are they mostly in this segment, or are they large-cap (Pfizer, Merck,
J&J)? If the latter, the ICP filter should widen and Signal A's
`icp_company_count` normalization should change accordingly (large-caps are
nearly always going to have 10-K exposure to state pricing topics, which
would compress Signal A's signal-to-noise ratio).
