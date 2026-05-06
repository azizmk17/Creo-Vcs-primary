import sqlite3
from typing import List, Optional
from core.models.bom_model import Bom
from config import DB_NAME

class BomRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_metadata_columns()
        self._ensure_plm_columns()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_metadata_columns(self):
        """Best-effort schema upgrade for new BOM metadata fields."""
        try:
            with self.get_conn() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(bom)").fetchall()]
                if "pdf_path" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN pdf_path TEXT")
                if "step_path" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN step_path TEXT")
        except Exception:
            # Don't block app startup if schema is managed elsewhere
            pass

    def _ensure_plm_columns(self):
        """Best-effort schema upgrade for PLM-lite fields."""
        try:
            with self.get_conn() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(bom)").fetchall()]
                if "revision" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN revision TEXT DEFAULT 'A'")
                if "lifecycle_state" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN lifecycle_state TEXT DEFAULT 'WIP'")
                if "released_by" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN released_by INTEGER")
                if "released_at" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN released_at TEXT")
        except Exception:
            pass

    @staticmethod
    def _next_revision(rev: str) -> str:
        """Return next alphabetic revision: A..Z, AA..AZ, BA.."""
        r = (rev or "A").strip().upper()
        if not r.isalpha():
            r = "A"

        # Excel-style base-26 increment
        digits = [ord(ch) - ord('A') for ch in r]
        carry = 1
        for i in range(len(digits) - 1, -1, -1):
            if carry == 0:
                break
            digits[i] += carry
            if digits[i] >= 26:
                digits[i] = 0
                carry = 1
            else:
                carry = 0
        if carry:
            digits = [0] + digits
        return "".join(chr(d + ord('A')) for d in digits)

    def set_revision(self, bom_id: int, revision: str):
        revision = (revision or "A").strip().upper()
        if not revision.isalpha():
            raise ValueError("Revision must be alphabetic")
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE bom SET revision = ?, modified = datetime('now') WHERE id = ?",
                (revision, bom_id),
            )
            conn.commit()

    def release_part(self, bom_id: int, released_by: Optional[int] = None, bump_revision_if_already_released: bool = True):
        """Mark part Released; optionally bump revision if it was already Released."""
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT revision, lifecycle_state FROM bom WHERE id = ?",
                (bom_id,),
            ).fetchone()

            current_rev = (row[0] if row else None) or "A"
            current_state = (row[1] if row else None) or "WIP"

            new_rev = current_rev
            if bump_revision_if_already_released and str(current_state).upper() == "RELEASED":
                new_rev = self._next_revision(current_rev)

            conn.execute(
                """
                UPDATE bom
                SET revision = ?,
                    lifecycle_state = 'Released',
                    released_by = ?,
                    released_at = datetime('now'),
                    status = 'Released',
                    modified = datetime('now')
                WHERE id = ?
                """,
                (new_rev, released_by, bom_id),
            )
            conn.commit()

    # -------------------------------
    # CREATE / INSERT
    # -------------------------------
    def insert(self, bom: Bom) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO bom (type, name, part_number, drawing_number, aes_number,
                                filename, drawing, base_file_name, base_drw_name, material, weight, notes, pdf_path, step_path, status, created, modified, project_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bom.type, bom.name, bom.part_number, bom.drawing_number, bom.aes_number,
                bom.filename, bom.drawing, bom.base_file_name, bom.base_drw_name, bom.material, bom.weight, bom.notes, bom.pdf_path, bom.step_path, bom.status,
                bom.created, bom.modified, bom.project_id
            ))
            return cur.lastrowid


    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_by_id(self, bom_id: int) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE id=?", (bom_id,))
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None

    def get_by_aes(self, aes_number: str, project_id) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE aes_number=? AND project_id=?", (aes_number,project_id,))
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None
    
    def get_by_base_file_name(self, base_file_name: str) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE base_file_name=?", (base_file_name,))
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None

    def get_by_base_file_name_for_commit(
        self,
        base_file_name: str,
        project_id: int,
        preferred_user_id: Optional[int] = None,
    ) -> Optional[Bom]:
        """Project-scoped BOM lookup that prefers the part checked-in by preferred_user_id.

        This avoids selecting the wrong BOM row when the same base_file_name exists more
        than once (e.g., after versioning/duplication).
        """
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT b.*
                FROM bom b
                LEFT JOIN locks l ON l.part_id = b.id
                WHERE b.base_file_name = ? AND b.project_id = ?
                ORDER BY
                    CASE WHEN l.user_id = ? THEN 1 ELSE 0 END DESC,
                    CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END DESC,
                    b.id DESC
                LIMIT 1
                """,
                (base_file_name, project_id, preferred_user_id),
            )
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None
        
    def get_by_drawing_file_name(self, base_drw_name: str) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE base_drw_name=?", (base_drw_name,))
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None

    def get_by_drawing_file_name_for_commit(
        self,
        base_drw_name: str,
        project_id: int,
        preferred_user_id: Optional[int] = None,
    ) -> Optional[Bom]:
        """Project-scoped drawing BOM lookup that prefers the part checked-in by preferred_user_id."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT b.*
                FROM bom b
                LEFT JOIN locks l ON l.part_id = b.id
                WHERE b.base_drw_name = ? AND b.project_id = ?
                ORDER BY
                    CASE WHEN l.user_id = ? THEN 1 ELSE 0 END DESC,
                    CASE WHEN l.user_id IS NOT NULL THEN 1 ELSE 0 END DESC,
                    b.id DESC
                LIMIT 1
                """,
                (base_drw_name, project_id, preferred_user_id),
            )
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None

    def get_all(self, project_id) -> List[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE project_id=?", (project_id,))
            rows = cur.fetchall()
            return [Bom(**row) for row in rows]

    # -------------------------------
    # UPDATE
    # -------------------------------
    def update(self, bom: Bom):
        if not bom.id:
            raise ValueError("Bom ID is required for update")
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE bom
                SET type=?, name=?, part_number=?, drawing_number=?, aes_number=?,
                    filename=?, drawing=?, material=?, weight=?, notes=?, pdf_path=?, step_path=?, status=?, created=?, modified=?
                WHERE id=?
            """, (
                bom.type, bom.name, bom.part_number, bom.drawing_number, bom.aes_number,
                bom.filename, bom.drawing, bom.material, bom.weight, bom.notes, bom.pdf_path, bom.step_path, bom.status,
                bom.created, bom.modified, bom.id
            ))

            conn.commit()

    def update_bom_file_names(self, id, base_file_name, base_drw_name, project_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE bom
                SET base_file_name=?, base_drw_name=?
                WHERE id=? AND project_id=?
            """, (
                base_file_name, base_drw_name, id, project_id
            ))

    def checkin_bom(self, id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE bom
                SET locked = 1
                WHERE id=?
            """, (
                id,
            ))

    def checkout_bom(self, id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE bom
                SET locked = 0
                WHERE id=?
            """, (
                id,
            ))

    # -------------------------------
    # DELETE
    # -------------------------------
    def delete(self, bom_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bom WHERE id=?", (bom_id,))

    
