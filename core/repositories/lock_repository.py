import sqlite3
from typing import List, Optional
from core.models.lock_model import Locks
from core.models.lock_logs_model import Lock_logs
from config import DB_NAME
from datetime import datetime


class LockRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self.get_conn() as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "locks" in tables:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(locks)").fetchall()
                }
                if "checkout_origin" not in columns:
                    conn.execute(
                        "ALTER TABLE locks ADD COLUMN "
                        "checkout_origin TEXT NOT NULL DEFAULT 'ITEM'"
                    )
                if "checked_out_at" not in columns:
                    conn.execute("ALTER TABLE locks ADD COLUMN checked_out_at TEXT")
                conn.execute(
                    "UPDATE locks SET checkout_origin='ITEM' "
                    "WHERE checkout_origin IS NULL OR trim(checkout_origin)=''"
                )
            if "lock_logs" not in tables:
                return
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(lock_logs)").fetchall()
            }
            if "object_iteration_id" not in columns:
                conn.execute(
                    "ALTER TABLE lock_logs ADD COLUMN object_iteration_id INTEGER"
                )

    # -------------------------------
    def checkin(self, part_id, user_id, signature, object_iteration_id=None) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM locks WHERE part_id = ?", (part_id,))
            cur.execute("""
                INSERT INTO lock_logs (
                    part_id, user_id, action, timestamp, signature, object_iteration_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                part_id, user_id, "checkin", sqlite3.datetime.datetime.now().isoformat(),
                signature, object_iteration_id,
            ))
            return int(cur.lastrowid)

    def undo_checkout(self, part_id, user_id, signature, object_iteration_id=None) -> int:
        """Release a checkout without recording a check-in event."""
        with self.get_conn() as conn:
            conn.execute("DELETE FROM locks WHERE part_id = ?", (int(part_id),))
            conn.execute(
                """
                INSERT INTO lock_logs (
                    part_id, user_id, action, timestamp, signature, object_iteration_id
                ) VALUES (?, ?, 'undo_checkout', ?, ?, ?)
                """,
                (
                    int(part_id), int(user_id), sqlite3.datetime.datetime.now().isoformat(),
                    signature, object_iteration_id,
                ),
            )
            return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        
    def checkout(
        self,
        part_id,
        user_id,
        signature,
        object_iteration_id=None,
        checkout_origin: str = "ITEM",
    ) -> int:
        origin = str(checkout_origin or "ITEM").strip().upper()
        if origin not in {"ITEM", "CAD"}:
            raise ValueError(f"Unsupported checkout origin: {checkout_origin}.")
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO locks (
                    part_id, user_id, checkout_origin, checked_out_at
                ) VALUES (?, ?, ?, ?)
            """, (
                part_id, user_id, origin,
                sqlite3.datetime.datetime.now().isoformat(),
            ))
            cur.execute("""
                INSERT INTO lock_logs (
                    part_id, user_id, action, timestamp, signature, object_iteration_id
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                part_id, user_id, "checkout", sqlite3.datetime.datetime.now().isoformat(),
                signature, object_iteration_id,
            ))
            return int(cur.lastrowid)

    def set_checkout_origin(self, part_id: int, checkout_origin: str) -> bool:
        """Change why an active Item working copy is being retained."""
        origin = str(checkout_origin or "ITEM").strip().upper()
        if origin not in {"ITEM", "CAD"}:
            raise ValueError(f"Unsupported checkout origin: {checkout_origin}.")
        with self.get_conn() as conn:
            cur = conn.execute(
                "UPDATE locks SET checkout_origin=? WHERE part_id=?",
                (origin, int(part_id)),
            )
            return bool(cur.rowcount)

    def upgrade_to_item_checkout(self, part_id: int, user_id: int) -> bool:
        """Retain an auto CAD checkout as an explicit Item checkout."""
        with self.get_conn() as conn:
            cur = conn.execute(
                """
                UPDATE locks SET checkout_origin='ITEM'
                WHERE part_id=? AND user_id=? AND checkout_origin='CAD'
                """,
                (int(part_id), int(user_id)),
            )
            return bool(cur.rowcount)

    def set_log_object_iteration(self, log_id: int, object_iteration_id: int) -> None:
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE lock_logs SET object_iteration_id=? WHERE id=?",
                (int(object_iteration_id), int(log_id)),
            )
        
    
        
    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_by_part(self, part_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE part_id=?", (part_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None
        
    def get_by_user(self, user_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE user_id=?", (user_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None

    def get_all(self) -> List[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks")
            rows = cur.fetchall()
            return [Locks(**row) for row in rows]
        
    def get_history_by_part(self, part_id: int) -> List[Lock_logs]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            # cur.execute("SELECT * FROM lock_logs WHERE part_id=?", (part_id,))
            cur.execute("""
            SELECT 
                ll.*,
                u.username as user_name
            FROM lock_logs ll
            LEFT JOIN users u ON ll.user_id = u.id
            WHERE ll.part_id=?
            ORDER BY ll.timestamp DESC
        """, (part_id,))
            rows = cur.fetchall()
            #print rows results for debugging
            for row in rows:
                print(dict(row)) 
            return [Lock_logs(**row) for row in rows]
        
    def get_lock_by_part(self, part_id: int) -> Optional[Locks]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM locks WHERE part_id=?", (part_id,))
            row = cur.fetchone()
            if row:
                return Locks(**row)
            return None

    def get_lock_owners_for_project(self, project_id: int) -> dict[int, str]:
        """Return mapping: bom.part_id -> username for parts currently in locks.

        locks table has no project_id; join through bom.
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    SELECT l.part_id, u.username
                    FROM locks l
                    JOIN bom b ON b.id = l.part_id
                    LEFT JOIN users u ON u.id = l.user_id
                    WHERE b.project_id = ?
                    """,
                    (int(project_id),),
                )
                rows = cur.fetchall()
                out: dict[int, str] = {}
                for r in rows:
                    try:
                        pid = int(r[0])
                        uname = str(r[1] or "").strip()
                    except Exception:
                        continue
                    if uname:
                        out[pid] = uname
                return out
            except Exception:
                return {}
