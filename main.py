#!/usr/bin/env python3
"""
Chisinau Places Census — CLI entry point.

Usage examples:
  python main.py scout                        # Stage 1: single recon call
  python main.py run --dry-run                # estimate calls, no network
  python main.py run --categories beauty,food # selected verticals only
  python main.py run --max-calls 200          # hard cap
  python main.py run --resume                 # skip already-counted cells
  python main.py run --include-suburbs        # wider bbox
  python main.py report                       # generate report from existing DB
"""
from __future__ import annotations
import asyncio
import json
import logging
import sys

import click
from dotenv import load_dotenv

load_dotenv()

from config import API_KEY, NEARBY_SEARCH_URL, FIELD_MASK, LANGUAGE_CODE, REGION_CODE


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.option("--verbose", is_flag=True, default=False)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.command()
def scout() -> None:
    """Stage 1: single recon call — centre of Chisinau, cafe, r=500m."""
    import httpx

    if not API_KEY:
        click.echo("ERROR: GOOGLE_PLACES_API_KEY not set in .env", err=True)
        sys.exit(1)

    body = {
        "includedTypes": ["cafe"],
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": 47.0228, "longitude": 28.8350},
                "radius": 500.0,
            }
        },
        "languageCode": LANGUAGE_CODE,
        "regionCode": REGION_CODE,
    }

    click.echo(f"POST {NEARBY_SEARCH_URL}")
    click.echo(f"X-Goog-FieldMask: {FIELD_MASK}")
    click.echo(f"Body: {json.dumps(body, indent=2, ensure_ascii=False)}")
    click.echo("\n--- sending request ---\n")

    resp = httpx.post(
        NEARBY_SEARCH_URL,
        json=body,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": FIELD_MASK,
        },
        timeout=30,
    )
    click.echo(f"HTTP {resp.status_code}")
    data = resp.json()
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))

    places = data.get("places", [])
    click.echo(f"\n✓ Got {len(places)} places.")
    if places:
        click.echo("\nFirst place:")
        click.echo(json.dumps(places[0], indent=2, ensure_ascii=False))

    click.echo(
        "\n⚠️  Check Google Cloud Console > APIs & Services > Metrics to confirm SKU = 'Places API - Nearby Search Pro'."
        "\nDo NOT proceed to Stage 2 until you have confirmed this."
    )


@cli.command()
@click.option("--dry-run", is_flag=True, default=False, help="No network calls, just estimate.")
@click.option("--categories", default=None, help="Comma-separated verticals, e.g. beauty,food")
@click.option("--max-calls", default=None, type=int, help="Override MAX_CALLS")
@click.option("--include-suburbs", is_flag=True, default=False)
@click.option("--resume", is_flag=True, default=False, help="Resume interrupted run.")
def run(
    dry_run: bool,
    categories: str | None,
    max_calls: int | None,
    include_suburbs: bool,
    resume: bool,
) -> None:
    """Run the census collection."""
    from census import Census
    import config

    cat_list = [c.strip() for c in categories.split(",")] if categories else None
    mc = max_calls if max_calls is not None else config.MAX_CALLS

    census = Census(
        dry_run=dry_run,
        suburbs=include_suburbs,
        max_calls=mc,
        category_filter=cat_list,
        resume=resume,
    )

    asyncio.run(census.run())

    import report as report_mod
    report_mod.run(saturated=census.saturated)


@cli.command()
def report() -> None:
    """Generate report and CSV from existing database."""
    import report as report_mod
    report_mod.run()


@cli.command()
def estimate() -> None:
    """Print call budget estimate for the current config."""
    import yaml
    from config import BBOX, CATEGORIES_FILE, MAX_CALLS, MIN_RADIUS, START_RADIUS
    from geometry import generate_grid

    FREE_QUOTA = 5_000
    PRO_PRICE_PER_1K = 32.0  # USD after free tier

    all_cats: dict[str, list[str]] = yaml.safe_load(CATEGORIES_FILE.read_text(encoding="utf-8"))
    total_types = sum(len(v) for v in all_cats.values())
    centres = list(generate_grid(BBOX, START_RADIUS))
    per_type = len(centres)
    base = total_types * per_type

    # Conservative subdivision overhead: dense city centre may double calls in 1-2 types
    worst = base + total_types * per_type  # 2x total
    cost_worst = max(0, worst - FREE_QUOTA) / 1000 * PRO_PRICE_PER_1K

    status = "OK - fits in free tier" if worst <= FREE_QUOTA else "WARNING - may exceed free tier"

    click.echo(f"\n{'='*52}")
    click.echo(f"  Budget estimate (START_RADIUS={START_RADIUS}m, MIN_RADIUS={MIN_RADIUS}m)")
    click.echo(f"{'='*52}")
    click.echo(f"  Grid cells per type   : {per_type}")
    click.echo(f"  Total types           : {total_types}")
    click.echo(f"  Base calls            : {base:,}")
    click.echo(f"  Worst case (2x sub.)  : {worst:,}")
    click.echo(f"  Free Pro quota/month  : {FREE_QUOTA:,}")
    click.echo(f"  Hard stop (MAX_CALLS) : {MAX_CALLS:,}")
    click.echo(f"  Cost worst case       : ~${cost_worst:.0f} USD")
    click.echo(f"  Status                : {status}")
    click.echo(f"{'='*52}\n")

    click.echo("Per-vertical breakdown:")
    for vertical, types in all_cats.items():
        n = len(types) * per_type
        click.echo(f"  {vertical:<12} {len(types):2} types  {n:>5,} base calls")

    if worst > FREE_QUOTA:
        click.echo(
            f"\nTo reduce: increase START_RADIUS in config.py or run --categories with a subset.\n"
            f"Current worst-case exceeds free tier by {worst - FREE_QUOTA:,} calls."
        )


if __name__ == "__main__":
    cli()
