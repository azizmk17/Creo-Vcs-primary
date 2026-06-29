import sqlite3
import re
from typing import List, Optional

from config import DB_NAME
from core.models.part_file_model import PartFile
from core.models.part_file_version_model import PartFileVersion


class PartFileRepository:
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._ensure_tables()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self):
        with self.get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS part_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_id INTEGER NOT NULL,
                    file_type TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    description TEXT DEFAULT NULL,
                    created_by INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    active_version_id INTEGER DEFAULT NULL,
                    FOREIGN KEY (part_id) REFERENCES bom(id)
                );

                CREATE INDEX IF NOT EXISTS idx_part_files_part_id ON part_files(part_id);

                CREATE TABLE IF NOT EXISTS part_file_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    version_no INTEGER NOT NULL,
                    original_filename TEXT NOT NULL,
                    vault_rel_path TEXT NOT NULL,
                    sha256 TEXT DEFAULT NULL,
                    size_bytes INTEGER DEFAULT NULL,
                    note TEXT DEFAULT NULL,
                    revision TEXT DEFAULT NULL,
                    created_by INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    lifecycle_state TEXT DEFAULT 'WIP',
                    released_by INTEGER DEFAULT NULL,
                    released_at TEXT DEFAULT NULL,
                    root_project_id INTEGER DEFAULT NULL,
                    project_version_label TEXT DEFAULT NULL,
                    FOREIGN KEY (file_id) REFERENCES part_files(id),
                    UNIQUE(file_id, version_no)
                );

                CREATE INDEX IF NOT EXISTS idx_part_file_versions_file_id ON part_file_versions(file_id);
                """
            )

            # Best-effort add columns if DB pre-dates lifecycle/version-context fields
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(part_file_versions)").fetchall()]
                if "lifecycle_state" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN lifecycle_state TEXT DEFAULT 'WIP'")
                if "released_by" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN released_by INTEGER")
                if "released_at" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN released_at TEXT")
                if "root_project_id" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN root_project_id INTEGER")
                if "project_version_label" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN project_version_label TEXT")
                if "revision" not in cols:
                    conn.execute("ALTER TABLE part_file_versions ADD COLUMN revision TEXT")
                self._migrate_revision_notes_once(conn)
            except Exception:
                pass

    @staticmethod
    def _revision_from_legacy_note(note: str) -> Optional[str]:
        text = str(note or "").strip()
        if not text:
            return None
        match = re.fullmatch(
            r"(?i)(?:(?:rev(?:ision)?\.?\s*[:_-]?\s*)([A-Z]{1,2}[0-9]{0,3})|([A-Z]|[A-Z][0-9]{3}))",
            text,
        )
        if not match:
            return None
        return (match.group(1) or match.group(2)).upper()

    def _migrate_revision_notes_once(self, conn):
        """Move legacy revision-only notes into the dedicated revision column once."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        marker = "part_file_versions_note_to_revision_v1"
        done = conn.execute("SELECT value FROM app_metadata WHERE key=?", (marker,)).fetchone()
        if done:
            return
        rows = conn.execute(
            """
            SELECT id, note
            FROM part_file_versions
            WHERE (revision IS NULL OR TRIM(revision)='')
              AND note IS NOT NULL
              AND TRIM(note) <> ''
            """
        ).fetchall()
        for row in rows:
            revision = self._revision_from_legacy_note(row["note"])
            if not revision:
                continue
            conn.execute(
                "UPDATE part_file_versions SET revision=?, note=NULL WHERE id=?",
                (revision, int(row["id"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES(?, datetime('now'))",
            (marker,),
        )

    # -----------------
    # Files
    # -----------------
    def create_file(self, part_id: int, file_type: str, display_name: str, description: str = "", created_by: Optional[int] = None) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO part_files (part_id, file_type, display_name, description, created_by)
                VALUES (?, ?, ?, ?, ?)
                """,
                (part_id, file_type, display_name, description or None, created_by),
            )
            conn.commit()
            return cur.lastrowid

    def get_files_for_part(self, part_id: int) -> List[PartFile]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM part_files WHERE part_id = ? ORDER BY created_at DESC",
                (part_id,),
            ).fetchall()
            return [PartFile(**dict(r)) for r in rows]

    def get_file_by_id(self, file_id: int) -> Optional[PartFile]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM part_files WHERE id = ?", (file_id,)).fetchone()
            return PartFile(**dict(row)) if row else None

    def set_active_version(self, file_id: int, version_id: int):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE part_files SET active_version_id = ? WHERE id = ?",
                (version_id, file_id),
            )
            conn.commit()

    def delete_file(self, file_id: int):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM part_file_versions WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM part_files WHERE id = ?", (file_id,))
            conn.commit()

    # -----------------
    # Versions
    # -----------------
    def get_versions(self, file_id: int) -> List[PartFileVersion]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM part_file_versions WHERE file_id = ? ORDER BY version_no DESC",
                (file_id,),
            ).fetchall()
            return [PartFileVersion(**dict(r)) for r in rows]

    def get_version_by_id(self, version_id: int) -> Optional[PartFileVersion]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM part_file_versions WHERE id = ?", (version_id,)).fetchone()
            return PartFileVersion(**dict(row)) if row else None

    def get_next_version_no(self, file_id: int) -> int:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_no), 0) AS max_v FROM part_file_versions WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            max_v = int(row["max_v"]) if row else 0
            return max_v + 1

    def add_version(
        self,
        file_id: int,
        version_no: int,
        original_filename: str,
        vault_rel_path: str,
        sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        note: str = "",
        revision: str = "",
        created_by: Optional[int] = None,
        root_project_id: Optional[int] = None,
        project_version_label: Optional[str] = None,
    ) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()

            # Older DBs might not have the version-context columns.
            cols = []
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(part_file_versions)").fetchall()]
            except Exception:
                cols = []

            if "root_project_id" in cols and "project_version_label" in cols:
                revision_sql = ", revision" if "revision" in cols else ""
                revision_placeholder = ", ?" if "revision" in cols else ""
                cur.execute(
                    f"""
                    INSERT INTO part_file_versions (
                        file_id, version_no, original_filename, vault_rel_path, sha256, size_bytes, note, created_by,
                        lifecycle_state, root_project_id, project_version_label{revision_sql}
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?{revision_placeholder})
                    """,
                    tuple([
                        file_id,
                        version_no,
                        original_filename,
                        vault_rel_path,
                        sha256,
                        size_bytes,
                        note or None,
                        created_by,
                        "WIP",
                        root_project_id,
                        project_version_label,
                    ] + ([revision if revision is not None else None] if "revision" in cols else [])),
                )
            else:
                revision_sql = ", revision" if "revision" in cols else ""
                revision_placeholder = ", ?" if "revision" in cols else ""
                cur.execute(
                    f"""
                    INSERT INTO part_file_versions (
                        file_id, version_no, original_filename, vault_rel_path, sha256, size_bytes, note, created_by, lifecycle_state{revision_sql}
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?{revision_placeholder})
                    """,
                    tuple([
                        file_id,
                        version_no,
                        original_filename,
                        vault_rel_path,
                        sha256,
                        size_bytes,
                        note or None,
                        created_by,
                        "WIP",
                    ] + ([revision if revision is not None else None] if "revision" in cols else [])),
                )

            conn.commit()
            return cur.lastrowid

    def release_version(self, version_id: int, released_by: Optional[int] = None):
        with self.get_conn() as conn:
            conn.execute(
                """
                UPDATE part_file_versions
                SET lifecycle_state = 'Released', released_by = ?, released_at = datetime('now')
                WHERE id = ?
                """,
                (released_by, version_id),
            )
            conn.commit()

    def delete_version(self, version_id: int):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM part_file_versions WHERE id = ?", (version_id,))
            conn.commit()

    def clear_active_if_matches(self, file_id: int, version_id: int):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE part_files SET active_version_id = NULL WHERE id = ? AND active_version_id = ?",
                (file_id, version_id),
            )
            conn.commit()
