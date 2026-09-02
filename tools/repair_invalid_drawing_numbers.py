#!/usr/bin/env python3
"""
Repair invalid EBOM drawing-number metadata.

Problem
-------
Some older imports/updates filled ``bom.drawing_number`` with the related Creo
drawing file name, for example:

    assy_output_60_t_diff_n.drw.1
    wire_relay.drw.2

That value is not a drawing number.  If an Item has no real drawing number, the
Drawing Number parameter must be blank.  Real values such as ``P3256126`` are
kept.

Usage
-----
Run from the Nexus repository root.

Preview only, no database write:

    python tools/repair_invalid_drawing_numbers.py --dry-run

Apply the repair:

    python tools/repair_invalid_drawing_numbers.py --apply

Use a specific database:

    python tools/repair_invalid_drawing_numbers.py --db "C:\\path\\to\\creo_vcs.db" --dry-run
    python tools/repair_invalid_drawing_numbers.py --db "C:\\path\\to\\creo_vcs.db" --apply

What --dry-run means
--------------------
``--dry-run`` only reads the database and prints the rows that would be fixed.
It does not update anything.  Use it before ``--apply`` when repairing a
production database.

What --apply does
-----------------
``--apply`` updates every invalid row:

    UPDATE bom SET drawing_number='' WHERE id=...

It only touches ``bom.drawing_number``.  It does not change ``base_drw_name``,
``drawing``, CAD associations, attachments, revisions, commits, or vault files.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def default_db_path() -> str:
    try:
        from config import DB_NAME

        return str(DB_NAME)
    except Exception:
        return "creo_vcs.db"


def is_invalid_drawing_number(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if re.search(r"\.(?:drw|prt|asm)(?:\.\d+)?$", lowered):
        return True
    if re.search(r"\.(?:pdf|step|stp|iges|igs|dxf|dwg)$", lowered):
        return True
    return False


def resolve_db(path: str) -> str:
    candidate = Path(path or default_db_path())
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return str(candidate)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Blank invalid EBOM drawing_number values that contain file names."
    )
    parser.add_argument("--db", default=default_db_path(), help="SQLite database path.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview changes only.")
    mode.add_argument("--apply", action="store_true", help="Update the database.")
    args = parser.parse_args()

    db_path = resolve_db(args.db)
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(bom)").fetchall()
        }
        if "drawing_number" not in columns:
            print("Table bom has no drawing_number column. Nothing to repair.")
            return 0

        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, part_number, aes_number, name, drawing_number
                FROM bom
                WHERE COALESCE(drawing_number, '') <> ''
                ORDER BY id
                """
            ).fetchall()
            if is_invalid_drawing_number(row["drawing_number"])
        ]

        print(f"Database: {db_path}")
        print(f"Invalid drawing_number rows: {len(rows)}")
        for row in rows[:200]:
            print(
                f"- id={row['id']} | item={row.get('part_number') or ''} | "
                f"aes={row.get('aes_number') or ''} | name={row.get('name') or ''} | "
                f"drawing_number={row.get('drawing_number') or ''!r} -> ''"
            )
        if len(rows) > 200:
            print(f"... {len(rows) - 200} more row(s) not printed")

        if args.dry_run:
            print("Dry-run only. No changes written.")
            return 0

        with conn:
            conn.executemany(
                "UPDATE bom SET drawing_number='' WHERE id=?",
                [(int(row["id"]),) for row in rows],
            )
        print(f"Applied. Cleared drawing_number for {len(rows)} row(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
