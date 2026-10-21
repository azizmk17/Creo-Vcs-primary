"""Join the real publisher, signed feed, installer and database compatibility gate."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from core.updates import compatibility, install, protocol
from tools.licensing.update_version import generate_release_keypair, publish_release


ROOT = Path(__file__).resolve().parents[1]


class UpdateWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        environment = patch.dict(os.environ, {"APPDATA": str(self.base / "preferences"),
                                              "LOCALAPPDATA": str(self.base / "local")})
        environment.start()
        self.addCleanup(environment.stop)
        self.private, public = generate_release_keypair(self.base / "ephemeral-keys")
        self.public = public.read_text(encoding="ascii").strip()
        self.feed = self.base / "company-feed"
        self.installed = self.base / "installed"
        self.database = self.base / "workspace" / "creo_vcs.db"
        self.database.parent.mkdir()
        connect = getattr(sqlite3, "_nexus_original_connect", sqlite3.connect)
        with closing(connect(self.database)) as connection:
            connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO schema_migrations VALUES (49)")
            connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO app_metadata VALUES ('minimum_app_version', '4.1.0')")
            connection.execute("CREATE TABLE part_fixture(number TEXT PRIMARY KEY, description TEXT)")
            connection.execute("INSERT INTO part_fixture VALUES ('COMPANY-001', 'Keep this engineering work')")
            connection.commit()
        self.smoke_versions = []

    def distribution(self, version, build, schema=49):
        source = self.base / "distributions" / f"Nexus-{version}-{build}"
        for name in ("Nexus.exe", "CADViewer.exe", "_internal/runtime.dll",
                     "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe", "bootstrap/_internal/runtime.dll"):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"Fixture {version}/{build}/{name}".encode("ascii"))
        checks = {name: {"ok": True} for name in ("pdf", "step", "qt_viewer", "application")}
        manifest = {
            "app": "Nexus", "version": version, "build_id": build, "mode": "onedir",
            "release_public_key": self.public, "supported_schema": {"min": schema, "max": schema},
            "smoke_tests": {name: {"ok": True, "frozen": True, "checks": checks}
                            for name in ("Nexus.exe", "CADViewer.exe")},
            "bootstrap_tests": {name: {"ok": True, "frozen": True, "release_public_key": self.public}
                                for name in ("NexusLauncher.exe", "NexusUpdater.exe")},
        }
        if protocol.version_tuple(version) >= (4, 2, 0):
            (source / "Setup.exe").write_bytes(b"fixture setup executable")
            manifest["setup_test"] = {"ok": True, "frozen": True, "gui": True,
                                      "release_public_key": self.public}
        (source / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return source

    def publish(self, source, sequence, *, rollback=False):
        build = json.loads((source / "build_manifest.json").read_text(encoding="utf-8"))
        return publish_release(source, self.feed, self.private, version=build["version"],
                               release_sequence=sequence, schema_min=build["supported_schema"]["min"],
                               schema_max=build["supported_schema"]["max"], allow_rollback=rollback)

    def initial_release(self):
        source = self.distribution("4.1.0", "initial-build")
        payload = self.publish(source, 1)
        installed = install.initial_install(source, self.installed, str(self.feed), self.public)
        self.assertEqual(installed["version"], "4.1.0")
        self.assertFalse((install.current_application(self.installed).parent / "bootstrap").exists())
        return source, payload

    def smoke(self, directory, reports):
        self.assertTrue((directory / "Nexus.exe").is_file())
        self.assertTrue((directory / "CADViewer.exe").is_file())
        self.assertTrue((directory / "_internal/runtime.dll").is_file())
        self.assertFalse((directory / "bootstrap").exists())
        manifest = json.loads((directory / "build_manifest.json").read_text(encoding="utf-8"))
        self.smoke_versions.append(manifest["version"])

    def stage(self, *, rollback=False):
        return install.stage_release(self.feed, self.public, self.installed, smoke_runner=self.smoke,
                                     db_path=self.database, allow_rollback=rollback)

    def activate_readonly(self, *, rollback=False):
        statements = []
        original = compatibility.readonly_connection

        def audit_connection(path):
            self.assertEqual(Path(path).resolve(), self.database.resolve())
            connection = original(path)
            connection.set_trace_callback(statements.append)
            return connection

        before = protocol.hash_file(self.database)
        try:
            with patch.object(compatibility, "readonly_connection", side_effect=audit_connection):
                return install.activate_staged(self.installed, self.public, allow_rollback=rollback)
        finally:
            self.assertTrue(statements, "Activation must inspect the real workspace fixture")
            self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements), statements)
            self.assertEqual(protocol.hash_file(self.database), before)
            self.assertFalse(list(self.database.parent.glob("*-journal")))

    def test_published_update_and_explicit_historical_rollback_keep_bootstrap_and_database(self):
        old_source, old_release = self.initial_release()
        old_application = install.current_application(self.installed)
        old_archive = self.feed / old_release["package"]["path"]
        old_archive_hash = protocol.hash_file(old_archive)
        bootstrap = self.installed / "bootstrap"
        bootstrap_hashes = {path.relative_to(bootstrap).as_posix(): protocol.hash_file(path)
                            for path in bootstrap.rglob("*") if path.is_file()}
        database_hash = protocol.hash_file(self.database)
        with zipfile.ZipFile(old_archive) as archive:
            self.assertIn("bootstrap/NexusUpdater.exe", archive.namelist())

        new_source = self.distribution("4.1.1", "next-build")
        new_release = self.publish(new_source, 2)
        self.assertEqual(protocol.load_manifest(self.feed / "latest.json", self.public), new_release)
        self.stage()
        self.assertEqual(install.current_application(self.installed), old_application)
        self.assertEqual(self.activate_readonly()["version"], "4.1.1")
        new_application = install.current_application(self.installed)
        self.assertEqual(new_application.read_bytes(), (new_source / "Nexus.exe").read_bytes())

        rollback = self.publish(old_source, 3, rollback=True)
        self.assertEqual(rollback["package"], old_release["package"])
        self.stage(rollback=True)
        self.assertEqual(install.current_application(self.installed), new_application)
        restored = self.activate_readonly(rollback=True)
        self.assertEqual(restored["release_sequence"], 3)
        self.assertEqual(install.current_application(self.installed), old_application)
        self.assertEqual(protocol.hash_file(old_archive), old_archive_hash)
        self.assertEqual(protocol.hash_file(self.database), database_hash)
        self.assertEqual(self.smoke_versions, ["4.1.1", "4.1.0"])
        self.assertEqual({path.relative_to(bootstrap).as_posix(): protocol.hash_file(path)
                          for path in bootstrap.rglob("*") if path.is_file()}, bootstrap_hashes)

    def test_rollback_after_schema_migration_is_rejected_without_restoring_database(self):
        old_source, _ = self.initial_release()
        new_source = self.distribution("4.2.0", "schema-50-build", schema=50)
        self.publish(new_source, 2)
        self.stage()
        # Simulate a coordinated administrator migration of this fixture only.
        connect = getattr(sqlite3, "_nexus_original_connect", sqlite3.connect)
        with closing(connect(self.database)) as connection:
            connection.execute("INSERT INTO schema_migrations VALUES (50)")
            connection.execute("UPDATE part_fixture SET description='New work after migration'")
            connection.commit()
        self.assertEqual(self.activate_readonly()["version"], "4.2.0")
        current = install.current_application(self.installed)
        pointer = (self.installed / "current.json").read_bytes()
        database_hash = protocol.hash_file(self.database)

        self.publish(old_source, 3, rollback=True)
        self.stage(rollback=True)
        with self.assertRaises(compatibility.CompatibilityError) as raised:
            self.activate_readonly(rollback=True)
        self.assertEqual(raised.exception.code, "newer_schema")
        self.assertEqual(install.current_application(self.installed), current)
        self.assertEqual((self.installed / "current.json").read_bytes(), pointer)
        self.assertEqual(protocol.hash_file(self.database), database_hash)
        with closing(connect(self.database.as_uri() + "?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("SELECT description FROM part_fixture").fetchone()[0],
                             "New work after migration")

    def test_feed_archive_tampering_is_rejected_before_smoke_or_activation(self):
        self.initial_release()
        current = install.current_application(self.installed)
        source = self.distribution("4.1.1", "next-build")
        release = self.publish(source, 2)
        archive = self.feed / release["package"]["path"]
        with archive.open("ab") as stream:
            stream.write(b"Network share corruption after publication")
        database_hash = protocol.hash_file(self.database)
        with self.assertRaisesRegex(install.InstallError, "SHA-256"):
            self.stage()
        self.assertFalse(self.smoke_versions)
        self.assertEqual(install.current_application(self.installed), current)
        self.assertFalse((self.installed / "staged.json").exists())
        self.assertEqual(protocol.hash_file(self.database), database_hash)


if __name__ == "__main__":
    unittest.main()
