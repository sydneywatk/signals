# Brief Verification Audit — 2026-05-21

Target: `briefs/78003_2026-05-21.md` (Pfizer · Account Score 100 · 6 signals).

## Summary

- **7 checks run**
- **2 ✅ verified · 2 ⚠️ qualified · 3 ❌ broken**
- **Lead alert (Pfizer / CA SB 40 / NASHP Insulin):** ❌ broken — bill was chaptered into law on **2025-10-13** (Chapter 737, Statutes of 2025); the brief's "California now 13th state" framing is ~7 months stale.

### Action items before Loom

1. **Do not lead with CA SB 40.** It's not a current alert — the bill is already law. Either pull it from the demo or reframe the alert as "California *enacted* the 13th NASHP-aligned insulin law" (historical, not predictive).
2. **Pull the Signal E4 / OR HB 4040 alert entirely** — three independent defects: (a) the bill matched itself in the historical bag (sim 1.000), (b) the bill is hospital presumptive-eligibility / home-health-agency content, not DAB, and (c) it was chaptered into law 2026-04-07. The "100% sign rate (3/3 similar bills)" claim does not survive scrutiny.
3. **Surface a v1 LDA limitation note in Loom narration** even though the brief already says "ambient" — the LDA card under Signal A still names Ephraim McDowell Health, which an evaluator will Google in 5 seconds.
4. **Acknowledge propagation lists are state-only (no bill IDs).** When asked "where is the data for CO's insulin law", the answer is "it's a hand-curated state list, not a per-bill reference; v2 captures bill IDs."
5. **Caveat the two medium-confidence 10-K topic matches** (`pbm_regulation`, `340b_disputes`). The supporting quotes are technically accurate but read as tangential — don't over-claim "Pfizer flagged PBM as material risk" in the Loom; the language is about industry consolidation, not state PBM regulation specifically.

---

## Check 1: California SB 40 substance

**Finding:** SB 40 *is* substantively an insulin coverage bill (subject tags include `insulin`; abstract discusses Knox-Keene insulin coverage requirements; the bill amends large-group health plan contracts to require insulin coverage). Cosine match to NASHP model (0.56) is legitimate.

**However:** the bill's latest action is `"Chaptered by Secretary of State. Chapter 737, Statutes of 2025."` on **2025-10-13**. The bill has been California law for ~7 months. The alert narrative says "California introduced SB 40 — California now 13th state" — this framing implies a current legislative event. It is not.

The session is `20252026` (current), so the bill is in the recent_bills fixture; but its *status* is enacted, not pending.

**Verdict:** ❌ **Broken** — substantively valid bill, but not a current alert. The detector should filter on bills that are still moving (introduced, in committee, passed-one-chamber) — not bills that are already law. v2 fix: add a `bill_status` filter that excludes `chaptered` / `enacted` / `became-law` actions older than 30 days.

---

## Check 2: 12 prior states for "NASHP Insulin Affordability Act"

**Structural finding (raised at the top of the audit):** `config/model_bills.yml` lists `known_propagation` as a flat state-code list — no bill IDs. So I cannot verify "the cited bill in state X" because no bill is cited in the corpus.

**What I could verify:** ran cross-state OpenStates search for `"insulin"` across the 12 claimed states (CO, NM, IL, VA, ME, WA, OR, CT, MN, DE, KY, NH). Top-5 sorted by `first_action_desc`. Results:

| State | Substantive insulin bill in OpenStates (current sessions) | Notes |
|---|---|---|
| CO | Not in current top-5 (CO HB20-1335 from 2020 is the on-the-books law; not retrieved by current-session search) | Likely historical — needs deeper search to confirm |
| NM | None substantive in top-5 (HB 264 income tax deductions, HB 224 medical expenses) | Tangential |
| IL | None substantive (HB 5775 diapers, SB 4056 hospice — all 13 hits mention "insulin" in tangential line items) | Tangential |
| VA | ✓ **HB 1214 — Health insurance; cost-sharing payments for insulin and diabetes equipment** | Real |
| ME | None substantive (housing/childcare bills returned) | Likely historical |
| WA | None substantive (state budgets) | Likely historical |
| OR | HB 4040 (already audit-broken in Check 4) + HB 4119 workers' comp | Neither is substantive insulin |
| CT | None substantive (Medicaid review, study commissions) | Tangential |
| MN | ✓ **SF 4138 — Definition for covered insulin for the insulin safety net program and manufactur[er...]** | Real |
| DE | None substantive (Title 18 insurance amendments) | Tangential |
| KY | HB 729 pharmaceutical drug safety (broad) | Tangential |
| NH | None substantive (naturopathy, prosthetics, biomarker testing) | Off-topic |

This doesn't *prove* the propagation claim is wrong — most states' insulin laws were enacted 2019–2023 and aren't returned by a current-session search. The states most plausibly already have insulin affordability laws on the books (CO HB20-1335, IL Public Act 101-625, ME LD 1496, etc. — all real public records). But:

**Verdict:** ⚠️ **Qualified** — the historical propagation claim is *plausibly* defensible at the state level, but cannot be verified at the bill level without bill IDs in the corpus. **Demo risk:** an evaluator asks "show me the IL bill" and we can't point to a specific one. **Loom mitigation:** acknowledge upfront that propagation lists are hand-curated state lists in v1; v2 stores `(state, bill_id, year_enacted)` triples.

---

## Check 3: Three Kotek bills for Governor Track Record

**Finding:** Replicated the detector logic against `tests/fixtures/openstates/historical_bills_or.json` + the candidate OR HB 4040. The three "similar bills above 0.65" are:

| # | Bill | Outcome | Sim | Topic classification |
|---|---|---|---|---|
| 1 | **OR HB 4040 itself** | signed 2026-04-07 | **1.000** | drug_affordability_boards, prescribing_authority |
| 2 | OR HB 4070 — *behavioral health treatment in the medical assistance program* | signed 2026-03-31 | 0.708 | (none — no topic match in code) |
| 3 | OR HB 4039 — *transparent and data-driven process for developing capitation rates for coordinated care organizations* | signed 2026-03-31 | 0.699 | (none) |

The candidate bill matched itself at cosine 1.000 (it's present in *both* the recent_bills fixture and the historical_bills_or fixture). The other two are Oregon Medicaid managed-care bills — completely unrelated to Drug Affordability Boards.

**Real sample size: 0.** The "100% sign rate, 3/3" claim does not hold. The signal shouldn't fire.

**Verdict:** ❌ **Broken** — three independent defects:
1. **Code bug:** detector does not exclude the candidate bill from the historical comparison bag (self-match at cosine 1.000).
2. **Topic-classification noise:** HB 4070 and HB 4039 reach the 0.65 cosine threshold via shared "health care" / "Oregon Health Authority" vocabulary, not topic substance. Voyage/MiniLM is matching surface bureaucratic language.
3. The OR HB 4040 candidate itself isn't a DAB bill (see Check 4).

---

## Check 4: Oregon HB 4040 substance + status

**Finding:** HB 4040's abstract is:

> "Modifies the requirements for screening a hospital patient for presumptive eligibility for financial assistance. [Prohibits the Oregon Health Authority from requiring certain home health agencies to comply with Medicare conditions of participation.] [Modifies the requirements for how the D..."

This is **hospital financial-assistance and home-health-agency regulation**, not Drug Affordability Board content. The topic classifier mapped it to `drug_affordability_boards` because the bill text shares vocabulary with the topic keywords (probably "health care", "financial assistance"). False positive.

Status: **Chaptered into law 2026-04-07, Chapter 109 (2026 Laws), Effective April 7, 2026.** The brief calls it "likely to pass" — already passed.

**Verdict:** ❌ **Broken** — wrong topic + already enacted. The "Governor Track Record" signal should not be firing on this bill.

---

## Check 5: Pfizer 10-K Item 1A topic matches

**Finding:** Cached extraction at `tests/fixtures/anthropic/risk_factor_topics_78003.json` returns 5 topics for Pfizer. Each maps to a verbatim quote from the 10-K. Quality per claim:

| Claimed topic | Confidence | Supporting quote excerpt | Assessment |
|---|---|---|---|
| `drug_affordability_boards` | high | "Measures to regulate prices… including legislation on drug importation, international reference pricing and **prescription drug affordability boards (PDABs) that seek to impose reimbursement limits for certain drugs**, could adversely affect our business." | ✅ Verbatim, specific, named statute concept. Strong. |
| `drug_price_transparency` | medium | "States have continued to focus on addressing drug costs, **generally by increasing price transparency** or attempting to limit drug price increases for state-regulated insurance." | ✅ Real but generic — Pfizer doesn't name a specific transparency statute. Defensible. |
| `pbm_regulation` | medium | "We expect that **consolidation and integration among pharmacy chains, wholesalers and PBMs will increase pricing pressures in the industry**." | ⚠️ Quote is about industry consolidation, NOT state PBM regulation. The match is the word "PBMs" appearing in any context. Loose. |
| `prescribing_authority` | high | "Such payors are also increasingly imposing **utilization management tools requiring prior authorization** for a branded product or requiring the patient to first fail on one or more other products before permitting access to a particular branded medicine." | ✅ Real verbatim discussion of utilization management and step therapy. Strong. |
| `340b_disputes` | medium | "Any additional reduction of U.S. federal spending on entitlement programs beyond the IRA, including Medicare, Medicaid, or any other publicly funded or subsidized health programs, and the **340B Program**, may affect payment for our products" | ⚠️ Quote names 340B as part of a federal entitlements list. NOT specifically about state 340B disputes. Loose. |

The two "medium" matches (`pbm_regulation`, `340b_disputes`) are technically present in Pfizer's 10-K but match generic industry-context sentences rather than specific risk-factor framing on the topic.

**Verdict:** ⚠️ **Qualified** — the high-confidence matches (`drug_affordability_boards`, `prescribing_authority`) are bulletproof. The two medium matches are over-claimed: don't say in the Loom that "Pfizer's 10-K flagged PBM regulation as material risk" — Pfizer's 10-K mentions PBMs in the context of pharmacy-chain consolidation pricing dynamics, not state PBM regulation specifically. Same for 340B.

---

## Check 6: Three Maine bills

| Bill | Session | First action | Latest action | Topic substance | Status |
|---|---|---|---|---|---|
| **LD 697** | 132 (current) | 2025-02-20 | 2026-01-11 | Direct PDAB to assess strategies + implement reference-based pricing | ✅ Current + substantive PDAB |
| **LD 1829** | **131 (prior)** | 2023-04-27 | 2024-11-20 | Reference-based pricing requirement | ⚠️ Bill is from the 2023-2024 session, latest action Nov 2024 — **not in current Maine legislative session** |
| **LD 1580** | 132 (current) | 2025-04-10 | 2025-06-12 | Prohibit PBM fees + pricing | ✅ Current + substantive PBM |

**Verdict:** ⚠️ **Qualified** — LD 697 and LD 1580 are legitimate current Maine bills on the topics claimed. LD 1829 is from session 131 with no action since November 2024 — likely carried over but effectively stale. The Voyage-similarity matcher pulled it in because the title still scores high for "prescription drug" + "pricing," but operationally a Maine GA team has moved on. Either drop LD 1829 from the brief or note that it's from the prior session.

---

## Check 7: Cosine sanity — CA SB 40 vs NASHP Insulin model

**Reproduced cosine:** 0.5621 (brief claimed 0.562 — matches).

**Substantive token overlap:** `cost`, `formulary`, `health`, `insulin`, `plans`, `prescription`, `program`, `sharing`, `state`, `tier`.

**Boilerplate-only tokens in SB 40:** `bill`, `chapter`, `subsection`, `1975` (Knox-Keene reference), `2027`, `crime`, `delivered`, etc. The state-bill-specific legal vocabulary doesn't dominate.

**Verdict:** ✅ **Verified** — cosine 0.56 is earned by real conceptual overlap (insulin, cost-sharing, formulary tier, prescription coverage). Not boilerplate. The match itself is defensible *as a topical match.* The problem with the alert is NOT the cosine — it's that the bill is already enacted (Check 1).

---

## Recommended changes before Loom

### Must fix (broken claims)

- **Drop CA SB 40 from the lead position** OR explicitly reframe as "California enacted its NASHP-aligned insulin law in October 2025 — the 13th state on the curve." This is honest. The current "California now 13th state" framing implies new movement.
- **Drop Signal E4 / OR HB 4040 alert entirely.** Three independent defects (self-match bug, wrong topic, already enacted). The "Tina Kotek 3/3 sign rate" cannot survive 5 seconds of evaluator scrutiny.
- **Drop or qualify Maine LD 1829** (from prior session, no action since Nov 2024).

### Should qualify in Loom

- Acknowledge model-bill propagation lists are hand-curated state-only sets in v1 — bill-level provenance is v2.
- When walking through Pfizer's 10-K topic matches, lead with `drug_affordability_boards` and `prescribing_authority` (the high-confidence matches). Don't claim "Pfizer flagged PBM as material risk" — Pfizer's PBM mention is about pharmacy-chain consolidation, not state PBM regulation.
- The LDA card under the Signal A alert references Ephraim McDowell Health (a small KY community hospital). The narrative correctly says "ambient — no pharma-credible actor in v1 LDA filter," but an evaluator will Google the registrant. Either soften the card further or pull it.

### Already documented in DECISION_MEMO (cite during Loom)

- Static model corpus (no bill IDs)
- Synthetic 8-K fixture for Signal C demo
- TF-IDF→sentence-transformers fallback because Voyage rate-limited
- No cross-run dedup
- LDA filter limitation on actor credibility

### v2 follow-ups exposed by this audit

- **`bill_status` filter** — exclude bills already enacted (became-law / chaptered) older than 30 days from being treated as current alerts.
- **Self-exclusion in Signal E4 historical bag** — don't compute similarity between a candidate bill and itself.
- **Tighter topic-classifier guardrails** — HB 4040 should not classify to `drug_affordability_boards` based on shared health-care vocabulary alone.
- **Bill IDs in `model_bills.yml` propagation lists** — `known_propagation: [{state: "CO", bill_id: "HB20-1335", year: 2020}, ...]` so the propagation claim is verifiable per-bill.
- **Session-current filter** — exclude bills from prior legislative sessions with no current-session action.
