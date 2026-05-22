"""
scripts/introspect_myteam.py
----------------------------
One-shot schema introspection για τη myTeam DB μέσω Metabase /api/dataset.
Τρέχει INFORMATION_SCHEMA + enum/type discovery queries και βγάζει ΕΝΑ JSON
που τροφοδοτεί το rewrite του `schema_pruned.md` ώστε να σταματήσουν οι εικασίες.

Δεν αποθηκεύει credentials. Διαβάζει από env:
    MB_URL    π.χ. https://myteam.metabaseapp.com
    MB_KEY    Metabase API key (mb_...)
    MB_DBID   numeric database id στο Metabase (π.χ. 2)
    MB_CLUB   (optional) club_id για sample lookups (default 41)

Χρήση:
    export MB_URL='https://myteam.metabaseapp.com'
    export MB_KEY='mb_...'
    export MB_DBID='2'
    export MB_CLUB='41'
    python scripts/introspect_myteam.py > myteam_schema_dump.json

Μετά κάνε paste το περιεχόμενο του myteam_schema_dump.json πίσω στο chat.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("MB_URL", "").rstrip("/")
KEY = os.environ.get("MB_KEY", "")
DBID = os.environ.get("MB_DBID", "")
CLUB = os.environ.get("MB_CLUB", "41")

# Πίνακες που μας ενδιαφέρουν για το MVP context.
TABLES = [
    "clubs", "users", "teams", "team_user", "parent_users", "seasons",
    "divisions", "households", "events", "eventables", "appearance_events",
    "fields", "incomes", "outgoings", "payments", "subscriptions",
    "subscription_users", "recurring_subscriptions", "income_types",
    "expense_types", "suppliers", "posts", "post_team", "polls", "answers",
    "pollable", "notifications", "roles", "model_has_roles",
]


def _err(msg: str) -> None:
    sys.stderr.write(msg + "\n")


if not (URL and KEY and DBID):
    _err("Missing env. Set MB_URL, MB_KEY, MB_DBID (and optionally MB_CLUB).")
    sys.exit(2)


def run_sql(sql: str):
    """Returns (cols, rows) or raises with the Metabase error string."""
    payload = {"type": "native", "native": {"query": sql}, "database": int(DBID)}
    req = urllib.request.Request(
        URL + "/api/dataset",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    req.add_header("X-API-KEY", KEY)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:300]}")
    if body.get("status") == "failed":
        raise RuntimeError(str(body.get("error"))[:300])
    data = body.get("data", {})
    cols = [c["name"] for c in data.get("cols", [])]
    rows = data.get("rows", [])
    return cols, rows


def collect(sql: str):
    """Run a query; return structured dict (never raises)."""
    try:
        cols, rows = run_sql(sql)
        return {"sql": sql, "cols": cols, "row_count": len(rows), "rows": rows}
    except Exception as e:  # noqa: BLE001
        return {"sql": sql, "error": str(e)}


def main() -> None:
    in_list = "(" + ",".join(f"'{t}'" for t in TABLES) + ")"

    queries: dict[str, str] = {
        # ---- structure -------------------------------------------------
        "all_tables": (
            "SELECT TABLE_NAME, TABLE_ROWS "
            "FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"
        ),
        "columns": (
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, "
            "COLUMN_KEY, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN {in_list} "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        ),
        "foreign_keys": (
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
            "REFERENCED_COLUMN_NAME "
            "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME IN {in_list} "
            "AND REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY TABLE_NAME, COLUMN_NAME"
        ),
        # ---- enum / type distributions ---------------------------------
        "users_role": "SELECT role, COUNT(*) c FROM users GROUP BY role ORDER BY role",
        "users_status": "SELECT status, COUNT(*) c FROM users GROUP BY status ORDER BY status",
        "users_gender": "SELECT gender, COUNT(*) c FROM users GROUP BY gender ORDER BY gender",
        "events_type": "SELECT type, COUNT(*) c FROM events GROUP BY type ORDER BY type",
        "events_result": "SELECT result, COUNT(*) c FROM events GROUP BY result ORDER BY result",
        "teams_category": "SELECT category, COUNT(*) c FROM teams GROUP BY category ORDER BY category",
        "teams_status": "SELECT status, COUNT(*) c FROM teams GROUP BY status ORDER BY status",
        "teams_sport": "SELECT sport, COUNT(*) c FROM teams GROUP BY sport ORDER BY c DESC",
        "appearance_check": "SELECT `check`, COUNT(*) c FROM appearance_events GROUP BY `check` ORDER BY `check`",
        "appearance_status": "SELECT status, COUNT(*) c FROM appearance_events GROUP BY status ORDER BY status",
        "payments_method": "SELECT payment_method, COUNT(*) c FROM payments GROUP BY payment_method ORDER BY payment_method",
        "polls_status": "SELECT status, COUNT(*) c FROM polls GROUP BY status ORDER BY status",
        "subscriptions_status": "SELECT status, COUNT(*) c FROM subscriptions GROUP BY status ORDER BY status",
        "subscriptions_billing": "SELECT billing_cycle, `interval`, COUNT(*) c FROM subscriptions GROUP BY billing_cycle, `interval` ORDER BY c DESC",
        "incomes_type": "SELECT type, COUNT(*) c FROM incomes GROUP BY type ORDER BY c DESC LIMIT 50",
        "incomes_payment_type": "SELECT payment_type, COUNT(*) c FROM incomes GROUP BY payment_type ORDER BY c DESC",
        "outgoings_type": "SELECT type, COUNT(*) c FROM outgoings GROUP BY type ORDER BY c DESC LIMIT 50",
        "parent_users_status": "SELECT status, COUNT(*) c FROM parent_users GROUP BY status ORDER BY status",
        # ---- polymorphic morph maps ------------------------------------
        "eventable_type": "SELECT eventable_type, COUNT(*) c FROM eventables GROUP BY eventable_type ORDER BY c DESC",
        "pollable_type": "SELECT pollable_type, COUNT(*) c FROM pollable GROUP BY pollable_type ORDER BY c DESC",
        "model_has_roles_type": "SELECT model_type, COUNT(*) c FROM model_has_roles GROUP BY model_type ORDER BY c DESC",
        # ---- lookup table contents (grounding for answers) -------------
        "roles_rows": "SELECT id, name, club_id FROM roles ORDER BY id LIMIT 200",
        "income_types_rows": f"SELECT id, name, club_id FROM income_types WHERE club_id = {int(CLUB)} OR club_id IS NULL ORDER BY id LIMIT 100",
        "expense_types_rows": f"SELECT id, name, club_id FROM expense_types WHERE club_id = {int(CLUB)} OR club_id IS NULL ORDER BY id LIMIT 100",
        # ---- NULL-rate sanity on suspicious columns --------------------
        "team_user_created_null": "SELECT COUNT(*) total, SUM(created_at IS NULL) null_created FROM team_user",
        "sample_club": f"SELECT id, name, slug, status FROM clubs WHERE id = {int(CLUB)}",
    }

    out: dict[str, object] = {
        "_meta": {
            "db_id": DBID,
            "club_id": CLUB,
            "note": "Generated by scripts/introspect_myteam.py — paste this back to the agent.",
        }
    }
    for key, sql in queries.items():
        sys.stderr.write(f"... {key}\n")
        out[key] = collect(sql)

    json.dump(out, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
