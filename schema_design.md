
## Fact / Dimension Diagram

```
                    ┌─────────────────────────────┐
                    │       dim_filials            │
                    │─────────────────────────────│
                    │ PK  filial_id    INT         │
                    │     filial_name  VARCHAR(100)│
                    │     region       VARCHAR(50) │
                    │     filial_type  VARCHAR(50) │ ← почтамт / ОФ / сервис
                    │     is_active    BOOL        │
                    │     valid_from   DATE        │ SCD Type 2
                    │     valid_to     DATE        │
                    │     is_current   BOOL        │
                    └──────────────┬──────────────┘
                                   │
┌──────────────────┐               │          ┌──────────────────────────────┐
│   dim_date       │               │          │       dim_products           │
│──────────────────│               │          │──────────────────────────────│
│ PK date_id  DATE │               │          │ PK  inc_gk       BIGINT      │
│    year     INT  │               │          │     inc_name     VARCHAR(200)│
│    quarter  INT  │               │          │     kbk          VARCHAR(50) │
│    month    INT  │               │          │     product      VARCHAR(50) │ ← Посылки, EMS...
│    week     INT  │               │          │     subcategory  VARCHAR(50) │
│    is_holiday BOOL│              │          │     direction    VARCHAR(10) │ ← РК / МЖД
│    season   VARCHAR│             │          │     owner_dept   VARCHAR(50) │ ← ДРПБ, ДУПК...
└───────┬──────────┘               │          │     valid_from   DATE        │ SCD Type 2
        │                          │          │     valid_to     DATE        │
        │                          │          └──────────────┬───────────────┘
        │                          │                         │
        │              ┌───────────┴─────────────────────────┴───────────────┐
        │              │              fact_operations                         │
        └──────────────┤──────────────────────────────────────────────────────│
                       │ PK  operation_id    BIGSERIAL                        │
                       │ FK  filial_id       INT  → dim_filials               │
                       │ FK  inc_gk          BIGINT → dim_products            │
                       │ FK  operation_date  DATE → dim_date                  │
                       │     period          VARCHAR(10)  ← '2025_3'         │
                       │     sender_category VARCHAR(20)  ← Физ./Юр. лицо   │
                       │     weight          NUMERIC(10,3)                    │
                       │     cnt             INT                              │
                       │     summa           NUMERIC(18,2)                   │
                       │     loaded_at       TIMESTAMP                        │
                       │     source_file     VARCHAR(200)                     │
                       └──────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │   fact_adjustments           │ ← Slowly Changing Facts
                    │─────────────────────────────│
                    │ FK  operation_id  BIGINT     │
                    │     original_summa NUMERIC   │
                    │     adjusted_summa NUMERIC   │
                    │     reason_code    VARCHAR   │
                    │     adjusted_at    TIMESTAMP │
                    │     adjusted_by    VARCHAR   │
                    └─────────────────────────────┘
```



```sql
-- Партиция по году + месяцу
CREATE TABLE fact_operations (
    operation_id    BIGSERIAL,
    filial_id       INT           NOT NULL,
    inc_gk          BIGINT        NOT NULL,
    operation_date  DATE          NOT NULL,
    period          VARCHAR(10),
    sender_category VARCHAR(20),
    weight          NUMERIC(10,3),
    cnt             INT,
    summa           NUMERIC(18,2),
    loaded_at       TIMESTAMP DEFAULT NOW()
) PARTITION BY RANGE (operation_date);

-- Создаём партиции через pg_partman или Airflow
CREATE TABLE fact_operations_2025_03
    PARTITION OF fact_operations
    FOR VALUES FROM ('2025-03-01') TO ('2025-04-01');

CREATE TABLE fact_operations_2025_04
    PARTITION OF fact_operations
    FOR VALUES FROM ('2025-04-01') TO ('2025-05-01');

CREATE TABLE fact_operations_2025_05
    PARTITION OF fact_operations
    FOR VALUES FROM ('2025-05-01') TO ('2025-06-01');
```

## SCD (Slowly Changing Dimensions)

| Сущность | Тип SCD | Причина |
|---|---|---|
| dim_filials (name, region) | **Type 2** | Историческая точность при переименовании ОФ |
| dim_filials (is_active) | **Type 1** | Текущий статус; история не нужна |
| dim_products (product, direction) | **Type 2** | Сохранить историю классификации для правильной аналитики |
| dim_products (inc_name) | **Type 1** | Косметические правки названия |
| dim_date | **Неизменна** | Статическое измерение |

## Incremental Load (Airflow DAG pattern)

```sql
-- 
TRUNCATE TABLE stg_operations;
COPY stg_operations FROM '/data/Оперативные_данные.csv'
    CSV HEADER DELIMITER ',' ENCODING 'UTF8';

--
DELETE FROM stg_operations s
USING stg_operations s2
WHERE s.ctid > s2.ctid
  AND s.DATA = s2.DATA AND s.INC_GK = s2.INC_GK
  AND s.FILIAL = s2.FILIAL AND s.PERIOD = s2.PERIOD
  AND s.SNDRCTG = s2.SNDRCTG;

-- 
INSERT INTO fact_operations (filial_id, inc_gk, operation_date, period,
                              sender_category, weight, cnt, summa, loaded_at)
SELECT
    s.FILIAL,
    s.INC_GK,
    TO_DATE(s.DATA, 'DD.MM.YY'),
    s.PERIOD,
    s.SNDRCTG,
    s.WEIGHT,
    s.CNT,
    s.SUMMA,
    NOW()
FROM stg_operations s
ON CONFLICT (operation_date, filial_id, inc_gk, sender_category)
DO UPDATE SET
    summa     = EXCLUDED.summa,
    cnt       = EXCLUDED.cnt,
    loaded_at = NOW();

--  Refresh MV
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filial_monthly_kpi;

-- 
INSERT INTO etl_watermark (table_name, loaded_at, rows_loaded)
VALUES ('fact_operations', NOW(),
        (SELECT COUNT(*) FROM stg_operations))
ON CONFLICT (table_name) DO UPDATE
    SET loaded_at = NOW(), rows_loaded = EXCLUDED.rows_loaded;
```
