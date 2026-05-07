import os
from pathlib import Path

# Application version — bump this string with each release.
APP_VERSION = "2.0.0"

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
