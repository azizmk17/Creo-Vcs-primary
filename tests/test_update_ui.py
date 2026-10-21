import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QWidget

from core.updates import settings, trust
from core.updates.protocol import canonical_json
from core.updates.ui import UpdateCenter, UpdateController


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"APPDATA": str(self.base), "NEXUS_INSTALL_ROOT": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.host = QWidget()
        self.addCleanup(self.host.close)
        self.controller = UpdateController(self.host, "4.1.0", self.base / "unused.db")
        self.controller.timer.stop()
        self.center = UpdateCenter(self.controller)
        self.controller._center = self.center
        self.center.show()
        self.addCleanup(self.center.close)
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.key_patch = patch("core.updates.ui.release_public_key", return_value=self.public)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

    def wait(self):
        for _ in range(500):
            self.app.processEvents()
            if not self.controller.busy:
                return
            QTest.qWait(10)
        self.fail("Background update operation did not finish")

    def publish(self, corrupt=False, sequence=7, version="4.2.0"):
        feed = self.base / "feed"
        feed.mkdir(exist_ok=True)
        payload = {
            "schema": 1, "product": "Nexus", "platform": "windows-x64", "version": version,
            "build_id": "ui-test", "release_sequence": sequence,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "package": {"path": "releases/nexus.zip", "size": 32, "sha256": "a" * 64},
            "supported_schema": {"min": 49, "max": 49},
            "min_updater_version": "1.0.0", "release_notes": "Published update",
        }
        signature = base64.urlsafe_b64encode(self.key.sign(canonical_json(payload))).rstrip(b"=").decode()
        if corrupt:
            payload["version"] = "9.0.0"
        (feed / "latest.json").write_text(json.dumps({"signed": payload, "signature": signature}))
        settings.save_settings(feed=str(feed))
        return payload

    def test_unauthenticated_feed_never_enables_download(self):
        self.publish(corrupt=True)
        self.controller.check()
        self.wait()
        self.assertIsNone(self.controller.payload)
        self.assertFalse(self.center.download_button.isEnabled())
        self.assertFalse(self.center.restart_button.isEnabled())
        self.assertIn("could not", self.controller.status)

    def test_verified_feed_enables_download_and_caches_sequence_without_db_access(self):
        self.publish()
        self.controller.check()
        self.wait()
        self.assertIsNotNone(self.controller.payload, self.controller.status)
        self.assertEqual(self.controller.payload["version"], "4.2.0")
        self.assertTrue(self.center.download_button.isEnabled())
        self.assertEqual(settings.last_sequence(self.public), 7)
        self.assertFalse((self.base / "unused.db").exists())
        self.assertEqual(self.center.notes.toPlainText(), "Published update")

    def test_feed_preferences_preserve_license_and_sequence_cannot_decrease(self):
        license_path = self.base / "CreoVCS/creovcs.lic"
        license_path.parent.mkdir()
        license_path.write_bytes(b"existing license")
        settings.save_settings(feed="company-share")
        settings.remember_sequence(self.public, 8)
        settings.remember_sequence(self.public, 3)
        self.assertEqual(settings.last_sequence(self.public), 8)
        self.assertEqual(settings.update_feed(), "company-share")
        self.assertEqual(license_path.read_bytes(), b"existing license")

    def test_frozen_build_never_accepts_environment_key_override(self):
        with patch.object(sys, "frozen", True, create=True), patch.dict(os.environ, {"NEXUS_RELEASE_PUBLIC_KEY": self.public}), patch.dict(sys.modules, {"nexus_release_trust": None}):
            self.assertEqual(trust.release_public_key(), "")

    def test_new_release_reenables_download_after_an_older_release_was_staged(self):
        payload = self.publish()
        self.controller.check()
        self.wait()
        self.controller._finished("stage", {"payload": payload}, "")
        self.assertTrue(self.center.restart_button.isEnabled())
        self.publish(sequence=8, version="4.3.0")
        self.controller.check()
        self.wait()
        self.assertFalse(self.center.restart_button.isEnabled())
        self.assertTrue(self.center.download_button.isEnabled())
        self.assertEqual(settings.last_sequence(self.public), 8)

    def test_restart_restores_staged_readiness_after_authenticating_the_feed(self):
        payload = self.publish()
        root = self.base / "installation"
        root.mkdir()
        (root / "staged.json").write_text(json.dumps({"payload": payload}))
        with patch.dict(os.environ, {"NEXUS_INSTALL_ROOT": str(root)}):
            controller = UpdateController(self.host, "4.1.0", self.base / "unused.db")
            controller.timer.stop()
            center = UpdateCenter(controller)
            controller._center = center
            center.show()
            self.addCleanup(center.close)
            self.assertFalse(center.restart_button.isEnabled())
            self.controller = controller
            controller.check()
            self.wait()
            self.assertTrue(center.restart_button.isEnabled())
            self.assertFalse(center.download_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
