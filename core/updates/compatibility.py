"""Read-only compatibility checks performed before opening workspace services."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit


_RUNTIME_GUARD = None
_GUARD_THREAD_STATE = threading.local()


class CompatibilityError(RuntimeError):
    """The workspace cannot safely be opened by this application build."""

    def __init__(self, message: str, *, code: str = "incompatible"):
        super().__init__(message)
        self.code = code


def version_tuple(value: str) -> tuple[int, ...]:
    """Compare numeric release versions without lexicographic ordering."""
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", text):
        raise ValueError(f"Invalid application version: {value!r}. Use a numeric version such as 4.1.0.")
    return tuple(int(component) for component in text.split("."))



def readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open an existing database safely in read-only mode."""

    path = Path(db_path).expanduser().resolve()

    if not path.is_file():
        raise CompatibilityError(
            f"Workspace database does not exist: {path}",
            code="database_unavailable",
        )

    uri = path.as_uri()

    # Fix Windows UNC network paths for SQLite
    if str(path).startswith("\\\\") and uri.startswith("file://"):
        uri = "file:////" + uri[len("file://"):]

    connect = getattr(
        sqlite3,
        "_nexus_original_connect",
        sqlite3.connect,
    )

    return connect(uri + "?mode=ro", uri=True, timeout=5)



def latest_schema_version() -> int:
    """Return this build's supported schema without executing migrations."""
    from setup.migrations import MIGRATIONS

    return max(MIGRATIONS)


def _database_identity(database) -> str | None:
    value = os.fsdecode(os.fspath(database))
    if value == ":memory:":
        return None
    if value.startswith("file:"):
        uri = urlsplit(value)
        value = unquote(("//" + uri.netloc if uri.netloc else "") + uri.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", value):
            value = value[1:]
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def enable_runtime_guard(db_path, app_version: str, max_schema: int | None = None) -> None:
    """Protect subsequent connections to this workspace after the startup gate.

    Existing connections are not revoked. Repositories opening their next
    connection see current maintenance/minimum-version/schema metadata.
    """
    global _RUNTIME_GUARD
    maximum = latest_schema_version() if max_schema is None else int(max_schema)
    check_database(db_path, app_version=app_version, max_schema=maximum)
    _RUNTIME_GUARD = (_database_identity(db_path), app_version, maximum)


def disable_runtime_guard() -> None:
    """Clear this process's policy, for teardown or switching workspaces."""
    global _RUNTIME_GUARD
    _RUNTIME_GUARD = None


@contextmanager
def temporarily_disabled_runtime_guard():
    """Administrator-only exemption for this thread; other threads stay guarded."""
    previous = getattr(_GUARD_THREAD_STATE, "disabled", 0)
    _GUARD_THREAD_STATE.disabled = previous + 1
    try:
        yield
    finally:
        _GUARD_THREAD_STATE.disabled = previous


def guard_connection(conn: sqlite3.Connection, database) -> None:
    """Check the provided connection before the runtime's first write PRAGMA.

    No new connection is opened, so the global sqlite3.connect wrapper cannot
    recurse here. Other databases, backups, and memory fixtures are unaffected.
    The caller closes the connection and propagates a compatibility failure.
    """
    policy = _RUNTIME_GUARD
    if policy is None or getattr(_GUARD_THREAD_STATE, "disabled", 0):
        return
    if _database_identity(database) != policy[0]:
        return
    _check_connection(conn, app_version=policy[1], max_schema=policy[2])


def _check_connection(
    conn: sqlite3.Connection,
    *,
    app_version: str,
    max_schema: int,
    allow_pending: bool = False,
) -> dict:
    """Shared SELECT-only policy for startup and already-running clients."""
    try:
        current_app = version_tuple(app_version)
        supported_schema = int(max_schema)
        if supported_schema < 0:
            raise ValueError("The supported database schema must be non-negative.")
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('schema_migrations', 'app_metadata')"
            )
        }
        schema = 0
        if "schema_migrations" in tables:
            value = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            if value is not None and not re.fullmatch(r"[0-9]+", str(value)):
                raise ValueError("The database migration version is invalid.")
            schema = int(value) if value is not None else 0
            if schema < 0:
                raise ValueError("The database migration version is invalid.")
        metadata = {}
        if "app_metadata" in tables:
            metadata = dict(conn.execute(
                "SELECT key, value FROM app_metadata "
                "WHERE key IN ('minimum_app_version', 'maintenance_mode')"
            ))
        minimum = str(metadata.get("minimum_app_version") or "").strip()
        maintenance_message = str(metadata.get("maintenance_mode") or "").strip()
        maintenance = maintenance_message.lower() not in {
            "", "0", "false", "off", "no", "disabled",
        }
        state = {
            "schema": schema,
            "minimum_app_version": minimum or None,
            "needs_migration": schema < supported_schema,
            "maintenance": maintenance,
            "maintenance_message": maintenance_message if maintenance else "",
        }
        if maintenance:
            raise CompatibilityError(
                "This workspace is in maintenance mode. Close Nexus and contact your administrator "
                f"before continuing.\n\n{maintenance_message}", code="maintenance",
            )
        if schema > supported_schema:
            raise CompatibilityError(
                f"This database uses schema {schema}; this application supports schema "
                f"{supported_schema}. Install a compatible newer Nexus release before opening "
                "the workspace. The database has not been changed.", code="newer_schema",
            )
        if minimum and current_app < version_tuple(minimum):
            raise CompatibilityError(
                f"This workspace requires Nexus {minimum} or newer. You are running "
                f"{app_version}. Update Nexus before continuing. The database has not been changed.",
                code="minimum_version",
            )
        if state["needs_migration"] and not allow_pending:
            raise CompatibilityError(
                f"This workspace needs an administrator migration from schema {schema} to "
                f"{supported_schema}. Close all Nexus clients, back up the workspace, and ask "
                "the administrator to run tools/migrate_workspace.py from this release. "
                "Normal application startup does not migrate the shared database.",
                code="pending_migration",
            )
        return state
    except CompatibilityError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        raise CompatibilityError(
            f"Cannot safely inspect the workspace database: {exc}\n"
            "Contact your administrator. The database has not been changed.",
            code="database_unavailable",
        ) from exc


def check_database(
    db_path: str | Path,
    *,
    app_version: str,
    max_schema: int,
    allow_pending: bool = False,
) -> dict:
    """Inspect compatibility without creating, repairing, or writing a database.

    ``allow_pending`` is for the administrator's migration tool. Maintenance
    and newer-schema/minimum-version restrictions still apply to that tool.
    """
    try:
        with closing(readonly_connection(db_path)) as conn:
            return _check_connection(
                conn, app_version=app_version, max_schema=max_schema,
                allow_pending=allow_pending,
            )
    except CompatibilityError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        raise CompatibilityError(
            f"Cannot safely inspect the workspace database: {exc}\n"
            "Contact your administrator. The database has not been changed.",
            code="database_unavailable",
        ) from exc


def assert_workspace_compatible(db_path: str | Path, app_version: str | None = None) -> dict:
    """Startup entry point; pending migrations are deliberately refused."""
    if app_version is None:
        from config import APP_VERSION

        app_version = APP_VERSION
    return check_database(db_path, app_version=app_version, max_schema=latest_schema_version())
