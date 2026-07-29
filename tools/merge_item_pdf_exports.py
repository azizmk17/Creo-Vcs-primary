#!/usr/bin/env python3
"""Merge duplicate Item PDF/STEP export documents into versioned documents.

Some older Nexus projects can have several active PDF or STEP export records
under the same EBOM Item. Functionally those files are versions of the same
delivery export, but they appear as separate documents in the UI. This
standalone maintenance patch moves the version rows under one canonical
``part_files`` row per Item/type/role and renumbers the versions by date.

Default behavior is intentionally conservative:

* only active ``part_files`` rows are considered;
* only ``file_type='PDF'`` and ``file_type='STEP'`` are considered;
* only generated/export PDF and STEP roles are considered;
* grouping is by ``part_id`` and ``file_role``;
* version rows keep their original ids, paths, hashes, lifecycle, revision and
  commit metadata;
* duplicate ``part_files`` rows are soft-deleted.

Examples:
    python tools/merge_item_pdf_exports.py C:\\path\\to\\creo_vcs.db
    python tools/merge_item_pdf_exports.py C:\\path\\to\\creo_vcs.db --apply
    python tools/merge_item_pdf_exports.py C:\\path\\to\\creo_vcs.db --apply --all-delivery-roles
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_DELIVERY_EXPORT_ROLES = {
    "PDF": {"generated_pdf", "exported_pdf"},
    "STEP": {"generated_step", "exported_step"},
    "STP": {"generated_step", "exported_step"},
}


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
    backup = database.with_name(f"{database.name}.before_delivery_merge_{stamp}.bak")
    with sqlite3.connect(str(backup)) as destination:
        conn.backup(destination)
    return backup


def _effective_role(row: sqlite3.Row) -> str:
    role = str(row["file_role"] if "file_role" in row.keys() else "").strip().lower()
    if role:
        return role
    file_type = str(row["file_type"] if "file_type" in row.keys() else "").strip().upper()
    if file_type in {"STEP", "STP"}:
        return "generated_step"
    return "generated_pdf"


def _version_sort_key(row: sqlite3.Row) -> tuple[str, int]:
    created_at = str(row["created_at"] or "")
    return created_at, int(row["id"])


def _load_candidate_groups(
    conn: sqlite3.Connection,
    *,
    all_delivery_roles: bool,
) -> dict[tuple[int, str, str], list[sqlite3.Row]]:
    file_columns = _columns(conn, "part_files")
    if not {"id", "part_id", "file_type"}.issubset(file_columns):
        raise RuntimeError("part_files table is missing required columns")

    deleted_filter = "AND deleted_at IS NULL" if "deleted_at" in file_columns else ""
    rows = conn.execute(
        f"""
        SELECT *
        FROM part_files
        WHERE UPPER(TRIM(COALESCE(file_type,''))) IN ('PDF', 'STEP', 'STP')
          {deleted_filter}
        ORDER BY part_id, UPPER(TRIM(COALESCE(file_type,''))), COALESCE(created_at, ''), id
        """
    ).fetchall()

    groups: dict[tuple[int, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        file_type = str(row["file_type"] or "").strip().upper()
        if file_type == "STP":
            file_type = "STEP"
        role = _effective_role(row)
        if not all_delivery_roles and role not in DEFAULT_DELIVERY_EXPORT_ROLES.get(file_type, set()):
            continue
        groups[(int(row["part_id"]), file_type, role)].append(row)
    return {key: value for key, value in groups.items() if len(value) > 1}


def _load_versions(conn: sqlite3.Connection, file_ids: list[int]) -> list[sqlite3.Row]:
    if not file_ids:
        return []
    version_columns = _columns(conn, "part_file_versions")
    deleted_filter = "AND deleted_at IS NULL" if "deleted_at" in version_columns else ""
    placeholders = ",".join("?" for _ in file_ids)
    return conn.execute(
        f"""
        SELECT *
        FROM part_file_versions
        WHERE file_id IN ({placeholders})
          {deleted_filter}
        ORDER BY COALESCE(created_at, ''), id
        """,
        tuple(file_ids),
    ).fetchall()


def _choose_canonical_file(files: list[sqlite3.Row], versions: list[sqlite3.Row]) -> sqlite3.Row:
    active_version_ids = {
        int(row["active_version_id"])
        for row in files
        if "active_version_id" in row.keys() and row["active_version_id"] is not None
    }
    active_versions = [row for row in versions if int(row["id"]) in active_version_ids]
    newest_active = max(active_versions, key=_version_sort_key, default=None)
    if newest_active:
        for file_row in files:
            if int(file_row["id"]) == int(newest_active["file_id"]):
                return file_row
    return min(files, key=lambda row: (str(row["created_at"] or ""), int(row["id"])))


def _item_label(conn: sqlite3.Connection, part_id: int) -> str:
    bom_columns = _columns(conn, "bom")
    if not bom_columns:
        return str(part_id)
    select_bits = ["id"]
    for column in ("part_number", "number", "name", "description", "aes_number"):
        if column in bom_columns:
            select_bits.append(column)
    row = conn.execute(
        f"SELECT {', '.join(select_bits)} FROM bom WHERE id=?", (int(part_id),)
    ).fetchone()
    if not row:
        return str(part_id)
    number = str(
        row["part_number"] if "part_number" in row.keys() else row["number"] if "number" in row.keys() else ""
    ).strip()
    name = str(row["name"] if "name" in row.keys() else row["description"] if "description" in row.keys() else "").strip()
    aes = str(row["aes_number"] if "aes_number" in row.keys() else "").strip()
    parts = [value for value in (number, name, f"AES {aes}" if aes else "") if value]
    return " - ".join(parts) or str(part_id)


def _plan_merge(conn: sqlite3.Connection, *, all_delivery_roles: bool) -> list[dict]:
    groups = _load_candidate_groups(conn, all_delivery_roles=all_delivery_roles)
    plans = []
    for (part_id, file_type, role), files in sorted(groups.items()):
        file_ids = [int(row["id"]) for row in files]
        versions = _load_versions(conn, file_ids)
        if len(versions) <= 1:
            continue
        canonical = _choose_canonical_file(files, versions)
        duplicate_file_ids = [
            int(row["id"]) for row in files if int(row["id"]) != int(canonical["id"])
        ]
        ordered_versions = sorted(versions, key=_version_sort_key)
        active_version_id = int(ordered_versions[-1]["id"])
        plans.append(
            {
                "part_id": int(part_id),
                "part_label": _item_label(conn, int(part_id)),
                "file_type": file_type,
                "role": role,
                "canonical_file_id": int(canonical["id"]),
                "duplicate_file_ids": duplicate_file_ids,
                "version_ids": [int(row["id"]) for row in ordered_versions],
                "active_version_id": active_version_id,
            }
        )
    return plans


def _apply_plan(conn: sqlite3.Connection, plan: dict) -> None:
    canonical_file_id = int(plan["canonical_file_id"])
    version_ids = [int(value) for value in plan["version_ids"]]
    duplicate_file_ids = [int(value) for value in plan["duplicate_file_ids"]]

    # Avoid UNIQUE(file_id, version_no) collisions while moving all versions to
    # the canonical file.
    for offset, version_id in enumerate(version_ids, start=1):
        conn.execute(
            "UPDATE part_file_versions SET version_no=? WHERE id=?",
            (-1000000 - offset, version_id),
        )

    for version_id in version_ids:
        conn.execute(
            "UPDATE part_file_versions SET file_id=? WHERE id=?",
            (canonical_file_id, version_id),
        )

    for version_no, version_id in enumerate(version_ids, start=1):
        conn.execute(
            "UPDATE part_file_versions SET version_no=? WHERE id=?",
            (version_no, version_id),
        )

    conn.execute(
        "UPDATE part_files SET active_version_id=? WHERE id=?",
        (int(plan["active_version_id"]), canonical_file_id),
    )

    if duplicate_file_ids:
        placeholders = ",".join("?" for _ in duplicate_file_ids)
        file_columns = _columns(conn, "part_files")
        if "deleted_at" in file_columns:
            conn.execute(
                f"""
                UPDATE part_files
                SET deleted_at=COALESCE(deleted_at, datetime('now')),
                    active_version_id=NULL
                WHERE id IN ({placeholders})
                """,
                tuple(duplicate_file_ids),
            )
        else:
            # Very old schemas do not have soft-delete support. Keep the rows
            # inert by clearing the active pointer; all versions are already on
            # the canonical document.
            conn.execute(
                f"UPDATE part_files SET active_version_id=NULL WHERE id IN ({placeholders})",
                tuple(duplicate_file_ids),
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge duplicate active Item PDF/STEP exports into version history."
    )
    parser.add_argument("database", type=Path, help="Path to the Nexus SQLite database")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the patch. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--all-delivery-roles",
        action="store_true",
        help=(
            "Merge all active PDF/STEP documents per item/type/role. Without this flag, "
            "only generated/export PDF and STEP roles are considered."
        ),
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
        if not _table_exists(conn, "part_files") or not _table_exists(conn, "part_file_versions"):
            parser.error("Database does not contain part_files / part_file_versions tables.")

        plans = _plan_merge(conn, all_delivery_roles=bool(args.all_delivery_roles))
        total_versions = sum(len(plan["version_ids"]) for plan in plans)
        total_duplicates = sum(len(plan["duplicate_file_ids"]) for plan in plans)

        print(f"Database: {database}")
        print(
            "PDF/STEP export groups to merge: "
            f"{len(plans)} group(s), {total_duplicates} duplicate document row(s), "
            f"{total_versions} version row(s)."
        )
        for plan in plans[:50]:
            print(
                f"- Item {plan['part_id']} ({plan['part_label']}), "
                f"type={plan['file_type']}, role={plan['role']}: "
                f"keep file {plan['canonical_file_id']}, merge files "
                f"{plan['duplicate_file_ids']}, versions {plan['version_ids']}"
            )
        if len(plans) > 50:
            print(f"... {len(plans) - 50} more group(s)")

        if not args.apply:
            print("Dry run only; nothing was changed. Re-run with --apply to patch the database.")
            return 0

        backup = None
        if not args.no_backup:
            backup = _backup_database(conn, database)

        try:
            conn.execute("BEGIN IMMEDIATE")
            for plan in plans:
                _apply_plan(conn, plan)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    if backup:
        print(f"Backup: {backup}")
    print("Patch completed. PDF/STEP exports are now consolidated as version history.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (sqlite3.Error, OSError, shutil.Error, RuntimeError) as exc:
        print(f"Patch failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
