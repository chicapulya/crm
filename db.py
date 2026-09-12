from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from config import DB_PATH

DDL = """
CREATE TABLE IF NOT EXISTS budget_ledger (
    month TEXT,
    sku   TEXT CHECK (sku IN ('pro','enterprise')),
    used  INTEGER DEFAULT 0,
    PRIMARY KEY (month, sku)
);

CREATE TABLE IF NOT EXISTS places (
    place_id          TEXT PRIMARY KEY,
    display_name      TEXT,
    formatted_address TEXT,
    lat               REAL,
    lng               REAL,
    primary_type      TEXT,
    types_json        TEXT,
    business_status   TEXT,
    sector            TEXT,
    vertical          TEXT,
    product_fit       TEXT,
    first_seen_query  TEXT,
    raw_json          TEXT,
    fetched_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS place_categories (
    place_id TEXT,
    category TEXT,
    PRIMARY KEY (place_id, category)
);

CREATE TABLE IF NOT EXISTS api_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category     TEXT,
    lat          REAL,
    lng          REAL,
    radius       REAL,
    result_count INTEGER,
    saturated    BOOLEAN,
    sku          TEXT DEFAULT 'pro',
    from_cache   BOOLEAN,
    ts           TIMESTAMP
);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn


def upsert_place(conn: sqlite3.Connection, place: dict[str, Any], category: str) -> bool:
    """Insert place; returns True if new, False if already existed."""
    pid = place["place_id"]
    loc = place.get("location", {})
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute("SELECT place_id FROM places WHERE place_id = ?", (pid,)).fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO places
               (place_id, display_name, formatted_address, lat, lng,
                primary_type, types_json, business_status, first_seen_query, raw_json, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                place.get("display_name"),
                place.get("formatted_address"),
                loc.get("latitude"),
                loc.get("longitude"),
                place.get("primary_type"),
                json.dumps(place.get("types", []), ensure_ascii=False),
                place.get("business_status"),
                category,
                json.dumps(place, ensure_ascii=False),
                now,
            ),
        )

    conn.execute(
        "INSERT OR IGNORE INTO place_categories (place_id, category) VALUES (?,?)",
        (pid, category),
    )
    conn.commit()
    return not existing


def log_call(
    conn: sqlite3.Connection,
    *,
    category: str,
    lat: float,
    lng: float,
    radius: float,
    result_count: int,
    saturated: bool,
    from_cache: bool,
) -> None:
    conn.execute(
        """INSERT INTO api_calls (category, lat, lng, radius, result_count, saturated, from_cache, ts)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            category, lat, lng, radius, result_count,
            int(saturated), int(from_cache),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_call_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM api_calls WHERE from_cache = 0").fetchone()
    return row[0]


def update_sectors(conn: sqlite3.Connection, assignments: dict[str, str]) -> None:
    for place_id, sector in assignments.items():
        conn.execute("UPDATE places SET sector = ? WHERE place_id = ?", (sector, place_id))
    conn.commit()
