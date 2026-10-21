import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.updates import protocol


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("nexus_release_publisher", ROOT / "tools/licensing/update_version.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


class SignedReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.private, self.public = publisher.generate_release_keypair(self.directory / "keys")
        self.public_hex = self.public.read_text(encoding="ascii").strip()
        self.now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        self.package = self.directory / "Nexus"
        self.feed = self.directory / "feed"
        self.make_package()

    def make_package(self, *, version="4.1.0", build_id="fixture-build", public_key=None, with_setup=None):
        for relative in ("Nexus.exe", "CADViewer.exe", "_internal/runtime.dll",
                         "bootstrap/NexusLauncher.exe", "bootstrap/NexusUpdater.exe", "bootstrap/_internal/runtime.dll"):
            path = self.package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture " + relative).encode("ascii"))
        report = {"ok": True, "frozen": True,
                  "checks": {name: {"ok": True} for name in ("pdf", "step", "qt_viewer", "application")}}
        manifest = {"app": "Nexus", "mode": "onedir", "version": version, "build_id": build_id,
                    "release_public_key": self.public_hex if public_key is None else public_key,
                    "supported_schema": {"min": 49, "max": 50},
                    "bootstrap_tests": {name: {"ok": True, "frozen": True, "release_public_key": self.public_hex}
                                        for name in ("NexusLauncher.exe", "NexusUpdater.exe")},
                    "smoke_tests": {name: deepcopy(report) for name in ("Nexus.exe", "CADViewer.exe")}}
        if protocol.version_tuple(version) >= (4, 2, 0) and with_setup is not False:
            (self.package / "Setup.exe").write_bytes(b"setup executable")
            manifest["setup_test"] = {"ok": True, "frozen": True, "gui": True,
                                      "release_public_key": self.public_hex}
        else:
            (self.package / "Setup.exe").unlink(missing_ok=True)
        (self.package / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def payload(self):
        return {"schema": 1, "product": "Nexus", "platform": "windows-x64", "version": "4.1.0",
                "build_id": "test-build", "release_sequence": 4,
                "expires_at": (self.now + timedelta(days=10)).isoformat(),
                "package": {"path": "releases/Nexus-4.1.0-test-build.zip", "size": 12, "sha256": "a" * 64},
                "supported_schema": {"min": 49, "max": 50}, "min_updater_version": "1.0.0",
                "release_notes": "PDF and CAD improvements"}

    def publish(self, sequence=1, version="4.1.0", **kwargs):
        return publisher.publish_release(self.package, self.feed, self.private, version=version,
                                         release_sequence=sequence, schema_min=49, schema_max=50, now=self.now, **kwargs)

    def test_signature_round_trip_binds_every_package_field(self):
        payload = self.payload()
        envelope = protocol.sign_manifest(payload, self.private)
        self.assertEqual(protocol.verify_manifest(envelope, self.public_hex, now=self.now), payload)
        altered = deepcopy(envelope)
        altered["signed"]["package"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "signature"):
            protocol.verify_manifest(altered, self.public_hex, now=self.now)

    def test_wrong_key_and_expired_or_replayed_metadata_are_rejected(self):
        envelope = protocol.sign_manifest(self.payload(), self.private)
        _, other_public = publisher.generate_release_keypair(self.directory / "other-keys")
        with self.assertRaisesRegex(ValueError, "signature"):
            protocol.verify_manifest(envelope, other_public.read_text().strip(), now=self.now)
        with self.assertRaisesRegex(ValueError, "older"):
            protocol.verify_manifest(envelope, self.public_hex, last_sequence=5, now=self.now)
        self.assertEqual(protocol.verify_manifest(envelope, self.public_hex, last_sequence=4, now=self.now)["release_sequence"], 4)
        with self.assertRaisesRegex(ValueError, "expired"):
            protocol.verify_manifest(envelope, self.public_hex, now=self.now + timedelta(days=11))

    def test_manifest_paths_reject_urls_traversal_and_windows_aliases(self):
        for path in ("https://server/file.zip", "../file.zip", "releases/../file.zip", "C:/file.zip",
                     "//server/share.zip", "releases\\file.zip", "releases/file.zip:stream", "releases/CON.zip",
                     "releases/file.zip.", "releases//file.zip"):
            with self.subTest(path=path):
                payload = self.payload()
                payload["package"]["path"] = path
                with self.assertRaises(ValueError):
                    protocol.validate_payload(payload)

    def test_manifest_product_schema_and_numeric_versions_are_strict(self):
        for field, value in (("product", "Other"), ("platform", "linux-x64"), ("schema", True),
                             ("version", "4.1.0-beta"), ("version", "04.1.0"), ("release_sequence", True),
                             ("build_id", "../outside"), ("build_id", "alias."), ("min_updater_version", "1.0")):
            with self.subTest(field=field, value=value):
                payload = self.payload()
                payload[field] = value
                with self.assertRaises(ValueError):
                    protocol.validate_payload(payload)
        payload = self.payload()
        payload["supported_schema"] = {"min": 50, "max": 49}
        with self.assertRaises(ValueError):
            protocol.validate_payload(payload)
        self.assertLess(protocol.version_tuple("4.9.9"), protocol.version_tuple("4.10.0"))

    def test_oversized_and_duplicate_json_metadata_are_rejected(self):
        path = self.directory / "latest.json"
        path.write_bytes(b" " * (protocol.MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            protocol.load_manifest(path, self.public_hex, now=self.now)
        path.write_text('{"signed":{},"signed":{},"signature":"bad"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            protocol.load_manifest(path, self.public_hex, now=self.now)

    def test_publish_creates_verifiable_immutable_archive_with_direct_root(self):
        payload = self.publish()
        verified = protocol.load_manifest(self.feed / "latest.json", self.public_hex, now=self.now)
        self.assertEqual(payload, verified)
        archive = protocol.resolve_package_path(self.feed, payload["package"]["path"])
        self.assertEqual(protocol.hash_file(archive), payload["package"]["sha256"])
        self.assertEqual(archive.stat().st_size, payload["package"]["size"])
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn("Nexus.exe", bundle.namelist())
            self.assertIn("bootstrap/NexusUpdater.exe", bundle.namelist())
            self.assertFalse(any(name.startswith("Nexus/") for name in bundle.namelist()))
        self.assertFalse((self.feed / ".publish.lock").exists())
        self.assertFalse(list(self.feed.rglob("*.tmp")))

    def test_new_releases_require_the_setup_executable_and_successful_gui_check(self):
        self.make_package(version="4.2.0", with_setup=False)
        with self.assertRaisesRegex(ValueError, "Setup.exe"):
            self.publish(version="4.2.0")
        (self.package / "Setup.exe").write_bytes(b"setup executable")
        manifest_path = self.package / "build_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["setup_test"] = {"ok": True, "frozen": True, "gui": True,
                                  "release_public_key": self.public_hex}
        manifest_path.write_text(json.dumps(manifest))
        payload = self.publish(version="4.2.0")
        with zipfile.ZipFile(self.feed / payload["package"]["path"]) as archive:
            self.assertIn("Setup.exe", archive.namelist())

    def test_sequence_or_version_downgrade_preserves_existing_pointer_and_archive(self):
        payload = self.publish(sequence=10)
        pointer = (self.feed / "latest.json").read_bytes()
        digest = payload["package"]["sha256"]
        for sequence in (9, 10):
            with self.subTest(sequence=sequence), self.assertRaisesRegex(ValueError, "sequence"):
                self.publish(sequence=sequence)
        self.make_package(version="4.0.9", build_id="older")
        with self.assertRaisesRegex(ValueError, "older"):
            self.publish(sequence=11, version="4.0.9")
        self.assertEqual((self.feed / "latest.json").read_bytes(), pointer)
        self.assertEqual(protocol.hash_file(self.feed / payload["package"]["path"]), digest)

    def test_republishing_identical_build_is_rejected_even_with_new_sequence(self):
        self.publish()
        with self.assertRaisesRegex(FileExistsError, "immutable"):
            self.publish(sequence=2)

    def test_explicit_rollback_reuses_identical_immutable_archive_with_new_sequence(self):
        original = self.publish()
        original_path = self.feed / original["package"]["path"]
        original_bytes = original_path.read_bytes()
        self.make_package(version="4.2.0", build_id="newer")
        self.publish(sequence=2, version="4.2.0")
        self.make_package()
        rollback = self.publish(sequence=3, allow_rollback=True)
        self.assertEqual(rollback["package"], original["package"])
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(rollback["release_sequence"], 3)
        self.assertEqual(len(list((self.feed / "releases").glob("*.zip"))), 2)
        self.assertEqual(protocol.load_manifest(self.feed / "latest.json", self.public_hex, now=self.now), rollback)
        with self.assertRaisesRegex(ValueError, "sequence"):
            self.publish(sequence=2, allow_rollback=True)

    def test_rollback_never_reuses_same_identity_with_different_bits(self):
        original = self.publish()
        original_path = self.feed / original["package"]["path"]
        original_hash = protocol.hash_file(original_path)
        self.make_package(version="4.2.0", build_id="newer")
        self.publish(sequence=2, version="4.2.0")
        pointer = (self.feed / "latest.json").read_bytes()
        self.make_package()
        (self.package / "Nexus.exe").write_bytes(b"different executable")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.publish(sequence=3, allow_rollback=True)
        self.assertEqual(protocol.hash_file(original_path), original_hash)
        self.assertEqual((self.feed / "latest.json").read_bytes(), pointer)

    def test_cli_explicit_rollback_announces_historical_archive(self):
        original = self.publish()
        self.make_package(version="4.2.0", build_id="newer")
        self.publish(sequence=2, version="4.2.0")
        self.make_package()
        result = publisher.main(["--package-dir", str(self.package), "--feed-dir", str(self.feed),
                                 "--release-key", str(self.private), "--sequence", "3", "--version", "4.1.0",
                                 "--schema-min", "49", "--schema-max", "50", "--allow-rollback"])
        self.assertEqual(result, 0)
        payload = protocol.load_manifest(self.feed / "latest.json", self.public_hex)
        self.assertEqual(payload["package"], original["package"])
        self.assertEqual(payload["release_sequence"], 3)

    def test_expired_latest_still_prevents_sequence_rollback(self):
        self.publish(sequence=10)
        with self.assertRaisesRegex(ValueError, "sequence"):
            publisher.publish_release(self.package, self.feed, self.private, version="4.1.0",
                                      release_sequence=9, schema_min=49, schema_max=50,
                                      now=self.now + timedelta(days=100))

    def test_announcement_switch_happens_after_archive_hash_verification(self):
        original_write = publisher._write_atomic

        def check_archive_first(path, content):
            envelope = json.loads(content)
            package = envelope["signed"]["package"]
            archive = self.feed / package["path"]
            self.assertTrue(archive.is_file())
            self.assertEqual(protocol.hash_file(archive), package["sha256"])
            original_write(path, content)

        with patch.object(publisher, "_write_atomic", side_effect=check_archive_first):
            self.publish()

    def test_unsmoked_or_wrong_pinned_key_build_never_creates_feed(self):
        path = self.package / "build_manifest.json"
        original = path.read_text(encoding="utf-8")
        build = json.loads(original)
        build["smoke_tests"]["CADViewer.exe"]["checks"]["application"]["ok"] = False
        path.write_text(json.dumps(build), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "four"):
            self.publish()
        self.assertFalse(self.feed.exists())
        path.write_text(original, encoding="utf-8")
        build = json.loads(original)
        build["release_public_key"] = "0" * 64
        path.write_text(json.dumps(build), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "pinned"):
            self.publish()
        self.assertFalse(self.feed.exists())

    def test_version_must_match_built_metadata(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.publish(version="4.2.0")

    def test_schema_advertisement_and_bootstrap_trust_must_match_build(self):
        with self.assertRaisesRegex(ValueError, "schema bounds"):
            publisher.publish_release(self.package, self.feed, self.private, version="4.1.0",
                                      release_sequence=1, schema_min=0, schema_max=999, now=self.now)
        path = self.package / "build_manifest.json"
        build = json.loads(path.read_text(encoding="utf-8"))
        build["bootstrap_tests"]["NexusUpdater.exe"]["release_public_key"] = "0" * 64
        path.write_text(json.dumps(build), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "self-test"):
            self.publish()
        self.assertFalse(self.feed.exists())

    def test_private_files_and_symlinks_are_not_packaged(self):
        for name in ("company.db", "company.db-wal", "creovcs.lic", "release_private.key", "secret.pem"):
            with self.subTest(name=name):
                path = self.package / name
                path.write_text("private", encoding="utf-8")
                try:
                    with self.assertRaisesRegex(ValueError, "Private"):
                        self.publish()
                finally:
                    path.unlink()
        path = self.package / "linked.dll"
        outside = self.directory / "outside.dll"
        outside.write_bytes(b"outside")
        try:
            path.symlink_to(outside)
        except OSError:
            # Windows may lack developer-mode link privileges; exercise the
            # same traversal guard independently of host link permissions.
            with patch.object(publisher, "_is_link", side_effect=lambda item: item == self.package / "Nexus.exe"):
                with self.assertRaisesRegex(ValueError, "symbolic"):
                    self.publish()
        else:
            with self.assertRaisesRegex(ValueError, "symbolic"):
                self.publish()

    def test_public_ca_certificates_are_allowed_but_mixed_private_pem_is_rejected(self):
        certificate = self.package / "_internal" / "cacert.pem"
        certificate.write_bytes(b"-----BEGIN CERTIFICATE-----\npublic certificate\n-----END CERTIFICATE-----\n")
        self.assertIn(certificate, publisher._source_files(self.package))
        with certificate.open("ab") as stream:
            stream.write(b"-----BEGIN ENCRYPTED PRIVATE KEY-----\nsecret\n-----END ENCRYPTED PRIVATE KEY-----\n")
        with self.assertRaisesRegex(ValueError, "Private"):
            self.publish()

    def test_failed_archive_validation_never_changes_latest_pointer(self):
        self.publish()
        pointer = (self.feed / "latest.json").read_bytes()
        self.make_package(version="4.1.1", build_id="next-build")
        with patch.object(publisher.zipfile.ZipFile, "testzip", return_value="runtime.dll"):
            with self.assertRaisesRegex(ValueError, "ZIP verification"):
                self.publish(sequence=2, version="4.1.1")
        self.assertEqual((self.feed / "latest.json").read_bytes(), pointer)
        self.assertFalse(list(self.feed.rglob("*.tmp")))

    def test_feed_inside_source_and_concurrent_publisher_lock_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            publisher.publish_release(self.package, self.package / "feed", self.private, version="4.1.0",
                                      release_sequence=1, schema_min=49, schema_max=50, now=self.now)
        self.feed.mkdir()
        lock = self.feed / ".publish.lock"
        lock.write_text("another publisher")
        with self.assertRaises(FileExistsError):
            self.publish()
        self.assertEqual(lock.read_text(), "another publisher")

    def test_keys_never_overwrite_existing_material(self):
        original = self.private.read_bytes()
        with self.assertRaises(FileExistsError):
            publisher.generate_release_keypair(self.private.parent)
        self.assertEqual(self.private.read_bytes(), original)

    def test_legacy_cli_keeps_original_database_signature_format(self):
        database = self.directory / "legacy.db"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE unrelated(value TEXT)")
        self.assertEqual(publisher.main(["--version", "4.1.0", "--private-key", str(self.private), "--db", str(database)]), 0)
        with sqlite3.connect(database) as connection:
            values = dict(connection.execute("SELECT key,value FROM app_metadata"))
            self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='unrelated'").fetchone())
        self.assertEqual(values["latest_version"], "4.1.0")
        signature = base64.urlsafe_b64decode(values["latest_version_sig"] + "==")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public_hex)).verify(
            signature, protocol.canonical_json({"latest_version": "4.1.0"}))
        self.assertEqual(set(values), {"latest_version", "latest_version_sig"})

    def test_new_cli_can_publish_without_opening_any_database(self):
        with patch.object(publisher.sqlite3, "connect", side_effect=AssertionError("must not access DB")):
            result = publisher.main(["--package-dir", str(self.package), "--feed-dir", str(self.feed),
                                     "--release-key", str(self.private), "--sequence", "1", "--version", "4.1.0",
                                     "--schema-min", "49", "--schema-max", "50"])
        self.assertEqual(result, 0)
        self.assertEqual(protocol.load_manifest(self.feed / "latest.json", self.public_hex)["version"], "4.1.0")


if __name__ == "__main__":
    unittest.main()
