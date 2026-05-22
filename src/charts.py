"""
charts.py
---------
Render chart spec → Streamlit visualization.

Chart spec schema (από answer_writer):
{
  "type": "metric" | "bar" | "hbar" | "line" | "pie" | "table",
  "title": str,
  "x": str | None,
  "y": str | list[str] | None,
  "color": str | None,
  "agg": None | "sum" | "avg" | "count",
  "value_format": None | "number" | "currency_eur" | "percent"
}
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st


_VALID_TYPES = {"metric", "bar", "hbar", "line", "pie", "table"}


def _format_axis_tickformat(value_format: str | None) -> str | None:
    if value_format == "currency_eur":
        return "€,.0f"
    if value_format == "percent":
        return ".1f"
    if value_format == "number":
        return ",d"
    return None


def _format_scalar(value: Any, value_format: str | None) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value_format == "currency_eur":
        return f"€{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if value_format == "percent":
        return f"{v:,.1f}%".replace(",", "X").replace(".", ",").replace("X", ".")
    if v == int(v):
        return f"{int(v):,}".replace(",", ".")
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def render(spec: dict | None, rows: list[dict]) -> bool:
    """Returns True αν render-άρισε κάτι, False αλλιώς."""
    if not spec or not rows:
        return False
    t = spec.get("type")
    if t not in _VALID_TYPES:
        return False

    title = spec.get("title") or ""
    vfmt = spec.get("value_format")

    try:
        if t == "metric":
            # 1 row, 1 numeric → big number
            row = rows[0]
            y = spec.get("y")
            val = row.get(y) if y else next(iter(row.values()), None)
            st.metric(label=title or "Value", value=_format_scalar(val, vfmt))
            return True

        df = pd.DataFrame(rows)
        if df.empty:
            return False

        if t == "table":
            if title:
                st.markdown(f"**{title}**")
            st.dataframe(df, use_container_width=True, hide_index=True)
            return True

        x = spec.get("x")
        y = spec.get("y")
        color = spec.get("color")

        if t == "bar":
            fig = px.bar(df, x=x, y=y, color=color, title=title, text_auto=True)
        elif t == "hbar":
            # Horizontal: swap x↔y, διατάξιμο από μικρό σε μεγάλο
            if isinstance(y, str) and y in df.columns and isinstance(x, str) and x in df.columns:
                df_sorted = df.sort_values(by=x, ascending=True)
            else:
                df_sorted = df
            fig = px.bar(df_sorted, x=x, y=y, color=color, orientation="h", title=title, text_auto=True)
        elif t == "line":
            fig = px.line(df, x=x, y=y, color=color, title=title, markers=True)
        elif t == "pie":
            fig = px.pie(df, names=x, values=y, title=title, hole=0.3)
        else:
            return False

        # Axis tick format για currency/percent
        tickfmt = _format_axis_tickformat(vfmt)
        if tickfmt and t in ("bar", "line"):
            fig.update_yaxes(tickformat=tickfmt)
        if tickfmt and t == "hbar":
            fig.update_xaxes(tickformat=tickfmt)

        fig.update_layout(
            margin=dict(l=10, r=10, t=50, b=10),
            height=380,
            showlegend=bool(color),
        )
        st.plotly_chart(fig, use_container_width=True)
        return True
    except Exception as e:
        st.warning(f"Δεν μπόρεσα να φτιάξω chart: {e}")
        return False
