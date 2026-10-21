"""Administrator-only workspace migration with maintenance mode and a backup.

Close every Nexus/CADViewer client before running this tool. The maintenance
flag protects new compatible clients; it cannot stop legacy clients or an
already-running process. The lock file prevents concurrent migration tools,
not arbitrary database access.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.updates.compatibility import (
    CompatibilityError, check_database, latest_schema_version, readonly_connection,
    temporarily_disabled_runtime_guard, version_tuple,
)


def _connect(db_path):
    return getattr(sqlite3, "_nexus_original_connect", sqlite3.connect)(str(db_path), timeout=10)


@contextmanager
def _migration_lock(db_path):
    path = Path(db_path).parent / ".nexus-migration.lock"
    try:
        descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"A migration lock already exists: {path}. Confirm no migration process is running "
            "before asking the administrator to remove a stale lock."
        ) from exc
    locked = False
    try:
        payload = json.dumps({"pid": os.getpid(), "database": str(db_path),
                              "started_at": datetime.now(timezone.utc).isoformat()})
        os.write(descriptor, payload.encode("utf-8"))
        os.fsync(descriptor)
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            locked = True
        yield path
    finally:
        if locked:
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        os.close(descriptor)
        path.unlink()


def _set_maintenance(db_path, message):
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO app_metadata(key, value) VALUES('maintenance_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (message,),
        )
        conn.commit()


def _backup_database(db_path, backup_dir, schema):
    directory = Path(backup_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError(f"Backup location is not a directory: {directory}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{Path(db_path).stem}.before-schema-{schema}.{stamp}.{uuid.uuid4().hex[:10]}.db"
    if target.resolve() == Path(db_path).resolve() or target.exists():
        raise ValueError("The backup must be a new file separate from the workspace database.")
    descriptor = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(readonly_connection(db_path)) as source, closing(_connect(target)) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"Backup integrity check failed: {target}")
    except Exception:
        # Only the new file reserved above can be removed. Existing backups
        # and the live database are never overwritten or deleted.
        target.unlink(missing_ok=True)
        raise
    return target


def _run_migrations():
    from setup.migrations import migrate

    migrate()


def _finish_migration(db_path, *, minimum_app_version, max_schema):
    with closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        actual = int(row[0]) if row and row[0] is not None else 0
        if actual != max_schema:
            raise RuntimeError(f"Migration stopped at schema {actual}; expected {max_schema}.")
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Database integrity check failed after migration.")
        for key, value in (("minimum_app_version", minimum_app_version),
                           ("db_schema_version", str(max_schema))):
            conn.execute(
                "INSERT INTO app_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
            )
        conn.execute("DELETE FROM app_metadata WHERE key='maintenance_mode'")
        conn.commit()


def migrate_workspace(
    db_path,
    backup_dir,
    minimum_app_version,
    *,
    maintenance=False,
    confirm_clients_closed=False,
) -> dict:
    """Migrate once after explicit confirmation; retain maintenance on failure."""
    from config import APP_VERSION, DB_NAME

    if not maintenance or not confirm_clients_closed:
        raise ValueError(
            "Migration requires --maintenance and --confirm-clients-closed. Close every Nexus "
            "and CADViewer client first, including older releases; the maintenance flag "
            "cannot close them or prevent their writes."
        )
    requested_minimum = str(minimum_app_version).strip()
    if version_tuple(requested_minimum) > version_tuple(APP_VERSION):
        raise ValueError(f"The minimum application version cannot exceed this tool's version ({APP_VERSION}).")
    path = Path(db_path).expanduser().resolve()
    maximum = latest_schema_version()
    state = check_database(path, app_version=APP_VERSION, max_schema=maximum, allow_pending=True)
    if state["minimum_app_version"] and version_tuple(requested_minimum) < version_tuple(state["minimum_app_version"]):
        raise ValueError("The migration tool must not lower the workspace's existing minimum application version.")
    backup_path = None
    maintenance_set = False
    original_database = os.fspath(DB_NAME)
    with _migration_lock(path):
        state = check_database(path, app_version=APP_VERSION, max_schema=maximum, allow_pending=True)
        if state["minimum_app_version"] and version_tuple(requested_minimum) < version_tuple(state["minimum_app_version"]):
            raise ValueError("The migration tool must not lower the workspace's existing minimum application version.")
        try:
            _set_maintenance(path, f"Administrator migration in progress since {datetime.now(timezone.utc).isoformat()}")
            maintenance_set = True
            backup_path = _backup_database(path, backup_dir, state["schema"])
            print(f"Verified SQLite backup: {backup_path}", flush=True)
            DB_NAME.set(str(path))
            with temporarily_disabled_runtime_guard():
                _run_migrations()
            _finish_migration(path, minimum_app_version=requested_minimum, max_schema=maximum)
            result = check_database(path, app_version=APP_VERSION, max_schema=maximum)
        except Exception as exc:
            if maintenance_set:
                try:
                    _set_maintenance(path, "Migration failed. Administrator recovery is required before clients reopen the workspace.")
                except Exception:
                    pass
            backup_message = f"Verified backup preserved: {backup_path}." if backup_path else "No completed verified backup was created."
            raise RuntimeError(
                f"Migration failed: {exc}\n{backup_message}\n"
                "Maintenance mode remains enabled if it was set. Do not reopen clients or "
                "retry blindly. Review the error and recover from a verified backup with all "
                "clients closed; backups include the maintenance flag, which the administrator "
                "must clear only after recovery is validated. The database was not automatically restored."
            ) from exc
        finally:
            DB_NAME.set(original_database)
    result["backup"] = str(backup_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Existing shared creo_vcs.db")
    parser.add_argument("--backup-dir", type=Path, help="Directory for a new, verified SQLite backup")
    parser.add_argument("--minimum-app-version", help="Minimum compatible Nexus version after migration")
    parser.add_argument("--maintenance", action="store_true", help="Enable workspace maintenance during migration")
    parser.add_argument("--confirm-clients-closed", action="store_true", help="Confirm every Nexus/CADViewer client, including legacy clients, is closed")
    parser.add_argument("--check", action="store_true", help="Read-only inspection; do not back up, lock, or migrate")
    args = parser.parse_args(argv)
    from config import APP_VERSION

    try:
        if args.check:
            state = check_database(args.db, app_version=APP_VERSION, max_schema=latest_schema_version(), allow_pending=True)
            print(json.dumps(state, indent=2))
            return 0
        if args.backup_dir is None or not args.minimum_app_version:
            parser.error("--backup-dir and --minimum-app-version are required for migration")
        result = migrate_workspace(
            args.db, args.backup_dir, args.minimum_app_version,
            maintenance=args.maintenance, confirm_clients_closed=args.confirm_clients_closed,
        )
        print("MIGRATION SUCCESSFUL\n" + json.dumps(result, indent=2))
        return 0
    except (CompatibilityError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
