"""
Step 5: Enterprise Place Details enrichment.

Fetches rating, userRatingCount, websiteUri, nationalPhoneNumber,
regularOpeningHours for places with product_fit appointment/booking.

Candidate priority:
  1. Vertical: beauty → health → food_sitdown → fitness → lodging → events
  2. OSM contact gap: no contact at all → partial → both already known

Usage:
  python enrich.py --show-budget        # print budget status and exit
  python enrich.py --dry-run            # count candidates, no API calls
  python enrich.py --limit 50           # first batch (default — verify before full run)
  python enrich.py --limit 990          # full run
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone

import httpx

from places_census.api.budget import BudgetExhausted, detect_sku, google_call, print_budget_status
from places_census.config import DB_PATH

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DETAILS_BASE = "https://places.googleapis.com/v1/places"
FIELD_MASK = "rating,userRatingCount,websiteUri,nationalPhoneNumber,regularOpeningHours,businessStatus"

# Exactly these keys are expected in any Place Details response.
# `id` is always returned by the API even without being in the mask.
# Any key outside this set means a billing-scope surprise — stop and fix.
_EXPECTED_KEYS = frozenset({
    "id", "rating", "userRatingCount", "websiteUri",
    "nationalPhoneNumber", "regularOpeningHours", "businessStatus",
})

_DDL_COLS = [
    ("rating",                "REAL"),
    ("user_rating_count",     "INTEGER"),
    ("website_uri",           "TEXT"),
    ("national_phone_number", "TEXT"),
    ("regular_opening_hours", "TEXT"),
    ("enriched_at",           "TIMESTAMP"),
]


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}
    for col, typ in _DDL_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE places ADD COLUMN {col} {typ}")
    conn.commit()


def _load_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT place_id, display_name, vertical, product_fit, sector,
               osm_website, osm_phone
        FROM places
        WHERE product_fit IN ('appointment', 'booking')
          AND business_status = 'OPERATIONAL'
          AND enriched_at IS NULL
        ORDER BY
            -- 1. appointment before booking (real scheduling leads first)
            CASE product_fit WHEN 'appointment' THEN 0 ELSE 1 END,
            -- 2. vertical priority within each product_fit tier
            CASE vertical
                WHEN 'beauty'       THEN 1
                WHEN 'health'       THEN 2
                WHEN 'fitness'      THEN 3
                WHEN 'food_sitdown' THEN 4
                WHEN 'lodging'      THEN 5
                WHEN 'events'       THEN 6
                ELSE 7
            END,
            -- 3. central sectors first (Centru → Buiucani → Botanica → Rîșcani → Ciocana)
            CASE sector
                WHEN 'Centru'   THEN 1
                WHEN 'Buiucani' THEN 2
                WHEN 'Botanica' THEN 3
                WHEN 'Rîșcani'  THEN 4
                WHEN 'Riscani'  THEN 4
                WHEN 'Ciocana'  THEN 5
                ELSE 6
            END,
            -- 4. OSM contact gaps first (no contact → highest need for enrichment)
            CASE
                WHEN osm_website IS NULL AND osm_phone IS NULL THEN 0
                WHEN osm_website IS NULL OR  osm_phone IS NULL THEN 1
                ELSE 2
            END,
            display_name
    """).fetchall()
    return [dict(r) for r in rows]


def _parse_and_store(conn: sqlite3.Connection, place_id: str, data: dict) -> None:
    hours_raw = data.get("regularOpeningHours")
    conn.execute(
        """UPDATE places SET
            rating                = ?,
            user_rating_count     = ?,
            website_uri           = ?,
            national_phone_number = ?,
            regular_opening_hours = ?,
            enriched_at           = ?
        WHERE place_id = ?""",
        (
            data.get("rating"),
            data.get("userRatingCount"),
            data.get("websiteUri"),
            data.get("nationalPhoneNumber"),
            json.dumps(hours_raw, ensure_ascii=False) if hours_raw else None,
            datetime.now(timezone.utc).isoformat(),
            place_id,
        ),
    )
    conn.commit()


def _dry_run_report(conn: sqlite3.Connection) -> None:
    candidates = _load_candidates(conn)
    print(f"SKU that will be billed: {detect_sku(FIELD_MASK).upper()}")
    print(f"Field mask: {FIELD_MASK}")
    print()
    print(f"Candidates (unenriched, appointment/booking, operational): {len(candidates):,}")
    by_v: dict[str, int] = {}
    for c in candidates:
        by_v[c["vertical"]] = by_v.get(c["vertical"], 0) + 1
    for v in ["beauty", "health", "food_sitdown", "fitness", "lodging", "events", "other"]:
        if v in by_v:
            print(f"  {v:<14} {by_v[v]:>5}")
    no_any = sum(1 for c in candidates if not c["osm_website"] and not c["osm_phone"])
    has_partial = sum(1 for c in candidates
                      if bool(c["osm_website"]) != bool(c["osm_phone"]))
    has_both = sum(1 for c in candidates if c["osm_website"] and c["osm_phone"])
    print(f"\nOSM contact coverage in candidates:")
    print(f"  no contact at all : {no_any:>5} ({no_any/len(candidates):.1%})")
    print(f"  partial (one field): {has_partial:>5} ({has_partial/len(candidates):.1%})")
    print(f"  both web+phone    : {has_both:>5} ({has_both/len(candidates):.1%})")


async def _run_enrichment(conn: sqlite3.Connection, limit: int) -> None:
    candidates = _load_candidates(conn)
    total = len(candidates)
    batch = candidates[:limit]

    print(f"Candidates unenriched: {total:,}  |  this run: {len(batch)} (limit={limit})")
    print(f"SKU: {detect_sku(FIELD_MASK).upper()}  |  mask: {FIELD_MASK}")
    print()

    ok = errors = 0

    async with httpx.AsyncClient(timeout=30.0) as http:
        for i, c in enumerate(batch, 1):
            pid = c["place_id"]
            url = f"{DETAILS_BASE}/{pid}"
            try:
                data = await google_call(
                    url=url,
                    body={},
                    field_mask=FIELD_MASK,
                    conn=conn,
                    http_client=http,
                    method="GET",
                )

                # First response: dump keys and stop if anything unexpected came back.
                if ok == 0:
                    returned_keys = sorted(data.keys())
                    unexpected = set(data.keys()) - _EXPECTED_KEYS
                    print(f"First response keys ({len(returned_keys)}): {returned_keys}")
                    if unexpected:
                        print(f"\nSTOP — unexpected keys in response: {unexpected}")
                        print("Fix the field mask before proceeding. No data written.")
                        break
                    print()

                _parse_and_store(conn, pid, data)
                ok += 1
                rating  = data.get("rating", "—")
                reviews = data.get("userRatingCount", 0)
                web     = "web" if data.get("websiteUri")           else "   "
                phone   = "ph"  if data.get("nationalPhoneNumber")  else "  "
                hours   = "hr"  if data.get("regularOpeningHours")  else "  "
                print(f"  [{i:3d}/{len(batch)}] {c['display_name'][:38]:<38} "
                      f"★{str(rating):<4}  #{reviews:>5}  {web} {phone} {hours}")
            except BudgetExhausted as e:
                print(f"\nBudget exhausted after {ok} calls: {e}")
                break
            except Exception as e:
                errors += 1
                print(f"  [{i:3d}] ERROR {pid}: {e}")

    print(f"\nDone: {ok} enriched, {errors} errors.")
    print()
    print_budget_status(conn)

    if ok == limit:
        print(f"\nRan exactly {limit} calls — verify budget_ledger, then run with --limit 990.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 5: Enterprise Place Details enrichment")
    ap.add_argument("--show-budget", action="store_true", help="Print budget status and exit")
    ap.add_argument("--dry-run",     action="store_true", help="Count candidates, no API calls")
    ap.add_argument("--limit",       type=int, default=50,
                    help="Max calls this run (default 50 — verify before full run)")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_columns(conn)

    if args.show_budget:
        print_budget_status(conn)
        conn.close()
        return

    if args.dry_run:
        _dry_run_report(conn)
        conn.close()
        return

    asyncio.run(_run_enrichment(conn, args.limit))
    conn.close()


if __name__ == "__main__":
    main()
