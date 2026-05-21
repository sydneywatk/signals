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
    return (PROMPTS_DIR / f"{name}.md").read_text()


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

