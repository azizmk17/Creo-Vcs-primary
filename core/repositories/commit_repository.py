from core.models.commit_model import Commit
from datetime import datetime
from config import DB_NAME
import sqlite3

class CommitRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self.get_conn() as conn:
            cur = conn.cursor()

            def has_column(table_name: str, column_name: str) -> bool:
                try:
                    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table_name})").fetchall()]
                    return column_name in cols
                except Exception:
                    return False

            try:
                if not has_column("commits", "step_compare_enabled"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_compare_enabled INTEGER DEFAULT 0")
                if not has_column("commits", "step_file_path"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_file_path TEXT")
                if not has_column("commits", "step_prev_file_path"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_prev_file_path TEXT")
                if not has_column("commits", "step_diff_path"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_diff_path TEXT")
                if not has_column("commits", "step_diff_summary"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_diff_summary TEXT")
                if not has_column("commits", "step_diff_status"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_diff_status TEXT")
                if not has_column("commits", "step_error"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_error TEXT")
                if not has_column("commits", "step_face_map_path"):
                    cur.execute("ALTER TABLE commits ADD COLUMN step_face_map_path TEXT")
            except Exception:
                pass

            try:
                cur.execute("CREATE INDEX IF NOT EXISTS idx_commits_step_part_project ON commits(part_id, project_id)")
            except Exception:
                pass

            conn.commit()
    # -------------------------------
    # CREATE / INSERT
    # -------------------------------
    def insert(
        self,
        part_id,
        part_type,
        filename,
        filepath,
        base_file_name,
        designer,
        committer,
        message,
        signature,
        project_id,
        title,
        commit_id,
        step_compare_enabled=0,
        step_file_path=None,
        step_prev_file_path=None,
        step_diff_path=None,
        step_diff_summary=None,
        step_diff_status=None,
        step_error=None,
        step_face_map_path=None,
    ) -> int:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO commits (
                    part_id, type, filename, file_path, base_file_name, designer, message,
                    committed_by, status, committed_at, signature, project_id, title, commit_id,
                    step_compare_enabled, step_file_path, step_prev_file_path, step_diff_path,
                    step_diff_summary, step_diff_status, step_error, step_face_map_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                part_id,
                part_type,
                filename,
                filepath,
                base_file_name,
                designer,
                message,
                committer,
                'Pending',
                datetime.now(),
                signature,
                project_id,
                title,
                commit_id,
                int(bool(step_compare_enabled)),
                step_file_path,
                step_prev_file_path,
                step_diff_path,
                step_diff_summary,
                step_diff_status,
                step_error,
                step_face_map_path,
            ))
            return cur.lastrowid

    def get_latest_step_commit_for_part(self, part_id: int, project_id: int, exclude_commit_id: str | None = None):
        with self.get_conn() as conn:
            cur = conn.cursor()
            if exclude_commit_id:
                cur.execute(
                    """
                    SELECT *
                    FROM commits
                    WHERE part_id = ?
                      AND project_id = ?
                      AND step_file_path IS NOT NULL
                      AND TRIM(step_file_path) <> ''
                      AND commit_id <> ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (int(part_id), int(project_id), str(exclude_commit_id)),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM commits
                    WHERE part_id = ?
                      AND project_id = ?
                      AND step_file_path IS NOT NULL
                      AND TRIM(step_file_path) <> ''
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (int(part_id), int(project_id)),
                )
            row = cur.fetchone()
            return self._row_to_commit(row) if row else None

    def get_latest_step_commit_for_base_name_family(
        self,
        base_file_name: str,
        project_id: int,
        exclude_commit_id: str | None = None,
    ):
        with self.get_conn() as conn:
            cur = conn.cursor()

            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            except Exception:
                cols = []

            params = []
            if "root_project_id" in cols:
                try:
                    row = conn.execute(
                        "SELECT root_project_id, id FROM projects WHERE id = ?",
                        (int(project_id),),
                    ).fetchone()
                    root_project_id = int(row[0]) if row and row[0] is not None else int(project_id)
                except Exception:
                    root_project_id = int(project_id)

                sql = """
                    SELECT c.*
                    FROM commits c
                    JOIN projects p ON p.id = c.project_id
                    WHERE p.root_project_id = ?
                      AND c.base_file_name = ?
                      AND c.step_file_path IS NOT NULL
                      AND TRIM(c.step_file_path) <> ''
                """
                params = [int(root_project_id), str(base_file_name)]
            else:
                sql = """
                    SELECT c.*
                    FROM commits c
                    WHERE c.project_id = ?
                      AND c.base_file_name = ?
                      AND c.step_file_path IS NOT NULL
                      AND TRIM(c.step_file_path) <> ''
                """
                params = [int(project_id), str(base_file_name)]

            if exclude_commit_id:
                sql += " AND c.commit_id <> ?"
                params.append(str(exclude_commit_id))

            sql += " ORDER BY c.id DESC LIMIT 1"
            cur.execute(sql, tuple(params))
            row = cur.fetchone()
            return self._row_to_commit(row) if row else None
        
    
    def get_by_status(self, status: str, project_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.*, u.username 
                FROM commits c
                JOIN users u ON c.designer = u.id
                WHERE c.status = ? AND c.project_id=?
            """, (status,project_id,))
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]
    
    def get_pending_and_validated(self, project_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.*, u.username 
                FROM commits c
                JOIN users u ON c.designer = u.id
                WHERE (c.status = 'Pending' OR c.status = 'Validated')  AND c.project_id=?
            """, (project_id,))
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]

    def get_pending_and_validated_for_family(self, project_id: int):
        """Return pending+validated commits across all versions in the same project family.

        If the projects table doesn't support root_project_id, falls back to the single project.
        """
        with self.get_conn() as conn:
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            except Exception:
                cols = []
            if "root_project_id" not in cols:
                return self.get_pending_and_validated(int(project_id))

            try:
                cur = conn.cursor()
                cur.execute("SELECT root_project_id FROM projects WHERE id = ?", (int(project_id),))
                row = cur.fetchone()
                root_project_id = int(row[0]) if row and row[0] is not None else int(project_id)
            except Exception:
                root_project_id = int(project_id)

            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.*, u.username
                FROM commits c
                JOIN users u ON c.designer = u.id
                JOIN projects p ON p.id = c.project_id
                WHERE p.root_project_id = ?
                  AND (c.status = 'Pending' OR c.status = 'Validated')
                ORDER BY c.committed_at DESC
                """,
                (int(root_project_id),),
            )
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]
        
    def get_pending_commit_by_base_filename(self, base_file_name: str, project_id: int) -> Commit:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM commits WHERE base_file_name=? AND status='Pending' AND project_id=?", (base_file_name,project_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_commit(row)
            return None
    
    def is_duplicate_commit(self, base_file_name: str, project_id: int) -> bool:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM commits WHERE base_file_name=? AND status='Pending' AND project_id=?", (base_file_name,project_id,))
            count = cur.fetchone()[0]
            return count > 0
        
    def get_all_commits(self, project_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM commits WHERE project_id=? ORDER BY committed_at DESC", (project_id,))
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]

    def get_all_commits_for_root(self, root_project_id: int):
        """Return commits across all projects in a version family (by projects.root_project_id).

        Falls back to direct project_id filtering if the projects table does not support root_project_id.
        """
        with self.get_conn() as conn:
            # Detect support
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            except Exception:
                cols = []
            if "root_project_id" not in cols:
                return self.get_all_commits(int(root_project_id))

            cur = conn.cursor()
            cur.execute(
                """
                SELECT c.*
                FROM commits c
                JOIN projects p ON p.id = c.project_id
                WHERE p.root_project_id = ?
                ORDER BY c.committed_at DESC
                """,
                (int(root_project_id),),
            )
            rows = cur.fetchall()
            return [self._row_to_commit(r) for r in rows]

    def get_rows_by_commit_id(self, commit_id: str, project_id: int | None = None) -> list[dict]:
        """Return all file rows for a logical commit id with manager-facing joins."""
        with self.get_conn() as conn:
            params = [str(commit_id)]
            project_clause = ""
            if project_id is not None:
                project_clause = " AND c.project_id = ?"
                params.append(int(project_id))
            rows = conn.execute(
                f"""
                SELECT
                    c.*,
                    designer.username AS designer_name,
                    committer.username AS committed_by_name,
                    checker.username AS checked_by_name,
                    merger.username AS merged_by_name,
                    b.name AS part_name,
                    b.aes_number,
                    b.part_number,
                    b.drawing_number,
                    b.revision AS part_revision,
                    b.lifecycle_state AS part_lifecycle_state,
                    p.name AS project_name,
                    p.version_label AS project_version_label,
                    p.root_project_id
                FROM commits c
                LEFT JOIN users designer ON designer.id=c.designer
                LEFT JOIN users committer ON committer.id=c.committed_by
                LEFT JOIN users checker ON checker.id=c.checked_by
                LEFT JOIN users merger ON merger.id=c.merged_by
                LEFT JOIN bom b ON b.id=c.part_id
                LEFT JOIN projects p ON p.id=c.project_id
                WHERE c.commit_id = ? {project_clause}
                ORDER BY c.id
                """,
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]
    
    #retur true if the filename exists in the commit table
    def is_filename_exist(self, filename, project_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM commits WHERE filename=? AND status='Approved' AND project_id=?", (filename,project_id,))
            count = cur.fetchone()[0]
            return count > 0

    
    def get_commit_by_id(self, id: int, project_id: int) -> Commit:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM commits WHERE id=? AND project_id=?", (id,project_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_commit(row)
            return None
    
    def get_commit_by_commitid(self, id: int, project_id: int) -> Commit:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM commits WHERE id=? AND project_id=?", (id,project_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_commit(row)
            return None

    def _row_to_commit(self, row: sqlite3.Row) -> Commit:
        """Convert a sqlite3.Row into a Commit dataclass, filtering unexpected columns.

        This avoids passing database-specific column names that don't match the
        Commit constructor.
        """
        if row is None:
            return None
        data = dict(row)
        # Fields expected by Commit dataclass
        keys = [
            'id',
            'commit_id',
            'title',
            'project_id',
            'part_id',
            'type',
            'filename',
            'file_path',
            'base_file_name',
            'designer',
            'message',
            'committed_by',
            'checked_by',
            'status',
            'approved_version',
            'last_snapshot',
            'committed_at',
            'merged_at',
            'signature',
            'username',
            'step_compare_enabled',
            'step_file_path',
            'step_prev_file_path',
            'step_diff_path',
            'step_diff_summary',
            'step_diff_status',
            'step_error',
            'step_face_map_path',
        ]
        filtered = {k: data.get(k) for k in keys}
        return Commit(**filtered)
    
    def delete(self, commit_id: int, project_id: int):
        """Mark a commit group as reverted. Kept for legacy callers."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE commits SET status = 'Reverted' WHERE commit_id = ? AND project_id = ?",
                (commit_id, project_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def hard_delete_by_commit_id(self, commit_id: str, project_id: int):
        with self.get_conn() as conn:
            cur = conn.cursor()
            for sql, params in (
                ("DELETE FROM issue_commit_links WHERE commit_id = ?", (str(commit_id),)),
                ("DELETE FROM commit_file_links WHERE commit_group_id IN (SELECT id FROM commit_groups WHERE commit_id = ? AND project_id = ?)", (str(commit_id), int(project_id))),
                ("DELETE FROM commit_groups WHERE commit_id = ? AND project_id = ?", (str(commit_id), int(project_id))),
            ):
                try:
                    cur.execute(sql, params)
                except Exception:
                    pass
            cur.execute(
                "DELETE FROM commits WHERE commit_id = ? AND project_id = ?",
                (str(commit_id), int(project_id)),
            )
            conn.commit()
            return cur.rowcount


    def update_snapshot(self, filename, last_snapshot_id, project_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE commits
                SET last_snapshot=?
                WHERE filename=? AND project_id = ?
            """, (
                last_snapshot_id, filename, project_id,
            ))

    def validate(self, commit_id, checker,  project_id):
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE commits
                SET status='Validated', checked_by = ?
                WHERE commit_id=? AND project_id = ?
            """, (
                checker, commit_id, project_id,
            ))
            conn.commit()
            return cur.rowcount > 0  # True if at least one row was deleted

    def get_forced_integrated_base_names(self, project_id: int):
        """Return base_file_name list for files that were force-integrated (status='Integrated')."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT base_file_name FROM commits WHERE status='Integrated' AND project_id=?",
                    (int(project_id),),
                )
                return [r[0] for r in cur.fetchall() if r and r[0]]
            except Exception:
                return []
            
    def get_forced_integrated_filenames(self, project_id: int):
        """Return filename list for files that were force-integrated (status='Integrated')."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT filename FROM commits WHERE status='Integrated' AND project_id=?",
                    (int(project_id),),
                )
                return [r[0] for r in cur.fetchall() if r and r[0]]
            except Exception:
                return []

    def is_forced_integrated(self, base_file_name: str, project_id: int) -> bool:
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM commits WHERE status='Integrated' AND base_file_name=? AND project_id=?",
                (base_file_name, int(project_id)),
            )
            return (cur.fetchone()[0] or 0) > 0
        
    def is_forced_integrated_filename(self, filename: str, project_id: int) -> bool:
        """Check if the exact filename (with version) is already force integrated."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM commits WHERE status='Integrated' AND filename=? AND project_id=?",
                (filename, int(project_id)),
            )
            return (cur.fetchone()[0] or 0) > 0

    def insert_forced_integrated(
        self,
        part_id,
        part_type,
        filename,
        filepath,
        base_file_name,
        designer,
        committer,
        message,
        signature,
        project_id,
        title,
        commit_id,
    ) -> int:
        """Insert a special row with status='Integrated' to allow a working-dir file without committing."""
        with self.get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO commits (part_id, type, filename, file_path, base_file_name, designer, message, committed_by, status, committed_at, signature, project_id, title, commit_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id,
                    part_type,
                    filename,
                    filepath,
                    base_file_name,
                    designer,
                    message,
                    committer,
                    'Integrated',
                    datetime.now(),
                    signature,
                    project_id,
                    title,
                    commit_id,
                ),
            )
            conn.commit()
            return cur.lastrowid
