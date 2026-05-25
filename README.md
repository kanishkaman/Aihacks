# QueryDoctor

Paste a slow SQL query, get a faster one.

QueryDoctor runs your query against a real SQLite database, times it, hands the EXPLAIN plan to Gemini, and gets back a concrete fix — an index, a rewrite, or both. One click applies it and re-runs the query so you can see how much faster it actually got.

Built for the AI Hacks hackathon, problem statement #5.

## Run it

```bash
pip install -r requirements.txt
export GEMINI_API_KEY='your-key-from-aistudio.google.com/apikey'
streamlit run app.py
```

First launch seeds a ~12 MB e-commerce database (10k users, 100k orders, 260k order items, 1k products). Subsequent launches just open.

## What it does

- Runs your query against the local SQLite DB and takes the median of three runs
- Pulls the EXPLAIN QUERY PLAN, table sizes, and existing indexes
- Sends all of that to Gemini 2.5 Flash with a tight system prompt
- Parses the response into structured suggestions (each with rationale + expected impact)
- One click applies the suggestion, re-runs the query, and shows the new runtime, the plan diff, and a ballpark savings estimate

Because the model gets the actual schema and indexes, suggestions are grounded in the real database — not generic SQL tips you'd find in a blog post.

## Presets

Six queries in the dropdown, each picked to show a different optimization pattern:

1. **Customer order history** — basic full scan, single-column index fix
2. **Top US customers by revenue** — multi-table join needing a composite index
3. **Recent pending orders** — compound filter + sort that benefits from a covering index
4. **Best-selling products** — aggregate over 260k rows, join-side index
5. **Dormant users** — `NOT IN` anti-pattern; the model rewrites it as a `LEFT JOIN`
6. **Suspicious large orders** — correlated subquery

Preset 1 gives the cleanest before/after for a demo (~100× faster). Preset 5 is the most interesting because the model rewrites the query rather than just adding an index.

## Files

| Path | What's in it |
|---|---|
| `app.py` | Streamlit UI |
| `optimizer/db.py` | Connection, EXPLAIN, benchmarking, index DDL |
| `optimizer/analyzer.py` | Gemini client + structured JSON parsing |
| `optimizer/sample_data.py` | Seeds the demo database |
| `diagnose.py` | Latency / connectivity check for the Gemini API |

## Config

| Env var | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | required | Free-tier key from Google AI Studio |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Thinking is disabled for sub-3s responses |

If the **Analyze** button hangs, run `python diagnose.py` — it lists which models your key can actually call and prints the round-trip time for each.

## Things I'd add next

- Postgres support, so we get real `EXPLAIN ANALYZE` cost numbers instead of SQLite's plan-only output
- Persisted history of past fixes, so you can see which indexes have been suggested across queries
- A pre-commit hook that lints SQL in migration files

## License

MIT
