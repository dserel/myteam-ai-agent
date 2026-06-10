"""
parent_view.py
--------------
ΕΠΑΝΑΧΡΗΣΙΜΟΠΟΙΗΣΙΜΟ data layer για το parent experience («Το παιδί μου»).

Σκόπιμα UI-agnostic: καθαρές συναρτήσεις που παίρνουν έναν MetabaseClient
(ή οτιδήποτε με .run_sql(sql)->list[dict]), club_id, parent_user_id, today,
και επιστρέφουν απλά dicts/lists. Όλο το household-scoping SQL είναι
ΚΕΝΤΡΙΚΟΠΟΙΗΜΕΝΟ εδώ ώστε να μεταφερθεί εύκολα μέσα στο myTeam app
(Laravel) — αρκεί να ξαναγραφεί το ίδιο SQL ή να τυλιχτεί ως API.

ΣΗΜΕΙΩΣΕΙΣ ΜΟΝΤΕΛΟΥ (επιβεβαιωμένες σε production):
  • Οικογένεια: κοινό users.household_id (parent role=5 + αθλητής role=4).
    parent_users είναι συχνά κενό -> χρησιμοποιούμε household UNION parent_users.
  • appearance_events.check = 1 σημαίνει «παρών» (ΔΕΝ είναι 0/1 boolean).
  • events <-> teams μέσω eventables.eventable_type = 'teams'.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Runner(Protocol):
    def run_sql(self, sql: str) -> list[dict[str, Any]]: ...


def _q(today: str) -> str:
    """Quote a YYYY-MM-DD date literal for inline SQL."""
    return "'" + today.replace("'", "") + "'"


def children_subquery(parent_user_id: int) -> str:
    """Το σύνολο των user_ids των παιδιών ενός γονιού (household UNION parent_users)."""
    p = int(parent_user_id)
    return (
        f"(SELECT u2.id FROM users u2 "
        f"WHERE u2.household_id = (SELECT household_id FROM users WHERE id = {p}) "
        f"AND u2.id <> {p} AND u2.deleted_at IS NULL "
        f"UNION SELECT pu.user_id FROM parent_users pu WHERE pu.parent_id = {p})"
    )


def child_profiles(mb: _Runner, club_id: int, parent_user_id: int, today: str) -> list[dict]:
    """Κάρτα ταυτότητας ανά παιδί: ομάδα, προπονητής, ηλικία, φανέλα, μέλος από, σύνολο προπονήσεων."""
    c = int(club_id)
    sql = f"""
SELECT ath.id,
  CONCAT_WS(' ', ath.name, ath.last_name) AS athlete,
  ath.birthday,
  TIMESTAMPDIFF(YEAR, ath.birthday, {_q(today)}) AS age,
  ath.created_at AS member_since,
  ROUND(TIMESTAMPDIFF(MONTH, ath.created_at, {_q(today)}) / 12, 1) AS years_in_club,
  (SELECT t.name FROM team_user tu JOIN teams t ON t.id = tu.team_id
     WHERE tu.user_id = ath.id AND tu.deleted_at IS NULL AND t.club_id = {c} AND t.status = 1
     ORDER BY tu.team_id DESC LIMIT 1) AS team,
  (SELECT tu.number FROM team_user tu
     WHERE tu.user_id = ath.id AND tu.deleted_at IS NULL AND tu.number IS NOT NULL
     ORDER BY tu.team_id DESC LIMIT 1) AS jersey,
  (SELECT CONCAT_WS(' ', cu.name, cu.last_name)
     FROM team_user tc JOIN users cu ON cu.id = tc.user_id
     WHERE tc.team_id = (
        SELECT tu2.team_id FROM team_user tu2 JOIN teams tt ON tt.id = tu2.team_id
        WHERE tu2.user_id = ath.id AND tu2.deleted_at IS NULL AND tt.club_id = {c} AND tt.status = 1
        ORDER BY tu2.team_id DESC LIMIT 1)
       AND tc.first_coach = 1 AND tc.deleted_at IS NULL LIMIT 1) AS coach,
  (SELECT COUNT(*) FROM appearance_events ae JOIN teams t2 ON t2.id = ae.team_id
     WHERE ae.user_id = ath.id AND ae.check = 1 AND t2.club_id = {c}) AS lifetime_present
FROM users ath
WHERE ath.id IN {children_subquery(parent_user_id)}
ORDER BY athlete;
"""
    return mb.run_sql(sql)


def upcoming_events(mb: _Runner, club_id: int, parent_user_id: int, today: str, days: int = 7) -> list[dict]:
    """Επόμενες προπονήσεις/αγώνες των ομάδων του παιδιού (+ user-specific events)."""
    c = int(club_id)
    kids = children_subquery(parent_user_id)
    sql = f"""
SELECT e.start_date, e.title, e.type, e.location_alias
FROM events e
WHERE e.club_id = {c} AND e.deleted_at IS NULL
  AND e.start_date >= {_q(today)} AND e.start_date < DATE_ADD({_q(today)}, INTERVAL {int(days)} DAY)
  AND e.id IN (
    SELECT ev.event_id FROM eventables ev
    JOIN team_user tu ON tu.team_id = ev.eventable_id AND tu.deleted_at IS NULL
    WHERE ev.eventable_type = 'teams' AND tu.user_id IN {kids}
    UNION
    SELECT ev.event_id FROM eventables ev
    WHERE ev.eventable_type = 'users' AND ev.eventable_id IN {kids}
  )
ORDER BY e.start_date
LIMIT 200;
"""
    return mb.run_sql(sql)


def attendance(mb: _Runner, club_id: int, parent_user_id: int, today: str, days: int = 30) -> list[dict]:
    """Παρουσία ανά παιδί στο διάστημα (check=1 = παρών)."""
    c = int(club_id)
    sql = f"""
SELECT CONCAT_WS(' ', ath.name, ath.last_name) AS athlete,
  COUNT(*) AS sessions,
  SUM(CASE WHEN ae.check = 1 THEN 1 ELSE 0 END) AS present,
  ROUND(100.0 * SUM(CASE WHEN ae.check = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) AS attendance_pct,
  MAX(CASE WHEN ae.check = 1 THEN e.start_date END) AS last_present
FROM appearance_events ae
JOIN events e ON e.id = ae.event_id AND e.deleted_at IS NULL
JOIN teams t ON t.id = ae.team_id AND t.club_id = {c}
JOIN users ath ON ath.id = ae.user_id
WHERE ae.check IS NOT NULL AND e.start_date >= DATE_SUB({_q(today)}, INTERVAL {int(days)} DAY)
  AND ae.user_id IN {children_subquery(parent_user_id)}
GROUP BY ath.id, athlete;
"""
    return mb.run_sql(sql)


def dues(mb: _Runner, club_id: int, parent_user_id: int) -> list[dict]:
    """Συνδρομές & οφειλή (owed = amount - total_paid) ανά παιδί."""
    c = int(club_id)
    sql = f"""
SELECT CONCAT_WS(' ', ath.name, ath.last_name) AS athlete, s.title AS subscription,
  s.amount, su.due_at AS expires_at, su.total_paid,
  GREATEST(s.amount - COALESCE(su.total_paid, 0), 0) AS owed
FROM subscription_users su
JOIN subscriptions s ON s.id = su.subscription_id AND s.club_id = {c} AND s.status = 1
JOIN users ath ON ath.id = su.user_id
WHERE su.user_id IN {children_subquery(parent_user_id)}
ORDER BY su.due_at DESC
LIMIT 200;
"""
    return mb.run_sql(sql)


def milestones(profile_row: dict, today_month: int) -> list[str]:
    """Engagement στοιχεία από το προφίλ — μηδέν επιπλέον data. Επιστρέφει λίστα μηνυμάτων."""
    out: list[str] = []
    name = (profile_row.get("athlete") or "Ο αθλητής").split(" ")[0]
    bday = profile_row.get("birthday")
    if bday:
        try:
            bmonth = int(str(bday)[5:7])
            if bmonth == today_month:
                out.append(f"🎂 Ο/Η {name} έχει γενέθλια αυτόν τον μήνα!")
        except Exception:
            pass
    yic = profile_row.get("years_in_club")
    try:
        if yic is not None and float(yic) >= 1 and abs(float(yic) - round(float(yic))) < 0.1:
            out.append(f"🎉 {int(round(float(yic)))} χρόνια στον σύλλογο!")
    except Exception:
        pass
    lp = profile_row.get("lifetime_present")
    try:
        lp = int(lp or 0)
        for milestone in (200, 150, 100, 50, 25, 10):
            if lp >= milestone:
                out.append(f"💪 {lp} προπονήσεις συνολικά (ορόσημο {milestone}+).")
                break
    except Exception:
        pass
    return out
