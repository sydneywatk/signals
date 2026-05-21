"""Per-account brief markdown generator.

For each Slack alert, we also emit a long-form markdown brief with the full
evidence trail: all firing signals, supporting quotes, bill links, lobbying
details, score breakdowns, and a few opener variants. The Slack message
itself stays short; the brief is the deep-dive an AE clicks into before a
call.

Briefs are written to `briefs/<cik>_<YYYY-MM-DD>.md`. The Slack alert links
to the GitHub blob URL for that path. v1 limitation: the brief file has to
be committed to the repo for the link to work. Production write-back is a
v2 item (either commit briefs via a write-permission workflow, or host them
from Modal at `/brief/<cik>`).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from signals.detectors import Signal
from signals.score.scoring import ScoreBreakdown
from signals.settings import REPO_ROOT

logger = logging.getLogger(__name__)

BRIEFS_DIR: Path = REPO_ROOT / "briefs"
GITHUB_BLOB_BASE = "https://github.com/sydneywatk/signals/blob/main/briefs"

# Mirror of slack.SIGNAL_LABEL — keep in sync.
_SIGNAL_LABEL = {
    "A":  "Multistate Convergence",
    "C":  "Public Risk Disclosure",
    "D3": "Model Bill Spread",
    "E4": "Governor Track Record",
}


def brief_filename(company_cik: str, when: date | None = None) -> str:
    when = when or date.today()
    return f"{company_cik or 'unknown'}_{when.isoformat()}.md"


def brief_url(company_cik: str, when: date | None = None) -> str:
    return f"{GITHUB_BLOB_BASE}/{brief_filename(company_cik, when)}"


def write_brief(
    *,
    company_cik: str,
    company_name: str,
    composite_score: int,
    signals: list[tuple[Signal, ScoreBreakdown]],
    suggested_openers: list[str],
) -> Path:
    """Write the brief markdown to disk and return the path."""
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / brief_filename(company_cik)
    path.write_text(_render_brief(
        company_cik=company_cik,
        company_name=company_name,
        composite_score=composite_score,
        signals=signals,
        suggested_openers=suggested_openers,
    ))
    logger.info("Wrote brief for %s -> %s", company_name, path)
    return path


def _render_brief(
    *,
    company_cik: str,
    company_name: str,
    composite_score: int,
    signals: list[tuple[Signal, ScoreBreakdown]],
    suggested_openers: list[str],
) -> str:
    top_sig, top_score = signals[0]
    when = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {company_name} — Account Score {composite_score}",
        "",
        f"_Generated {when} · CIK {company_cik} · {len(signals)} signal"
        f"{'s' if len(signals) != 1 else ''} firing_",
        "",
        "## Suggested opener variants",
        "",
    ]
    for i, opener in enumerate(suggested_openers, 1):
        lines.append(f"{i}. {opener}")
    lines.extend(["", "---", ""])

    # Top signal — full detail
    lines.extend([
        f"## Lead signal: {_SIGNAL_LABEL.get(top_sig.signal_type, top_sig.signal_type)}"
        f" ({top_score.total} / 100)",
        "",
        f"**{top_sig.title}**",
        "",
        top_sig.why_now,
        "",
    ])
    lines.extend(_evidence_block(top_sig))

    # Other signals
    if len(signals) > 1:
        lines.extend(["", "## Supporting signals", ""])
        for sig, score in signals[1:]:
            lines.append(
                f"### {_SIGNAL_LABEL.get(sig.signal_type, sig.signal_type)}"
                f" ({score.total} / 100)")
            lines.append("")
            lines.append(f"**{sig.title}**")
            lines.append("")
            lines.append(sig.why_now)
            lines.append("")
            lines.extend(_evidence_block(sig))
            lines.append("")

    # Per-signal score breakdown (collapsed)
    lines.extend(["", "## Score breakdown", "", "<details><summary>Click to expand</summary>", ""])
    for sig, score in signals:
        label = _SIGNAL_LABEL.get(sig.signal_type, sig.signal_type)
        lines.append(f"**{label} ({score.total} / 100)** — confidence: {score.confidence}")
        lines.append("")
        for k, v in score.components.items():
            lines.append(f"- `{k}`: {v:.1f}")
        lines.append("")
    lines.append("</details>")

    # Account context footer
    lines.extend([
        "",
        "---",
        "",
        "## Account context",
        "",
        f"- **Company:** {company_name}",
        f"- **CIK:** {company_cik}",
        f"- **EDGAR 10-K history:** https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={company_cik.zfill(10) if company_cik.isdigit() else company_cik}&type=10-K",
        "",
        "_Reference: [docs/SIGNALS_BUSINESS.md](../docs/SIGNALS_BUSINESS.md) · "
        "[docs/SCORING.md](../docs/SCORING.md) · "
        "[docs/DECISION_MEMO.md](../docs/DECISION_MEMO.md)_",
        "",
    ])

    return "\n".join(lines)


def _evidence_block(sig: Signal) -> list[str]:
    """Per-signal evidence rendered as markdown."""
    ev = sig.evidence
    lines: list[str] = []
    if sig.signal_type == "A":
        lines.append(f"- **Topic:** {ev.get('topic_label')}")
        lines.append(f"- **States in cluster:** {', '.join(ev.get('states', []))}")
        bills = ev.get("bills", [])
        if bills:
            lines.append(f"- **Bills ({len(bills)}):**")
            for b in bills:
                url = b.get("openstates_url") or ""
                lines.append(
                    f"    - [{b['jurisdiction']} {b['identifier']}]({url}) — {b.get('title', '')[:90]}")
        lda = ev.get("lda_filing", {})
        if lda:
            attribution = ev.get("lda_attribution", "named")
            tag = "" if attribution == "named" else " _(ambient — no pharma-credible actor in v1 LDA filter)_"
            lines.append(
                f"- **LDA filing{tag}:** [{lda.get('registrant')} → {lda.get('client')}]"
                f"({lda.get('url') or ''}) · posted {(lda.get('dt_posted') or '')[:10]}")

    elif sig.signal_type == "C":
        f = ev.get("filing", {})
        synthetic = " ⚠️ _(synthetic demo fixture)_" if f.get("is_synthetic_demo") else ""
        lines.append(f"- **Filing:** 8-K {f.get('accession')} · items {f.get('items')}{synthetic}")
        if f.get("url"):
            lines.append(f"- **8-K URL:** [{f['url']}]({f['url']})")
        lines.append(f"- **States named:** {', '.join(ev.get('states', []))}")
        lines.append(f"- **Topics:** {', '.join(ev.get('topics', []))}")
        if ev.get("supporting_text"):
            quote = ev["supporting_text"].strip()
            lines.append("- **Supporting quote:**")
            lines.append("")
            lines.append(f"  > {quote}")
        bills = ev.get("active_bills", [])
        if bills:
            lines.append(f"- **Active matching bills ({len(bills)}):**")
            for b in bills:
                url = b.get("openstates_url") or ""
                lines.append(
                    f"    - [{b['jurisdiction']} {b['identifier']}]({url}) — {b.get('title', '')[:90]}")

    elif sig.signal_type == "D3":
        lines.append(f"- **Model bill:** {ev.get('model_bill_title')} ({ev.get('model_bill_source')})")
        lines.append(f"- **Topic:** {ev.get('model_bill_topic')}")
        mb = ev.get("matched_bill", {})
        url = mb.get("openstates_url") or ""
        lines.append(f"- **State bill:** [{mb.get('jurisdiction')} {mb.get('identifier')}]({url}) — {mb.get('title', '')[:90]}")
        lines.append(f"- **Cosine similarity:** {ev.get('similarity')}")
        lines.append(f"- **Prior states ({len(ev.get('prior_states', []))}):** "
                     f"{', '.join(ev.get('prior_states', []))}")
        related = ev.get("related_suppressed", [])
        if related:
            lines.append(f"- **+ Related bills (suppressed by dedup):** {', '.join(related)}")

    elif sig.signal_type == "E4":
        b = ev.get("bill", {})
        url = b.get("openstates_url") or ""
        lines.append(f"- **Bill:** [{b.get('jurisdiction')} {b.get('identifier')}]({url}) — {b.get('title', '')[:90]}")
        lines.append(f"- **Governor:** {ev.get('governor')} ({ev.get('governor_party')}) · term-start {ev.get('term_start')}")
        lines.append(
            f"- **Signing track record:** {ev.get('sign_count')} signed / "
            f"{ev.get('veto_count')} vetoed of {ev.get('total_acted_on')} acted-on bills "
            f"({int((ev.get('sign_rate') or 0) * 100)}% sign rate)")
        lines.append(f"- **Topic:** {ev.get('topic_label')}")
        lines.append(f"- **Similar sample size:** {ev.get('similar_sample_size')}")

    return lines
