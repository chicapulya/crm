from __future__ import annotations
import csv
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config import CSV_PATH, DB_PATH, REPORT_PATH
import db as db_mod

VERTICAL_MAP = {
    "beauty_salon": "beauty", "hair_salon": "beauty", "nail_salon": "beauty",
    "barber_shop": "beauty", "spa": "beauty",
    "dentist": "health", "doctor": "health", "physiotherapist": "health",
    "veterinary_care": "health",
    "restaurant": "food", "cafe": "food", "coffee_shop": "food", "bar": "food",
    "bakery": "food", "fast_food_restaurant": "food", "pizza_restaurant": "food",
    "meal_takeaway": "food",
    "gym": "fitness", "fitness_center": "fitness", "yoga_studio": "fitness",
    "car_repair": "auto", "car_wash": "auto", "car_dealer": "auto",
    "hotel": "lodging", "guest_house": "lodging", "bed_and_breakfast": "lodging",
    "event_venue": "events", "banquet_hall": "events",
}


def export_csv(conn: sqlite3.Connection, path: Path = CSV_PATH) -> int:
    rows = conn.execute(
        "SELECT place_id, display_name, formatted_address, lat, lng, "
        "primary_type, types_json, business_status, sector, first_seen_query, fetched_at "
        "FROM places ORDER BY primary_type, display_name"
    ).fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "place_id", "display_name", "formatted_address", "lat", "lng",
            "primary_type", "types_json", "business_status", "sector", "first_seen_query", "fetched_at",
        ])
        writer.writerows(rows)
    return len(rows)


def generate_report(conn: sqlite3.Connection, saturated: list[dict]) -> str:
    total = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    non_operational = conn.execute(
        "SELECT COUNT(*) FROM places WHERE business_status != 'OPERATIONAL'"
    ).fetchone()[0]
    pct_non_op = 100 * non_operational / total if total else 0

    # Per-type distribution
    type_counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT primary_type, COUNT(*) as cnt FROM places GROUP BY primary_type ORDER BY cnt DESC"
    ).fetchall():
        type_counts[row[0] or "unknown"] = row[1]

    # Vertical distribution
    vertical_counts: dict[str, int] = defaultdict(int)
    for ptype, cnt in type_counts.items():
        vertical_counts[VERTICAL_MAP.get(ptype, "other")] += cnt

    # Sector distribution
    sector_counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT sector, COUNT(*) as cnt FROM places GROUP BY sector ORDER BY cnt DESC"
    ).fetchall():
        sector_counts[row[0] or "Unknown"] = row[1]

    # API calls summary
    api_total = conn.execute("SELECT COUNT(*) FROM api_calls WHERE from_cache=0").fetchone()[0]
    api_cached = conn.execute("SELECT COUNT(*) FROM api_calls WHERE from_cache=1").fetchone()[0]
    cost_est = max(0, api_total - 5000) * 0.032  # $32/1000 after 5000 free Pro calls

    lines = [
        f"# Перепись заведений Кишинёва",
        f"",
        f"Сгенерировано: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"",
        f"## Итого",
        f"",
        f"| Метрика | Значение |",
        f"|---------|----------|",
        f"| Уникальных заведений | **{total:,}** |",
        f"| Не операционных (businessStatus ≠ OPERATIONAL) | {non_operational:,} ({pct_non_op:.1f}%) |",
        f"| Вызовов API (сеть) | {api_total:,} |",
        f"| Ответов из кэша | {api_cached:,} |",
        f"| Насыщенных ячеек (потолок 20 на минимальном радиусе) | {len(saturated):,} |",
        f"| Оценка стоимости (Pro SKU, сверх 5 000 бесплатных) | ~${cost_est:.2f} |",
        f"",
        f"## Распределение по вертикалям",
        f"",
        f"| Вертикаль | Заведений |",
        f"|-----------|-----------|",
    ]
    for v, cnt in sorted(vertical_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {v} | {cnt:,} |")

    lines += [
        f"",
        f"## Распределение по типам (primaryType) — топ 50",
        f"",
        f"| Тип | Заведений |",
        f"|-----|-----------|",
    ]
    top50 = list(type_counts.items())[:50]
    tail = list(type_counts.items())[50:]
    for ptype, cnt in top50:
        lines.append(f"| {ptype} | {cnt:,} |")
    if tail:
        lines += [
            f"",
            f"**Хвост ({len(tail)} типов):**",
            f"",
            f"| Тип | Заведений |",
            f"|-----|-----------|",
        ]
        for ptype, cnt in tail:
            lines.append(f"| {ptype} | {cnt:,} |")

    lines += [
        f"",
        f"## Распределение по секторам",
        f"",
        f"> ⚠️ Привязка по ближайшему центроиду сектора — приближение, не официальная граница.",
        f"",
        f"| Сектор | Заведений |",
        f"|--------|-----------|",
    ]
    for sector, cnt in sorted(sector_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {sector} | {cnt:,} |")

    if saturated:
        lines += [
            f"",
            f"## ⚠️ Флаг недосбора: насыщенные ячейки",
            f"",
            "В следующих ячейках алгоритм упёрся в потолок 20 результатов на минимальном радиусе ({} м).".format(
                conn.execute("SELECT MIN(radius) FROM api_calls WHERE saturated=1").fetchone()[0] or "N/A"
            ),
            f"Реальное число заведений в этих зонах **выше** указанного.",
            f"",
            f"| Категория | lat | lng | Радиус |",
            f"|-----------|-----|-----|--------|",
        ]
        for s in saturated:
            lines.append(f"| {s['category']} | {s['lat']:.5f} | {s['lng']:.5f} | {s['radius']:.0f}м |")

    lines += [
        f"",
        f"## Потраченные вызовы по SKU",
        f"",
        f"| SKU | Вызовов (сеть) | Бесплатный лимит/мес | Превышение |",
        f"|-----|---------------|----------------------|-----------|",
        f"| Pro | {api_total:,} | 5 000 | {max(0, api_total - 5000):,} |",
        f"",
        f"---",
        f"*Координаты хранятся для внутреннего анализа. Политика Google Maps Platform запрещает "
        f"коммерческое встраивание координат в сторонние продукты без актуальной лицензии.*",
    ]

    report = "\n".join(lines)
    REPORT_PATH.write_text(report, encoding="utf-8")
    return report


def run(saturated: list[dict] | None = None) -> None:
    conn = db_mod.get_connection()
    n = export_csv(conn)
    print(f"Exported {n} places to {CSV_PATH}")
    report = generate_report(conn, saturated or [])
    print(f"Report written to {REPORT_PATH}")
    print(report[:2000])
