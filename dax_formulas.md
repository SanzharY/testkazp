# DAX Formulas — Казпочта Power BI

Все меры используют реальную структуру модели:
`fact_operations` (SUMMA, CNT, PERIOD, FILIAL, SNDRCTG) + `dim_filials` + `dim_products`

---

## 1. Базовые меры

```dax
// Общая выручка
Total Revenue =
    SUM(fact_operations[SUMMA])

// Количество операций (штук, не строк)
Total CNT =
    SUM(fact_operations[CNT])

// Средний чек
Avg Summa =
    DIVIDE([Total Revenue], COUNTROWS(fact_operations))

// Количество активных филиалов
Active Filials =
    DISTINCTCOUNT(fact_operations[FILIAL])
```

---

## 2. Time Intelligence

```dax
// Выручка предыдущего периода (LAG по PERIOD)
// Так как PERIOD = "2025_3" (не DATE), создаём вспомогательную таблицу периодов
Revenue Prior Period =
VAR CurrentPeriod = SELECTEDVALUE(fact_operations[PERIOD])
VAR PeriodMap =
    DATATABLE(
        "Period", STRING, "SortKey", INTEGER,
        {{"2025_3", 3}, {"2025_4", 4}, {"2025_5", 5}}
    )
VAR CurrentKey =
    LOOKUPVALUE(PeriodMap[SortKey], PeriodMap[Period], CurrentPeriod)
VAR PriorPeriod =
    LOOKUPVALUE(PeriodMap[Period], PeriodMap[SortKey], CurrentKey - 1)
RETURN
    CALCULATE(
        [Total Revenue],
        fact_operations[PERIOD] = PriorPeriod
    )

// MoM % изменение
MoM Change % =
    DIVIDE(
        [Total Revenue] - [Revenue Prior Period],
        [Revenue Prior Period]
    )

// YTD выручка (если добавить данные за 2024-2025)
Revenue YTD =
    TOTALYTD(
        [Total Revenue],
        dim_date[Date]
    )
```

---

## 3. Ранжирование и Top N

```dax
// Ранг филиала по выручке в выбранном периоде
Filial Revenue Rank =
    RANKX(
        ALLSELECTED(dim_filials[FILIAL_NAME]),
        [Total Revenue],
        ,
        DESC,
        DENSE
    )

// Флаг "Топ-5 филиал"
Is Top 5 Filial =
    IF([Filial Revenue Rank] <= 5, "⭐ Топ-5", "Остальные")

// Доля выручки филиала в общей
Revenue Share % =
    DIVIDE(
        [Total Revenue],
        CALCULATE([Total Revenue], ALL(dim_filials))
    )

// Кумулятивная доля (для Pareto)
Cumulative Revenue Share % =
VAR CurrentRank = [Filial Revenue Rank]
RETURN
    CALCULATE(
        [Revenue Share %],
        FILTER(
            ALLSELECTED(dim_filials[FILIAL_NAME]),
            [Filial Revenue Rank] <= CurrentRank
        )
    )
```

---

## 4. SWITCH / Conditional Logic

```dax
// Цвет KPI карточки (для conditional formatting)
Revenue KPI Color =
    SWITCH(
        TRUE(),
        [MoM Change %] >= 0.05,   "#27AE60",   -- зелёный: рост >5%
        [MoM Change %] >= 0,      "#F39C12",   -- жёлтый: рост 0-5%
        [MoM Change %] >= -0.10,  "#E67E22",   -- оранжевый: спад до -10%
        "#C0392B"                              -- красный: спад >10%
    )

// Сегмент клиента (для матрицы)
Client Segment Label =
    SWITCH(
        SELECTEDVALUE(fact_operations[SNDRCTG]),
        "Юр. лицо", "🏢 B2B",
        "Физ. лицо", "👤 B2C",
        "Все"
    )

// Статус продукта
Product Status =
VAR Growth = [MoM Change %]
RETURN
    SWITCH(
        TRUE(),
        Growth > 0.10,    "🚀 Быстрый рост",
        Growth > 0,       "📈 Рост",
        Growth > -0.10,   "📉 Спад",
        "⚠️ Кризис"
    )
```

---

## 5. Dynamic Titles (меняются по фильтрам)

```dax
// Заголовок страницы Executive
Page Title Executive =
VAR SelectedPeriod =
    IF(
        HASONEVALUE(fact_operations[PERIOD]),
        SELECTEDVALUE(fact_operations[PERIOD]),
        "Все периоды"
    )
VAR SelectedFilial =
    IF(
        HASONEVALUE(dim_filials[FILIAL_NAME]),
        " | " & SELECTEDVALUE(dim_filials[FILIAL_NAME]),
        ""
    )
RETURN
    "Выручка Казпочты — " & SelectedPeriod & SelectedFilial

// Заголовок страницы Drill-through
Page Title Drillthrough =
    "Аналитика — " & SELECTEDVALUE(dim_filials[FILIAL_NAME], "Все филиалы")
```

---

## 6. Variance анализ (план vs факт)

```dax
// Плановая выручка (из таблицы планов dim_plan)
Planned Revenue =
    CALCULATE(
        SUM(dim_plan[planned_summa]),
        TREATAS(
            VALUES(fact_operations[PERIOD]),
            dim_plan[period]
        )
    )

// Отклонение от плана
Plan Variance =
    [Total Revenue] - [Planned Revenue]

Plan Variance % =
    DIVIDE([Plan Variance], [Planned Revenue])

// Выполнение плана (для Gauge Chart)
Plan Achievement % =
    DIVIDE([Total Revenue], [Planned Revenue])
```
