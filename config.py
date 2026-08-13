import os
import sqlite3
from pathlib import Path

# Application version — bump this string with each release.
APP_VERSION = "3.0.0"

# ---------------------------------------------------------------------------
# Database path
# ---------------------------------------------------------------------------
# Only the file-name is a constant; the containing directory is resolved
# at startup by core.workspace_config.resolve_workspace() and injected via
# DB_NAME.set().
#
# _DBPath implements os.PathLike, so sqlite3.connect(DB_NAME) and
# pathlib.Path(DB_NAME) both work without any changes to existing
# repositories or services — they always use the current resolved path.
# ---------------------------------------------------------------------------

_DB_FILENAME = "creo_vcs.db"   # The only hard-coded constant: just the name.


class _DBPath(os.PathLike):
    """Mutable, path-like proxy for the database location.

    All repositories receive a reference to this object as their ``db_name``
    default argument.  When :meth:`set` is called once at startup, every
    subsequent ``sqlite3.connect(self.db_name)`` call transparently uses the
    new path — no repository code needs to change.

    Protocol support
    ----------------
    ``__fspath__``  → accepted by ``sqlite3.connect`` and ``pathlib.Path``
    ``__str__``     → safe for string formatting and ``os.path.*``
    ``parent``      → mirrors ``pathlib.Path.parent``
    ``__truediv__`` → supports ``DB_NAME / "subpath"`` idiom
    """

    def __init__(self, value: str) -> None:
        self._value = value

    # os.PathLike protocol -------------------------------------------------
    def __fspath__(self) -> str:
        return self._value

    # String representation ------------------------------------------------
    def __str__(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"_DBPath({self._value!r})"

    # Path helpers ---------------------------------------------------------
    @property
    def parent(self) -> Path:
        """Equivalent to ``pathlib.Path(DB_NAME).parent``."""
        return Path(self._value).parent

    def __truediv__(self, other) -> Path:
        """Support ``DB_NAME / "sibling"`` path construction."""
        return Path(self._value) / other

    # Mutation  (called once at startup) -----------------------------------
    def set(self, value: str) -> None:
        """Update the database path.

        Called exactly once by the workspace resolver before any repository
        is instantiated.  Thread-safety is not required since this happens
        synchronously during application startup.
        """
        self._value = value


# Singleton proxy — the same object is shared by all importers.
# Initially points to the bare filename so that code that imports DB_NAME
# before startup completes still has a usable (relative-path) fallback.
DB_NAME = _DBPath(_DB_FILENAME)

# Workspace root (reserved for future CAD file management if needed)
WORKSPACE_ROOT = Path("creo_vcs_workspace")


# ---------------------------------------------------------------------------
# Shared SQLite runtime hardening
# ---------------------------------------------------------------------------
# Nexus is commonly deployed with ``creo_vcs.db`` in a shared folder. SQLite can
# support this style for a small team when every process uses short-lived
# connections, waits for the single writer instead of failing immediately, and
# keeps durable rollback-journal semantics.  This wrapper is intentionally
# installed at config import time so existing repository code that still calls
# ``sqlite3.connect(...)`` inherits the same safe profile without a risky
# rewrite of every repository method.
#
# Defaults are conservative for a shared Windows/SMB folder:
#   - journal_mode=DELETE: network-share compatible rollback journal
#   - synchronous=FULL: strongest practical durability for file shares
#   - busy_timeout=60000: wait up to 60s for another user's short write
#   - foreign_keys=ON: enforce relational integrity per connection
#   - locking_mode=NORMAL: never hold an exclusive DB reservation
#
# For a local-only single-machine deployment, an admin may set
# NEXUS_SQLITE_JOURNAL=WAL for faster concurrent read/write behavior.
# Do not use WAL on ordinary network shares unless the storage is explicitly
# certified for SQLite WAL shared-memory semantics.
# ---------------------------------------------------------------------------

SQLITE_BUSY_TIMEOUT_MS = int(os.environ.get("NEXUS_SQLITE_BUSY_TIMEOUT_MS", "60000"))
SQLITE_TIMEOUT_SECONDS = max(1.0, SQLITE_BUSY_TIMEOUT_MS / 1000.0)
SQLITE_JOURNAL_MODE = os.environ.get("NEXUS_SQLITE_JOURNAL", "DELETE").strip().upper() or "DELETE"
SQLITE_SYNCHRONOUS = os.environ.get("NEXUS_SQLITE_SYNCHRONOUS", "FULL").strip().upper() or "FULL"
_SQLITE_JOURNAL_CONFIGURED_PATHS: set[str] = set()


def _install_sqlite_runtime_profile() -> None:
    if getattr(sqlite3, "_nexus_runtime_profile_installed", False):
        return
    original_connect = getattr(sqlite3, "_nexus_original_connect", sqlite3.connect)
    sqlite3._nexus_original_connect = original_connect

    def nexus_connect(database, *args, **kwargs):
        kwargs.setdefault("timeout", SQLITE_TIMEOUT_SECONDS)
        conn = original_connect(database, *args, **kwargs)
        _configure_sqlite_connection(conn, database)
        return conn

    sqlite3.connect = nexus_connect
    sqlite3._nexus_runtime_profile_installed = True


def _configure_sqlite_connection(conn, database=None) -> None:
    """Apply per-connection safety settings.

    SQLite PRAGMAs are deliberately best-effort here: a diagnostic/backup
    helper should not fail simply because one optional tuning pragma is not
    supported by an older SQLite build. Foreign-key and timeout settings are
    per-connection, so they are applied every time.
    """
    pragmas = [
        f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}",
        "PRAGMA foreign_keys=ON",
        "PRAGMA locking_mode=NORMAL",
        "PRAGMA temp_store=MEMORY",
        f"PRAGMA synchronous={SQLITE_SYNCHRONOUS}",
    ]
    db_key = str(os.fspath(database)) if database is not None else ""
    should_configure_journal = (
        bool(SQLITE_JOURNAL_MODE)
        and db_key
        and db_key not in {":memory:", ""}
        and db_key not in _SQLITE_JOURNAL_CONFIGURED_PATHS
    )
    if should_configure_journal:
        pragmas.insert(0, f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
    for pragma in pragmas:
        try:
            conn.execute(pragma)
            if pragma.startswith("PRAGMA journal_mode=") and db_key:
                _SQLITE_JOURNAL_CONFIGURED_PATHS.add(db_key)
        except Exception:
            pass


_install_sqlite_runtime_profile()
