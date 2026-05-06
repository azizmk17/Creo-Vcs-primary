"""
cad_viewer_launcher.py
=======================
Central helper to launch the Advanced CAD Viewer as a subprocess
(running in the ``pyoccenv`` conda environment).

Usage from any page::

    from tools.CAD.step_viewer.launcher import launch_viewer, launch_diff_viewer

    launch_viewer(step_path)
    launch_diff_viewer(path_a, path_b, commit_a="c1", commit_b="c2")
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional


def _repo_root() -> str:
    """Return the working directory for the CAD viewer subprocess.

    - Dev / source run: the project root (three levels above this file,
      i.e. the directory that contains the ``tools/`` package).
    - PyInstaller EXE: the folder that contains the EXE.  The viewer
      must be installed inside the pyoccenv conda environment so that
      ``python -m tools.CAD.step_viewer`` resolves via site-packages,
      not via cwd.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # Running inside a PyInstaller bundle — use the EXE's own directory.
        return os.path.dirname(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    # tools/CAD/step_viewer  →  go up three levels
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def _conda_bat() -> Optional[str]:
    bat = os.path.join(os.path.expanduser("~"), "miniconda3", "condabin", "conda.bat")
    if os.path.exists(bat):
        return bat
    return None


def _build_cmd(module_args: list[str]) -> list[str]:
    """Build the full command list.

    Priority order:
    1. When running from a PyInstaller EXE: look for CADViewer.exe in the
       same folder as the main EXE.  No conda or pythonocc needed on the
       user's machine.
    2. Dev / source fallback: conda run -n pyoccenv python -m tools.CAD.step_viewer
    3. Last resort: sys.executable (only works if pythonocc is in the active venv)
    """
    if getattr(sys, "frozen", False):
        viewer_exe = os.path.join(os.path.dirname(sys.executable), "CADViewer.exe")
        if os.path.isfile(viewer_exe):
            return [viewer_exe] + module_args

    conda = _conda_bat()
    if conda:
        return [conda, "run", "-n", "pyoccenv", "python", "-m",
                "tools.CAD.step_viewer"] + module_args
    return [sys.executable, "-m", "tools.CAD.step_viewer"] + module_args


def launch_viewer(
    filepath: str,
    *,
    part_id: int | str | None = None,
    project_id: int | str | None = None,
    db_path: str | None = None,
    face_map_path: str | None = None,
) -> subprocess.Popen:
    """Open a single CAD file in the advanced viewer (subprocess).

    If *part_id* and *project_id* are provided the viewer enables
    face‑lifecycle tracking (which commits added / modified / deleted
    each geometric face).
    """
    args = [filepath]
    if part_id is not None:
        args += ["--part-id", str(part_id)]
    if project_id is not None:
        args += ["--project-id", str(project_id)]
    if db_path:
        args += ["--db-path", db_path]
    if face_map_path:
        args += ["--face-map-path", face_map_path]
    cmd = _build_cmd(args)
    return subprocess.Popen(cmd, cwd=_repo_root())


def launch_diff_viewer(
    path_a: str,
    path_b: str,
    commit_a: str = "",
    commit_b: str = "",
    common_transparency: float = 0.80,
) -> subprocess.Popen:
    """Open a two‑file diff overlay in the advanced viewer (subprocess)."""
    args: list[str] = [
        "--step-a", path_a,
        "--step-b", path_b,
    ]
    if commit_a:
        args += ["--commit-a", commit_a]
    if commit_b:
        args += ["--commit-b", commit_b]
    args += ["--common-transparency", str(common_transparency)]
    cmd = _build_cmd(args)
    return subprocess.Popen(cmd, cwd=_repo_root())
