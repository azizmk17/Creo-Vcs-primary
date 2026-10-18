"""Exercise bundled Qt, PDF and CAD binaries without loading user data.

This diagnostic imports application definitions with SQLite connections blocked;
it never starts the application, resolves a workspace, or runs migrations.
Test documents and shapes live in a temporary directory, while the requested
result JSON is kept for build logs.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _check_pdf(directory: Path) -> dict:
    import fitz

    path = directory / "smoke.pdf"
    with fitz.open() as document:
        page = document.new_page(width=200, height=100)
        page.insert_text((12, 30), "Nexus portable PDF check")
        document.save(str(path))
    with fitz.open(str(path)) as document:
        page = document[0]
        pixmap = page.get_pixmap()
        if pixmap.width != 200 or pixmap.height != 100:
            raise RuntimeError("PDF rendering returned incorrect dimensions")
        if "Nexus portable PDF check" not in page.get_text():
            raise RuntimeError("PDF text could not be read back")
        if len(set(pixmap.samples)) < 2:
            raise RuntimeError("PDF rendered as an empty image")
    return {"module": fitz.__file__, "rendered_size": [200, 100]}


def _check_step(directory: Path) -> tuple[dict, Path]:
    import OCC
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.IFSelect import IFSelect_RetDone
    from OCC.Core.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from tools.CAD.step_diff_engine.step_parser import parse_step_file

    path = directory / "smoke.step"
    shape = BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()
    writer = STEPControl_Writer()
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError("OCC STEP transfer failed")
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError("OCC STEP writer failed")
    model = parse_step_file(path, commit_id="packaging-smoke")
    if not math.isclose(model.volume, 6000.0, rel_tol=1e-6) or len(model.faces) != 6:
        raise RuntimeError(f"Application STEP parser returned invalid box: {model.volume}, {len(model.faces)} faces")
    return {"module": OCC.__file__, "volume": model.volume, "faces": len(model.faces)}, path


def _check_qt_and_viewer(step_path: Path | None, require_opengl: bool) -> dict:
    from PyQt5.QtCore import QLibraryInfo
    from PyQt5.QtGui import QColor, QPainter, QPixmap
    from PyQt5.QtWidgets import QApplication, QLabel
    from tools.CAD.step_viewer import main_window

    app = QApplication.instance() or QApplication(["Nexus packaging smoke"])
    widget = QLabel("Portable Qt check")
    widget.resize(240, 60)
    pixmap = QPixmap(widget.size())
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    widget.render(painter)
    painter.end()
    if pixmap.isNull():
        raise RuntimeError("Qt widget rendering failed")
    result = {
        "platform": app.platformName(),
        "plugins": QLibraryInfo.location(QLibraryInfo.PluginsPath),
        "viewer_module": main_window.__file__,
        "opengl": "not requested",
    }
    if os.name == "nt" and app.platformName() != "windows":
        raise RuntimeError(f"Windows Qt platform plugin was not used: {app.platformName()}")
    if require_opengl:
        if step_path is None:
            raise RuntimeError("OpenGL check requires a successful STEP check")
        viewer = main_window.AdvancedCADViewer()
        try:
            viewer.resize(640, 480)
            viewer.show()
            viewer.load_and_show(str(step_path))
            app.processEvents()
            image_path = step_path.with_suffix(".png")
            viewer._display.ExportToImage(str(image_path))
            if not image_path.is_file() or image_path.stat().st_size < 100:
                raise RuntimeError("CAD viewport did not export a rendered image")
            result["opengl"] = "rendered STEP viewport"
        finally:
            viewer.close()
    widget.close()
    app.processEvents()
    return result


def _check_application_resources(modules: dict) -> dict:
    main = modules["main3"]
    worker = modules["core.integrations.creo_structure_worker"]
    resources = {
        relative: Path(main._resource_path(relative))
        for relative in ("modern_theme.qss", "assets/pictures/nexus_logo.ico", "assets/pictures/nexus_logo_glow.png")
    }
    resources.update({
        "creo_worker_source": Path(worker.__file__),
        "creo_async_java": worker._INTEGRATION / "async" / "NexusStructureScan.java",
        "creo_json_java": worker._INTEGRATION / "src" / "MiniJson.java",
    })
    for label, path in resources.items():
        if not path.is_file():
            raise RuntimeError(f"Required application resource is missing: {label}: {path}")
    if not modules["pages.pdf_viewer_widget"].HAS_FITZ:
        raise RuntimeError("Application PDF viewer could not load PyMuPDF")
    return {label: str(path) for label, path in resources.items()}


def _check_application_imports() -> dict:
    import importlib
    import sqlite3

    attempted_connections = []

    def reject_connection(*args, **kwargs):
        attempted_connections.append(True)
        raise RuntimeError("Database access is forbidden during packaging import checks")

    original_connect = sqlite3.connect
    original_dbapi_connect = sqlite3.dbapi2.connect
    sqlite3.connect = reject_connection
    sqlite3.dbapi2.connect = reject_connection
    try:
        modules = {}
        for name in (
            "main3", "pages.bom_page", "pages.commit_page", "setup.migrations",
            "pages.dashboard_page", "pages.issue_page", "pages.engineer_cli_page",
            "pages.pdf_viewer_widget", "core.integrations.creo_structure_worker",
        ):
            modules[name] = importlib.import_module(name)
        resources = _check_application_resources(modules)
        # Detect even import-time database access whose exception was swallowed.
        if attempted_connections:
            raise RuntimeError("Application import attempted to open a database")
        return {
            "modules": {name: module.__file__ for name, module in modules.items()},
            "resources": resources,
            "database_connections": "blocked during imports",
        }
    finally:
        sqlite3.connect = original_connect
        sqlite3.dbapi2.connect = original_dbapi_connect


def run_smoke(result_path: str | Path, *, require_opengl: bool = False) -> int:
    destination = Path(result_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": False,
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "bundle": str(getattr(sys, "_MEIPASS", "")),
        "checks": {},
    }
    # Use the explicitly writable report directory. Some Windows sandboxes
    # redirect TEMP to a location that native OCCT cannot open.
    with tempfile.TemporaryDirectory(prefix="nexus-packaging-smoke-", dir=destination.parent) as temporary:
        directory = Path(temporary)
        step_path = None
        checks = (
            ("pdf", lambda: _check_pdf(directory)),
            ("step", lambda: _check_step(directory)),
            ("qt_viewer", lambda: _check_qt_and_viewer(step_path, require_opengl)),
            ("application", _check_application_imports),
        )
        for name, check in checks:
            try:
                value = check()
                if name == "step":
                    value, step_path = value
                report["checks"][name] = {"ok": True, **value}
            except Exception:
                report["checks"][name] = {"ok": False, "error": traceback.format_exc()}
            # Keep useful results if a later native DLL crashes the process.
            report["ok"] = len(report["checks"]) == len(checks) and all(
                check["ok"] for check in report["checks"].values()
            )
            destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
