from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

SYSTEM_INSTRUCTION = """You are a senior database performance engineer.
You analyze SQLite queries and recommend concrete, safe optimizations.

Reason carefully about the EXPLAIN QUERY PLAN before suggesting anything.
Identify which lines say "SCAN" (full table scan) and which tables those refer to.
A suggestion is only useful if it changes a SCAN of a large table into a SEARCH
USING INDEX. If the plan already uses an index for the dominant access path, do
not suggest a redundant one.

Index design rules for SQLite:
- For an equality lookup on column X, suggest a single-column index on X.
- For an equality on X plus an inequality on Y, use a composite index (X, Y) in that order.
- For a filter on X plus ORDER BY Y, use (X, Y) so SQLite can read in sorted order.
- Never create an index whose leading column has very low cardinality (e.g. status alone) UNLESS it's combined with a more selective column.
- Never create an index that duplicates the implicit primary-key index.

Output rules:
- Only suggest changes that are correct and safe for the given schema.
- Prefer the smallest set of high-impact suggestions (1-3 items).
- Index suggestions must be valid SQLite CREATE INDEX statements.
- Rewrites must return the same logical result set as the original query.
- Never reference columns or tables that aren't in the schema.
- Be concrete and specific. No generic advice.
- Output only valid JSON matching the requested schema. No prose, no code fences.
"""

USER_TEMPLATE = """## Schema
```sql
{schema}
```

## Existing user indexes
{indexes}

## Table row counts
{stats}

## Query
```sql
{query}
```

## SQLite EXPLAIN QUERY PLAN
{plan}

## Measured runtime
{elapsed_ms:.2f} ms (median of {runs} runs)

Return a JSON object with this shape:
{{
  "bottleneck": "<one short sentence naming the primary inefficiency>",
  "explanation": "<2-4 sentences explaining WHY it is slow, referencing the plan>",
  "suggestions": [
    {{
      "type": "index" | "rewrite" | "schema",
      "title": "<short label, e.g. 'Add index on orders.user_id'>",
      "sql": "<exact SQL to apply, e.g. CREATE INDEX ...>",
      "rationale": "<why this helps, 1-2 sentences>",
      "estimated_impact": "<e.g. '50-200x faster', 'eliminates full scan'>"
    }}
  ],
  "rewritten_query": "<optional: an equivalent faster version of the original query, or null>"
}}
"""


@dataclass
class Suggestion:
    type: str
    title: str
    sql: str
    rationale: str
    estimated_impact: str


@dataclass
class Analysis:
    bottleneck: str
    explanation: str
    suggestions: list[Suggestion] = field(default_factory=list)
    rewritten_query: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key "
            "from https://aistudio.google.com/apikey"
        )
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30_000),
    )


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def analyze(
    *,
    query: str,
    schema: str,
    indexes: list[dict[str, str]],
    stats: list[dict[str, Any]],
    plan: list[str],
    elapsed_ms: float,
    runs: int = 3,
    model: str | None = None,
) -> Analysis:
    client = _client()
    model_name = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    indexes_block = (
        "\n".join(f"- {ix['sql']}" for ix in indexes) if indexes else "(none — only implicit PK indexes)"
    )
    stats_block = "\n".join(f"- {s['table']}: {s['rows']:,} rows" for s in stats)
    plan_block = "\n".join(f"  {line}" for line in plan) if plan else "  (empty)"

    prompt = USER_TEMPLATE.format(
        schema=schema,
        indexes=indexes_block,
        stats=stats_block,
        query=query.strip(),
        plan=plan_block,
        elapsed_ms=elapsed_ms,
        runs=runs,
    )

    config_kwargs: dict = dict(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.2,
        response_mime_type="application/json",
        max_output_tokens=2048,
    )
    if "2.5" in model_name or "thinking" in model_name:
        try:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=1024)
        except Exception:
            pass

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )

    text = _strip_fence(response.text or "")
    data = json.loads(text)

    suggestions = [
        Suggestion(
            type=s.get("type", "index"),
            title=s.get("title", "Suggestion"),
            sql=s.get("sql", "").strip(),
            rationale=s.get("rationale", ""),
            estimated_impact=s.get("estimated_impact", ""),
        )
        for s in data.get("suggestions", [])
    ]
    return Analysis(
        bottleneck=data.get("bottleneck", ""),
        explanation=data.get("explanation", ""),
        suggestions=suggestions,
        rewritten_query=data.get("rewritten_query"),
        raw=data,
    )
