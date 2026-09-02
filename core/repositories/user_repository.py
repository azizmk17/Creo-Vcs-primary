import sqlite3
from config import DB_NAME
from core.models.user_model import User

class UserRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_user_settings_table(self, conn) -> None:
        """Best-effort schema helper for per-user settings."""
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    last_project_id INTEGER,
                    cli_enabled INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            cols = self._table_columns(conn, "user_settings")
            if "cli_enabled" not in cols:
                conn.execute(
                    "ALTER TABLE user_settings ADD COLUMN cli_enabled INTEGER DEFAULT 0"
                )
        except Exception:
            pass

    def set_last_project_id(self, user_id: int, project_id: int | None) -> None:
        with self.get_conn() as conn:
            self._ensure_user_settings_table(conn)
            try:
                conn.execute(
                    """
                    INSERT INTO user_settings(user_id, last_project_id, updated_at)
                    VALUES(?, ?, datetime('now'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        last_project_id=excluded.last_project_id,
                        updated_at=datetime('now')
                    """,
                    (int(user_id), (int(project_id) if project_id is not None else None)),
                )
            except Exception:
                # SQLite < 3.24 may not support ON CONFLICT DO UPDATE.
                try:
                    conn.execute(
                        "UPDATE user_settings SET last_project_id = ?, updated_at = datetime('now') WHERE user_id = ?",
                        ((int(project_id) if project_id is not None else None), int(user_id)),
                    )
                    if conn.total_changes == 0:
                        conn.execute(
                            "INSERT INTO user_settings(user_id, last_project_id, updated_at) VALUES(?, ?, datetime('now'))",
                            (int(user_id), (int(project_id) if project_id is not None else None)),
                        )
                except Exception:
                    pass

    def get_last_project_id(self, user_id: int) -> int | None:
        with self.get_conn() as conn:
            self._ensure_user_settings_table(conn)
            try:
                cur = conn.cursor()
                cur.execute("SELECT last_project_id FROM user_settings WHERE user_id = ?", (int(user_id),))
                row = cur.fetchone()
                if not row:
                    return None
                val = row[0]
                return int(val) if val is not None else None
            except Exception:
                return None

    def set_cli_enabled(self, user_id: int, enabled: bool) -> None:
        """Enable/disable the controlled Nexus CLI panel for one user."""
        with self.get_conn() as conn:
            self._ensure_user_settings_table(conn)
            try:
                conn.execute(
                    """
                    INSERT INTO user_settings(user_id, cli_enabled, updated_at)
                    VALUES(?, ?, datetime('now'))
                    ON CONFLICT(user_id) DO UPDATE SET
                        cli_enabled=excluded.cli_enabled,
                        updated_at=datetime('now')
                    """,
                    (int(user_id), 1 if enabled else 0),
                )
            except Exception:
                cur = conn.execute(
                    "UPDATE user_settings SET cli_enabled=?, updated_at=datetime('now') WHERE user_id=?",
                    (1 if enabled else 0, int(user_id)),
                )
                if getattr(cur, "rowcount", 0) == 0:
                    conn.execute(
                        "INSERT INTO user_settings(user_id, cli_enabled, updated_at) VALUES(?, ?, datetime('now'))",
                        (int(user_id), 1 if enabled else 0),
                    )

    def is_cli_enabled(self, user_id: int) -> bool:
        """Return whether the controlled Nexus CLI panel is enabled for a user."""
        with self.get_conn() as conn:
            self._ensure_user_settings_table(conn)
            row = conn.execute(
                "SELECT cli_enabled FROM user_settings WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
            return bool(row and int(row["cli_enabled"] or 0))

    def _table_columns(self, conn, table_name: str) -> set[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            return {str(r[1]) for r in rows}
        except Exception:
            return set()

    def create(self, username: str, email: str, password: str, is_admin: int = 0, role_id: int | None = None) -> int:
        """Insert a new user and return its ID."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            cur = conn.cursor()
            if "is_admin" in cols and "role_id" in cols:
                cur.execute(
                    "INSERT INTO users (username, email, password, is_admin, role_id) VALUES (?, ?, ?, ?, ?)",
                    (username, email, password, int(is_admin or 0), (int(role_id) if role_id is not None else None)),
                )
            elif "is_admin" in cols:
                cur.execute(
                    "INSERT INTO users (username, email, password, is_admin) VALUES (?, ?, ?, ?)",
                    (username, email, password, int(is_admin or 0)),
                )
            elif "role_id" in cols:
                cur.execute(
                    "INSERT INTO users (username, email, password, role_id) VALUES (?, ?, ?, ?)",
                    (username, email, password, (int(role_id) if role_id is not None else None)),
                )
            else:
                cur.execute(
                    "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                    (username, email, password),
                )
            return cur.lastrowid

    def update(
        self,
        user_id: int,
        username: str | None = None,
        email: str | None = None,
        password: str | None = None,
        is_admin: int | None = None,
        role_id: int | None = None,
    ) -> bool:
        """Update user fields. Returns True if a row was updated."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            sets = []
            params = []
            if username is not None and "username" in cols:
                sets.append("username = ?")
                params.append(username)
            if email is not None and "email" in cols:
                sets.append("email = ?")
                params.append(email)
            if password is not None and "password" in cols:
                sets.append("password = ?")
                params.append(password)
            if is_admin is not None and "is_admin" in cols:
                sets.append("is_admin = ?")
                params.append(int(is_admin))
            if role_id is not None and "role_id" in cols:
                sets.append("role_id = ?")
                params.append(int(role_id))

            if not sets:
                return False

            params.append(int(user_id))
            cur = conn.cursor()
            cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(params))
            return cur.rowcount > 0

    def delete(self, user_id: int) -> bool:
        """Delete a user (best-effort cleanup)."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            # Best-effort cleanup of mappings
            for sql in (
                "DELETE FROM user_roles WHERE user_id = ?",
                "DELETE FROM user_projects WHERE user_id = ?",
                "DELETE FROM locks WHERE user_id = ?",
            ):
                try:
                    cur.execute(sql, (int(user_id),))
                except Exception:
                    pass

            cur.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
            return cur.rowcount > 0

    def find_by_id(self, user_id: int) -> User | None:
        """Find a user by ID."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            if row:
                return User(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    password=row["password"],
                    role_id=(row["role_id"] if "role_id" in cols else None),
                    is_admin=(row["is_admin"] if "is_admin" in cols else 0),
                )
            return None

    def find_by_email(self, email: str) -> User | None:
        """Find a user by email."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if row:
                return User(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    password=row["password"],
                    role_id=(row["role_id"] if "role_id" in cols else None),
                    is_admin=(row["is_admin"] if "is_admin" in cols else 0),
                )
            return None
    
        
    def find_by_username(self, username: str) -> User | None:
        """Find a user by username."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cur.fetchone()
            if row:
                return User(
                    id=row["id"],
                    username=row["username"],
                    email=row["email"],
                    password=row["password"],
                    role_id=(row["role_id"] if "role_id" in cols else None),
                    is_admin=(row["is_admin"] if "is_admin" in cols else 0),
                )
            return None

    def all(self) -> list[User]:
        """Return all users."""
        with self.get_conn() as conn:
            cols = self._table_columns(conn, "users")
            cur = conn.cursor()
            cur.execute("SELECT * FROM users")
            rows = cur.fetchall()
            out = []
            for row in rows:
                out.append(
                    User(
                        id=row["id"],
                        username=row["username"],
                        email=row["email"],
                        password=row["password"],
                        role_id=(row["role_id"] if "role_id" in cols else None),
                        is_admin=(row["is_admin"] if "is_admin" in cols else 0),
                    )
                )
            return out
