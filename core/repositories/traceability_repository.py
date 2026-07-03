import json
import re
import sqlite3
from typing import Iterable, Optional

from config import DB_NAME


JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


class TraceabilityRepository:
    """Persistence for issue/commit/Jira/file traceability links."""

    ISSUE_COMMIT_RELATIONS = {"solves", "partial_fix", "related", "regression"}
    ISSUE_FILE_ROLES = {
        "exported_pdf",
        "exported_step",
        "validation_doc",
        "inspection_report",
        "screenshot",
        "supporting_doc",
        "other",
    }

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self.ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row)

    @staticmethod
    def _table_columns(conn, table_name: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
        except Exception:
            return set()

    def _ensure_column(self, conn, table_name: str, column_name: str, definition: str):
        if column_name not in self._table_columns(conn, table_name):
            try:
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def ensure_schema(self):
        with self.get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS issue_jira_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER NOT NULL,
                    jira_key TEXT,
                    jira_url TEXT,
                    jira_summary TEXT,
                    jira_status TEXT,
                    last_checked_at TEXT,
                    created_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (issue_id) REFERENCES issues(id),
                    FOREIGN KEY (created_by) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS issue_file_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER NOT NULL,
                    part_file_id INTEGER NOT NULL,
                    part_file_version_id INTEGER,
                    file_role TEXT NOT NULL DEFAULT 'other',
                    linked_by INTEGER,
                    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    note TEXT DEFAULT '',
                    UNIQUE(issue_id, part_file_id, part_file_version_id, file_role),
                    FOREIGN KEY (issue_id) REFERENCES issues(id),
                    FOREIGN KEY (part_file_id) REFERENCES part_files(id),
                    FOREIGN KEY (part_file_version_id) REFERENCES part_file_versions(id),
                    FOREIGN KEY (linked_by) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS commit_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER,
                    commit_id TEXT NOT NULL,
                    title TEXT,
                    message TEXT,
                    author_id INTEGER,
                    created_at TEXT,
                    status TEXT,
                    reverted_at TEXT,
                    reverted_by INTEGER,
                    revert_note TEXT,
                    UNIQUE(project_id, commit_id),
                    FOREIGN KEY (project_id) REFERENCES projects(id),
                    FOREIGN KEY (author_id) REFERENCES users(id),
                    FOREIGN KEY (reverted_by) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS commit_file_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_group_id INTEGER NOT NULL,
                    commit_row_id INTEGER NOT NULL,
                    part_id INTEGER,
                    change_type TEXT NOT NULL DEFAULT 'modified',
                    UNIQUE(commit_group_id, commit_row_id),
                    FOREIGN KEY (commit_group_id) REFERENCES commit_groups(id),
                    FOREIGN KEY (commit_row_id) REFERENCES commits(id),
                    FOREIGN KEY (part_id) REFERENCES bom(id)
                );

                CREATE TABLE IF NOT EXISTS commit_engineering_file_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_id TEXT NOT NULL,
                    project_id INTEGER,
                    part_id INTEGER,
                    part_file_id INTEGER NOT NULL,
                    part_file_version_id INTEGER,
                    file_role TEXT NOT NULL DEFAULT 'other',
                    linked_by INTEGER,
                    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    note TEXT DEFAULT '',
                    UNIQUE(commit_id, part_file_id, part_file_version_id, file_role),
                    FOREIGN KEY (project_id) REFERENCES projects(id),
                    FOREIGN KEY (part_id) REFERENCES bom(id),
                    FOREIGN KEY (part_file_id) REFERENCES part_files(id),
                    FOREIGN KEY (part_file_version_id) REFERENCES part_file_versions(id),
                    FOREIGN KEY (linked_by) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS commit_validation_docs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_id TEXT NOT NULL,
                    project_id INTEGER,
                    part_id INTEGER,
                    original_filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    file_type TEXT,
                    doc_role TEXT NOT NULL DEFAULT 'validation_doc',
                    linked_by INTEGER,
                    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    note TEXT DEFAULT '',
                    UNIQUE(commit_id, stored_path),
                    FOREIGN KEY (project_id) REFERENCES projects(id),
                    FOREIGN KEY (part_id) REFERENCES bom(id),
                    FOREIGN KEY (linked_by) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS validation_doc_issue_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    validation_doc_id INTEGER NOT NULL,
                    issue_id INTEGER NOT NULL,
                    linked_by INTEGER,
                    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    note TEXT DEFAULT '',
                    UNIQUE(validation_doc_id, issue_id),
                    FOREIGN KEY (validation_doc_id) REFERENCES commit_validation_docs(id),
                    FOREIGN KEY (issue_id) REFERENCES issues(id),
                    FOREIGN KEY (linked_by) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_issue_jira_links_issue ON issue_jira_links(issue_id);
                CREATE INDEX IF NOT EXISTS idx_issue_jira_links_key ON issue_jira_links(jira_key);
                CREATE INDEX IF NOT EXISTS idx_issue_file_links_issue ON issue_file_links(issue_id);
                CREATE INDEX IF NOT EXISTS idx_issue_file_links_file ON issue_file_links(part_file_id);
                CREATE INDEX IF NOT EXISTS idx_commit_groups_commit ON commit_groups(commit_id);
                CREATE INDEX IF NOT EXISTS idx_commit_file_links_group ON commit_file_links(commit_group_id);
                CREATE INDEX IF NOT EXISTS idx_commit_eng_files_commit ON commit_engineering_file_links(commit_id);
                CREATE INDEX IF NOT EXISTS idx_commit_eng_files_file ON commit_engineering_file_links(part_file_id);
                CREATE INDEX IF NOT EXISTS idx_validation_docs_commit ON commit_validation_docs(commit_id);
                CREATE INDEX IF NOT EXISTS idx_validation_doc_issues_issue ON validation_doc_issue_links(issue_id);
                """
            )
            if self._table_exists(conn, "issue_commit_links"):
                self._ensure_column(conn, "issue_commit_links", "relation_type", "TEXT NOT NULL DEFAULT 'solves'")
                self._ensure_column(conn, "issue_commit_links", "note", "TEXT DEFAULT ''")
            self.backfill_commit_groups(conn)

    def backfill_commit_groups(self, conn=None):
        owns = conn is None
        conn = conn or self.get_conn()
        try:
            if not self._table_exists(conn, "commits"):
                return
            conn.execute(
                """
                INSERT OR IGNORE INTO commit_groups(
                    project_id, commit_id, title, message, author_id, created_at, status
                )
                SELECT
                    c.project_id,
                    c.commit_id,
                    MAX(c.title),
                    MAX(c.message),
                    MAX(c.committed_by),
                    MIN(c.committed_at),
                    MAX(c.status)
                FROM commits c
                WHERE c.commit_id IS NOT NULL AND TRIM(c.commit_id) <> ''
                GROUP BY c.project_id, c.commit_id
                """
            )
            conn.execute(
                """
                UPDATE commit_groups
                SET status = COALESCE(
                    (SELECT MAX(c.status) FROM commits c
                     WHERE c.project_id IS commit_groups.project_id
                       AND c.commit_id = commit_groups.commit_id),
                    status
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO commit_file_links(commit_group_id, commit_row_id, part_id, change_type)
                SELECT cg.id, c.id, c.part_id, 'modified'
                FROM commits c
                JOIN commit_groups cg
                  ON cg.commit_id = c.commit_id
                 AND (cg.project_id IS c.project_id OR cg.project_id = c.project_id)
                WHERE c.commit_id IS NOT NULL AND TRIM(c.commit_id) <> ''
                """
            )
        finally:
            if owns:
                conn.close()

    @staticmethod
    def normalize_jira(jira_key: Optional[str] = None, jira_url: Optional[str] = None) -> tuple[str, str]:
        key = (jira_key or "").strip().upper()
        url = (jira_url or "").strip()
        if not key and url:
            match = JIRA_KEY_RE.search(url.upper())
            if match:
                key = match.group(1)
        if not key and not url:
            raise ValueError("Jira key or Jira URL is required")
        return key, url

    def link_jira(
        self,
        issue_id: int,
        jira_key: Optional[str],
        jira_url: Optional[str],
        actor_id: Optional[int],
        jira_summary: Optional[str] = None,
        jira_status: Optional[str] = None,
    ) -> int:
        key, url = self.normalize_jira(jira_key, jira_url)
        with self.get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM issue_jira_links
                WHERE issue_id=? AND COALESCE(jira_key, '')=? AND COALESCE(jira_url, '')=?
                """,
                (int(issue_id), key, url),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE issue_jira_links
                    SET jira_summary=COALESCE(?, jira_summary),
                        jira_status=COALESCE(?, jira_status),
                        updated_at=datetime('now')
                    WHERE id=?
                    """,
                    (jira_summary, jira_status, int(existing["id"])),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO issue_jira_links(
                    issue_id, jira_key, jira_url, jira_summary, jira_status, created_by
                ) VALUES(?,?,?,?,?,?)
                """,
                (int(issue_id), key or None, url or None, jira_summary, jira_status, actor_id),
            )
            self._history(conn, issue_id, "Jira link added", actor_id, {"jira_key": key, "jira_url": url})
            return int(cur.lastrowid)

    def jira_links(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM issue_jira_links WHERE issue_id=? ORDER BY created_at DESC, id DESC",
                (int(issue_id),),
            ).fetchall()]

    def link_issue_to_commit(
        self,
        issue_ids: Iterable[int],
        commit_id: str,
        actor_id: Optional[int],
        relation_type: str = "solves",
        note: str = "",
    ):
        relation = relation_type if relation_type in self.ISSUE_COMMIT_RELATIONS else "solves"
        with self.get_conn() as conn:
            for issue_id in sorted({int(x) for x in issue_ids or []}):
                conn.execute(
                    """
                    INSERT INTO issue_commit_links(
                        issue_id, commit_id, resolution_comment, linked_by, relation_type, note
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(issue_id, commit_id) DO UPDATE SET
                        relation_type=excluded.relation_type,
                        note=COALESCE(NULLIF(excluded.note, ''), issue_commit_links.note)
                    """,
                    (issue_id, commit_id, note or "", actor_id, relation, note or ""),
                )
                old = conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()
                if relation in {"solves", "partial_fix"}:
                    conn.execute(
                        "UPDATE issues SET status='Ready For Validation', updated_at=datetime('now') WHERE id=?",
                        (issue_id,),
                    )
                self._history(
                    conn,
                    issue_id,
                    "Linked to commit",
                    actor_id,
                    {"commit_id": commit_id, "relation_type": relation, "note": note},
                )
                for doc in conn.execute(
                    "SELECT id FROM commit_validation_docs WHERE commit_id=?",
                    (str(commit_id),),
                ).fetchall():
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO validation_doc_issue_links(
                            validation_doc_id, issue_id, linked_by, note
                        ) VALUES(?,?,?,?)
                        """,
                        (int(doc["id"]), issue_id, actor_id, note or ""),
                    )
                if old and old["status"] != "Ready For Validation" and relation in {"solves", "partial_fix"}:
                    self._history(conn, issue_id, "Status changed to Ready For Validation", actor_id,
                                  {"from": old["status"], "to": "Ready For Validation"})

    def link_issue_to_engineering_file(
        self,
        issue_id: int,
        part_file_id: int,
        version_id: Optional[int],
        role: str,
        actor_id: Optional[int],
        note: str = "",
    ) -> int:
        file_role = role if role in self.ISSUE_FILE_ROLES else "other"
        with self.get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM issue_file_links
                WHERE issue_id=? AND part_file_id=?
                  AND ((part_file_version_id IS NULL AND ? IS NULL) OR part_file_version_id=?)
                  AND file_role=?
                """,
                (int(issue_id), int(part_file_id), version_id, version_id, file_role),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE issue_file_links SET note=COALESCE(NULLIF(?, ''), note) WHERE id=?",
                    (note or "", int(existing["id"])),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO issue_file_links(
                    issue_id, part_file_id, part_file_version_id, file_role, linked_by, note
                ) VALUES(?,?,?,?,?,?)
                """,
                (int(issue_id), int(part_file_id), version_id, file_role, actor_id, note or ""),
            )
            self._history(
                conn,
                issue_id,
                "Engineering file linked",
                actor_id,
                {"part_file_id": part_file_id, "version_id": version_id, "file_role": file_role, "note": note},
            )
            return int(cur.lastrowid or 0)

    def link_commit_to_engineering_file(
        self,
        commit_id: str,
        project_id: Optional[int],
        part_id: Optional[int],
        part_file_id: int,
        version_id: Optional[int],
        role: str,
        actor_id: Optional[int],
        note: str = "",
    ) -> int:
        file_role = role if role in self.ISSUE_FILE_ROLES else "other"
        with self.get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM commit_engineering_file_links
                WHERE commit_id=? AND part_file_id=?
                  AND ((part_file_version_id IS NULL AND ? IS NULL) OR part_file_version_id=?)
                  AND file_role=?
                """,
                (str(commit_id), int(part_file_id), version_id, version_id, file_role),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE commit_engineering_file_links SET note=COALESCE(NULLIF(?, ''), note) WHERE id=?",
                    (note or "", int(existing["id"])),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO commit_engineering_file_links(
                    commit_id, project_id, part_id, part_file_id, part_file_version_id,
                    file_role, linked_by, note
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    str(commit_id),
                    int(project_id) if project_id is not None else None,
                    int(part_id) if part_id is not None else None,
                    int(part_file_id),
                    int(version_id) if version_id is not None else None,
                    file_role,
                    actor_id,
                    note or "",
                ),
            )
            return int(cur.lastrowid or 0)

    def linked_issue_ids_for_commit(self, commit_id: str) -> list[int]:
        with self.get_conn() as conn:
            return [
                int(r["issue_id"])
                for r in conn.execute(
                    "SELECT DISTINCT issue_id FROM issue_commit_links WHERE commit_id=?",
                    (str(commit_id),),
                ).fetchall()
            ]

    def engineering_files_for_commit(self, commit_id: str) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT
                    l.*,
                    pf.file_type,
                    pf.display_name,
                    pf.active_version_id,
                    b.name AS part_name,
                    b.aes_number,
                    COALESCE(v.id, av.id) AS resolved_version_id,
                    COALESCE(v.version_no, av.version_no) AS version_no,
                    COALESCE(v.original_filename, av.original_filename) AS original_filename,
                    COALESCE(v.vault_rel_path, av.vault_rel_path) AS vault_rel_path,
                    COALESCE(v.sha256, av.sha256) AS sha256,
                    COALESCE(v.size_bytes, av.size_bytes) AS size_bytes,
                    COALESCE(v.created_at, av.created_at) AS version_created_at,
                    COALESCE(v.root_project_id, av.root_project_id) AS root_project_id,
                    COALESCE(v.project_version_label, av.project_version_label) AS project_version_label,
                    COALESCE(v.lifecycle_state, av.lifecycle_state) AS lifecycle_state,
                    COALESCE(v.note, av.note) AS version_note,
                    COALESCE(v.revision, av.revision, b.revision) AS revision
                FROM commit_engineering_file_links l
                JOIN part_files pf ON pf.id=l.part_file_id
                LEFT JOIN bom b ON b.id=COALESCE(l.part_id, pf.part_id)
                LEFT JOIN part_file_versions v ON v.id=l.part_file_version_id
                LEFT JOIN part_file_versions av ON av.id=pf.active_version_id
                WHERE l.commit_id=?
                ORDER BY l.linked_at DESC, l.id DESC
                """,
                (str(commit_id),),
            ).fetchall()]

    def register_validation_doc(
        self,
        commit_id: str,
        project_id: Optional[int],
        part_id: Optional[int],
        original_filename: str,
        stored_path: str,
        file_type: str,
        doc_role: str,
        actor_id: Optional[int],
        note: str = "",
    ) -> int:
        role = doc_role or "validation_doc"
        with self.get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM commit_validation_docs WHERE commit_id=? AND stored_path=?",
                (str(commit_id), stored_path),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE commit_validation_docs SET note=COALESCE(NULLIF(?, ''), note) WHERE id=?",
                    (note or "", int(existing["id"])),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO commit_validation_docs(
                    commit_id, project_id, part_id, original_filename, stored_path,
                    file_type, doc_role, linked_by, note
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(commit_id),
                    int(project_id) if project_id is not None else None,
                    int(part_id) if part_id is not None else None,
                    original_filename,
                    stored_path,
                    file_type,
                    role,
                    actor_id,
                    note or "",
                ),
            )
            return int(cur.lastrowid or 0)

    def link_validation_doc_to_issue(
        self,
        validation_doc_id: int,
        issue_id: int,
        actor_id: Optional[int],
        note: str = "",
    ) -> int:
        with self.get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM validation_doc_issue_links WHERE validation_doc_id=? AND issue_id=?",
                (int(validation_doc_id), int(issue_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE validation_doc_issue_links SET note=COALESCE(NULLIF(?, ''), note) WHERE id=?",
                    (note or "", int(existing["id"])),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO validation_doc_issue_links(validation_doc_id, issue_id, linked_by, note)
                VALUES(?,?,?,?)
                """,
                (int(validation_doc_id), int(issue_id), actor_id, note or ""),
            )
            self._history(
                conn,
                int(issue_id),
                "Validation document linked",
                actor_id,
                {"validation_doc_id": int(validation_doc_id), "note": note},
            )
            return int(cur.lastrowid or 0)

    def validation_docs_for_commit(self, commit_id: str) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT vd.*, b.name AS part_name, b.aes_number
                FROM commit_validation_docs vd
                LEFT JOIN bom b ON b.id=vd.part_id
                WHERE vd.commit_id=?
                ORDER BY vd.linked_at DESC, vd.id DESC
                """,
                (str(commit_id),),
            ).fetchall()]

    def validation_docs_for_issue(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT
                    l.id AS issue_validation_link_id,
                    l.issue_id,
                    l.linked_at AS issue_linked_at,
                    l.note AS issue_link_note,
                    vd.*,
                    b.name AS part_name,
                    b.aes_number
                FROM validation_doc_issue_links l
                JOIN commit_validation_docs vd ON vd.id=l.validation_doc_id
                LEFT JOIN bom b ON b.id=vd.part_id
                WHERE l.issue_id=?
                ORDER BY l.linked_at DESC, l.id DESC
                """,
                (int(issue_id),),
            ).fetchall()]

    def engineering_files_for_issue(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT
                    l.*,
                    pf.part_id,
                    pf.file_type,
                    pf.display_name,
                    pf.active_version_id,
                    b.name AS part_name,
                    b.aes_number,
                    COALESCE(v.id, av.id) AS resolved_version_id,
                    COALESCE(v.version_no, av.version_no) AS version_no,
                    COALESCE(v.original_filename, av.original_filename) AS original_filename,
                    COALESCE(v.vault_rel_path, av.vault_rel_path) AS vault_rel_path,
                    COALESCE(v.sha256, av.sha256) AS sha256,
                    COALESCE(v.size_bytes, av.size_bytes) AS size_bytes,
                    COALESCE(v.created_at, av.created_at) AS version_created_at,
                    COALESCE(v.root_project_id, av.root_project_id) AS root_project_id,
                    COALESCE(v.project_version_label, av.project_version_label) AS project_version_label,
                    COALESCE(v.lifecycle_state, av.lifecycle_state) AS lifecycle_state,
                    COALESCE(v.note, av.note) AS version_note,
                    COALESCE(v.revision, av.revision, b.revision) AS revision
                FROM issue_file_links l
                JOIN part_files pf ON pf.id=l.part_file_id
                LEFT JOIN bom b ON b.id=pf.part_id
                LEFT JOIN part_file_versions v ON v.id=l.part_file_version_id
                LEFT JOIN part_file_versions av ON av.id=pf.active_version_id
                WHERE l.issue_id=?
                ORDER BY l.linked_at DESC, l.id DESC
                """,
                (int(issue_id),),
            ).fetchall()]

    def issues_for_engineering_file(self, part_file_id: int, version_id: Optional[int] = None) -> list[dict]:
        where = ["l.part_file_id=?"]
        params = [int(part_file_id)]
        if version_id is not None:
            where.append("l.part_file_version_id=?")
            params.append(int(version_id))
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                f"""
                SELECT i.*, l.file_role, l.part_file_version_id, l.note, l.linked_at
                FROM issue_file_links l
                JOIN issues i ON i.id=l.issue_id
                WHERE {" AND ".join(where)}
                ORDER BY i.status <> 'Closed' DESC, i.updated_at DESC
                """,
                tuple(params),
            ).fetchall()]

    def commit_links_for_issue(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT
                    l.*,
                    cg.status AS group_status,
                    cg.reverted_at,
                    cg.revert_note,
                    MAX(c.status) AS commit_status,
                    MAX(c.merge_id) AS merge_id,
                    MAX(c.merged_at) AS merged_at,
                    MAX(c.merged_by) AS merged_by,
                    MAX(c.merge_message) AS merge_message,
                    MAX(c.approved_version) AS approved_version,
                    MAX(c.pr_path) AS pr_path,
                    MAX(c.snapshotted_in) AS snapshotted_in,
                    MAX(c.title) AS title,
                    MAX(c.message) AS message,
                    MAX(c.committed_at) AS committed_at,
                    MAX(c.committed_by) AS committed_by,
                    MAX(c.checked_by) AS checked_by,
                    MAX(c.step_compare_enabled) AS step_compare_enabled,
                    MAX(c.step_diff_status) AS step_diff_status,
                    MAX(c.step_diff_summary) AS step_diff_summary,
                    MAX(c.step_diff_path) AS step_diff_path,
                    MAX(c.step_file_path) AS step_file_path,
                    MAX(c.step_prev_file_path) AS step_prev_file_path,
                    MAX(c.step_error) AS step_error,
                    MAX(u.username) AS author_name,
                    MAX(ch.username) AS checked_by_name,
                    MAX(mu.username) AS merged_by_name
                FROM issue_commit_links l
                LEFT JOIN commit_groups cg ON cg.commit_id=l.commit_id
                LEFT JOIN commits c ON c.commit_id=l.commit_id
                LEFT JOIN users u ON u.id=c.committed_by
                LEFT JOIN users ch ON ch.id=c.checked_by
                LEFT JOIN users mu ON mu.id=c.merged_by
                WHERE l.issue_id=?
                GROUP BY l.id
                ORDER BY l.linked_at DESC
                """,
                (int(issue_id),),
            ).fetchall()]

    def commit_files(self, commit_id: str) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                """
                SELECT
                    c.id AS commit_row_id,
                    c.commit_id,
                    c.part_id,
                    c.filename,
                    c.file_path,
                    c.base_file_name,
                    c.type,
                    c.status,
                    c.committed_at,
                    c.message,
                    b.name AS part_name,
                    b.aes_number,
                    COALESCE(cfl.change_type, 'modified') AS change_type
                FROM commits c
                LEFT JOIN commit_groups cg ON cg.commit_id=c.commit_id AND (cg.project_id IS c.project_id OR cg.project_id=c.project_id)
                LEFT JOIN commit_file_links cfl ON cfl.commit_group_id=cg.id AND cfl.commit_row_id=c.id
                LEFT JOIN bom b ON b.id=c.part_id
                WHERE c.commit_id=?
                ORDER BY c.id
                """,
                (commit_id,),
            ).fetchall()]

    def mark_commit_reverted(self, commit_id: str, project_id: Optional[int], actor_id: Optional[int], note: str = "") -> bool:
        with self.get_conn() as conn:
            self.backfill_commit_groups(conn)
            if project_id is None:
                cur = conn.execute(
                    """
                    UPDATE commit_groups
                    SET status='Reverted', reverted_at=datetime('now'), reverted_by=?, revert_note=?
                    WHERE commit_id=?
                    """,
                    (actor_id, note or "", commit_id),
                )
                conn.execute("UPDATE commits SET status='Reverted' WHERE commit_id=?", (commit_id,))
            else:
                cur = conn.execute(
                    """
                    UPDATE commit_groups
                    SET status='Reverted', reverted_at=datetime('now'), reverted_by=?, revert_note=?
                    WHERE commit_id=? AND project_id=?
                    """,
                    (actor_id, note or "", commit_id, int(project_id)),
                )
                conn.execute(
                    "UPDATE commits SET status='Reverted' WHERE commit_id=? AND project_id=?",
                    (commit_id, int(project_id)),
                )
            for row in conn.execute("SELECT issue_id FROM issue_commit_links WHERE commit_id=?", (commit_id,)).fetchall():
                self._history(conn, int(row["issue_id"]), "Commit reverted", actor_id,
                              {"commit_id": commit_id, "note": note})
            return cur.rowcount > 0

    def get_issue_traceability(self, issue_id: int) -> dict:
        with self.get_conn() as conn:
            issue = conn.execute(
                """
                SELECT i.*, cu.username AS created_by_name, au.username AS assigned_to_name
                FROM issues i
                LEFT JOIN users cu ON cu.id=i.created_by
                LEFT JOIN users au ON au.id=i.assigned_to
                WHERE i.id=?
                """,
                (int(issue_id),),
            ).fetchone()
            if not issue:
                raise ValueError("Issue not found")
            parts = [dict(r) for r in conn.execute(
                """
                SELECT b.* FROM issue_parts ip
                JOIN bom b ON b.id=ip.part_id
                WHERE ip.issue_id=? ORDER BY b.name
                """,
                (int(issue_id),),
            ).fetchall()]
            history = [dict(r) for r in conn.execute(
                """
                SELECT h.*, u.username FROM issue_history h
                LEFT JOIN users u ON u.id=h.user_id
                WHERE h.issue_id=? ORDER BY h.created_at, h.id
                """,
                (int(issue_id),),
            ).fetchall()]
        commits = self.commit_links_for_issue(issue_id)
        for commit in commits:
            commit["files_changed"] = self.commit_files(commit["commit_id"])
            commit["engineering_files"] = self.engineering_files_for_commit(commit["commit_id"])
            commit["validation_docs"] = self.validation_docs_for_commit(commit["commit_id"])
        return {
            "issue": dict(issue),
            "jira_links": self.jira_links(issue_id),
            "linked_commits": commits,
            "native_creo_files": parts,
            "engineering_files": self.engineering_files_for_issue(issue_id),
            "validation_docs": self.validation_docs_for_issue(issue_id),
            "timeline": history,
        }

    def export_issue_traceability(self, project_id: int, filters: Optional[dict] = None,
                                  include_engineering_files: bool = True) -> list[dict]:
        filters = filters or {}
        where = ["(i.project_id=? OR EXISTS (SELECT 1 FROM issue_parts ip JOIN bom b ON b.id=ip.part_id WHERE ip.issue_id=i.id AND b.project_id=?))"]
        params: list = [int(project_id), int(project_id)]
        if filters.get("issue_id"):
            where.append("i.id=?")
            params.append(int(filters["issue_id"]))
        if filters.get("status") not in (None, "", "All"):
            where.append("i.status=?")
            params.append(filters["status"])
        if filters.get("assigned_to") not in (None, "", "All"):
            where.append("i.assigned_to=?")
            params.append(filters["assigned_to"])
        if filters.get("date_from"):
            where.append("date(i.created_at) >= date(?)")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            where.append("date(i.created_at) <= date(?)")
            params.append(filters["date_to"])
        if filters.get("jira_key"):
            where.append("EXISTS (SELECT 1 FROM issue_jira_links jl WHERE jl.issue_id=i.id AND jl.jira_key LIKE ?)")
            params.append(f"%{str(filters['jira_key']).upper()}%")
        if filters.get("commit_id"):
            where.append("EXISTS (SELECT 1 FROM issue_commit_links ic WHERE ic.issue_id=i.id AND ic.commit_id=?)")
            params.append(filters["commit_id"])
        if filters.get("part_file_id"):
            where.append("EXISTS (SELECT 1 FROM issue_file_links fl WHERE fl.issue_id=i.id AND fl.part_file_id=?)")
            params.append(int(filters["part_file_id"]))

        with self.get_conn() as conn:
            rows = conn.execute(
                f"SELECT i.id FROM issues i WHERE {' AND '.join(where)} ORDER BY i.updated_at DESC",
                params,
            ).fetchall()
        reports = []
        for row in rows:
            report = self.get_issue_traceability(int(row["id"]))
            if not include_engineering_files:
                report["engineering_files"] = []
            reports.append(report)
        return reports

    def _history(self, conn, issue_id: int, action: str, actor_id: Optional[int], details: Optional[dict] = None):
        conn.execute(
            """
            INSERT INTO issue_history(issue_id, action, user_id, details_json)
            VALUES(?,?,?,?)
            """,
            (int(issue_id), action, actor_id, json.dumps(details or {}, default=str)),
        )
