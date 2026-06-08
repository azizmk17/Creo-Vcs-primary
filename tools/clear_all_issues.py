#!/usr/bin/env python3
"""One-time utility to remove all engineering issue data from a CreoVCS database.

The utility creates a SQLite backup before changing anything, clears every
table that directly references ``issues``, clears ``issues`` itself, resets
their SQLite sequences, and removes embedded issue state from snapshots.

Close CreoVCS before running this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


CONFIRMATION = "CLEAR ALL ISSUES"


def default_database_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    config_path = Path(appdata) / "creovcs" / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        workspace = Path(config["last_workspace_path"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    database = workspace / "creo_vcs.db"
    return database if database.is_file() else None


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def issue_child_tables(conn: sqlite3.Connection) -> list[str]:
    children = []
    table_names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table_name in table_names:
        for foreign_key in conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table_name)})"):
            if foreign_key[2] == "issues":
                children.append(table_name)
                break
    return sorted(set(children))


def row_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])
        for table in tables
    }


def snapshot_issue_state_count(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
    ).fetchone()
    if not exists:
        return 0
    count = 0
    for row in conn.execute("SELECT snapshot_data FROM snapshots"):
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "issue_state" in data:
            count += 1
    return count


def scrub_snapshot_issue_state(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
    ).fetchone()
    if not exists:
        return 0
    updates = []
    for snapshot_id, raw_data in conn.execute("SELECT id, snapshot_data FROM snapshots"):
        try:
            data = json.loads(raw_data)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "issue_state" not in data:
            continue
        data.pop("issue_state", None)
        updates.append((json.dumps(data, separators=(",", ":")), snapshot_id))
    conn.executemany("UPDATE snapshots SET snapshot_data=? WHERE id=?", updates)
    return len(updates)


def create_backup(database: Path, requested_path: Path | None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = requested_path or database.with_name(
        f"{database.stem}.before_issue_clear_{timestamp}{database.suffix}"
    )
    backup_path = backup_path.resolve()
    if backup_path == database.resolve():
        raise ValueError("Backup path must be different from the database path.")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise FileExistsError(f"Backup already exists: {backup_path}")
    with sqlite3.connect(database) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def clear_all_issues(database: Path, backup_path: Path | None = None) -> tuple[Path, dict]:
    database = database.resolve()
    backup = create_backup(database, backup_path)
    with sqlite3.connect(database, timeout=10) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        children = issue_child_tables(conn)
        tables = children + ["issues"]
        before = row_counts(conn, tables)
        snapshot_states_before = snapshot_issue_state_count(conn)
        foreign_key_errors_before = Counter(
            tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
        )

        try:
            conn.execute("BEGIN IMMEDIATE")
            for table in children:
                conn.execute(f"DELETE FROM {quote_identifier(table)}")
            conn.execute("DELETE FROM issues")
            snapshots_scrubbed = scrub_snapshot_issue_state(conn)

            sequence_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone()
            if sequence_exists:
                placeholders = ",".join("?" for _ in tables)
                conn.execute(
                    f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                    tables,
                )

            foreign_key_errors_after = Counter(
                tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()
            )
            new_foreign_key_errors = foreign_key_errors_after - foreign_key_errors_before
            if new_foreign_key_errors:
                errors = list(new_foreign_key_errors.elements())
                raise RuntimeError(f"Cleanup introduced foreign-key errors: {errors[:5]}")

            after = row_counts(conn, tables)
            if any(after.values()):
                raise RuntimeError(f"Issue cleanup verification failed: {after}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return backup, {
        "before": before,
        "after": after,
        "snapshot_issue_states_before": snapshot_states_before,
        "snapshots_scrubbed": snapshots_scrubbed,
        "preexisting_foreign_key_errors": sum(foreign_key_errors_before.values()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up a CreoVCS database, then permanently clear all issue data."
    )
    parser.add_argument(
        "--db",
        type=Path,
        help="Path to creo_vcs.db. Defaults to the workspace saved in the CreoVCS config.",
    )
    parser.add_argument("--backup", type=Path, help="Optional explicit backup database path.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be cleared.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = (args.db or default_database_path())
    if database is None:
        print("No database found. Pass --db with the full path to creo_vcs.db.", file=sys.stderr)
        return 2
    database = database.expanduser().resolve()
    if not database.is_file():
        print(f"Database does not exist: {database}", file=sys.stderr)
        return 2

    try:
        with sqlite3.connect(database, timeout=5) as conn:
            children = issue_child_tables(conn)
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='issues'"
            ).fetchone():
                print(f"No issues table found in {database}.")
                return 0
            tables = children + ["issues"]
            counts = row_counts(conn, tables)
            snapshot_states = snapshot_issue_state_count(conn)
    except sqlite3.Error as exc:
        print(f"Cannot inspect database: {exc}", file=sys.stderr)
        return 1

    print(f"Database: {database}")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print(f"  snapshots containing issue_state: {snapshot_states}")

    if args.dry_run:
        print("Dry run complete. No data was changed.")
        return 0

    if not args.yes:
        print("\nThis permanently clears all listed issue data after creating a backup.")
        typed = input(f"Type {CONFIRMATION!r} to continue: ").strip()
        if typed != CONFIRMATION:
            print("Cancelled. No data was changed.")
            return 2

    try:
        backup, result = clear_all_issues(database, args.backup)
    except Exception as exc:
        print(f"Cleanup failed. No transaction changes were committed: {exc}", file=sys.stderr)
        return 1

    print(f"Backup created: {backup}")
    print(f"Snapshots scrubbed: {result['snapshots_scrubbed']}")
    if result["preexisting_foreign_key_errors"]:
        print(
            "Pre-existing unrelated foreign-key errors preserved: "
            f"{result['preexisting_foreign_key_errors']}"
        )
    print("All issue data was cleared and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
