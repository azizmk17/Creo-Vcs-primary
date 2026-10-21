"""A shipped bootstrap must activate packages staged by the newer application."""
import base64
import json
import os
import subprocess
import tempfile
import types
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.updates import install
from core.updates.protocol import canonical_json, hash_file


class SetupUpdaterCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = Path(__file__).resolve().parents[1]
        # HEAD is the already shipped installer; the working-tree installer
        # stages the new release. Do not import over the current install module.
        source = subprocess.check_output(
            ["git", "show", "HEAD:core/updates/install.py"],
            cwd=cls.repository, encoding="utf-8",
        )
        cls.previous = types.ModuleType("core.updates._previous_bootstrap_install")
        cls.previous.__package__ = "core.updates"
        cls.previous.__file__ = "git:HEAD:core/updates/install.py"
        exec(compile(source, cls.previous.__file__, "exec"), cls.previous.__dict__)

    def test_previous_bootstrap_activates_new_package_containing_setup(self):
        with tempfile.TemporaryDirectory(dir=self.repository) as directory:
            base = Path(directory)
            with patch.dict(os.environ, {"APPDATA": str(base / "preferences"),
                                         "LOCALAPPDATA": str(base / "local")}), patch(
                "sqlite3.connect", side_effect=AssertionError("An app update must not open a database")
            ):
                source, root, feed = base / "original", base / "installed", base / "feed"
                feed.mkdir()
                key = Ed25519PrivateKey.generate()
                public = key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw,
                ).hex()
                for name in ("Nexus.exe", "CADViewer.exe", "Setup.exe", "_internal/runtime.dll",
                             "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe",
                             "bootstrap/_internal/runtime.dll"):
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"original installation: " + name.encode())
                (source / "build_manifest.json").write_text(json.dumps({
                    "version": "4.1.0", "build_id": "oldbootstrap", "release_public_key": public,
                    "supported_schema": {"min": 0, "max": 49},
                }), encoding="utf-8")
                self.previous.initial_install(source, root, str(feed), public)
                original = self.previous.current_application(root)
                bootstrap = (root / "bootstrap/NexusUpdater.exe").read_bytes()

                archive_path = feed / "Nexus-4.2.0.zip"
                files = {
                    "Nexus.exe": b"new Nexus application",
                    "CADViewer.exe": b"new CAD viewer",
                    "Setup.exe": b"new graphical Setup sharing the application runtime",
                    "_internal/runtime.dll": b"new shared runtime",
                    "bootstrap/NexusUpdater.exe": b"must not replace the installed bootstrap",
                }
                with zipfile.ZipFile(archive_path, "w") as archive:
                    for name, content in files.items():
                        archive.writestr(name, content)
                payload = {
                    "schema": 1, "product": "Nexus", "platform": "windows-x64",
                    "version": "4.2.0", "build_id": "withsetup", "release_sequence": 1,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                    "package": {"path": archive_path.name, "size": archive_path.stat().st_size,
                                "sha256": hash_file(archive_path)},
                    "supported_schema": {"min": 0, "max": 49},
                    "min_updater_version": "1.0.0", "release_notes": "Adds graphical Setup",
                }
                signature = base64.urlsafe_b64encode(key.sign(canonical_json(payload))).rstrip(b"=").decode("ascii")
                (feed / "latest.json").write_text(json.dumps({
                    "signed": payload, "signature": signature,
                }), encoding="utf-8")
                smoke = Mock()
                staged = install.stage_release(feed, public, root, smoke_runner=smoke)
                smoke.assert_called_once()
                self.assertEqual(self.previous.current_application(root), original)

                # Activation is performed by the old executable, with its old
                # ZIP inventory rules, rather than the application's new code.
                activated = self.previous.activate_staged(root, public, schema_version=49)
                target = root / staged["directory"]
                self.assertEqual(activated["version"], "4.2.0")
                self.assertEqual(self.previous.current_application(root), target / "Nexus.exe")
                self.assertEqual((target / "Setup.exe").read_bytes(), files["Setup.exe"])
                self.assertEqual((target / "_internal/runtime.dll").read_bytes(), files["_internal/runtime.dll"])
                self.assertEqual((root / "bootstrap/NexusUpdater.exe").read_bytes(), bootstrap)
                self.assertFalse((target / "bootstrap").exists())
                self.assertTrue(original.is_file())
                self.assertFalse((root / "staged.json").exists())


if __name__ == "__main__":
    unittest.main()
