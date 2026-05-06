# core/repositories/permission_repository.py
import sqlite3
from typing import Optional
from config import DB_NAME

class PermissionRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

        self._role_level = {
            "designer": 1,
            "checker": 2,
            "master": 3,
            "admin": 4,
        }

        self._permission_min_role = {
            "commit": "designer",
            "manage_parts": "checker",   # add/edit/delete parts, add child
            "validate": "checker",
            "merge": "master",
            "admin_panel": "admin",      # admin tab visibility
        }

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_has_column(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        try:
            cur = conn.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]  # (cid, name, type, notnull, dflt_value, pk)
            return column in cols
        except Exception:
            return False

    def _user_is_admin_flag(self, conn: sqlite3.Connection, user_id: int) -> bool:
        if not self._table_has_column(conn, "users", "is_admin"):
            return False
        try:
            cur = conn.execute("SELECT is_admin FROM users WHERE id = ?", (int(user_id),))
            row = cur.fetchone()
            return bool(row and int(row[0] or 0) == 1)
        except Exception:
            return False

    def _user_max_role_level(self, conn: sqlite3.Connection, user_id: int, project_id: Optional[int]) -> int:
        try:
            cur = conn.execute(
                """
                SELECT r.name
                FROM user_roles ur
                JOIN roles r ON ur.role_id = r.id
                WHERE ur.user_id = ?
                  AND (ur.project_id IS NULL OR ur.project_id = ?)
                """,
                (int(user_id), project_id),
            )
            names = [str(r[0] or "").strip().lower() for r in cur.fetchall()]
            level = 0
            for n in names:
                level = max(level, int(self._role_level.get(n, 0)))
            return level
        except Exception:
            return 0

    def user_has_permission(self, user_id: int, permission_name: str, project_id: Optional[int] = None) -> bool:
        pname = (permission_name or "").strip().lower()
        query = """
        SELECT 1
        FROM user_roles ur
        JOIN role_permissions rp ON ur.role_id = rp.role_id
        JOIN permissions p ON rp.permission_id = p.id
        WHERE ur.user_id = ?
          AND p.name = ?
          AND (ur.project_id IS NULL OR ur.project_id = ?)
          AND (rp.project_id IS NULL OR rp.project_id = ?)
        LIMIT 1
        """
        with self.get_conn() as conn:
            # Enforce role hierarchy for key actions (independent of role_permissions config).
            if self._user_is_admin_flag(conn, user_id):
                return True

            user_level = self._user_max_role_level(conn, user_id, project_id)
            if user_level >= self._role_level["admin"]:
                return True

            min_role = self._permission_min_role.get(pname)
            if min_role:
                return user_level >= int(self._role_level.get(min_role, 99))

            params = (user_id, permission_name, project_id, project_id)
            cur = conn.cursor()
            cur.execute(query, params)
            return cur.fetchone() is not None
        
    def get_all(self):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM permissions")
            rows = cursor.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]

    def create_permission(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Permission name is required")
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO permissions (name) VALUES (?)", (name,))
            conn.commit()
            return int(cur.lastrowid)

    def delete_permission(self, permission_id: int) -> None:
        with self.get_conn() as conn:
            # Best-effort: remove mappings first
            try:
                conn.execute("DELETE FROM role_permissions WHERE permission_id = ?", (int(permission_id),))
            except Exception:
                pass
            conn.execute("DELETE FROM permissions WHERE id = ?", (int(permission_id),))
            conn.commit()
