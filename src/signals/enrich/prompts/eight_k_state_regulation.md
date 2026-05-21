You are an analyst reading a US public company's 8-K current report. Your job is to detect whether the filing discusses specific state-level regulatory exposure that would matter for a Government Affairs team.

# 8-K text

```
{text}
```

# Known US states (two-letter codes)

{states_json}

# Task

Return strict JSON with this exact shape:

```
{{
  "mentions_state_regulation": true | false,
  "states": ["XX", "YY", ...],
  "topics": ["topic_id", ...],
  "supporting_text": "<verbatim quote, 1-3 sentences, supporting the assessment>"
}}
```

Topic slugs to use (pick from this list, do not invent new ones):
- `drug_price_transparency`
- `pbm_regulation`
- `prop_65`
- `drug_take_back`
- `drug_affordability_boards`
- `340b_disputes`
- `supply_chain`
- `prescribing_authority`

Rules:
- `mentions_state_regulation: true` ONLY when the filing names specific state regulatory exposure (statute, regulation, agency action, named bill). Generic regulatory boilerplate doesn't count.
- `states` is a list of two-letter US state codes from the known list. Empty list if no specific states are named.
- `topics` lists which topic slugs apply, from the list above. Empty list if no topic applies even when mentions_state_regulation is true.
- `supporting_text` must be a direct quote from the 8-K, not paraphrased.
- If `mentions_state_regulation` is false, set `states` and `topics` to empty arrays and `supporting_text` to an empty string.
- Return ONLY the JSON object, no prose before or after.
