"""
Tests for budget ledger and cache.
Key invariant: two identical google_call invocations produce exactly ONE
budget increment and ONE HTTP request (second hit comes from cache).
"""
from __future__ import annotations
import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import cache as cache_mod
import budget as budget_mod
from budget import (
    FREE_LIMITS,
    BudgetExhausted,
    _check_and_increment,
    detect_sku,
    get_usage,
    google_call,
)

BUDGET_DDL = """
CREATE TABLE IF NOT EXISTS budget_ledger (
    month TEXT, sku TEXT, used INTEGER DEFAULT 0,
    PRIMARY KEY (month, sku)
)
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(BUDGET_DDL)
    return c


# ── SKU detection ────────────────────────────────────────────────────────────

def test_detect_sku_pro_fields_only():
    mask = "places.id,places.displayName,places.location,places.primaryType,places.businessStatus"
    assert detect_sku(mask) == "pro"


def test_detect_sku_enterprise_rating():
    assert detect_sku("places.id,places.rating") == "enterprise"


def test_detect_sku_enterprise_phone():
    assert detect_sku("places.nationalPhoneNumber,places.id") == "enterprise"


def test_detect_sku_enterprise_website():
    assert detect_sku("places.id,places.websiteUri,places.displayName") == "enterprise"


# ── Budget enforcement ───────────────────────────────────────────────────────

def test_budget_increments_on_first_call():
    conn = _conn()
    _check_and_increment(conn, "pro")
    assert get_usage(conn, "pro") == 1


def test_budget_increments_twice():
    conn = _conn()
    _check_and_increment(conn, "pro")
    _check_and_increment(conn, "pro")
    assert get_usage(conn, "pro") == 2


def test_budget_exhausted_raises():
    conn = _conn()
    month = budget_mod._current_month()
    conn.execute(
        "INSERT INTO budget_ledger(month, sku, used) VALUES(?,?,?)",
        (month, "pro", FREE_LIMITS["pro"]),
    )
    conn.commit()
    with pytest.raises(BudgetExhausted):
        _check_and_increment(conn, "pro")


def test_budget_counter_unchanged_on_exhausted():
    conn = _conn()
    month = budget_mod._current_month()
    limit = FREE_LIMITS["enterprise"]
    conn.execute(
        "INSERT INTO budget_ledger(month, sku, used) VALUES(?,?,?)",
        (month, "enterprise", limit),
    )
    conn.commit()
    try:
        _check_and_increment(conn, "enterprise")
    except BudgetExhausted:
        pass
    assert get_usage(conn, "enterprise") == limit  # must NOT have incremented


def test_pro_and_enterprise_counters_independent():
    conn = _conn()
    _check_and_increment(conn, "pro")
    _check_and_increment(conn, "enterprise")
    assert get_usage(conn, "pro") == 1
    assert get_usage(conn, "enterprise") == 1


# ── Double-call test: cache hit = 0 extra budget increment ───────────────────

def test_double_call_single_budget_increment(tmp_path, monkeypatch):
    """
    Two identical google_call() invocations must result in:
      - exactly 1 HTTP request (second comes from disk cache)
      - exactly 1 budget increment
    """
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)

    conn = _conn()
    url = "https://places.googleapis.com/v1/places:searchNearby"
    body = {
        "includedTypes": ["cafe"],
        "maxResultCount": 20,
        "locationRestriction": {"circle": {
            "center": {"latitude": 47.02, "longitude": 28.83},
            "radius": 500.0,
        }},
    }
    field_mask = "places.id,places.displayName,places.primaryType"
    fake_response = {"places": [{"id": "ChIJtest001"}]}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response

    mock_http = AsyncMock()
    mock_http.post.return_value = mock_resp

    async def run():
        r1 = await google_call(url=url, body=body, field_mask=field_mask,
                               conn=conn, http_client=mock_http)
        r2 = await google_call(url=url, body=body, field_mask=field_mask,
                               conn=conn, http_client=mock_http)
        return r1, r2

    r1, r2 = asyncio.run(run())

    assert r1 == fake_response
    assert r2 == fake_response
    assert mock_http.post.call_count == 1, "Expected exactly 1 HTTP call (2nd should be cache hit)"
    assert get_usage(conn, "pro") == 1, "Expected exactly 1 budget increment"


def test_different_field_masks_use_separate_cache_entries(tmp_path, monkeypatch):
    """Different field masks must NOT share a cache entry."""
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)

    conn = _conn()
    url = "https://places.googleapis.com/v1/places:searchNearby"
    body = {"includedTypes": ["cafe"], "maxResultCount": 20,
            "locationRestriction": {"circle": {"center": {"latitude": 47.0, "longitude": 28.8},
                                                "radius": 500.0}}}

    resp_pro = {"places": [{"id": "pro_result"}]}
    resp_ent = {"places": [{"id": "ent_result"}]}

    call_count = 0

    async def fake_post(url, **kwargs):
        nonlocal call_count
        call_count += 1
        mask = kwargs.get("headers", {}).get("X-Goog-FieldMask", "")
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = resp_ent if "rating" in mask else resp_pro
        return m

    mock_http = AsyncMock()
    mock_http.post.side_effect = fake_post

    async def run():
        r1 = await google_call(url=url, body=body, field_mask="places.id,places.displayName",
                               conn=conn, http_client=mock_http)
        r2 = await google_call(url=url, body=body, field_mask="places.id,places.rating",
                               conn=conn, http_client=mock_http)
        return r1, r2

    r1, r2 = asyncio.run(run())

    assert r1 == resp_pro
    assert r2 == resp_ent
    assert mock_http.post.call_count == 2  # separate cache keys → 2 HTTP calls
