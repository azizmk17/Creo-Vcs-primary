#!/usr/bin/env python3
"""
Remove orphan CAD-Item association rows.

Problem
-------
Older Nexus versions could leave rows in ``cad_item_associations`` after an
EBOM Item was deleted.  Then CAD checkout can fail because the CAD Document is
still associated to an Item id that no longer exists in ``bom``.

This patch deletes every association where:

    cad_item_associations.item_id IS NOT NULL
    AND no bom.id exists for that item_id

Usage
-----
Run from the Nexus repository root.

Preview only, no database write:

    python tools/remove_orphan_cad_item_associations.py --dry-run

Apply the repair:

    python tools/remove_orphan_cad_item_associations.py --apply

Use a specific database:

    python tools/remove_orphan_cad_item_associations.py --db "C:\\path\\to\\creo_vcs.db" --dry-run
    python tools/remove_orphan_cad_item_associations.py --db "C:\\path\\to\\creo_vcs.db" --apply

What --dry-run means
--------------------
``--dry-run`` only reads the database and prints the orphan associations that
would be deleted.  It does not write anything.

What --apply does
-----------------
``--apply`` permanently deletes the orphan rows from:

    cad_item_associations

It does not delete CAD Documents, EBOM Items, files, commits, or vault content.

Recommendation
--------------
For production data, make a copy of the SQLite database before running
``--apply``.  The script is intentionally narrow, but deletion is still a real
database write.
"""

from __future__ import annotations

import argparse
import os
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


def resolve_db(path: str) -> str:
    candidate = Path(path or default_db_path())
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return str(candidate)


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name),),
    ).fetchone()
    return row is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete cad_item_associations rows whose item_id no longer exists in bom."
    )
    parser.add_argument("--db", default=default_db_path(), help="SQLite database path.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview deletions only.")
    mode.add_argument("--apply", action="store_true", help="Delete orphan association rows.")
    args = parser.parse_args()

    db_path = resolve_db(args.db)
    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not table_exists(conn, "cad_item_associations"):
            print("Table cad_item_associations does not exist. Nothing to repair.")
            return 0
        if not table_exists(conn, "bom"):
            print("Table bom does not exist. Cannot validate Item ids.", file=sys.stderr)
            return 2

        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    a.id AS association_id,
                    a.cad_document_id,
                    a.item_id,
                    a.association_type,
                    a.active,
                    d.file_name AS cad_file_name,
                    d.name AS cad_name,
                    d.category AS cad_category
                FROM cad_item_associations a
                LEFT JOIN cad_documents d ON d.id=a.cad_document_id
                WHERE a.item_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM bom b WHERE b.id=a.item_id
                  )
                ORDER BY a.item_id, a.cad_document_id, a.id
                """
            ).fetchall()
        ]

        print(f"Database: {db_path}")
        print(f"Orphan cad_item_associations rows: {len(rows)}")
        for row in rows[:300]:
            cad_label = row.get("cad_file_name") or row.get("cad_name") or row.get("cad_document_id")
            print(
                f"- association_id={row['association_id']} | missing_item_id={row['item_id']} | "
                f"cad_document_id={row.get('cad_document_id')} | cad={cad_label} | "
                f"type={row.get('association_type') or ''} | active={row.get('active')}"
            )
        if len(rows) > 300:
            print(f"... {len(rows) - 300} more row(s) not printed")

        if args.dry_run:
            print("Dry-run only. No changes written.")
            return 0

        with conn:
            conn.executemany(
                "DELETE FROM cad_item_associations WHERE id=?",
                [(int(row["association_id"]),) for row in rows],
            )
        print(f"Applied. Deleted {len(rows)} orphan association row(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
