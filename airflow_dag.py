

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from airflow import DAG
from airflow.models import Variable
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.utils.email import send_email
from airflow.utils.trigger_rule import TriggerRule

logger = logging.getLogger(__name__)


DEFAULT_ARGS: dict[str, Any] = {
    "owner": "data_team",
    "depends_on_past": False,
    "email": ["@"],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=60),
    "sla": timedelta(hours=4),
    "execution_timeout": timedelta(hours=2),
}


POSTGRES_CONN_ID = "postg"
SOURCE_DATA_PATH = Variable.get("kh", default_var="")
MIN_EXPECTED_ROWS = 10_000 
MAX_DUPLICATE_PCT = 1.0       




def check_source_files(**context: Any) -> str:
    
    data_path = Path(SOURCE_DATA_PATH)
    required_files = [
        "Оперативные_данные.csv",
        "Справочник_филиалов.xlsx",
        "Справочник_продуктов.xlsx",
    ]
    missing = [f for f in required_files if not (data_path / f).exists()]

    if missing:
        logger.error("Missing source files: %s", missing)
        context["ti"].xcom_push(key="missing_files", value=missing)
        return "alert_missing_files"

    logger.info("All source files present: %s", required_files)
    return "validate_data_quality"


def validate_data_quality(**context: Any) -> None:
   
    data_path = Path(SOURCE_DATA_PATH)
    df = pd.read_csv(data_path / "Оперативные_данные.csv")

    report: dict[str, Any] = {
        "total_rows": len(df),
        "checked_at": datetime.utcnow().isoformat(),
    }

    
    if len(df) < MIN_EXPECTED_ROWS:
        raise ValueError(
            f"Data quality FAIL: only {len(df)} rows, expected >= {MIN_EXPECTED_ROWS}"
        )

    # Required columns
    required_cols = {"DATA", "INC_GK", "INC_NAME", "WEIGHT", "CNT", "SUMMA", "FILIAL", "PERIOD", "SNDRCTG"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing columns: {missing_cols}")

    #No nulls in key columns
    null_counts = df[["INC_GK", "SUMMA", "FILIAL", "PERIOD"]].isnull().sum()
    if null_counts.any():
        raise ValueError(f"Null values in key columns: {null_counts[null_counts > 0].to_dict()}")

    
    neg_rev = (df["SUMMA"] < 0).sum()
    if neg_rev > 0:
        raise ValueError(f"{neg_rev} rows with negative SUMMA — data integrity issue")

    #Duplicate check
    dup_count = df.duplicated().sum()
    dup_pct = dup_count / len(df) * 100
    report["duplicate_rows"] = int(dup_count)
    report["duplicate_pct"] = round(dup_pct, 2)
    if dup_pct > MAX_DUPLICATE_PCT:
        logger.warning("High duplicate rate: %.2f%% (%d rows)", dup_pct, dup_count)
        

    report["status"] = "PASSED"
    context["ti"].xcom_push(key="quality_report", value=report)
    logger.info("Data quality PASSED: %s", report)


def load_to_staging(**context: Any) -> dict[str, int]:
   
    data_path = Path(r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source")
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    df = pd.read_csv(data_path / "opdata.csv")
    filials = pd.read_excel(data_path / "spfilв.xlsx")
    products = pd.read_excel(data_path / "sprpod.xlsx")


    before = len(df)
    df = df.drop_duplicates()
    after = len(df)
    logger.info("Deduplication: %d → %d rows (removed %d)", before, after, before - after)

   
    hook.run("TRUNCATE TABLE stg_operations;")

    
    import io
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)

    conn = hook.get_conn()
    cur = conn.cursor()
    cur.copy_expert(
        "COPY stg_operations (DATA, INC_GK, INC_NAME, WEIGHT, CNT, SUMMA, FILIAL, PERIOD, SNDRCTG) FROM STDIN CSV",
        buf,
    )
    conn.commit()
    cur.close()

    
    buf2 = io.StringIO()
    filials.to_csv(buf2, index=False, header=False)
    buf2.seek(0)
    cur2 = conn.cursor()
    hook.run("TRUNCATE TABLE stg_filials;")
    cur2.copy_expert("COPY stg_filials (FILIAL, FILIAL_NAME) FROM STDIN CSV", buf2)
    conn.commit()
    cur2.close()
    conn.close()

    rows = {"operations": after, "filials": len(filials), "products": len(products)}
    context["ti"].xcom_push(key="load_stats", value=rows)
    logger.info("Staging load complete: %s", rows)
    return rows


def upsert_to_dwh(**context: Any) -> None:
    
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)


    scd2_sql = """
        -- Close old records where filial_name changed
        UPDATE dim_filials df
        SET valid_to = CURRENT_DATE - 1, is_current = FALSE
        FROM stg_filials sf
        WHERE df.filial_id = sf.FILIAL
          AND df.filial_name <> sf.FILIAL_NAME
          AND df.is_current = TRUE;

        -- Insert new/changed records
        INSERT INTO dim_filials (filial_id, filial_name, valid_from, valid_to, is_current)
        SELECT
            sf.FILIAL,
            sf.FILIAL_NAME,
            CURRENT_DATE,
            '9999-12-31',
            TRUE
        FROM stg_filials sf
        LEFT JOIN dim_filials df
            ON sf.FILIAL = df.filial_id AND df.is_current = TRUE
        WHERE df.filial_id IS NULL OR df.filial_name <> sf.FILIAL_NAME;
    """
    hook.run(scd2_sql)

    # Upsert fact table
    upsert_sql = """
        INSERT INTO fact_operations
            (filial_id, inc_gk, operation_date, period, sender_category, weight, cnt, summa, loaded_at)
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
            loaded_at = NOW()
        WHERE fact_operations.summa <> EXCLUDED.summa;
    """
    hook.run(upsert_sql)
    logger.info("DWH upsert complete")


def run_data_quality_post_load(**context: Any) -> None:
    
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)

    checks = {
        "orphaned_filials": """
            SELECT COUNT(*) FROM fact_operations fo
            LEFT JOIN dim_filials df ON fo.filial_id = df.filial_id
            WHERE df.filial_id IS NULL
        """,
        "orphaned_products": """
            SELECT COUNT(*) FROM fact_operations fo
            LEFT JOIN dim_products dp ON fo.inc_gk = dp.inc_gk
            WHERE dp.inc_gk IS NULL
        """,
        "zero_revenue_rows": """
            SELECT COUNT(*) FROM fact_operations WHERE summa = 0
        """,
        "freshness_hours": """
            SELECT EXTRACT(EPOCH FROM (NOW() - MAX(loaded_at))) / 3600
            FROM fact_operations
        """,
    }

    results = {}
    for check_name, sql in checks.items():
        val = hook.get_first(sql)[0]
        results[check_name] = float(val) if val is not None else 0
        logger.info("DQ check '%s': %s", check_name, val)

    # Fail if orphans exist
    if results["orphaned_filials"] > 0:
        raise ValueError(f"Post-load FAIL: {results['orphaned_filials']} orphaned filial records")
    if results["orphaned_products"] > 100:
        raise ValueError(f"Post-load FAIL: {results['orphaned_products']} orphaned product records")

    context["ti"].xcom_push(key="post_load_checks", value=results)
    logger.info("Post-load DQ PASSED: %s", results)


def send_success_alert(**context: Any) -> None:
    """Send success notification with key metrics."""
    load_stats = context["ti"].xcom_pull(key="load_stats", task_ids="load_to_staging") or {}
    quality = context["ti"].xcom_pull(key="quality_report", task_ids="validate_data_quality") or {}

    body = f"""
    ✅ Казпочта ETL Pipeline — УСПЕШНО
    
    Дата запуска: {context['logical_date']}
    Строк загружено: {load_stats.get('operations', 'N/A')}
    Дубликатов удалено: {quality.get('duplicate_rows', 'N/A')}
    Статус DQ: {quality.get('status', 'N/A')}
    
    Ссылка на DAG: {context['task_instance'].log_url}
    """
    logger.info("Pipeline completed successfully: %s", body)
    # In production: send_email(to=["team@kazpost.kz"], subject="ETL Success", html_content=body)


def alert_missing_files(**context: Any) -> None:
    """Alert when source files are missing."""
    missing = context["ti"].xcom_pull(key="missing_files", task_ids="check_source_files") or []
    msg = f"❌ Отсутствуют файлы: {missing}"
    logger.error(msg)
    raise FileNotFoundError(msg)


with DAG(
    dag_id="kazpost_etl_pipeline",
    description="Казпочта: ежемесячная загрузка оперативных данных в DWH",
    default_args=DEFAULT_ARGS,
    schedule_interval="0 6 1 * *",   # 06:00 первого числа каждого месяца
    start_date=datetime(2025, 3, 1),
    catchup=False,
    max_active_runs=1,
    tags=["kazpost", "etl", "production"],
    doc_md=__doc__,
) as dag:

    start = EmptyOperator(task_id="start")

    check_files = BranchPythonOperator(
        task_id="check_source_files",
        python_callable=check_source_files,
    )

    alert_files = PythonOperator(
        task_id="alert_missing_files",
        python_callable=alert_missing_files,
    )

    validate_dq = PythonOperator(
        task_id="validate_data_quality",
        python_callable=validate_data_quality,
    )

    load_staging = PythonOperator(
        task_id="load_to_staging",
        python_callable=load_to_staging,
    )

    upsert_dwh = PythonOperator(
        task_id="upsert_to_dwh",
        python_callable=upsert_to_dwh,
    )

    post_load_dq = PythonOperator(
        task_id="post_load_dq_checks",
        python_callable=run_data_quality_post_load,
    )

    refresh_mv = PostgresOperator(
        task_id="refresh_materialized_views",
        postgres_conn_id=POSTGRES_CONN_ID,
        sql="REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filial_monthly_kpi;",
    )

    update_watermark = PostgresOperator(
        task_id="update_etl_watermark",
        postgres_conn_id=POSTGRES_CONN_ID,
        sql="""
            INSERT INTO etl_watermark (table_name, loaded_at)
            VALUES ('fact_operations', NOW())
            ON CONFLICT (table_name) DO UPDATE SET loaded_at = NOW();
        """,
    )

    success_alert = PythonOperator(
        task_id="send_success_alert",
        python_callable=send_success_alert,
        trigger_rule=TriggerRule.ALL_SUCCESS,
    )

    end = EmptyOperator(
        task_id="end",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

   
    start >> check_files >> [alert_files, validate_dq]
    alert_files >> end
    (
        validate_dq
        >> load_staging
        >> upsert_dwh
        >> post_load_dq
        >> refresh_mv
        >> update_watermark
        >> success_alert
        >> end
    )
