# Decision Memo

**GTM Signal Pipeline · Sydney Watkins · May 2026**

This is a **hot and cold lead generation pipeline** — a layer above per-bill legislative tracking that translates policy events into qualified outbound leads. The thesis: surfacing every bill movement is table-stakes; what's missing is the layer above it that fuses bill data with corporate exposure, lobbying activity, and cross-state model-bill propagation to predict buying moments before the prospect knows they need us. Alerts gate at composite score ≥70 (hot), 50–70 (cold/watchlist), <50 suppressed. Pipeline runs on schedule via GitHub Actions, ingests live, scores against ICP, and posts account-grouped alerts to Slack.

## Three biggest tradeoffs

**Precision over recall, by design.** The composite favors multi-signal convergence on a single account. Fewer, higher-quality alerts. The documented failure mode of signal-based selling is rep trust erosion from noise — a pipeline that fires 200 alerts a week trains reps to ignore Slack. Real opportunities will be missed in v1. The v2 evolution is a rep feedback loop that tunes thresholds without changing the precision-first principle.

**Four deliberately-designed signals over a longer roadmap.** Each uses 3+ sources where every source materially changes the score — no decorative enrichment. I scoped down from a longer signal taxonomy (Appendix B) to ship four that work end-to-end rather than a dozen half-built.

**Scheduled batch over real-time.** GitHub Actions cron, not webhooks or streaming. A buying moment plays out over days-to-weeks, not minutes. The latency cost is negligible; the operational cost savings are significant. Real-time becomes worth it only after the precision question is settled.

## Verification discipline

The pipeline ships with audit reports under `verification/` documenting what I checked before submission. During final audit I caught that the detector was surfacing bills killed by Postpone Indefinitely — a signal-quality bug, not a documentation one. Fixed by gating dead bills (PI, withdrawn, vetoed, failed, died in committee) before scoring. Tested with fixtures covering case-insensitive matching across status and last-action fields. CI runs ruff + pytest on every PR (detectors, scoring, Slack, sources — 26 tests passing) plus a fixture-mode smoke run of the full pipeline.

## Reality check — what would have to be true to scale this

1. **Posture classification is the gating prerequisite.** The dead-bill filter caught one class of false positives; the harder one is industry-favorable bills firing alerts as if they were threats. A pro-pharma bill moving is not a buying moment for Pfizer. v2 needs a posture classifier (bill stance × prospect interest) before this scales to a sales team.
2. **Buying-moment definition needs calibration against actual closed-won data.** v1 uses informed guesses about what predicts a deal; v2 should be trained on State Affairs' real conversion history.
3. **Signals need historical validation.** 6–12 months of backtest against known conversions before any threshold can be defended.
4. **ICP validation against the real paying-customer base.** v1 used a hypothetical pharma GA segment; v2 needs the actual customer profile.
5. **Distribution-channel maturity.** Slack is a demo destination. Production needs CRM integration and per-AE routing rules.

What this build is: an honest demonstration of the architecture, the signal taxonomy, the verification discipline, and the v2 path. What it isn't: a production-ready system that should be pointed at the sales team next week without further calibration.

## Top 3 things I'd build in week 2

1. **Posture classifier** — bill stance (helps/hurts/neutral to industry) × prospect interest direction. Gates alerts so industry-favorable movement doesn't fire as a buying moment. Highest-ROI v2 item.
2. **Rep feedback loop** — thumbs-up/down on Slack alerts → threshold tuning. Closes the precision question with real data instead of guesses.
3. **Integration with State Affairs' first-party reporter feed** — article-mention confirmation signal (Aaron's entity-extraction layer) becomes the strongest enrichment available to no competitor. The combination of (a) the analytics stack, (b) the reporter-network entity extraction, and (c) the signal layer this build demonstrates is the actual GTM platform.

## What I intentionally did not build

See Appendix A. The short version: no auto-email (precision-first means no auto-send until thresholds are trusted), no CRM write-back (depends on which CRM), no UI (Slack is the UI for v1), no signal sub-types beyond the four (scope discipline).

## Data I wish I had

- State Affairs' actual closed-won data, tagged with which signal (if any) preceded the deal
- 6–12 months of historical bill activity tagged with conversion outcomes for backtest
- The real paying-customer base for ICP recalibration (the v1 ICP is hypothetical)
- The reporter-network entity extraction layer Aaron's team is shipping — article mentions of a prospect on a bill they're tracking is uniquely available to State Affairs and unavailable to every public-data competitor
- Per-account legislative tracking history — has this prospect ever cared about this topic before? Changes the alert's meaning entirely

## One pushback question

The spec frames this as "build the pipeline." The harder question I'd push back on before scaling: **what's the definition of a buying moment State Affairs is willing to commit to?** Without that, every signal threshold is an opinion. With it, every signal becomes testable. I'd want that conversation in week 1, not week 4.

---

## Appendix A — What I intentionally did not build

- **Auto-email send.** Precision-first means no automated outbound until thresholds are trusted against real conversion data. The pipeline produces the brief; a human sends it.
- **CRM integration.** Routing depends on which CRM (HubSpot vs Salesforce vs custom) and what per-AE territory rules look like. Better to ask than to guess.
- **UI / dashboard.** Slack is the v1 distribution channel. Building a separate UI before the signal quality is settled is decorative.
- **Signal sub-types beyond the four.** Appendix B lists 11 v2 signals scoped out. Four end-to-end > eleven half-built.
- **Multi-tenant scoring.** v1 scores against one ICP. Multi-tenant is a v3 problem.

## Appendix B — v2 signal roadmap

1. Pre-cache 10-K extractions + ICP expansion
2. Cross-run dedup with decay
3. Rep feedback loop
4. Conversational Slack bot ("why did this fire?")
5. Voyage embeddings for better text similarity
6. Signal C v2: 8-K Exhibit 99 (material event filings)
7. 20-F support (foreign issuers)
8. Automated outbound email (gated on threshold trust)
9. Time-to-decision modeling
10. Donor network signal
11. First-party reporter feed integration (Aaron's entity extraction)

## Appendix C — Spec coverage

| Requirement | Where covered |
|---|---|
| Target ICP segment | README + memo opening |
| Four novel signals | README + Appendix B for v2 |
| Bill data + 2+ enrichment sources | OpenStates + LDA + SEC EDGAR + news |
| Live deployed pipeline | GitHub Actions cron |
| Scoring + prioritization | Composite scoring, ≥70 hot / 50–70 cold |
| Distribution channel | Slack webhook, account-grouped |
| Three tradeoffs | This memo |
| What I didn't build | Appendix A |
| Week 2 priorities | This memo, top 3 |
| Wished-for data | This memo |
| Pushback question | This memo |

## Appendix D — How this integrates with State Affairs' existing stack

This signal layer is designed to sit on top of, not parallel to, State Affairs' existing infrastructure. In production: the analytics stack (Fivetran → BigQuery → dbt) provides the ICP and customer behavior context; the editorial entity-extraction layer (Aaron's team) provides the article-mention confirmation signal that no public-data competitor can produce; the signal layer this build demonstrates is the orchestration that fuses them into alerts. The combination is the GTM moat — each layer alone is replicable, the integration is not.
