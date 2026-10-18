"""Build the portable Nexus/CADViewer folder using one Python environment."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "folder"
RELEASE = ROOT / "dist" / "Nexus"


def workspace_path(path):
    """Resolve before any removal; never clean outside this checkout."""
    resolved = Path(path).resolve()
    root = ROOT.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError(f"Build path is outside this checkout: {resolved}")
    return resolved


def app_version():
    tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise ValueError("APP_VERSION is missing from config.py")


def checked_run(command, log_path, *, env=None, cwd=ROOT, timeout=None):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[run] {subprocess.list2cmdline([str(arg) for arg in command])}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(arg) for arg in command], cwd=str(cwd), env=env,
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError(f"Command timed out. Log: {log_path}") from None
    if code:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-35:]
        print("\n".join(tail), flush=True)
        raise RuntimeError(f"Command failed ({code}). Full log: {log_path}")


def check_environment():
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Use 64-bit Windows Python with pythonocc-core installed.")
    modules = ["PyInstaller", "PyQt5.QtWidgets", "PyQt5.QtOpenGL", "cryptography", "openpyxl",
               "OCC.Core.STEPControl", "OCC.Core.BRepPrimAPI", "OCC.Display.backend"]
    missing = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing.append(f"{name}: {exc}")
    if not any(message.startswith("OCC.Display.backend:") for message in missing):
        try:
            from OCC.Display.backend import load_backend
            load_backend("pyqt5")
            importlib.import_module("OCC.Display.qtDisplay")
        except Exception as exc:
            missing.append(f"OCC.Display.qtDisplay: {exc}")
    pdf_name = None
    for name in ("pymupdf", "fitz"):
        try:
            module = importlib.import_module(name)
            if not hasattr(module, "open"):
                raise ImportError("not the PyMuPDF package")
            pdf_name = name
            break
        except Exception:
            continue
    if pdf_name is None:
        missing.append("PyMuPDF (import fitz or pymupdf)")
    if missing:
        raise RuntimeError(
            "Build environment is incomplete:\n  " + "\n  ".join(missing)
            + "\nUse build_exe.bat --python C:\\path\\to\\pyoccenv\\python.exe."
            + "\nInstall pythonocc-core through Conda; it is not supplied by requirements.txt."
        )
    import PyInstaller
    if int(PyInstaller.__version__.split(".")[0]) < 6:
        raise RuntimeError("PyInstaller 6 or newer is required. Use --install-build-tools.")
    print(f"Python: {sys.executable}\nVersion: {sys.version.splitlines()[0]}\nPDF module: {pdf_name}", flush=True)


def write_version_file(path, version):
    numbers = tuple(int(part) for part in version.split("."))
    numbers = (numbers + (0, 0, 0, 0))[:4]
    path.write_text(
        "VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(numbers) + ", prodvers=" + repr(numbers)
        + ", mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)), "
        + "kids=[StringFileInfo([StringTable('040904B0', [StringStruct('ProductName', 'Nexus'), "
        + f"StringStruct('FileVersion', {version!r}), StringStruct('ProductVersion', {version!r})"
        + "])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])", encoding="utf-8",
    )


def smoke_environment():
    """Do not let developer Python, Conda, Qt, or OCCT paths rescue a broken bundle."""
    prefixes = ("CONDA", "PYTHON", "QT_", "QML", "CSF_", "OCCT_", "CASROOT", "PYINSTALLER")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(prefixes)}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = os.pathsep.join([str(system_root / "System32"), str(system_root)])
    env["QT_QPA_PLATFORM"] = "windows"
    return env


def test_release(*, opengl=False):
    reports = {}
    for executable in ("Nexus.exe", "CADViewer.exe"):
        report = BUILD / "logs" / (Path(executable).stem + "-smoke.json")
        report.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="nexus-build-smoke-", dir=BUILD) as directory:
            args = [RELEASE / executable, "--packaging-smoke", report]
            if opengl:
                args.append("--smoke-opengl")
            checked_run(args, BUILD / "logs" / (Path(executable).stem + "-smoke.log"),
                        env=smoke_environment(), cwd=Path(directory), timeout=180)
        result = json.loads(report.read_text(encoding="utf-8"))
        if not result.get("ok") or not result.get("frozen"):
            raise RuntimeError(f"Packaged runtime smoke test failed: {report}")
        checks = result.get("checks", {})
        if not all(checks.get(name, {}).get("ok") for name in ("pdf", "step", "qt_viewer", "application")):
            raise RuntimeError(f"Packaged runtime checks are incomplete: {report}")
        if Path(result.get("bundle", "")).resolve() != (RELEASE / "_internal").resolve():
            raise RuntimeError(f"Runtime check loaded an unexpected bundle: {report}")
        reports[executable] = result
    return reports


def release_manifest(version, smoke_reports):
    files = [path for path in RELEASE.rglob("*") if path.is_file()]
    unsafe = [path for path in files if path.suffix.lower() in {".db", ".lic", ".key"}
              or path.name.lower().startswith("trail.txt")]
    if unsafe:
        raise RuntimeError(f"Unexpected workspace files in release: {unsafe}")
    for required in ("Nexus.exe", "CADViewer.exe", "_internal/occ-resources/Shaders",
                     "_internal/assets/pictures", "_internal/modern_theme.qss"):
        if not (RELEASE / required).exists():
            raise RuntimeError(f"Required release file is missing: {required}")
    if not list((RELEASE / "_internal").rglob("qwindows.dll")):
        raise RuntimeError("Qt Windows platform plugin is missing from release.")
    versions = {}
    for package in ("pyinstaller", "PyQt5", "PyMuPDF", "cryptography", "openpyxl"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "unknown"
    import OCC
    versions["pythonocc-core"] = getattr(OCC, "VERSION", "unknown")
    manifest = {
        "app": "Nexus", "version": version, "built_at": datetime.now(timezone.utc).isoformat(),
        "mode": "onedir", "architecture": platform.machine(), "python": platform.python_version(),
        "packages": versions, "executables": ["Nexus.exe", "CADViewer.exe"],
        "shared_runtime": "_internal", "file_count": len(files),
        "size_mb": round(sum(path.stat().st_size for path in files) / 1024 ** 2, 1),
        "smoke_tests": smoke_reports,
    }
    (RELEASE / "build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", help="Build with this pyoccenv python.exe")
    parser.add_argument("--check", action="store_true", help="Check prerequisites without building")
    parser.add_argument("--clean", action="store_true", help="Clear only this builder's intermediate files")
    parser.add_argument("--install-build-tools", action="store_true", help="Install PyInstaller 6 in the selected environment")
    parser.add_argument("--no-smoke-test", action="store_true", help="Skip packaged PDF/STEP/Qt checks")
    parser.add_argument("--smoke-opengl", action="store_true", help="Also exercise a real OCC OpenGL viewport")
    args = parser.parse_args(argv)
    if args.python:
        requested = Path(args.python).resolve()
        if not requested.is_file():
            parser.error(f"Python executable not found: {requested}")
        if requested != Path(sys.executable).resolve():
            original = list(sys.argv[1:] if argv is None else argv)
            return subprocess.call([str(requested), str(Path(__file__).resolve()), *original], cwd=str(ROOT))
    workspace_path(BUILD)
    workspace_path(RELEASE)
    if args.clean and BUILD.exists():
        shutil.rmtree(workspace_path(BUILD))
    (BUILD / "logs").mkdir(parents=True, exist_ok=True)
    if args.install_build_tools:
        checked_run([sys.executable, "-m", "pip", "install", "PyInstaller>=6,<7"], BUILD / "logs" / "install.log")
    check_environment()
    sys.path.insert(0, str(ROOT))
    from tools.frozen_build.dependencies import collect_runtime_dependencies, native_library_search_paths
    datas, binaries, hidden = collect_runtime_dependencies()
    print(f"Runtime seeds: {len(binaries)} binaries, {len(datas)} data entries, {len(hidden)} imports", flush=True)
    if args.check:
        print("Build prerequisites OK. No executables were built.", flush=True)
        return 0
    version = app_version()
    version_file = BUILD / "version_info.txt"
    write_version_file(version_file, version)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    env["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "cache")
    env["NEXUS_VERSION_FILE"] = str(version_file)
    env["PATH"] = os.pathsep.join(native_library_search_paths() + [env.get("PATH", "")])
    print("Building Nexus and CADViewer; native DLLs are shared. See build/folder/logs/pyinstaller.log for progress.", flush=True)
    checked_run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                 "--distpath", ROOT / "dist", "--workpath", BUILD / "pyinstaller",
                 ROOT / "tools" / "frozen_build" / "nexus_folder.spec"], BUILD / "logs" / "pyinstaller.log", env=env)
    reports = {} if args.no_smoke_test else test_release(opengl=args.smoke_opengl)
    manifest = release_manifest(version, reports)
    print(f"\nBUILD SUCCESSFUL: Nexus {version}\n{RELEASE / 'Nexus.exe'}\n"
          f"Distribute the entire folder ({manifest['size_mb']} MB).\nLogs: {BUILD / 'logs'}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
