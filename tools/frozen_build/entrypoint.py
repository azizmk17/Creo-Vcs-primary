"""Shared frozen entry point for Nexus.exe and CADViewer.exe.

Keep imports here limited to the standard library until dispatch. In particular,
the packaging diagnostic must not initialize a workspace, database, or license.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    if "--packaging-smoke" in sys.argv[1:]:
        index = sys.argv.index("--packaging-smoke")
        if index + 1 >= len(sys.argv) or sys.argv[index + 1].startswith("--"):
            raise SystemExit("--packaging-smoke requires a result JSON path")
        from tools.frozen_build.smoke import run_smoke

        return run_smoke(
            sys.argv[index + 1], require_opengl="--smoke-opengl" in sys.argv[1:]
        )
    if Path(sys.executable).stem.casefold() == "cadviewer":
        from tools.CAD.step_viewer.main_window import main as viewer_main

        return viewer_main()
    runpy.run_module("main3", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
