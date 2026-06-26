#1 

WITH monthly_revenue AS (
    SELECT
        fo.FILIAL,
        df.FILIAL_NAME,
        fo.PERIOD,
        -- Числовой месяц для сортировки
        CAST(SPLIT_PART(fo.PERIOD, '_', 2) AS INT)  AS month_num,
        SUM(fo.SUMMA)                                AS monthly_rev,
        SUM(fo.CNT)                                  AS monthly_cnt
    FROM fact_operations fo
    JOIN dim_filials df ON fo.FILIAL = df.FILIAL
    GROUP BY fo.FILIAL, df.FILIAL_NAME, fo.PERIOD
),

rolling_revenue AS (
    SELECT
        FILIAL,
        FILIAL_NAME,
        PERIOD,
        month_num,
        monthly_rev,
        monthly_cnt,
        -- Rolling 3-month sum
        SUM(monthly_rev) OVER (
            PARTITION BY FILIAL
            ORDER BY month_num
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        )                                   AS rolling_3m_revenue,
        -- Previous month for MoM
        LAG(monthly_rev, 1) OVER (
            PARTITION BY FILIAL ORDER BY month_num
        )                                   AS prev_month_rev
    FROM monthly_revenue
),

ranked_filials AS (
    SELECT
        FILIAL,
        FILIAL_NAME,
        PERIOD,
        monthly_rev,
        rolling_3m_revenue,
        ROUND(
            100.0 * (monthly_rev - prev_month_rev)
            / NULLIF(prev_month_rev, 0), 2
        )                                   AS mom_change_pct,
        DENSE_RANK() OVER (
            PARTITION BY PERIOD ORDER BY monthly_rev DESC
        )                                   AS revenue_rank,
        COUNT(*) OVER (PARTITION BY PERIOD) AS total_filials
    FROM rolling_revenue
)

SELECT
    FILIAL,
    FILIAL_NAME,
    PERIOD,
    ROUND(monthly_rev / 1e6, 2)           AS revenue_mln,
    ROUND(rolling_3m_revenue / 1e6, 2)    AS rolling_3m_mln,
    mom_change_pct,
    revenue_rank,
    
    CASE
        WHEN revenue_rank <= CEIL(total_filials * 0.20)
         AND mom_change_pct > 0
        THEN TRUE
        ELSE FALSE
    END                                    AS is_star
FROM ranked_filials
ORDER BY PERIOD DESC, revenue_rank;


CREATE INDEX CONCURRENTLY idx_fact_ops_filial_period
    ON fact_operations (FILIAL, PERIOD)
    INCLUDE (SUMMA, CNT);


#2

WITH rfm_raw AS (
    SELECT
        fo.FILIAL,
        df.FILIAL_NAME,
        -- R: дни с последней операции (дата последней записи DATA)
        CURRENT_DATE - MAX(TO_DATE(fo.DATA, 'DD.MM.YY'))   AS recency_days,
        -- F: количество строк-транзакций за последние 90 дней
        COUNT(*)                                             AS frequency,
        -- M: суммарная выручка
        SUM(fo.SUMMA)                                        AS monetary
    FROM fact_operations fo
    JOIN dim_filials df ON fo.FILIAL = df.FILIAL
    WHERE TO_DATE(fo.DATA, 'DD.MM.YY') >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY fo.FILIAL, df.FILIAL_NAME
),

rfm_scored AS (
    SELECT
        FILIAL,
        FILIAL_NAME,
        recency_days,
        frequency,
        ROUND(monetary / 1e6, 2)                         AS monetary_mln,
        NTILE(5) OVER (ORDER BY recency_days ASC)        AS r_score,
        NTILE(5) OVER (ORDER BY frequency    DESC)       AS f_score,
        NTILE(5) OVER (ORDER BY monetary     DESC)       AS m_score
    FROM rfm_raw
),

rfm_segments AS (
    SELECT
        *,
        CASE
            WHEN r_score >= 4 AND f_score >= 4 AND m_score >= 4 THEN 'Champions'
            WHEN r_score >= 3 AND f_score >= 3                   THEN 'Loyal'
            WHEN r_score >= 3 AND f_score < 3                    THEN 'Potential'
            WHEN r_score < 3  AND f_score >= 3                   THEN 'At Risk'
            ELSE 'Lost'
        END AS segment
    FROM rfm_scored
)

SELECT
    segment,
    COUNT(*)                                              AS filial_count,
    STRING_AGG(FILIAL_NAME, ', ' ORDER BY monetary_mln DESC) AS filials,
    ROUND(AVG(recency_days), 0)                          AS avg_recency_days,
    ROUND(AVG(frequency), 0)                             AS avg_frequency,
    ROUND(AVG(monetary_mln), 1)                          AS avg_monetary_mln,
    ROUND(SUM(monetary_mln), 1)                          AS total_monetary_mln,
    ROUND(100.0 * SUM(monetary_mln)
          / SUM(SUM(monetary_mln)) OVER (), 1)           AS revenue_share_pct
FROM rfm_segments
GROUP BY segment
ORDER BY avg_monetary_mln DESC;


#3 

WITH product_period AS (
    SELECT
        dp.PRODUCT,
        dp.DIRECTION,
        fo.PERIOD,
        CAST(SPLIT_PART(fo.PERIOD, '_', 2) AS INT) AS month_num,
        SUM(fo.SUMMA)                               AS revenue
    FROM fact_operations fo
    JOIN dim_products dp ON fo.INC_GK = dp.INC_GK
    GROUP BY dp.PRODUCT, dp.DIRECTION, fo.PERIOD
),

growth AS (
    SELECT
        PRODUCT,
        DIRECTION,
        PERIOD,
        revenue,
        LAG(revenue) OVER (
            PARTITION BY PRODUCT, DIRECTION ORDER BY month_num
        )                                           AS prev_revenue,
        ROUND(
            100.0 * (revenue - LAG(revenue) OVER (
                PARTITION BY PRODUCT, DIRECTION ORDER BY month_num))
            / NULLIF(LAG(revenue) OVER (
                PARTITION BY PRODUCT, DIRECTION ORDER BY month_num), 0),
        2)                                          AS growth_pct
    FROM product_period
),

product_total AS (
    SELECT
        PRODUCT,
        SUM(revenue)                                AS total_rev,
        ROUND(
            100.0 * SUM(revenue)
            / SUM(SUM(revenue)) OVER (), 2
        )                                           AS rev_share_pct,
        SUM(SUM(revenue)) OVER (
            ORDER BY SUM(revenue) DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / SUM(SUM(revenue)) OVER () * 100        AS cumulative_pct,
        ROUND(AVG(growth_pct), 2)                  AS avg_growth_pct
    FROM growth
    GROUP BY PRODUCT
)

SELECT
    PRODUCT,
    ROUND(total_rev / 1e6, 1)     AS total_rev_mln,
    rev_share_pct,
    ROUND(cumulative_pct, 1)       AS cumulative_pct,
    CASE
        WHEN cumulative_pct <= 80 THEN '🏆 Парето топ-80%'
        ELSE '📌 Длинный хвост'
    END                            AS pareto_bucket,
    avg_growth_pct                 AS avg_mom_growth_pct
FROM product_total
ORDER BY total_rev DESC;




CREATE INDEX CONCURRENTLY idx_fact_ops_incgk
    ON fact_operations (INC_GK)
    INCLUDE (SUMMA, PERIOD, FILIAL);


#4

WITH monthly AS (
    SELECT
        fo.FILIAL,
        df.FILIAL_NAME,
        fo.PERIOD,
        CAST(SPLIT_PART(fo.PERIOD, '_', 2) AS INT) AS month_num,
        SUM(fo.SUMMA)                               AS rev,
        COUNT(*)                                    AS rows_cnt
    FROM fact_operations fo
    JOIN dim_filials df ON fo.FILIAL = df.FILIAL
    GROUP BY fo.FILIAL, df.FILIAL_NAME, fo.PERIOD
),

lagged AS (
    SELECT
        FILIAL,
        FILIAL_NAME,
        PERIOD,
        month_num,
        rev,
        rows_cnt,
        LAG(rev, 1) OVER w                          AS prev_1m,
        -- Среднее и стд по доступным периодам 
        AVG(rev) OVER (
            PARTITION BY FILIAL
            ORDER BY month_num
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                            AS rolling_avg,
        STDDEV(rev) OVER (
            PARTITION BY FILIAL
            ORDER BY month_num
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                            AS rolling_std
    FROM monthly
    WINDOW w AS (PARTITION BY FILIAL ORDER BY month_num)
),

anomalies AS (
    SELECT
        *,
        ROUND(100.0 * (rev - prev_1m) / NULLIF(prev_1m, 0), 2) AS mom_pct,
        -- Z-score относительно скользящего среднего
        ROUND((rev - rolling_avg) / NULLIF(rolling_std, 0), 2)  AS z_score,
        -- Коэффициент вариации по всем периодам
        ROUND(rolling_std / NULLIF(rolling_avg, 0) * 100, 1)    AS cv_pct,
        CASE
            WHEN ABS((rev - rolling_avg) / NULLIF(rolling_std, 0)) > 1.5
            THEN '⚠️ АНОМАЛИЯ'
            ELSE '✅ НОРМА'
        END                                                       AS anomaly_flag
    FROM lagged
)

SELECT
    FILIAL,
    FILIAL_NAME,
    PERIOD,
    ROUND(rev / 1e6, 2)   AS revenue_mln,
    mom_pct,
    z_score,
    cv_pct                AS volatility_cv_pct,
    anomaly_flag
FROM anomalies
ORDER BY FILIAL, month_num;
#bi



