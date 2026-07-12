"""core/workspace_config.py
--------------------------
Startup workspace resolver for Nexus.

Responsibilities
----------------
1. Load  %APPDATA%/creovcs/config.json on startup.
2. Read  the ``last_workspace_path`` key.
3. Validate the workspace: the folder must exist and contain ``creo_vcs.db``.
4. Silently succeed if the workspace is valid.
5. Prompt the user with a folder-picker dialog when the path is missing or
   the database is absent.
6. After a successful selection, persist the new path to config.json.
7. Return a :class:`WorkspaceResult` on success, or ``None`` when the user
   cancels or an unrecoverable error occurs.

No paths are hard-coded in this module.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants  (file-names only, never directory paths)
# ---------------------------------------------------------------------------
_DB_FILENAME: str = "creo_vcs.db"
_APP_DIRNAME: str = "creovcs"       # sub-folder inside %APPDATA%
_CONFIG_FILENAME: str = "config.json"
_CONFIG_KEY: str = "last_workspace_path"


# ---------------------------------------------------------------------------
# Public data class
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkspaceResult:
    """Returned by :func:`resolve_workspace` on success.

    Attributes
    ----------
    workspace_path:
        The shared folder that the user selected / previously configured.
    db_path:
        Full path to ``creo_vcs.db`` inside *workspace_path*.
    was_prompted:
        ``True`` when the dialog was shown (first-run or broken config),
        ``False`` when the saved path was valid and no dialog was needed.
    """

    workspace_path: Path
    db_path: Path
    was_prompted: bool = False


# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

def _config_file() -> Path:
    """Return the absolute path to the per-user config file.

    Derives the directory from the ``APPDATA`` environment variable so that
    no path is hard-coded.  Falls back to the user home directory on systems
    where ``APPDATA`` is not set.
    """
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / _APP_DIRNAME / _CONFIG_FILENAME


def load_config() -> dict:
    """Load and return the contents of the JSON config file.

    Returns an empty :class:`dict` if the file does not exist, cannot be
    read, or is not a valid JSON object.  Never raises.
    """
    cfg_path = _config_file()
    try:
        with cfg_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.warning(
                "workspace_config: %s is not a JSON object – ignoring.", cfg_path
            )
            return {}
        return data
    except FileNotFoundError:
        logger.debug("workspace_config: config file not found at %s.", cfg_path)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning(
            "workspace_config: config file is malformed (%s) – ignoring.", exc
        )
        return {}
    except OSError as exc:
        logger.warning("workspace_config: cannot read config file: %s", exc)
        return {}


def save_config(config_data: dict) -> bool:
    """Persist *config_data* to the JSON config file.

    Creates the parent directory if it does not exist.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the file could not be written.
        Never raises.
    """
    cfg_path = _config_file()
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg_path.open("w", encoding="utf-8") as fh:
            json.dump(config_data, fh, indent=2)
        logger.debug("workspace_config: config saved to %s.", cfg_path)
        return True
    except OSError as exc:
        logger.error("workspace_config: cannot write config file: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Workspace validation
# ---------------------------------------------------------------------------

def validate_workspace(path: "str | Path") -> bool:
    """Return ``True`` if *path* is a directory containing ``creo_vcs.db``.

    Safe to call with any value (``None``, empty string, non-existent path).
    Never raises.
    """
    try:
        p = Path(path)
        return p.is_dir() and (p / _DB_FILENAME).is_file()
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Dialog helper
# ---------------------------------------------------------------------------

def prompt_workspace(parent=None) -> Optional[Path]:
    """Show a folder-picker dialog and return the validated :class:`Path`.

    Requires a running :class:`~PyQt5.QtWidgets.QApplication`.
    *parent* may be ``None``.

    Returns
    -------
    Path
        The selected folder if it contains ``creo_vcs.db``.
    None
        When the user cancels the dialog or selects a folder that does not
        contain the database.
    """
    try:
        from PyQt5.QtWidgets import QFileDialog, QMessageBox
    except ImportError:
        logger.error(
            "workspace_config: PyQt5 is not available – cannot show dialog."
        )
        return None

    selected = QFileDialog.getExistingDirectory(
        parent,
        f"Select Workspace Folder  (must contain {_DB_FILENAME})",
        str(Path.home()),
        QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
    )

    if not selected:
        logger.info("workspace_config: user cancelled folder selection.")
        return None

    chosen = Path(selected)
    if not validate_workspace(chosen):
        QMessageBox.warning(
            parent,
            "Invalid Workspace",
            f"The selected folder does not contain '{_DB_FILENAME}'.\n\n"
            f"Path: {chosen}\n\n"
            "Please choose a folder that already contains the Nexus database.",
        )
        logger.warning(
            "workspace_config: selected folder '%s' does not contain %s.",
            chosen,
            _DB_FILENAME,
        )
        return None

    return chosen


# ---------------------------------------------------------------------------
# Primary public API
# ---------------------------------------------------------------------------

def resolve_workspace(parent=None) -> Optional[WorkspaceResult]:
    """Resolve the workspace at application startup.

    Algorithm
    ---------
    1. Load ``config.json`` and read ``last_workspace_path``.
    2. Validate the saved path (directory must exist and contain the DB).
    3. If valid  → return :class:`WorkspaceResult` silently
       (``was_prompted=False``).
    4. If missing / invalid → log a warning and open the folder-picker dialog.
    5. After a successful selection → persist the chosen path and return
       :class:`WorkspaceResult` with ``was_prompted=True``.
    6. If the user cancels the dialog → return ``None``.

    Parameters
    ----------
    parent:
        Optional Qt widget used as the parent for any dialogs shown.

    Returns
    -------
    WorkspaceResult
        On success.
    None
        When no workspace could be resolved (user cancelled or no Qt context).
    """
    cfg = load_config()
    saved_path: str = cfg.get(_CONFIG_KEY, "")

    # --- Happy path: saved path is still valid ----------------------------
    if saved_path and validate_workspace(saved_path):
        ws = Path(saved_path)
        logger.info("workspace_config: workspace OK – %s", ws)
        return WorkspaceResult(
            workspace_path=ws,
            db_path=ws / _DB_FILENAME,
            was_prompted=False,
        )

    # --- Explain why we are prompting -------------------------------------
    if saved_path:
        logger.warning(
            "workspace_config: saved workspace '%s' is missing or invalid – "
            "prompting user.",
            saved_path,
        )
    else:
        logger.info(
            "workspace_config: no workspace configured – prompting user."
        )

    # --- Prompt the user --------------------------------------------------
    chosen = prompt_workspace(parent)
    if chosen is None:
        return None  # user cancelled

    # --- Persist the new selection ----------------------------------------
    cfg[_CONFIG_KEY] = str(chosen)
    if not save_config(cfg):
        logger.error(
            "workspace_config: failed to save new workspace path – "
            "the user will be prompted again on next launch."
        )

    return WorkspaceResult(
        workspace_path=chosen,
        db_path=chosen / _DB_FILENAME,
        was_prompted=True,
    )
