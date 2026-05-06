from config import DB_NAME
import sqlite3

class UserRoleRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def get_roles_for_user(self, user_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.id, r.name 
                FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = ?
            """, (user_id,))
            return cursor.fetchall()

    def assign_role(self, user_id: int, role_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
            conn.commit()

    def remove_role(self, user_id: int, role_id: int):
        with self.get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_roles WHERE user_id=? AND role_id=?", (user_id, role_id))
            conn.commit()
