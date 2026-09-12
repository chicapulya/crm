import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import db as db_mod


def _make_place(pid: str, ptype: str = "cafe") -> dict:
    return {
        "place_id": pid,
        "display_name": f"Place {pid}",
        "formatted_address": "Test Street 1",
        "location": {"latitude": 47.02, "longitude": 28.83},
        "types": [ptype],
        "primary_type": ptype,
        "business_status": "OPERATIONAL",
        "google_maps_uri": "",
    }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db_mod.DDL)
    conn.commit()
    return conn


def test_upsert_new_place_returns_true():
    conn = _conn()
    p = _make_place("abc123")
    assert db_mod.upsert_place(conn, p, "cafe") is True


def test_upsert_duplicate_returns_false():
    conn = _conn()
    p = _make_place("abc123")
    db_mod.upsert_place(conn, p, "cafe")
    assert db_mod.upsert_place(conn, p, "coffee_shop") is False


def test_duplicate_does_not_create_extra_row():
    conn = _conn()
    p = _make_place("abc123")
    db_mod.upsert_place(conn, p, "cafe")
    db_mod.upsert_place(conn, p, "coffee_shop")
    count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    assert count == 1


def test_multiple_categories_for_same_place():
    conn = _conn()
    p = _make_place("abc123")
    db_mod.upsert_place(conn, p, "cafe")
    db_mod.upsert_place(conn, p, "coffee_shop")
    rows = conn.execute("SELECT category FROM place_categories WHERE place_id='abc123'").fetchall()
    cats = {r[0] for r in rows}
    assert cats == {"cafe", "coffee_shop"}


def test_two_distinct_places_both_stored():
    conn = _conn()
    db_mod.upsert_place(conn, _make_place("p1"), "cafe")
    db_mod.upsert_place(conn, _make_place("p2"), "cafe")
    count = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    assert count == 2


def test_first_seen_query_not_overwritten():
    conn = _conn()
    p = _make_place("abc123")
    db_mod.upsert_place(conn, p, "cafe")
    db_mod.upsert_place(conn, p, "restaurant")
    row = conn.execute("SELECT first_seen_query FROM places WHERE place_id='abc123'").fetchone()
    assert row[0] == "cafe"
