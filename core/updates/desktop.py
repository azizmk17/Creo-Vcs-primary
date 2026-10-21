"""Small frozen launcher/updater command line; imports no application pages."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import install


def _message(title: str, text: str, *, error=False):
    if not getattr(sys, "frozen", False):
        print(f"{title}: {text}", file=sys.stderr if error else sys.stdout)
        return
    from PyQt5.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication(["Nexus installer"])
    if error:
        QMessageBox.critical(None, title, text)
    else:
        QMessageBox.information(None, title, text)


def _wait_for_process(pid: int, timeout: float):
    """Wait on the original Windows process handle; never terminate it."""
    if pid <= 0 or pid == os.getpid():
        raise install.InstallError("Invalid process to wait for")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Process has already exited.
                return
            raise install.InstallError("Cannot wait for the original Nexus process. Close it and activate again.")
        try:
            if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise install.InstallError("Nexus is still running. Close it before activating the update.")
        finally:
            kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    raise install.InstallError("Nexus is still running. Close it before activating the update.")


def _saved_database():
    # Reading configuration does not resolve, open or modify a workspace.
    from core.workspace_config import load_config
    workspace = load_config().get("last_workspace_path")
    return Path(workspace) / "creo_vcs.db" if workspace else None


def _launch(root: Path):
    executable = install.current_application(root)
    database = _saved_database()
    if database and database.is_file():
        from .compatibility import check_database
        current = install._read_json(root / "current.json")
        check_database(database, app_version=current["version"], max_schema=current["supported_schema"]["max"])
    environment = os.environ.copy()
    environment["NEXUS_INSTALL_ROOT"] = str(root)
    # Main application runtime hooks choose their own bundled Qt/OCCT paths.
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(key, None)
    return subprocess.Popen([str(executable)], cwd=str(executable.parent), env=environment)


def _run_current(root: Path) -> int:
    # The launcher's lease spans child startup and its complete lifetime. This
    # closes the gap before the new child's own runtime lease is acquired.
    with install.acquire_run_lease(root):
        return _launch(root).wait()


def _wait_for_leases(root: Path, timeout: float):
    deadline = time.monotonic() + timeout
    while install.live_processes(root):
        if time.monotonic() >= deadline:
            raise install.InstallError("Nexus or CADViewer is still running. Close its windows and activate again. No process was stopped.")
        time.sleep(0.2)


def main(role: str, public_key_hex: str, current_version: str) -> int:
    parser = argparse.ArgumentParser(description="Nexus versioned local launcher and updater")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--feed")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--activate-staged", action="store_true")
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--wait-timeout", type=float, default=120)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--schema-version", type=int)
    parser.add_argument("--allow-rollback", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Suppress dialogs for administrator automation")
    parser.add_argument("--result-json", type=Path, help="Write the action result as JSON")
    args = parser.parse_args()
    inferred = None if args.install else install.installed_root_for_executable()
    root = Path(args.root or inferred or install.default_install_root()).resolve()
    os.environ["NEXUS_INSTALL_ROOT"] = str(root)
    action = "install" if args.install else "stage" if args.stage else "activate" if args.activate_staged else "launch"

    def record(ok, *, result=None, error=None):
        outcome = {"ok": ok, "action": action, "root": str(root)}
        if result is not None:
            outcome["result"] = result
            if "version" in result:
                outcome["version"] = result["version"]
        if error is not None:
            outcome["error"] = str(error)
        if args.result_json:
            install._atomic_json(args.result_json.resolve(), outcome)

    def respond(title, text, *, error=False, result=None):
        record(not error, result=result, error=text if error else None)
        if not args.quiet:
            _message(title, text, error=error)

    try:
        if sum((args.install, args.stage, args.activate_staged)) > 1:
            raise install.InstallError("Choose only one installer action")
        if args.install:
            source = args.source
            if source is None:
                folder = Path(sys.executable).resolve().parent
                source = folder.parent if folder.name.casefold() == "bootstrap" else folder
            result = install.initial_install(source, root, args.feed or "", public_key_hex)
            respond("Nexus installed", f"Nexus {result['version']} is installed.\n\nStart Nexus using:\n{root / 'bootstrap' / 'NexusLauncher.exe'}", result=result)
            if args.launch:
                return _run_current(root)
            return 0
        if args.stage:
            feed = args.feed or install._read_json(root / "install.json").get("feed")
            if not feed:
                raise install.InstallError("Choose a company update feed before checking for updates")
            result = install.stage_release(feed, public_key_hex, root, db_path=args.database, allow_rollback=args.allow_rollback)
            respond("Nexus update ready", f"Nexus {result['version']} is staged. Close Nexus and CADViewer, then activate the update.", result=result)
            return 0
        if args.activate_staged:
            if args.wait_pid:
                _wait_for_process(args.wait_pid, min(max(args.wait_timeout, 1), 600))
            _wait_for_leases(root, min(max(args.wait_timeout, 1), 600))
            database = args.database or _saved_database()
            result = install.activate_staged(root, public_key_hex, schema_version=args.schema_version, db_path=database, allow_rollback=args.allow_rollback)
            if args.launch:
                record(True, result=result)
                return _run_current(root)
            else:
                respond("Nexus updated", "The verified update is active. Start Nexus using the Nexus launcher.", result=result)
            return 0
        if role != "launcher":
            raise install.InstallError("Choose --stage or --activate-staged")
        code = _run_current(root)
        record(code == 0, error=f"Nexus exited with code {code}" if code else None)
        return code
    except Exception as exc:
        record(False, error=exc)
        from .compatibility import CompatibilityError
        if isinstance(exc, CompatibilityError):
            if args.quiet:
                return 1
            from PyQt5.QtWidgets import QApplication
            from .ui import show_compatibility_error
            app = QApplication.instance() or QApplication(["Nexus launcher"])
            current = install._read_json(root / "current.json")
            show_compatibility_error(str(exc), current.get("version", current_version), args.database or _saved_database())
            return 1
        if not args.quiet:
            _message("Nexus installer", str(exc), error=True)
        return 1
