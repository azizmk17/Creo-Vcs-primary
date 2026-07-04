import sys
import os
import sqlite3
import re

# Add the parent directory to the Python path so we can import config.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import DB_NAME


class DatabaseConnection:
    """Minimal sqlite3 context manager replacing the missing `db` module."""

    def __enter__(self):
        self._conn = sqlite3.connect(DB_NAME)
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def __exit__(self, exc_type, *_):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


def init_db():
    """Create the schema_migrations tracking table if it does not exist."""
    with DatabaseConnection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


def get_current_version() -> int:
    """Return the highest migration version already applied, or 0 for a fresh DB."""
    try:
        with DatabaseConnection() as conn:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _table_columns(conn, table_name: str) -> list:
    if not table_name or not all(ch.isalnum() or ch == "_" for ch in table_name):
        return []
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [r[1] for r in rows]
    except Exception:
        return []


def _ensure_column(conn, table_name: str, column_name: str, column_def_sql: str):
    cols = _table_columns(conn, table_name)
    if column_name not in cols:
        try:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def_sql}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _migration_5(conn):
    """Bring legacy DBs in line with current repositories/models."""

    # Projects + user-project mapping
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            working_directory TEXT DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_projects (
            user_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            is_current INTEGER DEFAULT 0,
            UNIQUE(user_id, project_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );

        CREATE INDEX IF NOT EXISTS idx_user_projects_user_id ON user_projects(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_projects_project_id ON user_projects(project_id);
        """
    )

    # Users (some code still expects role_id)
    if "users" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "users", "role_id", "role_id INTEGER")

    # BOM: columns used across UI/services
    if "bom" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "bom", "base_file_name", "base_file_name TEXT")
        _ensure_column(conn, "bom", "base_drw_name", "base_drw_name TEXT")
        _ensure_column(conn, "bom", "pdf_path", "pdf_path TEXT")
        _ensure_column(conn, "bom", "step_path", "step_path TEXT")
        _ensure_column(conn, "bom", "project_id", "project_id INTEGER")
        _ensure_column(conn, "bom", "locked", "locked INTEGER DEFAULT 0")
        _ensure_column(conn, "bom", "revision", "revision TEXT DEFAULT 'A'")
        _ensure_column(conn, "bom", "lifecycle_state", "lifecycle_state TEXT DEFAULT 'WIP'")
        _ensure_column(conn, "bom", "released_by", "released_by INTEGER")
        _ensure_column(conn, "bom", "released_at", "released_at TEXT")
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bom_project_id ON bom(project_id)")
        except Exception:
            pass

    # Commits: bring table up to current usage (CommitRepository/MergeRepository)
    if "commits" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "commits", "type", "type TEXT")
        _ensure_column(conn, "commits", "file_path", "file_path TEXT")
        _ensure_column(conn, "commits", "base_file_name", "base_file_name TEXT")
        _ensure_column(conn, "commits", "committed_by", "committed_by INTEGER")
        _ensure_column(conn, "commits", "checked_by", "checked_by INTEGER")
        _ensure_column(conn, "commits", "signature", "signature TEXT")
        _ensure_column(conn, "commits", "project_id", "project_id INTEGER")
        _ensure_column(conn, "commits", "title", "title TEXT")
        _ensure_column(conn, "commits", "commit_id", "commit_id TEXT")
        _ensure_column(conn, "commits", "last_snapshot", "last_snapshot INTEGER")
        _ensure_column(conn, "commits", "approved_version", "approved_version TEXT")
        _ensure_column(conn, "commits", "merged_by", "merged_by INTEGER")
        _ensure_column(conn, "commits", "merge_id", "merge_id TEXT")
        _ensure_column(conn, "commits", "merged_at", "merged_at TEXT")
        _ensure_column(conn, "commits", "merge_message", "merge_message TEXT")
        _ensure_column(conn, "commits", "pr_path", "pr_path TEXT")
        _ensure_column(conn, "commits", "snapshotted_in", "snapshotted_in TEXT")
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_project_id ON commits(project_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_part_id ON commits(part_id)")
        except Exception:
            pass

    # Lock logs: repository writes signature column
    if "lock_logs" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "lock_logs", "signature", "signature INTEGER")

    # Signatures table (used by SignatureRepository)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS signature (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            note TEXT DEFAULT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )

    # Snapshots table (used by SnapshotRepository)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            snapshot_name TEXT NOT NULL,
            description TEXT DEFAULT NULL,
            snapshot_data TEXT NOT NULL,
            created_by INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_project_id ON snapshots(project_id);
        """
    )

    # part_file_versions lifecycle columns (v2 older DBs)
    if "part_file_versions" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "part_file_versions", "lifecycle_state", "lifecycle_state TEXT DEFAULT 'WIP'")
        _ensure_column(conn, "part_file_versions", "released_by", "released_by INTEGER")
        _ensure_column(conn, "part_file_versions", "released_at", "released_at TEXT")
        _ensure_column(conn, "part_file_versions", "revision", "revision TEXT")

    # RBAC: ensure 'master' exists (migration 4 maps it, but doesn't create it)
    if "roles" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        try:
            conn.execute("INSERT OR IGNORE INTO roles(name) VALUES ('master')")
        except Exception:
            pass
    if "permissions" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        # Re-apply mapping for admin/master in case master was added after v4
        try:
            conn.executescript(
                """
                INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
                SELECT r.id, p.id FROM roles r, permissions p
                WHERE r.name IN ('admin','master') AND p.name IN ('commit','merge','validate','release_files','set_revision');
                """
            )
        except Exception:
            pass


def _migration_6(conn):
    """Project versioning: model each project row as a version in a family."""

    # Add columns (safe)
    if "projects" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "projects", "root_project_id", "root_project_id INTEGER")
        _ensure_column(conn, "projects", "version_label", "version_label TEXT")
        _ensure_column(conn, "projects", "version_state", "version_state TEXT DEFAULT 'WIP'")
        _ensure_column(conn, "projects", "created_from_project_id", "created_from_project_id INTEGER")
        _ensure_column(conn, "projects", "created_from_baseline_id", "created_from_baseline_id INTEGER")
        _ensure_column(conn, "projects", "is_readonly", "is_readonly INTEGER DEFAULT 0")

        # Backfill existing rows
        conn.execute("UPDATE projects SET root_project_id = id WHERE root_project_id IS NULL")
        conn.execute("UPDATE projects SET version_label = 'A' WHERE version_label IS NULL OR TRIM(version_label) = ''")
        conn.execute("UPDATE projects SET version_state = 'WIP' WHERE version_state IS NULL OR TRIM(version_state) = ''")
        conn.execute("UPDATE projects SET is_readonly = 0 WHERE is_readonly IS NULL")

        # Enforce one version label per family (unique index, not constraint)
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_root_version ON projects(root_project_id, version_label)"
            )
        except Exception:
            pass

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_root_project_id ON projects(root_project_id)")
        except Exception:
            pass


def _migration_7(conn):
    """Append-only, per-part audit log (tamper-proof with hash chain + triggers)."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id INTEGER NOT NULL,
            project_id INTEGER DEFAULT NULL,
            user_id INTEGER DEFAULT NULL,
            event_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT DEFAULT NULL,
            message TEXT DEFAULT NULL,
            payload_json TEXT DEFAULT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            prev_hash TEXT DEFAULT NULL,
            hash TEXT NOT NULL,
            FOREIGN KEY (part_id) REFERENCES bom(id)
        );

        CREATE INDEX IF NOT EXISTS idx_audit_log_part_id ON audit_log(part_id);
        CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_log_project_id ON audit_log(project_id);

        -- Append-only: prevent updates/deletes
        CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_update
        BEFORE UPDATE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_audit_log_no_delete
        BEFORE DELETE ON audit_log
        BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        """
    )


def _migration_8(conn):
    """Store project version context on each part_file_versions row."""

    if "part_file_versions" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "part_file_versions", "root_project_id", "root_project_id INTEGER")
        _ensure_column(conn, "part_file_versions", "project_version_label", "project_version_label TEXT")

        # Backfill best-effort: set missing root_project_id/version_label based on the owning project's family.
        # If a row can't be mapped (e.g. DB missing expected relations), leave NULL so runtime can fall back.
        try:
            conn.executescript(
                """
                UPDATE part_file_versions
                SET
                    root_project_id = (SELECT p.root_project_id FROM projects p WHERE p.id = (SELECT b.project_id FROM bom b WHERE b.id = (SELECT pf.part_id FROM part_files pf WHERE pf.id = part_file_versions.file_id))),
                    project_version_label = (SELECT p.version_label FROM projects p WHERE p.id = (SELECT b.project_id FROM bom b WHERE b.id = (SELECT pf.part_id FROM part_files pf WHERE pf.id = part_file_versions.file_id)))
                WHERE root_project_id IS NULL OR project_version_label IS NULL OR TRIM(project_version_label) = '';
                """
            )
        except Exception:
            pass


def _migration_9(conn):
    """Create table to store user acknowledgement that a PDF/STEP is up-to-date.

    We keep this separate from part_file_versions so it is independent of which
    file/version is currently active.

    Keyed by (part_id, doc_type) where doc_type is 'PDF' or 'STEP'.
    acknowledged_against is the BOM.modified timestamp that the user acknowledged.
    """
    cur = conn.cursor()

    # Create table if missing
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS part_doc_ack (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL,
            acknowledged_against TEXT NOT NULL,
            acknowledged_by INTEGER DEFAULT NULL,
            acknowledged_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(part_id, doc_type)
        );
        """
    )

    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_part_doc_ack_part ON part_doc_ack(part_id)")
    except Exception:
        pass

    conn.commit()


def _migration_10(conn):
    """Add STEP compare metadata fields on commits."""

    if "commits" in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        _ensure_column(conn, "commits", "step_compare_enabled", "step_compare_enabled INTEGER DEFAULT 0")
        _ensure_column(conn, "commits", "step_file_path", "step_file_path TEXT")
        _ensure_column(conn, "commits", "step_prev_file_path", "step_prev_file_path TEXT")
        _ensure_column(conn, "commits", "step_diff_path", "step_diff_path TEXT")
        _ensure_column(conn, "commits", "step_diff_summary", "step_diff_summary TEXT")
        _ensure_column(conn, "commits", "step_diff_status", "step_diff_status TEXT")
        _ensure_column(conn, "commits", "step_error", "step_error TEXT")

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_commits_step_part_project ON commits(part_id, project_id)")
        except Exception:
            pass


def _migration_13(conn):
    """Upgrade legacy engineering-issue tables without deleting existing issue data."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "issues" not in tables:
        return

    original_issue_cols = set(_table_columns(conn, "issues"))
    for name, definition in (
        ("issue_number", "issue_number TEXT"),
        ("priority", "priority TEXT"),
        ("closed_by", "closed_by INTEGER"),
        ("closed_at", "closed_at TEXT"),
        ("archived", "archived INTEGER NOT NULL DEFAULT 0"),
        ("archive_reason", "archive_reason TEXT"),
        ("source_type", "source_type TEXT"),
        ("source_key", "source_key TEXT"),
    ):
        _ensure_column(conn, "issues", name, definition)

    issue_cols = set(_table_columns(conn, "issues"))
    if "issue_key" in issue_cols:
        conn.execute(
            """
            UPDATE issues SET issue_number=COALESCE(
                NULLIF(TRIM(issue_number), ''), NULLIF(TRIM(issue_key), ''),
                printf('ISS-%06d', id)
            )
            WHERE issue_number IS NULL OR TRIM(issue_number)=''
            """
        )
    else:
        conn.execute(
            """
            UPDATE issues SET issue_number=COALESCE(
                NULLIF(TRIM(issue_number), ''), printf('ISS-%06d', id)
            )
            WHERE issue_number IS NULL OR TRIM(issue_number)=''
            """
        )

    if "severity" in issue_cols and "priority" not in original_issue_cols:
        conn.execute(
            """
            UPDATE issues SET priority=CASE UPPER(COALESCE(severity, ''))
                WHEN 'CRITICAL' THEN 'Critical'
                WHEN 'HIGH' THEN 'High'
                WHEN 'MEDIUM' THEN 'Medium'
                WHEN 'LOW' THEN 'Low'
                WHEN 'INFO' THEN 'Low'
                ELSE COALESCE(NULLIF(priority, ''), 'Medium')
            END
            """
        )
    conn.execute("UPDATE issues SET priority='Medium' WHERE priority IS NULL OR TRIM(priority)=''")
    conn.execute(
        """
        UPDATE issues SET status=CASE UPPER(REPLACE(COALESCE(status, ''), ' ', '_'))
            WHEN 'OPEN' THEN 'Open'
            WHEN 'IN_PROGRESS' THEN 'In Progress'
            WHEN 'WAITING_VALIDATION' THEN 'Ready For Validation'
            WHEN 'READY_FOR_VALIDATION' THEN 'Ready For Validation'
            WHEN 'VALIDATED' THEN 'Ready For Validation'
            WHEN 'CLOSED' THEN 'Closed'
            WHEN 'REJECTED' THEN 'Rejected'
            ELSE COALESCE(NULLIF(status, ''), 'Open')
        END
        WHERE status IS NULL OR TRIM(status)='' OR status IN (
            'OPEN', 'IN_PROGRESS', 'WAITING_VALIDATION', 'READY_FOR_VALIDATION',
            'VALIDATED', 'CLOSED', 'REJECTED'
        )
        """
    )
    if "fixed_by" in issue_cols:
        conn.execute(
            "UPDATE issues SET closed_by=fixed_by "
            "WHERE status='Closed' AND closed_by IS NULL AND fixed_by IS NOT NULL"
        )
    if "resolved_at" in issue_cols:
        conn.execute(
            "UPDATE issues SET closed_at=resolved_at "
            "WHERE status='Closed' AND closed_at IS NULL AND resolved_at IS NOT NULL"
        )
    elif "fixed_at" in issue_cols:
        conn.execute(
            "UPDATE issues SET closed_at=fixed_at "
            "WHERE status='Closed' AND closed_at IS NULL AND fixed_at IS NOT NULL"
        )

    if "part_id" in issue_cols and "issue_parts" in tables:
        conn.execute(
            """
            INSERT INTO issue_parts(issue_id, part_id, linked_by, linked_at)
            SELECT i.id, i.part_id, i.created_by, COALESCE(i.created_at, datetime('now'))
            FROM issues i
            WHERE i.part_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM issue_parts ip
                  WHERE ip.issue_id=i.id AND ip.part_id=i.part_id
              )
            """
        )

    if "issue_comments" in tables:
        _ensure_column(conn, "issue_comments", "comment", "comment TEXT")
        _ensure_column(conn, "issue_comments", "user_id", "user_id INTEGER")
        comment_cols = set(_table_columns(conn, "issue_comments"))
        if "body" in comment_cols:
            conn.execute("UPDATE issue_comments SET comment=body WHERE comment IS NULL")
        if "created_by" in comment_cols:
            conn.execute("UPDATE issue_comments SET user_id=created_by WHERE user_id IS NULL")

    if "issue_attachments" in tables:
        _ensure_column(conn, "issue_attachments", "created_at", "created_at TEXT")
        if "uploaded_at" in set(_table_columns(conn, "issue_attachments")):
            conn.execute("UPDATE issue_attachments SET created_at=uploaded_at WHERE created_at IS NULL")

    if "issue_history" in tables:
        _ensure_column(conn, "issue_history", "user_id", "user_id INTEGER")
        if "actor_id" in set(_table_columns(conn, "issue_history")):
            conn.execute("UPDATE issue_history SET user_id=actor_id WHERE user_id IS NULL")

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_issues_issue_number ON issues(issue_number);
        CREATE INDEX IF NOT EXISTS idx_issues_project_status ON issues(project_id, status, archived);
        CREATE INDEX IF NOT EXISTS idx_issues_project_priority ON issues(project_id, priority, archived);
        CREATE INDEX IF NOT EXISTS idx_issues_assigned_to ON issues(assigned_to, status, archived);
        CREATE INDEX IF NOT EXISTS idx_issues_due_date ON issues(due_date, status, archived);
        CREATE INDEX IF NOT EXISTS idx_issue_parts_part ON issue_parts(part_id, issue_id);
        CREATE INDEX IF NOT EXISTS idx_issue_history_issue ON issue_history(issue_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_issue_commit_links_commit ON issue_commit_links(commit_id);
        CREATE INDEX IF NOT EXISTS idx_issue_comments_issue ON issue_comments(issue_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_issue_attachments_issue ON issue_attachments(issue_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_issue_notifications_user ON issue_notifications(user_id, is_read, created_at);
        """
    )


def _migration_14(conn):
    """Engineering traceability links for Jira, commit groups, and vaulted files."""
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

        CREATE INDEX IF NOT EXISTS idx_issue_jira_links_issue ON issue_jira_links(issue_id);
        CREATE INDEX IF NOT EXISTS idx_issue_jira_links_key ON issue_jira_links(jira_key);
        CREATE INDEX IF NOT EXISTS idx_issue_file_links_issue ON issue_file_links(issue_id);
        CREATE INDEX IF NOT EXISTS idx_issue_file_links_file ON issue_file_links(part_file_id);
        CREATE INDEX IF NOT EXISTS idx_commit_groups_commit ON commit_groups(commit_id);
        CREATE INDEX IF NOT EXISTS idx_commit_file_links_group ON commit_file_links(commit_group_id);
        """
    )

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "issue_commit_links" in tables:
        _ensure_column(conn, "issue_commit_links", "relation_type", "relation_type TEXT NOT NULL DEFAULT 'solves'")
        _ensure_column(conn, "issue_commit_links", "note", "note TEXT DEFAULT ''")

    if "commits" in tables:
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
            INSERT OR IGNORE INTO commit_file_links(commit_group_id, commit_row_id, part_id, change_type)
            SELECT cg.id, c.id, c.part_id, 'modified'
            FROM commits c
            JOIN commit_groups cg
              ON cg.commit_id = c.commit_id
             AND (cg.project_id IS c.project_id OR cg.project_id = c.project_id)
            WHERE c.commit_id IS NOT NULL AND TRIM(c.commit_id) <> ''
            """
        )


def _legacy_note_revision(note: str):
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


def _migration_15(conn):
    """Move legacy revision-only part-file version notes into revision column."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for key, value in (
        ("app_version", "2.2.0"),
        ("minimum_app_version", "2.2.0"),
        ("db_schema_version", "15"),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES(?, ?)",
            (key, value),
        )

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "part_file_versions" not in tables:
        return
    _ensure_column(conn, "part_file_versions", "revision", "revision TEXT")
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
        revision = _legacy_note_revision(row["note"])
        if not revision:
            continue
        conn.execute(
            "UPDATE part_file_versions SET revision=?, note=NULL WHERE id=?",
            (revision, int(row["id"])),
        )


def _migration_16(conn):
    """Allow project versions to share the same clean project name."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for key, value in (
        ("app_version", "2.2.0"),
        ("minimum_app_version", "2.2.0"),
        ("db_schema_version", "16"),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES(?, ?)",
            (key, value),
        )

    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"
    ).fetchone()
    if not table or not table["sql"]:
        return

    table_sql = str(table["sql"])
    if "UNIQUE" in table_sql.upper():
        new_sql = re.sub(
            r"CREATE\s+TABLE\s+projects\b",
            "CREATE TABLE projects_new",
            table_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        new_sql = re.sub(
            r"\bname\s+TEXT\s+NOT\s+NULL\s+UNIQUE\b",
            "name TEXT NOT NULL",
            new_sql,
            count=1,
            flags=re.IGNORECASE,
        )
        new_sql = re.sub(
            r",\s*UNIQUE\s*\(\s*name\s*\)",
            "",
            new_sql,
            flags=re.IGNORECASE,
        )

        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DROP TABLE IF EXISTS projects_new")
        conn.execute(new_sql)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
        col_sql = ", ".join(cols)
        conn.execute(f"INSERT INTO projects_new ({col_sql}) SELECT {col_sql} FROM projects")
        conn.execute("DROP TABLE projects")
        conn.execute("ALTER TABLE projects_new RENAME TO projects")
        conn.execute("PRAGMA foreign_keys=ON")

    cols = set(_table_columns(conn, "projects"))
    if {"name", "version_label"}.issubset(cols):
        conn.execute(
            """
            UPDATE projects
            SET name = SUBSTR(name, 1, LENGTH(name) - LENGTH(version_label) - 2)
            WHERE version_label IS NOT NULL
              AND TRIM(version_label) <> ''
              AND UPPER(name) LIKE '%__' || UPPER(TRIM(version_label))
            """
        )

    if {"root_project_id", "version_label"}.issubset(cols):
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_root_version ON projects(root_project_id, version_label)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_root_project_id ON projects(root_project_id)")


MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME DEFAULT NULL
    );


    CREATE TABLE IF NOT EXISTS bom (
        id INTEGER PRIMARY KEY AUTOINCREMENT,              
        type TEXT NOT NULL,                 -- e.g. "asm", "prt"
        name TEXT NOT NULL,                 -- human-readable name
        part_number TEXT,
        drawing_number TEXT,
        aes_number TEXT,                    -- e.g. "DJB01"
        filename TEXT,
        drawing TEXT,
        material TEXT,
        weight TEXT,
        notes TEXT,
        status TEXT DEFAULT 'Design',  -- e.g. "Design", "Released"
        created TEXT NOT NULL,              -- ISO timestamp
        modified TEXT NOT NULL             -- ISO timestamp
    );

    CREATE TABLE IF NOT EXISTS bom_children (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_id INTEGER NOT NULL,  -- e.g. "DJB01"
        child_id INTEGER NOT NULL,   -- e.g. "E01"
        quantity INTEGER DEFAULT 1,
        FOREIGN KEY (parent_id) REFERENCES bom (id),
        FOREIGN KEY (child_id) REFERENCES bom (id)
    );

    CREATE INDEX IF NOT EXISTS idx_bom_children_parent ON bom_children(parent_id);
    CREATE INDEX IF NOT EXISTS idx_bom_children_child  ON bom_children(child_id);

    CREATE TABLE IF NOT EXISTS locks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id INTEGER UNIQUE NOT NULL,         -- FK to bom(id)
        user_id INTEGER NOT NULL,         -- FK to users(id)
        FOREIGN KEY (part_id) REFERENCES bom(id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        UNIQUE(part_id)  -- only one user can lock a part at a time
    );


    CREATE TABLE IF NOT EXISTS lock_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id INTEGER NOT NULL,         -- FK to bom(id)
        user_id INTEGER NOT NULL,         -- who performed the action
        action TEXT NOT NULL,             -- 'lock' or 'unlock'
        timestamp TEXT NOT NULL DEFAULT (datetime('now')), -- ISO timestamp
        FOREIGN KEY (part_id) REFERENCES bom(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );




    CREATE TABLE IF NOT EXISTS commits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        designer TEXT NOT NULL,
        committed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        message TEXT NOT NULL,
        checked_by TEXT DEFAULT NULL,
        committed_by TEXT DEFAULT NULL,
        status TEXT DEFAULT 'PENDING',
        merged_by TEXT DEFAULT NULL,
        merge_id TEXT DEFAULT NULL,
        merged_at DATETIME DEFAULT NULL,
        merge_message TEXT DEFAULT NULL,
        approved_version TEXT DEFAULT NULL,
        pr_path TEXT DEFAULT NULL,
        snapshotted_in TEXT DEFAULT NULL,
        FOREIGN KEY (part_id) REFERENCES bom (id)
    );

    

    """,

    2: """
    CREATE TABLE IF NOT EXISTS part_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_id INTEGER NOT NULL,
        file_type TEXT NOT NULL, -- e.g. 'PDF', 'STEP', 'DWG', 'OTHER'
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
        FOREIGN KEY (file_id) REFERENCES part_files(id),
        UNIQUE(file_id, version_no)
    );

    CREATE INDEX IF NOT EXISTS idx_part_file_versions_file_id ON part_file_versions(file_id);
    """,

    3: """
    -- PLM-lite: baselines
    -- Note: PLM-lite columns on existing tables are created best-effort at runtime
    -- (see repositories) to avoid SQLite duplicate-column migration failures.

    CREATE TABLE IF NOT EXISTS baselines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        created_by INTEGER DEFAULT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        include_children INTEGER DEFAULT 1,
        part_ids_json TEXT DEFAULT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_baselines_project_id ON baselines(project_id);

    CREATE TABLE IF NOT EXISTS baseline_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        baseline_id INTEGER NOT NULL,
        part_id INTEGER NOT NULL,
        file_type TEXT NOT NULL,
        file_id INTEGER DEFAULT NULL,
        version_id INTEGER DEFAULT NULL,
        FOREIGN KEY (baseline_id) REFERENCES baselines(id)
    );

    CREATE INDEX IF NOT EXISTS idx_baseline_files_baseline_id ON baseline_files(baseline_id);
    """,

    4: """
    -- RBAC: roles + permissions + mappings
    CREATE TABLE IF NOT EXISTS roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS user_roles (
        user_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        project_id INTEGER DEFAULT NULL,
        UNIQUE(user_id, role_id, project_id)
    );

    CREATE TABLE IF NOT EXISTS role_permissions (
        role_id INTEGER NOT NULL,
        permission_id INTEGER NOT NULL,
        project_id INTEGER DEFAULT NULL,
        UNIQUE(role_id, permission_id, project_id)
    );

    -- Seed roles (don't remove existing roles like 'master')
    INSERT OR IGNORE INTO roles(name) VALUES ('admin');
    INSERT OR IGNORE INTO roles(name) VALUES ('checker');
    INSERT OR IGNORE INTO roles(name) VALUES ('designer');

    -- Seed permissions (existing + PLM-lite)
    INSERT OR IGNORE INTO permissions(name) VALUES ('commit');
    INSERT OR IGNORE INTO permissions(name) VALUES ('merge');
    INSERT OR IGNORE INTO permissions(name) VALUES ('validate');
    INSERT OR IGNORE INTO permissions(name) VALUES ('release_files');
    INSERT OR IGNORE INTO permissions(name) VALUES ('set_revision');

    -- Map permissions to roles
    INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
    SELECT r.id, p.id FROM roles r, permissions p
    WHERE r.name IN ('admin','master') AND p.name IN ('commit','merge','validate','release_files','set_revision');

    INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
    SELECT r.id, p.id FROM roles r, permissions p
    WHERE r.name = 'checker' AND p.name IN ('validate','release_files');

    INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
    SELECT r.id, p.id FROM roles r, permissions p
    WHERE r.name = 'designer' AND p.name IN ('commit');
    """,

    5: _migration_5,

    6: _migration_6,

    7: _migration_7,

    8: _migration_8,

    9: _migration_9,

    10: _migration_10,

    11: """
CREATE TABLE IF NOT EXISTS app_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
""",

    12: """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open',
    priority TEXT NOT NULL DEFAULT 'Medium',
    category TEXT NOT NULL DEFAULT 'Design',
    created_by INTEGER,
    assigned_to INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    due_date TEXT,
    project_id INTEGER NOT NULL,
    closed_by INTEGER,
    closed_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    archive_reason TEXT,
    source_type TEXT,
    source_key TEXT,
    FOREIGN KEY (created_by) REFERENCES users(id),
    FOREIGN KEY (assigned_to) REFERENCES users(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (closed_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    part_id INTEGER NOT NULL,
    linked_by INTEGER,
    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(issue_id, part_id),
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (part_id) REFERENCES bom(id),
    FOREIGN KEY (linked_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    user_id INTEGER,
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_commit_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    commit_id TEXT NOT NULL,
    resolution_comment TEXT DEFAULT '',
    linked_by INTEGER,
    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
    validation_status TEXT NOT NULL DEFAULT 'Pending',
    validated_by INTEGER,
    validated_at TEXT,
    validation_comment TEXT DEFAULT '',
    UNIQUE(issue_id, commit_id),
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (linked_by) REFERENCES users(id),
    FOREIGN KEY (validated_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    uploaded_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (uploaded_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    user_id INTEGER,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS issue_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    read_at TEXT,
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
INSERT OR IGNORE INTO permissions(name) VALUES ('manage_issues');
INSERT OR IGNORE INTO permissions(name) VALUES ('validate_issues');
INSERT OR IGNORE INTO permissions(name) VALUES ('archive_issues');
INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id FROM roles r, permissions p
WHERE r.name IN ('admin','master') AND p.name IN ('manage_issues','validate_issues','archive_issues');
INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id FROM roles r, permissions p
WHERE r.name = 'checker' AND p.name IN ('manage_issues','validate_issues');
INSERT OR IGNORE INTO role_permissions(role_id, permission_id)
SELECT r.id, p.id FROM roles r, permissions p
WHERE r.name = 'designer' AND p.name = 'manage_issues';
""",

    13: _migration_13,

    14: _migration_14,

    15: _migration_15,

    16: _migration_16,

}

# --- Migration ---
def migrate():
    init_db()  
    current_version = get_current_version()
    latest_version = max(MIGRATIONS.keys())

    for version in sorted(MIGRATIONS.keys()):
        if version > current_version:
            print(f"WARNING: Database is outdated (current: {current_version}, latest: {latest_version}).")
            print(f"Applying migration {version}...")
            migration = MIGRATIONS[version]
            
            with DatabaseConnection() as conn:
                cur = conn.cursor()
                if callable(migration):
                    migration(conn)
                else:
                    cur.executescript(migration)
                cur.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            print(f"Migration {version} applied.")

    print("All migrations are up to date.")
