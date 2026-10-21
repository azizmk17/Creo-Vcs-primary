"""Workspace opening must fail safely before repositories can alter a database."""
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import config
from core.updates.compatibility import (
    CompatibilityError, assert_workspace_compatible, check_database,
    disable_runtime_guard, enable_runtime_guard, guard_connection,
    temporarily_disabled_runtime_guard,
)
from tools import migrate_workspace as admin_migration


class _WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "workspace #1 ü.db"
        self.connect = getattr(sqlite3, "_nexus_original_connect", sqlite3.connect)

    def make_database(self, schema=2, metadata=None, *, ledger=True):
        with contextlib.closing(self.connect(self.database)) as conn, conn:
            conn.execute("CREATE TABLE payload(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO payload(value) VALUES('company data')")
            if ledger:
                conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO schema_migrations(version) VALUES(?)", (schema,))
            if metadata is not None:
                conn.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT)")
                conn.executemany("INSERT INTO app_metadata VALUES(?,?)", metadata.items())

    def check(self, **kwargs):
        return check_database(self.database, app_version="4.1.0", max_schema=2, **kwargs)

    def metadata(self):
        with contextlib.closing(self.connect(self.database)) as conn:
            return dict(conn.execute("SELECT key,value FROM app_metadata"))


class WorkspaceCompatibilityTests(_WorkspaceFixture):
    def test_current_schema_and_version_open_without_modifying_file(self):
        self.make_database(metadata={"minimum_app_version": "4.0.10"})
        before = self.database.read_bytes()
        state = self.check()
        self.assertEqual(state["schema"], 2)
        self.assertFalse(state["needs_migration"])
        self.assertFalse(state["maintenance"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_newer_schema_is_rejected_before_any_write(self):
        self.make_database(schema=3)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(CompatibilityError, "supports schema 2") as result:
            self.check()
        self.assertEqual(result.exception.code, "newer_schema")
        self.assertEqual(self.database.read_bytes(), before)

    def test_minimum_app_version_blocks_old_client_numerically(self):
        self.make_database(metadata={"minimum_app_version": "4.10.0"})
        with self.assertRaisesRegex(CompatibilityError, "requires Nexus 4.10.0") as result:
            self.check()
        self.assertEqual(result.exception.code, "minimum_version")

    def test_pending_schema_requires_administrator_migration(self):
        self.make_database(schema=1)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(CompatibilityError, "administrator migration") as result:
            self.check()
        self.assertEqual(result.exception.code, "pending_migration")
        self.assertTrue(self.check(allow_pending=True)["needs_migration"])
        self.assertEqual(self.database.read_bytes(), before)

    def test_legacy_database_without_ledger_is_schema_zero(self):
        self.make_database(ledger=False)
        with self.assertRaisesRegex(CompatibilityError, "schema 0 to 2"):
            self.check()
        self.assertEqual(self.check(allow_pending=True)["schema"], 0)

    def test_maintenance_blocks_clients_and_admin_preflight(self):
        self.make_database(metadata={"maintenance_mode": "Scheduled database migration"})
        before = self.database.read_bytes()
        for allow_pending in (False, True):
            with self.assertRaisesRegex(CompatibilityError, "maintenance mode") as result:
                self.check(allow_pending=allow_pending)
            self.assertEqual(result.exception.code, "maintenance")
        self.assertEqual(self.database.read_bytes(), before)

    def test_explicit_false_maintenance_flag_is_inactive(self):
        self.make_database(metadata={"maintenance_mode": "false"})
        self.assertFalse(self.check()["maintenance"])

    def test_invalid_minimum_version_fails_closed(self):
        self.make_database(metadata={"minimum_app_version": "unknown"})
        with self.assertRaisesRegex(CompatibilityError, "Invalid application version"):
            self.check()

    def test_missing_database_is_not_created(self):
        with self.assertRaisesRegex(CompatibilityError, "does not exist"):
            self.check()
        self.assertFalse(self.database.exists())

    def test_invalid_database_fails_closed_without_changing_bytes(self):
        self.database.write_bytes(b"this is not a SQLite database")
        before = self.database.read_bytes()
        with self.assertRaises(CompatibilityError):
            self.check()
        self.assertEqual(self.database.read_bytes(), before)

    def test_readonly_probe_bypasses_runtime_pragmas_and_runs_only_selects(self):
        self.make_database()
        statements = []

        def observed_connect(database, *args, **kwargs):
            self.assertIn("mode=ro", database)
            self.assertTrue(kwargs["uri"])
            conn = self.connect(database, *args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(sqlite3, "_nexus_original_connect", observed_connect):
            self.check()
        self.assertTrue(statements)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))

    def test_startup_helper_uses_supported_schema_without_running_migrations(self):
        self.make_database()
        with patch("core.updates.compatibility.latest_schema_version", return_value=2), \
                patch("setup.migrations.migrate", side_effect=AssertionError("must not migrate")):
            self.assertEqual(assert_workspace_compatible(self.database, "4.1.0")["schema"], 2)


class RuntimeConnectionGuardTests(_WorkspaceFixture):
    def setUp(self):
        super().setUp()
        disable_runtime_guard()
        self.addCleanup(disable_runtime_guard)
        self.make_database(metadata={"minimum_app_version": "4.1.0"})
        enable_runtime_guard(self.database, "4.1.0", max_schema=2)

    def set_metadata(self, key, value):
        with contextlib.closing(self.connect(self.database)) as conn, conn:
            conn.execute(
                "INSERT INTO app_metadata(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value),
            )

    def test_active_client_observes_maintenance_before_profile_pragmas(self):
        self.set_metadata("maintenance_mode", "Administrator deployment")
        before = self.database.read_bytes()
        connections = []
        statements = []

        def observed_guard(conn, database):
            connections.append(conn)
            conn.set_trace_callback(statements.append)
            guard_connection(conn, database)

        with patch("core.updates.compatibility.guard_connection", side_effect=observed_guard), \
                patch("config._configure_sqlite_connection") as profile:
            with self.assertRaisesRegex(CompatibilityError, "maintenance mode"):
                sqlite3.connect(self.database)
        profile.assert_not_called()
        self.assertTrue(statements)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT ") for statement in statements))
        with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
            connections[0].execute("SELECT 1")
        self.assertEqual(self.database.read_bytes(), before)

    def test_active_client_observes_raised_version_floor_on_next_connection(self):
        self.set_metadata("minimum_app_version", "4.2.0")
        before = self.database.read_bytes()
        with patch("config._configure_sqlite_connection") as profile:
            with self.assertRaisesRegex(CompatibilityError, "requires Nexus 4.2.0"):
                sqlite3.connect(self.database)
        profile.assert_not_called()
        self.assertEqual(self.database.read_bytes(), before)

    def test_active_client_refuses_schema_changed_by_newer_release(self):
        with contextlib.closing(self.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_migrations VALUES(3)")
        with self.assertRaisesRegex(CompatibilityError, "supports schema 2"):
            sqlite3.connect(self.database)

    def test_unrelated_database_and_memory_connections_are_unaffected(self):
        self.set_metadata("maintenance_mode", "on")
        for database in (self.root / "unrelated.db", ":memory:"):
            with contextlib.closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

    def test_uri_path_cannot_bypass_workspace_identity(self):
        self.set_metadata("maintenance_mode", "on")
        with self.assertRaisesRegex(CompatibilityError, "maintenance mode"):
            sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True)

    def test_disabled_guard_can_be_reenabled_for_a_workspace(self):
        disable_runtime_guard()
        self.set_metadata("maintenance_mode", "on")
        with contextlib.closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
        self.set_metadata("maintenance_mode", "off")
        enable_runtime_guard(self.database, "4.1.0", max_schema=2)
        self.set_metadata("maintenance_mode", "on")
        with self.assertRaises(CompatibilityError):
            sqlite3.connect(self.database)

    def test_admin_exemption_is_thread_local_and_restored_on_exit(self):
        self.set_metadata("maintenance_mode", "on")
        errors = []

        def another_client_thread():
            try:
                sqlite3.connect(self.database)
            except CompatibilityError as exc:
                errors.append(exc.code)

        with temporarily_disabled_runtime_guard():
            with contextlib.closing(sqlite3.connect(self.database)) as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            worker = threading.Thread(target=another_client_thread)
            worker.start()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["maintenance"])
        with self.assertRaises(CompatibilityError):
            sqlite3.connect(self.database)


class AdministratorMigrationTests(_WorkspaceFixture):
    """Exercise administrator actions against isolated fixture databases only."""

    def setUp(self):
        super().setUp()
        self.backups = self.root / "backups"
        self.original_database = os.fspath(config.DB_NAME)
        self.addCleanup(config.DB_NAME.set, self.original_database)

    def migrate(self, **kwargs):
        settings = {"maintenance": True, "confirm_clients_closed": True}
        settings.update(kwargs)
        with patch.object(admin_migration, "latest_schema_version", return_value=2):
            return admin_migration.migrate_workspace(
                self.database, self.backups, config.APP_VERSION, **settings,
            )

    def apply_schema_two(self):
        self.assertEqual(Path(os.fspath(config.DB_NAME)).resolve(), self.database.resolve())
        self.assertTrue(self.metadata()["maintenance_mode"])
        self.assertTrue((self.database.parent / ".nexus-migration.lock").is_file())
        with contextlib.closing(self.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO schema_migrations VALUES(2)")
            conn.execute("ALTER TABLE payload ADD COLUMN migrated INTEGER DEFAULT 1")

    def test_explicit_client_shutdown_and_maintenance_confirmation_required(self):
        self.make_database(schema=1)
        before = self.database.read_bytes()
        for kwargs in ({"maintenance": False}, {"confirm_clients_closed": False}):
            with self.assertRaisesRegex(ValueError, "Close every Nexus"):
                self.migrate(**kwargs)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.backups.exists())

    def test_success_creates_consistent_backup_and_records_version_floor(self):
        self.make_database(schema=1)
        with patch.object(admin_migration, "_run_migrations", side_effect=self.apply_schema_two) as runner:
            result = self.migrate()
        runner.assert_called_once_with()
        self.assertEqual(result["schema"], 2)
        self.assertFalse(result["maintenance"])
        metadata = self.metadata()
        self.assertEqual(metadata["minimum_app_version"], config.APP_VERSION)
        self.assertEqual(metadata["db_schema_version"], "2")
        self.assertNotIn("maintenance_mode", metadata)
        backup = Path(result["backup"])
        self.assertEqual(backup.parent, self.backups.resolve())
        with contextlib.closing(self.connect(backup)) as conn:
            self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT value FROM payload").fetchone()[0], "company data")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertTrue(conn.execute("SELECT value FROM app_metadata WHERE key='maintenance_mode'").fetchone()[0])
        self.assertFalse((self.root / ".nexus-migration.lock").exists())
        self.assertEqual(os.fspath(config.DB_NAME), self.original_database)

    def test_failed_migration_preserves_backup_and_maintenance_without_restoring(self):
        self.make_database(schema=1)

        def failing_migration():
            with contextlib.closing(self.connect(self.database)) as conn, conn:
                conn.execute("UPDATE payload SET value='partial migration'")
            raise RuntimeError("migration test failure")

        with patch.object(admin_migration, "_run_migrations", side_effect=failing_migration):
            with self.assertRaisesRegex(RuntimeError, "Verified backup preserved"):
                self.migrate()
        backups = list(self.backups.glob("*.db"))
        self.assertEqual(len(backups), 1)
        with contextlib.closing(self.connect(backups[0])) as conn:
            self.assertEqual(conn.execute("SELECT value FROM payload").fetchone()[0], "company data")
        with contextlib.closing(self.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM payload").fetchone()[0], "partial migration")
        self.assertIn("failed", self.metadata()["maintenance_mode"].lower())
        self.assertFalse((self.root / ".nexus-migration.lock").exists())
        self.assertEqual(os.fspath(config.DB_NAME), self.original_database)

    def test_incomplete_migration_never_clears_maintenance(self):
        self.make_database(schema=1)
        with patch.object(admin_migration, "_run_migrations"):
            with self.assertRaisesRegex(RuntimeError, "expected 2"):
                self.migrate()
        self.assertTrue(self.metadata()["maintenance_mode"])

    def test_existing_migration_lock_cannot_be_overwritten(self):
        self.make_database(schema=1)
        lock = self.root / ".nexus-migration.lock"
        lock.write_text("another migration")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "lock already exists"):
            self.migrate()
        self.assertEqual(lock.read_text(), "another migration")
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.backups.exists())

    def test_newer_schema_is_refused_before_lock_backup_or_maintenance(self):
        self.make_database(schema=3)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(CompatibilityError, "supports schema 2"):
            self.migrate()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.root / ".nexus-migration.lock").exists())

    def test_minimum_version_cannot_be_lowered(self):
        self.make_database(schema=1, metadata={"minimum_app_version": config.APP_VERSION})
        with patch.object(admin_migration, "latest_schema_version", return_value=2):
            with self.assertRaisesRegex(ValueError, "must not lower"):
                admin_migration.migrate_workspace(
                    self.database, self.backups, "2.2.0", maintenance=True,
                    confirm_clients_closed=True,
                )
        self.assertFalse(self.backups.exists())

    def test_floor_is_rechecked_after_acquiring_migration_lock(self):
        self.make_database(schema=1, metadata={"minimum_app_version": "2.2.0"})
        initial = check_database(self.database, app_version=config.APP_VERSION, max_schema=2, allow_pending=True)
        changed = dict(initial, minimum_app_version=config.APP_VERSION)
        before = self.database.read_bytes()
        with patch.object(admin_migration, "latest_schema_version", return_value=2), \
                patch.object(admin_migration, "check_database", side_effect=[initial, changed]):
            with self.assertRaisesRegex(ValueError, "must not lower"):
                admin_migration.migrate_workspace(
                    self.database, self.backups, "2.2.0", maintenance=True,
                    confirm_clients_closed=True,
                )
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(self.backups.exists())
        self.assertFalse((self.root / ".nexus-migration.lock").exists())

    def test_check_cli_is_read_only_and_does_not_require_confirmation(self):
        self.make_database(schema=1)
        before = self.database.read_bytes()
        with patch.object(admin_migration, "latest_schema_version", return_value=2), \
                patch.object(admin_migration, "_run_migrations", side_effect=AssertionError("must not migrate")), \
                contextlib.redirect_stdout(io.StringIO()):
            status = admin_migration.main(["--db", str(self.database), "--check"])
        self.assertEqual(status, 0)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse((self.root / ".nexus-migration.lock").exists())

    def test_empty_workspace_runs_the_real_supported_migration_chain(self):
        with contextlib.closing(self.connect(self.database)):
            pass
        with contextlib.redirect_stdout(io.StringIO()):
            result = admin_migration.migrate_workspace(
                self.database, self.backups, config.APP_VERSION,
                maintenance=True, confirm_clients_closed=True,
            )
        self.assertEqual(result["schema"], admin_migration.latest_schema_version())
        self.assertFalse(result["needs_migration"])
        self.assertFalse(result["maintenance"])
        with contextlib.closing(self.connect(self.database)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"users", "bom", "cad_documents"}.issubset(tables))


if __name__ == "__main__":
    unittest.main()
