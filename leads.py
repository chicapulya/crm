"""
Step 7a: Generate leads CSV files from enriched data.

Outputs:
  leads_main.csv       — sales targets (no confirmed booking widget)
  leads_interviews.csv — 105 automated (Altegio / Stilio / etc.) for product interviews

Priority in main list:
  P1  no_booking + has_social + userRatingCount >= 10   ← first call list
  P2  no_booking + has_social + userRatingCount 1-9
  P3  no_booking + has_social + userRatingCount = 0
  P4  no_booking + no_social  + has_phone + reviews >= 10
  P5  no_booking + no_social  + has_phone + reviews 1-9
  P6  no_booking + no_phone   + any
"""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
from pathlib import Path

from config import DB_PATH

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_MAIN       = Path(__file__).parent / "leads_main.csv"
OUT_INTERVIEWS = Path(__file__).parent / "leads_interviews.csv"

SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com",
    "vk.com", "tiktok.com", "linktr.ee", "mst.link", "4sq.com",
)


# ── Derivation helpers ────────────────────────────────────────────────────────

def _best_phone(row: dict) -> str:
    return row.get("national_phone_number") or row.get("osm_phone") or ""


def _social_url(row: dict) -> str:
    """Best social presence URL: Instagram > Facebook > social website_uri."""
    if row.get("osm_instagram"):
        return row["osm_instagram"]
    if row.get("osm_facebook"):
        return row["osm_facebook"]
    w = row.get("website_uri") or ""
    if any(d in w for d in SOCIAL_DOMAINS):
        return w
    return ""


def _booking_status(row: dict) -> str:
    # Confirmed via crawl
    if row.get("has_booking_widget"):
        provider = row.get("booking_provider") or "widget"
        return f"widget:{provider}"
    # Fallback: website_uri itself IS a booking platform (missed by crawl)
    w = row.get("website_uri") or ""
    if w and not row.get("crawl_status"):
        for domain, name in {
            "alteg.io": "Altegio", "altegio.com": "Altegio",
            "stilio.md": "Stilio", "stilio.app": "Stilio",
            "fresha.com": "Fresha", "dikidi.net": "DIKIDI",
            "simplybook.me": "SimplyBook", "yclients.com": "YClients",
        }.items():
            if domain in w:
                return f"widget:{name}"
    status = row.get("crawl_status")
    if status == "ok":
        return "site_no_widget"
    if status in ("blocked", "error", "timeout"):
        return "site_unclear"
    w = row.get("website_uri") or ""
    if w and any(d in w for d in SOCIAL_DOMAINS):
        return "social_only"
    if w:
        return "site_not_crawled"
    return "no_website"


def _priority(row: dict, social: str, phone: str, reviews: int) -> int:
    """Lower number = higher priority."""
    has_social = bool(social)
    has_phone  = bool(phone)
    if has_social and reviews >= 10:   return 1
    if has_social and reviews >= 1:    return 2
    if has_social and reviews == 0:    return 3
    if has_phone  and reviews >= 10:   return 4
    if has_phone  and reviews >= 1:    return 5
    return 6


# ── Load ──────────────────────────────────────────────────────────────────────

def _load(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT
            place_id, display_name, product_fit, vertical, sector,
            national_phone_number, osm_phone,
            osm_instagram, osm_facebook, website_uri,
            user_rating_count, rating,
            has_booking_widget, booking_provider, crawl_status,
            osm_matched
        FROM places
        WHERE enriched_at IS NOT NULL
    """).fetchall()
    return [dict(r) for r in rows]


# ── Write helpers ─────────────────────────────────────────────────────────────

MAIN_COLS = [
    "priority", "display_name", "product_fit", "vertical", "sector",
    "phone", "social_url", "user_rating_count", "rating",
    "booking_status",
]

INTERVIEW_COLS = [
    "booking_provider", "display_name", "product_fit", "vertical", "sector",
    "phone", "social_url", "user_rating_count", "rating", "website_uri",
]


def _write_csv(path: Path, cols: list[str], data: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)


# ── Main ──────────────────────────────────────────────────────────────────────

def run() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = _load(conn)
    conn.close()

    main_rows: list[dict] = []
    interview_rows: list[dict] = []

    for r in rows:
        phone    = _best_phone(r)
        social   = _social_url(r)
        reviews  = r.get("user_rating_count") or 0
        bstatus  = _booking_status(r)
        rating   = r.get("rating") or ""

        flat = {
            "display_name":    r["display_name"],
            "product_fit":     r["product_fit"],
            "vertical":        r["vertical"],
            "sector":          r["sector"],
            "phone":           phone,
            "social_url":      social,
            "user_rating_count": reviews,
            "rating":          rating,
            "booking_status":  bstatus,
            "website_uri":     r.get("website_uri") or "",
            "booking_provider": r.get("booking_provider") or "",
        }

        if bstatus.startswith("widget:"):
            flat["booking_provider"] = bstatus[len("widget:"):]
            interview_rows.append(flat)
        else:
            flat["priority"] = _priority(r, social, phone, reviews)
            main_rows.append(flat)

    # Sort main: priority ASC, reviews DESC
    main_rows.sort(key=lambda x: (x["priority"], -(x["user_rating_count"] or 0)))

    # Sort interviews: Altegio first (largest share), then by reviews DESC
    _provider_rank = {"Altegio": 0, "Stilio": 1, "DIKIDI": 2, "Fresha": 3}
    interview_rows.sort(key=lambda x: (
        _provider_rank.get(x["booking_provider"], 99),
        -(x["user_rating_count"] or 0),
    ))

    _write_csv(OUT_MAIN, MAIN_COLS, main_rows)
    _write_csv(OUT_INTERVIEWS, INTERVIEW_COLS, interview_rows)

    # ── Console summary ──────────────────────────────────────────────────────
    from collections import Counter
    p_cnt = Counter(r["priority"] for r in main_rows)
    bstatus_cnt = Counter(r["booking_status"] for r in main_rows)

    print(f"leads_main.csv       → {len(main_rows):>4} rows")
    print(f"leads_interviews.csv → {len(interview_rows):>4} rows")
    print()

    print("Main list — priority breakdown:")
    labels = {
        1: "P1  no_booking + social + ≥10 reviews",
        2: "P2  no_booking + social + 1–9 reviews",
        3: "P3  no_booking + social + 0 reviews",
        4: "P4  no_booking + phone  + ≥10 reviews",
        5: "P5  no_booking + phone  + 1–9 reviews",
        6: "P6  no contact / unclear",
    }
    for p in sorted(labels):
        print(f"  {labels[p]:<44} {p_cnt.get(p, 0):>4}")
    print()

    print("Main list — booking status breakdown:")
    for s, n in sorted(bstatus_cnt.items(), key=lambda x: -x[1]):
        print(f"  {s:<22} {n:>4}")
    print()

    print("Interview list — platform breakdown:")
    pr_cnt = Counter(r["booking_provider"] for r in interview_rows)
    for pl, n in pr_cnt.most_common():
        print(f"  {pl:<20} {n:>4}")
    print()

    # First call list preview
    p1 = [r for r in main_rows if r["priority"] == 1]
    print(f"First call list (P1, n={len(p1)}) — top 10:")
    print(f"  {'#reviews':>7}  {'name':<38}  {'phone':<18}  social")
    for r in p1[:10]:
        print(f"  {r['user_rating_count']:>7}  {r['display_name'][:38]:<38}  "
              f"{r['phone'][:18]:<18}  {r['social_url'][:45]}")


if __name__ == "__main__":
    run()
