# 📊 Postal Analytics Project

Тестовое задание на позицию **Data Analyst**.

Проект демонстрирует полный цикл аналитической обработки данных почтовой компании: от проверки качества данных и исследовательского анализа до прогнозирования, написания сложных SQL-запросов, проектирования DWH и подготовки архитектуры Power BI.

---

# Цели проекта

В рамках проекта были выполнены следующие задачи:

- Загрузка и валидация данных с использованием Pydantic;
- Проверка качества данных (Data Quality);
- Исследовательский анализ данных (EDA);
- Feature Engineering;
- Построение модели прогнозирования выручки;
- Разработка сложных SQL-запросов PostgreSQL;
- Оптимизация SQL-запросов;
- Проектирование Data Warehouse;
- Разработка архитектуры Power BI Dashboard;
- Проектирование ETL-пайплайна на Apache Airflow.

---

# Структура проекта

```
testkazp/

├── analysis.py                    # Основной скрипт анализа
├── data_validation.py             # Проверка качества данных
├── feature_engineering.py         # Создание новых признаков
├── eda.py                         # Исследовательский анализ данных
├── forecasting.py                 # Модель прогнозирования
├── airflow_dag.py                 # Пример DAG Apache Airflow
│
├── advanced_queries.sql           # SQL-запросы PostgreSQL
│
├── dashboard_design_advanced.md   # Архитектура Power BI
├── dax_formulas.md                # DAX-формулы
├── schema_design.md               # Проектирование DWH
├── optimization_notes.md          # Оптимизация SQL
├── eda_insights.md                # Выводы EDA
├── model_performance.json         # Метрики модели
├── data_quality_report.json       # Отчет по качеству данных
│
├── requirements.txt
└── README.md
```

---

# Используемые технологии

## Python

- Python 3.13
- Pandas
- NumPy
- Scikit-learn
- Pydantic
- SciPy
- Statsmodels

## Database

- PostgreSQL

## BI

- Microsoft Power BI

## ETL

- Apache Airflow
- Apache Hop (архитектурное описание)

---

# Выполненные задачи

## 1. Data Validation

✔ Валидация схемы данных (Pydantic)

✔ Проверка пропусков

✔ Проверка дубликатов

✔ Проверка выбросов

✔ Data Profiling

---

## 2. Feature Engineering

Созданы дополнительные признаки:

- День недели
- Месяц
- Квартал
- Признак выходного дня
- Revenue per Filial
- Concentration Ratio

---

## 3. Exploratory Data Analysis

Выполнены:

- описательная статистика;
- корреляционный анализ;
- поиск аномалий;
- кластеризация филиалов (K-Means);
- анализ трендов;
- анализ сезонности.

---

## 4. Machine Learning

Построена модель прогнозирования выручки.

Использована модель:

- Random Forest Regressor

Оценка качества:

- R²
- MAE
- RMSE
- 5-Fold Cross Validation

---

## 5. SQL

Разработаны аналитические запросы:

- Rolling Revenue;
- Ranking филиалов;
- RFM-анализ;
- Pareto-анализ;
- Анализ услуг;
- Анализ трендов;
- Поиск аномалий.

Использованы:

- CTE
- Window Functions
- LAG / LEAD
- DENSE_RANK
- NTILE

---

## 6. Оптимизация PostgreSQL

Подготовлены рекомендации по:

- EXPLAIN ANALYZE;
- индексированию;
- Materialized Views;
- Partitioning;
- оптимизации оконных функций.

---

## 7. Проектирование DWH

Разработана архитектура Star Schema.

Описаны:

- Fact Table;
- Dimension Tables;
- Slowly Changing Dimensions;
- Incremental Load.

---

## 8. Power BI

Разработана архитектура аналитического дашборда.

Предусмотрены:

- Executive Dashboard;
- Drill-through;
- Dynamic Titles;
- KPI Cards;
- Waterfall;
- Matrix;
- Scatter Plot;
- Decomposition Tree;
- Key Influencers.

---

# Запуск проекта

## Клонирование

```bash
git clone https://github.com/SanzharY/testkazp.git

cd testkazp
```

## Создание виртуального окружения

Windows

```bash
python -m venv venv

venv\Scripts\activate
```

Linux / macOS

```bash
python3 -m venv venv

source venv/bin/activate
```

---

## Установка зависимостей

```bash
pip install -r requirements.txt
```

---

## Запуск анализа

```bash
python analysis.py
```

---

## Запуск тестов

```bash
pytest
```

---

# Результаты проекта

В результате выполнения проекта были:

- проведена оценка качества данных;
- выполнен исследовательский анализ;
- разработана модель прогнозирования;
- подготовлены аналитические SQL-запросы;
- предложены способы оптимизации PostgreSQL;
- разработана архитектура хранилища данных;
- спроектирован Power BI Dashboard.

---

# Автор

**Sanzhar Yekpinbay**

Тестовое задание на позицию **Data Analyst**.