"""
Single entry point for all Google Places API calls.
Enforces budget limits before every network request.
Cache hits never touch the budget counter.
"""
from __future__ import annotations
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from places_census.storage import cache as cache_mod
from places_census.config import API_KEY, RETRY_ATTEMPTS, RETRY_MAX_WAIT, RETRY_MIN_WAIT

logger = logging.getLogger(__name__)

# Fields that trigger Enterprise SKU billing.
ENTERPRISE_FIELDS = frozenset({
    "rating",
    "userRatingCount",
    "websiteUri",
    "nationalPhoneNumber",
    "regularOpeningHours",
    "priceLevel",
    "currentOpeningHours",
})

# Stay below real free limits (5 000 Pro, 1 000 Enterprise) by 100 calls each.
FREE_LIMITS: dict[str, int] = {"pro": 4_900, "enterprise": 990}

BUDGET_DDL = """
CREATE TABLE IF NOT EXISTS budget_ledger (
    month TEXT,
    sku   TEXT,
    used  INTEGER DEFAULT 0,
    PRIMARY KEY (month, sku)
);
"""

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BudgetExhausted(Exception):
    pass


def detect_sku(field_mask: str) -> str:
    """Return 'enterprise' if any Enterprise field is in the mask, else 'pro'."""
    bare = {f.split(".")[-1] for f in field_mask.split(",")}
    return "enterprise" if bare & ENTERPRISE_FIELDS else "pro"


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _next_reset() -> datetime:
    now = datetime.now(timezone.utc)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


def get_usage(conn: sqlite3.Connection, sku: str) -> int:
    row = conn.execute(
        "SELECT used FROM budget_ledger WHERE month=? AND sku=?",
        (_current_month(), sku),
    ).fetchone()
    return row["used"] if row else 0


def print_budget_status(conn: sqlite3.Connection) -> None:
    reset = _next_reset()
    days = (reset - datetime.now(timezone.utc)).days + 1
    print("Budget status:")
    for sku, limit in FREE_LIMITS.items():
        used = get_usage(conn, sku)
        print(f"  {sku.upper():12} {used:>5}/{limit}  ({limit - used} remaining)"
              f"  resets {reset.strftime('%Y-%m-%d')} UTC ({days}d)")


def _check_and_increment(conn: sqlite3.Connection, sku: str) -> None:
    """
    Atomically read, check, and increment the budget counter.
    Raises BudgetExhausted before any network call is made.
    """
    month = _current_month()
    limit = FREE_LIMITS[sku]
    with conn:
        row = conn.execute(
            "SELECT used FROM budget_ledger WHERE month=? AND sku=?", (month, sku)
        ).fetchone()
        used = row["used"] if row else 0
        if used >= limit:
            reset = _next_reset()
            days = (reset - datetime.now(timezone.utc)).days + 1
            raise BudgetExhausted(
                f"{sku.upper()} budget exhausted: {used}/{limit} used in {month}. "
                f"Resets in {days} day(s) on {reset.strftime('%Y-%m-%d')} UTC."
            )
        conn.execute(
            "INSERT INTO budget_ledger(month, sku, used) VALUES(?,?,1) "
            "ON CONFLICT(month,sku) DO UPDATE SET used=used+1",
            (month, sku),
        )


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    if isinstance(exc, httpx.RequestError):
        return True
    return False


async def google_call(
    *,
    url: str,
    body: dict[str, Any],
    field_mask: str,
    conn: sqlite3.Connection,
    http_client: httpx.AsyncClient,
    method: str = "POST",
) -> dict[str, Any]:
    """
    THE single entry point for all Google Places API calls.
    Direct httpx calls to places.googleapis.com anywhere else are forbidden.

    Flow:
      1. Check cache — no budget touch on hit.
      2. Atomic check + increment budget counter BEFORE sending request.
      3. Send request with retry/backoff.
      4. Cache response (including empty).
      5. Return data.
    """
    sku = detect_sku(field_mask)

    cached = cache_mod.get(url=url, body=body, field_mask=field_mask)
    if cached is not None:
        logger.debug("cache hit sku=%s url=%s", sku, url)
        return cached

    _check_and_increment(conn, sku)

    data = await _fetch_with_retry(
        url=url, body=body, field_mask=field_mask, http_client=http_client, method=method
    )

    cache_mod.put(url=url, body=body, field_mask=field_mask, response=data)
    return data


@retry(
    retry=retry_if_exception(_should_retry),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_exponential_jitter(initial=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def _fetch_with_retry(
    *,
    url: str,
    body: dict[str, Any],
    field_mask: str,
    http_client: httpx.AsyncClient,
    method: str = "POST",
) -> dict[str, Any]:
    headers = {
        "X-Goog-Api-Key": API_KEY,
        "X-Goog-FieldMask": field_mask,
    }
    if method == "GET":
        resp = await http_client.get(url, headers=headers)
    else:
        headers["Content-Type"] = "application/json"
        resp = await http_client.post(url, content=json.dumps(body), headers=headers)
    if resp.status_code == 400:
        msg = resp.json().get("error", {}).get("message", "")
        if "Unsupported type" in msg:
            logger.warning("Unsupported place type — skipping: %s", msg)
            return {}
        raise RuntimeError(f"HTTP 400 — check API key / field mask: {resp.text[:400]}")
    if resp.status_code == 403:
        raise RuntimeError(f"HTTP 403 — check API key: {resp.text[:400]}")
    resp.raise_for_status()
    return resp.json()
