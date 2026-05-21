"""Slack distribution — Block Kit alerts or stdout dry-run.

Two alert shapes:
- Per-signal alert (`post_alert`) — one Slack message per individual signal.
  Used for tests and one-off posts.
- Per-account alert (`post_account_alert`) — one Slack message per company
  per run, consolidating all that company's firing signals into a single
  message. This is what `main.py` uses in production.

If `SLACK_WEBHOOK_URL` is set, POST the payload to the webhook. Otherwise
format the same content and print to stdout.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

from signals.detectors import Signal
from signals.score.scoring import ScoreBreakdown
from signals.settings import SLACK_WEBHOOK_URL

logger = logging.getLogger(__name__)

_CONFIDENCE_EMOJI = {"high": "🔥", "medium": "🟡", "low": "⚪"}


def render_alert_text(signal: Signal, score: ScoreBreakdown) -> str:
    """Plain-text fallback for stdout dry-run + Slack notification text."""
    parts = [
        f"{_CONFIDENCE_EMOJI[score.confidence]} Score {score.total} — {signal.company_name}",
        f"  ({signal.signal_type} | confidence: {score.confidence})",
        f"  {signal.title}",
        "",
        f"Why now: {signal.why_now}",
        "",
    ]
    parts.extend(_render_key_facts(signal))
    parts.append("")
    parts.append("Score breakdown:")
    for k, v in score.components.items():
        parts.append(f"  - {k}: {v:.1f}")
    parts.append("")
    parts.extend(_render_action_links(signal))
    return "\n".join(parts)


def build_block_kit(signal: Signal, score: ScoreBreakdown) -> dict[str, Any]:
    """Slack Block Kit payload."""
    emoji = _CONFIDENCE_EMOJI[score.confidence]
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"{emoji} Score {score.total} — {signal.company_name}"[:150]},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn",
                 "text": f"*Signal {signal.signal_type}* · confidence: *{score.confidence}* · _{signal.title}_"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why now:* {signal.why_now}"},
        },
    ]
    facts = _render_key_facts(signal)
    if facts:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "```" + "\n".join(facts) + "```"},
        })
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "*Score breakdown:* " +
             " · ".join(f"{k}={v:.1f}" for k, v in score.components.items())},
        ],
    })
    actions = _action_buttons(signal)
    if actions:
        blocks.append({"type": "actions", "elements": actions})

    return {"text": render_alert_text(signal, score)[:600], "blocks": blocks}


def post_alert(signal: Signal, score: ScoreBreakdown) -> bool:
    """Send to Slack if webhook configured; otherwise print to stdout."""
    if not SLACK_WEBHOOK_URL:
        text = render_alert_text(signal, score)
        print("=" * 78, file=sys.stdout)
        print(text, file=sys.stdout)
        print("=" * 78, file=sys.stdout, flush=True)
        logger.info("Slack dry-run: alert %s printed to stdout", signal.key())
        return True

    payload = build_block_kit(signal, score)
    try:
        resp = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Slack webhook POST failed for %s: %s", signal.key(), exc)
        return False
    logger.info("Slack alert posted: %s -> %s", signal.key(), resp.status_code)
    return True


def _render_key_facts(signal: Signal) -> list[str]:
    ev = signal.evidence
    rows: list[str] = []
    if signal.signal_type == "A":
        rows.append(f"Topic           {ev.get('topic_label')}")
        rows.append(f"States          {', '.join(ev.get('states', []))}")
        bills = ev.get("bills", [])[:5]
        rows.append(f"Bills           {', '.join(b['identifier'] for b in bills)}")
        lda = ev.get("lda_filing", {})
        if lda:
            rows.append(f"Lobbying        {lda.get('registrant')} -> {lda.get('client')} "
                        f"(posted {(lda.get('dt_posted') or '')[:10]})")
    elif signal.signal_type == "C":
        f = ev.get("filing", {})
        rows.append(f"Filing          8-K {f.get('accession')} items={f.get('items')}")
        rows.append(f"States          {', '.join(ev.get('states', []))}")
        rows.append(f"Topics          {', '.join(ev.get('topics', []))}")
        rows.append(f"Active bills    {', '.join(b['identifier'] for b in ev.get('active_bills', [])[:4])}")
        if f.get("is_synthetic_demo"):
            rows.append("Note            ** SYNTHETIC FIXTURE — see DECISION_MEMO **")
    elif signal.signal_type == "D3":
        rows.append(f"Model bill      {ev.get('model_bill_title')} ({ev.get('model_bill_source')})")
        mb = ev.get("matched_bill", {})
        rows.append(f"State bill      [{mb.get('jurisdiction')}] {mb.get('identifier')} {mb.get('title')}")
        rows.append(f"Similarity      {ev.get('similarity')}")
        rows.append(f"Prior states    {', '.join(ev.get('prior_states', []))}")
    related = ev.get("related_suppressed")
    if related:
        if len(related) <= 4:
            rows.append(f"+ Related       {', '.join(related)}")
        else:
            shown = ', '.join(related[:4])
            rows.append(f"+ Related       {shown}, +{len(related) - 4} more")
    return rows


def _render_action_links(signal: Signal) -> list[str]:
    lines = []
    ev = signal.evidence
    if signal.signal_type == "A":
        for b in ev.get("bills", [])[:3]:
            if b.get("openstates_url"):
                lines.append(f"  → {b['jurisdiction']} {b['identifier']}: {b['openstates_url']}")
        if ev.get("lda_filing", {}).get("url"):
            lines.append(f"  → LDA filing: {ev['lda_filing']['url']}")
    elif signal.signal_type == "C":
        if ev.get("filing", {}).get("url"):
            lines.append(f"  → 8-K: {ev['filing']['url']}")
        for b in ev.get("active_bills", [])[:3]:
            if b.get("openstates_url"):
                lines.append(f"  → {b['jurisdiction']} {b['identifier']}: {b['openstates_url']}")
    elif signal.signal_type == "D3":
        mb = ev.get("matched_bill", {})
        if mb.get("openstates_url"):
            lines.append(f"  → {mb['jurisdiction']} {mb['identifier']}: {mb['openstates_url']}")
    return lines


def _action_buttons(signal: Signal) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    ev = signal.evidence
    if signal.signal_type == "A":
        for b in ev.get("bills", [])[:3]:
            if b.get("openstates_url"):
                buttons.append(_btn(f"{b['jurisdiction'][:2]} {b['identifier']}", b["openstates_url"]))
    elif signal.signal_type == "C":
        if ev.get("filing", {}).get("url"):
            buttons.append(_btn("View 8-K", ev["filing"]["url"]))
        for b in ev.get("active_bills", [])[:2]:
            if b.get("openstates_url"):
                buttons.append(_btn(f"{b['jurisdiction'][:2]} {b['identifier']}", b["openstates_url"]))
    elif signal.signal_type == "D3":
        mb = ev.get("matched_bill", {})
        if mb.get("openstates_url"):
            buttons.append(_btn(f"View {mb['identifier']}", mb["openstates_url"]))
    return buttons


def _btn(label: str, url: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label[:75]},
        "url": url,
    }


def _suggested_opener(account: "AccountAlert") -> str:
    """One sentence the AE could read on a discovery call.

    Templated per top signal so the language is concrete and fact-grounded —
    every clause cites a specific bill, filing, or signing rate from the alert
    evidence. No generic hand-waving.
    """
    top_sig, _ = account.top
    ev = top_sig.evidence
    co = account.company_name

    if top_sig.signal_type == "A":
        topic_label = ev.get("topic_label", "the topic")
        states = ev.get("states", [])
        state_phrase = ", ".join(states[:3]) + (
            f" and {len(states) - 3} other" if len(states) > 3 else "")
        return (f"{co}'s 10-K just flagged {topic_label} as a material risk, and "
                f"the same bill is now active in {state_phrase} — wanted to make "
                f"sure your team had a heads-up before the next committee vote.")

    if top_sig.signal_type == "C":
        states = ev.get("states", [])
        topics = ev.get("topics", [])
        topic_phrase = topics[0].replace("_", " ") if topics else "state regulation"
        state_phrase = "/".join(states[:3])
        return (f"{co} just filed an 8-K naming {state_phrase} {topic_phrase} as a "
                f"specific exposure — saw active matching bills and wanted to flag "
                f"it before your team has to scramble.")

    if top_sig.signal_type == "D3":
        model_title = ev.get("model_bill_title", "a model bill")
        prior_states = ev.get("prior_states", [])
        bill_state = ev.get("matched_bill", {}).get("jurisdiction", "a target state")
        return (f"The {model_title} just showed up in {bill_state} — that's "
                f"{len(prior_states) + 1} states now. With {co}'s 10-K exposure "
                f"on this topic, you're going to want this on the radar before "
                f"two more states follow.")

    if top_sig.signal_type == "E4":
        gov = ev.get("governor", "the governor")
        rate = int((ev.get("sign_rate") or 0) * 100)
        bill_state = ev.get("bill", {}).get("jurisdiction", "")
        bill_id = ev.get("bill", {}).get("identifier", "")
        return (f"{bill_state} just introduced {bill_id} — {gov} has signed "
                f"{rate}% of similar bills this term, so this one is on a fast "
                f"path. {co}'s 10-K already flags this as material.")

    return f"{co} has {account.num_signals} signals firing right now — worth a call this week."


# ---------------------------------------------------------------------------
# Account-level alerts: one Slack message per company per run.
# ---------------------------------------------------------------------------


@dataclass
class AccountAlert:
    company_cik: str
    company_name: str
    # Ordered by score desc — signals[0] is the top-scoring signal for this account.
    signals: list[tuple[Signal, ScoreBreakdown]] = field(default_factory=list)
    composite_score: int = 0

    @property
    def num_signals(self) -> int:
        return len(self.signals)

    @property
    def top(self) -> tuple[Signal, ScoreBreakdown]:
        return self.signals[0]


def aggregate_by_company(
    scored: list[tuple[Signal, ScoreBreakdown]],
) -> list[AccountAlert]:
    """Group scored signals by company; compute composite score.

    Composite formula: max(signal_scores) + 5 * (num_signals - 1), capped at 100.
    Rationale: a single strong signal floors the score; each additional firing
    signal adds 5 points (so 2 signals at 80 ranks above 1 signal at 84). Cap
    prevents runaway from a noisy day.
    """
    by_cik: dict[str, list[tuple[Signal, ScoreBreakdown]]] = {}
    for pair in scored:
        by_cik.setdefault(pair[0].company_cik, []).append(pair)

    accounts: list[AccountAlert] = []
    for cik, sigs in by_cik.items():
        if not sigs:
            continue
        sigs.sort(key=lambda x: -x[1].total)
        top_score = sigs[0][1].total
        composite = min(100, top_score + 5 * (len(sigs) - 1))
        accounts.append(AccountAlert(
            company_cik=cik,
            company_name=sigs[0][0].company_name,
            signals=sigs,
            composite_score=composite,
        ))
    accounts.sort(key=lambda a: -a.composite_score)
    return accounts


def _composite_emoji(composite: int) -> str:
    if composite >= 80:
        return "🔥"
    if composite >= 50:
        return "🟡"
    return "⚪"


def render_account_alert(account: AccountAlert) -> str:
    top_sig, top_score = account.top
    emoji = _composite_emoji(account.composite_score)
    parts = [
        f"{emoji} Account Score {account.composite_score} — {account.company_name}",
        f"  {account.num_signals} signal{'s' if account.num_signals != 1 else ''} firing"
        f" · top: Signal {top_sig.signal_type} ({top_score.total})",
        "",
        f"Suggested opener: \"{_suggested_opener(account)}\"",
        "",
        f"Top signal: {top_sig.title}",
        f"Why now: {top_sig.why_now}",
        "",
    ]
    parts.extend(_render_key_facts(top_sig))

    if account.num_signals > 1:
        parts.append("")
        parts.append("Other firing signals:")
        for sig, score in account.signals[1:]:
            parts.append(f"  • Signal {sig.signal_type} ({score.total}): {sig.title}")
            short = sig.why_now[:160] + ("…" if len(sig.why_now) > 160 else "")
            parts.append(f"    {short}")

    parts.append("")
    parts.append("Per-signal score breakdown:")
    for sig, score in account.signals:
        comps = ", ".join(f"{k}={v:.1f}" for k, v in score.components.items())
        parts.append(f"  Signal {sig.signal_type} total {score.total} — {comps}")

    parts.append("")
    parts.extend(_render_action_links(top_sig))
    return "\n".join(parts)


def build_account_block_kit(account: AccountAlert) -> dict[str, Any]:
    top_sig, top_score = account.top
    emoji = _composite_emoji(account.composite_score)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": f"{emoji} Account Score {account.composite_score} — "
                             f"{account.company_name}"[:150]},
        },
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"*{account.num_signals} signal"
                         f"{'s' if account.num_signals != 1 else ''} firing* "
                         f"· top: Signal *{top_sig.signal_type}* "
                         f"({top_score.total}) · _{top_sig.title}_",
            }],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*Suggested opener:*\n> {_suggested_opener(account)}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why now:* {top_sig.why_now}"},
        },
    ]

    facts = _render_key_facts(top_sig)
    if facts:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "```" + "\n".join(facts) + "```"},
        })

    if account.num_signals > 1:
        lines = ["*Other firing signals:*"]
        for sig, score in account.signals[1:]:
            lines.append(f"• *Signal {sig.signal_type}* ({score.total}): {sig.title}")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

    breakdown_segments = []
    for sig, score in account.signals:
        comps = ", ".join(f"{k}={v:.1f}" for k, v in score.components.items())
        breakdown_segments.append(f"_{sig.signal_type} ({score.total})_ {comps}")
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": ("*Score breakdown:* " + " · ".join(breakdown_segments))[:2900],
        }],
    })

    buttons = _action_buttons(top_sig)
    if buttons:
        blocks.append({"type": "actions", "elements": buttons[:5]})

    return {"text": render_account_alert(account)[:600], "blocks": blocks}


def post_account_alert(account: AccountAlert) -> bool:
    if not SLACK_WEBHOOK_URL:
        text = render_account_alert(account)
        print("=" * 78, file=sys.stdout)
        print(text, file=sys.stdout)
        print("=" * 78, file=sys.stdout, flush=True)
        logger.info("Slack dry-run: account alert %s printed to stdout", account.company_cik)
        return True

    payload = build_account_block_kit(account)
    try:
        resp = httpx.post(SLACK_WEBHOOK_URL, json=payload, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Slack webhook POST failed for account %s: %s",
                     account.company_cik, exc)
        return False
    logger.info("Account alert posted: %s (%d signals, composite %d) -> %s",
                account.company_name, account.num_signals, account.composite_score,
                resp.status_code)
    return True
