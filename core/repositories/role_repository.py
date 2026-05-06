from config import DB_NAME
import sqlite3

class RoleRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all(self):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM roles")
            rows = cursor.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]

    def create_role(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("Role name is required")
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO roles (name) VALUES (?)", (name,))
            conn.commit()
            return int(cur.lastrowid)

    def rename_role(self, role_id: int, new_name: str) -> None:
        new_name = (new_name or "").strip()
        if not new_name:
            raise ValueError("Role name is required")
        with self.get_conn() as conn:
            conn.execute("UPDATE roles SET name = ? WHERE id = ?", (new_name, int(role_id)))
            conn.commit()

    def delete_role(self, role_id: int) -> None:
        with self.get_conn() as conn:
            # Best-effort: remove mappings first
            try:
                conn.execute("DELETE FROM user_roles WHERE role_id = ?", (int(role_id),))
            except Exception:
                pass
            try:
                conn.execute("DELETE FROM role_permissions WHERE role_id = ?", (int(role_id),))
            except Exception:
                pass
            conn.execute("DELETE FROM roles WHERE id = ?", (int(role_id),))
            conn.commit()
        
    def get_roles_for_user(self, user_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.id, r.name
                FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = ?
            """, (user_id,))
            rows = cursor.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]

    def get_permissions(self, role_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.id, p.name
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                WHERE rp.role_id = ?
            """, (role_id,))
            rows = cursor.fetchall()
            return [{"id": r[0], "name": r[1]} for r in rows]

    def add_permission(self, role_id, permission_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                (role_id, permission_id),
            )
            conn.commit()

    def remove_permission(self, role_id, permission_id):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM role_permissions WHERE role_id = ? AND permission_id = ?",
                (role_id, permission_id),
            )
            conn.commit()
