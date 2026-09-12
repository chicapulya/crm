"""
Disk cache for Google API responses.
Key = sha256(url + body + field_mask) so different field masks never collide.
"""
from __future__ import annotations
import hashlib
import json
from typing import Any
from places_census.config import CACHE_DIR


def _key(url: str, body: dict, field_mask: str) -> str:
    raw = json.dumps(
        {"url": url, "body": body, "field_mask": field_mask},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def get(*, url: str, body: dict, field_mask: str) -> dict | None:
    path = CACHE_DIR / f"{_key(url, body, field_mask)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def put(*, url: str, body: dict, field_mask: str, response: dict) -> None:
    path = CACHE_DIR / f"{_key(url, body, field_mask)}.json"
    path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
