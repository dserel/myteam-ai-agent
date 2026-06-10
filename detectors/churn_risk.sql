-- ============================================================
-- Detector: churn_risk
-- Τρέχει: nightly
-- Επιστρέφει: αθλητές με ενεργή συνδρομή που έχουν χάσει
--            >= 4 συνεχόμενες προπονήσεις (>= 30 ημέρες χωρίς παρουσία).
--
-- Threshold logic (in-SQL):
--   - Active subscription σήμερα
--   - Καμία παρουσία (check=1) στις τελευταίες 30 μέρες
--   - Είχε τουλάχιστον 1 παρουσία τις προηγούμενες 60 μέρες πριν από αυτό
--     (διαφορετικά είναι "νέος" χωρίς churn risk)
--
-- Variables: {{tenant_club_id}}, {{today}}
-- ============================================================

-- Dedupe per athlete: ένας αθλητής με πολλαπλές ενεργές συνδρομές
-- εμφανιζόταν N φορές (1 ανά subscription title) — group by user_id και
-- συγχωνεύουμε τους τίτλους σε ' / '-separated string ώστε να μένει
-- 1 row/athlete στον τελικό πίνακα κινδύνου.
WITH active_athletes AS (
    SELECT u.id AS user_id,
           u.name, u.last_name, u.email, u.phone,
           GROUP_CONCAT(DISTINCT s.title ORDER BY s.title SEPARATOR ' / ') AS subscription_title
    FROM users u
    JOIN subscription_users su ON su.user_id = u.id
    JOIN subscriptions s       ON s.id = su.subscription_id
    WHERE s.club_id = {{tenant_club_id}}
      AND s.status  = 1
      AND u.deleted_at IS NULL
      AND su.subscription_at <= {{today}}
      AND (su.due_at IS NULL OR su.due_at >= {{today}})
    GROUP BY u.id, u.name, u.last_name, u.email, u.phone
),
recent_attendance AS (
    SELECT ae.user_id,
           MAX(e.start_date) AS last_present_at,
           COUNT(*)          AS attendance_30d
    FROM appearance_events ae
    JOIN events e ON e.id = ae.event_id AND e.deleted_at IS NULL
    JOIN teams t  ON t.id = ae.team_id
    WHERE t.club_id = {{tenant_club_id}}
      AND ae.check  = 1
      AND e.start_date >= DATE_SUB({{today}}, INTERVAL 30 DAY)
      AND e.start_date <  {{today}}
    GROUP BY ae.user_id
),
prior_attendance AS (
    SELECT ae.user_id, COUNT(*) AS attendance_60d_prior
    FROM appearance_events ae
    JOIN events e ON e.id = ae.event_id AND e.deleted_at IS NULL
    JOIN teams t  ON t.id = ae.team_id
    WHERE t.club_id = {{tenant_club_id}}
      AND ae.check  = 1
      AND e.start_date >= DATE_SUB({{today}}, INTERVAL 90 DAY)
      AND e.start_date <  DATE_SUB({{today}}, INTERVAL 30 DAY)
    GROUP BY ae.user_id
)
SELECT
    a.user_id,
    CONCAT_WS(' ', a.name, a.last_name) AS athlete,
    a.email,
    a.phone,
    a.subscription_title,
    COALESCE(p.attendance_60d_prior, 0) AS attended_30_to_90d_ago,
    COALESCE(r.attendance_30d, 0)        AS attended_last_30d,
    (SELECT MAX(e2.start_date)
       FROM appearance_events ae2
       JOIN events e2 ON e2.id = ae2.event_id
       JOIN teams t2  ON t2.id = ae2.team_id
      WHERE ae2.user_id = a.user_id
        AND ae2.check   = 1
        AND t2.club_id  = {{tenant_club_id}}
    ) AS last_present_at,
    'churn_risk' AS detector_key
FROM active_athletes a
LEFT JOIN recent_attendance r ON r.user_id = a.user_id
LEFT JOIN prior_attendance  p ON p.user_id = a.user_id
WHERE COALESCE(r.attendance_30d, 0) = 0
  AND COALESCE(p.attendance_60d_prior, 0) >= 1
ORDER BY p.attendance_60d_prior DESC
LIMIT 100;
