"""
Step 6: Web crawler for booking signals.
Targets own-domain websites from enriched places (excludes social URLs).

Detects:
  - Booking widget embeds (Booksy, Fresha, SimplyBook, YClients, etc.)
  - Messenger CTAs (WhatsApp, Telegram, Viber, Facebook Messenger)

New columns in places:
  crawl_status        TEXT     ok | timeout | error | blocked | skip
  has_booking_widget  INTEGER  0/1
  booking_provider    TEXT     platform name or NULL
  has_messenger_cta   INTEGER  0/1
  messenger_platforms TEXT     JSON array e.g. ["whatsapp","telegram"]
  crawled_at          TIMESTAMP

Usage:
  python crawl.py --show-stats        candidates and coverage
  python crawl.py --dry-run           list first 20 targets, no requests
  python crawl.py                     crawl all own-domain URLs
  python crawl.py --limit 30          crawl first N
  python crawl.py --force             re-crawl already-crawled
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from places_census.config import DB_PATH

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Detection tables ──────────────────────────────────────────────────────────

BOOKING_PLATFORMS: dict[str, str] = {
    "booksy.com":             "Booksy",
    "fresha.com":             "Fresha",
    "simplybook.me":          "SimplyBook",
    "setmore.com":            "Setmore",
    "vagaro.com":             "Vagaro",
    "calendly.com":           "Calendly",
    "acuityscheduling.com":   "Acuity",
    "planfy.com":             "Planfy",
    "mindbodyonline.com":     "Mindbody",
    "treatwell.com":          "Treatwell",
    "yclients.com":           "YClients",
    "widget.yclients.com":    "YClients",
    "dikidi.net":             "DIKIDI",
    "dikidi.ru":              "DIKIDI",
    "altegio.com":            "Altegio",
    "alteg.io":               "Altegio",    # new domain since 2024
    "stilio.md":              "Stilio",
    "stilio.app":             "Stilio",
    "apnt.app":               "Apnt",
    "heygoldie.com":          "Goldie",
    "rezervy.md":             "Rezervy",
    "meest-booking.com":      "MeestBooking",
}

MESSENGER_SIGNALS: dict[str, str] = {
    "wa.me":               "whatsapp",
    "api.whatsapp.com":    "whatsapp",
    "whatsapp.com/send":   "whatsapp",
    "t.me/":               "telegram",
    "telegram.me/":        "telegram",
    "viber.com/":          "viber",
    "m.me/":               "messenger",
}

# Treated as non-own-domain: link aggregators, review platforms, booking platforms
# whose URL IS the website_uri (business has no own site beyond the platform page).
SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com",
    "vk.com", "tiktok.com", "linktr.ee",
    "mst.link",         # Moldovan link-in-bio
    "4sq.com",          # Foursquare
    "kvradar.com",      # platform link
)

MAX_BODY_BYTES = 512_000   # read at most 500 KB per page
TIMEOUT_S      = 12.0
CONCURRENCY    = 8
UA = "chisinau-crm-crawler/1.0 (market research; denis3.ciorba@gmail.com)"

_DDL_COLS = [
    ("crawl_status",        "TEXT"),
    ("has_booking_widget",  "INTEGER"),
    ("booking_provider",    "TEXT"),
    ("has_messenger_cta",   "INTEGER"),
    ("messenger_platforms", "TEXT"),
    ("crawled_at",          "TIMESTAMP"),
]


# ── DB helpers ────────────────────────────────────────────────────────────────

def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}
    for col, typ in _DDL_COLS:
        if col not in existing:
            conn.execute(f"ALTER TABLE places ADD COLUMN {col} {typ}")
    conn.commit()


def _is_own_domain(url: str) -> bool:
    if not url:
        return False
    return not any(d in url for d in SOCIAL_DOMAINS)


def _load_targets_safe(conn: sqlite3.Connection, force: bool) -> list[dict]:
    """Same as _load_targets but handles missing crawl_status column."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}
    has_crawl = "crawl_status" in cols
    rows = conn.execute("""
        SELECT place_id, display_name, website_uri, vertical, sector
        FROM places
        WHERE enriched_at IS NOT NULL
          AND website_uri IS NOT NULL
    """).fetchall()
    out = []
    for r in rows:
        url = r["website_uri"]
        if not _is_own_domain(url):
            continue
        if not force and has_crawl:
            status = conn.execute(
                "SELECT crawl_status FROM places WHERE place_id = ?", (r["place_id"],)
            ).fetchone()
            if status and status[0] is not None:
                continue
        out.append(dict(r))
    return out


def _store(conn: sqlite3.Connection, place_id: str, result: dict) -> None:
    conn.execute(
        """UPDATE places SET
            crawl_status        = ?,
            has_booking_widget  = ?,
            booking_provider    = ?,
            has_messenger_cta   = ?,
            messenger_platforms = ?,
            crawled_at          = ?
        WHERE place_id = ?""",
        (
            result["status"],
            int(bool(result["booking_provider"])),
            result["booking_provider"],
            int(bool(result["messengers"])),
            json.dumps(result["messengers"], ensure_ascii=False) if result["messengers"] else None,
            datetime.now(timezone.utc).isoformat(),
            place_id,
        ),
    )
    conn.commit()


# ── Detection ─────────────────────────────────────────────────────────────────

def _detect(html: str) -> tuple[str | None, list[str]]:
    """Return (booking_provider | None, [messenger, ...])."""
    lower = html.lower()

    booking = None
    for domain, name in BOOKING_PLATFORMS.items():
        if domain in lower:
            booking = name
            break

    messengers: list[str] = []
    seen: set[str] = set()
    for signal, platform in MESSENGER_SIGNALS.items():
        if signal in lower and platform not in seen:
            messengers.append(platform)
            seen.add(platform)

    return booking, messengers


# ── Async fetch ───────────────────────────────────────────────────────────────

def _provider_from_url(url: str) -> str | None:
    """If the website_uri itself points to a booking platform, return the provider name."""
    lower = url.lower()
    for domain, name in BOOKING_PLATFORMS.items():
        if domain in lower:
            return name
    return None


async def _fetch(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    target: dict,
) -> tuple[str, dict]:
    url = target["website_uri"]
    pid = target["place_id"]
    result: dict = {"status": "error", "booking_provider": None, "messengers": []}

    # Short-circuit: if the URL itself is a booking platform, no crawl needed.
    provider = _provider_from_url(url)
    if provider:
        return pid, {"status": "ok", "booking_provider": provider, "messengers": []}

    async with sem:
        try:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code == 403:
                    result["status"] = "blocked"
                    return pid, result
                resp.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=16_384):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BODY_BYTES:
                        break
                html = b"".join(chunks).decode("utf-8", errors="replace")

            booking, messengers = _detect(html)
            result = {
                "status":           "ok",
                "booking_provider": booking,
                "messengers":       messengers,
            }
        except httpx.TimeoutException:
            result["status"] = "timeout"
        except httpx.HTTPStatusError as e:
            result["status"] = f"error"
        except Exception:
            result["status"] = "error"

    return pid, result


# ── Main logic ────────────────────────────────────────────────────────────────

def _show_stats(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(places)").fetchall()}

    enriched_own = conn.execute("""
        SELECT COUNT(*) FROM places
        WHERE enriched_at IS NOT NULL AND website_uri IS NOT NULL
    """).fetchone()[0]
    social = sum(
        1 for r in conn.execute(
            "SELECT website_uri FROM places WHERE enriched_at IS NOT NULL AND website_uri IS NOT NULL"
        ).fetchall()
        if not _is_own_domain(r[0])
    )
    own = enriched_own - social

    print(f"Enriched with website_uri : {enriched_own}")
    print(f"  Social URL (skip)       : {social}")
    print(f"  Own domain (crawl)      : {own}")

    if "crawl_status" in cols:
        rows = conn.execute("""
            SELECT crawl_status, COUNT(*) n FROM places
            WHERE crawl_status IS NOT NULL
            GROUP BY crawl_status ORDER BY n DESC
        """).fetchall()
        print("\nAlready crawled:")
        for r in rows:
            print(f"  {r[0]:<10} {r[1]}")

        has_widget = conn.execute(
            "SELECT COUNT(*) FROM places WHERE has_booking_widget = 1"
        ).fetchone()[0]
        has_msg = conn.execute(
            "SELECT COUNT(*) FROM places WHERE has_messenger_cta = 1"
        ).fetchone()[0]
        crawled_ok = conn.execute(
            "SELECT COUNT(*) FROM places WHERE crawl_status = 'ok'"
        ).fetchone()[0]
        if crawled_ok:
            print(f"\nOf ok crawls ({crawled_ok}):")
            print(f"  has_booking_widget : {has_widget}  ({has_widget/crawled_ok:.1%})")
            print(f"  has_messenger_cta  : {has_msg}  ({has_msg/crawled_ok:.1%})")


async def _run(conn: sqlite3.Connection, targets: list[dict]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY + 4, max_keepalive_connections=CONCURRENCY)
    timeout = httpx.Timeout(TIMEOUT_S, connect=8.0)

    ok = timeout_n = error_n = blocked_n = 0
    booking_hits = 0
    msg_hits = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": UA},
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    ) as client:
        tasks = [_fetch(client, sem, t) for t in targets]
        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            pid, result = await coro

            # find display_name for logging
            name = next((t["display_name"] for t in targets if t["place_id"] == pid), pid)
            url  = next((t["website_uri"]  for t in targets if t["place_id"] == pid), "")

            _store(conn, pid, result)

            status = result["status"]
            bp     = result["booking_provider"] or ""
            msgs   = result["messengers"]

            if status == "ok":
                ok += 1
                if bp:       booking_hits += 1
                if msgs:     msg_hits += 1
                tag = f"[{bp}]" if bp else ("MSG" if msgs else "   ")
                print(f"  [{i:3d}/{len(targets)}] {status:<7} {tag:<12} "
                      f"{','.join(msgs) or '':<20} {name[:35]}")
            else:
                if status == "timeout":  timeout_n  += 1
                elif status == "blocked": blocked_n += 1
                else:                    error_n    += 1
                print(f"  [{i:3d}/{len(targets)}] {status:<7}              "
                      f"                     {name[:35]}  {url[:40]}")

    total = len(targets)
    ok_pct = ok / total if total else 0
    print(f"\n{'─'*60}")
    print(f"Crawled: {total}  |  ok: {ok} ({ok_pct:.0%})  "
          f"timeout: {timeout_n}  blocked: {blocked_n}  error: {error_n}")
    if ok:
        print(f"Booking widget found : {booking_hits}/{ok}  ({booking_hits/ok:.1%})")
        print(f"Messenger CTA found  : {msg_hits}/{ok}  ({msg_hits/ok:.1%})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 6: Crawl own-domain websites for booking signals")
    ap.add_argument("--show-stats", action="store_true", help="Show coverage stats and exit")
    ap.add_argument("--dry-run",    action="store_true", help="List targets, no requests")
    ap.add_argument("--limit",      type=int,            help="Crawl first N targets")
    ap.add_argument("--force",      action="store_true", help="Re-crawl already-crawled targets")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_columns(conn)

    if args.show_stats:
        _show_stats(conn)
        conn.close()
        return

    targets = _load_targets_safe(conn, args.force)  # noqa: F821 (only func now)
    if args.limit:
        targets = targets[: args.limit]

    if args.dry_run:
        print(f"Own-domain targets to crawl: {len(targets)}")
        for t in targets[:20]:
            print(f"  {t['vertical']:<14} {t['display_name'][:35]:<35}  {t['website_uri']}")
        conn.close()
        return

    print(f"Crawling {len(targets)} own-domain URLs  (concurrency={CONCURRENCY})")
    print()
    asyncio.run(_run(conn, targets))
    conn.close()


if __name__ == "__main__":
    main()
