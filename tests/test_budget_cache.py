"""
Tests for budget ledger and cache.
Key invariant: two identical google_call invocations produce exactly ONE
budget increment and ONE HTTP request (second hit comes from cache).
"""
from __future__ import annotations

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from places_census.api import budget as budget_mod
from places_census.api.budget import (
    FREE_LIMITS,
    BudgetExhausted,
    _check_and_increment,
    detect_sku,
    get_usage,
    google_call,
)
from places_census.storage import cache as cache_mod

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


def test_detect_sku_pro_fields_only():
    mask = "places.id,places.displayName,places.location,places.primaryType,places.businessStatus"
    assert detect_sku(mask) == "pro"


def test_detect_sku_enterprise_rating():
    assert detect_sku("places.id,places.rating") == "enterprise"


def test_detect_sku_enterprise_phone():
    assert detect_sku("places.nationalPhoneNumber,places.id") == "enterprise"


def test_detect_sku_enterprise_website():
    assert detect_sku("places.id,places.websiteUri,places.displayName") == "enterprise"


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
    assert get_usage(conn, "enterprise") == limit


def test_pro_and_enterprise_counters_independent():
    conn = _conn()
    _check_and_increment(conn, "pro")
    _check_and_increment(conn, "enterprise")
    assert get_usage(conn, "pro") == 1
    assert get_usage(conn, "enterprise") == 1


def test_double_call_single_budget_increment(tmp_path, monkeypatch):
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
    assert mock_http.post.call_count == 1
    assert get_usage(conn, "pro") == 1


def test_different_field_masks_use_separate_cache_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path)

    conn = _conn()
    url = "https://places.googleapis.com/v1/places:searchNearby"
    body = {"includedTypes": ["cafe"], "maxResultCount": 20,
            "locationRestriction": {"circle": {"center": {"latitude": 47.0, "longitude": 28.8},
                                                "radius": 500.0}}}

    resp_pro = {"places": [{"id": "pro_result"}]}
    resp_ent = {"places": [{"id": "ent_result"}]}

    async def fake_post(url, **kwargs):
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
    assert mock_http.post.call_count == 2
