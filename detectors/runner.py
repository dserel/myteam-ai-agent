"""
detectors/runner.py
-------------------
Cron-friendly runner που:
  1. Βρίσκει όλα τα *.sql στον φάκελο detectors/
  2. Για κάθε ενεργό club, τα τρέχει με {{tenant_club_id}} και {{today}}
  3. Γράφει τα ευρήματα σε πίνακα `agent_insights`
     (σχήμα προτεινόμενο παρακάτω — δημιούργησέ τον στη myTeam DB)

Σχήμα `agent_insights` (DDL που τρέχεις 1 φορά):

    CREATE TABLE agent_insights (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        club_id BIGINT UNSIGNED NOT NULL,
        detector_key VARCHAR(64) NOT NULL,
        payload JSON NOT NULL,
        severity TINYINT UNSIGNED NOT NULL DEFAULT 1,  -- 1=info, 2=warn, 3=alert
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TIMESTAMP NULL,
        INDEX idx_club_created (club_id, created_at),
        INDEX idx_detector (detector_key)
    );

Εκτέλεση:
    python -m detectors.runner [--today 2026-05-20] [--club 42]

Cron (crontab):
    15 3 * * *  cd /opt/myteam-ai-agent && /usr/bin/python -m detectors.runner
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from src import metabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("detectors")

DETECTORS_DIR = Path(__file__).resolve().parent

# Severity ανά detector. Επεξεργάσιμο.
SEVERITY = {
    "churn_risk":               3,  # alert
    "mom_revenue":              2,  # warn
    "expiring_subscriptions":   2,  # warn
}


def list_detectors() -> list[Path]:
    return sorted(p for p in DETECTORS_DIR.glob("*.sql"))


def active_club_ids() -> list[int]:
    rows = metabase.run_sql(
        "SELECT id FROM clubs WHERE deleted_at IS NULL AND status = 2 LIMIT 1000;"
        # NOTE: clubs.status=2 means active per schema default. Επιβεβαίωσε.
    )
    return [r["id"] for r in rows]


def render(sql_template: str, *, club_id: int, today: str) -> str:
    return (
        sql_template
        .replace("{{tenant_club_id}}", str(club_id))
        .replace("{{today}}", f"'{today}'")
    )


def insert_insight(*, club_id: int, detector_key: str, payload: dict, severity: int) -> None:
    """Γράφει 1 insight row. Χρησιμοποιεί ad-hoc SQL για INSERT — αυτό απαιτεί
    Metabase user με write permissions στο agent_insights table, ή κάνε το
    με ξεχωριστό DB connection (recommended)."""
    # Το /api/dataset του Metabase είναι read-only.
    # Για write, χρησιμοποίησε ξεχωριστό MySQL connection (mysql.connector / sqlalchemy).
    # Για το MVP, log only:
    log.info("INSIGHT club=%s key=%s sev=%s payload=%s",
             club_id, detector_key, severity,
             json.dumps(payload, ensure_ascii=False, default=str)[:300])


def run_for_club(club_id: int, today: str) -> None:
    log.info("Running %d detectors for club=%s today=%s", len(list_detectors()), club_id, today)
    for sql_path in list_detectors():
        key = sql_path.stem
        sev = SEVERITY.get(key, 1)
        try:
            sql = render(sql_path.read_text(encoding="utf-8"), club_id=club_id, today=today)
            rows = metabase.run_sql(sql)
        except Exception as e:
            log.warning("detector %s failed for club=%s: %s", key, club_id, e)
            continue

        if not rows:
            log.info("club=%s detector=%s: no findings", club_id, key)
            continue

        # Για insights που επιστρέφουν λίστα rows (π.χ. churn_risk),
        # κάνε aggregated insight, όχι μία γραμμή ανά athlete.
        payload = {"rows": rows, "count": len(rows)}

        # Custom thresholds για συγκεκριμένους detectors
        if key == "mom_revenue":
            row = rows[0]
            change = row.get("change_pct")
            if change is None or abs(change) < 10:
                log.info("mom_revenue: change=%s%% — below threshold, skipping", change)
                continue

        insert_insight(club_id=club_id, detector_key=key, payload=payload, severity=sev)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", default=date.today().isoformat(), help="YYYY-MM-DD")
    ap.add_argument("--club", type=int, help="Run for one club only")
    args = ap.parse_args()

    clubs = [args.club] if args.club else active_club_ids()
    log.info("clubs to process: %s", clubs)
    for cid in clubs:
        try:
            run_for_club(cid, args.today)
        except Exception as e:
            log.exception("club %s failed", cid)


if __name__ == "__main__":
    main()
