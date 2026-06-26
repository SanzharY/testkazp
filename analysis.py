

import logging
import json
import subprocess
import sys
import unittest
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, date

import numpy as np
import pandas as pd
from pydantic import BaseModel, validator, Field
from sklearn.ensemble import RandomForestRegressor, IsolationForest
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import cross_val_score, KFold
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from scipy import stats
from statsmodels.tsa.seasonal import seasonal_decompose


# Logging 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("analysis.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("postal_analysis")



# Pydantic 

class RevenueRecord(BaseModel):
    """Validates a single monthly revenue record for a branch."""

    filial_id: int = Field(..., gt=0, description="Branch identifier")
    filial_name: str = Field(..., min_length=1)
    service_type: str = Field(..., min_length=1)
    period: str = Field(..., description="YYYY-MM format")
    revenue: float = Field(..., ge=0, description="Revenue in currency units")
    operations_count: int = Field(..., ge=0)
    region: Optional[str] = None

    @validator("period")
    def validate_period(cls, v: str) -> str:  # noqa: N805
        try:
            datetime.strptime(v, "%Y-%m")
        except ValueError:
            raise ValueError("period must be YYYY-MM")
        return v

    @validator("revenue")
    def revenue_not_nan(cls, v: float) -> float:  # noqa: N805
        if np.isnan(v):
            raise ValueError("revenue must not be NaN")
        return v



# Data Quality 

@dataclass
class DataQualityReport:
    

    total_rows: int = 0
    valid_rows: int = 0
    duplicate_rows: int = 0
    missing_by_column: dict = field(default_factory=dict)
    outlier_indices: list = field(default_factory=list)
    numeric_profile: dict = field(default_factory=dict)
    validation_errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "duplicate_rows": self.duplicate_rows,
            "missing_by_column": self.missing_by_column,
            "outlier_count": len(self.outlier_indices),
            "numeric_profile": self.numeric_profile,
            "validation_error_count": len(self.validation_errors),
            "validation_errors_sample": self.validation_errors[:5],
        }





class DataLoader:
    

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.raw_df: Optional[pd.DataFrame] = None
        self.clean_df: Optional[pd.DataFrame] = None
        self.quality_report = DataQualityReport()
        logger.info("DataLoader initialised for '%s'", filepath)

    def load(self) -> pd.DataFrame:
        
        try:
            if self.filepath.endswith(".csv"):
                self.raw_df = pd.read_csv(self.filepath)
            elif self.filepath.endswith((".xlsx", ".xls")):
                self.raw_df = pd.read_excel(self.filepath)
            else:
                raise ValueError(f"Unsupported file type: {self.filepath}")
            logger.info("Loaded %d rows from '%s'", len(self.raw_df), self.filepath)
            return self.raw_df
        except Exception as exc:
            logger.error("Failed to load data: %s", exc)
            raise

    def _generate_sample_data(self, n: int = 500) -> pd.DataFrame:
        
        rng = np.random.default_rng(42)
        filials = [f"F{i:03d}" for i in range(1, 11)]
        services = ["Parcel", "Express", "Letter", "Cargo", "Insurance"]
        periods = pd.date_range("2022-01", periods=24, freq="MS").strftime("%Y-%m").tolist()

        records = []
        for _ in range(n):
            filial = rng.choice(filials)
            service = rng.choice(services)
            period = rng.choice(periods)
            # seasonal multiplier
            month = int(period.split("-")[1])
            seasonal = 1 + 0.3 * np.sin(2 * np.pi * (month - 1) / 12)
            base = rng.uniform(5000, 50000)
            records.append(
                {
                    "filial_id": int(filial[1:]),
                    "filial_name": f"Branch_{filial}",
                    "service_type": service,
                    "period": period,
                    "revenue": round(base * seasonal * rng.uniform(0.8, 1.2), 2),
                    "operations_count": int(rng.integers(50, 2000)),
                    "region": rng.choice(["North", "South", "East", "West"]),
                }
            )
        # inject a few outliers
        for i in range(5):
            records[i]["revenue"] = rng.uniform(500_000, 1_000_000)
        # inject duplicates
        records += records[:3]
        df = pd.DataFrame(records)
        logger.info("Generated %d synthetic records", len(df))
        return df

    def validate(self, df: pd.DataFrame) -> DataQualityReport:
       
        report = DataQualityReport()
        report.total_rows = len(df)

       
        dup_mask = df.duplicated()
        report.duplicate_rows = int(dup_mask.sum())
        df = df[~dup_mask].reset_index(drop=True)
        logger.info("Removed %d duplicate rows", report.duplicate_rows)

        report.missing_by_column = df.isnull().sum().to_dict()
        errors = []
        valid_indices = []
        for idx, row in df.iterrows():
            try:
                RevenueRecord(**row.to_dict())
                valid_indices.append(idx)
            except Exception as exc:
                errors.append({"row": int(idx), "error": str(exc)})
        report.valid_rows = len(valid_indices)
        report.validation_errors = errors
        df = df.loc[valid_indices].reset_index(drop=True)
        logger.info(
            "Validation: %d valid / %d total; %d errors",
            report.valid_rows,
            report.total_rows,
            len(errors),
        )

        
        z_scores = np.abs(stats.zscore(df["revenue"].fillna(0)))
        report.outlier_indices = list(np.where(z_scores > 3)[0])
        logger.info("Outliers detected (Z > 3): %d", len(report.outlier_indices))

        
        numeric_cols = df.select_dtypes(include=np.number).columns.tolist()
        profile = {}
        for col in numeric_cols:
            s = df[col].dropna()
            profile[col] = {
                "min": float(s.min()),
                "max": float(s.max()),
                "mean": float(s.mean()),
                "median": float(s.median()),
                "std": float(s.std()),
                "q25": float(s.quantile(0.25)),
                "q75": float(s.quantile(0.75)),
            }
        report.numeric_profile = profile

        self.clean_df = df
        self.quality_report = report
        return report



# Feature Engineering

class FeatureEngineer:
    

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        logger.info("FeatureEngineer initialised with %d rows", len(self.df))

    def add_temporal_features(self) -> "FeatureEngineer":
    
        
        def _period_to_date(p: str) -> str:
            if "_" in p:
                year, month = p.split("_")
                return f"{year}-{int(month):02d}-01"
            return p + "-01"   

        self.df["period_dt"] = pd.to_datetime(self.df["period"].apply(_period_to_date))
        self.df["month"] = self.df["period_dt"].dt.month
        self.df["quarter"] = self.df["period_dt"].dt.quarter
        self.df["year"] = self.df["period_dt"].dt.year
        self.df["is_weekend_start"] = self.df["period_dt"].dt.dayofweek >= 5
        logger.info("Temporal features added")
        return self

    def add_interaction_features(self) -> "FeatureEngineer":
        
        monthly_total = (
            self.df.groupby("period")["revenue"].transform("sum")
        )
        self.df["revenue_share"] = self.df["revenue"] / monthly_total.replace(0, np.nan)

        filial_monthly = (
            self.df.groupby(["filial_id", "period"])["revenue"].transform("sum")
        )
        self.df["revenue_per_filial"] = filial_monthly

        
        hhi = (
            self.df.groupby("period")["revenue_share"]
            .apply(lambda x: (x ** 2).sum())
            .rename("concentration_ratio")
        )
        self.df = self.df.merge(hhi, on="period", how="left")
        logger.info("Interaction features added")
        return self

    def normalize(self, cols: list[str]) -> "FeatureEngineer":
        scaler = StandardScaler()
        self.df[[f"{c}_scaled" for c in cols]] = scaler.fit_transform(
            self.df[cols].fillna(0)
        )
        logger.info("Normalised columns: %s", cols)
        return self

    def build(self) -> pd.DataFrame:
        return self.df




class RevenuePredictor:


    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()
        self.model = RandomForestRegressor(n_estimators=100, random_state=42)
        self.scaler = StandardScaler()
        self.metrics: dict = {}
        logger.info("RevenuePredictor initialised")

    def _prepare_features(self) -> tuple[np.ndarray, np.ndarray]:
       
        agg = (
            self.df.groupby(["filial_id", "period", "month", "quarter", "year"])
            ["revenue"]
            .sum()
            .reset_index()
            .sort_values(["filial_id", "period"])
        )

    
        n_periods = agg.groupby("filial_id")["period"].nunique().min()
        n_lags = min(n_periods - 1, 3)   # нужен хотя бы 1 период после лага

        if n_lags < 1:
            raise ValueError(
                f"Недостаточно периодов для прогноза: {n_periods}. Нужно минимум 2."
            )

        logger.info("RevenuePredictor: %d периодов → используем %d лаг(ов)", n_periods, n_lags)

        agg["revenue_lag1"] = agg.groupby("filial_id")["revenue"].shift(1)
        feature_cols = ["filial_id", "month", "quarter", "year", "revenue_lag1"]

        if n_lags >= 2:
            agg["revenue_lag2"] = agg.groupby("filial_id")["revenue"].shift(2)
            feature_cols.append("revenue_lag2")
        if n_lags >= 3:
            agg["revenue_lag3"] = agg.groupby("filial_id")["revenue"].shift(3)
            feature_cols.append("revenue_lag3")

        agg = agg.dropna(subset=feature_cols)

        if len(agg) == 0:
            raise ValueError(
            
                f"Периодов в данных: {n_periods}, лагов: {n_lags}."
            )

        self._feature_cols = feature_cols   # сохраняем для отчёта
        X = agg[feature_cols].values
        y = agg["revenue"].values
        return X, y

    def train_evaluate(self) -> dict:
        X, y = self._prepare_features()
        X_scaled = self.scaler.fit_transform(X)

        n = len(X)
        if n < 10:
            from sklearn.model_selection import LeaveOneOut
            cv = LeaveOneOut()
            cv_name = "LeaveOneOut"
        else:
            n_splits = min(5, n)
            cv = KFold(n_splits=n_splits, shuffle=True, random_state=42)
            cv_name = f"KFold(n_splits={n_splits})"

        logger.info("CV стратегия: %s на %d строках", cv_name, n)

        r2_scores = cross_val_score(self.model, X_scaled, y, cv=cv, scoring="r2")
        neg_mae   = cross_val_score(self.model, X_scaled, y, cv=cv,
                                    scoring="neg_mean_absolute_error")
        neg_rmse  = cross_val_score(self.model, X_scaled, y, cv=cv,
                                    scoring="neg_root_mean_squared_error")

        self.model.fit(X_scaled, y)

        feature_names = getattr(self, "_feature_cols",
                                ["filial_id", "month", "quarter", "year", "revenue_lag1"])

        self.metrics = {
            "model":      "RandomForestRegressor",
            "cv_method":  cv_name,
            "n_samples":  n,
            "features":   feature_names,
            "r2_mean":    round(float(r2_scores.mean()), 4),
            "r2_std":     round(float(r2_scores.std()),  4),
            "mae_mean":   round(float(-neg_mae.mean()),  2),
            "mae_std":    round(float(neg_mae.std()),    2),
            "rmse_mean":  round(float(-neg_rmse.mean()), 2),
            "rmse_std":   round(float(neg_rmse.std()),   2),
            "feature_importances": dict(
                zip(feature_names,
                    [round(float(f), 4) for f in self.model.feature_importances_])
            ),
        }
        logger.info(
            "Model trained — R²=%.4f, MAE=%.0f, RMSE=%.0f",
            self.metrics["r2_mean"],
            self.metrics["mae_mean"],
            self.metrics["rmse_mean"],
        )
        return self.metrics





class PostalAnalysisPipeline:
    

    def __init__(self, filepath: Optional[str] = None) -> None:
        self.filepath = filepath
        self.loader = DataLoader(filepath or "synthetic")
        logger.info("Pipeline created")

    def run(self) -> dict:
        logger.info("=== Pipeline START ===")

        # Load data
        if self.filepath:
            df_raw = self.loader.load()
        else:
            df_raw = self.loader._generate_sample_data(500)

        #Validate
        quality_report = self.loader.validate(df_raw)
        df_clean = self.loader.clean_df

        # Feature engineering
        fe = (
            FeatureEngineer(df_clean)
            .add_temporal_features()
            .add_interaction_features()
            .normalize(["revenue", "operations_count"])
        )
        df_features = fe.build()

        # EDA
        eda = EDAAnalyser(df_features)
        eda.correlation_matrix()
        eda.detect_anomalies()
        eda.cluster_filials()
        eda.seasonal_decomposition(filial_id=1)
        eda_md = eda.generate_insights_md()

        # Model
        predictor = RevenuePredictor(df_features)
        model_metrics = predictor.train_evaluate()

        logger.info("=== Pipeline DONE ===")
        return {
            "quality_report": quality_report.to_dict(),
            "eda_insights_md": eda_md,
            "model_metrics": model_metrics,
        }



# Unit Tests

class TestDataValidation(unittest.TestCase):


    def setUp(self) -> None:
        self.loader = DataLoader("synthetic")
        self.df = self.loader._generate_sample_data(100)

    def test_pydantic_valid_record(self) -> None:
        record = RevenueRecord(
            filial_id=1,
            filial_name="Branch_F001",
            service_type="Parcel",
            period="2023-06",
            revenue=12345.67,
            operations_count=200,
            region="North",
        )
        self.assertEqual(record.filial_id, 1)

    def test_pydantic_invalid_period(self) -> None:
        with self.assertRaises(Exception):
            RevenueRecord(
                filial_id=1,
                filial_name="Branch_F001",
                service_type="Parcel",
                period="06-2023",  
                revenue=100.0,
                operations_count=10,
            )

    def test_pydantic_negative_revenue(self) -> None:
        with self.assertRaises(Exception):
            RevenueRecord(
                filial_id=1,
                filial_name="B",
                service_type="X",
                period="2023-01",
                revenue=-500.0,
                operations_count=5,
            )

    def test_duplicate_removal(self) -> None:
        report = self.loader.validate(self.df)
        self.assertGreater(report.duplicate_rows, 0, "Expected injected duplicates")

    def test_feature_engineering_columns(self) -> None:
        report = self.loader.validate(self.df)
        clean = self.loader.clean_df
        fe = (
            FeatureEngineer(clean)
            .add_temporal_features()
            .add_interaction_features()
        )
        result = fe.build()
        for col in ("month", "quarter", "year", "revenue_per_filial", "concentration_ratio"):
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_model_metrics_keys(self) -> None:
        report = self.loader.validate(self.df)
        clean = self.loader.clean_df
        fe = (
            FeatureEngineer(clean)
            .add_temporal_features()
            .add_interaction_features()
            .normalize(["revenue", "operations_count"])
        )
        df_features = fe.build()
        predictor = RevenuePredictor(df_features)
        metrics = predictor.train_evaluate()
        for key in ("r2_mean", "mae_mean", "rmse_mean"):
            self.assertIn(key, metrics)
            self.assertIsInstance(metrics[key], float)



class KazpostRealDataPipeline:
    

    def __init__(
        self,
        ops_path: str,
        filials_path: str,
        products_path: str,
    ) -> None:
        self.ops_path      = ops_path
        self.filials_path  = filials_path
        self.products_path = products_path
        logger.info("KazpostRealDataPipeline initialised")

    def load_and_merge(self) -> pd.DataFrame:
        df       = pd.read_csv(r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\opdata.csv")
        filials  = pd.read_excel(r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\spfil.xlsx")
        products = pd.read_excel(r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\spprod.xlsx")

        logger.info(
            "Loaded | ops=%d | filials=%d | products=%d",
            len(df), len(filials), len(products),
        )

        before = len(df)
        df = df.drop_duplicates()
        logger.info("Duplicates removed: %d", before - len(df))

        df = df.merge(filials, on="FILIAL", how="left")
        df = df.merge(
            products[["Счет ГК", "Продукт", "Направление"]],
            left_on="INC_GK",
            right_on="Счет ГК",
            how="left",
        )
        return df

    def profile(self, df: pd.DataFrame) -> dict:
        result = {
            "total_rows":      len(df),
            "periods":         sorted(df["PERIOD"].unique().tolist()),
            "unique_filials":  int(df["FILIAL"].nunique()),
            "unique_products": int(df["INC_GK"].nunique()),
            "summa_stats": {
                "min":    float(df["SUMMA"].min()),
                "max":    float(df["SUMMA"].max()),
                "mean":   float(df["SUMMA"].mean()),
                "median": float(df["SUMMA"].median()),
                "std":    float(df["SUMMA"].std()),
            },
            "missing": df.isnull().sum().to_dict(),
        }
        logger.info("Data profile: %d rows, %d filials", result["total_rows"], result["unique_filials"])
        return result

    def run(self) -> dict:
       
        logger.info("=== KazpostRealDataPipeline START ===")
        df = self.load_and_merge()
        quality_report = self.profile(df) 
        df = df.rename(columns={
            "FILIAL":      "filial_id",
            "FILIAL_NAME": "filial_name",
            "Продукт":     "service_type",
            "PERIOD":      "period",
            "SUMMA":       "revenue",
            "CNT":         "operations_count",
            "Направление": "region",
        })

        fe = (
            FeatureEngineer(df)
            .add_temporal_features()
            .add_interaction_features()
            .normalize(["revenue", "operations_count"])
        )
        df_features = fe.build()
        

        
        predictor     = RevenuePredictor(df_features)
        model_metrics = predictor.train_evaluate()

        logger.info("=== KazpostRealDataPipeline DONE ===")
        return {
            "quality_report":  quality_report,
           
            "model_metrics":   model_metrics,
        }





if __name__ == "__main__":
    import sys
    import os

    if "--test" in sys.argv:
        # Run unit tests
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestDataValidation)
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)

    print("\n" + "═" * 60)
    print("  КАЗПОЧТА — PIPELINE ЗАПУСК")
    print("═" * 60)
    print("\nУкажите пути к файлам данных.")
    print("Нажмите Enter чтобы использовать путь по умолчанию.\n")

    DEFAULT_OPS      = r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\opdata.csv"
    DEFAULT_FILIALS  = r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\spfil.xlsx"
    DEFAULT_PRODUCTS = r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source\spprod.xlsx"

    def ask_path(prompt: str, default: str) -> str:
        user_input = input(f"  {prompt}\n  [по умолчанию: {default}]\n  > ").strip()
        return user_input if user_input else default

    ops_path      = ask_path("Оперативные данные (.csv):",      DEFAULT_OPS)
    filials_path  = ask_path("Справочник филиалов (.xlsx):",    DEFAULT_FILIALS)
    products_path = ask_path("Справочник продуктов (.xlsx):",   DEFAULT_PRODUCTS)

    
    print()
    missing = []
    for label, path in [
        ("Оперативные данные", ops_path),
        ("Справочник филиалов", filials_path),
        ("Справочник продуктов", products_path),
    ]:
        if os.path.exists(path):
            print(f"  ✅ {label}: {path}")
        else:
            print(f"  ❌ {label}: файл не найден → {path}")
            missing.append(path)

    if missing:
        print(f"\n❌ Не найдено {len(missing)} файл(ов). Завершение.")
        sys.exit(1)

    print("\n  Все файлы найдены. Запускаем pipeline...\n")

    
    pipeline = KazpostRealDataPipeline(
        ops_path=ops_path,
        filials_path=filials_path,
        products_path=products_path,
    )
    results = pipeline.run()

    

    
    with open("model_performance.json", "w", encoding="utf-8") as f:
        json.dump(results["model_metrics"], f, indent=2, ensure_ascii=False)
    logger.info("Saved model_performance.json")

    print("\n✅ ")



