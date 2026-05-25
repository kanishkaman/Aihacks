from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import sqlparse
import streamlit as st
from dotenv import load_dotenv

from optimizer import db as dbmod
from optimizer.analyzer import Analysis, analyze
from optimizer.sample_data import reset_database

load_dotenv()

st.set_page_config(
    page_title="QueryDoctor",
    page_icon="◆",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 2.2rem; max-width: 1180px; }
      h1 { font-weight: 700; letter-spacing: -0.02em; }
      h5 { color: #cbd5e1; font-weight: 600; margin-top: 0.4rem; }
      .runtime-label { font-size: 0.78rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.08em; }
      .runtime-big   { font-size: 2.4rem; font-weight: 700; line-height: 1.05; margin-top: 4px; }
      .stButton > button { border-radius: 8px; font-weight: 500; }
      div[data-testid="stMetric"] { background: rgba(139, 92, 246, 0.05); border: 1px solid rgba(139,92,246,0.15); border-radius: 10px; padding: 10px 14px; }
      div[data-testid="stMetricValue"] { font-size: 1.4rem; }
      section[data-testid="stSidebar"] h3 { font-size: 0.82rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; margin-top: 1.2rem; }
      code { font-size: 0.85rem; }
      .stDivider { margin: 1.5rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

PLACEHOLDER = "— select a query —"

PRESET_QUERIES: dict[str, str] = {
    PLACEHOLDER: "",
    "Customer order history (single-user lookup)": (
        "SELECT id, status, total, created_at\n"
        "FROM orders\n"
        "WHERE user_id = 4242\n"
        "ORDER BY created_at DESC;"
    ),
    "Top US customers by revenue (multi-table join)": (
        "SELECT u.id, u.name, u.country,\n"
        "       COUNT(o.id) AS orders,\n"
        "       ROUND(SUM(o.total), 2) AS revenue\n"
        "FROM users u\n"
        "JOIN orders o ON o.user_id = u.id\n"
        "WHERE u.country = 'US'\n"
        "  AND o.status IN ('paid','shipped','delivered')\n"
        "GROUP BY u.id\n"
        "ORDER BY revenue DESC\n"
        "LIMIT 10;"
    ),
    "Recent pending orders (compound filter + sort)": (
        "SELECT id, user_id, total, created_at\n"
        "FROM orders\n"
        "WHERE status = 'pending'\n"
        "  AND created_at > '2025-06-01'\n"
        "ORDER BY created_at DESC\n"
        "LIMIT 20;"
    ),
    "Best-selling products (aggregate over 260k rows)": (
        "SELECT p.category, p.name,\n"
        "       SUM(oi.quantity) AS units_sold,\n"
        "       ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue\n"
        "FROM order_items oi\n"
        "JOIN products p ON p.id = oi.product_id\n"
        "GROUP BY p.id\n"
        "ORDER BY units_sold DESC\n"
        "LIMIT 15;"
    ),
    "Dormant users (NOT IN anti-pattern)": (
        "SELECT id, name, email, country, created_at\n"
        "FROM users\n"
        "WHERE id NOT IN (\n"
        "  SELECT user_id FROM orders WHERE status != 'cancelled'\n"
        ")\n"
        "ORDER BY created_at DESC\n"
        "LIMIT 50;"
    ),
    "Suspicious large orders (correlated subquery)": (
        "SELECT id, user_id, total, status, created_at\n"
        "FROM orders\n"
        "WHERE total > (SELECT AVG(total) * 10 FROM orders)\n"
        "  AND status = 'pending'\n"
        "ORDER BY total DESC\n"
        "LIMIT 20;"
    ),
}


def _format_sql(sql: str) -> str:
    return sqlparse.format(sql, reindent=True, keyword_case="upper")


def _init_state() -> None:
    defaults = {
        "query": "",
        "baseline": None,
        "optimized": None,
        "analysis": None,
        "applied_sql": [],
        "preset_select": PLACEHOLDER,
        "qpm": 100,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def _on_preset_change() -> None:
    label = st.session_state.preset_select
    if label and label != PLACEHOLDER:
        st.session_state.query = PRESET_QUERIES[label]
        st.session_state.baseline = None
        st.session_state.optimized = None
        st.session_state.analysis = None
        st.session_state.applied_sql = []


def _render_plan(plan: list[str]) -> None:
    st.code("\n".join(plan) if plan else "(empty)", language="text")


def _render_runtime(label: str, ms: float, color: str) -> None:
    st.markdown(
        f"<div class='runtime-label'>{label}</div>"
        f"<div class='runtime-big' style='color:{color}'>"
        f"{ms:.2f} <span style='font-size:1rem;color:#94a3b8;font-weight:500'>ms</span></div>",
        unsafe_allow_html=True,
    )


def _render_results(label: str, result: dbmod.BenchmarkResult, color: str) -> None:
    _render_runtime(label, result.elapsed_ms, color)
    if result.rows:
        df = pd.DataFrame(result.rows, columns=result.columns)
        st.dataframe(df, use_container_width=True, height=220, hide_index=True)
    else:
        st.caption("0 rows returned.")


def _render_speedup_chart(before_ms: float, after_ms: float) -> None:
    speedup = before_ms / after_ms if after_ms > 0 else float("inf")
    title = f"{speedup:,.1f}× faster" if speedup != float("inf") else "Effectively instant"
    fig = go.Figure(
        data=[
            go.Bar(
                x=["Before", "After"],
                y=[before_ms, after_ms],
                marker_color=["#f43f5e", "#10b981"],
                text=[f"{before_ms:.2f} ms", f"{after_ms:.2f} ms"],
                textposition="outside",
                textfont=dict(size=13, color="#e6edf3"),
                width=0.45,
            )
        ]
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=18, color="#e6edf3")),
        yaxis=dict(title="ms", gridcolor="rgba(255,255,255,0.06)"),
        xaxis=dict(showgrid=False),
        height=300,
        margin=dict(t=60, b=30, l=40, r=20),
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_plan_diff(before: list[str], after: list[str]) -> None:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Before**")
        _render_plan(before)
    with col2:
        st.markdown("**After**")
        _render_plan(after)


def _render_impact(before_ms: float, after_ms: float, qpm: int) -> None:
    runs_per_day = qpm * 60 * 24
    saved_ms = max(before_ms - after_ms, 0)
    saved_sec_per_day = (saved_ms * runs_per_day) / 1000
    saved_min_per_day = saved_sec_per_day / 60
    cpu_hours_per_month = (saved_sec_per_day * 30) / 3600
    monthly_dollars = cpu_hours_per_month * 0.20

    c1, c2, c3 = st.columns(3)
    c1.metric("Saved per request", f"{saved_ms:.2f} ms")
    c2.metric("Saved per day", f"{saved_min_per_day:,.1f} min", help=f"at {qpm} req/min")
    c3.metric("Monthly DB-CPU saving", f"${monthly_dollars:,.2f}", help="$0.20/CPU-hr")


def _render_analysis(analysis: Analysis) -> None:
    st.markdown(f"**Diagnosis.** {analysis.bottleneck}")
    st.markdown(analysis.explanation)
    st.markdown("##### Suggestions")
    for i, sug in enumerate(analysis.suggestions, 1):
        with st.container(border=True):
            cols = st.columns([3, 1])
            with cols[0]:
                st.markdown(f"**{i}. {sug.title}**  ·  _{sug.type}_")
                st.code(_format_sql(sug.sql), language="sql")
                if sug.rationale:
                    st.caption(sug.rationale)
            with cols[1]:
                st.metric("Expected", sug.estimated_impact or "—")


def sidebar() -> None:
    with st.sidebar:
        st.markdown("### Setup")
        env_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        user_key = st.text_input(
            "Gemini API key (optional)",
            value="",
            type="password",
            placeholder="paste your own key to override",
            help="Get one free at aistudio.google.com/apikey",
        )
        if user_key:
            os.environ["GEMINI_API_KEY"] = user_key
            st.success("Using your key")
        elif env_key:
            st.success("Default key configured")
        else:
            st.error(
                "No key configured. Paste one above or set GEMINI_API_KEY before launch."
            )

        conn = dbmod.connect()
        stats = dbmod.table_stats(conn)
        indexes = dbmod.list_user_indexes(conn)

        st.markdown("### Tables")
        st.dataframe(pd.DataFrame(stats), hide_index=True, use_container_width=True)

        st.markdown("### Indexes")
        if indexes:
            for ix in indexes:
                st.code(ix["sql"], language="sql")
        else:
            st.caption("None yet — clean slate.")

        st.markdown("### Load assumption")
        st.slider(
            "Production requests / min",
            min_value=1, max_value=10_000, step=10,
            key="qpm",
            help="Used in the savings estimate.",
        )

        st.markdown("### Actions")
        if st.button("Reset database", use_container_width=True):
            reset_database()
            for k in ("baseline", "optimized", "analysis"):
                st.session_state[k] = None
            st.session_state.applied_sql = []
            st.rerun()

        if indexes and st.button("Drop indexes", use_container_width=True):
            dbmod.drop_user_indexes(conn)
            for k in ("baseline", "optimized", "analysis"):
                st.session_state[k] = None
            st.session_state.applied_sql = []
            st.rerun()

        conn.close()


def main() -> None:
    _init_state()
    sidebar()

    st.title("QueryDoctor")
    st.caption(
        "Benchmark a SQL query, let Gemini read its plan, apply the suggested fix, "
        "see the runtime change."
    )

    st.selectbox(
        "Pick a preset, or paste your own SQL below",
        list(PRESET_QUERIES.keys()),
        key="preset_select",
        on_change=_on_preset_change,
    )

    st.text_area(
        "SQL",
        key="query",
        height=200,
        placeholder="-- SELECT ... from users / orders / order_items / products",
        label_visibility="collapsed",
    )

    c1, c2, c3 = st.columns([1, 1, 1])
    run_clicked = c1.button("Run", use_container_width=True, type="primary")
    analyze_clicked = c2.button("Analyze", use_container_width=True)
    clear_clicked = c3.button("Clear", use_container_width=True)

    if clear_clicked:
        st.session_state.baseline = None
        st.session_state.optimized = None
        st.session_state.analysis = None
        st.session_state.applied_sql = []
        st.rerun()

    if run_clicked:
        if not st.session_state.query.strip():
            st.warning("Enter a query or pick a preset first.")
        else:
            conn = dbmod.connect()
            try:
                with st.spinner("Running…"):
                    st.session_state.baseline = dbmod.benchmark(conn, st.session_state.query)
                    st.session_state.optimized = None
                    st.session_state.analysis = None
            except Exception as e:
                st.error(f"Query failed: {e}")
            finally:
                conn.close()
            st.rerun()

    if analyze_clicked:
        if not st.session_state.baseline:
            st.warning("Run the query first.")
        else:
            conn = dbmod.connect()
            try:
                with st.spinner("Analyzing…"):
                    st.session_state.analysis = analyze(
                        query=st.session_state.query,
                        schema=dbmod.schema_summary(conn),
                        indexes=dbmod.list_user_indexes(conn),
                        stats=dbmod.table_stats(conn),
                        plan=st.session_state.baseline.explain_plan,
                        elapsed_ms=st.session_state.baseline.elapsed_ms,
                    )
            except Exception as e:
                st.error(f"Analysis failed: {e}")
            finally:
                conn.close()
            st.rerun()

    if st.session_state.baseline:
        st.divider()
        left, right = st.columns(2)
        with left:
            st.markdown("##### Baseline")
            _render_results("Median runtime", st.session_state.baseline, "#f43f5e")
        with right:
            st.markdown("##### After fix")
            if st.session_state.optimized:
                _render_results("Median runtime", st.session_state.optimized, "#10b981")
            else:
                st.caption("Apply a suggestion below to see the result here.")

        if st.session_state.optimized:
            _render_speedup_chart(
                st.session_state.baseline.elapsed_ms,
                st.session_state.optimized.elapsed_ms,
            )
            st.markdown("##### Execution plan diff")
            _render_plan_diff(
                st.session_state.baseline.explain_plan,
                st.session_state.optimized.explain_plan,
            )
            st.markdown("##### Estimated impact at scale")
            _render_impact(
                st.session_state.baseline.elapsed_ms,
                st.session_state.optimized.elapsed_ms,
                st.session_state.qpm,
            )

    if st.session_state.analysis:
        st.divider()
        _render_analysis(st.session_state.analysis)

        n_buttons = len(st.session_state.analysis.suggestions) + (
            1 if st.session_state.analysis.rewritten_query else 0
        )
        apply_cols = st.columns(max(n_buttons, 1))

        for i, sug in enumerate(st.session_state.analysis.suggestions):
            if apply_cols[i].button(f"Apply #{i+1}", key=f"apply_{i}", use_container_width=True):
                conn = dbmod.connect()
                try:
                    if sug.type in {"index", "schema"}:
                        dbmod.apply_statement(conn, sug.sql)
                        st.session_state.applied_sql.append(sug.sql)
                    query_to_run = st.session_state.query
                    if sug.type == "rewrite" and sug.sql.lower().lstrip().startswith("select"):
                        query_to_run = sug.sql
                    st.session_state.optimized = dbmod.benchmark(conn, query_to_run)
                except Exception as e:
                    st.error(f"Failed: {e}")
                finally:
                    conn.close()
                st.rerun()

        if st.session_state.analysis.rewritten_query:
            if apply_cols[-1].button("Use rewrite", key="apply_rewrite", use_container_width=True):
                conn = dbmod.connect()
                try:
                    st.session_state.optimized = dbmod.benchmark(
                        conn, st.session_state.analysis.rewritten_query
                    )
                except Exception as e:
                    st.error(f"Rewrite failed: {e}")
                finally:
                    conn.close()
                st.rerun()

            with st.expander("Rewritten query"):
                st.code(_format_sql(st.session_state.analysis.rewritten_query), language="sql")


if __name__ == "__main__":
    main()
