"""
streamlit_app.py
----------------
Chat UI για το myTeam AI Agent.

Phase A+B: live deployment με auth + Supabase storage για feedback loop.

Τρέξιμο τοπικά:
    pip install -r requirements.txt
    streamlit run streamlit_app.py

Δες README_MVP.md για το deployment στο Streamlit Cloud.
"""

from __future__ import annotations

import time
from datetime import date

import streamlit as st

from src.auth import login_gate, logout_button
from src.llm import LLMClient
from src.metabase import MetabaseClient, MetabaseError
from src.storage import StorageClient
from src.validator import ValidationError, validate_and_normalize

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="myTeam AI Agent",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚽ myTeam AI Agent")
st.caption("Text-to-SQL βοηθός με feedback loop · Internal demo")

# Auth gate (st.stop() αν δεν είναι logged in)
user = login_gate()

# ---------------------------------------------------------------------------
# Config from secrets
# ---------------------------------------------------------------------------

def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# Όλη η config έρχεται από secrets — ΔΕΝ τα ζητάμε στο sidebar πια.
# Ο χρήστης βλέπει μόνο: role, club_id (αν χρειάζεται override).

GEMINI_API_KEY = _secret("GEMINI_API_KEY")
METABASE_URL = _secret("METABASE_URL", "https://metabase.myteam.gr")
METABASE_API_KEY = _secret("METABASE_API_KEY")
METABASE_SESSION = _secret("METABASE_SESSION")
METABASE_DATABASE_ID = int(_secret("METABASE_DATABASE_ID", "2"))
SUPABASE_URL = _secret("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _secret("SUPABASE_SERVICE_KEY")
DEFAULT_CLUB_ID = int(_secret("DEFAULT_CLUB_ID", "41"))

# Sanity check on required secrets
required_missing = []
if not GEMINI_API_KEY:
    required_missing.append("GEMINI_API_KEY")
if not (METABASE_API_KEY or METABASE_SESSION):
    required_missing.append("METABASE_API_KEY or METABASE_SESSION")
if not METABASE_URL:
    required_missing.append("METABASE_URL")
if required_missing:
    st.error(
        "❌ Λείπουν secrets: **"
        + ", ".join(required_missing)
        + "**. Δες README_MVP.md → Streamlit Cloud deployment."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar (lightweight — μόνο role/club, όχι credentials)
# ---------------------------------------------------------------------------

with st.sidebar:
    logout_button()

    st.divider()
    st.subheader("👤 Context")

    user_role = st.selectbox("Role", options=["manager", "parent"], index=0)
    tenant_club_id = st.number_input(
        "Club ID", min_value=1, value=DEFAULT_CLUB_ID, step=1
    )
    tenant_user_id_str = st.text_input(
        "User ID (απαιτείται για parent)",
        value="",
        help="users.id του γονιού",
    )
    tenant_user_id = int(tenant_user_id_str) if tenant_user_id_str.strip() else None

    today_override = st.text_input("Today (YYYY-MM-DD, κενό = σήμερα)", value="")

    st.divider()

    # Test buttons (μόνο για admins)
    if user.role == "admin":
        st.subheader("🧪 Tests")
        col1, col2 = st.columns(2)
        if col1.button("Test Gemini", use_container_width=True):
            with st.spinner("..."):
                ok, msg = LLMClient(api_key=GEMINI_API_KEY).test_connection()
            (st.success if ok else st.error)(msg)
        if col2.button("Test Metabase", use_container_width=True):
            with st.spinner("..."):
                ok, msg = MetabaseClient(
                    url=METABASE_URL,
                    api_key=METABASE_API_KEY,
                    session_token=METABASE_SESSION,
                    database_id=METABASE_DATABASE_ID,
                ).test_connection()
            (st.success if ok else st.error)(msg)
        if SUPABASE_URL and SUPABASE_SERVICE_KEY:
            if st.button("Test Supabase", use_container_width=True):
                with st.spinner("..."):
                    ok, msg = StorageClient(
                        url=SUPABASE_URL, service_key=SUPABASE_SERVICE_KEY
                    ).test_connection()
                (st.success if ok else st.error)(msg)

    st.divider()
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    with st.expander("ℹ️ Πληροφορίες"):
        st.markdown(
            "- Το LLM παράγει SQL που ελέγχεται από validator.\n"
            "- 👍/👎 αποθηκεύονται στο Supabase για να μαθαίνει το σύστημα.\n"
            "- Admin page για ιστορικό: μενού πάνω-αριστερά.\n"
        )

# ---------------------------------------------------------------------------
# Parent role guard
# ---------------------------------------------------------------------------

if user_role == "parent" and tenant_user_id is None:
    st.warning("Για role=parent δώσε το User ID του γονιού στο sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Build clients (cached)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _get_clients(
    gk: str, mb_url: str, mb_api: str, mb_sess: str, mb_db: int, sb_url: str, sb_key: str
):
    llm = LLMClient(api_key=gk)
    mb = MetabaseClient(url=mb_url, api_key=mb_api, session_token=mb_sess, database_id=mb_db)
    storage = None
    if sb_url and sb_key:
        try:
            storage = StorageClient(url=sb_url, service_key=sb_key)
        except Exception as e:
            st.warning(f"⚠️ Supabase μη διαθέσιμο: {e}. Το feedback δεν θα αποθηκεύεται.")
    return llm, mb, storage


llm_client, mb_client, storage_client = _get_clients(
    GEMINI_API_KEY,
    METABASE_URL,
    METABASE_API_KEY,
    METABASE_SESSION,
    METABASE_DATABASE_ID,
    SUPABASE_URL,
    SUPABASE_SERVICE_KEY,
)
today_str = today_override.strip() or date.today().isoformat()

# ---------------------------------------------------------------------------
# Chat history rendering
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []


def _render_feedback(msg_idx: int, history_id: str | None) -> None:
    """Render 👍/👎/edit για ένα assistant message."""
    if not history_id or not storage_client:
        return
    msg = st.session_state.messages[msg_idx]
    current_fb = msg.get("feedback")  # int or None

    c1, c2, c3, _ = st.columns([1, 1, 2, 8])
    up_label = "👍" + (" ✓" if current_fb == 1 else "")
    down_label = "👎" + (" ✓" if current_fb == -1 else "")

    if c1.button(up_label, key=f"up_{msg_idx}"):
        storage_client.set_feedback(history_id, feedback=1)
        st.session_state.messages[msg_idx]["feedback"] = 1
        st.toast("Σημειώθηκε ως καλή απάντηση. Ευχαριστώ!", icon="✅")
        st.rerun()
    if c2.button(down_label, key=f"down_{msg_idx}"):
        st.session_state.messages[msg_idx]["feedback"] = -1
        st.session_state.messages[msg_idx]["show_correction"] = True
        storage_client.set_feedback(history_id, feedback=-1)
        st.rerun()
    if c3.button("✏️ Edit SQL", key=f"edit_{msg_idx}"):
        st.session_state.messages[msg_idx]["show_correction"] = True
        st.rerun()

    if msg.get("show_correction"):
        with st.form(f"correction_{msg_idx}"):
            new_sql = st.text_area(
                "Διορθωμένο SQL", value=msg.get("sql") or "", height=180,
            )
            note = st.text_input("Σημείωση (προαιρετικό)")
            saved = st.form_submit_button("💾 Αποθήκευση διόρθωσης")
            if saved:
                storage_client.set_feedback(
                    history_id,
                    feedback=msg.get("feedback") or 0,
                    feedback_note=note or None,
                    corrected_sql=new_sql,
                )
                st.session_state.messages[msg_idx]["corrected_sql"] = new_sql
                st.session_state.messages[msg_idx]["show_correction"] = False
                st.toast("Αποθηκεύτηκε. Θα χρησιμοποιηθεί στη βελτίωση του μοντέλου.", icon="🧠")
                st.rerun()


# Render history
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            with st.expander("🔎 Generated SQL"):
                st.code(msg["sql"], language="sql")
        if msg.get("rows") is not None and msg["rows"]:
            with st.expander(f"📊 Rows ({len(msg['rows'])})"):
                st.dataframe(msg["rows"], use_container_width=True)
        if msg.get("error"):
            st.error(msg["error"])
        if msg["role"] == "assistant":
            _render_feedback(idx, msg.get("history_id"))


# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

prompt = st.chat_input("Ρώτα κάτι... π.χ. «πόσοι νέοι αθλητές μπήκαν φέτος;»")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        placeholder = st.empty()

        ctx = {
            "tenant_club_id": int(tenant_club_id),
            "tenant_user_id": tenant_user_id,
            "user_role": user_role,
            "today": today_str,
        }
        msg_payload: dict = {
            "role": "assistant",
            "content": "",
            "sql": None,
            "rows": None,
            "error": None,
            "history_id": None,
            "feedback": None,
        }
        t0 = time.time()
        rejected_reason: str | None = None
        err: str | None = None
        raw_sql: str | None = None
        validated_sql: str | None = None
        rows: list[dict] | None = None
        answer: str = ""

        # 1. SQL generation
        try:
            placeholder.markdown("🧠 _Παράγω SQL…_")
            gen = llm_client.generate_sql(prompt, ctx)
        except Exception as e:
            err = str(e)
            answer = f"❌ SQL generation failed: {e}"
            placeholder.markdown(answer)

        if not err:
            if "error" in gen:
                rejected_reason = "model_refused"
                answer = f"⚠️ {gen['error']}"
                placeholder.markdown(answer)
            else:
                raw_sql = (gen.get("sql") or "").strip()

        # 2. Validation
        if raw_sql and not err:
            try:
                placeholder.markdown("🛡️ _Έλεγχος ασφαλείας…_")
                v = validate_and_normalize(
                    raw_sql,
                    tenant_club_id=int(tenant_club_id),
                    tenant_user_id=tenant_user_id,
                    user_role=user_role,
                )
                validated_sql = v.sql
            except ValidationError as e:
                rejected_reason = f"validator: {e}"
                answer = (
                    f"🚫 Το παραγόμενο SQL δεν πέρασε τους ελέγχους ασφαλείας:\n\n"
                    f"`{e}`\n\nΔοκίμασε να αναδιατυπώσεις την ερώτηση πιο συγκεκριμένα."
                )
                placeholder.markdown(answer)

        # 3. Execution
        if validated_sql and not err and not rejected_reason:
            try:
                placeholder.markdown("📡 _Τρέχω query στο Metabase…_")
                rows = mb_client.run_sql(validated_sql)
            except MetabaseError as e:
                err = f"metabase: {e}"
                answer = f"💥 Metabase error: {e}"
                placeholder.markdown(answer)

        # 4. Answer
        if rows is not None and not err:
            try:
                placeholder.markdown("✍️ _Συντάσσω απάντηση…_")
                answer = llm_client.write_answer(prompt, validated_sql, rows)
            except Exception as e:
                answer = f"Βρήκα **{len(rows)}** γραμμές αλλά απέτυχε η σύνταξη της απάντησης ({e})."

        elapsed_ms = int((time.time() - t0) * 1000)
        placeholder.markdown(answer)
        st.caption(f"⏱ {elapsed_ms} ms · {len(rows) if rows else 0} rows")

        # Persist to Supabase (best-effort)
        history_id = None
        if storage_client:
            try:
                history_id = storage_client.log_query(
                    username=user.username,
                    tenant_club_id=int(tenant_club_id),
                    user_role=user_role,
                    tenant_user_id=tenant_user_id,
                    question=prompt,
                    generated_sql=raw_sql,
                    validated_sql=validated_sql,
                    row_count=(len(rows) if rows is not None else None),
                    answer=answer,
                    rejected_reason=rejected_reason,
                    error=err,
                    elapsed_ms=elapsed_ms,
                )
            except Exception as e:
                st.toast(f"Δεν αποθηκεύτηκε στο Supabase: {e}", icon="⚠️")

        # Show SQL + rows
        if validated_sql or raw_sql:
            with st.expander("🔎 Generated SQL"):
                st.code(validated_sql or raw_sql, language="sql")
        if rows is not None and rows:
            with st.expander(f"📊 Rows ({len(rows)})"):
                st.dataframe(rows, use_container_width=True)

        msg_payload["content"] = answer
        msg_payload["sql"] = validated_sql or raw_sql
        msg_payload["rows"] = rows
        msg_payload["error"] = err
        msg_payload["history_id"] = history_id
        st.session_state.messages.append(msg_payload)

        # Render feedback for this new message
        _render_feedback(len(st.session_state.messages) - 1, history_id)
