# Final Pre-Loom Check — 2026-05-21

## Decision

**Action taken:** None yet — surfacing to Sydney. Both candidate leads (SB 140 and HB 1056) are PDAB/PBM bills that (a) died in committee and (b) are industry-favorable. Per spec stop conditions, escalating before any swap or filter change.

## Check A: Colorado SB 140 substance

**What the bill does (substantively):** SB 140 ("Exempt Drugs from Prescription Drug Affordability Board Reviews") amends Colorado's existing PDAB framework to **strip the board's authority** to perform affordability reviews or set upper payment limits on two drug categories: (1) drugs designated as treatments for a rare disease or condition by the FDA, and (2) licensed biological products derived from human whole blood or plasma. The full text of the abstract is unambiguous: *"the Colorado prescription drug affordability review board has no authority to perform an affordability review of, or to establish an upper payment limit for, a prescription drug that is [rare-disease-designated or human-blood-derived]."*

**Industry posture:** **Pharma-favorable / PDAB-narrowing.** This is a manufacturer-protective bill — it carves out two valuable drug categories (orphan / rare disease drugs, and plasma-derived biologics) from PDAB price-control authority. Pharma trade associations actively support this kind of carve-out; manufacturers of rare-disease and blood-derived products specifically lobby in favor. The buying-moment thesis breaks: Pfizer's State Government Affairs team is **not mobilizing against this bill** — they're supporting it.

**Status:** **Dead.** Latest action 2026-04-22: *"House Committee on Health & Human Services Postpone Indefinitely."* The bill passed the Senate on 2026-04-13 but was killed in the House committee 9 days later. The v1 status filter (which catches "chaptered" / "became law") doesn't catch "Postpone Indefinitely" — but it should.

**Posture:** favorable.

## Check B: CO in known_propagation

**CO IS in `known_propagation`** for NASHP PDAB Act: `['MD', 'CO', 'WA', 'OR', 'ME', 'MN', 'NJ', 'NH', 'OH']`. So the brief's narrative — *"Colorado now 10th state"* and *"This model has already propagated to 9 states: MD, CO, WA, OR, ME, MN, NJ, NH, OH"* — has a logic bug: CO appears in both the count of "prior 9 states" AND is being framed as the new (10th) state. The double-count.

**Bug:** the detector's `len(prior_states) + 1` math assumes the candidate bill's state isn't already in `known_propagation`. It is. So CO is being counted twice.

**Fix path (v2):** in `signal_d.py`, compute `effective_prior = [s for s in prior_states if s != candidate_state_code]` before deriving the propagation count and narrative. Documented as v2 per spec — not implementing tonight.

## Check C: PBM signal validation (HB 1056)

**Substance:** HB 1056 ("Prescription Drug Benefit Information Transparency") creates the *"Prescription Drug Optimized Sourcing Transparency and Integrity Act"*. It (a) prohibits PBMs and health-care consultants from making false/misleading statements to self-insured employers about lawful pharmacy stewardship programs, and (b) requires PBMs to provide cost information on each prescription drug dispensed when a self-insured employer asks. The bill is a **PBM transparency / accountability** instrument.

**Industry posture for Pfizer:** **Pharma-favorable.** PBM transparency bills are widely supported by pharma manufacturers because PBM rebate opacity is the mechanism through which PBMs extract margin from manufacturer list prices. Pfizer and the broader pharma industry (via PhRMA) typically lobby *in favor* of PBM transparency legislation; PBMs (via PCMA) lobby against. This is the inverse of the PDAB case in terms of opponents, but the same conclusion: Pfizer's GA team is not fighting HB 1056, they're supporting it.

**Status:** **Dead.** Latest action 2026-02-17: *"House Committee on Health & Human Services Postpone Indefinitely."* Bill died in committee three months ago, before it ever made it to the floor. Sole sponsor (K. DeGraaf) is a single Republican legislator with no Democratic co-sponsorship — bill was DOA.

**Propagation claim (8 prior states for ALEC-aligned PBM Oversight Model):** Not deeply spot-checked because the bill is dead — propagation accuracy is moot once the candidate bill is irrelevant.

**Holds up as alternate lead:** **No.** HB 1056 has the same two problems as SB 140 — industry-favorable AND already dead.

## Bigger picture

Both of the two "audit-bulletproof" alerts after the 6 fixes turn out to be:

1. **Industry-favorable** (pharma supports the bill, not opposes) — invalidates the buying-moment thesis
2. **Postponed-indefinitely / dead** (months ago) — the v1 status filter doesn't catch this kill action

This is the same kind of "topic classifier insufficient to assess industry posture" problem the spec flagged as a stop condition. The v1 detectors classify bills topically (PDAB-related, PBM-related) but not directionally (expands manufacturer constraint vs. narrows it). Without that directional signal, *any* bill on the topic — including the manufacturer-protective ones — fires.

If I apply the obvious fixes:
- Add "Postpone Indefinitely" to the `_ENACTED_MARKERS` list → both bills filtered out
- Result: **0 firing signals for Pfizer.** Per spec stop condition: surface and let Sydney decide.

## Options

**Option 1 — Demo the pipeline against an older fixture snapshot.**
The CO bills used to be alive when the fixture was first captured. Re-capture the OpenStates fixture from an earlier date, or use a manually-curated fixture where the bills haven't been killed yet. Costs honesty; reviewer might notice the dates.

**Option 2 — Pull both signals, run the Loom on the filter behavior + the audit report itself.**
Demo the system's *defenses* (the 6 audit fixes, the status filter, the dedup, the brief generation) using the verification work as the narrative spine. The story becomes "here's what I built, here's how I audited it, here's what I found and fixed, here's why the v1 result is honest about its limitations." This is a strong story for a take-home — it shows engineering judgment over feature-completeness.

**Option 3 — Build a posture filter tonight.**
Add a Claude classification call that asks "does this bill expand or constrain pharmaceutical manufacturer obligations?" Reject favorable bills as non-alerts. Probably 90 minutes of work + new prompt template + cache. Risky on time budget.

**Option 4 — Manually curate one or two known-adverse-and-alive bills into the fixture.**
For example, the brief was previously showing the Maryland HB 424 / SB 357 PDAB-expansion bills before dedup collapsed them. Those are adverse (expand PDAB authority) but their session status would need verification. This is closer in spirit to option 1 but more targeted.

## What I changed
- Nothing. No code, no config, no fixtures, no brief.

## What I did NOT change
- Did not add "Postpone Indefinitely" to the enactment-marker list (would zero out the brief).
- Did not modify the SB 140 / HB 1056 entries.
- Did not modify `known_propagation` to remove CO (per spec, v2 fix).
- Did not run the brief regeneration.

## Recommended path

I lean toward **Option 2** for tonight, with **Option 3 (posture filter)** as the post-Loom roadmap item.

The audit + this final check together tell a clean engineering story: you built the pipeline, you audited it against authoritative sources, you found three categories of v1 failure modes (stale-enacted bills, self-match bugs, missing posture classification), you shipped fixes for the first two before submission, and you documented the third as the highest-priority v2 work because honest sales tooling can't claim "buying moment" on a bill the buyer's company supports.

That's a more defensible Loom than "here are two alerts" when both alerts have substantive problems.

Standing by for direction.
