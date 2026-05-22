"""
pages/01_Admin.py
-----------------
Admin page (Streamlit multipage): ιστορικό queries, feedback summary,
mark as golden, manage schema annotations.

Εμφανίζεται αυτόματα στο sidebar του Streamlit όταν υπάρχει `pages/` folder.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.auth import login_gate, logout_button, require_admin
from src.storage import StorageClient

st.set_page_config(page_title="Admin · myTeam AI Agent", page_icon="🛠️", layout="wide")

user = login_gate()
require_admin(user)
logout_button()


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _secret("SUPABASE_SERVICE_KEY")

if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
    st.error("Λείπουν SUPABASE_URL / SUPABASE_SERVICE_KEY στα secrets.")
    st.stop()


@st.cache_resource(show_spinner=False)
def _storage(url: str, key: str) -> StorageClient:
    return StorageClient(url=url, service_key=key)


storage = _storage(SUPABASE_URL, SUPABASE_SERVICE_KEY)

st.title("🛠️ Admin · Query History & Learning")
st.caption(f"Logged in as **{user.name}** ({user.role})")

tab_history, tab_golden, tab_schema = st.tabs(
    ["📜 Query history", "⭐ Golden examples", "📝 Schema annotations"]
)

# ============================================================================
# Tab 1: Query history
# ============================================================================
with tab_history:
    col_f1, col_f2, col_f3 = st.columns([2, 2, 6])
    fb_filter = col_f1.selectbox(
        "Feedback filter",
        options=["all", "thumbs up", "thumbs down", "no feedback"],
        index=0,
    )
    golden_filter = col_f2.checkbox("Only golden", value=False)
    limit = col_f3.slider("Max rows", min_value=10, max_value=500, value=100, step=10)

    fb_map = {"thumbs up": 1, "thumbs down": -1, "no feedback": 0}
    history = storage.list_history(
        limit=limit,
        only_feedback=fb_map.get(fb_filter),
        only_golden=golden_filter,
    )

    if not history:
        st.info("Καμία εγγραφή ακόμα. Κάνε μερικές ερωτήσεις από το main page!")
    else:
        # Summary stats
        total = len(history)
        ups = sum(1 for h in history if h.get("feedback") == 1)
        downs = sum(1 for h in history if h.get("feedback") == -1)
        empties = sum(1 for h in history if (h.get("row_count") or 0) == 0 and not h.get("error"))
        errors = sum(1 for h in history if h.get("error") or h.get("rejected_reason"))
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", total)
        c2.metric("👍 / 👎", f"{ups} / {downs}")
        c3.metric("Empty results", empties)
        c4.metric("Errors", errors)

        st.divider()

        # Detail expander per row
        for h in history:
            badge = (
                "👍" if h.get("feedback") == 1
                else "👎" if h.get("feedback") == -1
                else "❌" if (h.get("error") or h.get("rejected_reason"))
                else "⭐" if h.get("is_golden") else "•"
            )
            label = (
                f"{badge}  {h['created_at'][:19]}  ·  **{h['username']}** ({h['user_role']})  ·  "
                f"_{h['question'][:80]}_"
            )
            with st.expander(label):
                left, right = st.columns([3, 2])
                with left:
                    st.markdown(f"**Question**: {h['question']}")
                    if h.get("answer"):
                        st.markdown("**Answer**:")
                        st.markdown(h["answer"])
                    if h.get("rejected_reason"):
                        st.warning(f"Rejected: {h['rejected_reason']}")
                    if h.get("error"):
                        st.error(f"Error: {h['error']}")
                    if h.get("feedback_note"):
                        st.info(f"📝 Note: {h['feedback_note']}")
                with right:
                    st.markdown(
                        f"- club: `{h['tenant_club_id']}`\n"
                        f"- rows: `{h.get('row_count')}`\n"
                        f"- elapsed: `{h.get('elapsed_ms')} ms`\n"
                        f"- feedback: `{h.get('feedback')}`\n"
                        f"- golden: `{h.get('is_golden')}`"
                    )

                if h.get("generated_sql") or h.get("validated_sql"):
                    st.markdown("**SQL (validated):**")
                    st.code(h.get("validated_sql") or h.get("generated_sql"), language="sql")
                if h.get("corrected_sql"):
                    st.markdown("**SQL (user-corrected):**")
                    st.code(h["corrected_sql"], language="sql")

                # Actions
                a1, a2, _ = st.columns([2, 2, 8])
                if not h.get("is_golden"):
                    if a1.button("⭐ Mark as golden", key=f"gold_{h['id']}"):
                        # Save to golden_examples & mark history row
                        sql = h.get("corrected_sql") or h.get("validated_sql") or h.get("generated_sql")
                        if sql:
                            storage.add_golden_example(
                                user_role=h["user_role"],
                                question=h["question"],
                                sql_template=sql,
                                explanation=h.get("feedback_note"),
                                source_history_id=h["id"],
                            )
                            storage.mark_golden(h["id"], golden=True)
                            st.toast("Saved as golden example", icon="⭐")
                            st.rerun()
                else:
                    if a1.button("✖ Unmark golden", key=f"ungold_{h['id']}"):
                        storage.mark_golden(h["id"], golden=False)
                        st.rerun()

# ============================================================================
# Tab 2: Golden examples
# ============================================================================
with tab_golden:
    examples = storage.list_golden_examples()
    st.markdown(f"### {len(examples)} curated examples")
    st.caption(
        "Όταν φτάσουμε σε Φάση Γ, αυτά θα φορτώνονται αυτόματα στο prompt "
        "ως few-shot examples (top-3 πιο όμοια με την ερώτηση)."
    )

    for ex in examples:
        with st.expander(f"📚 {ex['question']}  ·  _{ex.get('user_role')}_"):
            st.code(ex["sql_template"], language="sql")
            if ex.get("explanation"):
                st.caption(ex["explanation"])
            if ex.get("tags"):
                st.write("Tags: " + ", ".join(ex["tags"]))

    st.divider()
    with st.form("new_golden"):
        st.markdown("### ➕ Πρόσθεσε χειροκίνητα")
        c1, c2 = st.columns(2)
        role = c1.selectbox("Role", options=["manager", "parent", "any"], index=0)
        tags_str = c2.text_input("Tags (comma-separated, optional)")
        q = st.text_area("Question", height=80)
        s = st.text_area("SQL", height=200)
        expl = st.text_input("Explanation (optional)")
        if st.form_submit_button("Save"):
            if q.strip() and s.strip():
                storage.add_golden_example(
                    user_role=role,
                    question=q.strip(),
                    sql_template=s.strip(),
                    explanation=expl or None,
                    tags=[t.strip() for t in tags_str.split(",") if t.strip()] or None,
                )
                st.success("Saved!")
                st.rerun()
            else:
                st.error("Question και SQL είναι υποχρεωτικά.")

# ============================================================================
# Tab 3: Schema annotations
# ============================================================================
with tab_schema:
    annotations = storage.list_schema_annotations()
    st.markdown(f"### {len(annotations)} active annotations")
    st.caption(
        "Notes που θα ενσωματώνονται στο schema context του LLM (Φάση Γ). "
        "Καλά για \"το X είναι αναξιόπιστο\", \"χρησιμοποίησε Y αντί για Z\", κλπ."
    )

    if annotations:
        df = pd.DataFrame(annotations)[
            ["table_name", "column_name", "note", "severity", "created_by", "created_at"]
        ]
        st.dataframe(df, use_container_width=True)

    st.divider()
    with st.form("new_annotation"):
        st.markdown("### ➕ Πρόσθεσε annotation")
        c1, c2, c3 = st.columns([3, 3, 2])
        table = c1.text_input("Table name", placeholder="π.χ. team_user")
        col = c2.text_input("Column name (optional)", placeholder="π.χ. created_at")
        sev = c3.selectbox("Severity", options=["info", "warn", "critical"], index=0)
        note = st.text_area(
            "Note", placeholder="π.χ. team_user.created_at είναι αναξιόπιστο, χρησιμοποίησε users.created_at"
        )
        if st.form_submit_button("Save"):
            if table.strip() and note.strip():
                storage.add_schema_annotation(
                    table_name=table.strip(),
                    column_name=col.strip() or None,
                    note=note.strip(),
                    severity=sev,
                    created_by=user.username,
                )
                st.success("Saved!")
                st.rerun()
            else:
                st.error("Table name και note είναι υποχρεωτικά.")
