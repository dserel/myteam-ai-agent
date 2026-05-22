-- ============================================================
-- Detector: expiring_subscriptions
-- Τρέχει: nightly
-- Επιστρέφει: ενεργές συνδρομές αθλητών που λήγουν στις
--             επόμενες 14 μέρες (για proactive renewal nudges).
--
-- Variables: {{tenant_club_id}}, {{today}}
-- ============================================================

SELECT
    su.user_id,
    CONCAT_WS(' ', u.name, u.last_name) AS athlete,
    u.email,
    u.phone,
    s.id            AS subscription_id,
    s.title         AS subscription_title,
    s.amount        AS amount,
    su.due_at       AS expires_at,
    DATEDIFF(su.due_at, {{today}}) AS days_until_expiry,
    su.total_paid,
    'expiring_subscription' AS detector_key
FROM subscription_users su
JOIN subscriptions s ON s.id = su.subscription_id
JOIN users u         ON u.id = su.user_id
WHERE s.club_id   = {{tenant_club_id}}
  AND s.status    = 1
  AND u.deleted_at IS NULL
  AND su.due_at IS NOT NULL
  AND su.due_at >= {{today}}
  AND su.due_at <  DATE_ADD({{today}}, INTERVAL 14 DAY)
ORDER BY su.due_at ASC, athlete ASC
LIMIT 200;
