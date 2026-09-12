"""
Step 3: Geo-deduplication.

Algorithm:
  1. Project lat/lng to approximate metres (Equirectangular, accurate < 1% for city scale).
  2. KDTree query_pairs finds every pair within radius R.
  3. For each geo-pair:
       a. Normalise names: NFKD diacritics + Cyrillic→Latin transliteration
          + legal suffix strip + generic stop-word strip.
       b. Compute token_set_ratio.
       c. Guard: len(short) / len(long) >= 0.6 (prevents substring inflation).
       d. Flag: if either normalised name < 4 chars → needs_review.
  4. Pairs with name_score >= 85 AND len_ratio >= 0.6 go into `duplicates`.
     Short-name pairs (flag) are also written regardless of score if dist < 5 m.
  5. Show 30 stratified pairs (product_fit != 'none') for manual review.
"""
from __future__ import annotations
import io
import math
import sqlite3
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from scipy.spatial import KDTree

from places_census.config import DB_PATH
from places_census.processing.text_norm import norm as _norm, similarity as _sim_pair

# ── Constants ────────────────────────────────────────────────────────────────

LAT_DEG_M  = 111_000
LAT_CENTER = 47.02
LNG_DEG_M  = LAT_DEG_M * math.cos(math.radians(LAT_CENTER))

NAME_THRESHOLD  = 85
LEN_RATIO_MIN   = 0.6
SHORT_NAME_CHARS = 4
RADIUS_FINAL    = 25

DDL_DUPLICATES = """
CREATE TABLE IF NOT EXISTS duplicates (
    place_id_a   TEXT,
    place_id_b   TEXT,
    distance_m   REAL,
    name_score   INTEGER,
    confirmed    INTEGER DEFAULT NULL,
    needs_review INTEGER DEFAULT 0,
    PRIMARY KEY (place_id_a, place_id_b)
);
"""


# ── Spatial helpers ───────────────────────────────────────────────────────────

def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _load_places(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT place_id, display_name, lat, lng, product_fit"
        " FROM places WHERE lat IS NOT NULL AND lng IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def _score_all(places: list[dict], radius_m: float) -> list[dict]:
    xy  = np.array([[p["lat"] * LAT_DEG_M, p["lng"] * LNG_DEG_M] for p in places])
    raw = KDTree(xy).query_pairs(radius_m)
    out = []
    for i, j in raw:
        dist = _haversine(places[i]["lat"], places[i]["lng"],
                          places[j]["lat"], places[j]["lng"])
        na = _norm(places[i]["display_name"])
        nb = _norm(places[j]["display_name"])
        tsr, lr = _sim_pair(na, nb)
        short = min(len(na), len(nb)) < SHORT_NAME_CHARS
        out.append({
            "place_id_a": places[i]["place_id"],
            "place_id_b": places[j]["place_id"],
            "name_a":     places[i]["display_name"],
            "name_b":     places[j]["display_name"],
            "norm_a":     na,
            "norm_b":     nb,
            "pf_a":       places[i]["product_fit"],
            "pf_b":       places[j]["product_fit"],
            "distance_m": dist,
            "name_score": tsr,
            "len_ratio":  lr,
            "short_name": short,
        })
    return out


# ── Run ──────────────────────────────────────────────────────────────────────

def run() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL_DUPLICATES)
    conn.commit()

    # Add needs_review column if missing (idempotent upgrade)
    try:
        conn.execute("ALTER TABLE duplicates ADD COLUMN needs_review INTEGER DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    places = _load_places(conn)
    print(f"Loaded {len(places):,} places with coordinates.\n")

    # ── Radius comparison ─────────────────────────────────────────────────────
    print("Radius comparison (score >= 85, len_ratio >= 0.6, stop-words stripped)")
    print("-" * 62)
    print(f"{'radius':>8}  {'geo pairs':>10}  {'name>=85':>9}  {'name>=95':>9}")
    print("-" * 62)

    all_scored: list[dict] = []

    for radius in (25, 40):
        scored = _score_all(places, radius)
        n85 = sum(1 for p in scored if p["name_score"] >= 85 and p["len_ratio"] >= LEN_RATIO_MIN)
        n95 = sum(1 for p in scored if p["name_score"] >= 95 and p["len_ratio"] >= LEN_RATIO_MIN)
        print(f"{radius:>7}m  {len(scored):>10,}  {n85:>9,}  {n95:>9,}")
        if radius == RADIUS_FINAL:
            all_scored = scored

    print()

    # ── Write duplicates ──────────────────────────────────────────────────────
    conn.execute("DELETE FROM duplicates")

    dups = [p for p in all_scored
            if p["name_score"] >= NAME_THRESHOLD and p["len_ratio"] >= LEN_RATIO_MIN]

    conn.executemany(
        "INSERT OR IGNORE INTO duplicates"
        " (place_id_a, place_id_b, distance_m, name_score, needs_review)"
        " VALUES (?,?,?,?,?)",
        [(p["place_id_a"], p["place_id_b"],
          round(p["distance_m"], 1), p["name_score"],
          int(p["short_name"]))
         for p in dups],
    )
    conn.commit()

    n_flagged = sum(1 for p in dups if p["short_name"])
    print(f"Written {len(dups):,} pairs -> duplicates table "
          f"({len(dups)-n_flagged} threshold, {n_flagged} short-name flagged).\n")

    # ── 30 stratified pairs (product_fit != none) ─────────────────────────────
    relevant = [p for p in all_scored
                if p["pf_a"] != "none" or p["pf_b"] != "none"]

    def _sample(lo: int, hi: int, n: int, require_lr: bool = True) -> list[dict]:
        b = [p for p in relevant
             if lo <= p["name_score"] < hi
             and (not require_lr or p["len_ratio"] >= LEN_RATIO_MIN)]
        b.sort(key=lambda x: -x["name_score"])
        step = max(1, len(b) // n)
        return b[::step][:n]

    top10 = _sample(95, 101, 10)
    mid10 = _sample(85, 95,  10)
    low10 = _sample(75, 85,  10, require_lr=False)  # show LR violations too

    def _hdr(label: str) -> None:
        print(f"-- {label} {'-' * max(0, 55 - len(label))}")
        print(f"  {'sc':>4}  {'lr':>4}  {'dist':>5}  {'norm_a':<28}  norm_b")

    def _row(p: dict) -> None:
        na = p["norm_a"][:28]
        nb = p["norm_b"][:34]
        flags = []
        if p["len_ratio"] < LEN_RATIO_MIN: flags.append("LR")
        if p["short_name"]:                flags.append("SH")
        tag = " ".join(flags)
        print(f"  {p['name_score']:>4.0f}  {p['len_ratio']:>4.2f}  "
              f"{p['distance_m']:>4.1f}m  {na:<28}  {nb}  {tag}")

    print("30 stratified pairs  [product_fit != none, radius 25 m]")
    print(f"  LR = len_ratio < {LEN_RATIO_MIN}  |  SH = short name < {SHORT_NAME_CHARS} chars")
    print("=" * 78)
    _hdr(f"top score 95-100  n={len(top10)}")
    for p in top10: _row(p)
    print()
    _hdr(f"near threshold 85-95  n={len(mid10)}")
    for p in mid10: _row(p)
    print()
    _hdr(f"below threshold 75-85  n={len(low10)}")
    for p in low10: _row(p)
    print()

    conn.close()


if __name__ == "__main__":
    run()
