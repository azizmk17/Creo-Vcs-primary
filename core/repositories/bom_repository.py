import sqlite3
from typing import List, Optional
from core.models.bom_model import Bom
from config import DB_NAME

class BomRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_metadata_columns()
        self._ensure_plm_columns()
        self._ensure_category_schema()

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

    def _ensure_category_schema(self):
        """Keep category storage available when a repository starts before migrations run."""
        try:
            with self.get_conn() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS bom_categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        name TEXT NOT NULL COLLATE NOCASE,
                        created_by INTEGER,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(project_id, name)
                    );
                    CREATE TABLE IF NOT EXISTS bom_item_categories (
                        bom_id INTEGER NOT NULL,
                        category_id INTEGER NOT NULL,
                        assigned_by INTEGER,
                        assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (bom_id, category_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_bom_categories_project_name
                        ON bom_categories(project_id, name);
                    CREATE INDEX IF NOT EXISTS idx_bom_item_categories_bom
                        ON bom_item_categories(bom_id);
                    CREATE INDEX IF NOT EXISTS idx_bom_item_categories_category
                        ON bom_item_categories(category_id);
                    """
                )
        except Exception:
            # Startup migration remains the source of truth; do not block the app here.
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
        """Project-scoped BOM lookup that prefers the part checked out by preferred_user_id.

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

    def get_all_by_base_file_name_for_commit(
        self,
        base_file_name: str,
        project_id: int,
        preferred_user_id: Optional[int] = None,
    ) -> List[Bom]:
        """Return every BOM row in this project that shares the same CAD base file."""
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
                    b.id ASC
                """,
                (base_file_name, project_id, preferred_user_id),
            )
            rows = cur.fetchall()
            return [Bom(**row) for row in rows]
        
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
    # PROJECT CATEGORIES
    # -------------------------------
    @staticmethod
    def _clean_category_name(name: str) -> str:
        value = " ".join(str(name or "").split())
        if not value:
            raise ValueError("Category name is required.")
        if len(value) > 80:
            raise ValueError("Category name must be 80 characters or fewer.")
        return value

    def list_categories(self, project_id: int) -> List[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.project_id, c.name, c.created_by, c.created_at,
                       COUNT(ic.bom_id) AS item_count
                FROM bom_categories c
                LEFT JOIN bom_item_categories ic ON ic.category_id=c.id
                WHERE c.project_id=?
                GROUP BY c.id
                ORDER BY lower(c.name), c.id
                """,
                (int(project_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_categories_for_bom(self, bom_id: int) -> List[str]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT c.name
                FROM bom_item_categories ic
                JOIN bom_categories c ON c.id=ic.category_id
                WHERE ic.bom_id=?
                ORDER BY lower(c.name), c.id
                """,
                (int(bom_id),),
            ).fetchall()
            return [str(row["name"]) for row in rows]

    def get_categories_for_boms(self, bom_ids) -> dict:
        ids = sorted({int(bom_id) for bom_id in (bom_ids or []) if bom_id is not None})
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT ic.bom_id, c.name
                FROM bom_item_categories ic
                JOIN bom_categories c ON c.id=ic.category_id
                WHERE ic.bom_id IN ({placeholders})
                ORDER BY lower(c.name), c.id
                """,
                ids,
            ).fetchall()
        result = {bom_id: [] for bom_id in ids}
        for row in rows:
            result.setdefault(int(row["bom_id"]), []).append(str(row["name"]))
        return result

    def set_categories_for_bom(self, bom_id: int, project_id: int, category_names, assigned_by=None) -> List[str]:
        cleaned_names = []
        seen = set()
        for raw_name in category_names or []:
            name = self._clean_category_name(raw_name)
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                cleaned_names.append(name)

        with self.get_conn() as conn:
            part = conn.execute(
                "SELECT id FROM bom WHERE id=? AND project_id=?",
                (int(bom_id), int(project_id)),
            ).fetchone()
            if not part:
                raise ValueError("BOM item was not found in the current project.")

            conn.execute("DELETE FROM bom_item_categories WHERE bom_id=?", (int(bom_id),))
            for name in cleaned_names:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bom_categories(project_id, name, created_by)
                    VALUES(?,?,?)
                    """,
                    (int(project_id), name, assigned_by),
                )
                category = conn.execute(
                    "SELECT id, name FROM bom_categories WHERE project_id=? AND name=? COLLATE NOCASE",
                    (int(project_id), name),
                ).fetchone()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO bom_item_categories(bom_id, category_id, assigned_by)
                    VALUES(?,?,?)
                    """,
                    (int(bom_id), int(category["id"]), assigned_by),
                )

        return self.get_categories_for_bom(int(bom_id))

    def get_category_usage(self, project_id: int, category_id: int) -> dict:
        with self.get_conn() as conn:
            category = conn.execute(
                "SELECT id, project_id, name FROM bom_categories WHERE id=? AND project_id=?",
                (int(category_id), int(project_id)),
            ).fetchone()
            if not category:
                raise ValueError("Category was not found in the current project.")
            parts = conn.execute(
                """
                SELECT b.id, b.name, b.aes_number, b.part_number, b.type
                FROM bom_item_categories ic
                JOIN bom b ON b.id=ic.bom_id
                WHERE ic.category_id=? AND b.project_id=?
                ORDER BY lower(COALESCE(b.aes_number, '')), lower(b.name), b.id
                """,
                (int(category_id), int(project_id)),
            ).fetchall()
            return {"category": dict(category), "parts": [dict(row) for row in parts]}

    def delete_category(self, project_id: int, category_id: int) -> dict:
        with self.get_conn() as conn:
            category = conn.execute(
                "SELECT id, project_id, name FROM bom_categories WHERE id=? AND project_id=?",
                (int(category_id), int(project_id)),
            ).fetchone()
            if not category:
                raise ValueError("Category was not found in the current project.")
            parts = conn.execute(
                """
                SELECT b.id, b.name, b.aes_number, b.part_number, b.type
                FROM bom_item_categories ic
                JOIN bom b ON b.id=ic.bom_id
                WHERE ic.category_id=? AND b.project_id=?
                ORDER BY lower(COALESCE(b.aes_number, '')), lower(b.name), b.id
                """,
                (int(category_id), int(project_id)),
            ).fetchall()
            conn.execute("DELETE FROM bom_item_categories WHERE category_id=?", (int(category_id),))
            conn.execute(
                "DELETE FROM bom_categories WHERE id=? AND project_id=?",
                (int(category_id), int(project_id)),
            )
            return {"category": dict(category), "parts": [dict(row) for row in parts]}

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
                SET locked = 0
                WHERE id=?
            """, (
                id,
            ))

    def checkout_bom(self, id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE bom
                SET locked = 1
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
            cur.execute("DELETE FROM bom_item_categories WHERE bom_id=?", (bom_id,))
            try:
                cur.execute("DELETE FROM bom_folder_items WHERE bom_id=?", (bom_id,))
                folder_ids = []

                def collect_folder(folder_id: int):
                    folder_ids.append(int(folder_id))
                    rows = cur.execute(
                        "SELECT id FROM bom_folders WHERE parent_folder_id=?", (int(folder_id),)
                    ).fetchall()
                    for row in rows:
                        collect_folder(int(row["id"]))

                roots = cur.execute(
                    "SELECT id FROM bom_folders WHERE parent_bom_id=?", (int(bom_id),)
                ).fetchall()
                for row in roots:
                    collect_folder(int(row["id"]))
                if folder_ids:
                    placeholders = ",".join("?" for _ in folder_ids)
                    cur.execute(
                        f"DELETE FROM bom_folder_items WHERE folder_id IN ({placeholders})", folder_ids
                    )
                    cur.execute(f"DELETE FROM bom_folders WHERE id IN ({placeholders})", folder_ids)
            except sqlite3.OperationalError:
                pass
            cur.execute("DELETE FROM bom WHERE id=?", (bom_id,))

    
