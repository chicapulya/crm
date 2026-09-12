"""
PlacesClient wraps google_call for Nearby Search.
All network I/O goes through budget.google_call — never direct httpx.
"""
from __future__ import annotations
import logging
import sqlite3
from typing import Any

import httpx

from budget import BudgetExhausted, google_call
from config import (
    FIELD_MASK,
    LANGUAGE_CODE,
    NEARBY_SEARCH_URL,
    REGION_CODE,
)

logger = logging.getLogger(__name__)

# Re-export so census.py imports don't break.
FatalAPIError = RuntimeError


class PlacesClient:
    def __init__(self, dry_run: bool = False, conn: sqlite3.Connection | None = None) -> None:
        self.dry_run = dry_run
        self._conn = conn
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PlacesClient":
        self._http = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def nearby_search(
        self, lat: float, lng: float, radius: float, place_type: str
    ) -> tuple[list[dict], bool]:
        """Return (places, from_cache). Dry-run returns empty without network."""
        body = {
            "includedTypes": [place_type],
            "maxResultCount": 20,
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": radius,
                }
            },
            "languageCode": LANGUAGE_CODE,
            "regionCode": REGION_CODE,
        }

        if self.dry_run:
            logger.info("dry-run: would call lat=%.5f lng=%.5f r=%.0f type=%s",
                        lat, lng, radius, place_type)
            return [], False

        assert self._http is not None
        assert self._conn is not None, "PlacesClient requires a db conn for budget tracking"

        from cache import get as cache_get
        cached_raw = cache_get(url=NEARBY_SEARCH_URL, body=body, field_mask=FIELD_MASK)
        was_cached = cached_raw is not None

        data = await google_call(
            url=NEARBY_SEARCH_URL,
            body=body,
            field_mask=FIELD_MASK,
            conn=self._conn,
            http_client=self._http,
        )
        return _parse_places(data), was_cached


def _parse_places(data: dict) -> list[dict[str, Any]]:
    out = []
    for p in data.get("places", []):
        out.append({
            "place_id": p.get("id", ""),
            "display_name": p.get("displayName", {}).get("text"),
            "formatted_address": p.get("formattedAddress"),
            "location": p.get("location", {}),
            "types": p.get("types", []),
            "primary_type": p.get("primaryType"),
            "business_status": p.get("businessStatus"),
            "google_maps_uri": p.get("googleMapsUri"),
        })
    return out
