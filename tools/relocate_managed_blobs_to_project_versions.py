#!/usr/bin/env python3
"""Move misplaced managed blobs from the family root into each project version.

Background
----------
Older Nexus builds stored ``storage_scheme='managed_blob'`` files under the
root project working directory, usually version A:

    <A working dir>/.nexus/vault/blobs/...

even when ``part_file_versions.project_version_label`` was B, C, ...

This patch repairs production data by copying those blobs into the working
directory of the project version that owns the file version. It keeps the
database ``vault_rel_path`` unchanged because it is intentionally relative to
the project working directory.

What this fixes
---------------
If a file version says:

    project_version_label = B
    vault_rel_path = .nexus\\vault\\blobs\\...

then the physical file must exist under:

    <B project working directory>\\.nexus\\vault\\blobs\\...

not under:

    <A/root project working directory>\\.nexus\\vault\\blobs\\...

The script finds B/C/... files that are physically stored in A/root and copies
them to the correct version folder.

Safety model
------------
1. Default mode is DRY-RUN. It prints the repair plan and changes nothing.
2. ``--apply`` copies files and creates a database backup first.
3. ``--apply --move`` copies files and then removes the misplaced source only
   when no other project version still references that source path.
4. The database ``vault_rel_path`` is not rewritten. It remains version-relative.
5. Existing legacy files are not converted. This patch only repairs
   ``storage_scheme='managed_blob'`` placement.

Recommended production procedure
--------------------------------
Close Nexus before running this patch.

Step 1 - dry-run:

    python tools\\relocate_managed_blobs_to_project_versions.py "I:\\path\\to\\creo_vcs.db"

Review the output. You should see COPY rows for B/C files currently located in
the wrong project folder. MISSING rows mean the script cannot find the physical
blob in the target/current/root folders; check that the drive is mounted and the
database project paths are correct.

Step 2 - apply copy repair:

    python tools\\relocate_managed_blobs_to_project_versions.py "I:\\path\\to\\creo_vcs.db" --apply

Open Nexus and verify generated PDF/STEP files from version B open correctly.

Step 3 - optional cleanup:

    python tools\\relocate_managed_blobs_to_project_versions.py "I:\\path\\to\\creo_vcs.db" --apply --move

Use cleanup only after verification. It removes misplaced source blobs only when
safe.

Examples:
    python tools/relocate_managed_blobs_to_project_versions.py C:\\path\\creo_vcs.db
    python tools/relocate_managed_blobs_to_project_versions.py C:\\path\\creo_vcs.db --apply
    python tools/relocate_managed_blobs_to_project_versions.py C:\\path\\creo_vcs.db --apply --move
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Relocation:
    version_id: int
    file_id: int
    part_id: int | None
    item_label: str
    version_label: str
    source: Path
    destination: Path
    action: str
    reason: str = ""


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
    backup = database.with_name(f"{database.name}.before_blob_relocate_{stamp}.bak")
    with sqlite3.connect(str(backup)) as destination:
        conn.backup(destination)
    return backup


def _project_rows(conn: sqlite3.Connection) -> dict[int, dict]:
    if not _table_exists(conn, "projects"):
        return {}
    return {
        int(row["id"]): dict(row)
        for row in conn.execute("SELECT * FROM projects").fetchall()
    }


def _version_project_map(projects: dict[int, dict]) -> dict[tuple[int, str], dict]:
    out = {}
    for project in projects.values():
        root_id = project.get("root_project_id") or project.get("id")
        label = str(project.get("version_label") or "").strip().upper()
        if root_id and label:
            out[(int(root_id), label)] = project
    return out


def _item_label(row: sqlite3.Row) -> str:
    number = str(row["part_number"] or "").strip() if "part_number" in row.keys() else ""
    aes = str(row["aes_number"] or "").strip() if "aes_number" in row.keys() else ""
    name = str(row["name"] or "").strip() if "name" in row.keys() else ""
    values = [value for value in (number, name, f"AES {aes}" if aes else "") if value]
    return " - ".join(values) or f"part {row['part_id']}" if "part_id" in row.keys() else "-"


def _load_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    required = {"part_file_versions", "part_files", "bom", "projects"}
    missing = [table for table in required if not _table_exists(conn, table)]
    if missing:
        raise RuntimeError(f"Missing required table(s): {', '.join(missing)}")

    return conn.execute(
        """
        SELECT
            v.id AS version_id,
            v.file_id,
            v.vault_rel_path,
            v.storage_scheme,
            v.root_project_id,
            v.project_version_label,
            pf.part_id,
            b.project_id,
            b.part_number,
            b.aes_number,
            b.name
        FROM part_file_versions v
        JOIN part_files pf ON pf.id=v.file_id
        JOIN bom b ON b.id=pf.part_id
        WHERE COALESCE(v.deleted_at,'')=''
          AND lower(COALESCE(v.storage_scheme,''))='managed_blob'
          AND TRIM(COALESCE(v.vault_rel_path,''))<>''
        ORDER BY v.id
        """
    ).fetchall()


def _choose_source(
    rel_path: str,
    root_project: dict | None,
    target_project: dict,
    current_project: dict | None,
) -> Path | None:
    candidates = []
    for project in (target_project, current_project, root_project):
        if not project:
            continue
        wd = str(project.get("working_directory") or "").strip()
        if wd:
            candidates.append(Path(wd) / rel_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _plan(conn: sqlite3.Connection) -> list[Relocation]:
    projects = _project_rows(conn)
    by_version = _version_project_map(projects)
    plans: list[Relocation] = []
    for row in _load_rows(conn):
        root_id = row["root_project_id"] or None
        if root_id is None:
            current_project = projects.get(int(row["project_id"])) if row["project_id"] is not None else None
            root_id = (current_project or {}).get("root_project_id") or (current_project or {}).get("id")
        version_label = str(row["project_version_label"] or "").strip().upper()
        current_project = projects.get(int(row["project_id"])) if row["project_id"] is not None else None
        if not version_label and current_project:
            version_label = str(current_project.get("version_label") or "").strip().upper()
        if not root_id or not version_label:
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label or "-", Path(), Path(), "skip",
                "Missing root_project_id or project_version_label",
            ))
            continue

        target_project = by_version.get((int(root_id), version_label))
        root_project = projects.get(int(root_id))
        if not target_project:
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label, Path(), Path(), "skip",
                "Cannot find target project version",
            ))
            continue

        target_dir = str(target_project.get("working_directory") or "").strip()
        rel_path = str(row["vault_rel_path"] or "").strip()
        if not target_dir or not rel_path:
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label, Path(), Path(), "skip",
                "Missing target working directory or vault path",
            ))
            continue

        destination = Path(target_dir) / rel_path
        source = _choose_source(rel_path, root_project, target_project, current_project)
        if source is None:
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label, Path(), destination, "missing",
                "File not found in target, current, or root project folders",
            ))
            continue

        try:
            if source.resolve() == destination.resolve():
                plans.append(Relocation(
                    int(row["version_id"]), int(row["file_id"]), row["part_id"],
                    _item_label(row), version_label, source, destination, "ok",
                    "Already in correct project version folder",
                ))
                continue
        except Exception:
            pass

        if destination.exists():
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label, source, destination, "ok",
                "Destination already exists",
            ))
        else:
            plans.append(Relocation(
                int(row["version_id"]), int(row["file_id"]), row["part_id"],
                _item_label(row), version_label, source, destination, "copy",
                "Will copy managed blob into its project version folder",
            ))
    return plans


def _protected_correct_paths(plans: list[Relocation]) -> set[Path]:
    protected = set()
    for item in plans:
        if item.action != "ok":
            continue
        try:
            if item.source and item.destination and item.source.resolve() == item.destination.resolve():
                protected.add(item.source.resolve())
            elif item.destination:
                protected.add(item.destination.resolve())
        except Exception:
            if item.destination:
                protected.add(item.destination)
    return protected


def _apply(plans: list[Relocation], *, move: bool) -> tuple[int, int]:
    copied = 0
    removed = 0
    protected_sources = _protected_correct_paths(plans)
    for item in plans:
        if item.action != "copy":
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.destination)
        copied += 1
        if move:
            try:
                source_resolved = item.source.resolve()
                if (
                    item.source.exists()
                    and source_resolved != item.destination.resolve()
                    and source_resolved not in protected_sources
                ):
                    item.source.unlink()
                    removed += 1
                elif source_resolved in protected_sources:
                    print(
                        f"[safe] kept source for version {item.version_id}; "
                        "another project version still references it."
                    )
            except Exception as exc:
                print(f"[warn] copied but could not remove source for version {item.version_id}: {exc}")
    return copied, removed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair managed_blob files that were created in the root/A project "
            "folder although their database version belongs to B/C/etc. "
            "Default mode is dry-run."
        ),
        epilog=(
            "Recommended: close Nexus, run dry-run, run --apply, verify files "
            "open from the correct project version, then optionally run "
            "--apply --move for cleanup. MISSING rows usually mean the project "
            "drive/path is not mounted or the physical blob is already absent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("database", help="Path to the production creo_vcs.db file.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair. Without this flag the script only prints a dry-run plan.",
    )
    parser.add_argument(
        "--move",
        action="store_true",
        help=(
            "After copying, remove the misplaced source file when safe. "
            "Use only after verifying the --apply copy repair."
        ),
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create a database backup before applying. Not recommended for production.",
    )
    args = parser.parse_args(argv)

    database = Path(args.database)
    if not database.exists():
        print(f"Database not found: {database}", file=sys.stderr)
        return 2

    with sqlite3.connect(str(database)) as conn:
        conn.row_factory = sqlite3.Row
        plans = _plan(conn)
        summary = {}
        for item in plans:
            summary[item.action] = summary.get(item.action, 0) + 1
        print("Managed blob relocation plan")
        print(f"Database: {database}")
        print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
        print("Summary:", ", ".join(f"{key}={value}" for key, value in sorted(summary.items())) or "none")
        for item in plans:
            if item.action in {"copy", "missing", "skip"}:
                print(
                    f"[{item.action.upper()}] v{item.version_id} file {item.file_id} "
                    f"{item.item_label} | project {item.version_label}"
                )
                if item.source:
                    print(f"  from: {item.source}")
                if item.destination:
                    print(f"  to:   {item.destination}")
                if item.reason:
                    print(f"  note: {item.reason}")

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to copy files.")
            return 0

        if not args.no_backup:
            backup = _backup_database(conn, database)
            print(f"Database backup created: {backup}")
        copied, removed = _apply(plans, move=bool(args.move))
        print(f"Copied: {copied}")
        print(f"Removed misplaced sources: {removed}")
        if not args.move:
            print("Source files were left in place. Use --move only after verifying the copied files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
