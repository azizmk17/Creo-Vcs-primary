#!/usr/bin/env python3
"""Set every current Nexus Item and CAD Document to WIP.

This is a standalone database patch: it uses only the Python standard
library and does not import or start Nexus. Historical Item revisions and CAD
iterations are retained; only each object's current revision/iteration is
changed from its released state.

Examples:
    python tools/set_all_objects_to_wip.py C:\path\to\creo_vcs.db
    python tools/set_all_objects_to_wip.py C:\path\to\creo_vcs.db --apply
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


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _backup_database(conn: sqlite3.Connection, database: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = database.with_name(f"{database.name}.before_wip_{stamp}.bak")
    with sqlite3.connect(str(backup)) as destination:
        conn.backup(destination)
    return backup


def _update_items(conn: sqlite3.Connection) -> tuple[int, int]:
    bom_columns = _columns(conn, "bom")
    if not bom_columns:
        return 0, 0

    assignments = []
    if "lifecycle_state" in bom_columns:
        assignments.append("lifecycle_state='WIP'")
    if "status" in bom_columns:
        assignments.append("status='Design'")
    if "released_at" in bom_columns:
        assignments.append("released_at=NULL")
    if "released_by" in bom_columns:
        assignments.append("released_by=NULL")
    if "modified" in bom_columns:
        assignments.append("modified=datetime('now')")
    item_count = 0
    if assignments:
        item_count = conn.execute(
            "UPDATE bom SET " + ", ".join(assignments)
        ).rowcount

    revision_count = 0
    revision_columns = _columns(conn, "bom_revisions")
    if revision_columns and "state" in revision_columns:
        revision_assignments = ["state='In Work'"]
        for column in ("released_at", "released_by", "release_note"):
            if column in revision_columns:
                revision_assignments.append(f"{column}=NULL")

        if "current_revision_id" in bom_columns:
            # Preserve historical Released revisions. For old Items without a
            # current pointer, use their newest revision as the current one.
            revision_count = conn.execute(
                """
                UPDATE bom_revisions
                SET """ + ", ".join(revision_assignments) + """
                WHERE id IN (
                    SELECT COALESCE(
                        b.current_revision_id,
                        (SELECT MAX(r.id) FROM bom_revisions r WHERE r.bom_id=b.id)
                    )
                    FROM bom b
                )
                """
            ).rowcount
    return item_count, revision_count


def _update_cad(conn: sqlite3.Connection) -> tuple[int, int]:
    document_columns = _columns(conn, "cad_documents")
    if not document_columns:
        return 0, 0

    assignments = []
    if "lifecycle_state" in document_columns:
        assignments.append("lifecycle_state='IN_WORK'")
    if "modified_at" in document_columns:
        assignments.append("modified_at=datetime('now')")
    document_count = 0
    if assignments:
        document_count = conn.execute(
            "UPDATE cad_documents SET " + ", ".join(assignments)
        ).rowcount

    iteration_count = 0
    iteration_columns = _columns(conn, "cad_document_iterations")
    required = {"id", "cad_document_id", "lifecycle_state"}
    if required.issubset(iteration_columns):
        can_match_current = {
            "revision", "iteration"
        }.issubset(document_columns) and {"revision", "iteration"}.issubset(
            iteration_columns
        )
        if can_match_current:
            iteration_count = conn.execute(
                """
                UPDATE cad_document_iterations
                SET lifecycle_state='IN_WORK'
                WHERE id IN (
                    SELECT COALESCE(
                        (
                            SELECT current_iteration.id
                            FROM cad_document_iterations current_iteration
                            WHERE current_iteration.cad_document_id=document.id
                              AND current_iteration.revision=document.revision
                              AND current_iteration.iteration=document.iteration
                            ORDER BY current_iteration.id DESC LIMIT 1
                        ),
                        (
                            SELECT fallback_iteration.id
                            FROM cad_document_iterations fallback_iteration
                            WHERE fallback_iteration.cad_document_id=document.id
                            ORDER BY fallback_iteration.id DESC LIMIT 1
                        )
                    )
                    FROM cad_documents document
                )
                """
            ).rowcount
    return document_count, iteration_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set all current Nexus Items and CAD Documents to WIP."
    )
    parser.add_argument("database", type=Path, help="Path to the Nexus SQLite database")
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

    with sqlite3.connect(str(database)) as conn:
        conn.row_factory = sqlite3.Row
        item_total = _count(conn, "bom")
        cad_total = _count(conn, "cad_documents")
        print(f"Database: {database}")
        print(f"Current objects found: {item_total} Item(s), {cad_total} CAD Document(s)")

        if not args.apply:
            print("Dry run only; nothing was changed. Re-run with --apply to patch the database.")
            return 0

        backup = None
        if not args.no_backup:
            backup = _backup_database(conn, database)

        try:
            conn.execute("BEGIN IMMEDIATE")
            items, item_revisions = _update_items(conn)
            cad_documents, cad_iterations = _update_cad(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    print(
        "Updated: "
        f"{items} Item(s), {item_revisions} current Item revision(s), "
        f"{cad_documents} CAD Document(s), and {cad_iterations} current CAD iteration(s)."
    )
    if backup:
        print(f"Backup: {backup}")
    print("Patch completed. Historical released revisions and iterations were preserved.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (sqlite3.Error, OSError, shutil.Error) as exc:
        print(f"Patch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
