from __future__ import annotations
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from rich.progress import Progress, SpinnerColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn, MofNCompleteColumn

from places_census.storage import db as db_mod
from places_census.api.budget import BudgetExhausted
from places_census.api.client import FatalAPIError, PlacesClient
from places_census.config import (
    BBOX,
    BBOX_SUBURBS,
    CATEGORIES_FILE,
    CONCURRENCY,
    LOG_PATH,
    MAX_CALLS,
    MIN_RADIUS,
    PAUSE_BETWEEN_TYPES,
    START_RADIUS,
)
from places_census.census.geometry import Cell, generate_grid, nearest_sector
from places_census.config import SECTOR_CENTROIDS

class QuotaExceeded(Exception):
    pass


logger = logging.getLogger(__name__)
_log_fh: Any = None


def _jsonl(record: dict) -> None:
    global _log_fh
    if _log_fh is None:
        _log_fh = open(LOG_PATH, "a", encoding="utf-8")
    _log_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    _log_fh.flush()


def load_categories(only: list[str] | None = None) -> dict[str, list[str]]:
    raw: dict[str, list[str]] = yaml.safe_load(CATEGORIES_FILE.read_text(encoding="utf-8"))
    if only:
        return {k: v for k, v in raw.items() if k in only}
    return raw


class Census:
    def __init__(
        self,
        *,
        dry_run: bool = False,
        suburbs: bool = False,
        max_calls: int = MAX_CALLS,
        category_filter: list[str] | None = None,
        resume: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.suburbs = suburbs
        self.max_calls = max_calls
        self.category_filter = category_filter
        self.resume = resume
        self.saturated: list[dict] = []
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._call_count = 0
        self._stop = False

    def _check_quota(self) -> None:
        if self._call_count >= self.max_calls:
            self._stop = True
            raise QuotaExceeded(f"Reached MAX_CALLS={self.max_calls}")

    async def _search_cell(
        self,
        client: PlacesClient,
        conn: Any,
        category: str,
        lat: float,
        lng: float,
        radius: float,
        progress: Progress,
        task_id: Any,
    ) -> None:
        if self._stop:
            return

        # Semaphore covers only the HTTP request + DB write, NOT recursive sub-cell calls.
        # Holding the semaphore across asyncio.gather(sub-cells) causes near-deadlock:
        # all 5 slots blocked waiting for children that can't acquire the semaphore.
        saturated = False
        async with self._sem:
            if self._stop:
                return
            try:
                places, from_cache = await client.nearby_search(lat, lng, radius, category)
            except BudgetExhausted as exc:
                logger.error("Budget exhausted: %s", exc)
                self._stop = True
                return
            except FatalAPIError:
                raise
            except Exception as exc:
                logger.warning("request failed lat=%.5f lng=%.5f r=%.0f: %s", lat, lng, radius, exc)
                return

            if not from_cache:
                self._call_count += 1
                if self._call_count >= self.max_calls:
                    self._stop = True

            saturated = len(places) >= 20
            db_mod.log_call(
                conn,
                category=category,
                lat=lat,
                lng=lng,
                radius=radius,
                result_count=len(places),
                saturated=saturated,
                from_cache=from_cache,
            )
            _jsonl(
                dict(
                    ts=datetime.now(timezone.utc).isoformat(),
                    category=category,
                    lat=lat,
                    lng=lng,
                    radius=radius,
                    result_count=len(places),
                    saturated=saturated,
                    from_cache=from_cache,
                    sku="Pro",
                )
            )
            for p in places:
                db_mod.upsert_place(conn, p, category)
            progress.advance(task_id)

        # Recurse outside the semaphore so sub-cells can acquire their own slots freely.
        if saturated:
            if radius > MIN_RADIUS:
                cell = Cell(
                    lat - _deg_from_m(radius),
                    lat + _deg_from_m(radius),
                    lng - _deg_from_m(radius, lat),
                    lng + _deg_from_m(radius, lat),
                )
                new_r = radius / 2
                tasks = [
                    self._search_cell(client, conn, category, *sub.center, new_r, progress, task_id)
                    for sub in cell.split_into_4()
                ]
                await asyncio.gather(*tasks)
            else:
                self.saturated.append(dict(category=category, lat=lat, lng=lng, radius=radius))
                logger.warning("SATURATED at min_radius cat=%s lat=%.5f lng=%.5f", category, lat, lng)

    async def run(self) -> None:
        categories = load_categories(self.category_filter)
        bbox = BBOX_SUBURBS if self.suburbs else BBOX

        conn = db_mod.get_connection()
        if self.resume:
            self._call_count = db_mod.get_call_count(conn)
            logger.info("Resuming from %d prior API calls", self._call_count)

        async with PlacesClient(dry_run=self.dry_run, conn=conn) as client:
            with Progress(
                SpinnerColumn(),
                "[progress.description]{task.description}",
                BarColumn(),
                MofNCompleteColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
            ) as progress:
                for vertical, types in categories.items():
                    for place_type in types:
                        if self._stop:
                            break
                        centres = list(generate_grid(bbox, START_RADIUS))
                        task_id = progress.add_task(
                            f"[cyan]{vertical}/{place_type}", total=len(centres)
                        )
                        tasks = [
                            self._search_cell(
                                client, conn, place_type, lat, lng, START_RADIUS, progress, task_id
                            )
                            for lat, lng in centres
                        ]
                        await asyncio.gather(*tasks)
                        await asyncio.sleep(PAUSE_BETWEEN_TYPES)

        # Post-processing: assign sectors
        logger.info("Assigning sectors…")
        rows = conn.execute("SELECT place_id, lat, lng FROM places WHERE sector IS NULL").fetchall()
        assignments = {
            r["place_id"]: nearest_sector(r["lat"], r["lng"], SECTOR_CENTROIDS)
            for r in rows
        }
        db_mod.update_sectors(conn, assignments)

        logger.info(
            "Done. Total API calls (non-cached): %d. Saturated cells: %d",
            self._call_count,
            len(self.saturated),
        )


import math


def _deg_from_m(metres: float, lat: float = 47.0) -> float:
    return metres / 6_371_000 * (180 / math.pi)
