"""
pages/03_Parent.py — «Το παιδί μου»
-----------------------------------
Glance dashboard για γονείς: ταυτότητα αθλητή, ορόσημα, παρουσία, οφειλές,
πρόγραμμα εβδομάδας με καιρό + smart heads-up, ειδοποίηση αλλαγών προγράμματος,
λήψη ημερολογίου (.ics με υπενθυμίσεις), και follow-up κουμπάκια προς το chat.

Thin renderer πάνω από src/parent_view.py + src/enrich.py (η λογική/SQL ζει εκεί,
ώστε να μεταφερθεί στο myTeam app).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from src.auth import login_gate, logout_button
from src.metabase import MetabaseClient, MetabaseError
from src.llm import LLMClient
from src import parent_view as pv
from src import enrich

st.set_page_config(page_title="Το παιδί μου · myTeam", page_icon="👦", layout="wide")

user = login_gate()
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
ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _secret("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
OPENWEATHER_API_KEY = _secret("OPENWEATHER_API_KEY")
WEATHER_CITY = _secret("WEATHER_CITY", "Glyfada,GR")

if not (METABASE_API_KEY or METABASE_SESSION):
    st.error("Λείπει METABASE_API_KEY / METABASE_SESSION στα secrets.")
    st.stop()


@st.cache_resource(show_spinner=False)
def _mb(url, api, sess, db):
    return MetabaseClient(url=url, api_key=api, session_token=sess, database_id=db)


mb = _mb(METABASE_URL, METABASE_API_KEY, METABASE_SESSION, METABASE_DATABASE_ID)


def _build_llm(key, model):
    return LLMClient(api_key=key, model=model) if key else None  # μη-cached (αποφυγή stale)


llm = _build_llm(ANTHROPIC_API_KEY, CLAUDE_MODEL)

EVENT_TYPE = {1: "Προπόνηση", 2: "Αγώνας", 3: "Εκδήλωση", 4: "Άλλο"}
PARENT_FOLLOWUPS = [
    "Πόσο καλή είναι η παρουσία του παιδιού μου τον τελευταίο μήνα;",
    "Έχω ανεξόφλητη συνδρομή; Πόσα οφείλω;",
    "Σε ποια ομάδα είναι το παιδί μου και ποιος είναι ο προπονητής;",
]


def eur(n) -> str:
    try:
        return ("€{:,.0f}".format(float(n))).replace(",", ".")
    except Exception:
        return "—"


def fmt_dt(s) -> str:
    if not s:
        return "—"
    try:
        return pd.to_datetime(str(s)).strftime("%a %d/%m %H:%M")
    except Exception:
        return str(s)


st.title("👦 Το παιδί μου")
st.caption(f"Σύνδεση: **{user.name}** · ζωντανά δεδομένα από Metabase")

c1, c2, c3 = st.columns([2, 2, 4])
club_id = int(c1.number_input("Club ID", min_value=1, value=DEFAULT_CLUB_ID, step=1))
parent_id = int(c2.number_input("Parent User ID", min_value=1, value=143441, step=1,
                                help="users.id του γονέα (στο πραγματικό app θα έρχεται από το login)"))
today_str = (c3.text_input("Today (YYYY-MM-DD, κενό = σήμερα)", value="").strip() or date.today().isoformat())
if c3.button("🔄 Ανανέωση"):
    st.cache_data.clear()
    st.rerun()


@st.cache_data(ttl=300, show_spinner=False)
def load(club: int, pid: int, today: str, ow_key: str, city: str) -> dict:
    events = pv.upcoming_events(mb, club, pid, today, 7)
    if ow_key:
        events = enrich.attach_weather(ow_key, city, events)
    return {
        "profiles": pv.child_profiles(mb, club, pid, today),
        "attendance": pv.attendance(mb, club, pid, today, 30),
        "dues": pv.dues(mb, club, pid),
        "events": events,
        "changes": pv.schedule_changes(mb, club, pid, today, 14),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def event_tips(events: list, model: str) -> list:
    return llm.event_tips(events) if (llm and events) else []


try:
    with st.spinner("Φόρτωση…"):
        data = load(club_id, parent_id, today_str, OPENWEATHER_API_KEY, WEATHER_CITY)
except MetabaseError as e:
    st.error(f"Metabase error: {e}")
    st.stop()

profiles = data["profiles"]
if not profiles:
    st.warning(
        "Δεν βρέθηκαν παιδιά συνδεδεμένα με αυτόν τον γονέα. "
        "Πιθανώς λείπει η σύνδεση household_id / parent_users — δες Alerts → «Αθλητές χωρίς συνδεδεμένο γονέα»."
    )
    st.stop()

# Ειδοποίηση αλλαγών/ακυρώσεων προγράμματος
changes = data.get("changes") or []
if changes:
    msgs = [f"**{c.get('change_type','άλλαξε')}**: {c.get('title')} ({fmt_dt(c.get('start_date'))})" for c in changes[:5]]
    st.warning("⚠️ Αλλαγές προγράμματος:\n\n" + "\n\n".join("• " + m for m in msgs))

att_by = {r.get("athlete"): r for r in (data["attendance"] or [])}
dues_by: dict = {}
for d in (data["dues"] or []):
    dues_by.setdefault(d.get("athlete"), []).append(d)
today_month = int(today_str[5:7])

for prof in profiles:
    name = prof.get("athlete") or "—"
    st.divider()
    st.subheader(f"🧒 {name}")
    cols = st.columns(5)
    cols[0].metric("Ομάδα", prof.get("team") or "—")
    cols[1].metric("Προπονητής", prof.get("coach") or "—")
    cols[2].metric("Ηλικία", f"{prof.get('age')} ετών" if prof.get("age") is not None else "—")
    cols[3].metric("Φανέλα", f"#{prof.get('jersey')}" if prof.get("jersey") else "—")
    cols[4].metric("Μέλος από", str(prof.get("member_since"))[:10] if prof.get("member_since") else "—")

    ms = pv.milestones(prof, today_month)
    if ms:
        st.success("  ".join(ms))

    a, b = st.columns(2)
    with a:
        st.markdown("**📅 Παρουσία (30 μέρες)**")
        ar = att_by.get(name)
        if ar and ar.get("sessions"):
            st.metric("Παρουσία", f"{ar.get('attendance_pct')}%",
                      delta=f"{ar.get('present')}/{ar.get('sessions')} προπονήσεις", delta_color="off")
            st.caption(f"Τελευταία παρουσία: {str(ar.get('last_present'))[:10] if ar.get('last_present') else '—'}")
        else:
            st.caption("Δεν έχουν καταγραφεί παρουσίες σε αυτό το διάστημα.")
    with b:
        st.markdown("**💶 Συνδρομή**")
        dl = dues_by.get(name) or []
        owed = sum(float(x.get("owed") or 0) for x in dl)
        if dl:
            nxt = min((str(x.get("expires_at"))[:10] for x in dl if x.get("expires_at")), default="—")
            st.metric("Οφειλή", eur(owed), delta=(f"λήξη {nxt}" if nxt != "—" else None), delta_color="off")
        else:
            st.caption("Δεν βρέθηκε ενεργή συνδρομή.")

# Πρόγραμμα εβδομάδας με καιρό + smart heads-up
st.divider()
st.subheader("🗓️ Πρόγραμμα αυτής της εβδομάδας")
ev = data["events"] or []
if not ev:
    st.info("Δεν υπάρχουν προγραμματισμένα events αυτή την εβδομάδα.")
else:
    tips = event_tips(ev, CLAUDE_MODEL)
    for i, e in enumerate(ev):
        w = e.get("weather")
        wtxt = ""
        if w and w.get("temp") is not None:
            wtxt = f" · 🌡️ {round(float(w['temp']))}°C, {w.get('desc','')}"
            if w.get("rain_prob"):
                wtxt += f", ☔ {w['rain_prob']}%"
        loc = f" — {e.get('location_alias')}" if e.get("location_alias") else ""
        st.markdown(f"**{fmt_dt(e.get('start_date'))}** · {EVENT_TYPE.get(e.get('type'),'Άλλο')}: {e.get('title')}{loc}{wtxt}")
        if i < len(tips) and tips[i]:
            st.caption("💡 " + tips[i])
    ics = enrich.build_ics(ev, calendar_name="myTeam — Πρόγραμμα")
    st.download_button("📆 Λήψη στο ημερολόγιο (.ics, με υπενθυμίσεις)",
                       ics.encode("utf-8"), file_name="myteam_schedule.ics", mime="text/calendar")

# Follow-up κουμπάκια -> μεταφορά στο chat
st.divider()
st.markdown("##### 💬 Ρώτησε περισσότερα")
fcols = st.columns(len(PARENT_FOLLOWUPS))
for i, q in enumerate(PARENT_FOLLOWUPS):
    if fcols[i].button(q, key=f"pfu_{i}", use_container_width=True):
        st.session_state["_pending_prompt"] = q
        try:
            st.switch_page("streamlit_app.py")
        except Exception:
            st.info("Πήγαινε στη σελίδα του chat — η ερώτηση είναι έτοιμη.")
