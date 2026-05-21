"""Slack distribution — Block Kit alerts or stdout dry-run.

If `SLACK_WEBHOOK_URL` is set, POST the alert payload to the webhook. Otherwise
format the same content and print to stdout. Same code path either way; the
fixture-first contract means a reviewer with no Slack credentials still sees
the alerts.
"""
from __future__ import annotations

import logging
import sys
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
