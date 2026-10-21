import base64
import json
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.updates import desktop, install, settings
from core.updates.protocol import canonical_json, hash_file


class UpdateInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {"APPDATA": str(self.base / "preferences")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = self.base / "installed"
        self.feed = self.base / "feed"
        self.feed.mkdir()
        self.key = Ed25519PrivateKey.generate()
        self.public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        source = self.base / "trusted"
        for name in ("Nexus.exe", "CADViewer.exe", "_internal/runtime.dll", "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe", "bootstrap/_internal/bootstrap.dll"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original trusted " + name.encode())
        (source / "build_manifest.json").write_text(json.dumps({"version": "4.0.10", "build_id": "original", "release_public_key": self.public, "supported_schema": {"min": 0, "max": 49}}))
        self.source = source
        install.initial_install(source, self.root, str(self.feed), self.public)
        self.original = install.current_application(self.root)

    def publish(self, *, version="4.1.0", sequence=1, extra=None, minimum_schema=0, maximum_schema=49, updater="1.0.0"):
        package = self.feed / f"release-{sequence}.zip"
        with zipfile.ZipFile(package, "w") as archive:
            for name in ("Nexus.exe", "CADViewer.exe", "_internal/runtime.dll"):
                archive.writestr(name, f"updated {version} {sequence} {name}")
            for name, content in (extra or {}).items():
                archive.writestr(name, content)
        payload = {
            "schema": 1, "product": "Nexus", "platform": "windows-x64", "version": version,
            "build_id": f"build{sequence}", "release_sequence": sequence,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            "package": {"path": package.name, "size": package.stat().st_size, "sha256": hash_file(package)},
            "supported_schema": {"min": minimum_schema, "max": maximum_schema},
            "min_updater_version": updater, "release_notes": "A verified company release",
        }
        self.announce(payload)
        return payload

    def announce(self, payload):
        signature = base64.urlsafe_b64encode(self.key.sign(canonical_json(payload))).rstrip(b"=").decode()
        (self.feed / "latest.json").write_text(json.dumps({"signed": payload, "signature": signature}))

    def stage(self, **kwargs):
        return install.stage_release(self.feed, self.public, self.root, smoke_runner=lambda directory, reports: None, **kwargs)

    def test_manual_install_creates_stable_bootstrap_and_keeps_user_data_out(self):
        self.assertTrue((self.root / "bootstrap/NexusLauncher.exe").is_file())
        self.assertFalse((self.original.parent / "bootstrap").exists())
        configuration = json.loads((self.root / "install.json").read_text())
        self.assertEqual(configuration["feed"], str(self.feed))
        self.assertNotIn(self.public, (self.root / "install.json").read_text())
        self.assertEqual(install.installed_root_for_executable(self.original), self.root)

    def test_manual_install_requires_matching_baked_key_and_explicit_schema(self):
        manifest_path = self.source / "build_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["release_public_key"] = "00" * 32
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(install.InstallError, "public key does not match"):
            install.initial_install(self.source, self.base / "wrong-key", str(self.feed), self.public)
        manifest["release_public_key"] = self.public
        del manifest["supported_schema"]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(install.InstallError, "schema range"):
            install.initial_install(self.source, self.base / "missing-schema", str(self.feed), self.public)

    def test_manual_install_refuses_private_signing_key_files(self):
        (self.source / "release.key").write_text("must not ship")
        with self.assertRaisesRegex(install.InstallError, "private workspace or license"):
            install.initial_install(self.source, self.base / "private-key", str(self.feed), self.public)

    def test_stage_keeps_running_version_and_activate_switches_pointer_only(self):
        self.publish()
        staged = self.stage()
        self.assertEqual(install.current_application(self.root), self.original)
        bootstrap = (self.root / "bootstrap/NexusLauncher.exe").read_bytes()
        activated = install.activate_staged(self.root, self.public)
        self.assertEqual(activated["version"], "4.1.0")
        self.assertEqual(install.current_application(self.root).parent, self.root / staged["directory"])
        self.assertTrue(self.original.is_file())
        self.assertEqual((self.root / "bootstrap/NexusLauncher.exe").read_bytes(), bootstrap)

    def test_failed_runtime_check_never_changes_current_pointer(self):
        self.publish()
        with self.assertRaisesRegex(RuntimeError, "bad runtime"):
            install.stage_release(self.feed, self.public, self.root, smoke_runner=lambda *args: (_ for _ in ()).throw(RuntimeError("bad runtime")))
        self.assertEqual(install.current_application(self.root), self.original)
        self.assertFalse((self.root / "staged.json").exists())

    def test_bad_archive_hash_never_extracts_or_activates(self):
        payload = self.publish()
        (self.feed / payload["package"]["path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(install.InstallError, "SHA-256"):
            self.stage()
        self.assertEqual(install.current_application(self.root), self.original)

    def test_archive_path_traversal_private_files_and_case_duplicates_rejected(self):
        for index, entry in enumerate(("../escape.exe", "/absolute.exe", "C:/drive.exe", "_internal/creo_vcs.db", "creovcs.lic", "nexus.EXE"), 1):
            with self.subTest(entry=entry):
                self.publish(sequence=index, extra={entry: b"unsafe"})
                with self.assertRaises(install.InstallError):
                    self.stage()
        self.assertFalse((self.base / "escape.exe").exists())

    def test_archive_symlink_is_rejected(self):
        payload = self.publish()
        package = self.feed / payload["package"]["path"]
        with zipfile.ZipFile(package, "a") as archive:
            link = zipfile.ZipInfo("_internal/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../escape")
        payload["package"].update(size=package.stat().st_size, sha256=hash_file(package))
        self.announce(payload)
        with self.assertRaisesRegex(install.InstallError, "links"):
            self.stage()

    def test_activation_rechecks_every_file_and_archive(self):
        self.publish()
        staged = self.stage()
        (self.root / staged["directory"] / "_internal/runtime.dll").write_bytes(b"changed after smoke")
        with self.assertRaisesRegex(install.InstallError, "changed after its runtime checks"):
            install.activate_staged(self.root, self.public)
        self.assertEqual(install.current_application(self.root), self.original)

    def test_run_leases_block_activation_and_stale_pid_files_do_not(self):
        self.publish()
        self.stage()
        with install.acquire_run_lease(self.root):
            self.assertEqual(install.live_processes(self.root)[0]["pid"], os.getpid())
            with self.assertRaisesRegex(install.InstallError, "Close all Nexus"):
                install.activate_staged(self.root, self.public)
        stale = self.root / "leases/stale.json"
        stale.write_bytes(b"\0" + json.dumps({"pid": os.getpid()}).encode())
        self.assertEqual(install.live_processes(self.root), [])
        install.activate_staged(self.root, self.public)

    def test_signed_older_sequence_is_rejected_after_newer_observation(self):
        self.publish(sequence=5)
        self.stage()
        self.publish(sequence=4)
        with self.assertRaisesRegex(ValueError, "older than"):
            self.stage()
        self.assertEqual(install.current_application(self.root), self.original)

    def test_gui_observed_sequence_is_respected(self):
        self.publish(sequence=2)
        settings.remember_sequence(self.public, 3)
        with self.assertRaisesRegex(ValueError, "older than"):
            self.stage()

    def test_downgrade_and_newer_updater_are_refused(self):
        self.publish(version="4.0.9")
        with self.assertRaisesRegex(install.InstallError, "downgrade"):
            self.stage()
        self.publish(sequence=2, updater="2.0.0")
        with self.assertRaisesRegex(install.InstallError, "bootstrap manually"):
            self.stage()

    def test_normal_update_cannot_replace_same_version_with_another_build(self):
        self.publish(version="4.0.10")
        with self.assertRaisesRegex(install.InstallError, "different build must use"):
            self.stage()

    def test_explicit_rollback_requires_known_compatible_schema(self):
        self.publish(version="4.0.9", maximum_schema=48)
        self.stage(allow_rollback=True)
        with self.assertRaisesRegex(install.InstallError, "known compatible"):
            install.activate_staged(self.root, self.public, allow_rollback=True)
        with self.assertRaisesRegex(install.InstallError, "cannot read"):
            install.activate_staged(self.root, self.public, allow_rollback=True, schema_version=49)
        install.activate_staged(self.root, self.public, allow_rollback=True, schema_version=48)

    def test_current_pointer_cannot_escape_versions(self):
        (self.root / "current.json").write_text(json.dumps({"directory": "versions/../../trusted"}))
        with self.assertRaises(install.InstallError):
            install.current_application(self.root)

    def test_database_compatibility_is_readonly_and_refuses_activation(self):
        self.publish(maximum_schema=49)
        database = self.base / "workspace.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
            connection.execute("INSERT INTO schema_migrations VALUES(50)")
        before = database.read_bytes()
        self.stage(db_path=database)
        with self.assertRaisesRegex(RuntimeError, "schema 50"):
            install.activate_staged(self.root, self.public)
        self.assertEqual(before, database.read_bytes())
        self.assertEqual(install.current_application(self.root), self.original)

    def test_manual_install_cli_does_not_start_another_application(self):
        arguments = ["NexusLauncher.exe", "--install", "--source", str(self.source), "--root", str(self.base / "second"), "--feed", str(self.feed)]
        with patch.object(desktop.sys, "argv", arguments), patch.object(desktop, "_message"), patch.object(desktop, "_run_current") as launch:
            self.assertEqual(desktop.main("launcher", self.public, "4.0.10"), 0)
        launch.assert_not_called()

    def test_frozen_distribution_installer_defaults_to_local_install_not_source(self):
        target = self.base / "default-local-install"
        arguments = ["NexusLauncher.exe", "--install", "--feed", str(self.feed)]
        with patch.object(desktop.sys, "argv", arguments), \
                patch.object(desktop.sys, "executable", str(self.source / "bootstrap/NexusLauncher.exe")), \
                patch.object(install, "default_install_root", return_value=target), \
                patch.object(desktop, "_message"), patch.object(desktop, "_run_current") as launch:
            self.assertEqual(desktop.main("launcher", self.public, "4.0.10"), 0)
        self.assertTrue((target / "current.json").is_file())
        self.assertFalse((self.source / "current.json").exists())
        launch.assert_not_called()

    def test_quiet_installer_reports_success_without_showing_dialogs(self):
        target = self.base / "quiet-install"
        report = self.base / "install-result.json"
        arguments = ["NexusLauncher.exe", "--install", "--source", str(self.source), "--root", str(target), "--quiet", "--result-json", str(report)]
        with patch.object(desktop.sys, "argv", arguments), patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main("launcher", self.public, "4.0.10"), 0)
        message.assert_not_called()
        result = json.loads(report.read_text())
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "install")
        self.assertEqual(result["root"], str(target))
        self.assertEqual(result["version"], "4.0.10")

    def test_quiet_protocol_failure_reports_error_without_showing_dialogs(self):
        self.publish()
        envelope = json.loads((self.feed / "latest.json").read_text())
        envelope["signed"]["release_notes"] = "tampered"
        (self.feed / "latest.json").write_text(json.dumps(envelope))
        report = self.base / "stage-result.json"
        arguments = ["NexusUpdater.exe", "--stage", "--feed", str(self.feed), "--root", str(self.root), "--quiet", "--result-json", str(report)]
        with patch.object(desktop.sys, "argv", arguments), patch.object(desktop, "_message") as message:
            self.assertEqual(desktop.main("updater", self.public, "4.0.10"), 1)
        message.assert_not_called()
        result = json.loads(report.read_text())
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "stage")
        self.assertIn("signature is invalid", result["error"])

    def test_launcher_holds_lease_until_child_exits(self):
        class Child:
            def wait(child):
                self.assertTrue(install.live_processes(self.root))
                return 0
        with patch.object(desktop, "_launch", return_value=Child()):
            self.assertEqual(desktop._run_current(self.root), 0)
        self.assertEqual(install.live_processes(self.root), [])

    def test_wait_for_remaining_processes_fails_without_terminating_them(self):
        with install.acquire_run_lease(self.root):
            with self.assertRaisesRegex(install.InstallError, "No process was stopped"):
                desktop._wait_for_leases(self.root, 0)
            self.assertTrue(install.live_processes(self.root))

    def test_packaged_smoke_requires_all_checks_and_removes_host_runtime_paths(self):
        report_data = {"ok": True, "frozen": True, "checks": {name: {"ok": True} for name in ("pdf", "step", "qt_viewer", "application")}}
        def run(command, **kwargs):
            Path(command[-1]).write_text(json.dumps(report_data))
            environment = kwargs["env"]
            self.assertEqual(environment["QT_QPA_PLATFORM"], "windows")
            for variable in ("CONDA_PREFIX", "PYTHONPATH", "QT_PLUGIN_PATH", "CSF_XSMessage", "CASROOT"):
                self.assertNotIn(variable, environment)
            return SimpleNamespace(returncode=0)
        with patch.dict(os.environ, {"CONDA_PREFIX": "host", "PYTHONPATH": "host", "QT_PLUGIN_PATH": "host", "CSF_XSMessage": "host", "CASROOT": "host"}), \
                patch.object(install.subprocess, "run", side_effect=run) as process:
            install._smoke_version(self.original.parent, self.base / "smoke-reports")
            self.assertEqual(process.call_count, 2)
            del report_data["checks"]["application"]
            with self.assertRaisesRegex(install.InstallError, "failed its PDF/CAD/runtime check"):
                install._smoke_version(self.original.parent, self.base / "smoke-incomplete")


if __name__ == "__main__":
    unittest.main()
