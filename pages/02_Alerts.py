"""
pages/02_Alerts.py
------------------
Alerts & Club Ops dashboard (Streamlit multipage).

Τρέχει live μέσω Metabase για επιλέξιμο σύλλογο (default DEFAULT_CLUB_ID):
  • KPI band (ενεργοί αθλητές, νέες εγγραφές, καθαρό μήνα, παρουσία, ληξιπρόθεσμες)
  • 🚨 Alerts: churn risk, λήγουσες & ληξιπρόθεσμες συνδρομές, κοιμισμένοι αθλητές
  • 📈 Trends: έσοδα/μήνα, έσοδα vs έξοδα, εγγραφές/μήνα, παρουσίες/εβδομάδα
  • 🏐 Ομάδες & Events: παρουσία ανά ομάδα, επόμενα events, αποτελέσματα, ομάδες χωρίς προπονητή
  • 💶 Οικονομικά: MoM έσοδα, έξοδα ανά κατηγορία
  • 🎂 Extras: γενέθλια μήνα
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
EVENT_TYPE = {1: "Προπόνηση", 2: "Αγώνας", 3: "Εκδήλωση", 4: "Άλλο"}
RESULT_LABEL = {1: "✅ Νίκη", 2: "❌ Ήττα", 3: "➖ Ισοπαλία"}


@st.cache_resource(show_spinner=False)
def _mb(url: str, api: str, sess: str, db: int) -> MetabaseClient:
    return MetabaseClient(url=url, api_key=api, session_token=sess, database_id=db)


mb = _mb(METABASE_URL, METABASE_API_KEY, METABASE_SESSION, METABASE_DATABASE_ID)


# ---------------------------------------------------------------------------
# SQL templates ({{C}} = club_id, {{T}} = 'today' literal). Όλα επικυρωμένα.
# ---------------------------------------------------------------------------
Q = {
    "kpi": """
SELECT
 (SELECT COUNT(DISTINCT u.id) FROM users u
    JOIN team_user tu ON tu.user_id=u.id AND (tu.first_coach=0 OR tu.first_coach IS NULL) AND tu.deleted_at IS NULL
    JOIN teams t ON t.id=tu.team_id AND t.club_id={{C}} AND t.status=1
   WHERE u.deleted_at IS NULL AND u.status=1) AS active_athletes,
 (SELECT COUNT(*) FROM users u WHERE u.club_id={{C}} AND u.deleted_at IS NULL AND u.role=4
     AND u.created_at >= DATE_FORMAT({{T}},'%Y-%m-01')) AS new_athletes_month,
 (SELECT COALESCE(SUM(amount),0) FROM incomes WHERE club_id={{C}} AND deleted_at IS NULL
     AND transaction_at >= DATE_FORMAT({{T}},'%Y-%m-01')) AS income_month,
 (SELECT COALESCE(SUM(amount),0) FROM outgoings WHERE club_id={{C}} AND deleted_at IS NULL
     AND transaction_at >= DATE_FORMAT({{T}},'%Y-%m-01')) AS outgoing_month,
 (SELECT ROUND(100.0*SUM(CASE WHEN ae.check=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1)
    FROM appearance_events ae JOIN events e ON e.id=ae.event_id AND e.deleted_at IS NULL JOIN teams t ON t.id=ae.team_id
    WHERE t.club_id={{C}} AND e.start_date>=DATE_SUB({{T}},INTERVAL 30 DAY) AND e.start_date<{{T}} AND ae.check IS NOT NULL) AS attendance_pct_30d,
 (SELECT COUNT(*) FROM subscription_users su JOIN subscriptions s ON s.id=su.subscription_id JOIN users u ON u.id=su.user_id
    WHERE s.club_id={{C}} AND s.status=1 AND u.deleted_at IS NULL AND su.due_at IS NOT NULL AND su.due_at < {{T}}) AS overdue_count,
 (SELECT COALESCE(SUM(GREATEST(s.amount-COALESCE(su.total_paid,0),0)),0) FROM subscription_users su JOIN subscriptions s ON s.id=su.subscription_id JOIN users u ON u.id=su.user_id
    WHERE s.club_id={{C}} AND s.status=1 AND u.deleted_at IS NULL AND su.due_at IS NOT NULL AND su.due_at < {{T}}) AS total_owed
""",
    "income_by_month": """
SELECT DATE_FORMAT(transaction_at,'%Y-%m-01') m, SUM(amount) income
FROM incomes WHERE club_id={{C}} AND deleted_at IS NULL
 AND transaction_at>=DATE_SUB(DATE_FORMAT({{T}},'%Y-%m-01'),INTERVAL 11 MONTH)
GROUP BY m ORDER BY m
""",
    "outgoing_by_month": """
SELECT DATE_FORMAT(transaction_at,'%Y-%m-01') m, SUM(amount) outgoing
FROM outgoings WHERE club_id={{C}} AND deleted_at IS NULL
 AND transaction_at>=DATE_SUB(DATE_FORMAT({{T}},'%Y-%m-01'),INTERVAL 11 MONTH)
GROUP BY m ORDER BY m
""",
    "regs_by_month": """
SELECT DATE_FORMAT(created_at,'%Y-%m-01') m, COUNT(*) registrations
FROM users WHERE club_id={{C}} AND role=4 AND deleted_at IS NULL
 AND created_at>=DATE_SUB(DATE_FORMAT({{T}},'%Y-%m-01'),INTERVAL 11 MONTH)
GROUP BY m ORDER BY m
""",
    "attendance_by_week": """
SELECT MIN(DATE(e.start_date)) wk_start,
  ROUND(100.0*SUM(CASE WHEN ae.check=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) attendance_pct
FROM appearance_events ae JOIN events e ON e.id=ae.event_id AND e.deleted_at IS NULL JOIN teams t ON t.id=ae.team_id
WHERE t.club_id={{C}} AND ae.check IS NOT NULL AND e.start_date>=DATE_SUB({{T}},INTERVAL 56 DAY) AND e.start_date<{{T}}
GROUP BY YEARWEEK(e.start_date,3) ORDER BY wk_start
""",
    "overdue_list": """
SELECT CONCAT_WS(' ',u.name,u.last_name) athlete, s.title AS subscription,
  GREATEST(s.amount-COALESCE(su.total_paid,0),0) AS owed, su.due_at AS expired_at,
  DATEDIFF({{T}}, su.due_at) AS days_overdue
FROM subscription_users su JOIN subscriptions s ON s.id=su.subscription_id JOIN users u ON u.id=su.user_id
WHERE s.club_id={{C}} AND s.status=1 AND u.deleted_at IS NULL AND su.due_at IS NOT NULL AND su.due_at < {{T}}
ORDER BY days_overdue DESC LIMIT 200
""",
    "dormant_list": """
SELECT CONCAT_WS(' ',u.name,u.last_name) athlete,
 (SELECT MAX(e.start_date) FROM appearance_events ae JOIN events e ON e.id=ae.event_id JOIN teams t2 ON t2.id=ae.team_id
   WHERE ae.user_id=u.id AND ae.check=1 AND t2.club_id={{C}}) last_present
FROM users u
JOIN team_user tu ON tu.user_id=u.id AND (tu.first_coach=0 OR tu.first_coach IS NULL) AND tu.deleted_at IS NULL
JOIN teams t ON t.id=tu.team_id AND t.club_id={{C}} AND t.status=1
WHERE u.deleted_at IS NULL AND u.status=1 AND u.role=4
GROUP BY u.id, athlete
HAVING last_present IS NULL OR last_present < DATE_SUB({{T}},INTERVAL 90 DAY)
ORDER BY last_present IS NULL DESC, last_present ASC LIMIT 200
""",
    "attendance_by_team": """
SELECT t.name team, COUNT(*) recorded,
  ROUND(100.0*SUM(CASE WHEN ae.check=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0),1) attendance_pct
FROM appearance_events ae JOIN events e ON e.id=ae.event_id AND e.deleted_at IS NULL JOIN teams t ON t.id=ae.team_id
WHERE t.club_id={{C}} AND ae.check IS NOT NULL AND e.start_date>=DATE_SUB({{T}},INTERVAL 30 DAY) AND e.start_date<{{T}}
GROUP BY t.id,t.name HAVING recorded>0 ORDER BY attendance_pct ASC LIMIT 50
""",
    "upcoming_events": """
SELECT e.start_date, e.title, e.type, e.location_alias
FROM events e WHERE e.club_id={{C}} AND e.deleted_at IS NULL
 AND e.start_date>={{T}} AND e.start_date<DATE_ADD({{T}},INTERVAL 7 DAY)
ORDER BY e.start_date LIMIT 200
""",
    "recent_results": """
SELECT e.start_date, e.title, e.result, e.score_home, e.score_away
FROM events e WHERE e.club_id={{C}} AND e.deleted_at IS NULL AND e.type=2 AND e.result IS NOT NULL
ORDER BY e.start_date DESC LIMIT 15
""",
    "teams_no_coach": """
SELECT t.name team FROM teams t
WHERE t.club_id={{C}} AND t.status=1
 AND NOT EXISTS (SELECT 1 FROM team_user tu WHERE tu.team_id=t.id AND tu.first_coach=1 AND tu.deleted_at IS NULL)
ORDER BY t.name LIMIT 100
""",
    "expense_by_category": """
SELECT COALESCE(et.title,'(χωρίς κατηγορία)') category, SUM(o.amount) total
FROM outgoings o LEFT JOIN expense_types et ON et.id=o.expense_type_id
WHERE o.club_id={{C}} AND o.deleted_at IS NULL AND o.transaction_at>=DATE_SUB({{T}},INTERVAL 12 MONTH)
GROUP BY category ORDER BY total DESC LIMIT 15
""",
    "birthdays": """
SELECT CONCAT_WS(' ',name,last_name) athlete, DAY(birthday) day_of_month
FROM users WHERE club_id={{C}} AND role=4 AND deleted_at IS NULL AND birthday IS NOT NULL
 AND MONTH(birthday)=MONTH({{T}})
ORDER BY DAY(birthday) LIMIT 100
""",
}


def _render(template: str, club_id: int, today: str) -> str:
    return template.replace("{{C}}", str(club_id)).replace("{{T}}", f"'{today}'")


@st.cache_data(ttl=300, show_spinner=False)
def runq(key: str, club_id: int, today: str) -> list[dict]:
    return mb.run_sql(_render(Q[key], club_id, today))


@st.cache_data(ttl=300, show_spinner=False)
def run_detector(detector: str, club_id: int, today: str) -> list[dict]:
    sql = (DETECTORS_DIR / f"{detector}.sql").read_text(encoding="utf-8")
    return mb.run_sql(sql.replace("{{tenant_club_id}}", str(club_id)).replace("{{today}}", f"'{today}'"))


def eur(n) -> str:
    try:
        return ("€{:,.0f}".format(float(n))).replace(",", ".")
    except Exception:
        return "—"


def downloads(df: pd.DataFrame, key: str) -> None:
    c1, c2, _ = st.columns([1, 1, 6])
    c1.download_button("⬇️ CSV", df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{key}.csv", mime="text/csv", key=f"csv_{key}")
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name=key[:31])
        c2.download_button("⬇️ Excel", buf.getvalue(), file_name=f"{key}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"xlsx_{key}")
    except Exception:
        pass


def table(rows: list[dict], cols: list[str], key: str) -> None:
    df = pd.DataFrame(rows)
    show = [c for c in cols if c in df.columns] or list(df.columns)
    st.dataframe(df[show], use_container_width=True, hide_index=True)
    downloads(df, key)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
st.title("🚨 Alerts & Club Ops")
st.caption(f"Logged in as **{user.name}** ({user.role}) · ζωντανά δεδομένα από Metabase")

c1, c2, c3 = st.columns([2, 2, 6])
club_id = int(c1.number_input("Club ID", min_value=1, value=DEFAULT_CLUB_ID, step=1))
today_override = c2.text_input("Today (YYYY-MM-DD, κενό = σήμερα)", value="")
today_str = today_override.strip() or date.today().isoformat()
if c3.button("🔄 Ανανέωση δεδομένων"):
    st.cache_data.clear()
    st.rerun()


def safe(fn, label: str):
    try:
        return fn()
    except MetabaseError as e:
        st.error(f"Metabase error ({label}): {e}")
    except Exception as e:
        st.error(f"Σφάλμα ({label}): {e}")
    return None


# ---------------------------------------------------------------------------
# KPI band
# ---------------------------------------------------------------------------
kpi_rows = safe(lambda: runq("kpi", club_id, today_str), "KPIs")
if kpi_rows:
    k = kpi_rows[0]
    net = float(k.get("income_month") or 0) - float(k.get("outgoing_month") or 0)
    m = st.columns(5)
    m[0].metric("Ενεργοί αθλητές", int(k.get("active_athletes") or 0))
    m[1].metric("Νέες εγγραφές (μήνα)", int(k.get("new_athletes_month") or 0))
    m[2].metric("Καθαρό μήνα", eur(net))
    m[3].metric("Μέση παρουσία 30η", f"{k.get('attendance_pct_30d') or 0}%")
    m[4].metric("Ληξιπρόθεσμες", int(k.get("overdue_count") or 0),
                delta=(f"-{eur(k.get('total_owed'))}" if (k.get('total_owed') or 0) else None),
                delta_color="inverse")

st.divider()

tab_alerts, tab_trends, tab_teams, tab_fin, tab_extra = st.tabs(
    ["🚨 Alerts", "📈 Trends", "🏐 Ομάδες & Events", "💶 Οικονομικά", "🎂 Extras"]
)

# ===========================================================================
# Tab: Alerts
# ===========================================================================
with tab_alerts:
    st.subheader("⚠️ Κίνδυνος αποχώρησης")
    st.caption("Ενεργή συνδρομή, καμία παρουσία 30 μέρες ενώ είχαν παρουσίες πριν.")
    churn = safe(lambda: run_detector("churn_risk", club_id, today_str), "churn")
    if churn is not None:
        st.metric("Σε κίνδυνο", len(churn))
        if churn:
            table(churn, ["athlete", "subscription_title", "attended_30_to_90d_ago", "last_present_at", "email", "phone"], "churn_risk")
        else:
            st.success("✓ Κανένας σε κίνδυνο.")

    st.divider()
    st.subheader("⏰ Συνδρομές που λήγουν (14 μέρες)")
    exp = safe(lambda: run_detector("expiring_subscriptions", club_id, today_str), "expiring")
    if exp is not None:
        st.metric("Λήγουν σύντομα", len(exp))
        if exp:
            table(exp, ["athlete", "subscription_title", "amount", "expires_at", "days_until_expiry", "email", "phone"], "expiring")
        else:
            st.success("✓ Καμία συνδρομή δεν λήγει τις επόμενες 14 μέρες.")

    st.divider()
    st.subheader("🔴 Ληξιπρόθεσμες συνδρομές")
    st.caption("Συνδρομές που έχουν ήδη λήξει και δεν ανανεώθηκαν.")
    overdue = safe(lambda: runq("overdue_list", club_id, today_str), "overdue")
    if overdue is not None:
        if overdue:
            table(overdue, ["athlete", "subscription", "owed", "expired_at", "days_overdue"], "overdue")
        else:
            st.success("✓ Καμία ληξιπρόθεσμη συνδρομή.")

    st.divider()
    st.subheader("😴 Κοιμισμένοι αθλητές")
    st.caption("Ενεργοί αθλητές χωρίς καμία παρουσία τις τελευταίες 90 μέρες (ή ποτέ).")
    dormant = safe(lambda: runq("dormant_list", club_id, today_str), "dormant")
    if dormant is not None:
        st.metric("Κοιμισμένοι", len(dormant))
        if dormant:
            table(dormant, ["athlete", "last_present"], "dormant")
        else:
            st.success("✓ Όλοι οι αθλητές είναι ενεργοί.")

# ===========================================================================
# Tab: Trends
# ===========================================================================
with tab_trends:
    st.subheader("💶 Έσοδα ανά μήνα (12μηνο)")
    inc = safe(lambda: runq("income_by_month", club_id, today_str), "income/month")
    out = safe(lambda: runq("outgoing_by_month", club_id, today_str), "outgoing/month")
    if inc is not None and inc:
        di = pd.DataFrame(inc).rename(columns={"m": "Μήνας", "income": "Έσοδα"})
        do = pd.DataFrame(out or []).rename(columns={"m": "Μήνας", "outgoing": "Έξοδα"})
        merged = di.merge(do, on="Μήνας", how="outer").fillna(0).sort_values("Μήνας").set_index("Μήνας")
        st.line_chart(merged[["Έσοδα"]])
        st.subheader("📊 Έσοδα vs Έξοδα")
        st.bar_chart(merged[[c for c in ["Έσοδα", "Έξοδα"] if c in merged.columns]])
    else:
        st.info("Δεν υπάρχουν δεδομένα εσόδων.")

    st.divider()
    st.subheader("🆕 Νέες εγγραφές ανά μήνα")
    regs = safe(lambda: runq("regs_by_month", club_id, today_str), "regs/month")
    if regs:
        dr = pd.DataFrame(regs).rename(columns={"m": "Μήνας", "registrations": "Εγγραφές"}).set_index("Μήνας")
        st.bar_chart(dr)
    else:
        st.info("Δεν υπάρχουν δεδομένα εγγραφών.")

    st.divider()
    st.subheader("📅 Παρουσίες ανά εβδομάδα (8 εβδομάδες)")
    aw = safe(lambda: runq("attendance_by_week", club_id, today_str), "attendance/week")
    if aw:
        da = pd.DataFrame(aw).rename(columns={"wk_start": "Εβδομάδα", "attendance_pct": "Παρουσία %"}).set_index("Εβδομάδα")
        st.line_chart(da)
    else:
        st.info("Δεν υπάρχουν δεδομένα παρουσιών.")

# ===========================================================================
# Tab: Teams & Events
# ===========================================================================
with tab_teams:
    st.subheader("📉 Παρουσία ανά ομάδα (30 μέρες)")
    abt = safe(lambda: runq("attendance_by_team", club_id, today_str), "attendance/team")
    if abt:
        dft = pd.DataFrame(abt)
        st.bar_chart(dft.rename(columns={"team": "Ομάδα", "attendance_pct": "Παρουσία %"}).set_index("Ομάδα")[["Παρουσία %"]])
        table(abt, ["team", "recorded", "attendance_pct"], "attendance_team")
    else:
        st.info("Δεν υπάρχουν δεδομένα παρουσιών.")

    st.divider()
    st.subheader("🗓️ Επόμενα events (7 μέρες)")
    up = safe(lambda: runq("upcoming_events", club_id, today_str), "upcoming")
    if up is not None:
        if up:
            df = pd.DataFrame(up)
            df["Τύπος"] = df["type"].map(EVENT_TYPE).fillna("Άλλο")
            df = df.rename(columns={"start_date": "Πότε", "title": "Τίτλος", "location_alias": "Τοποθεσία"})
            st.dataframe(df[["Πότε", "Τύπος", "Τίτλος", "Τοποθεσία"]], use_container_width=True, hide_index=True)
            downloads(df, "upcoming_events")
        else:
            st.info("Κανένα event τις επόμενες 7 μέρες.")

    st.divider()
    st.subheader("🏆 Τελευταία αποτελέσματα")
    res = safe(lambda: runq("recent_results", club_id, today_str), "results")
    if res is not None:
        if res:
            df = pd.DataFrame(res)
            df["Αποτέλεσμα"] = df["result"].map(RESULT_LABEL).fillna("—")
            df["Σκορ"] = df.apply(lambda r: f"{r.get('score_home','')}-{r.get('score_away','')}", axis=1)
            df = df.rename(columns={"start_date": "Πότε", "title": "Αγώνας"})
            st.dataframe(df[["Πότε", "Αγώνας", "Αποτέλεσμα", "Σκορ"]], use_container_width=True, hide_index=True)
        else:
            st.info("Δεν υπάρχουν καταγεγραμμένα αποτελέσματα.")

    st.divider()
    st.subheader("🧑‍🏫 Ομάδες χωρίς προπονητή")
    noc = safe(lambda: runq("teams_no_coach", club_id, today_str), "no-coach")
    if noc is not None:
        if noc:
            table(noc, ["team"], "teams_no_coach")
        else:
            st.success("✓ Όλες οι ομάδες έχουν προπονητή.")

# ===========================================================================
# Tab: Financials
# ===========================================================================
with tab_fin:
    st.subheader("📈 Έσοδα μήνα vs προηγούμενου")
    rev = safe(lambda: run_detector("mom_revenue", club_id, today_str), "mom")
    if rev:
        r = rev[0]
        pct = r.get("change_pct")
        prev_i = float(r.get("prev_income") or 0)
        this_i = float(r.get("this_income") or 0)
        m1, m2, m3 = st.columns(3)
        m1.metric(str(r.get("prev_month", ""))[:7], eur(prev_i))
        m2.metric(str(r.get("this_month", ""))[:7], eur(this_i), delta=(f"{pct}%" if pct is not None else None))
        m3.metric("Διαφορά", eur(this_i - prev_i))
    else:
        st.info("Δεν υπάρχουν επαρκή δεδομένα εσόδων για σύγκριση.")

    st.divider()
    st.subheader("💸 Έξοδα ανά κατηγορία (12μηνο)")
    exc = safe(lambda: runq("expense_by_category", club_id, today_str), "expense/category")
    if exc:
        de = pd.DataFrame(exc)
        st.bar_chart(de.rename(columns={"category": "Κατηγορία", "total": "Σύνολο"}).set_index("Κατηγορία"))
        table(exc, ["category", "total"], "expense_category")
    else:
        st.info("Δεν υπάρχουν δεδομένα εξόδων.")

# ===========================================================================
# Tab: Extras
# ===========================================================================
with tab_extra:
    st.subheader("🎂 Γενέθλια αθλητών αυτόν τον μήνα")
    bd = safe(lambda: runq("birthdays", club_id, today_str), "birthdays")
    if bd is not None:
        if bd:
            df = pd.DataFrame(bd).rename(columns={"athlete": "Αθλητής", "day_of_month": "Ημέρα"})
            st.dataframe(df, use_container_width=True, hide_index=True)
            downloads(df, "birthdays")
        else:
            st.info("Κανένα γενέθλιο αυτόν τον μήνα.")
