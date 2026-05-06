from config import DB_NAME
import sqlite3

class RolePermissionRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def get_permissions_for_role(self, role_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.id, p.name 
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                WHERE rp.role_id = ?
            """, (role_id,))
            return cursor.fetchall()

    def add_permission_to_role(self, role_id: int, permission_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)", (role_id, permission_id))
            conn.commit()

    def remove_permission_from_role(self, role_id: int, permission_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM role_permissions WHERE role_id=? AND permission_id=?", (role_id, permission_id))
            conn.commit()
