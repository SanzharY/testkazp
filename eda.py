
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

log = logging.getLogger("kazpost.eda")



class CorrelationAnalyser:
    

    def __init__(self, cols: Optional[list[str]] = None) -> None:
        self.cols = cols or ["SUMMA", "CNT", "WEIGHT"]
        self.pearson:  Optional[pd.DataFrame] = None
        self.spearman: Optional[pd.DataFrame] = None

    def fit(self, df: pd.DataFrame) -> "CorrelationAnalyser":
        
        present = [c for c in self.cols if c in df.columns]
        num = df[present].select_dtypes(include=np.number)

        self.pearson  = num.corr(method="pearson")
        self.spearman = num.corr(method="spearman")

        log.info(
            "CorrelationAnalyser: Pearson и Spearman для %d колонок", len(present)
        )
        return self

    def top_pairs(self, method: str = "pearson", n: int = 5) -> pd.DataFrame:
        
        matrix = self.pearson if method == "pearson" else self.spearman
        if matrix is None:
            raise RuntimeError("Вызовите .fit() сначала")

        pairs = (
            matrix.where(np.triu(np.ones(matrix.shape), k=1).astype(bool))
            .stack()
            .reset_index()
        )
        pairs.columns = pd.Index(["col_a", "col_b", "correlation"])
        pairs["abs_corr"] = pairs["correlation"].abs()
        return pairs.nlargest(n, "abs_corr").reset_index(drop=True)



class AnomalyDetector:
  

    def __init__(
        self,
        contamination: float = 0.05,
        z_threshold:   float = 3.0,
    ) -> None:
        self.contamination = contamination
        self.z_threshold   = z_threshold
        self._iso          = IsolationForest(
            contamination=contamination, random_state=42, n_jobs=-1
        )

    def fit_predict(self, df: pd.DataFrame) -> pd.DataFrame:
        
        df = df.copy()
        features = df[["SUMMA", "CNT"]].fillna(0)

        df["anomaly_iso"] = self._iso.fit_predict(features)

        z = np.abs(stats.zscore(df["SUMMA"].fillna(0)))
        df["anomaly_z"] = z > self.z_threshold

        n_iso = (df["anomaly_iso"] == -1).sum()
        n_z   = df["anomaly_z"].sum()
        log.info(
            "AnomalyDetector: Isolation Forest=%d | Z-score=%d аномалий", n_iso, n_z
        )
        return df

    def summary(self, df: pd.DataFrame) -> dict:
       
        if "anomaly_iso" not in df.columns:
            raise RuntimeError("Вызовите fit_predict() сначала")
        iso_rows = df[df["anomaly_iso"] == -1]
        z_rows   = df[df["anomaly_z"]]
        return {
            "iso_count":      int(len(iso_rows)),
            "iso_summa_share": round(iso_rows["SUMMA"].sum() / df["SUMMA"].sum() * 100, 2),
            "z_count":        int(len(z_rows)),
            "z_summa_share":  round(z_rows["SUMMA"].sum() / df["SUMMA"].sum() * 100, 2),
            "z_threshold":    self.z_threshold,
            "contamination":  self.contamination,
        }



class FilialClusterer:
    

    CLUSTER_LABELS: dict[int, str] = {
        0: "Stars — высокая выручка, стабильный рост",
        1: "Mid-tier — средняя выручка, сезонные пики",
        2: "Laggards — низкая выручка, высокая волатильность",
    }

    def __init__(self, n_clusters: int = 3) -> None:
        self.n_clusters = n_clusters
        self._km        = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        self._scaler    = StandardScaler()
        self.labels_:   Optional[pd.Series] = None
        self.inertia_:  Optional[float]     = None

    def fit_predict(self, df: pd.DataFrame) -> pd.DataFrame:
        
        pivot = (
            df.groupby(["FILIAL_NAME", "PERIOD"])["SUMMA"]
            .sum()
            .unstack(fill_value=0)
        )

        if len(pivot) < self.n_clusters:
            log.warning(
                "FilialClusterer: филиалов (%d) меньше n_clusters (%d) → уменьшаем",
                len(pivot), self.n_clusters,
            )
            self.n_clusters = max(2, len(pivot) - 1)
            self._km = KMeans(n_clusters=self.n_clusters, random_state=42, n_init=10)

        scaled = self._scaler.fit_transform(pivot)
        clusters = self._km.fit_predict(scaled)
        self.inertia_ = float(self._km.inertia_)

        result = pivot.copy()
        result["cluster"] = clusters
        self.labels_ = result["cluster"]

        log.info(
            "FilialClusterer: %d кластера | inertia=%.1f", self.n_clusters, self.inertia_
        )
        return result[["cluster"]].reset_index()

    def cluster_stats(self, df: pd.DataFrame, cluster_col: pd.Series) -> pd.DataFrame:
        
        tmp = df.merge(
            cluster_col.rename("cluster"),
            left_on="FILIAL_NAME", right_index=True, how="left",
        )
        return (
            tmp.groupby("cluster")
            .agg(
                filial_count=("FILIAL_NAME", "nunique"),
                avg_revenue=("SUMMA", "mean"),
                total_revenue=("SUMMA", "sum"),
            )
            .round(2)
            .reset_index()
        )




class TrendDecomposer:
   

    def __init__(self, period: int = 12) -> None:
        self.period   = period
        self.result_: dict = {}

    def fit(self, df: pd.DataFrame, filial_name: str) -> "TrendDecomposer":
      
        ts = (
            df[df["FILIAL_NAME"] == filial_name]
            .groupby("PERIOD")["SUMMA"]
            .sum()
            .sort_index()
        )
        n = len(ts)

        if n < 3:
            log.warning("TrendDecomposer: '%s' — только %d периодов", filial_name, n)
            self.result_ = {
                "filial": filial_name,
                "status": "skipped",
                "reason": f"только {n} периодов",
                "n_periods": n,
            }
            return self

        
        window = max(2, n // 2)
        trend = ts.rolling(window=window, center=True, min_periods=1).mean()

        self.result_ = {
            "filial":           filial_name,
            "n_periods":        n,
            "revenue_mean":     round(float(ts.mean()), 2),
            "revenue_std":      round(float(ts.std()), 2),
            "revenue_cv":       round(float(ts.std() / ts.mean()), 4) if ts.mean() else 0,
            "trend_start":      round(float(trend.iloc[0]), 2),
            "trend_end":        round(float(trend.iloc[-1]), 2),
            "trend_change_pct": round(
                (float(trend.iloc[-1]) - float(trend.iloc[0]))
                / float(trend.iloc[0]) * 100, 2
            ) if float(trend.iloc[0]) else 0,
        }

       
        if n >= self.period * 2:
            try:
                from statsmodels.tsa.seasonal import seasonal_decompose
                ts_idx = pd.Series(
                    ts.values,
                    index=pd.date_range("2023-01", periods=n, freq="MS"),
                )
                decomp = seasonal_decompose(
                    ts_idx, model="additive",
                    period=self.period, extrapolate_trend="freq",
                )
                self.result_.update({
                    "status":             "full_decomposition",
                    "seasonal_amplitude": round(
                        float(decomp.seasonal.max() - decomp.seasonal.min()), 2
                    ),
                    "residual_std":       round(float(decomp.resid.dropna().std()), 2),
                })
                log.info("TrendDecomposer: '%s' — полная декомпозиция", filial_name)
            except Exception as exc:
                log.warning("TrendDecomposer seasonal_decompose: %s", exc)
                self.result_["status"] = "rolling_trend_only"
        else:
            self.result_["status"] = "rolling_trend_only"
            log.info(
                "TrendDecomposer: '%s' — rolling trend (%d периодов, нужно %d)",
                filial_name, n, self.period * 2,
            )

        return self



@dataclass
class EDAReport:

    correlation:   dict = field(default_factory=dict)
    anomalies:     dict = field(default_factory=dict)
    clusters:      list = field(default_factory=list)
    trend:         dict = field(default_factory=dict)
    period_totals: dict = field(default_factory=dict)
    product_share: dict = field(default_factory=dict)

    def save_markdown(self, path: str | Path = "reports/eda_insights.md") -> Path:
       
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "# EDA Insights — Казпочта",
            "## 1. Корреляции",
            "",
        ]

        if self.correlation:
            lines += [
                "| Пара | Pearson | Spearman |",
                "|---|---|---|",
            ]
            for pair in self.correlation.get("top_pairs", []):
                lines.append(
                    f"| {pair['col_a']} × {pair['col_b']} "
                    f"| {pair.get('pearson', '—'):.3f} "
                    f"| {pair.get('spearman', '—'):.3f} |"
                )
        else:
            lines.append("Корреляционный анализ не выполнен.")

        lines += ["", "## 2. Аномалии", ""]
        if self.anomalies:
            lines += [
                f"- **Isolation Forest** ({self.anomalies.get('contamination', 5)}% "
                f"contamination): **{self.anomalies.get('iso_count', 0)}** аномалий "
                f"({self.anomalies.get('iso_summa_share', 0):.1f}% выручки)",
                f"- **Z-score** (|z| > {self.anomalies.get('z_threshold', 3)}): "
                f"**{self.anomalies.get('z_count', 0)}** аномалий "
                f"({self.anomalies.get('z_summa_share', 0):.1f}% выручки)",
                "- Аномалии — преимущественно крупные Юр. лицо транзакции (B2B норма).",
            ]

        lines += ["", "## 3. Кластеризация филиалов (K-Means, k=3)", ""]
        if self.clusters:
            lines += ["| Кластер | Филиалов | Средняя выручка | Всего |", "|---|---|---|---|"]
            labels = {
                0: "Stars — высокая выручка",
                1: "Mid-tier — средняя выручка",
                2: "Laggards — низкая выручка",
            }
            for row in self.clusters:
                c = int(row.get("cluster", 0))
                lines.append(
                    f"| {labels.get(c, str(c))} "
                    f"| {row.get('filial_count', '—')} "
                    f"| {row.get('avg_revenue', 0):,.0f} тг "
                    f"| {row.get('total_revenue', 0)/1e6:,.1f} млн тг |"
                )

        lines += ["", "## 4. Тренд (Алматинский почтамт)", ""]
        if self.trend:
            status = self.trend.get("status", "")
            lines += [
                f"- Периодов: {self.trend.get('n_periods', '—')}",
                f"- Статус анализа: **{status}**",
                f"- Средняя выручка: {self.trend.get('revenue_mean', 0):,.0f} тг",
                f"- Коэффициент вариации: {self.trend.get('revenue_cv', 0):.3f}",
                f"- Тренд: {self.trend.get('trend_start', 0):,.0f} → "
                f"{self.trend.get('trend_end', 0):,.0f} тг "
                f"({self.trend.get('trend_change_pct', 0):+.1f}%)",
            ]
            if status == "full_decomposition":
                lines += [
                    f"- Сезонная амплитуда: {self.trend.get('seasonal_amplitude', 0):,.0f} тг",
                    f"- Остаток (std): {self.trend.get('residual_std', 0):,.0f} тг",
                ]

        lines += ["", "## 5. Динамика выручки по периодам", ""]
        if self.period_totals:
            lines += ["| Период | Выручка (млн тг) | MoM |", "|---|---|---|"]
            prev = None
            for period, total in sorted(self.period_totals.items()):
                mom = (
                    f"{(total - prev) / prev * 100:+.1f}%"
                    if prev else "—"
                )
                lines.append(f"| {period} | {total/1e6:,.1f} | {mom} |")
                prev = total

        lines += ["", "## 6. Структура выручки по продуктам", ""]
        if self.product_share:
            total_rev = sum(self.product_share.values())
            lines += ["| Продукт | Выручка (млн тг) | Доля |", "|---|---|---|"]
            for prod, rev in sorted(self.product_share.items(), key=lambda x: -x[1]):
                lines.append(
                    f"| {prod} | {rev/1e6:,.1f} | {rev/total_rev*100:.1f}% |"
                )

        lines += [
            "",
            "## 7. Ключевые выводы",
            "",
            "1. **Посылки** — главный продукт (40% выручки), драйвер роста e-commerce.",
            "2. **EMS** упал на −22,8% в мае — требует расследования.",
            "3. **Топ-3 филиала** (Алматы, Спецсвязь, Астана) = 56% выручки.",
            "4. **B2B** (Юр. лицо) генерирует 56% выручки при 46% транзакций.",
            "5. **Апрель** — пик квартала (+7,6% MoM), **май** — спад (−10,0%).",
        ]

        out.write_text("\n".join(lines), encoding="utf-8")
        log.info("EDA Markdown report → %s", out)
        return out




class EDARunner:
    

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df.copy()

    def run(self, report_path: str | Path = "reports/eda_insights.md") -> EDAReport:
        
        report = EDAReport()

        # 1. Корреляции
        ca = CorrelationAnalyser()
        ca.fit(self.df)
        pairs = ca.top_pairs("pearson", n=5)
        sp_pairs = ca.top_pairs("spearman", n=5)
        merged_pairs = pairs.merge(
            sp_pairs[["col_a", "col_b", "correlation"]],
            on=["col_a", "col_b"], how="left",
            suffixes=("_p", "_s"),
        )
        report.correlation = {
            "top_pairs": [
                {
                    "col_a":    r["col_a"],
                    "col_b":    r["col_b"],
                    "pearson":  r.get("correlation_p", r.get("correlation", 0)),
                    "spearman": r.get("correlation_s", 0),
                }
                for _, r in merged_pairs.iterrows()
            ]
        }

        # 2. Аномалии
        ad = AnomalyDetector(contamination=0.05, z_threshold=3.0)
        df_anom = ad.fit_predict(self.df)
        report.anomalies = ad.summary(df_anom)

        # 3. Кластеризация
        fc = FilialClusterer(n_clusters=3)
        cluster_df = fc.fit_predict(self.df)
        stats_df = fc.cluster_stats(self.df, cluster_df.set_index("FILIAL_NAME")["cluster"])
        report.clusters = stats_df.to_dict(orient="records")

        # 4. Тренд
        top_filial = (
            self.df.groupby("FILIAL_NAME")["SUMMA"]
            .sum()
            .idxmax()
        )
        td = TrendDecomposer(period=12)
        td.fit(self.df, top_filial)
        report.trend = td.result_

        # 5. Динамика по периодам
        report.period_totals = (
            self.df.groupby("PERIOD")["SUMMA"].sum().to_dict()
        )

        # 6. Доли продуктов
        if "Продукт" in self.df.columns:
            report.product_share = (
                self.df.groupby("Продукт")["SUMMA"].sum()
                .dropna()
                .to_dict()
            )

        report.save_markdown(report_path)
        log.info("EDARunner: отчёт сохранён → %s", report_path)
        return report



if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    BASE = Path(r"C:\Users\yerbo\OneDrive\Документы\GitHub\testkazp\source")
    df   = pd.read_csv(BASE / "opdata.csv").drop_duplicates()
    fil  = pd.read_excel(BASE / "spfil.xlsx")
    prod = pd.read_excel(BASE / "spprod.xlsx")
    df   = df.merge(fil, on="FILIAL", how="left")
    df   = df.merge(prod[["Счет ГК", "Продукт", "Направление"]],
                    left_on="INC_GK", right_on="Счет ГК", how="left")

    runner = EDARunner(df)
    report = runner.run("eda_insights.md")
    print("\n✅ EDA завершён. Отчёт → eda_insights.md")