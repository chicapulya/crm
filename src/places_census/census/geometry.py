from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Iterator
from places_census.config import BBOX, BBOX_SUBURBS, GRID_OVERLAP, START_RADIUS, MIN_RADIUS

EARTH_RADIUS_M = 6_371_000.0


def metres_to_lat(m: float) -> float:
    return m / EARTH_RADIUS_M * (180 / math.pi)


def metres_to_lng(m: float, lat: float) -> float:
    return m / (EARTH_RADIUS_M * math.cos(math.radians(lat))) * (180 / math.pi)


@dataclass
class Cell:
    lat_min: float
    lat_max: float
    lng_min: float
    lng_max: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.lat_min + self.lat_max) / 2, (self.lng_min + self.lng_max) / 2

    def split_into_4(self) -> list["Cell"]:
        lat_mid = (self.lat_min + self.lat_max) / 2
        lng_mid = (self.lng_min + self.lng_max) / 2
        return [
            Cell(self.lat_min, lat_mid, self.lng_min, lng_mid),
            Cell(self.lat_min, lat_mid, lng_mid,      self.lng_max),
            Cell(lat_mid,      self.lat_max, self.lng_min, lng_mid),
            Cell(lat_mid,      self.lat_max, lng_mid,      self.lng_max),
        ]


def root_cell(suburbs: bool = False) -> Cell:
    b = BBOX_SUBURBS if suburbs else BBOX
    return Cell(b["lat_min"], b["lat_max"], b["lng_min"], b["lng_max"])


def generate_grid(bbox: dict, radius: float) -> Iterator[tuple[float, float]]:
    """
    Yield (lat, lng) circle centres covering bbox with given radius.
    Step = radius * GRID_OVERLAP so adjacent circles overlap ~30 %.
    Rows offset by half-step to reduce gaps in corner areas.
    """
    step_lat = metres_to_lat(radius * GRID_OVERLAP)
    lat = bbox["lat_min"]
    row = 0
    while lat <= bbox["lat_max"] + step_lat:
        mid_lat = lat
        step_lng = metres_to_lng(radius * GRID_OVERLAP, mid_lat)
        lng_offset = (step_lng / 2) if (row % 2 == 1) else 0.0
        lng = bbox["lng_min"] + lng_offset
        while lng <= bbox["lng_max"] + step_lng:
            yield lat, lng
            lng += step_lng
        lat += step_lat
        row += 1


def grid_covers_bbox(bbox: dict, radius: float) -> bool:
    """
    Verify no point inside bbox is more than `radius` metres from the nearest
    circle centre. Uses the half-diagonal of the step cell as worst case.
    """
    step_lat_m = radius * GRID_OVERLAP
    step_lng_m = radius * GRID_OVERLAP
    diag = math.sqrt((step_lat_m / 2) ** 2 + (step_lng_m / 2) ** 2)
    return diag <= radius


def nearest_sector(lat: float, lng: float, centroids: dict[str, tuple[float, float]]) -> str:
    best, best_d = "Unknown", float("inf")
    for name, (clat, clng) in centroids.items():
        d = math.sqrt((lat - clat) ** 2 + (lng - clng) ** 2)
        if d < best_d:
            best, best_d = name, d
    return best


# ── Adaptive quadtree helpers ─────────────────────────────────────────────────

def cell_radius(cell: Cell) -> float:
    """Inscribed circle radius of a cell (min of half-width, half-height)."""
    lat, lng = cell.center
    half_h = metres_to_lat(1) and ((cell.lat_max - cell.lat_min) / 2)
    half_w_deg = (cell.lng_max - cell.lng_min) / 2
    # convert degrees back to metres for comparison
    half_h_m = half_h * (math.pi / 180) * EARTH_RADIUS_M
    half_w_m = half_w_deg * (math.pi / 180) * EARTH_RADIUS_M * math.cos(math.radians(lat))
    return min(half_h_m, half_w_m)
