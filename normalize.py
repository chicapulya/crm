"""Step 2: Type normalization — populate places.vertical from taxonomy.yaml."""
from __future__ import annotations
import sqlite3
import yaml
from pathlib import Path
from config import DB_PATH

TAXONOMY_FILE = Path(__file__).parent / "taxonomy.yaml"


def load_taxonomy() -> dict[str, str]:
    """Returns {primaryType: vertical} flat mapping."""
    raw: dict[str, list[str]] = yaml.safe_load(TAXONOMY_FILE.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for vertical, types in raw.items():
        for t in types:
            if t in mapping:
                raise ValueError(f"Duplicate type '{t}' in taxonomy (in both '{mapping[t]}' and '{vertical}')")
            mapping[t] = vertical
    return mapping


def run() -> None:
    mapping = load_taxonomy()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("ALTER TABLE places ADD COLUMN vertical TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Validate: every non-null primaryType in DB must be in taxonomy
    db_types = {
        r[0]
        for r in conn.execute("SELECT DISTINCT primary_type FROM places").fetchall()
        if r[0] is not None
    }
    unmapped = db_types - set(mapping)
    if unmapped:
        raise ValueError(f"Types present in DB but missing from taxonomy.yaml:\n  {sorted(unmapped)}")

    # Apply mapping for non-null primary_type
    for primary_type, vertical in mapping.items():
        conn.execute(
            "UPDATE places SET vertical = ? WHERE primary_type = ?",
            (vertical, primary_type),
        )

    # For null primary_type: infer from first recognized type in types_json array
    import json
    null_rows = conn.execute(
        "SELECT place_id, types_json FROM places WHERE primary_type IS NULL"
    ).fetchall()
    inferred = 0
    for row in null_rows:
        types = json.loads(row["types_json"] or "[]")
        vertical = next((mapping[t] for t in types if t in mapping), "excluded")
        conn.execute(
            "UPDATE places SET vertical = ? WHERE place_id = ?",
            (vertical, row["place_id"]),
        )
        if vertical != "excluded":
            inferred += 1

    conn.commit()
    if inferred:
        print(f"Inferred vertical from types_json for {inferred} null-primaryType places.")

    # Report
    rows = conn.execute("""
        SELECT vertical, COUNT(*) AS cnt
        FROM places
        GROUP BY vertical
        ORDER BY cnt DESC
    """).fetchall()

    print(f"\n{'vertical':<16} {'places':>7}")
    print("-" * 24)
    for r in rows:
        print(f"{r['vertical']:<16} {r['cnt']:>7}")
    total = sum(r["cnt"] for r in rows)
    print("-" * 24)
    print(f"{'TOTAL':<16} {total:>7}")
    print()

    null_count = conn.execute(
        "SELECT COUNT(*) FROM places WHERE vertical IS NULL"
    ).fetchone()[0]
    if null_count:
        print(f"WARNING: {null_count} places still have no vertical assigned!")
    else:
        print("OK — all places have a vertical.")

    conn.close()


if __name__ == "__main__":
    run()
