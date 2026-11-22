#!/usr/bin/env python3
"""Seed the catalog with Blender open-movie clips (CC-BY Blender Foundation)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running without editable install when PYTHONPATH includes src/
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from product_placement.catalog import seed_blender_open_movies  # noqa: E402
from product_placement.db import get_connection, init_schema, migrate_file_brands  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Seed catalog DB with Blender open movies")
    p.add_argument("--force", action="store_true", help="Seed even if titles exist")
    args = p.parse_args(argv)

    conn = get_connection()
    try:
        init_schema(conn)
        migrate_file_brands(conn)
        result = seed_blender_open_movies(conn, force=bool(args.force))
        print(json.dumps(result, indent=2, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
