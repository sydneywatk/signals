"""Claude LLM extraction for risk-factor topic identification + 8-K state reg detection.

Uses Anthropic tool use to force structured JSON output (text-mode responses
break when free-text fields contain embedded quotes that the model fails to
escape). Tool input is guaranteed to be valid JSON conforming to the schema.

Both functions dispatch through `load_fixture("anthropic", "<scenario>")` in
fixture mode. In live mode they call `claude-sonnet-4-6` with the tool schema.

The prompt templates in `prompts/*.md` still carry the task instructions; the
tool name and schema enforce the output shape.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from signals.fixtures import load_fixture
from signals.settings import ANTHROPIC_API_KEY, USE_LIVE_APIS

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
# Additional prompt directory for distribution-side templates (e.g., openers).
_DISTRIBUTE_PROMPTS_DIR = Path(__file__).parent.parent / "distribute" / "prompts"
MODEL = "claude-sonnet-4-6"
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]

_RISK_FACTORS_TOOL = {
    "name": "record_risk_factor_topics",
    "description": "Record which pharma regulatory topics are flagged in a 10-K Item 1A.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Topic id from the taxonomy"},
                        "supporting_text": {"type": "string",
                                             "description": "Verbatim quote (1-3 sentences) from the input text"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["id", "supporting_text", "confidence"],
                },
            },
        },
        "required": ["topics"],
    },
}

_OPENER_TOOL = {
    "name": "record_opener_variants",
    "description": "Record 3 one-sentence opener variants, each picking a different positioning angle.",
    "input_schema": {
        "type": "object",
        "properties": {
            "openers": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "angle": {"type": "string",
                                   "enum": ["coverage_extension", "pattern_detection",
                                            "bandwidth_briefing", "lobbyist_comparison"]},
                        "text": {"type": "string",
                                  "description": "One sentence (two short max), AE-readable opener."},
                    },
                    "required": ["angle", "text"],
                },
            },
        },
        "required": ["openers"],
    },
}


_EIGHT_K_TOOL = {
    "name": "record_eight_k_state_regulation",
    "description": "Record whether an 8-K mentions specific state regulatory exposure.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mentions_state_regulation": {"type": "boolean"},
            "states": {"type": "array", "items": {"type": "string",
                                                    "description": "Two-letter US state code"}},
            "topics": {"type": "array", "items": {"type": "string"}},
            "supporting_text": {"type": "string",
                                 "description": "Verbatim quote (1-3 sentences); empty if mentions_state_regulation is false"},
        },
        "required": ["mentions_state_regulation", "states", "topics", "supporting_text"],
    },
}

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY must be set for live mode")
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _load_prompt(name: str) -> str:
    """Look up a prompt by name in enrich/prompts/ first, then distribute/prompts/."""
    for base in (PROMPTS_DIR, _DISTRIBUTE_PROMPTS_DIR):
        path = base / f"{name}.md"
        if path.exists():
            return path.read_text()
    raise FileNotFoundError(f"prompt {name!r} not found in {PROMPTS_DIR} or {_DISTRIBUTE_PROMPTS_DIR}")


def _force_tool_call(prompt: str, tool: dict, max_tokens: int = 2500) -> dict[str, Any]:
    """Force Claude to emit the tool call; return tool_input as a dict."""
    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool["name"]:
            return block.input
    raise RuntimeError(f"Claude did not emit expected tool call: {tool['name']}")


def extract_risk_factor_topics(
    risk_factors_text: str,
    taxonomy: list[dict[str, Any]],
    *,
    fixture_scenario: str,
) -> dict[str, Any]:
    """Return {topics: [{id, supporting_text, confidence}, ...]}."""
    if not USE_LIVE_APIS:
        return load_fixture("anthropic", fixture_scenario)

    prompt = _load_prompt("risk_factor_topics").format(
        text=risk_factors_text[:80_000],
        topics_json=json.dumps(
            [{"id": t["id"], "label": t["label"], "description": t["description"]}
             for t in taxonomy], indent=2),
    )
    result = _force_tool_call(prompt, _RISK_FACTORS_TOOL)
    logger.info("extract_risk_factor_topics: %d topics detected", len(result.get("topics", [])))
    return result


def generate_opener_variants(
    *,
    company_name: str,
    signal_type: str,
    signal_label: str,
    top_signal_title: str,
    why_now: str,
    evidence: dict[str, Any],
    num_signals: int,
) -> list[dict[str, str]]:
    """Three opener variants positioning State Affairs as additive coverage / pattern
    / bandwidth (not informational). Falls back to a code template if Anthropic
    isn't configured, so the fixtures-first contract holds.
    """
    if not ANTHROPIC_API_KEY:
        return _fallback_openers(signal_type=signal_type, signal_label=signal_label,
                                   company_name=company_name, evidence=evidence)

    # Trim evidence dict to readable size for the prompt
    evidence_trimmed = {k: v for k, v in evidence.items() if k != "related_suppressed"}
    prompt = _load_prompt("suggested_opener").format(
        company_name=company_name,
        signal_type=signal_type,
        signal_label=signal_label,
        top_signal_title=top_signal_title,
        why_now=why_now,
        evidence_json=json.dumps(evidence_trimmed, indent=2, default=str)[:4000],
        num_signals=num_signals,
    )
    try:
        result = _force_tool_call(prompt, _OPENER_TOOL, max_tokens=1500)
    except Exception as exc:
        logger.warning("Opener generation failed; using fallback: %s", exc)
        return _fallback_openers(signal_type=signal_type, signal_label=signal_label,
                                   company_name=company_name, evidence=evidence)
    return result.get("openers", [])


def _fallback_openers(*, signal_type: str, signal_label: str, company_name: str,
                      evidence: dict) -> list[dict[str, str]]:
    """Code-template fallback when Anthropic isn't configured. Tries to match the
    positioning tone of the prompt template at low fidelity."""
    co = company_name
    if signal_type == "A":
        states = evidence.get("states", [])
        n = len(evidence.get("bills", []))
        return [
            {"angle": "coverage_extension",
             "text": f"You're probably already tracking the {evidence.get('topic_label', 'topic')} bill in "
                      f"{(states or [''])[0]} — the same language just landed in {len(states)-1} other states "
                      f"this week. Which of those is currently uncovered for {co}?"},
            {"angle": "pattern_detection",
             "text": f"{n} substantively identical bills hit {len(states)} states inside two weeks — "
                      f"that's a coordinated wave. Want to see who's funding it before your board does?"},
            {"angle": "bandwidth_briefing",
             "text": f"If your CEO asked you tomorrow morning what {co}'s exposure is to "
                      f"{evidence.get('topic_label', 'this topic')} across all 50 states, we built the answer."},
        ]
    if signal_type == "C":
        states = evidence.get("states", [])
        return [
            {"angle": "bandwidth_briefing",
             "text": f"You just told the SEC about {co}'s {(states or [''])[0]} exposure publicly — "
                      f"want a one-pager on the matching active bills your IR team is about to get asked about?"},
            {"angle": "pattern_detection",
             "text": f"Your 8-K named {len(states)} states by statute — we're tracking how this topic moves "
                      f"in peer-company filings to spot the next wave."},
            {"angle": "lobbyist_comparison",
             "text": f"Your contract lobbyist in {(states or [''])[0]} should be flagging this — "
                      f"we're an objective second source on whether they are."},
        ]
    if signal_type == "D3":
        mb = evidence.get("model_bill_title", "the model bill")
        prior = evidence.get("prior_states", [])
        bill_state = evidence.get("matched_bill", {}).get("jurisdiction", "")
        return [
            {"angle": "pattern_detection",
             "text": f"You almost certainly track {mb} — what I want to show you is which 2-3 states are next "
                      f"after {bill_state}, based on the cross-state pattern."},
            {"angle": "coverage_extension",
             "text": f"{mb} is now in {len(prior)+1} states. Which of those is on your active coverage list, "
                      f"and which are you relying on contract counsel to flag?"},
            {"angle": "bandwidth_briefing",
             "text": f"How long does it take your team to assemble the full state-by-state status of {mb} for "
                      f"the CEO right now? We do it in real time."},
        ]
    if signal_type == "E4":
        gov = evidence.get("governor", "the governor")
        rate = int((evidence.get("sign_rate") or 0) * 100)
        bill_state = evidence.get("bill", {}).get("jurisdiction", "")
        return [
            {"angle": "pattern_detection",
             "text": f"You know {bill_state} {evidence.get('bill', {}).get('identifier', '')} dropped — the part you "
                      f"may not have time to verify is that {gov} has signed {rate}% of similar bills this term."},
            {"angle": "bandwidth_briefing",
             "text": "Forward-pass probability per governor per topic is a 4-hour analysis per state. "
                      "We compute it across all 50 in real time."},
            {"angle": "lobbyist_comparison",
             "text": f"Your contract lobbyist in {bill_state} is probably already telling you this is going to pass — "
                      f"we'll tell you whether the data agrees."},
        ]
    return [
        {"angle": "pattern_detection",
         "text": f"{co} has {evidence.get('num_signals', 'multiple')} things hitting at once — "
                  f"want a 15-min walkthrough of the cross-state landscape?"},
    ]


def extract_8k_state_regulation(
    eight_k_text: str,
    *,
    fixture_scenario: str,
) -> dict[str, Any]:
    """Return {mentions_state_regulation, states, topics, supporting_text}."""
    if not USE_LIVE_APIS:
        return load_fixture("anthropic", fixture_scenario)

    prompt = _load_prompt("eight_k_state_regulation").format(
        text=eight_k_text[:30_000],
        states_json=json.dumps(US_STATES),
    )
    result = _force_tool_call(prompt, _EIGHT_K_TOOL, max_tokens=1500)
    logger.info("extract_8k_state_regulation: mentions=%s states=%s topics=%s",
                result.get("mentions_state_regulation"),
                result.get("states"), result.get("topics"))
    return result

