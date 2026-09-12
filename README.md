# Перепись заведений Кишинёва — Google Places API (New)

CLI и пайплайн для сбора, обогащения и экспорта данных о заведениях Кишинёва через Google Places API.

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -e ".[dev]"
cp .env.example .env
# вставь в .env свой ключ Google Places API (New)
```

## Запуск CLI

```bash
places-census scout
places-census estimate
places-census run --categories food --max-calls 200 --dry-run
places-census run --categories food --max-calls 200
places-census run --max-calls 5000
places-census run --categories beauty,food,health
places-census run --include-suburbs
places-census run --resume
places-census report
```

Эквивалент через модуль:

```bash
python -m places_census scout
python -m places_census run --max-calls 5000
```

## Этапы запуска

### Этап 1 — Разведка (обязательно перед остальным)

```bash
places-census scout
```

Делает один запрос по центру Кишинёва (cafe, r=500м), печатает сырой JSON.
**Проверь в Google Cloud Console > Metrics, что SKU = "Places API - Nearby Search Pro", и только потом продолжай.**

### Оценка бюджета

```bash
places-census estimate
```

Считает количество ячеек сетки и типов, выдаёт ожидаемое число вызовов.

> **Важно:** при 36 типах и `START_RADIUS=1800` базовая оценка зависит от сетки —  
> это может превысить 5 000 бесплатных Pro-вызовов в месяц.  
> Варианты укладки в бюджет:
> - Запускать по 1–2 вертикали в месяц: `--categories beauty`  
> - Увеличить `START_RADIUS` в `src/places_census/config.py`  
> - Сократить список типов в `data/categories.yaml`  
> - Прогнать всё сразу с пониманием стоимости сверх лимита

### Этап 2 — Тест на двух категориях

```bash
places-census run --categories food --max-calls 200 --dry-run
places-census run --categories food --max-calls 200
```

### Полный прогон

```bash
places-census run --max-calls 5000
places-census run --categories beauty,food,health
places-census run --include-suburbs
places-census run --resume
```

### Отчёт и CSV

```bash
places-census report
```

Генерирует `var/report.md` и `var/places.csv` из текущей базы.

## Структура проекта

```
crm/
├── README.md
├── pyproject.toml              # packaging, entry points, pytest config
├── requirements.txt            # pinned deps (mirrors pyproject.toml)
├── .env.example
├── .gitignore
│
├── data/                       # статические конфиги (в git)
│   ├── categories.yaml         # вертикали и типы мест для сбора
│   ├── taxonomy.yaml           # primaryType → vertical
│   └── osm_sectors.json        # seed-данные секторов OSM
│
├── src/
│   └── places_census/          # основной пакет
│       ├── __init__.py
│       ├── __main__.py         # python -m places_census
│       ├── cli.py              # Click CLI (scout, run, report, estimate)
│       ├── config.py           # константы, пути, API-настройки
│       │
│       ├── api/                # Google Places API
│       │   ├── client.py       # Nearby Search клиент
│       │   └── budget.py       # бюджет, кэш-aware google_call
│       │
│       ├── census/             # алгоритм сбора
│       │   ├── runner.py       # адаптивная сетка, Census
│       │   └── geometry.py     # генератор сетки, дробление ячеек
│       │
│       ├── storage/            # персистентность
│       │   ├── db.py           # SQLite: схема, upsert, дедуп
│       │   └── cache.py        # дисковый кэш ответов API
│       │
│       ├── export/             # отчёты и выгрузки
│       │   ├── report.py       # CSV + Markdown-отчёт
│       │   ├── leads.py        # leads_main.csv, leads_interviews.csv
│       │   ├── xlsx.py         # chisinau_leads_v2.xlsx
│       │   └── simple.py       # leads.xlsx
│       │
│       ├── processing/         # пост-обработка данных
│       │   ├── normalize.py    # vertical из taxonomy.yaml
│       │   ├── product_fit.py  # appointment / booking / orders_menu
│       │   ├── dedup.py        # geo + name deduplication
│       │   ├── enrich.py       # Enterprise Place Details
│       │   └── text_norm.py    # нормализация названий
│       │
│       └── scripts/            # утилиты (python -m places_census.scripts.<name>)
│           ├── check_auto.py
│           ├── crawl.py
│           ├── fix_sku.py
│           ├── osm.py
│           └── query_usage.py
│
├── tests/
│   ├── conftest.py
│   ├── test_geometry.py
│   ├── test_dedup.py
│   └── test_budget_cache.py
│
└── var/                        # runtime-артефакты (gitignored)
    ├── cache/                  # кэш ответов API
    ├── places.db
    ├── places.csv
    ├── report.md
    ├── api_calls.jsonl
    └── …                       # osm_cache.json, leads_*.csv, *.xlsx
```

## Вспомогательные скрипты

```bash
python -m places_census.processing.normalize
python -m places_census.processing.product_fit
python -m places_census.processing.dedup
python -m places_census.processing.enrich --show-budget
python -m places_census.scripts.osm
python -m places_census.scripts.crawl --show-stats
python -m places_census.export.leads
```

## Тесты

```bash
pytest
# или
python -m pytest tests/ -v
```

## Алгоритм (кратко)

1. Bbox города разбивается на круги с шагом `START_RADIUS * GRID_OVERLAP` (~30% перекрытие).
2. Каждый тип запрашивается отдельно (1 тип = 1 `includedTypes`).
3. Если ответ = 20 (потолок API), ячейка дробится на 4 подячейки с радиусом `/2` — рекурсивно.
4. Если дробление дошло до `MIN_RADIUS` и всё ещё 20 результатов — ячейка помечается как насыщенная.
5. Каждый ответ кэшируется до обработки. Перезапуск продолжает с кэша.

## Ограничения (политика Google)

Координаты (`lat`, `lng`) в базе допустимо хранить для внутреннего анализа, но  
**не встраивать в коммерческий продукт без актуальной лицензии Google Maps Platform.**  
`place_id` можно хранить бессрочно.

## Поля запроса (Pro SKU)

`id, displayName, formattedAddress, location, types, primaryType, businessStatus, googleMapsUri`

Рейтинги, телефоны, часы работы — Enterprise SKU, запрашиваются отдельно через `processing/enrich.py`.
