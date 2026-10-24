"""Shared frozen entry point for Nexus.exe, CADViewer.exe and Setup.exe.

Keep imports here limited to the standard library until dispatch. In particular,
the packaging diagnostic must not initialize a workspace, database, or license.
"""
from __future__ import annotations

import runpy
import os
import sys
from pathlib import Path


def main() -> int:
    from core.startup_profile import mark
    mark("frozen_entry")
    if Path(sys.executable).stem.casefold() == "setup":
        from core.updates.trust import release_public_key
        public = release_public_key()
        if "--setup-self-test" in sys.argv[1:]:
            index = sys.argv.index("--setup-self-test")
            if index + 1 >= len(sys.argv) or sys.argv[index + 1].startswith("--"):
                raise SystemExit("--setup-self-test requires a result JSON path")
            from tools.frozen_build.setup_smoke import run_setup_smoke
            return run_setup_smoke(sys.argv[index + 1], public)
        from core.updates.setup_wizard import run_setup
        from core.updates.settings import read_settings
        from core.updates.install import installed_root_for_executable
        return run_setup(
            Path(sys.executable).resolve().parent, public,
            root=installed_root_for_executable(),
            feed=str(read_settings().get("feed") or ""),
        )
    if "--performance-benchmark" in sys.argv[1:]:
        if os.environ.get("NEXUS_ENABLE_PERF_BENCHMARK") != "1":
            raise SystemExit(
                "Performance benchmark mode is disabled. Set "
                "NEXUS_ENABLE_PERF_BENCHMARK=1 for an isolated benchmark run."
            )
        index = sys.argv.index("--performance-benchmark")
        sys.argv = [sys.argv[0], *sys.argv[index + 1:]]
        from tools.benchmark_project_loading import main as benchmark_main
        benchmark_main()
        return 0
    if "--packaging-smoke" in sys.argv[1:]:
        index = sys.argv.index("--packaging-smoke")
        if index + 1 >= len(sys.argv) or sys.argv[index + 1].startswith("--"):
            raise SystemExit("--packaging-smoke requires a result JSON path")
        from tools.frozen_build.smoke import run_smoke

        return run_smoke(
            sys.argv[index + 1], require_opengl="--smoke-opengl" in sys.argv[1:]
        )
    from contextlib import nullcontext
    from core.updates.install import acquire_run_lease, installed_root_for_executable
    root = installed_root_for_executable()
    if root:
        os.environ["NEXUS_INSTALL_ROOT"] = str(root)
    # Portable builds never take an unrelated managed installation's lease.
    else:
        os.environ.pop("NEXUS_INSTALL_ROOT", None)
    with acquire_run_lease(root) if root else nullcontext():
        if Path(sys.executable).stem.casefold() == "cadviewer":
            import argparse
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("--db-path")
            viewer_arguments, _ = parser.parse_known_args(sys.argv[1:])
            if viewer_arguments.db_path:
                from core.updates.compatibility import assert_workspace_compatible, enable_runtime_guard
                from config import APP_VERSION
                database = viewer_arguments.db_path
                assert_workspace_compatible(database, APP_VERSION)
                enable_runtime_guard(database, APP_VERSION)
            from tools.CAD.step_viewer.main_window import main as viewer_main
            return viewer_main()
        runpy.run_module("main3", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
