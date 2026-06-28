import os
import sqlite3
from pathlib import Path

from config import APP_VERSION, DB_NAME
from core import workspace_config
from core.repositories.user_role_repository import UserRoleRepository
from core.repositories.role_permission_repository import RolePermissionRepository

class AdminService:
    def __init__(self):
        self.user_role_repo = UserRoleRepository()
        self.role_perm_repo = RolePermissionRepository()

    # User ↔ Role
    def assign_role_to_user(self, user_id: int, role_id: int):
        self.user_role_repo.assign_role(user_id, role_id)

    def remove_role_from_user(self, user_id: int, role_id: int):
        self.user_role_repo.remove_role(user_id, role_id)

    def get_roles_for_user(self, user_id: int):
        return self.user_role_repo.get_roles_for_user(user_id)

    # Role ↔ Permission
    def add_permission_to_role(self, role_id: int, permission_id: int):
        self.role_perm_repo.add_permission_to_role(role_id, permission_id)

    def remove_permission_from_role(self, role_id: int, permission_id: int):
        self.role_perm_repo.remove_permission_from_role(role_id, permission_id)

    def get_permissions_for_role(self, role_id: int):
        return self.role_perm_repo.get_permissions_for_role(role_id)

    # Configuration / metadata
    def get_configuration_paths(self):
        db_path = Path(os.fspath(DB_NAME)).resolve()
        config_path = workspace_config._config_file()
        appdata_root = config_path.parent
        env_license = os.environ.get("CREOVCS_LICENSE_PATH", "").strip()
        appdata_license = appdata_root / "creovcs.lic"
        license_path = Path(env_license) if env_license else (
            appdata_license if appdata_license.exists() else Path("creovcs.lic")
        )
        license_path = license_path.expanduser()
        if not license_path.is_absolute():
            license_path = license_path.resolve()

        rows = [
            {
                "name": "Database file",
                "path": str(db_path),
                "kind": "file",
                "status": "Exists" if db_path.is_file() else "Missing",
            },
            {
                "name": "Database directory",
                "path": str(db_path.parent),
                "kind": "directory",
                "status": "Exists" if db_path.parent.is_dir() else "Missing",
            },
            {
                "name": "License file",
                "path": str(license_path),
                "kind": "file",
                "status": "Exists" if license_path.is_file() else "Missing",
            },
            {
                "name": "License directory",
                "path": str(license_path.parent),
                "kind": "directory",
                "status": "Exists" if license_path.parent.is_dir() else "Missing",
            },
            {
                "name": "Workspace config",
                "path": str(config_path),
                "kind": "file",
                "status": "Exists" if config_path.is_file() else "Missing",
            },
            {
                "name": "App data directory",
                "path": str(appdata_root),
                "kind": "directory",
                "status": "Exists" if appdata_root.is_dir() else "Missing",
            },
            {
                "name": "Revocation file",
                "path": str(db_path.parent / "revoked.json"),
                "kind": "file",
                "status": "Exists" if (db_path.parent / "revoked.json").is_file() else "Missing",
            },
        ]
        return {
            "app_version": APP_VERSION,
            "license_source": "Environment override" if env_license else "Default",
            "paths": rows,
        }

    def _metadata_connection(self):
        conn = sqlite3.connect(DB_NAME)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        return conn

    def list_app_metadata(self):
        with self._metadata_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM app_metadata ORDER BY key COLLATE NOCASE")
            return [{"key": key, "value": value or ""} for key, value in cur.fetchall()]

    def set_app_metadata(self, key: str, value: str):
        key = str(key or "").strip()
        if not key:
            raise ValueError("Metadata key is required.")
        with self._metadata_connection() as conn:
            conn.execute(
                """
                INSERT INTO app_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value or "")),
            )
            conn.commit()

    def delete_app_metadata(self, key: str):
        key = str(key or "").strip()
        if not key:
            raise ValueError("Metadata key is required.")
        with self._metadata_connection() as conn:
            conn.execute("DELETE FROM app_metadata WHERE key = ?", (key,))
            conn.commit()
