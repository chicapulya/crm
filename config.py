from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

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
# START_RADIUS=1800m → ~60 grid cells; 36 types * 60 = ~2200 base calls.
# Leaves ~2300 calls for subdivision — dense centre can subdivide 3-4 levels
# without exceeding the 4500 hard stop or 5000 free Pro quota.
# Accuracy tradeoff: step=2520m, max gap ~1780m < radius — no holes in coverage.
START_RADIUS = 1_800      # metres
MIN_RADIUS   = 150        # metres
GRID_OVERLAP = 1.4        # centre spacing = radius * GRID_OVERLAP (~30% overlap)

# ── Limits ────────────────────────────────────────────────────────────────────
# Hard stop at 4500 — leaves 500-call buffer before the 5000 free Pro quota.
MAX_CALLS        = 4_500
CONCURRENCY      = 3       # reduced from 5 to avoid triggering Google rate limits
PAUSE_BETWEEN_TYPES = 1.0  # seconds pause after each type completes
RETRY_ATTEMPTS   = 5
RETRY_MIN_WAIT   = 1.0    # seconds
RETRY_MAX_WAIT   = 60.0   # seconds

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
CACHE_DIR  = BASE_DIR / "cache"
DB_PATH    = BASE_DIR / "places.db"
LOG_PATH   = BASE_DIR / "api_calls.jsonl"
REPORT_PATH = BASE_DIR / "report.md"
CSV_PATH    = BASE_DIR / "places.csv"
CATEGORIES_FILE = BASE_DIR / "categories.yaml"

CACHE_DIR.mkdir(exist_ok=True)

# ── Sector centroids (rough approximation, clearly labelled as such) ──────────
SECTOR_CENTROIDS = {
    "Centru":      (47.0228, 28.8350),
    "Botanica":    (46.9872, 28.8637),
    "Riscani":     (47.0432, 28.8268),
    "Buiucani":    (47.0304, 28.7994),
    "Ciocana":     (47.0176, 28.8890),
}
