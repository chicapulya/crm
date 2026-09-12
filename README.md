# Перепись заведений Кишинёва — Google Places API (New)

## Установка

```bash
pip install -r requirements.txt
cp .env.example .env
# вставь в .env свой ключ Google Places API (New)
```

## Этапы запуска

### Этап 1 — Разведка (обязательно перед остальным)

```bash
python main.py scout
```

Делает один запрос по центру Кишинёва (cafe, r=500м), печатает сырой JSON.
**Проверь в Google Cloud Console > Metrics, что SKU = "Places API - Nearby Search Pro", и только потом продолжай.**

### Оценка бюджета

```bash
python main.py estimate
```

Считает количество ячеек сетки и типов, выдаёт ожидаемое число вызовов.

> **Важно:** при 36 типах и `START_RADIUS=700` базовая оценка ~13 000 вызовов —  
> это больше 5 000 бесплатных Pro-вызовов в месяц (~$256 сверх лимита).  
> Варианты укладки в бюджет:
> - Запускать по 1–2 вертикали в месяц: `--categories beauty`  
> - Увеличить `START_RADIUS` в `config.py` до 1 200–1 500 м (меньше ячеек)  
> - Сократить список типов в `categories.yaml`  
> - Прогнать всё сразу с пониманием, что потратишь ~$250

### Этап 2 — Тест на двух категориях

```bash
python main.py run --categories food --max-calls 200 --dry-run
python main.py run --categories food --max-calls 200
```

Посмотри на фактическое число вызовов и насыщенных ячеек, прежде чем запускать всё.

### Полный прогон

```bash
python main.py run --max-calls 5000
```

С ограничением по категориям:

```bash
python main.py run --categories beauty,food,health
```

С пригородами:

```bash
python main.py run --include-suburbs
```

Продолжить прерванный прогон:

```bash
python main.py run --resume
```

### Отчёт и CSV

```bash
python main.py report
```

Генерирует `report.md` и `places.csv` из текущей базы.

## Структура проекта

```
.
├── main.py           # CLI
├── census.py         # Основной алгоритм сбора (адаптивная сетка)
├── client.py         # HTTP-клиент с кэшем, ретраями, backoff
├── geometry.py       # Генератор сетки, дробление ячеек, сектора
├── db.py             # SQLite: схема, upsert, дедупликация
├── cache.py          # Кэш ответов на диске (SHA-256 ключ)
├── report.py         # Экспорт CSV + Markdown-отчёт
├── config.py         # Все константы и пути
├── categories.yaml   # Вертикали и типы мест
├── tests/
│   ├── test_geometry.py   # Сетка, дробление ячеек, сектора
│   └── test_dedup.py      # Дедупликация по place_id
└── cache/            # Создаётся автоматически
```

## Тесты

```bash
pip install pytest
python -m pytest tests/ -v
```

## Алгоритм (кратко)

1. Bbox города разбивается на круги с шагом `START_RADIUS * 1.4` (~30% перекрытие).
2. Каждый тип запрашивается отдельно (1 тип = 1 `includedTypes`, чтобы не взрывать лимит).
3. Если ответ = 20 (потолок API), ячейка дробится на 4 подячейки с радиусом `/2` — рекурсивно.
4. Если дробление дошло до `MIN_RADIUS=120м` и всё ещё 20 результатов — ячейка помечается как насыщенная (честный флаг недосбора в отчёте).
5. Каждый ответ кэшируется до обработки. Перезапуск продолжает с кэша.

## Ограничения (политика Google)

Координаты (`lat`, `lng`) в базе допустимо хранить для внутреннего анализа, но  
**не встраивать в коммерческий продукт без актуальной лицензии Google Maps Platform.**  
`place_id` можно хранить бессрочно.

## Поля запроса (Pro SKU)

`id, displayName, formattedAddress, location, types, primaryType, businessStatus, googleMapsUri`

Рейтинги, телефоны, часы работы — Enterprise SKU, не запрашиваются в этой итерации.
