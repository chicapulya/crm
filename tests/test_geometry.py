import math

from places_census.census.geometry import (
    Cell,
    generate_grid,
    metres_to_lat,
    metres_to_lng,
    nearest_sector,
)
from places_census.config import BBOX, GRID_OVERLAP, START_RADIUS


def test_cell_split_produces_four_children():
    c = Cell(46.92, 47.09, 28.74, 28.95)
    children = c.split_into_4()
    assert len(children) == 4


def test_cell_split_covers_parent():
    parent = Cell(46.92, 47.09, 28.74, 28.95)
    children = parent.split_into_4()
    lat_min = min(c.lat_min for c in children)
    lat_max = max(c.lat_max for c in children)
    lng_min = min(c.lng_min for c in children)
    lng_max = max(c.lng_max for c in children)
    assert abs(lat_min - parent.lat_min) < 1e-9
    assert abs(lat_max - parent.lat_max) < 1e-9
    assert abs(lng_min - parent.lng_min) < 1e-9
    assert abs(lng_max - parent.lng_max) < 1e-9


def test_cell_split_no_overlap():
    parent = Cell(46.92, 47.09, 28.74, 28.95)
    children = parent.split_into_4()
    lat_mid = (parent.lat_min + parent.lat_max) / 2
    lng_mid = (parent.lng_min + parent.lng_max) / 2
    bl = children[0]
    assert bl.lat_max == lat_mid
    assert bl.lng_max == lng_mid


def test_cell_center():
    c = Cell(46.0, 47.0, 28.0, 29.0)
    lat, lng = c.center
    assert abs(lat - 46.5) < 1e-9
    assert abs(lng - 28.5) < 1e-9


def test_grid_no_gaps():
    radius = START_RADIUS
    centres = list(generate_grid(BBOX, radius))

    sample_lats = [BBOX["lat_min"] + i * 0.01 for i in range(int((BBOX["lat_max"] - BBOX["lat_min"]) / 0.01) + 1)]
    sample_lngs = [BBOX["lng_min"] + i * 0.01 for i in range(int((BBOX["lng_max"] - BBOX["lng_min"]) / 0.01) + 1)]

    EARTH_R = 6_371_000.0
    failures = []
    for slat in sample_lats:
        for slng in sample_lngs:
            if not (BBOX["lat_min"] <= slat <= BBOX["lat_max"] and BBOX["lng_min"] <= slng <= BBOX["lng_max"]):
                continue
            closest = min(
                math.sqrt(
                    ((slat - clat) * (math.pi / 180) * EARTH_R) ** 2
                    + ((slng - clng) * (math.pi / 180) * EARTH_R * math.cos(math.radians(slat))) ** 2
                )
                for clat, clng in centres
            )
            if closest > radius:
                failures.append((slat, slng, closest))

    assert not failures, f"{len(failures)} sample points not covered: first={failures[0]}"


def test_grid_generates_centres():
    centres = list(generate_grid(BBOX, START_RADIUS))
    assert len(centres) > 0


def test_nearest_sector_centre():
    centroids = {
        "Centru": (47.0228, 28.8350),
        "Botanica": (46.9872, 28.8637),
    }
    assert nearest_sector(47.0228, 28.8350, centroids) == "Centru"


def test_nearest_sector_botanica():
    centroids = {
        "Centru": (47.0228, 28.8350),
        "Botanica": (46.9872, 28.8637),
    }
    assert nearest_sector(46.990, 28.860, centroids) == "Botanica"
