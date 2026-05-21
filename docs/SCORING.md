# Scoring

Every alert that ships to Slack carries a score in 0–100, a per-component
breakdown showing what drove the score, and a confidence band. This doc
specifies the math, the weight rationale, the thresholds, and the v2 path.

## Raw signal score (per signal)

Each signal type defines a small set of `score_inputs` produced by the
detector. The scoring layer normalizes each input to a 0–100 scale, applies
a per-signal weight from `config/settings.yml`, and sums.

`score = sum_over_components(normalize(input) * weight)` — sums to 0–100 because weights per signal sum to 1.0.

### Signal A weights (`signal_a`)
| Component | Weight | Normalizer | Rationale |
|---|---|---|---|
| `cluster_size` | 0.30 | `min(n/5, 1) * 100` | Already gated at `>=3`; 5 bills is a strong cluster. |
| `lda_recency` | 0.20 | `100 - days_since * (100/60)` | LDA registration today → 100; 60d → 0. |
| `icp_company_count` | 0.30 | `min(n/3, 1) * 100` | 1 ICP match → 33 (acceptable). 3+ → 100. |
| `cluster_cohesion` | 0.20 | `cohesion * 100` | Voyage mean pairwise similarity; 0.70 → 70, 1.0 → 100. |

### Signal C weights (`signal_c`)
| Component | Weight | Normalizer | Rationale |
|---|---|---|---|
| `filing_recency` | 0.30 | `100 - days_since * (100/14)` | 14-day window; closer to filing date = higher. |
| `bill_count` | 0.20 | `min(n/5, 1) * 100` | More matching active bills = stronger case. |
| `bill_stage` | 0.30 | `count_acted_30d * 33` | Recent legislative action proxies for urgency. |
| `match_specificity` | 0.20 | tiered by named-state count | 1 specific state = sharper signal than 4. |

### Signal D3 weights (`signal_d`)
| Component | Weight | Normalizer | Rationale |
|---|---|---|---|
| `propagation_count` | 0.40 | `min(n/10, 1) * 100` | The whole point of D3 is propagation. Dominant weight. |
| `acceleration` | 0.30 | `states / (year-2010)` | More states / fewer years = faster spread. |
| `similarity` | 0.15 | `voyage_sim * 100` | 0.65 → 65, 1.0 → 100. Threshold-gated already at 0.65. |
| `icp_topic_match` | 0.15 | binary `0/100` | ICP company has 10-K topic exposure or not. |

### Signal E4 weights (`signal_e`)
| Component | Weight | Normalizer | Rationale |
|---|---|---|---|
| `sign_rate` | 0.45 | `rate * 100` | The headline number. 70% → 70, 100% → 100. |
| `sample_size` | 0.15 | `min(n/10, 1) * 100` | More historical bills = more reliable rate. |
| `bill_stage` | 0.20 | `stage / 3 * 100` | 0=intro, 3=passed both chambers. Closer to desk = sharper. |
| `icp_topic_match` | 0.20 | binary `0/100` | ICP company exposure. |

## Composite account score

The Slack alert is account-grouped: one message per company per run. The
composite score for a company alert is:

`composite = min(100, max(signal_scores) + 5 * (num_signals - 1))`

Rationale: a single strong signal floors the score; each additional firing
signal adds 5 points (so 2 signals at 80 ranks above 1 signal at 84). Cap
prevents runaway from a noisy day. The system rewards "this account has
multiple things going on right now" without letting volume bury quality.

**Worked example — Pfizer's current alert:**
Top D3 = 84, second D3 = 83, Signal A = 77.
`max = 84`. `num_signals = 3`. `composite = min(100, 84 + 5*2) = 94`. Reported
as **🔥 Account Score 94 — Pfizer Inc · 3 signals firing**.

## Thresholds + routing

| Band | Range | Action |
|---|---|---|
| Alert | `score >= 70` | Posts to Slack #signals channel via webhook |
| Watchlist | `50 <= score < 70` | Written to `data/watchlist.jsonl` for analyst review |
| Drop | `score < 50` | Logged at INFO, not surfaced |

Routing uses the **raw per-signal score**, not the composite — so a company
with one signal scoring 65 + a second scoring 30 produces no Slack alert (the
65 goes to watchlist; the 30 drops).

## Confidence bands

Computed from the per-signal raw score, surfaced on the alert as
`high | medium | low`:

- `high`: `score >= 80`
- `medium`: `50 <= score < 80`
- `low`: `score < 50`

The lead signal on a multi-signal account alert sets the headline emoji
(🔥 for high composite, 🟡 for medium).

## Precision-over-recall stance

The system optimizes for **precision over recall** by design. The binding
constraint in signal-based selling is rep trust — one false-positive alert
costs more than one missed opportunity. The well-documented industry failure
mode is signal noise: rep ignores Slack → rep misses real signals → CRO
deprecates the tool. Tight thresholds, dedup before alerting, and watchlist
overflow are all defenses against that failure mode.

Concretely:
- Alert threshold 70 (not 50). Watchlist (50–69) preserves recall without
  burning rep attention.
- Within-run dedup collapses 5 Maryland PDAB bills × 1 NASHP model → 1 alert
  with `+4 related bills` footer rather than 5 duplicate alerts.
- Company-grouped output: one message per company per run, not one per signal.
- Confidence band displayed so the rep can decide how hard to lean in.

## Known limits

- **No cross-run dedup.** Same `(signal_type, company, anchor)` re-fires on
  every cron tick until the evidence ages out of its lookback window.
- **Static weights.** No feedback loop adjusts weights based on which alerts
  led to meetings. Day-one weights are reasoned, not learned.
- **No rep-level personalization.** Single Slack channel; per-AE routing
  requires CRM mapping.
- **Threshold gaming.** The 70 cutoff can produce sharp on/off behavior
  near the threshold. A signal at 71 looks identical in routing to one at 95.

## v2 evolution path

1. **Cross-run dedup** with 7-day TTL keyed on
   `(signal_type, company_cik, evidence_anchor)` — prevents same alert firing
   on every cron tick.
2. **Rep feedback weights.** Slack reactions `:fire:` / `:zzz:` write to
   `data/feedback.jsonl`; a weekly job re-fits the per-signal weights to
   maximize meeting-attributed alerts.
3. **Soft alert band.** Replace the hard 70 threshold with a calibrated
   probability output ("78% likely to convert to a meeting"); rep sees the
   probability, not a 0–100 number.
4. **Per-AE routing.** Salesforce CRM mapping owner → company; alerts route
   to the AE who owns the account, not a shared channel.
5. **Drift monitoring.** Track per-signal alert volume + per-signal "fired
   but no rep action" rate; alert internally when a signal type is silently
   degrading.
