import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from core.updates import install, setup_wizard
from tools.frozen_build.setup_smoke import run_setup_smoke


class SetupIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_wizard_installs_real_fixture_without_database_access_and_preserves_source_and_license(self):
        source, target = self.base / "extracted", self.base / "local/Nexus"
        public = "ab" * 32
        for name in ("Nexus.exe", "CADViewer.exe", "Setup.exe", "_internal/runtime.dll",
                     "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe",
                     "bootstrap/_internal/runtime.dll"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        (source / "build_manifest.json").write_text(json.dumps({
            "version": "4.2.0", "build_id": "setup-test", "release_public_key": public,
            "supported_schema": {"min": 49, "max": 49},
        }))
        preferences = self.base / "preferences"
        license_path = preferences / "CreoVCS/creovcs.lic"
        license_path.parent.mkdir(parents=True)
        license_path.write_bytes(b"existing signed license")
        with patch.dict(os.environ, {"APPDATA": str(preferences)}), patch(
            "sqlite3.connect", side_effect=AssertionError("Setup must not open a database")
        ), patch.object(setup_wizard, "_create_shortcuts", return_value=[]) as shortcuts, patch.object(
            setup_wizard.subprocess, "Popen"
        ) as launch:
            dialog = setup_wizard.SetupDialog(source, public, root=target, feed=r"\\server\NexusReleases")
            dialog.install_button.click()
            for _ in range(500):
                self.app.processEvents()
                if not dialog.busy:
                    break
                QTest.qWait(10)
            self.assertFalse(dialog.busy)
            self.assertEqual(dialog.install_button.text(), "Open Nexus", dialog.status_label.text())
            self.assertEqual(json.loads((target / "current.json").read_text())["version"], "4.2.0")
            self.assertEqual(json.loads((target / "install.json").read_text())["feed"], r"\\server\NexusReleases")
            self.assertEqual(install.current_application(target).read_bytes(), b"Nexus.exe")
            self.assertTrue((target / "bootstrap/NexusLauncher.exe").is_file())
            self.assertEqual((install.current_application(target).parent / "Setup.exe").read_bytes(), b"Setup.exe")
            self.assertTrue((source / "Setup.exe").is_file())
            self.assertTrue((source / "Nexus.exe").is_file())
            self.assertEqual(license_path.read_bytes(), b"existing signed license")
            shortcuts.assert_called_once_with(target, desktop=True)
            launch.assert_not_called()
            dialog.close()
            self.app.processEvents()

    def test_setup_diagnostic_checks_gui_without_creating_an_installation(self):
        report = self.base / "setup-smoke.json"
        with patch("sqlite3.connect", side_effect=AssertionError("Diagnostic opened a database")), patch.object(
            setup_wizard.install, "initial_install"
        ) as copy, patch.object(setup_wizard, "_create_shortcuts") as shortcuts:
            self.assertEqual(run_setup_smoke(report, "ab" * 32), 0)
        result = json.loads(report.read_text())
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["gui"])
        copy.assert_not_called()
        shortcuts.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [report])


if __name__ == "__main__":
    unittest.main()
