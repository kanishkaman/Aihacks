from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sample_data import DB_PATH, ensure_database


@dataclass
class BenchmarkResult:
    rows: list[tuple]
    columns: list[str]
    elapsed_ms: float
    explain_plan: list[str]
    raw_explain: list[tuple]


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    ensure_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def schema_summary(conn: sqlite3.Connection) -> str:
    cur = conn.cursor()
    cur.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type DESC, tbl_name, name"
    )
    chunks: list[str] = []
    for row in cur.fetchall():
        sql = row["sql"]
        if sql:
            chunks.append(sql.strip() + ";")
    return "\n".join(chunks)


def table_stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    out = []
    for (name,) in cur.fetchall():
        cur.execute(f"SELECT COUNT(*) FROM {name}")
        out.append({"table": name, "rows": cur.fetchone()[0]})
    return out


def list_user_indexes(conn: sqlite3.Connection) -> list[dict[str, str]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
        "ORDER BY tbl_name, name"
    )
    return [{"name": r[0], "table": r[1], "sql": r[2]} for r in cur.fetchall()]


def explain_plan(conn: sqlite3.Connection, query: str) -> tuple[list[str], list[tuple]]:
    cur = conn.cursor()
    cur.execute(f"EXPLAIN QUERY PLAN {query}")
    rows = cur.fetchall()
    raw = [tuple(r) for r in rows]
    formatted: list[str] = []
    for r in rows:
        # (id, parent, notused, detail)
        detail = r[3]
        depth = 0
        parent = r[1]
        seen = {r[0]: 0}
        while parent and parent in seen:
            depth = seen[parent] + 1
            break
        formatted.append("  " * depth + detail)
    return formatted, raw


def benchmark(conn: sqlite3.Connection, query: str, runs: int = 3, limit: int = 200) -> BenchmarkResult:
    plan, raw = explain_plan(conn, query)

    timings: list[float] = []
    rows: list[tuple] = []
    columns: list[str] = []
    for i in range(runs):
        cur = conn.cursor()
        t0 = time.perf_counter()
        cur.execute(query)
        fetched = cur.fetchall()
        elapsed = (time.perf_counter() - t0) * 1000
        timings.append(elapsed)
        if i == runs - 1:
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [tuple(r) for r in fetched[:limit]]

    timings.sort()
    median = timings[len(timings) // 2]
    return BenchmarkResult(rows=rows, columns=columns, elapsed_ms=median, explain_plan=plan, raw_explain=raw)


def apply_statement(conn: sqlite3.Connection, sql: str) -> None:
    cur = conn.cursor()
    cur.executescript(sql)
    conn.commit()
    cur.execute("ANALYZE")
    conn.commit()


def drop_user_indexes(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    )
    names = [r[0] for r in cur.fetchall()]
    for name in names:
        cur.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    return len(names)
