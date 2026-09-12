"""
Exports leads to chisinau_leads.xlsx — three sheets:
  Сводка, Лиды, Интервью
"""
from __future__ import annotations

import io
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from places_census.config import DB_PATH

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from places_census.config import EXPORT_XLSX as OUT

SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com",
    "vk.com", "tiktok.com", "linktr.ee", "mst.link", "4sq.com",
)
BOOKING_URL_DOMAINS = {
    "alteg.io": "Altegio", "altegio.com": "Altegio",
    "stilio.md": "Stilio", "stilio.app": "Stilio",
    "fresha.com": "Fresha", "dikidi.net": "DIKIDI",
    "simplybook.me": "SimplyBook", "yclients.com": "YClients",
}

# ── Styles ────────────────────────────────────────────────────────────────────
_thin   = Side(style="thin", color="BFBFBF")
_border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

def _fill(hex6: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex6)

def _hdr_cell(cell, text: str, bg: str = "1F3864") -> None:
    cell.value = text
    cell.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
    cell.fill = _fill(bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = _border

def _data_cell(cell, value, bg: str = "FFFFFF", wrap: bool = False) -> None:
    cell.value = value
    cell.font = Font(name="Calibri", size=10)
    cell.fill = _fill(bg)
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=wrap)
    cell.border = _border

def _set_widths(ws, widths: list[float]) -> None:
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

# ── Data helpers ──────────────────────────────────────────────────────────────

def _phone(r: dict) -> str:
    return r.get("national_phone_number") or r.get("osm_phone") or ""

def _social(r: dict) -> str:
    if r.get("osm_instagram"):  return r["osm_instagram"]
    if r.get("osm_facebook"):   return r["osm_facebook"]
    w = r.get("website_uri") or ""
    if w and any(d in w for d in SOCIAL_DOMAINS): return w
    return ""

def _is_widget(r: dict) -> bool:
    if r.get("has_booking_widget"): return True
    w = r.get("website_uri") or ""
    return bool(w) and not r.get("crawl_status") and any(d in w for d in BOOKING_URL_DOMAINS)

def _platform(r: dict) -> str:
    if r.get("booking_provider"): return r["booking_provider"]
    w = r.get("website_uri") or ""
    for d, name in BOOKING_URL_DOMAINS.items():
        if d in w: return name
    return ""

def _bstatus(r: dict) -> str:
    if _is_widget(r): return _platform(r) or "widget"
    cs = r.get("crawl_status")
    w  = r.get("website_uri") or ""
    if cs == "ok":    return "сайт, нет виджета"
    if cs in ("blocked", "error", "timeout"): return "сайт, не проверено"
    if w and any(d in w for d in SOCIAL_DOMAINS): return "только соцсеть"
    if w:             return "сайт (не сканировался)"
    return "нет сайта"

def _priority(r: dict) -> int:
    s = bool(_social(r)); p = bool(_phone(r)); n = r.get("user_rating_count") or 0
    if s and n >= 10: return 1
    if s and n >= 1:  return 2
    if s:             return 3
    if p and n >= 10: return 4
    if p and n >= 1:  return 5
    return 6

# ── Sheet: Сводка ─────────────────────────────────────────────────────────────

def _summary(ws, rows: list[dict], conn: sqlite3.Connection) -> None:
    ws.title = "Сводка"
    ws.sheet_view.showGridLines = False
    _set_widths(ws, [42, 12, 44])

    def row_title(r: int, text: str) -> None:
        c = ws.cell(r, 1, text)
        c.font = Font(bold=True, size=12, color="1F3864", name="Calibri")
        c.alignment = Alignment(vertical="center")
        ws.row_dimensions[r].height = 24

    def row_section(r: int, text: str) -> None:
        c = ws.cell(r, 1, text)
        c.font = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
        c.fill = _fill("1F3864")
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[r].height = 20

    def row_kv(r: int, label: str, val, note: str = "") -> None:
        ws.cell(r, 1, label).font = Font(name="Calibri", size=10)
        c = ws.cell(r, 2, val)
        c.font = Font(bold=True, name="Calibri", size=10)
        c.alignment = Alignment(horizontal="right", vertical="center")
        ws.cell(r, 3, note).font = Font(name="Calibri", size=10, color="595959")
        ws.row_dimensions[r].height = 16

    def row_kv_fill(r: int, label: str, val, note: str, bg: str) -> None:
        for ci in (1, 2, 3):
            ws.cell(r, ci).fill = _fill(bg)
        row_kv(r, label, val, note)

    r = 1
    row_title(r, f"Кишинёв CRM — Итоги фазы 2   ({datetime.now().strftime('%d.%m.%Y')})")
    r = 3

    # ── База ─────────────────────────────────────────────────────────────────
    row_section(r, "База данных"); r += 1
    total   = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    oper    = conn.execute("SELECT COUNT(*) FROM places WHERE business_status='OPERATIONAL'").fetchone()[0]
    enr     = len(rows)
    dupes   = conn.execute("SELECT COUNT(*) FROM duplicates").fetchone()[0]
    row_kv(r, "Всего мест (фаза 1)",                    total);  r += 1
    row_kv(r, "Из них OPERATIONAL",                     oper);   r += 1
    row_kv(r, "Обогащено через Place Details (фаза 2)", enr);    r += 1
    row_kv(r, "Геодупликатов помечено",                 dupes);  r += 1

    r += 1
    # ── Покрытие ─────────────────────────────────────────────────────────────
    row_section(r, "Веб-покрытие (990 обогащённых)"); r += 1
    no_web   = sum(1 for x in rows if not x.get("website_uri"))
    social_w = sum(1 for x in rows if x.get("website_uri") and any(d in x["website_uri"] for d in SOCIAL_DOMAINS))
    widget_n = sum(1 for x in rows if _is_widget(x))
    own_no_w = sum(1 for x in rows if x.get("crawl_status") == "ok" and not _is_widget(x))
    row_kv(r, "Нет сайта",                      no_web,   "→ онлайн-запись точно отсутствует"); r += 1
    row_kv(r, "Только соцсеть / платформа",      social_w, "→ онлайн-запись точно отсутствует"); r += 1
    row_kv(r, "Собственный сайт, нет виджета",   own_no_w, "→ запись не автоматизирована");      r += 1
    row_kv(r, "Booking-виджет подтверждён",       widget_n, "→ уже автоматизированы");            r += 1

    r += 1
    # ── Платформы ────────────────────────────────────────────────────────────
    row_section(r, "Booking-платформы (106 автоматизированных)"); r += 1
    plat_cnt = Counter(_platform(x) for x in rows if _is_widget(x))
    for plat, n in plat_cnt.most_common():
        note = ""
        if plat == "Altegio":   note = "Полная CRM — источник интервью"
        elif plat in ("Stilio", "Fresha", "DIKIDI"): note = "Виджет без полной CRM"
        row_kv(r, plat, n, note); r += 1

    r += 1
    # ── Воронка ──────────────────────────────────────────────────────────────
    row_section(r, "Воронка продаж — основной список (884 строки)"); r += 1
    main_rows = [x for x in rows if not _is_widget(x)]
    p_cnt = Counter(_priority(x) for x in main_rows)
    labels = {
        1: ("P1  нет записи + соцсеть + ≥10 отзывов",  "C6EFCE"),
        2: ("P2  нет записи + соцсеть + 1–9 отзывов",  "C6EFCE"),
        3: ("P3  нет записи + соцсеть + 0 отзывов",    "FFEB9C"),
        4: ("P4  нет записи + телефон + ≥10 отзывов",  "FFEB9C"),
        5: ("P5  нет записи + телефон + 1–9 отзывов",  "FCE4D6"),
        6: ("P6  нет контакта / статус неизвестен",     "F2F2F2"),
    }
    for p, (label, bg) in labels.items():
        row_kv_fill(r, label, p_cnt.get(p, 0), "", bg); r += 1


# ── Sheet: Лиды ──────────────────────────────────────────────────────────────

def _leads(ws, rows: list[dict]) -> None:
    ws.title = "Lidy"          # ASCII title to avoid encoding issues in some Excel versions

    cols   = ["Prio", "Название",       "product_fit", "Вертикаль", "Сектор",
               "Телефон",       "Соцсеть",       "Отзывов", "Рейтинг", "Статус записи"]
    widths = [6,      38,               13,            14,          12,
               18,             40,              9,         9,         22]

    for ci, (h, w) in enumerate(zip(cols, widths), 1):
        _hdr_cell(ws.cell(1, ci), h)
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"

    P_FILL = {1: "C6EFCE", 2: "C6EFCE", 3: "FFEB9C", 4: "FFEB9C", 5: "FCE4D6", 6: "F2F2F2"}

    for ri, r in enumerate(rows, 2):
        p   = _priority(r)
        bg  = P_FILL.get(p, "FFFFFF")
        vals = [p, r.get("display_name"), r.get("product_fit"), r.get("vertical"),
                r.get("sector"), _phone(r), _social(r),
                r.get("user_rating_count") or 0, r.get("rating") or "", _bstatus(r)]
        for ci, v in enumerate(vals, 1):
            _data_cell(ws.cell(ri, ci), v, bg)
        ws.row_dimensions[ri].height = 15


# ── Sheet: Интервью ───────────────────────────────────────────────────────────

def _interviews(ws, rows: list[dict]) -> None:
    ws.title = "Interviu"      # ASCII

    cols   = ["Платформа",     "Название",       "product_fit", "Вертикаль", "Сектор",
               "Телефон",       "Соцсеть",        "Отзывов",    "Рейтинг",   "Сайт"]
    widths = [12,              38,               13,            14,          12,
               18,             40,               9,             9,           44]

    for ci, (h, w) in enumerate(zip(cols, widths), 1):
        _hdr_cell(ws.cell(1, ci), h)
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"

    _rank = {"Altegio": 0, "Stilio": 1, "DIKIDI": 2, "Fresha": 3}
    rows_s = sorted(rows, key=lambda r: (_rank.get(_platform(r), 99), -(r.get("user_rating_count") or 0)))

    for ri, r in enumerate(rows_s, 2):
        vals = [_platform(r), r.get("display_name"), r.get("product_fit"), r.get("vertical"),
                r.get("sector"), _phone(r), _social(r),
                r.get("user_rating_count") or 0, r.get("rating") or "",
                r.get("website_uri") or ""]
        for ci, v in enumerate(vals, 1):
            _data_cell(ws.cell(ri, ci), v)
        ws.row_dimensions[ri].height = 15


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("""
        SELECT display_name, product_fit, vertical, sector,
               national_phone_number, osm_phone, osm_instagram, osm_facebook,
               website_uri, user_rating_count, rating,
               has_booking_widget, booking_provider, crawl_status
        FROM places WHERE enriched_at IS NOT NULL

        UNION ALL

        -- booking places not yet enriched via API: use OSM contacts only
        SELECT display_name, product_fit, vertical, sector,
               NULL as national_phone_number, osm_phone, osm_instagram, osm_facebook,
               osm_website as website_uri, NULL as user_rating_count, NULL as rating,
               NULL as has_booking_widget, NULL as booking_provider, NULL as crawl_status
        FROM places
        WHERE product_fit = 'booking'
          AND business_status = 'OPERATIONAL'
          AND enriched_at IS NULL
          AND (osm_phone IS NOT NULL OR osm_instagram IS NOT NULL OR osm_facebook IS NOT NULL)
    """).fetchall()]

    main_rows = sorted(
        [r for r in rows if not _is_widget(r)],
        key=lambda r: (_priority(r), -(r.get("user_rating_count") or 0)),
    )
    interview_rows = [r for r in rows if _is_widget(r)]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    _summary(wb.create_sheet(), main_rows + interview_rows, conn)
    _leads(wb.create_sheet(), main_rows)
    _interviews(wb.create_sheet(), interview_rows)

    conn.close()
    wb.save(OUT)

    print(f"Saved: {OUT}")
    print(f"  Sheet 1 (Сводка):   метрики")
    print(f"  Sheet 2 (Lidy):     {len(main_rows)} строк")
    print(f"  Sheet 3 (Interviu): {len(interview_rows)} строк")


if __name__ == "__main__":
    main()
