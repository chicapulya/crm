"""
Step 4: OSM enrichment + sector polygon assignment.

Phase A — business enrichment:
  Fetch named amenity/shop/leisure/tourism nodes and ways from Overpass in 6
  sub-bboxes. Match to places.db by (50 m geo + token_set_ratio >= 80 +
  len_ratio >= 0.6). Write website, phone, instagram, facebook, opening_hours,
  cuisine to places.

Phase B — sector polygon assignment:
  Fetch admin_level 8/9/10 boundaries for Chisinau sectors from Overpass.
  Reconstruct shapely Polygons from member way geometries.
  Replace centroid-distance sector assignment with proper point-in-polygon.
"""
from __future__ import annotations
import io
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from scipy.spatial import KDTree
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, polygonize, unary_union

from places_census.config import (
    BBOX,
    DB_PATH,
    OSM_CACHE_FILE,
    OSM_CACHE_PARTIAL,
    OSM_SECTORS_FILE,
)
from places_census.processing.text_norm import norm, similarity

# ── Constants ─────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_ALT = "https://overpass.kumi.systems/api/interpreter"
USER_AGENT = "CRM-Chisinau/1.0 (contact: denis3.ciorba@gmail.com)"
CACHE_FILE = OSM_CACHE_FILE
SECTOR_CACHE = OSM_SECTORS_FILE
_PARTIAL = OSM_CACHE_PARTIAL

MATCH_RADIUS  = 50    # metres
NAME_THRESH   = 80    # token_set_ratio minimum
LEN_RATIO_MIN = 0.6   # substring guard (same as dedup)

LAT_DEG_M = 111_000
LNG_DEG_M = LAT_DEG_M * math.cos(math.radians(47.02))

OSM_ENRICHMENT_COLS = {
    "osm_id":            "TEXT",
    "osm_website":       "TEXT",
    "osm_phone":         "TEXT",
    "osm_instagram":     "TEXT",
    "osm_facebook":      "TEXT",
    "osm_opening_hours": "TEXT",
    "osm_cuisine":       "TEXT",
    "osm_matched":       "INTEGER DEFAULT 0",
}

# ── Overpass helpers ──────────────────────────────────────────────────────────

def _bbox_cells() -> list[tuple[float, float, float, float]]:
    """Split BBOX into a 2×3 grid (6 cells) to avoid Overpass timeouts."""
    lat_min, lat_max = BBOX["lat_min"], BBOX["lat_max"]
    lng_min, lng_max = BBOX["lng_min"], BBOX["lng_max"]
    rows, cols = 2, 3
    dlat = (lat_max - lat_min) / rows
    dlng = (lng_max - lng_min) / cols
    cells = []
    for r in range(rows):
        for c in range(cols):
            cells.append((
                lat_min + r * dlat,
                lng_min + c * dlng,
                lat_min + (r + 1) * dlat,
                lng_min + (c + 1) * dlng,
            ))
    return cells


def _business_query(cell: tuple[float, float, float, float]) -> str:
    s, w, n, e = cell
    bbox = f"{s},{w},{n},{e}"
    tags = ["amenity", "shop", "leisure", "tourism", "craft"]
    lines = []
    for t in tags:
        for kind in ("node", "way", "relation"):
            lines.append(f'  {kind}["name"]["{t}"]({bbox});')
    body = "\n".join(lines)
    return f'[out:json][timeout:55];\n(\n{body}\n);\nout center tags;'


def _sector_query() -> str:
    s, w = BBOX["lat_min"], BBOX["lng_min"]
    n, e = BBOX["lat_max"], BBOX["lng_max"]
    # Chisinau city sectors (Centru, Botanica, Riscani, Buiucani, Ciocana)
    # are mapped at admin_level=7 in OSM Moldova scheme.
    return (
        f'[out:json][timeout:55];\n'
        f'(\n'
        f'  rel["boundary"="administrative"]["admin_level"="7"]({s},{w},{n},{e});\n'
        f');\n'
        f'out geom;'
    )


def _http_post(query: str, retries: int = 5) -> dict:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
    }
    body = urllib.parse.urlencode({"data": query}).encode()
    urls = [OVERPASS_URL, OVERPASS_ALT]
    for attempt in range(retries):
        url = urls[attempt % len(urls)]
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            wait = 30 if e.code == 429 else 15
            wait *= (attempt + 1)
            print(f"  HTTP {e.code} from {url} — waiting {wait}s …")
            time.sleep(wait)
        except Exception as exc:
            wait = 10 * (attempt + 1)
            print(f"  Error ({exc}) — waiting {wait}s …")
            time.sleep(wait)
    raise RuntimeError("Overpass unreachable after retries")


# ── Parse OSM elements ────────────────────────────────────────────────────────

def _element_coords(el: dict) -> tuple[float, float] | None:
    if el["type"] == "node":
        return el.get("lat"), el.get("lon")
    center = el.get("center", {})
    if center:
        return center.get("lat"), center.get("lon")
    return None


def _pick(*keys: str, tags: dict) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            return v
    return None


def _parse_element(el: dict) -> dict | None:
    coords = _element_coords(el)
    if not coords or coords[0] is None:
        return None
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("name:ru") or tags.get("name:ro")
    if not name:
        return None
    website = _pick("website", "contact:website", tags=tags)
    phone   = _pick("phone", "contact:phone", tags=tags)
    return {
        "osm_id":            f"{el['type']}/{el['id']}",
        "name":              name,
        "lat":               coords[0],
        "lng":               coords[1],
        "osm_website":       website,
        "osm_phone":         phone,
        "osm_instagram":     _pick("contact:instagram", "instagram", tags=tags),
        "osm_facebook":      _pick("contact:facebook", "facebook", tags=tags),
        "osm_opening_hours": tags.get("opening_hours"),
        "osm_cuisine":       tags.get("cuisine"),
    }


# ── Fetch businesses ──────────────────────────────────────────────────────────


def fetch_businesses(force: bool = False) -> list[dict]:
    if not force and CACHE_FILE.exists():
        print(f"Loading OSM businesses from cache ({CACHE_FILE.name}) …")
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))

    cells = _bbox_cells()

    # Resume from partial cache if available
    if not force and _PARTIAL.exists():
        partial = json.loads(_PARTIAL.read_text(encoding="utf-8"))
        seen_ids: set[str] = {el["osm_id"] for el in partial["elements"]}
        elements: list[dict] = partial["elements"]
        start_idx: int = partial["next_cell"]
        print(f"Resuming from cell {start_idx + 1}/{len(cells)} "
              f"({len(elements):,} elements already fetched) …")
    else:
        seen_ids = set()
        elements = []
        start_idx = 0

    for idx in range(start_idx, len(cells)):
        cell = cells[idx]
        print(f"  Fetching sub-bbox {idx+1}/{len(cells)} …", end=" ", flush=True)
        raw = _http_post(_business_query(cell))
        batch = 0
        for el in raw.get("elements", []):
            eid = f"{el['type']}/{el['id']}"
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            parsed = _parse_element(el)
            if parsed:
                elements.append(parsed)
                batch += 1
        print(f"{batch} new  (total {len(elements):,})")
        # Save partial progress after each cell
        _PARTIAL.write_text(
            json.dumps({"elements": elements, "next_cell": idx + 1},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        if idx < len(cells) - 1:
            time.sleep(5)   # be polite to public instance

    CACHE_FILE.write_text(json.dumps(elements, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    _PARTIAL.unlink(missing_ok=True)
    print(f"Cached {len(elements):,} OSM business elements → {CACHE_FILE.name}\n")
    return elements


# ── Matching ──────────────────────────────────────────────────────────────────

def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def match_osm(
    places: list[dict],
    osm: list[dict],
    radius_m: float = MATCH_RADIUS,
    threshold: int = NAME_THRESH,
) -> dict[str, dict]:
    """
    Returns {place_id: best_osm_element} for matched places.
    Uses same KDTree + norm() + similarity() as dedup.py.
    """
    # Build KDTree on places
    place_xy = np.array([[p["lat"] * LAT_DEG_M, p["lng"] * LNG_DEG_M]
                         for p in places])
    tree = KDTree(place_xy)

    matches: dict[str, tuple[dict, float]] = {}   # place_id → (osm_el, score)

    for osm_el in osm:
        ox = osm_el["lat"] * LAT_DEG_M
        oy = osm_el["lng"] * LNG_DEG_M
        idxs = tree.query_ball_point([ox, oy], radius_m)
        if not idxs:
            continue
        on = norm(osm_el["name"])
        for i in idxs:
            p = places[i]
            # Verify exact haversine (KDTree uses equirect approximation)
            d = _haversine(p["lat"], p["lng"], osm_el["lat"], osm_el["lng"])
            if d > radius_m:
                continue
            pn = norm(p["display_name"])
            tsr, lr = similarity(on, pn)
            if tsr >= threshold and lr >= LEN_RATIO_MIN:
                pid = p["place_id"]
                if pid not in matches or tsr > matches[pid][1]:
                    matches[pid] = (osm_el, tsr)

    return {pid: el for pid, (el, _) in matches.items()}


# ── DB enrichment ─────────────────────────────────────────────────────────────

def _ensure_osm_columns(conn: sqlite3.Connection) -> None:
    for col, typ in OSM_ENRICHMENT_COLS.items():
        try:
            conn.execute(f"ALTER TABLE places ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def write_matches(conn: sqlite3.Connection, matched: dict[str, dict]) -> None:
    rows = []
    for place_id, el in matched.items():
        rows.append((
            el["osm_id"],
            el["osm_website"],
            el["osm_phone"],
            el["osm_instagram"],
            el["osm_facebook"],
            el["osm_opening_hours"],
            el["osm_cuisine"],
            place_id,
        ))
    conn.executemany(
        """UPDATE places SET
             osm_id=?, osm_website=?, osm_phone=?, osm_instagram=?,
             osm_facebook=?, osm_opening_hours=?, osm_cuisine=?,
             osm_matched=1
           WHERE place_id=?""",
        rows,
    )
    conn.commit()


# ── Sector polygons ───────────────────────────────────────────────────────────

SECTOR_NAMES = {
    "centru", "botanica", "riscani", "riscani", "buiucani", "ciocana",
    "risc ani",
}

def fetch_sectors(force: bool = False) -> list[dict]:
    if not force and SECTOR_CACHE.exists():
        print(f"Loading sector polygons from cache ({SECTOR_CACHE.name}) …")
        return json.loads(SECTOR_CACHE.read_text(encoding="utf-8"))

    print("Fetching sector boundaries from Overpass …")
    raw = _http_post(_sector_query())
    sectors = []
    for el in raw.get("elements", []):
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        name = (tags.get("name:ro") or tags.get("name") or "").strip()
        level = tags.get("admin_level", "")
        sectors.append({
            "name":        name,
            "admin_level": level,
            "members":     el.get("members", []),
            "tags":        tags,
        })

    SECTOR_CACHE.write_text(json.dumps(sectors, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"Cached {len(sectors)} admin relations → {SECTOR_CACHE.name}\n")
    return sectors


def _build_polygon(members: list[dict]) -> Polygon | None:
    """
    Assemble OSM admin-boundary outer-ring member ways into a shapely Polygon.
    Uses linemerge + polygonize (standard for OSM boundary relations where a
    single ring is split across multiple way segments).
    """
    lines = []
    for m in members:
        role = m.get("role", "")
        if role not in ("outer", ""):
            continue
        geom = m.get("geometry", [])
        if len(geom) >= 2:
            lines.append(LineString([(pt["lon"], pt["lat"]) for pt in geom]))

    if not lines:
        return None

    merged = linemerge(lines)          # join segments that share endpoints
    polys  = list(polygonize(merged))  # close rings → Polygon objects

    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    # Multiple polygons (disjoint parts) → return largest or merge
    return max(polys, key=lambda p: p.area)


def assign_sectors_polygon(conn: sqlite3.Connection, sectors: list[dict]) -> int:
    """
    For each place, find which sector polygon contains it.
    Returns number of places updated.
    """
    # Build list of (sector_name, shapely_geometry)
    polys: list[tuple[str, Polygon | MultiPolygon]] = []
    for s in sectors:
        name = s["name"]
        poly = _build_polygon(s["members"])
        if poly and poly.is_valid:
            polys.append((name, poly))

    if not polys:
        print("  WARNING: no valid sector polygons built — skipping polygon assignment.")
        return 0

    print(f"  Built {len(polys)} sector polygons: {[p[0] for p in polys]}")

    rows = conn.execute("SELECT place_id, lat, lng FROM places WHERE lat IS NOT NULL").fetchall()
    updated = 0
    for r in rows:
        pt = Point(r["lng"], r["lat"])   # shapely: (x=lng, y=lat)
        sector = None
        for name, poly in polys:
            if poly.contains(pt):
                sector = name
                break
        if sector:
            # Normalise "sectorul Centru" → "Centru"
            clean = sector.replace("sectorul ", "").strip()
            conn.execute("UPDATE places SET sector=? WHERE place_id=?",
                         (clean, r["place_id"]))
            updated += 1

    conn.commit()
    return updated


# ── Sector DDL ────────────────────────────────────────────────────────────────

DDL_SECTORS = """
CREATE TABLE IF NOT EXISTS sectors (
    name        TEXT PRIMARY KEY,
    admin_level TEXT,
    polygon_wkt TEXT
);
"""

def save_sector_polygons(conn: sqlite3.Connection, sectors: list[dict]) -> None:
    conn.executescript(DDL_SECTORS)
    for s in sectors:
        poly = _build_polygon(s["members"])
        if poly:
            from shapely import wkt as swkt
            conn.execute(
                "INSERT OR REPLACE INTO sectors (name, admin_level, polygon_wkt) VALUES (?,?,?)",
                (s["name"], s["admin_level"], swkt.dumps(poly)),
            )
    conn.commit()


# ── Run ───────────────────────────────────────────────────────────────────────

def run(force_fetch: bool = False) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_osm_columns(conn)

    # ── Phase A: business enrichment ──────────────────────────────────────────
    print("=== Phase A: OSM business enrichment ===\n")
    osm_elements = fetch_businesses(force=force_fetch)
    print(f"OSM elements loaded: {len(osm_elements):,}\n")

    places = [dict(r) for r in conn.execute(
        "SELECT place_id, display_name, lat, lng FROM places WHERE lat IS NOT NULL"
    ).fetchall()]

    print(f"Matching {len(osm_elements):,} OSM elements → {len(places):,} places "
          f"(radius {MATCH_RADIUS}m, score>={NAME_THRESH}) …")
    matched = match_osm(places, osm_elements)
    print(f"Matched: {len(matched):,} / {len(places):,} "
          f"({len(matched)/len(places)*100:.1f}%)\n")

    write_matches(conn, matched)

    # Coverage by field
    for col in ("osm_website", "osm_phone", "osm_instagram", "osm_facebook",
                "osm_opening_hours", "osm_cuisine"):
        n = conn.execute(
            f"SELECT COUNT(*) FROM places WHERE osm_matched=1 AND {col} IS NOT NULL"
        ).fetchone()[0]
        print(f"  {col:<20} {n:>5} places have data")

    # ── Phase B: sector polygons ──────────────────────────────────────────────
    print("\n=== Phase B: sector polygon assignment ===\n")
    sectors = fetch_sectors(force=force_fetch)
    print(f"Admin relations fetched: {len(sectors)}")
    for s in sectors:
        print(f"  level={s['admin_level']:>2}  {s['name']}")

    save_sector_polygons(conn, sectors)

    n_updated = assign_sectors_polygon(conn, sectors)
    print(f"\nSector polygon assignment: {n_updated:,} places updated.")

    # Compare coverage before/after
    null_sector = conn.execute(
        "SELECT COUNT(*) FROM places WHERE sector IS NULL"
    ).fetchone()[0]
    by_sector = conn.execute(
        "SELECT sector, COUNT(*) AS n FROM places GROUP BY sector ORDER BY n DESC"
    ).fetchall()
    print(f"\nSector distribution (nulls: {null_sector}):")
    for r in by_sector:
        print(f"  {str(r['sector']):<15} {r['n']:>6}")

    conn.close()


if __name__ == "__main__":
    force        = "--force" in sys.argv
    sectors_only = "--sectors-only" in sys.argv
    if sectors_only:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        _ensure_osm_columns(conn)
        SECTOR_CACHE.unlink(missing_ok=True)
        secs = fetch_sectors(force=True)
        print(f"Fetched {len(secs)} relations:")
        for s in secs:
            print(f"  level={s['admin_level']:>3}  {s['name']}")
        save_sector_polygons(conn, secs)
        n = assign_sectors_polygon(conn, secs)
        print(f"\nUpdated {n:,} places.")
        for r in conn.execute("SELECT sector, COUNT(*) n FROM places GROUP BY sector ORDER BY n DESC").fetchall():
            print(f"  {str(r['sector']):<20} {r['n']}")
        conn.close()
    else:
        run(force_fetch=force)
