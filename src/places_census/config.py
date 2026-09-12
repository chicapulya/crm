from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
VAR_DIR = PROJECT_ROOT / "var"

# ── API ──────────────────────────────────────────────────────────────────────
API_KEY: str = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"

# Only Pro-tier fields — no Enterprise fields (rating, hours, phone, website).
FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.location,"
    "places.types,"
    "places.primaryType,"
    "places.businessStatus,"
    "places.googleMapsUri"
)

LANGUAGE_CODE = "ru"
REGION_CODE = "MD"

# ── Geography ─────────────────────────────────────────────────────────────────
BBOX = dict(lat_min=46.92, lat_max=47.09, lng_min=28.74, lng_max=28.95)
BBOX_SUBURBS = dict(lat_min=46.88, lat_max=47.13, lng_min=28.68, lng_max=29.02)

# ── Grid algorithm ────────────────────────────────────────────────────────────
START_RADIUS = 1_800
MIN_RADIUS = 150
GRID_OVERLAP = 1.4

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_CALLS = 4_500
CONCURRENCY = 3
PAUSE_BETWEEN_TYPES = 1.0
RETRY_ATTEMPTS = 5
RETRY_MIN_WAIT = 1.0
RETRY_MAX_WAIT = 60.0

# ── Paths ─────────────────────────────────────────────────────────────────────
CACHE_DIR = VAR_DIR / "cache"
DB_PATH = VAR_DIR / "places.db"
LOG_PATH = VAR_DIR / "api_calls.jsonl"
REPORT_PATH = VAR_DIR / "report.md"
CSV_PATH = VAR_DIR / "places.csv"
CATEGORIES_FILE = DATA_DIR / "categories.yaml"
TAXONOMY_FILE = DATA_DIR / "taxonomy.yaml"
OSM_SECTORS_FILE = DATA_DIR / "osm_sectors.json"
OSM_CACHE_FILE = VAR_DIR / "osm_cache.json"
OSM_CACHE_PARTIAL = VAR_DIR / "osm_cache_partial.json"
LEADS_MAIN_CSV = VAR_DIR / "leads_main.csv"
LEADS_INTERVIEWS_CSV = VAR_DIR / "leads_interviews.csv"
EXPORT_SIMPLE_XLSX = VAR_DIR / "leads.xlsx"
EXPORT_XLSX = VAR_DIR / "chisinau_leads_v2.xlsx"

VAR_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

# ── Sector centroids (rough approximation, clearly labelled as such) ──────────
SECTOR_CENTROIDS = {
    "Centru": (47.0228, 28.8350),
    "Botanica": (46.9872, 28.8637),
    "Riscani": (47.0432, 28.8268),
    "Buiucani": (47.0304, 28.7994),
    "Ciocana": (47.0176, 28.8890),
}
