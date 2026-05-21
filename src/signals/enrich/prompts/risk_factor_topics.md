You are an analyst reading a US public company's 10-K Item 1A Risk Factors section. Your job is to identify which pharma regulatory topics from a known taxonomy are explicitly flagged in the text.

# Topic taxonomy

The following topics define the regulatory categories of interest. Each has an `id` (the slug to use in your output), a `label`, and a `description` clarifying scope:

{topics_json}

# Risk Factors text

```
{text}
```

# Task

Return strict JSON with this exact shape:

```
{{
  "topics": [
    {{
      "id": "<topic_id_from_taxonomy>",
      "supporting_text": "<verbatim quote, 1-3 sentences, that establishes the topic is flagged>",
      "confidence": "high" | "medium" | "low"
    }},
    ...
  ]
}}
```

Rules:
- Only include topics that are explicitly flagged as material risks in the text. Do not infer.
- `id` MUST be one of the taxonomy slugs above. Do not invent new ids.
- `supporting_text` must be a direct quote from the input, not paraphrased.
- `confidence: high` when a named statute or precise topic phrase appears; `medium` when the concept is clearly discussed without a named statute; `low` for tangential mentions.
- If no topics apply, return `{{"topics": []}}`.
- Return ONLY the JSON object, no prose before or after.
