import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from core.updates import setup_wizard


class SetupWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "downloaded Nexus"
        self.source.mkdir()
        self.root = self.base / "installed Nexus"
        self.dialogs = []
        self.addCleanup(self.close_dialogs)

    def close_dialogs(self):
        for dialog in self.dialogs:
            self.wait(dialog)
            dialog.close()
        self.app.processEvents()

    def dialog(self, **kwargs):
        dialog = setup_wizard.SetupDialog(self.source, "ab" * 32, root=self.root, **kwargs)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def wait(self, dialog):
        for _ in range(500):
            self.app.processEvents()
            if not dialog.busy:
                return
            QTest.qWait(10)
        self.fail("Setup worker did not finish")

    def create_installation(self, version="4.1.2", **unused):
        folder = f"versions/{version}-test"
        application = self.root / folder / "Nexus.exe"
        application.parent.mkdir(parents=True, exist_ok=True)
        application.write_bytes(b"mock executable")
        launcher = self.root / "bootstrap" / "NexusLauncher.exe"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_bytes(b"mock launcher")
        current = {"version": version, "directory": folder, "build_id": "test"}
        (self.root / "current.json").write_text(json.dumps(current))
        (self.root / "install.json").write_text(json.dumps({"schema": 1, "feed": "old feed"}))
        return current

    def test_default_location_is_per_user_and_setup_does_not_open_workspace(self):
        with patch.object(setup_wizard.install, "default_install_root", return_value=self.root), patch(
            "sqlite3.connect", side_effect=AssertionError("Setup must not open a database")
        ), patch.object(setup_wizard.subprocess, "Popen") as launch:
            dialog = setup_wizard.SetupDialog(self.source, "ab" * 32)
            self.dialogs.append(dialog)
            self.assertEqual(Path(dialog.location_edit.text()), self.root)
            self.assertTrue(dialog.desktop_checkbox.isChecked())
            self.assertTrue(dialog.install_button.isEnabled())
            self.assertEqual(dialog.install_button.text(), "Install")
            launch.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_install_copies_in_worker_then_creates_shortcuts_without_launching(self):
        main_thread = threading.get_ident()
        calls = []

        def copy(source, root, feed, key):
            calls.append((source, root, feed, key, threading.get_ident()))
            return self.create_installation()

        with patch.object(setup_wizard.install, "initial_install", side_effect=copy), patch.object(
            setup_wizard, "_create_shortcuts", return_value=[self.base / "Nexus.lnk"]
        ) as shortcuts, patch.object(setup_wizard.subprocess, "Popen") as launch:
            dialog = self.dialog(feed=r"\\server\releases")
            dialog.install_button.click()
            self.wait(dialog)
            self.assertEqual(calls[0][:4], (self.source, self.root, r"\\server\releases", "ab" * 32))
            self.assertNotEqual(calls[0][4], main_thread)
            shortcuts.assert_called_once_with(self.root, desktop=True)
            self.assertEqual(dialog.install_button.text(), "Open Nexus")
            self.assertEqual(dialog.cancel_button.text(), "Close")
            self.assertIn("is ready", dialog.status_label.text())
            self.assertFalse(dialog.feed_edit.isEnabled())
            launch.assert_not_called()
            dialog.cancel_button.click()
            self.assertEqual(dialog.result(), QDialog.Accepted)

    def test_cancel_and_window_close_keep_copy_worker_alive(self):
        release = threading.Event()
        started = threading.Event()

        def copy(*args):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release worker")
            return self.create_installation()

        with patch.object(setup_wizard.install, "initial_install", side_effect=copy), patch.object(
            setup_wizard, "_create_shortcuts", return_value=[]
        ):
            dialog = self.dialog()
            dialog.install_button.click()
            self.assertTrue(started.wait(timeout=2))
            try:
                self.assertTrue(dialog.busy)
                self.assertFalse(dialog.location_edit.isEnabled())
                self.assertFalse(dialog.cancel_button.isEnabled())
                self.assertFalse(dialog.close())
                dialog.reject()
                dialog.accept()
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.busy)
            finally:
                release.set()
                self.wait(dialog)
            self.assertFalse(dialog.busy)
            self.assertTrue(dialog.cancel_button.isEnabled())

    def test_shortcut_failure_keeps_installation_and_retry_does_not_copy_again(self):
        with patch.object(
            setup_wizard.install, "initial_install", side_effect=lambda *args: self.create_installation()
        ) as copy, patch.object(
            setup_wizard, "_create_shortcuts", side_effect=[OSError("Desktop is unavailable"), []]
        ) as shortcuts:
            dialog = self.dialog()
            dialog.desktop_checkbox.setChecked(False)
            dialog.install_button.click()
            self.wait(dialog)
            self.assertIn("Nexus is installed", dialog.status_label.text())
            self.assertIn("Repair shortcuts", dialog.status_label.text())
            self.assertTrue(dialog.repair_button.isVisible())
            self.assertEqual(dialog.install_button.text(), "Open Nexus")
            dialog.repair_button.click()
            self.wait(dialog)
            copy.assert_called_once()
            self.assertEqual(shortcuts.call_count, 2)
            shortcuts.assert_called_with(self.root, desktop=False)
            self.assertIn("is ready", dialog.status_label.text())

    def test_existing_installation_can_open_without_overwrite_or_feed_change(self):
        self.create_installation(version="9.0.0")
        with patch.object(setup_wizard.install, "initial_install") as copy, patch.object(
            setup_wizard.subprocess, "Popen"
        ) as launch, patch.dict(os.environ, {"QT_PLUGIN_PATH": "old runtime", "PYTHONPATH": "old source"}):
            dialog = self.dialog(feed="new source feed")
            self.assertEqual(dialog.install_button.text(), "Open Nexus")
            self.assertIn("9.0.0 is already installed", dialog.status_label.text())
            self.assertFalse(dialog.feed_edit.isEnabled())
            dialog.install_button.click()
            copy.assert_not_called()
            launch.assert_called_once()
            args, kwargs = launch.call_args
            self.assertEqual(args[0], [str(self.root / "bootstrap" / "NexusLauncher.exe")])
            self.assertEqual(kwargs["env"]["NEXUS_INSTALL_ROOT"], str(self.root))
            self.assertNotIn("QT_PLUGIN_PATH", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(json.loads((self.root / "install.json").read_text())["feed"], "old feed")

    def test_existing_installation_repairs_shortcuts_without_reinstalling(self):
        self.create_installation()
        with patch.object(setup_wizard.install, "initial_install") as copy, patch.object(
            setup_wizard, "_create_shortcuts", return_value=[]
        ) as shortcuts:
            dialog = self.dialog()
            dialog.repair_button.click()
            self.wait(dialog)
            copy.assert_not_called()
            shortcuts.assert_called_once_with(self.root, desktop=True)

    def test_incomplete_installation_requires_an_empty_location(self):
        (self.root / "bootstrap").mkdir(parents=True)
        (self.root / "bootstrap" / "NexusLauncher.exe").write_bytes(b"partial")
        with patch.object(setup_wizard.install, "initial_install") as copy:
            dialog = self.dialog()
            self.assertFalse(dialog.install_button.isEnabled())
            self.assertIn("incomplete", dialog.status_label.text())
            copy.assert_not_called()
            new_root = self.base / "empty new location"
            dialog.location_edit.setText(str(new_root))
            self.assertTrue(dialog.install_button.isEnabled())
            self.assertEqual(dialog.install_button.text(), "Install")
        self.assertFalse(new_root.exists())

    def test_copy_failure_reports_error_and_does_not_create_shortcuts(self):
        with patch.object(setup_wizard.install, "initial_install", side_effect=OSError("Disk is full")), patch.object(
            setup_wizard, "_create_shortcuts"
        ) as shortcuts:
            dialog = self.dialog()
            dialog.install_button.click()
            self.wait(dialog)
            self.assertIn("Disk is full", dialog.status_label.text())
            self.assertTrue(dialog.install_button.isEnabled())
            self.assertEqual(dialog.install_button.text(), "Install")
            shortcuts.assert_not_called()

    def test_launch_failure_remains_visible_and_can_be_retried(self):
        self.create_installation()
        with patch.object(setup_wizard.subprocess, "Popen", side_effect=OSError("Access denied")):
            dialog = self.dialog()
            dialog.install_button.click()
            self.assertIn("could not be opened", dialog.status_label.text())
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.install_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
