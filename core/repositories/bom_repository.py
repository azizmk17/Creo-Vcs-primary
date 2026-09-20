import sqlite3
from typing import List, Optional
from core.models.bom_model import Bom
from core.ebom_policy import (
    normalize_classification,
    normalize_cad_control_mode,
    normalize_default_behavior,
    normalize_requirement,
)
from core.item_policy import (
    ITEM_NUMBER_START,
    ITEM_NUMBER_WIDTH,
    normalize_assembly_mode,
    normalize_default_unit,
    normalize_item_type,
    normalize_item_view,
    normalize_procurement_source,
)
from config import DB_NAME

class BomRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_metadata_columns()
        self._ensure_plm_columns()
        self._ensure_ebom_columns()
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
                if "cad_revision" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN cad_revision TEXT DEFAULT ''")
                if "lifecycle_state" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN lifecycle_state TEXT DEFAULT 'WIP'")
                if "released_by" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN released_by INTEGER")
                if "released_at" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN released_at TEXT")
                if "pending_revision_code" not in cols:
                    conn.execute("ALTER TABLE bom ADD COLUMN pending_revision_code TEXT")
        except Exception:
            pass

    def _ensure_ebom_columns(self):
        """Compatibility fallback when a repository is opened before migrations."""
        try:
            with self.get_conn() as conn:
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(bom)")}
                definitions = {
                    "part_number": "part_number TEXT",
                    "project_id": "project_id INTEGER",
                    "classification": "classification TEXT NOT NULL DEFAULT 'PHYSICAL'",
                    "default_ebom_behavior": (
                        "default_ebom_behavior TEXT NOT NULL DEFAULT 'NORMAL'"
                    ),
                    "cad_requirement": (
                        "cad_requirement TEXT NOT NULL DEFAULT 'OPTIONAL'"
                    ),
                    "drawing_requirement": (
                        "drawing_requirement TEXT NOT NULL DEFAULT 'OPTIONAL'"
                    ),
                    "represented_part_id": "represented_part_id INTEGER",
                    "cad_control_mode": (
                        "cad_control_mode TEXT NOT NULL DEFAULT 'CONTROLLED'"
                    ),
                    "item_type": (
                        "item_type TEXT NOT NULL DEFAULT 'MECHANICAL_PART'"
                    ),
                    "assembly_mode": (
                        "assembly_mode TEXT NOT NULL DEFAULT 'COMPONENT'"
                    ),
                    "procurement_source": (
                        "procurement_source TEXT NOT NULL DEFAULT 'MAKE'"
                    ),
                    "item_view": (
                        "item_view TEXT NOT NULL DEFAULT 'DESIGN'"
                    ),
                    "default_unit": (
                        "default_unit TEXT NOT NULL DEFAULT 'EA'"
                    ),
                    "sort_order": "sort_order INTEGER NOT NULL DEFAULT 0",
                    "deleted_at": "deleted_at TEXT",
                    "deleted_by": "deleted_by INTEGER",
                    "delete_reason": "delete_reason TEXT",
                }
                for name, definition in definitions.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE bom ADD COLUMN {definition}")
                conn.execute(
                    """
                    UPDATE bom
                    SET deleted_at=COALESCE(deleted_at, datetime('now')),
                        delete_reason=COALESCE(
                            NULLIF(delete_reason, ''),
                            'Legacy deleted Item hidden from active product structure.'
                        )
                    WHERE deleted_at IS NULL
                      AND (
                          lower(COALESCE(status, ''))='deleted'
                          OR lower(COALESCE(lifecycle_state, ''))='deleted'
                      )
                    """
                )
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS bom_cad_dependencies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        owner_bom_id INTEGER NOT NULL,
                        base_file_name TEXT NOT NULL COLLATE NOCASE,
                        original_filename TEXT NOT NULL DEFAULT '',
                        assigned_by INTEGER,
                        assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
                        UNIQUE(project_id, base_file_name)
                    );
                    CREATE INDEX IF NOT EXISTS idx_bom_cad_dependencies_owner
                        ON bom_cad_dependencies(owner_bom_id);
                    CREATE INDEX IF NOT EXISTS idx_bom_cad_dependencies_project
                        ON bom_cad_dependencies(project_id, base_file_name);
                    CREATE TABLE IF NOT EXISTS item_number_sequence (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        next_value INTEGER NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    """
                )
                self._initialize_item_number_sequence(conn)
                try:
                    conn.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS ux_bom_project_item_number
                        ON bom(project_id, part_number COLLATE NOCASE)
                        WHERE part_number IS NOT NULL AND trim(part_number)<>''
                          AND represented_part_id IS NULL
                        """
                    )
                except sqlite3.IntegrityError:
                    # Legacy databases can contain duplicate numbers.  Service
                    # validation prevents new conflicts until those rows are repaired.
                    pass
        except Exception:
            pass

    @staticmethod
    def _initialize_item_number_sequence(conn) -> None:
        row = conn.execute(
            "SELECT next_value FROM item_number_sequence WHERE id=1"
        ).fetchone()
        if row is not None:
            return
        highest = ITEM_NUMBER_START - 1
        for existing in conn.execute(
            "SELECT part_number FROM bom WHERE part_number IS NOT NULL"
        ).fetchall():
            raw = str(existing[0] or "").strip()
            if raw.isdigit():
                highest = max(highest, int(raw))
        conn.execute(
            "INSERT INTO item_number_sequence(id,next_value) VALUES(1,?)",
            (max(ITEM_NUMBER_START, highest + 1),),
        )

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
            sort_order = int(getattr(bom, "sort_order", 0) or 0)
            if sort_order <= 0 and bom.project_id is not None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(sort_order),0)+10 FROM bom WHERE project_id=?",
                    (int(bom.project_id),),
                ).fetchone()
                sort_order = int(row[0] or 10)
            cur.execute("""
                INSERT INTO bom (type, name, part_number, drawing_number, aes_number,
                                filename, drawing, base_file_name, base_drw_name, material,
                                weight, notes, pdf_path, step_path, revision, cad_revision,
                                status, created, modified,
                                project_id, classification, default_ebom_behavior,
                                cad_requirement, drawing_requirement, represented_part_id,
                                cad_control_mode, item_type, assembly_mode,
                                procurement_source, item_view, default_unit, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bom.type, bom.name, bom.part_number, bom.drawing_number, bom.aes_number,
                bom.filename, bom.drawing, bom.base_file_name, bom.base_drw_name,
                bom.material, bom.weight, bom.notes, bom.pdf_path, bom.step_path,
                bom.revision, bom.cad_revision, bom.status,
                bom.created, bom.modified, bom.project_id,
                normalize_classification(bom.classification),
                normalize_default_behavior(bom.default_ebom_behavior),
                normalize_requirement(bom.cad_requirement, "CAD requirement"),
                normalize_requirement(bom.drawing_requirement, "drawing requirement"),
                bom.represented_part_id,
                normalize_cad_control_mode(bom.cad_control_mode),
                normalize_item_type(bom.item_type),
                normalize_assembly_mode(bom.assembly_mode),
                normalize_procurement_source(bom.procurement_source),
                normalize_item_view(bom.item_view),
                normalize_default_unit(bom.default_unit),
                sort_order,
            ))
            return cur.lastrowid


    # -------------------------------
    # READ / GET
    # -------------------------------
    def get_by_id(self, bom_id: int) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE id=? AND deleted_at IS NULL", (bom_id,))
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None

    def get_by_aes(self, aes_number: str, project_id) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT * FROM bom WHERE aes_number=? AND project_id=?
                     AND deleted_at IS NULL
                   ORDER BY CASE WHEN represented_part_id IS NULL THEN 0 ELSE 1 END, id
                   LIMIT 1""",
                (aes_number, project_id),
            )
            row = cur.fetchone()
            if row:
                return Bom(**row)
            return None
    
    def get_by_base_file_name(self, base_file_name: str) -> Optional[Bom]:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM bom WHERE base_file_name=? AND deleted_at IS NULL", (base_file_name,))
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
                  AND b.deleted_at IS NULL
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

    def get_by_part_number(self, part_number: str, project_id) -> Optional[Bom]:
        """Return the real Item identified by its project-scoped PLM Number."""
        number = str(part_number or "").strip()
        if not number:
            return None
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM bom
                WHERE project_id=? AND lower(trim(part_number))=lower(?)
                  AND represented_part_id IS NULL
                  AND deleted_at IS NULL
                ORDER BY id LIMIT 1
                """,
                (int(project_id), number),
            ).fetchone()
            return Bom(**row) if row else None

    def allocate_part_number(self) -> str:
        """Atomically reserve the next global generated Item Number."""
        with self.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS item_number_sequence (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    next_value INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._initialize_item_number_sequence(conn)
            row = conn.execute(
                "SELECT next_value FROM item_number_sequence WHERE id=1"
            ).fetchone()
            candidate = max(ITEM_NUMBER_START, int(row[0] if row else ITEM_NUMBER_START))
            while conn.execute(
                "SELECT 1 FROM bom WHERE lower(trim(part_number))=lower(?) AND deleted_at IS NULL LIMIT 1",
                (str(candidate).zfill(ITEM_NUMBER_WIDTH),),
            ).fetchone():
                candidate += 1
            conn.execute(
                """
                UPDATE item_number_sequence
                SET next_value=?,updated_at=datetime('now') WHERE id=1
                """,
                (candidate + 1,),
            )
            conn.commit()
            return str(candidate).zfill(ITEM_NUMBER_WIDTH)

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
                  AND b.deleted_at IS NULL
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
            cur.execute("SELECT * FROM bom WHERE base_drw_name=? AND deleted_at IS NULL", (base_drw_name,))
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
                  AND b.deleted_at IS NULL
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
            cur.execute("SELECT * FROM bom WHERE project_id=? AND deleted_at IS NULL", (project_id,))
            rows = cur.fetchall()
            return [Bom(**row) for row in rows]

    def list_deliverable_parts(self, project_id: int, exclude_id=None) -> List[dict]:
        """Return physical BOM identities that CAD-only representations may reference."""
        params = [int(project_id)]
        exclude_clause = ""
        if exclude_id is not None:
            exclude_clause = " AND id<>?"
            params.append(int(exclude_id))
        with self.get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id, part_number, aes_number, name, type
                FROM bom
                WHERE project_id=?
                  AND represented_part_id IS NULL
                  AND deleted_at IS NULL
                  AND UPPER(COALESCE(classification, 'PHYSICAL'))='PHYSICAL'
                  {exclude_clause}
                ORDER BY lower(COALESCE(part_number, '')), lower(name), id
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_representations(self, represented_part_id: int) -> List[Bom]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM bom WHERE represented_part_id=? AND deleted_at IS NULL ORDER BY id",
                (int(represented_part_id),),
            ).fetchall()
            return [Bom(**row) for row in rows]

    def sync_representation_aes(self, represented_part_id: int, aes_number: str) -> None:
        with self.get_conn() as conn:
            conn.execute(
                """
                UPDATE bom
                SET aes_number=?, modified=datetime('now')
                WHERE represented_part_id=?
                """,
                (str(aes_number or ""), int(represented_part_id)),
            )

    def list_supplier_packages(self, project_id: int) -> List[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT b.id, b.part_number, b.aes_number, b.name, b.type,
                       COUNT(d.id) AS dependency_count
                FROM bom b
                LEFT JOIN bom_cad_dependencies d ON d.owner_bom_id=b.id
                WHERE b.project_id=?
                  AND b.deleted_at IS NULL
                  AND UPPER(COALESCE(b.cad_control_mode,'CONTROLLED'))='SUPPLIER_PACKAGE'
                GROUP BY b.id
                ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name), b.id
                """,
                (int(project_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def assign_cad_dependencies(
        self, project_id: int, owner_bom_id: int, dependencies, assigned_by=None
    ) -> int:
        cleaned = []
        seen = set()
        for dependency in dependencies or []:
            base = str((dependency or {}).get("base_file_name") or "").strip()
            filename = str((dependency or {}).get("original_filename") or base).strip()
            key = base.casefold()
            if base and key not in seen:
                seen.add(key)
                cleaned.append((base, filename))
        if not cleaned:
            return 0
        with self.get_conn() as conn:
            owner = conn.execute(
                """
                SELECT id FROM bom
                WHERE id=? AND project_id=?
                  AND deleted_at IS NULL
                  AND UPPER(COALESCE(cad_control_mode,'CONTROLLED'))='SUPPLIER_PACKAGE'
                """,
                (int(owner_bom_id), int(project_id)),
            ).fetchone()
            if not owner:
                raise ValueError("Select a supplier-managed CAD package in this project.")
            for base, filename in cleaned:
                conn.execute(
                    """
                    INSERT INTO bom_cad_dependencies(
                        project_id, owner_bom_id, base_file_name,
                        original_filename, assigned_by
                    ) VALUES(?,?,?,?,?)
                    ON CONFLICT(project_id, base_file_name) DO UPDATE SET
                        owner_bom_id=excluded.owner_bom_id,
                        original_filename=excluded.original_filename,
                        assigned_by=excluded.assigned_by,
                        assigned_at=datetime('now')
                    """,
                    (int(project_id), int(owner_bom_id), base, filename, assigned_by),
                )
        return len(cleaned)

    def list_cad_dependencies(self, project_id: int, owner_bom_id=None) -> List[dict]:
        params = [int(project_id)]
        owner_clause = ""
        if owner_bom_id is not None:
            owner_clause = " AND d.owner_bom_id=?"
            params.append(int(owner_bom_id))
        with self.get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT d.*, b.part_number AS owner_item_number,
                       b.aes_number AS owner_aes_number, b.name AS owner_name
                FROM bom_cad_dependencies d
                JOIN bom b ON b.id=d.owner_bom_id
                WHERE d.project_id=? AND b.deleted_at IS NULL {owner_clause}
                ORDER BY lower(COALESCE(b.part_number,'')), lower(d.base_file_name), d.id
                """,
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def remove_cad_dependencies(self, project_id: int, dependency_ids) -> int:
        ids = sorted({int(value) for value in (dependency_ids or [])})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.get_conn() as conn:
            cur = conn.execute(
                f"DELETE FROM bom_cad_dependencies WHERE project_id=? AND id IN ({placeholders})",
                [int(project_id), *ids],
            )
            return int(cur.rowcount or 0)

    def remove_cad_dependency_by_base(self, project_id: int, base_file_name: str) -> int:
        with self.get_conn() as conn:
            cur = conn.execute(
                """
                DELETE FROM bom_cad_dependencies
                WHERE project_id=? AND base_file_name=? COLLATE NOCASE
                """,
                (int(project_id), str(base_file_name or "")),
            )
            return int(cur.rowcount or 0)

    def dependency_base_names(self, project_id: int) -> set[str]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT d.base_file_name
                FROM bom_cad_dependencies d
                JOIN bom b ON b.id=d.owner_bom_id
                WHERE d.project_id=?
                  AND b.deleted_at IS NULL
                  AND UPPER(COALESCE(b.cad_control_mode,'CONTROLLED'))='SUPPLIER_PACKAGE'
                """,
                (int(project_id),),
            ).fetchall()
            return {str(row[0]).casefold() for row in rows if row[0]}

    def get_dependency_owner(self, project_id: int, base_file_name: str) -> Optional[dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT d.id AS dependency_id, d.owner_bom_id, d.base_file_name,
                       b.part_number, b.aes_number, b.name
                FROM bom_cad_dependencies d
                JOIN bom b ON b.id=d.owner_bom_id
                WHERE d.project_id=? AND d.base_file_name=? COLLATE NOCASE
                  AND b.deleted_at IS NULL
                  AND UPPER(COALESCE(b.cad_control_mode,'CONTROLLED'))='SUPPLIER_PACKAGE'
                LIMIT 1
                """,
                (int(project_id), str(base_file_name or "")),
            ).fetchone()
            return dict(row) if row else None

    def count_cad_dependencies(self, owner_bom_id: int) -> int:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM bom_cad_dependencies WHERE owner_bom_id=?",
                (int(owner_bom_id),),
            ).fetchone()
            return int(row[0] or 0) if row else 0

    def get_project_ids(self, project_id: int) -> List[int]:
        """Return the lightweight project membership used by the lazy BOM index."""
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id FROM bom
                WHERE project_id=? AND deleted_at IS NULL
                ORDER BY COALESCE(sort_order,id),id
                """,
                (int(project_id),),
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def ordered_root_item_ids(self, project_id: int) -> List[int]:
        """Return top-level Item ids in their persisted project root order."""
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT b.id
                FROM bom b
                WHERE b.project_id=?
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM item_usages u
                      JOIN bom p ON p.id=u.parent_item_id
                      WHERE u.child_item_id=b.id
                        AND u.project_id=b.project_id
                        AND p.project_id=b.project_id
                        AND p.deleted_at IS NULL
                  )
                ORDER BY COALESCE(b.sort_order,b.id),b.id
                """,
                (int(project_id),),
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def set_root_item_order(self, project_id: int, ordered_item_ids) -> bool:
        ordered = []
        seen = set()
        for value in ordered_item_ids or []:
            try:
                item_id = int(value)
            except Exception:
                continue
            if item_id in seen:
                continue
            ordered.append(item_id)
            seen.add(item_id)
        if not ordered:
            return False
        with self.get_conn() as conn:
            existing = self.ordered_root_item_ids(int(project_id))
            existing_set = set(existing)
            final_order = [item_id for item_id in ordered if item_id in existing_set]
            final_order.extend(item_id for item_id in existing if item_id not in set(final_order))
            for index, item_id in enumerate(final_order):
                conn.execute(
                    """
                    UPDATE bom
                    SET sort_order=?, modified=datetime('now')
                    WHERE id=? AND project_id=?
                    """,
                    ((index + 1) * 10, int(item_id), int(project_id)),
                )
            return True

    def get_many(self, project_id: int, bom_ids) -> List[Bom]:
        """Fetch full BOM rows only for the level currently being displayed."""
        ids = []
        seen = set()
        for raw_id in bom_ids or []:
            try:
                bom_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if bom_id not in seen:
                seen.add(bom_id)
                ids.append(bom_id)
        if not ids:
            return []
        fetched = []
        with self.get_conn() as conn:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                fetched.extend(conn.execute(
                    f"SELECT * FROM bom WHERE project_id=? AND deleted_at IS NULL AND id IN ({placeholders})",
                    [int(project_id), *chunk],
                ).fetchall())
        by_id = {int(row["id"]): Bom(**row) for row in fetched}
        return [by_id[bom_id] for bom_id in ids if bom_id in by_id]

    def search_project(self, project_id: int, query: str, limit: int | None = None) -> List[Bom]:
        """Search in SQLite so typing does not deserialize the whole BOM."""
        value = str(query or "").strip()
        sql = """
                SELECT * FROM bom
                WHERE project_id=?
                  AND deleted_at IS NULL
                  AND (
                    instr(lower(COALESCE(aes_number, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(name, '')), lower(?)) > 0 OR
                    instr(lower(COALESCE(part_number, '')), lower(?)) > 0
                  )
                ORDER BY id
              """
        params = [int(project_id), value, value, value]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
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
        fetched = []
        with self.get_conn() as conn:
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                fetched.extend(conn.execute(
                    f"""
                    SELECT ic.bom_id, c.name
                    FROM bom_item_categories ic
                    JOIN bom_categories c ON c.id=ic.category_id
                    WHERE ic.bom_id IN ({placeholders})
                    ORDER BY lower(c.name), c.id
                    """,
                    chunk,
                ).fetchall())
        result = {bom_id: [] for bom_id in ids}
        for row in fetched:
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
                  AND b.deleted_at IS NULL
                ORDER BY lower(COALESCE(b.part_number, '')), lower(b.name), b.id
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
                  AND b.deleted_at IS NULL
                ORDER BY lower(COALESCE(b.part_number, '')), lower(b.name), b.id
                """,
                (int(category_id), int(project_id)),
            ).fetchall()
            conn.execute("DELETE FROM bom_item_categories WHERE category_id=?", (int(category_id),))
            conn.execute(
                "DELETE FROM bom_categories WHERE id=? AND project_id=?",
                (int(category_id), int(project_id)),
            )
            return {"category": dict(category), "parts": [dict(row) for row in parts]}

    @staticmethod
    def _clean_variant_name(name: str) -> str:
        value = " ".join(str(name or "").split())
        if not value:
            raise ValueError("Variant name is required.")
        if len(value) > 100:
            raise ValueError("Variant name must be 100 characters or fewer.")
        return value

    def _ensure_variant_tables(self, conn) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS product_variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_by INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(project_id, name)
            );
            CREATE TABLE IF NOT EXISTS product_variant_items (
                variant_id INTEGER NOT NULL,
                bom_id INTEGER NOT NULL,
                assigned_by INTEGER,
                assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (variant_id, bom_id)
            );
            CREATE INDEX IF NOT EXISTS idx_product_variants_project
                ON product_variants(project_id, sort_order, name, id);
            CREATE INDEX IF NOT EXISTS idx_product_variant_items_bom
                ON product_variant_items(bom_id);
            """
        )

    def list_variants(self, project_id: int) -> List[dict]:
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            if self._table_exists(conn, "item_usages"):
                count_join = (
                    "LEFT JOIN item_usages vu "
                    "ON vu.parent_item_id=b.id AND vu.project_id=b.project_id"
                )
                count_expr = "COUNT(vu.child_item_id)"
            elif self._table_exists(conn, "bom_children"):
                count_join = "LEFT JOIN bom_children vu ON vu.parent_id=b.id"
                count_expr = "COUNT(vu.child_id)"
            else:
                count_join = "LEFT JOIN product_variant_items vi ON vi.variant_id=b.id"
                count_expr = "COUNT(vi.bom_id)"
            rows = conn.execute(
                f"""
                SELECT b.id, b.project_id, b.name,
                       COALESCE(b.notes, '') AS description,
                       COALESCE(b.part_number, b.aes_number, '') AS number,
                       b.part_number, b.aes_number, b.type, b.item_type,
                       b.created AS created_at, b.modified AS updated_at,
                       {count_expr} AS item_count
                FROM bom b
                {count_join}
                WHERE b.project_id=?
                  AND UPPER(COALESCE(b.item_type, ''))='PRODUCT'
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                GROUP BY b.id
                ORDER BY lower(COALESCE(b.part_number, '')), lower(b.name), b.id
                """,
                (int(project_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_variant(self, project_id: int, variant_id: int) -> dict:
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            if self._table_exists(conn, "item_usages"):
                count_join = (
                    "LEFT JOIN item_usages vu "
                    "ON vu.parent_item_id=b.id AND vu.project_id=b.project_id"
                )
                count_expr = "COUNT(vu.child_item_id)"
            elif self._table_exists(conn, "bom_children"):
                count_join = "LEFT JOIN bom_children vu ON vu.parent_id=b.id"
                count_expr = "COUNT(vu.child_id)"
            else:
                count_join = "LEFT JOIN product_variant_items vi ON vi.variant_id=b.id"
                count_expr = "COUNT(vi.bom_id)"
            row = conn.execute(
                f"""
                SELECT b.id, b.project_id, b.name,
                       COALESCE(b.notes, '') AS description,
                       b.part_number, b.aes_number, b.type, b.item_type,
                       b.created AS created_at, b.modified AS updated_at,
                       {count_expr} AS item_count
                FROM bom b
                {count_join}
                WHERE b.project_id=? AND b.id=?
                  AND UPPER(COALESCE(b.item_type, ''))='PRODUCT'
                  AND b.deleted_at IS NULL
                GROUP BY b.id
                """,
                (int(project_id), int(variant_id)),
            ).fetchone()
            if not row:
                raise ValueError("Product variant was not found in the current project.")
            return dict(row)

    def create_variant(self, project_id: int, name: str, description: str = "", created_by=None) -> dict:
        raise ValueError("Create a Product / Variant Item from the item editor.")

    def update_variant(self, project_id: int, variant_id: int, *, name=None, description=None) -> dict:
        raise ValueError("Edit the Product / Variant Item attributes from the item editor.")

    def delete_variant(self, project_id: int, variant_id: int) -> dict:
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            variant = conn.execute(
                """
                SELECT *
                FROM bom
                WHERE id=? AND project_id=? AND UPPER(COALESCE(item_type,''))='PRODUCT'
                """,
                (int(variant_id), int(project_id)),
            ).fetchone()
            if not variant:
                raise ValueError("Product variant was not found in the current project.")
            parts = conn.execute(
                """
                SELECT b.id, b.name, b.part_number, b.aes_number, b.type
                FROM product_variant_items vi
                JOIN bom b ON b.id=vi.bom_id
                WHERE vi.variant_id=? AND b.project_id=? AND b.deleted_at IS NULL
                ORDER BY lower(COALESCE(b.part_number, '')), lower(b.name), b.id
                """,
                (int(variant_id), int(project_id)),
            ).fetchall()
            conn.execute("DELETE FROM product_variant_items WHERE variant_id=?", (int(variant_id),))
            return {"variant": dict(variant), "parts": [dict(row) for row in parts]}

    def get_variant_names_for_bom(self, bom_id: int) -> List[str]:
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            if self._table_exists(conn, "item_usages"):
                rows = conn.execute(
                    """
                    SELECT DISTINCT v.name
                    FROM item_usages u
                    JOIN bom v ON v.id=u.parent_item_id
                    WHERE u.child_item_id=?
                      AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                      AND v.deleted_at IS NULL
                    ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                    """,
                    (int(bom_id),),
                ).fetchall()
                return [str(row["name"]) for row in rows]
            if self._table_exists(conn, "bom_children"):
                rows = conn.execute(
                    """
                    SELECT DISTINCT v.name
                    FROM bom_children u
                    JOIN bom v ON v.id=u.parent_id
                    WHERE u.child_id=?
                      AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                      AND v.deleted_at IS NULL
                    ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                    """,
                    (int(bom_id),),
                ).fetchall()
                return [str(row["name"]) for row in rows]
            rows = conn.execute(
                """
                SELECT v.name
                FROM product_variant_items vi
                JOIN bom v ON v.id=vi.variant_id
                WHERE vi.bom_id=?
                  AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                  AND v.deleted_at IS NULL
                ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                """,
                (int(bom_id),),
            ).fetchall()
            return [str(row["name"]) for row in rows]

    def get_variant_names_for_boms(self, bom_ids) -> dict:
        ids = sorted({int(bom_id) for bom_id in (bom_ids or []) if bom_id is not None})
        if not ids:
            return {}
        fetched = []
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            for offset in range(0, len(ids), 800):
                chunk = ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                if self._table_exists(conn, "item_usages"):
                    fetched.extend(conn.execute(
                        f"""
                        SELECT DISTINCT u.child_item_id AS bom_id, v.name
                        FROM item_usages u
                        JOIN bom v ON v.id=u.parent_item_id
                        WHERE u.child_item_id IN ({placeholders})
                          AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                          AND v.deleted_at IS NULL
                        ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                        """,
                        chunk,
                    ).fetchall())
                    continue
                if self._table_exists(conn, "bom_children"):
                    fetched.extend(conn.execute(
                        f"""
                        SELECT DISTINCT u.child_id AS bom_id, v.name
                        FROM bom_children u
                        JOIN bom v ON v.id=u.parent_id
                        WHERE u.child_id IN ({placeholders})
                          AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                          AND v.deleted_at IS NULL
                        ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                        """,
                        chunk,
                    ).fetchall())
                    continue
                fetched.extend(conn.execute(
                    f"""
                    SELECT vi.bom_id, v.name
                    FROM product_variant_items vi
                    JOIN bom v ON v.id=vi.variant_id
                    WHERE vi.bom_id IN ({placeholders})
                      AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                      AND v.deleted_at IS NULL
                    ORDER BY lower(COALESCE(v.part_number, '')), lower(v.name), v.id
                    """,
                    chunk,
                ).fetchall())
        result = {bom_id: [] for bom_id in ids}
        for row in fetched:
            result.setdefault(int(row["bom_id"]), []).append(str(row["name"]))
        return result

    def set_variants_for_bom(self, bom_id: int, project_id: int, variant_ids, assigned_by=None) -> List[str]:
        cleaned_ids = []
        seen = set()
        for raw_id in variant_ids or []:
            try:
                variant_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if variant_id not in seen:
                seen.add(variant_id)
                cleaned_ids.append(variant_id)
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            part = conn.execute(
                "SELECT id FROM bom WHERE id=? AND project_id=?",
                (int(bom_id), int(project_id)),
            ).fetchone()
            if not part:
                raise ValueError("BOM item was not found in the current project.")
            if cleaned_ids:
                placeholders = ",".join("?" for _ in cleaned_ids)
                valid = {
                    int(row["id"]) for row in conn.execute(
                        f"""
                        SELECT id
                        FROM bom
                        WHERE project_id=? AND id IN ({placeholders})
                          AND UPPER(COALESCE(item_type, ''))='PRODUCT'
                          AND deleted_at IS NULL
                        """,
                        [int(project_id), *cleaned_ids],
                    ).fetchall()
                }
                if len(valid) != len(cleaned_ids):
                    raise ValueError("One or more selected variants no longer exist.")
            conn.execute("DELETE FROM product_variant_items WHERE bom_id=?", (int(bom_id),))
            for variant_id in cleaned_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO product_variant_items(variant_id, bom_id, assigned_by)
                    VALUES(?,?,?)
                    """,
                    (int(variant_id), int(bom_id), assigned_by),
                )
        return self.get_variant_names_for_bom(int(bom_id))

    def variant_item_ids(self, project_id: int, variant_id: int) -> set[int]:
        with self.get_conn() as conn:
            self._ensure_variant_tables(conn)
            if self._table_exists(conn, "item_usages"):
                rows = conn.execute(
                    """
                    SELECT u.child_item_id AS bom_id
                    FROM item_usages u
                    JOIN bom v ON v.id=u.parent_item_id
                    JOIN bom b ON b.id=u.child_item_id
                    WHERE v.project_id=? AND v.id=? AND b.project_id=? AND b.deleted_at IS NULL
                      AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                      AND v.deleted_at IS NULL
                    """,
                    (int(project_id), int(variant_id), int(project_id)),
                ).fetchall()
                return {int(row["bom_id"]) for row in rows}
            if self._table_exists(conn, "bom_children"):
                rows = conn.execute(
                    """
                    SELECT u.child_id AS bom_id
                    FROM bom_children u
                    JOIN bom v ON v.id=u.parent_id
                    JOIN bom b ON b.id=u.child_id
                    WHERE v.project_id=? AND v.id=? AND b.project_id=? AND b.deleted_at IS NULL
                      AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                      AND v.deleted_at IS NULL
                    """,
                    (int(project_id), int(variant_id), int(project_id)),
                ).fetchall()
                return {int(row["bom_id"]) for row in rows}
            rows = conn.execute(
                """
                SELECT vi.bom_id
                FROM product_variant_items vi
                JOIN bom v ON v.id=vi.variant_id
                JOIN bom b ON b.id=vi.bom_id
                WHERE v.project_id=? AND v.id=? AND b.project_id=? AND b.deleted_at IS NULL
                  AND UPPER(COALESCE(v.item_type, ''))='PRODUCT'
                  AND v.deleted_at IS NULL
                """,
                (int(project_id), int(variant_id), int(project_id)),
            ).fetchall()
            return {int(row["bom_id"]) for row in rows}

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
                    filename=?, drawing=?, material=?, weight=?, notes=?, pdf_path=?, step_path=?,
                    revision=?, cad_revision=?, status=?, created=?, modified=?, classification=?,
                    default_ebom_behavior=?, cad_requirement=?, drawing_requirement=?,
                    represented_part_id=?, cad_control_mode=?, item_type=?,
                    assembly_mode=?, procurement_source=?, item_view=?, default_unit=?
                WHERE id=?
            """, (
                bom.type, bom.name, bom.part_number, bom.drawing_number, bom.aes_number,
                bom.filename, bom.drawing, bom.material, bom.weight, bom.notes,
                bom.pdf_path, bom.step_path, bom.revision, bom.cad_revision, bom.status,
                bom.created, bom.modified,
                normalize_classification(bom.classification),
                normalize_default_behavior(bom.default_ebom_behavior),
                normalize_requirement(bom.cad_requirement, "CAD requirement"),
                normalize_requirement(bom.drawing_requirement, "drawing requirement"),
                bom.represented_part_id,
                normalize_cad_control_mode(bom.cad_control_mode),
                normalize_item_type(bom.item_type),
                normalize_assembly_mode(bom.assembly_mode),
                normalize_procurement_source(bom.procurement_source),
                normalize_item_view(bom.item_view),
                normalize_default_unit(bom.default_unit),
                bom.id,
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

    def clear_legacy_cad_links(self, bom_id: int) -> bool:
        """Clear legacy Item CAD fallback fields after PDM associations are removed."""
        with self.get_conn() as conn:
            cur = conn.execute(
                """
                UPDATE bom
                SET filename=NULL,
                    base_file_name=NULL,
                    drawing=NULL,
                    base_drw_name=NULL
                WHERE id=?
                """,
                (int(bom_id),),
            )
            return bool(cur.rowcount)

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
    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table_name),),
        ).fetchone()
        return bool(row)

    @staticmethod
    def _table_columns(conn, table_name: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        except sqlite3.OperationalError:
            return set()

    @staticmethod
    def _delete_if_table(conn, table_name: str, where_sql: str, params) -> int:
        if not BomRepository._table_exists(conn, table_name):
            return 0
        cur = conn.execute(f"DELETE FROM {table_name} WHERE {where_sql}", tuple(params))
        return int(cur.rowcount or 0)

    @staticmethod
    def _has_rows(conn, table_name: str, where_sql: str, params) -> bool:
        if not BomRepository._table_exists(conn, table_name):
            return False
        try:
            row = conn.execute(
                f"SELECT 1 FROM {table_name} WHERE {where_sql} LIMIT 1",
                tuple(params),
            ).fetchone()
            return bool(row)
        except sqlite3.OperationalError:
            return False

    def _has_traceability_refs_conn(self, conn, bom_id: int) -> bool:
        """Return True when a BOM row is referenced by history that should survive."""
        checks = (
            ("audit_log", "part_id=?", (int(bom_id),)),
            ("commits", "part_id=?", (int(bom_id),)),
            ("lock_logs", "part_id=?", (int(bom_id),)),
            ("bom_revisions", "bom_id=?", (int(bom_id),)),
            ("bom_iteration_files", "bom_id=?", (int(bom_id),)),
            ("commit_file_links", "part_id=?", (int(bom_id),)),
            ("commit_engineering_file_links", "part_id=?", (int(bom_id),)),
            ("commit_validation_docs", "part_id=?", (int(bom_id),)),
        )
        return any(self._has_rows(conn, table, where_sql, params) for table, where_sql, params in checks)

    def _soft_delete_conn(
        self,
        conn,
        bom_id: int,
        deleted_by: Optional[int] = None,
        reason: str = "",
    ) -> bool:
        """Archive an Item when immutable history prevents physical deletion.

        Engineering systems must preserve traceability.  If old commits,
        baselines, audit rows, or signatures still point to bom.id, the Item is
        removed from the active product structure but the historical row stays
        available for those records.
        """
        cur = conn.execute(
            """
            UPDATE bom
            SET deleted_at=datetime('now'),
                deleted_by=?,
                delete_reason=?,
                status='Deleted',
                lifecycle_state='Deleted',
                part_number=NULL,
                aes_number=NULL,
                filename=NULL,
                base_file_name=NULL,
                drawing=NULL,
                base_drw_name=NULL,
                modified=datetime('now')
            WHERE id=? AND deleted_at IS NULL
            """,
            (
                deleted_by,
                reason
                or "Deleted from active product structure; retained because traceability/history references exist.",
                int(bom_id),
            ),
        )
        return bool(cur.rowcount)

    def delete(self, bom_id: int, deleted_by: Optional[int] = None):
        with self.get_conn() as conn:
            cur = conn.cursor()
            has_traceability_refs = self._has_traceability_refs_conn(conn, int(bom_id))
            # Remove dependent records that intentionally belong to the Item
            # master before deleting bom.id.  Newer projects have foreign-key
            # enforcement enabled, so legacy "delete the bom row first" logic
            # is no longer safe.
            if not has_traceability_refs:
                try:
                    file_ids = [
                        int(row["id"])
                        for row in cur.execute(
                            "SELECT id FROM part_files WHERE part_id=?", (int(bom_id),)
                        ).fetchall()
                    ]
                    if file_ids:
                        placeholders = ",".join("?" for _ in file_ids)
                        try:
                            cur.execute(
                                f"DELETE FROM bom_iteration_files WHERE part_file_id IN ({placeholders})",
                                file_ids,
                            )
                        except sqlite3.OperationalError:
                            pass
                        cur.execute(
                            f"DELETE FROM part_file_versions WHERE file_id IN ({placeholders})",
                            file_ids,
                        )
                        cur.execute(
                            f"DELETE FROM part_files WHERE id IN ({placeholders})",
                            file_ids,
                        )
                except sqlite3.OperationalError:
                    pass
            try:
                cur.execute("DELETE FROM issue_parts WHERE part_id=?", (int(bom_id),))
            except sqlite3.OperationalError:
                pass
            for table_name, where_sql, params in (
                ("part_doc_ack", "part_id=?", (int(bom_id),)),
                ("bom_item_categories", "bom_id=?", (int(bom_id),)),
                ("bom_children", "parent_id=? OR child_id=?", (int(bom_id), int(bom_id))),
                ("bom_cad_dependencies", "owner_bom_id=?", (int(bom_id),)),
                ("cad_item_associations", "item_id=?", (int(bom_id),)),
                ("cad_document_checkout_items", "item_id=?", (int(bom_id),)),
            ):
                try:
                    self._delete_if_table(conn, table_name, where_sql, params)
                except sqlite3.OperationalError:
                    pass
            try:
                usage_ids = [
                    int(row["id"])
                    for row in conn.execute(
                        """
                        SELECT id FROM item_usages
                        WHERE parent_item_id=? OR child_item_id=?
                        """,
                        (int(bom_id), int(bom_id)),
                    ).fetchall()
                ] if self._table_exists(conn, "item_usages") else []
                if usage_ids:
                    placeholders = ",".join("?" for _ in usage_ids)
                    self._delete_if_table(
                        conn,
                        "item_occurrences",
                        f"item_usage_id IN ({placeholders})",
                        usage_ids,
                    )
                    cur.execute(
                        f"DELETE FROM item_usages WHERE id IN ({placeholders})",
                        usage_ids,
                    )
            except sqlite3.OperationalError:
                pass
            try:
                self._delete_if_table(
                    conn,
                    "item_structure_iterations",
                    "parent_item_id=?",
                    (int(bom_id),),
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._delete_if_table(
                    conn,
                    "bom_working_bindings",
                    "parent_bom_id=? OR child_bom_id=?",
                    (int(bom_id), int(bom_id)),
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._delete_if_table(
                    conn,
                    "bom_iteration_children",
                    "child_bom_id=?",
                    (int(bom_id),),
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._delete_if_table(
                    conn,
                    "assembly_configuration_members",
                    "bom_id=?",
                    (int(bom_id),),
                )
                self._delete_if_table(
                    conn,
                    "assembly_configurations",
                    "root_bom_id=?",
                    (int(bom_id),),
                )
            except sqlite3.OperationalError:
                pass
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
            if has_traceability_refs:
                return self._soft_delete_conn(
                    conn,
                    int(bom_id),
                    deleted_by=deleted_by,
                    reason=(
                        "Deleted from active product structure; historical commits, "
                        "audit records, baselines, or file links still reference this Item."
                    ),
                )
            try:
                cur.execute("DELETE FROM bom WHERE id=?", (int(bom_id),))
                return bool(cur.rowcount)
            except sqlite3.IntegrityError:
                return self._soft_delete_conn(
                    conn,
                    int(bom_id),
                    deleted_by=deleted_by,
                    reason=(
                        "Delete converted to archive because commits, audits, baselines, "
                        "or other traceability records still reference this Item."
                    ),
                )

    
