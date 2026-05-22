-- ============================================================
-- Detector: mom_revenue (Month-over-Month revenue change)
-- Τρέχει: 1η κάθε μήνα (ή και κάθε μέρα — επιστρέφει το ίδιο)
-- Επιστρέφει: μία γραμμή με τη μεταβολή Μ vs Μ-1
--             (positive = αύξηση, negative = μείωση)
-- Threshold προειδοποίησης: |change_pct| >= 10
--
-- Variables: {{tenant_club_id}}, {{today}}
-- ============================================================

WITH month_buckets AS (
    SELECT
        DATE_FORMAT(transaction_at, '%Y-%m-01') AS month_start,
        SUM(amount) AS total_income,
        COUNT(*)    AS num_transactions
    FROM incomes
    WHERE club_id = {{tenant_club_id}}
      AND deleted_at IS NULL
      AND transaction_at >= DATE_SUB(DATE_FORMAT({{today}}, '%Y-%m-01'), INTERVAL 2 MONTH)
      AND transaction_at <  DATE_FORMAT({{today}}, '%Y-%m-01')
    GROUP BY DATE_FORMAT(transaction_at, '%Y-%m-01')
)
SELECT
    prev.month_start                AS prev_month,
    prev.total_income               AS prev_income,
    curr.month_start                AS this_month,
    curr.total_income               AS this_income,
    (curr.total_income - prev.total_income)                                  AS delta,
    ROUND(100.0 * (curr.total_income - prev.total_income) / NULLIF(prev.total_income, 0), 1) AS change_pct,
    'mom_revenue'                   AS detector_key
FROM month_buckets curr
JOIN month_buckets prev
  ON prev.month_start = DATE_SUB(curr.month_start, INTERVAL 1 MONTH)
WHERE curr.month_start = DATE_SUB(DATE_FORMAT({{today}}, '%Y-%m-01'), INTERVAL 1 MONTH);
