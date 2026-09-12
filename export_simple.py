"""Generates chisinau_leads_simple.xlsx — flat table, all contacts, no API needed for booking."""
from __future__ import annotations

import io, sqlite3, sys
from pathlib import Path
from collections import Counter

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import DB_PATH

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = Path(__file__).parent / "leads.xlsx"

SOCIAL_DOMAINS = ("facebook.com", "fb.com", "instagram.com", "vk.com",
                  "tiktok.com", "linktr.ee", "mst.link", "4sq.com")
BOOKING_URL    = {"alteg.io": "Altegio", "altegio.com": "Altegio",
                  "stilio.md": "Stilio", "stilio.app": "Stilio",
                  "fresha.com": "Fresha", "dikidi.net": "DIKIDI",
                  "simplybook.me": "SimplyBook", "yclients.com": "YClients"}

# ── Style helpers ─────────────────────────────────────────────────────────────
_thin   = Side(style="thin", color="D0D0D0")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

def _fill(h: str) -> PatternFill: return PatternFill("solid", fgColor=h)

P_COLOR = {1: "C6EFCE", 2: "C6EFCE", 3: "FFEB9C", 4: "FFEB9C", 5: "FCE4D6", 6: "F2F2F2"}

# ── Data helpers ──────────────────────────────────────────────────────────────

def _phone(r: dict) -> str:
    return r.get("national_phone_number") or r.get("osm_phone") or ""

def _social(r: dict) -> str:
    if r.get("osm_instagram"):  return r["osm_instagram"]
    if r.get("osm_facebook"):   return r["osm_facebook"]
    w = r.get("website_uri") or r.get("osm_website") or ""
    if w and any(d in w for d in SOCIAL_DOMAINS): return w
    return ""

def _website(r: dict) -> str:
    w = r.get("website_uri") or r.get("osm_website") or ""
    if w and any(d in w for d in SOCIAL_DOMAINS): return ""   # социальная — уже в соцсети
    return w

def _is_widget(r: dict) -> bool:
    if r.get("has_booking_widget"): return True
    w = r.get("website_uri") or ""
    return bool(w) and not r.get("crawl_status") and any(d in w for d in BOOKING_URL)

def _booking_status(r: dict) -> str:
    if _is_widget(r):
        p = r.get("booking_provider") or ""
        if not p:
            w = r.get("website_uri") or ""
            p = next((n for d, n in BOOKING_URL.items() if d in w), "widget")
        return p
    cs = r.get("crawl_status")
    w  = r.get("website_uri") or r.get("osm_website") or ""
    if cs == "ok":   return "сайт, нет виджета"
    if cs in ("blocked", "error", "timeout"): return "сайт, не открылся"
    if w and any(d in w for d in SOCIAL_DOMAINS): return "только соцсеть"
    if w:            return "есть сайт"
    return "нет сайта"

def _priority(r: dict) -> int:
    s = bool(_social(r)); ph = bool(_phone(r)); n = r.get("user_rating_count") or 0
    if s  and n >= 10: return 1
    if s  and n >= 1:  return 2
    if s:              return 3
    if ph and n >= 10: return 4
    if ph and n >= 1:  return 5
    return 6

def _priority_label(p: int) -> str:
    return {
        1: "P1 — соцсеть + ≥10 отзывов",
        2: "P2 — соцсеть + 1–9 отзывов",
        3: "P3 — соцсеть, нет отзывов",
        4: "P4 — телефон + ≥10 отзывов",
        5: "P5 — телефон + 1–9 отзывов",
        6: "P6 — нет контакта",
    }.get(p, "")

# ── Load ──────────────────────────────────────────────────────────────────────

def _load(conn: sqlite3.Connection) -> list[dict]:
    enriched = [dict(r) for r in conn.execute("""
        SELECT display_name, product_fit, vertical, sector,
               national_phone_number, osm_phone, osm_instagram, osm_facebook,
               website_uri, NULL as osm_website,
               user_rating_count, rating,
               has_booking_widget, booking_provider, crawl_status
        FROM places WHERE enriched_at IS NOT NULL
    """).fetchall()]

    booking_osm = [dict(r) for r in conn.execute("""
        SELECT display_name, product_fit, vertical, sector,
               NULL as national_phone_number, osm_phone, osm_instagram, osm_facebook,
               NULL as website_uri, osm_website,
               NULL as user_rating_count, NULL as rating,
               NULL as has_booking_widget, NULL as booking_provider, NULL as crawl_status
        FROM places
        WHERE product_fit = 'booking'
          AND business_status = 'OPERATIONAL'
          AND enriched_at IS NULL
          AND (osm_phone IS NOT NULL OR osm_instagram IS NOT NULL OR osm_facebook IS NOT NULL)
    """).fetchall()]

    return enriched + booking_osm

# ── Build sheet ───────────────────────────────────────────────────────────────

COLS = [
    ("Приоритет",       "priority_label",  28),
    ("Название",        "display_name",    38),
    ("product_fit",     "product_fit",     13),
    ("Вертикаль",       "vertical",        14),
    ("Сектор",          "sector",          11),
    ("Телефон",         "phone",           18),
    ("Соцсеть",         "social",          40),
    ("Сайт",            "website",         30),
    ("Отзывов",         "user_rating_count", 9),
    ("Рейтинг",         "rating",           9),
    ("Статус записи",   "booking_status",  22),
]

def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    raw = _load(conn)
    conn.close()

    rows = []
    for r in raw:
        p = _priority(r)
        rows.append({
            "priority_label":    _priority_label(p),
            "_p":                p,
            "display_name":      r.get("display_name") or "",
            "product_fit":       r.get("product_fit") or "",
            "vertical":          r.get("vertical") or "",
            "sector":            r.get("sector") or "",
            "phone":             _phone(r),
            "social":            _social(r),
            "website":           _website(r),
            "user_rating_count": r.get("user_rating_count") if r.get("user_rating_count") is not None else "",
            "rating":            r.get("rating") if r.get("rating") is not None else "",
            "booking_status":    _booking_status(r),
        })

    # Sort: widgets last (interview list), rest by priority then reviews desc
    non_widget = sorted(
        [r for r in rows if not r["booking_status"] in
         ("Altegio","Stilio","Fresha","DIKIDI","YClients","SimplyBook","Apnt","Goldie","Setmore","widget")],
        key=lambda r: (r["_p"], -(r["user_rating_count"] if isinstance(r["user_rating_count"], int) else 0)),
    )
    widget = sorted(
        [r for r in rows if r["booking_status"] in
         ("Altegio","Stilio","Fresha","DIKIDI","YClients","SimplyBook","Apnt","Goldie","Setmore","widget")],
        key=lambda r: r["booking_status"],
    )
    all_rows = non_widget + widget

    # ── Build workbook ────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False

    # Header row
    for ci, (label, _, width) in enumerate(COLS, 1):
        c = ws.cell(1, ci, label)
        c.font      = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        c.fill      = _fill("1F3864")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = _border
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[1].height = 30
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS))}1"

    # Data rows
    for ri, row in enumerate(all_rows, 2):
        p  = row["_p"]
        bg = P_COLOR.get(p, "FFFFFF")
        # widgets get a light blue tint
        if row["booking_status"] not in P_COLOR:
            bg = "DDEEFF"

        for ci, (_, key, _) in enumerate(COLS, 1):
            c = ws.cell(ri, ci, row[key])
            c.font      = Font(name="Calibri", size=10)
            c.fill      = _fill(bg)
            c.alignment = Alignment(horizontal="left", vertical="center")
            c.border    = _border
        ws.row_dimensions[ri].height = 15

    # Summary in a second sheet
    ws2 = wb.create_sheet("Info")
    ws2.sheet_view.showGridLines = False
    info = [
        ("Лист Leads — структура данных", ""),
        ("", ""),
        ("Колонка",             "Описание"),
        ("Приоритет",          "P1 = самый горячий (соцсеть + ≥10 отзывов). P6 = нет контакта."),
        ("Название",           "Название места из Google Maps"),
        ("product_fit",        "appointment = по записи (салоны, врачи). booking = резервирование (рестораны, отели)."),
        ("Вертикаль",          "Отрасль: beauty, health, food_sitdown, fitness, lodging, events"),
        ("Сектор",             "Район Кишинёва: Centru, Buiucani, Botanica, Riscani, Ciocana"),
        ("Телефон",            "Из Google Place Details, если есть — иначе из OSM"),
        ("Соцсеть",            "Instagram или Facebook (из OSM или из ссылки в Google)"),
        ("Сайт",               "Собственный домен (соцсети вынесены в колонку Соцсеть)"),
        ("Отзывов",            "Количество отзывов Google. Пусто = данные не запрашивались (booking без API-вызова)."),
        ("Рейтинг",            "Средняя оценка 1.0–5.0. Пусто = нет данных."),
        ("Статус записи",      "нет сайта / соцсеть / сайт без виджета / Altegio / Stilio / Fresha…"),
        ("", ""),
        ("Цвет строки",        ""),
        ("Зелёный",            "P1–P2: соцсеть + отзывы → первые звонки"),
        ("Жёлтый",             "P3–P4: соцсеть без отзывов, или телефон + отзывы"),
        ("Оранжевый",          "P5: только телефон, мало отзывов"),
        ("Серый",              "P6: нет контакта"),
        ("Голубой",            "Уже автоматизированы (Altegio / Stilio / …) → лист для интервью"),
        ("", ""),
        ("Источники данных",   ""),
        ("appointment (990)",  "Google Place Details (Enterprise SKU) + OSM-обогащение"),
        ("booking (103)",      "OSM только: телефон и соцсети без API-вызовов"),
    ]
    for ri, (a, b) in enumerate(info, 1):
        ca = ws2.cell(ri, 1, a)
        cb = ws2.cell(ri, 2, b)
        if a in ("Лист Leads — структура данных", "Колонка", "Цвет строки", "Источники данных"):
            ca.font = Font(bold=True, name="Calibri", size=10, color="1F3864" if a != "Колонка" else "FFFFFF")
            if a == "Колонка":
                for c in (ca, cb):
                    c.fill = _fill("1F3864")
                    c.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        else:
            ca.font = Font(name="Calibri", size=10, bold=True)
            cb.font = Font(name="Calibri", size=10)
        ca.alignment = Alignment(vertical="center")
        cb.alignment = Alignment(vertical="center", wrap_text=True)
        ws2.row_dimensions[ri].height = 18
    ws2.column_dimensions["A"].width = 24
    ws2.column_dimensions["B"].width = 70

    wb.save(OUT)

    p_cnt = Counter(r["_p"] for r in non_widget)
    print(f"Saved: {OUT}")
    print(f"  Итого строк: {len(all_rows)}  (продажи: {len(non_widget)}, виджет/интервью: {len(widget)})")
    for p in range(1, 7):
        print(f"  {_priority_label(p)}: {p_cnt.get(p,0)}")

if __name__ == "__main__":
    main()
