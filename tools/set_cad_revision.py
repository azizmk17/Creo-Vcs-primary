#!/usr/bin/env python3
"""Set Nexus Item CAD Rev values in an existing SQLite database.

Examples:
    python tools/set_cad_revision.py C:\path\to\creo_vcs.db A010
    python tools/set_cad_revision.py C:\path\to\creo_vcs.db A010 --apply
    python tools/set_cad_revision.py C:\path\to\creo_vcs.db B --project-id 12 --only-blank --apply
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _backup_database(conn: sqlite3.Connection, database: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = database.with_name(f"{database.name}.before_cad_rev_{stamp}.bak")
    with sqlite3.connect(str(backup)) as destination:
        conn.backup(destination)
    return backup


def _ensure_cad_revision_column(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "bom"):
        raise RuntimeError("The database has no bom table.")
    if "cad_revision" not in _columns(conn, "bom"):
        conn.execute("ALTER TABLE bom ADD COLUMN cad_revision TEXT DEFAULT ''")


def _where_clause(
    bom_columns: set[str], project_id: int | None, only_blank: bool
) -> tuple[str, list[object]]:
    clauses = ["deleted_at IS NULL"] if "deleted_at" in bom_columns else []
    params: list[object] = []
    if project_id is not None:
        clauses.append("project_id=?")
        params.append(int(project_id))
    if only_blank:
        clauses.append("TRIM(COALESCE(cad_revision, '')) = ''")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch existing Nexus Items to a specific CAD Rev."
    )
    parser.add_argument("database", type=Path, help="Path to the Nexus SQLite database")
    parser.add_argument("revision", help="CAD Rev to write, for example A010 or B")
    parser.add_argument(
        "--project-id",
        type=int,
        default=None,
        help="Only update Items in this project id.",
    )
    parser.add_argument(
        "--only-blank",
        action="store_true",
        help="Only fill Items where CAD Rev is currently blank.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the patch. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create the automatic pre-patch backup.",
    )
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    if not database.is_file():
        parser.error(f"Database does not exist: {database}")

    revision = str(args.revision or "").strip().upper()
    if not revision:
        parser.error("Revision cannot be blank.")

    with sqlite3.connect(str(database)) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "bom"):
            raise RuntimeError("The database has no bom table.")
        bom_columns = _columns(conn, "bom")
        if args.project_id is not None and "project_id" not in bom_columns:
            raise RuntimeError("The bom table has no project_id column; remove --project-id.")
        has_cad_revision = "cad_revision" in bom_columns
        count_only_blank = args.only_blank and has_cad_revision
        where_sql, params = _where_clause(bom_columns, args.project_id, count_only_blank)
        count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM bom{where_sql}",
                tuple(params),
            ).fetchone()[0]
        )
        print(f"Database: {database}")
        print(f"Target CAD Rev: {revision}")
        print(f"Matched Item(s): {count}")

        if not args.apply:
            if not has_cad_revision:
                print("The cad_revision column is missing and would be added during --apply.")
            if args.only_blank and not has_cad_revision:
                print("--only-blank has no effect before the cad_revision column exists.")
            print("Dry run only; nothing was changed. Re-run with --apply to patch the database.")
            return 0

        backup = None
        if not args.no_backup:
            backup = _backup_database(conn, database)

        try:
            conn.execute("BEGIN IMMEDIATE")
            if not has_cad_revision:
                conn.execute("ALTER TABLE bom ADD COLUMN cad_revision TEXT DEFAULT ''")
                bom_columns = _columns(conn, "bom")
                where_sql, params = _where_clause(
                    bom_columns, args.project_id, args.only_blank
                )
            modified_sql = ", modified=datetime('now')" if "modified" in bom_columns else ""
            updated = conn.execute(
                f"UPDATE bom SET cad_revision=?{modified_sql}{where_sql}",
                (revision, *params),
            ).rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    print(f"Updated Item(s): {updated}")
    if backup:
        print(f"Backup: {backup}")
    print("Patch completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (sqlite3.Error, OSError, shutil.Error, RuntimeError) as exc:
        print(f"Patch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
