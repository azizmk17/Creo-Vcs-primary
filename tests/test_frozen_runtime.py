import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.CAD.step_viewer import launcher
from tools.frozen_build import entrypoint, runtime_hook, smoke


class FrozenRuntimeTests(unittest.TestCase):
    def temporary_directory(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_frozen_resources_override_host_paths_and_keep_dll_handles(self):
        bundle = self.temporary_directory()
        for relative in ("occ-resources/Shaders", "occ-resources/StdResource", "Library/bin", "OCC/Core"):
            (bundle / relative).mkdir(parents=True)
        dll_handle = Mock()
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "_MEIPASS", str(bundle), create=True), \
                patch.object(sys, "_nexus_dll_directory_handles", [], create=True), \
                patch.dict(os.environ, {"CASROOT": "developer-only", "CSF_UnitsLexicon": "host-lexicon"}, clear=True), \
                patch.object(os, "add_dll_directory", return_value=dll_handle, create=True) as add_dll:
            runtime_hook.configure_frozen_runtime()
            self.assertEqual(os.environ["OCCT_ESSENTIALS_ROOT"], str(bundle))
            self.assertEqual(os.environ["CASROOT"], str(bundle / "occ-resources"))
            self.assertEqual(os.environ["CSF_ShadersDirectory"], str(bundle / "occ-resources/Shaders"))
            self.assertNotIn("CSF_UnitsLexicon", os.environ)
            self.assertTrue(sys._nexus_dll_directory_handles)
            for call in add_dll.call_args_list:
                self.assertTrue(Path(call.args[0]).resolve().is_relative_to(bundle))

    def test_source_runtime_leaves_environment_unchanged(self):
        with patch.object(sys, "frozen", False, create=True), patch.dict(os.environ, {"CASROOT": "source-root"}):
            runtime_hook.configure_frozen_runtime()
            self.assertEqual(os.environ["CASROOT"], "source-root")

    def test_smoke_dispatch_does_not_start_application_or_viewer(self):
        with patch.object(sys, "argv", ["Nexus.exe", "--packaging-smoke", "result.json"]), \
                patch.object(smoke, "run_smoke", return_value=0) as run, \
                patch.object(entrypoint.runpy, "run_module") as run_module:
            self.assertEqual(entrypoint.main(), 0)
        run.assert_called_once_with("result.json", require_opengl=False)
        run_module.assert_not_called()

    def test_cad_viewer_executable_dispatches_viewer_not_main_app(self):
        viewer = SimpleNamespace(main=Mock(return_value=7))
        with patch.object(sys, "argv", ["CADViewer.exe", "part.step"]), \
                patch.object(sys, "executable", str(Path("CADViewer.exe").resolve())), \
                patch.dict(sys.modules, {"tools.CAD.step_viewer.main_window": viewer}), \
                patch.object(entrypoint.runpy, "run_module") as run_module:
            self.assertEqual(entrypoint.main(), 7)
        viewer.main.assert_called_once_with()
        run_module.assert_not_called()

    def test_nexus_dispatch_keeps_normal_main_startup(self):
        with patch.object(sys, "argv", ["Nexus.exe"]), \
                patch.object(sys, "executable", str(Path("Nexus.exe").resolve())), \
                patch.object(entrypoint.runpy, "run_module") as run_module:
            self.assertEqual(entrypoint.main(), 0)
        run_module.assert_called_once_with("main3", run_name="__main__")

    def test_smoke_records_failures_and_continues_other_checks(self):
        report = self.temporary_directory() / "smoke.json"
        with patch.object(smoke, "_check_pdf", side_effect=ImportError("missing PDF DLL")), \
                patch.object(smoke, "_check_step", return_value=({"volume": 6000}, Path("temporary.step"))), \
                patch.object(smoke, "_check_qt_and_viewer", return_value={"platform": "windows"}) as qt, \
                patch.object(smoke, "_check_application_imports", return_value={"modules": {}}) as application:
            self.assertEqual(smoke.run_smoke(report), 1)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertIn("missing PDF DLL", result["checks"]["pdf"]["error"])
        self.assertTrue(result["checks"]["step"]["ok"])
        self.assertTrue(result["checks"]["application"]["ok"])
        qt.assert_called_once_with(Path("temporary.step"), False)
        application.assert_called_once_with()

    def test_application_import_check_loads_startup_pages_without_database_access(self):
        original_connect = sqlite3.connect
        with patch("importlib.import_module", side_effect=lambda name: SimpleNamespace(__file__=name)) as imports, \
                patch.object(smoke, "_check_application_resources", return_value={}):
            result = smoke._check_application_imports()
        self.assertEqual(list(result["modules"])[:4], ["main3", "pages.bom_page", "pages.commit_page", "setup.migrations"])
        self.assertEqual(imports.call_count, 9)
        self.assertIs(sqlite3.connect, original_connect)

    def test_application_resource_check_detects_missing_worker_physical_source(self):
        root = self.temporary_directory()
        names = (
            "modern_theme.qss", "assets/pictures/nexus_logo.ico", "assets/pictures/nexus_logo_glow.png",
            "creo/async/NexusStructureScan.java", "creo/src/MiniJson.java",
        )
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"resource")
        modules = {
            "main3": SimpleNamespace(_resource_path=lambda relative: str(root / relative)),
            "core.integrations.creo_structure_worker": SimpleNamespace(__file__=str(root / "worker.pyc"), _INTEGRATION=root / "creo"),
            "pages.pdf_viewer_widget": SimpleNamespace(HAS_FITZ=True),
        }
        with self.assertRaisesRegex(RuntimeError, "creo_worker_source"):
            smoke._check_application_resources(modules)
        (root / "worker.pyc").write_bytes(b"worker")
        self.assertEqual(len(smoke._check_application_resources(modules)), 6)
        modules["pages.pdf_viewer_widget"].HAS_FITZ = False
        with self.assertRaisesRegex(RuntimeError, "PDF viewer could not load"):
            smoke._check_application_resources(modules)

    def test_application_import_check_rejects_database_and_restores_sqlite(self):
        original_connect = sqlite3.connect
        original_dbapi_connect = sqlite3.dbapi2.connect
        with patch("importlib.import_module", side_effect=lambda name: sqlite3.connect(":memory:")):
            with self.assertRaisesRegex(RuntimeError, "Database access is forbidden"):
                smoke._check_application_imports()
        self.assertIs(sqlite3.connect, original_connect)
        self.assertIs(sqlite3.dbapi2.connect, original_dbapi_connect)

    def test_frozen_viewer_never_falls_back_to_developer_conda(self):
        with patch.object(launcher, "_is_frozen", return_value=True), \
                patch.object(launcher.os.path, "isfile", return_value=False), \
                patch.object(launcher, "_conda_bat") as conda:
            with self.assertRaisesRegex(FileNotFoundError, "complete application folder"):
                launcher._build_cmd(["part.step"])
        conda.assert_not_called()

    def test_frozen_viewer_passes_arguments_to_bundled_executable(self):
        folder = self.temporary_directory()
        viewer = folder / "CADViewer.exe"
        viewer.touch()
        with patch.object(launcher, "_is_frozen", return_value=True), \
                patch.object(sys, "executable", str(folder / "Nexus.exe")):
            self.assertEqual(launcher._build_cmd(["a b.step"]), [str(viewer), "a b.step"])

    def test_source_viewer_preserves_conda_launch(self):
        with patch.object(launcher, "_is_frozen", return_value=False), \
                patch.object(launcher, "_conda_bat", return_value="conda.bat"):
            self.assertEqual(
                launcher._build_cmd(["part.step"]),
                ["conda.bat", "run", "-n", "pyoccenv", "python", "-m", "tools.CAD.step_viewer", "part.step"],
            )


if __name__ == "__main__":
    unittest.main()
