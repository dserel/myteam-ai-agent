"""
pages/02_Alerts.py
------------------
Alerts page (Streamlit multipage): τρέχει τους detectors (churn risk,
expiring subscriptions, month-over-month revenue) live μέσω Metabase και
τους εμφανίζει για τον επιλεγμένο σύλλογο.

Εμφανίζεται αυτόματα στο sidebar του Streamlit (pages/ folder).
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.auth import login_gate, logout_button, require_admin
from src.metabase import MetabaseClient, MetabaseError

st.set_page_config(page_title="Alerts · myTeam AI Agent", page_icon="🚨", layout="wide")

user = login_gate()
require_admin(user)
logout_button()


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


METABASE_URL = _secret("METABASE_URL", "https://metabase.myteam.gr")
METABASE_API_KEY = _secret("METABASE_API_KEY")
METABASE_SESSION = _secret("METABASE_SESSION")
METABASE_DATABASE_ID = int(_secret("METABASE_DATABASE_ID", "2"))
DEFAULT_CLUB_ID = int(_secret("DEFAULT_CLUB_ID", "41"))

if not (METABASE_API_KEY or METABASE_SESSION):
    st.error("Λείπει METABASE_API_KEY / METABASE_SESSION στα secrets.")
    st.stop()

DETECTORS_DIR = Path(__file__).resolve().parent.parent / "detectors"


@st.cache_resource(show_spinner=False)
def _mb(url: str, api: str, sess: str, db: int) -> MetabaseClient:
    return MetabaseClient(url=url, api_key=api, session_token=sess, database_id=db)


mb = _mb(METABASE_URL, METABASE_API_KEY, METABASE_SESSION, METABASE_DATABASE_ID)


def _render_sql(template: str, *, club_id: int, today: str) -> str:
    return template.replace("{{tenant_club_id}}", str(club_id)).replace("{{today}}", f"'{today}'")


@st.cache_data(ttl=300, show_spinner=False)
def _run_detector(detector: str, club_id: int, today: str) -> list[dict]:
    """Τρέχει ένα detector .sql και επιστρέφει rows. Cached για 5'."""
    sql = (DETECTORS_DIR / f"{detector}.sql").read_text(encoding="utf-8")
    return mb.run_sql(_render_sql(sql, club_id=club_id, today=today))


def _download_buttons(df: pd.DataFrame, key: str) -> None:
    c1, c2, _ = st.columns([1, 1, 6])
    c1.download_button(
        "⬇️ CSV", df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{key}.csv", mime="text/csv", key=f"csv_{key}",
    )
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name=key[:31])
        c2.download_button(
            "⬇️ Excel", buf.getvalue(), file_name=f"{key}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{key}",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
st.title("🚨 Alerts")
st.caption(f"Logged in as **{user.name}** ({user.role}) · ζωντανά δεδομένα από Metabase")

c1, c2, c3 = st.columns([2, 2, 6])
club_id = c1.number_input("Club ID", min_value=1, value=DEFAULT_CLUB_ID, step=1)
today_override = c2.text_input("Today (YYYY-MM-DD, κενό = σήμερα)", value="")
today_str = today_override.strip() or date.today().isoformat()
if c3.button("🔄 Ανανέωση", help="Καθαρίζει το cache και ξανατρέχει"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# 1. Churn risk
# ---------------------------------------------------------------------------
st.subheader("⚠️ Αθλητές σε κίνδυνο αποχώρησης")
st.caption("Ενεργή συνδρομή, καμία παρουσία τις τελευταίες 30 μέρες, ενώ είχαν παρουσίες πριν.")
try:
    with st.spinner("Έλεγχος churn risk…"):
        churn = _run_detector("churn_risk", int(club_id), today_str)
    st.metric("Σε κίνδυνο", len(churn))
    if churn:
        df = pd.DataFrame(churn)
        cols = [c for c in ["athlete", "subscription_title", "attended_30_to_90d_ago", "last_present_at", "email", "phone"] if c in df.columns]
        st.dataframe(df[cols] if cols else df, use_container_width=True, hide_index=True)
        _download_buttons(df, "churn_risk")
    else:
        st.success("✓ Κανένας αθλητής σε κίνδυνο αποχώρησης.")
except MetabaseError as e:
    st.error(f"Metabase error (churn): {e}")
except Exception as e:
    st.error(f"Σφάλμα (churn): {e}")

st.divider()

# ---------------------------------------------------------------------------
# 2. Expiring subscriptions
# ---------------------------------------------------------------------------
st.subheader("⏰ Συνδρομές που λήγουν σύντομα")
st.caption("Ενεργές συνδρομές που λήγουν στις επόμενες 14 μέρες.")
try:
    with st.spinner("Έλεγχος συνδρομών…"):
        exp = _run_detector("expiring_subscriptions", int(club_id), today_str)
    st.metric("Λήγουν σε 14 μέρες", len(exp))
    if exp:
        df = pd.DataFrame(exp)
        cols = [c for c in ["athlete", "subscription_title", "amount", "expires_at", "days_until_expiry", "email", "phone"] if c in df.columns]
        st.dataframe(df[cols] if cols else df, use_container_width=True, hide_index=True)
        _download_buttons(df, "expiring_subscriptions")
    else:
        st.success("✓ Καμία συνδρομή δεν λήγει τις επόμενες 14 μέρες.")
except MetabaseError as e:
    st.error(f"Metabase error (expiring): {e}")
except Exception as e:
    st.error(f"Σφάλμα (expiring): {e}")

st.divider()

# ---------------------------------------------------------------------------
# 3. Month-over-month revenue
# ---------------------------------------------------------------------------
st.subheader("💶 Έσοδα μήνα vs προηγούμενου")
try:
    with st.spinner("Υπολογισμός εσόδων…"):
        rev = _run_detector("mom_revenue", int(club_id), today_str)
    if rev:
        r = rev[0]
        pct = r.get("change_pct")
        prev_income = float(r.get("prev_income") or 0)
        this_income = float(r.get("this_income") or 0)
        m1, m2, m3 = st.columns(3)
        m1.metric(str(r.get("prev_month", ""))[:7], f"€{prev_income:,.0f}".replace(",", "."))
        m2.metric(
            str(r.get("this_month", ""))[:7], f"€{this_income:,.0f}".replace(",", "."),
            delta=(f"{pct}%" if pct is not None else None),
        )
        m3.metric("Διαφορά", f"€{(this_income - prev_income):,.0f}".replace(",", "."))
    else:
        st.info("Δεν υπάρχουν επαρκή δεδομένα εσόδων για σύγκριση.")
except MetabaseError as e:
    st.error(f"Metabase error (revenue): {e}")
except Exception as e:
    st.error(f"Σφάλμα (revenue): {e}")
