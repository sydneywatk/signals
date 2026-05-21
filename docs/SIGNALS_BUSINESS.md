# Signals — Business Brief

For AE + CRO consumption. What each signal detects, why it predicts a buying
moment for our mid-market pharma ICP, and what to do when it fires.

## The picture

Corporate pharma government-affairs teams (3–8 people per company) cover 50
states' regulatory exposure with very limited bandwidth. They feel pain
acutely in four moments: (1) a coordinated multistate campaign lands, (2)
their own filings expose a state regulatory risk publicly, (3) a model bill
they've been tracking propagates to a new state, (4) a bill they care about
lands on a governor's desk who has a strong history of signing similar bills.

These four moments are the four signals.

## Signal A — Coordinated multistate legislative wave

**What it detects:** 3+ near-identical bills introduced across 3+ states in a
14-day window, on a topic the target company's 10-K already flags as a
material risk, with a fresh federal lobbying registration on the same issue.

**Why it predicts a buying moment:** Coordinated multistate campaigns are the
highest-pain moment for a corporate GA team. The 10-K disclosure means
leadership already considers this material. The LDA activity names a known
actor. The signal tells the AE that the company's GA team is mobilizing this
week.

**Live example:** Pfizer · Account Score 94 · Coordinated multistate bills in
3 states on Drug Affordability Boards. Five bills (CO SB 140, ME LD 697, ME LD
1829, MD HB 424, MD SB 357) on Prescription Drug Affordability Board scope;
Pfizer's 2026 10-K Item 1A flagged drug affordability boards as material risk
verbatim; new LDA registration on healthcare reform 1 day ago.

**AE action:** Call this week, not next. Reference the specific bills by
identifier so they know you've done the work.

## Signal C — SEC 8-K material event + active state bill match

**What it detects:** The target company files an 8-K (items 7.01 Reg FD,
8.01 Other Events, 1.05 Cyber, or 2.05 Restructuring) that names a specific
state regulatory exposure, while a bill on that exact topic is active in the
named state.

**Why it predicts a buying moment:** The company just publicly told its
investors this matters. The GA team is mobilizing now to respond — typically
in the 3–10 day window after the 8-K hits. Our call beats their internal
scramble by days.

**Live example:** Pfizer 8-K naming CA SB 17, OR HB 4005, WA SB 5532 PDAB —
maps to active matching bills. *(Note: this example is a clearly labeled
synthetic fixture in v1 because real pharma 8-K bodies almost never mention
state regs; the real content lives in attached press releases. v2 will read
those exhibits.)*

**AE action:** "Saw your 8-K. Wanted to flag what's moving in [state] this
week before your team has to triage it."

## Signal D3 — Model legislation propagation velocity

**What it detects:** A newly introduced state bill substantively matches a
known model bill (ALEC, NASHP) that has already propagated to 3+ prior states.

**Why it predicts a buying moment:** Model bills with adoption momentum are
the strongest "this is going to spread further" signal. Corporate GA teams
care intensely about propagation because that's how single-state issues
become multistate fires. Naming the model bill in the opener positions you
as the analyst they wish they had on staff.

**Live example:** Pfizer · D3 (84) · NASHP Prescription Drug Affordability
Board Act spreading; Colorado SB 140 now in the 10th state. Prior states: MD,
CO, WA, OR, ME, MN, NJ, NH, OH.

**AE action:** "The NASHP PDAB model just showed up in [state] — that's the
10th state. Your peers in [adjacent state] are scrambling on this." Offer a
30-min walkthrough of the current state-by-state landscape.

## Signal E4 — Governor signing track record predictor

**What it detects:** A bill on an ICP-relevant topic introduced in a state
where the governor has signed >70% of substantively similar bills since
taking office. Flags as "high-probability passage" lead.

**Why it predicts a buying moment:** Most state legislative activity dies
quietly. Signal E4 separates "will pass" from "will die" by quantifying
the governor's actual signing behavior on topic-similar bills during the
current term. Asymmetric by party + state; a Newsom in CA signs PBM bills,
an Abbott in TX doesn't.

**AE action:** "[State] just dropped [bill]. The governor has signed [N]%
of similar bills this term — this one is on a fast path. Worth a call
before it moves out of committee."

## How alerts arrive

One Slack message per company per run. Header: account score (max signal
score + 5 per additional firing, capped at 100) and number of signals firing.
Lead signal's narrative is the body. Other firing signals listed as supporting
evidence. Each alert includes a one-sentence **Suggested Opener** you can
literally read on a call, plus action buttons linking to the source bill or
filing.

The system optimizes for **precision over recall**: alert threshold is 70 of
100; medium-confidence signals (50–69) drop into a watchlist file rather than
ping Slack. The binding constraint is rep trust — one bad alert costs more
than one missed good signal.
