import sys
import os
import sqlite3
import re
import json

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


def _migration_17(conn):
    """Correct inverted check-in/check-out action names in existing audit rows."""
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
        ("db_schema_version", "17"),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata(key, value) VALUES(?, ?)",
            (key, value),
        )

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "lock_logs" in tables:
        conn.execute(
            """
            UPDATE lock_logs
            SET action = CASE LOWER(action)
                WHEN 'checkin' THEN 'checkout'
                WHEN 'checkout' THEN 'checkin'
                ELSE action
            END
            WHERE LOWER(action) IN ('checkin', 'checkout')
            """
        )

    if "signature" in tables:
        conn.execute(
            """
            UPDATE signature
            SET action = CASE LOWER(action)
                WHEN 'checkin' THEN 'checkout'
                WHEN 'checkout' THEN 'checkin'
                ELSE action
            END
            WHERE LOWER(action) IN ('checkin', 'checkout')
              AND id IN (SELECT signature FROM lock_logs WHERE signature IS NOT NULL)
            """
        )


def _migration_18(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(bom_children)").fetchall()]
    if "sort_order" not in cols:
        conn.execute("ALTER TABLE bom_children ADD COLUMN sort_order INTEGER DEFAULT 0")
    conn.execute(
        """
        UPDATE bom_children
        SET sort_order = id
        WHERE sort_order IS NULL OR sort_order = 0
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bom_children_parent_order ON bom_children(parent_id, sort_order, id)")


def _migration_19(conn):
    """Add project-scoped categories with many-to-many BOM item assignments."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bom_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project_id, name),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bom_item_categories (
            bom_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            assigned_by INTEGER,
            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (bom_id, category_id),
            FOREIGN KEY (bom_id) REFERENCES bom(id),
            FOREIGN KEY (category_id) REFERENCES bom_categories(id),
            FOREIGN KEY (assigned_by) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_bom_categories_project_name
            ON bom_categories(project_id, name);
        CREATE INDEX IF NOT EXISTS idx_bom_item_categories_bom
            ON bom_item_categories(bom_id);
        CREATE INDEX IF NOT EXISTS idx_bom_item_categories_category
            ON bom_item_categories(category_id);
        """
    )


def _migration_20(conn):
    """Add a non-engineering folder layer for organizing BOM tree occurrences."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bom_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            parent_bom_id INTEGER,
            parent_folder_id INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (parent_bom_id) REFERENCES bom(id),
            FOREIGN KEY (parent_folder_id) REFERENCES bom_folders(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bom_folder_items (
            folder_id INTEGER NOT NULL,
            bom_id INTEGER NOT NULL,
            assigned_by INTEGER,
            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (folder_id, bom_id),
            FOREIGN KEY (folder_id) REFERENCES bom_folders(id),
            FOREIGN KEY (bom_id) REFERENCES bom(id),
            FOREIGN KEY (assigned_by) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_bom_folders_project_parent
            ON bom_folders(project_id, parent_bom_id, parent_folder_id, sort_order, id);
        CREATE INDEX IF NOT EXISTS idx_bom_folder_items_folder
            ON bom_folder_items(folder_id);
        CREATE INDEX IF NOT EXISTS idx_bom_folder_items_bom
            ON bom_folder_items(bom_id);
        """
    )


def _migration_21(conn):
    """Add reusable private and project-shared BOM filter definitions."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bom_saved_filters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            owner_user_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            definition_json TEXT NOT NULL,
            is_shared INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project_id, owner_user_id, name),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (owner_user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_bom_saved_filters_visible
            ON bom_saved_filters(project_id, is_shared, owner_user_id, sort_order);
        """
    )


def _migration_22(conn):
    """Add object revisions, automatic iterations, and exact assembly bindings."""
    _ensure_column(conn, "bom", "current_revision_id", "current_revision_id INTEGER")
    _ensure_column(conn, "bom", "current_iteration_id", "current_iteration_id INTEGER")
    _ensure_column(conn, "baseline_files", "object_iteration_id", "object_iteration_id INTEGER")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bom_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bom_id INTEGER NOT NULL,
            revision_code TEXT NOT NULL COLLATE NOCASE,
            state TEXT NOT NULL DEFAULT 'In Work',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by INTEGER,
            released_at TEXT,
            released_by INTEGER,
            release_note TEXT,
            UNIQUE(bom_id, revision_code),
            FOREIGN KEY (bom_id) REFERENCES bom(id),
            FOREIGN KEY (created_by) REFERENCES users(id),
            FOREIGN KEY (released_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bom_iterations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id INTEGER NOT NULL,
            iteration_number INTEGER NOT NULL,
            checkin_note TEXT,
            source_commit_id TEXT,
            object_data_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            created_by INTEGER,
            UNIQUE(revision_id, iteration_number),
            FOREIGN KEY (revision_id) REFERENCES bom_revisions(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS bom_iteration_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_iteration_id INTEGER NOT NULL,
            usage_id INTEGER,
            child_bom_id INTEGER NOT NULL,
            child_revision_id INTEGER NOT NULL,
            child_iteration_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(parent_iteration_id, usage_id),
            FOREIGN KEY (parent_iteration_id) REFERENCES bom_iterations(id),
            FOREIGN KEY (child_bom_id) REFERENCES bom(id),
            FOREIGN KEY (child_revision_id) REFERENCES bom_revisions(id),
            FOREIGN KEY (child_iteration_id) REFERENCES bom_iterations(id)
        );

        CREATE TABLE IF NOT EXISTS bom_working_bindings (
            parent_bom_id INTEGER NOT NULL,
            usage_id INTEGER NOT NULL,
            child_bom_id INTEGER NOT NULL,
            child_revision_id INTEGER NOT NULL,
            child_iteration_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_by INTEGER,
            PRIMARY KEY(parent_bom_id, usage_id),
            FOREIGN KEY (parent_bom_id) REFERENCES bom(id),
            FOREIGN KEY (child_bom_id) REFERENCES bom(id),
            FOREIGN KEY (child_revision_id) REFERENCES bom_revisions(id),
            FOREIGN KEY (child_iteration_id) REFERENCES bom_iterations(id),
            FOREIGN KEY (updated_by) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_bom_revisions_bom
            ON bom_revisions(bom_id, id);
        CREATE INDEX IF NOT EXISTS idx_bom_iterations_revision
            ON bom_iterations(revision_id, iteration_number);
        CREATE INDEX IF NOT EXISTS idx_bom_iteration_bindings_parent
            ON bom_iteration_bindings(parent_iteration_id, sort_order, id);
        CREATE INDEX IF NOT EXISTS idx_bom_iteration_bindings_child
            ON bom_iteration_bindings(child_bom_id, child_iteration_id);
        CREATE INDEX IF NOT EXISTS idx_bom_working_bindings_parent
            ON bom_working_bindings(parent_bom_id, usage_id);
        """
    )
    _ensure_column(conn, "bom_iterations", "source_commit_id", "source_commit_id TEXT")
    _ensure_column(conn, "bom_iterations", "object_data_json", "object_data_json TEXT")
    iteration_columns = set(_table_columns(conn, "bom_iterations"))
    if "commit_id" in iteration_columns and "source_commit_id" in iteration_columns:
        conn.execute(
            """
            UPDATE bom_iterations
            SET source_commit_id=CAST(commit_id AS TEXT)
            WHERE source_commit_id IS NULL AND commit_id IS NOT NULL
            """
        )

    # Legacy rows have no reconstructable object-iteration history. Preserve the
    # current visible revision as iteration 1, then snapshot the current structure.
    rows = conn.execute(
        """
        SELECT id, COALESCE(NULLIF(TRIM(revision), ''), 'A') AS revision_code,
               lifecycle_state, status
        FROM bom ORDER BY id
        """
    ).fetchall()
    for row in rows:
        bom_id = int(row[0])
        revision_code = str(row[1] or "A").strip() or "A"
        lifecycle = str(row[2] or row[3] or "").strip().lower()
        state = "Released" if "release" in lifecycle else "In Work"
        conn.execute(
            """
            INSERT OR IGNORE INTO bom_revisions(bom_id, revision_code, state)
            VALUES(?,?,?)
            """,
            (bom_id, revision_code, state),
        )
        revision_id = int(conn.execute(
            "SELECT id FROM bom_revisions WHERE bom_id=? AND revision_code=? COLLATE NOCASE",
            (bom_id, revision_code),
        ).fetchone()[0])
        object_data = dict(conn.execute(
            """
            SELECT type, name, part_number, drawing_number, aes_number, filename, drawing,
                   base_file_name, base_drw_name, material, weight, notes, pdf_path, step_path
            FROM bom WHERE id=?
            """,
            (bom_id,),
        ).fetchone())
        object_json = json.dumps(object_data, ensure_ascii=True, sort_keys=True)
        insert_columns = ["revision_id", "iteration_number", "object_data_json"]
        insert_values = [revision_id, 1, object_json]
        if "folder_path" in iteration_columns:
            insert_columns.append("folder_path")
            insert_values.append("")
        conn.execute(
            f"""
            INSERT OR IGNORE INTO bom_iterations({','.join(insert_columns)})
            VALUES({','.join('?' for _ in insert_columns)})
            """,
            insert_values,
        )
        iteration_row = conn.execute(
            "SELECT id FROM bom_iterations WHERE revision_id=? AND iteration_number=1",
            (revision_id,),
        ).fetchone()
        if not iteration_row:
            raise RuntimeError(
                f"Could not initialize iteration 1 for BOM {bom_id}, revision {revision_code}."
            )
        iteration_id = int(iteration_row[0])
        conn.execute(
            """
            UPDATE bom_iterations
            SET object_data_json=COALESCE(object_data_json, ?)
            WHERE id=?
            """,
            (object_json, iteration_id),
        )
        conn.execute(
            "UPDATE bom SET current_revision_id=?, current_iteration_id=? WHERE id=?",
            (revision_id, iteration_id, bom_id),
        )

    relations = conn.execute(
        """
        SELECT bc.id, bc.parent_id, bc.child_id, COALESCE(bc.quantity, 1),
               COALESCE(bc.sort_order, bc.id), parent.current_iteration_id,
               child.current_revision_id, child.current_iteration_id
        FROM bom_children bc
        JOIN bom parent ON parent.id=bc.parent_id
        JOIN bom child ON child.id=bc.child_id
        ORDER BY bc.parent_id, COALESCE(bc.sort_order, bc.id), bc.id
        """
    ).fetchall()
    for row in relations:
        if row[5] is None or row[6] is None or row[7] is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO bom_iteration_bindings(
                parent_iteration_id, usage_id, child_bom_id, child_revision_id,
                child_iteration_id, quantity, sort_order
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (int(row[5]), int(row[0]), int(row[2]), int(row[6]), int(row[7]), int(row[3]), int(row[4])),
        )


def _migration_23(conn):
    """Track a deferred new revision when a Released object is checked out."""
    _ensure_column(conn, "bom", "pending_revision_code", "pending_revision_code TEXT")


def _migration_24(conn):
    """Add named frozen assembly configurations and their exact occurrence manifests."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assembly_configurations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            series_key TEXT NOT NULL DEFAULT '',
            configuration_name TEXT NOT NULL DEFAULT '',
            version_number INTEGER NOT NULL DEFAULT 1,
            based_on_configuration_id INTEGER,
            purpose TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            root_bom_id INTEGER NOT NULL,
            root_iteration_id INTEGER NOT NULL,
            root_version_label TEXT NOT NULL,
            root_name TEXT NOT NULL DEFAULT '',
            source_project_version TEXT NOT NULL DEFAULT '',
            storage_rel_path TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'Draft',
            member_count INTEGER NOT NULL DEFAULT 0,
            file_count INTEGER NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            draft_updated_at TEXT,
            frozen_at TEXT,
            frozen_by INTEGER,
            last_built_at TEXT,
            last_built_path TEXT,
            UNIQUE(project_id, name),
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (root_bom_id) REFERENCES bom(id),
            FOREIGN KEY (root_iteration_id) REFERENCES bom_iterations(id),
            FOREIGN KEY (created_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS assembly_configuration_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            configuration_id INTEGER NOT NULL,
            sequence_no INTEGER NOT NULL,
            occurrence_path TEXT NOT NULL,
            parent_occurrence_path TEXT,
            usage_id INTEGER,
            bom_id INTEGER NOT NULL,
            revision_id INTEGER NOT NULL,
            iteration_id INTEGER NOT NULL,
            version_label TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            position INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            type TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            aes_number TEXT NOT NULL DEFAULT '',
            part_number TEXT NOT NULL DEFAULT '',
            drawing_number TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            drawing TEXT NOT NULL DEFAULT '',
            native_source_rel_path TEXT NOT NULL DEFAULT '',
            drawing_source_rel_path TEXT NOT NULL DEFAULT '',
            native_frozen_rel_path TEXT NOT NULL DEFAULT '',
            drawing_frozen_rel_path TEXT NOT NULL DEFAULT '',
            native_sha256 TEXT NOT NULL DEFAULT '',
            drawing_sha256 TEXT NOT NULL DEFAULT '',
            UNIQUE(configuration_id, occurrence_path),
            FOREIGN KEY (configuration_id) REFERENCES assembly_configurations(id),
            FOREIGN KEY (bom_id) REFERENCES bom(id),
            FOREIGN KEY (revision_id) REFERENCES bom_revisions(id),
            FOREIGN KEY (iteration_id) REFERENCES bom_iterations(id)
        );

        CREATE INDEX IF NOT EXISTS idx_assembly_configurations_project
            ON assembly_configurations(project_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_assembly_configuration_members_config
            ON assembly_configuration_members(configuration_id, sequence_no);
        CREATE INDEX IF NOT EXISTS idx_assembly_configuration_members_iteration
            ON assembly_configuration_members(iteration_id);
        """
    )
    _ensure_column(
        conn,
        "assembly_configuration_members",
        "native_source_rel_path",
        "native_source_rel_path TEXT NOT NULL DEFAULT ''",
    )
    _ensure_column(
        conn,
        "assembly_configuration_members",
        "drawing_source_rel_path",
        "drawing_source_rel_path TEXT NOT NULL DEFAULT ''",
    )


def _migration_25(conn):
    """Add editable Draft/Frozen configuration versions."""
    for column, definition in (
        ("series_key", "series_key TEXT NOT NULL DEFAULT ''"),
        ("configuration_name", "configuration_name TEXT NOT NULL DEFAULT ''"),
        ("version_number", "version_number INTEGER NOT NULL DEFAULT 1"),
        ("based_on_configuration_id", "based_on_configuration_id INTEGER"),
        ("draft_updated_at", "draft_updated_at TEXT"),
        ("frozen_at", "frozen_at TEXT"),
        ("frozen_by", "frozen_by INTEGER"),
    ):
        _ensure_column(conn, "assembly_configurations", column, definition)
    conn.execute(
        """
        UPDATE assembly_configurations
        SET series_key='legacy:' || id
        WHERE trim(COALESCE(series_key,''))=''
        """
    )
    conn.execute(
        """
        UPDATE assembly_configurations
        SET configuration_name=name
        WHERE trim(COALESCE(configuration_name,''))=''
        """
    )
    conn.execute(
        """
        UPDATE assembly_configurations
        SET version_number=1
        WHERE version_number IS NULL OR version_number < 1
        """
    )
    conn.execute(
        """
        UPDATE assembly_configurations
        SET state='Frozen'
        WHERE lower(trim(COALESCE(state,''))) NOT IN ('draft','frozen')
           OR trim(COALESCE(state,''))=''
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_assembly_configurations_series
        ON assembly_configurations(project_id, series_key, version_number DESC)
        """
    )


def _migration_26(conn):
    """Bind history-producing records to the exact BOM object iteration."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table_name in ("commits", "lock_logs", "part_file_versions"):
        if table_name in tables:
            _ensure_column(
                conn,
                table_name,
                "object_iteration_id",
                "object_iteration_id INTEGER",
            )

    if "commits" in tables:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_commits_object_iteration ON commits(object_iteration_id)"
        )
    if "lock_logs" in tables:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lock_logs_object_iteration ON lock_logs(object_iteration_id)"
        )
    if "part_file_versions" in tables:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_part_file_versions_object_iteration "
            "ON part_file_versions(object_iteration_id)"
        )


def _migration_27(conn):
    """Add managed vault metadata and immutable iteration file manifests."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "part_files" in tables:
        _ensure_column(conn, "part_files", "file_role", "file_role TEXT NOT NULL DEFAULT 'document'")
        _ensure_column(conn, "part_files", "deleted_at", "deleted_at TEXT")
        conn.execute(
            """
            UPDATE part_files
            SET file_role=CASE UPPER(TRIM(COALESCE(file_type,'')))
                WHEN 'PDF' THEN 'generated_pdf'
                WHEN 'STEP' THEN 'generated_step'
                WHEN 'STP' THEN 'generated_step'
                WHEN 'DRW' THEN 'drawing'
                ELSE 'document'
            END
            WHERE TRIM(COALESCE(file_role,''))='' OR file_role='document'
            """
        )
    if "part_file_versions" in tables:
        for column, definition in (
            ("storage_scheme", "storage_scheme TEXT NOT NULL DEFAULT 'legacy'"),
            ("source_kind", "source_kind TEXT NOT NULL DEFAULT 'manual'"),
            ("source_commit_id", "source_commit_id TEXT"),
            ("integrity_status", "integrity_status TEXT NOT NULL DEFAULT 'Unknown'"),
            ("derived_from_version_id", "derived_from_version_id INTEGER"),
            ("deleted_at", "deleted_at TEXT"),
        ):
            _ensure_column(conn, "part_file_versions", column, definition)
        conn.execute(
            """
            UPDATE part_file_versions
            SET source_kind='legacy',
                integrity_status=CASE
                    WHEN sha256 IS NOT NULL AND TRIM(sha256)<>'' THEN 'Available'
                    ELSE 'Unknown'
                END
            WHERE storage_scheme='legacy'
            """
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bom_iteration_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bom_id INTEGER NOT NULL,
            iteration_id INTEGER NOT NULL,
            binding_key TEXT NOT NULL,
            file_role TEXT NOT NULL,
            file_type TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT 'legacy',
            part_file_id INTEGER,
            part_file_version_id INTEGER,
            filename TEXT NOT NULL,
            file_revision TEXT NOT NULL DEFAULT '',
            creo_iteration INTEGER,
            storage_scheme TEXT NOT NULL DEFAULT 'legacy_reference',
            vault_rel_path TEXT NOT NULL DEFAULT '',
            sha256 TEXT,
            size_bytes INTEGER,
            integrity_status TEXT NOT NULL DEFAULT 'Unknown',
            lifecycle_state TEXT NOT NULL DEFAULT 'In Work',
            source_commit_id TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(iteration_id, binding_key),
            FOREIGN KEY (bom_id) REFERENCES bom(id),
            FOREIGN KEY (iteration_id) REFERENCES bom_iterations(id),
            FOREIGN KEY (part_file_id) REFERENCES part_files(id),
            FOREIGN KEY (part_file_version_id) REFERENCES part_file_versions(id)
        );
        CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_iteration
            ON bom_iteration_files(iteration_id, file_role);
        CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_bom
            ON bom_iteration_files(bom_id, iteration_id);
        CREATE INDEX IF NOT EXISTS idx_bom_iteration_files_version
            ON bom_iteration_files(part_file_version_id);
        """
    )


def _migration_28(conn):
    """Restore explicit attachment revision in managed-file manifests."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "bom_iteration_files" not in tables:
        return
    _ensure_column(
        conn, "bom_iteration_files", "file_revision",
        "file_revision TEXT NOT NULL DEFAULT ''",
    )
    if "part_file_versions" in tables:
        conn.execute(
            """
            UPDATE bom_iteration_files
            SET file_revision=COALESCE((
                SELECT v.revision
                FROM part_file_versions v
                WHERE v.id=bom_iteration_files.part_file_version_id
            ), '')
            WHERE part_file_version_id IS NOT NULL
              AND TRIM(COALESCE(file_revision,''))=''
            """
        )


def _migration_29(conn):
    """Add versioned CAD/EBOM policy fields without creating another BOM store."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "bom" in tables:
        _ensure_column(
            conn, "bom", "classification",
            "classification TEXT NOT NULL DEFAULT 'PHYSICAL'",
        )
        _ensure_column(
            conn, "bom", "default_ebom_behavior",
            "default_ebom_behavior TEXT NOT NULL DEFAULT 'NORMAL'",
        )
        _ensure_column(
            conn, "bom", "cad_requirement",
            "cad_requirement TEXT NOT NULL DEFAULT 'OPTIONAL'",
        )
        _ensure_column(
            conn, "bom", "drawing_requirement",
            "drawing_requirement TEXT NOT NULL DEFAULT 'OPTIONAL'",
        )
        conn.execute(
            """
            UPDATE bom
            SET classification=CASE
                    WHEN UPPER(TRIM(COALESCE(classification,'')))
                         IN ('PHYSICAL','CAD_ONLY','REFERENCE','SKELETON')
                    THEN UPPER(TRIM(classification)) ELSE 'PHYSICAL' END,
                default_ebom_behavior=CASE
                    WHEN UPPER(TRIM(COALESCE(default_ebom_behavior,'')))
                         IN ('NORMAL','FLATTEN','EXCLUDE')
                    THEN UPPER(TRIM(default_ebom_behavior)) ELSE 'NORMAL' END,
                cad_requirement=CASE
                    WHEN UPPER(TRIM(COALESCE(cad_requirement,'')))
                         IN ('REQUIRED','OPTIONAL','NOT_REQUIRED')
                    THEN UPPER(TRIM(cad_requirement)) ELSE 'OPTIONAL' END,
                drawing_requirement=CASE
                    WHEN UPPER(TRIM(COALESCE(drawing_requirement,'')))
                         IN ('REQUIRED','OPTIONAL','NOT_REQUIRED')
                    THEN UPPER(TRIM(drawing_requirement)) ELSE 'OPTIONAL' END
            """
        )

    if "bom_children" in tables:
        _ensure_column(
            conn, "bom_children", "ebom_behavior",
            "ebom_behavior TEXT NOT NULL DEFAULT 'INHERIT'",
        )
        conn.execute(
            """
            UPDATE bom_children
            SET ebom_behavior=CASE
                WHEN UPPER(TRIM(COALESCE(ebom_behavior,'')))
                     IN ('INHERIT','NORMAL','FLATTEN','EXCLUDE')
                THEN UPPER(TRIM(ebom_behavior)) ELSE 'INHERIT' END
            """
        )

    if "bom_iteration_bindings" in tables:
        _ensure_column(
            conn, "bom_iteration_bindings", "ebom_behavior",
            "ebom_behavior TEXT NOT NULL DEFAULT 'INHERIT'",
        )
        conn.execute(
            """
            UPDATE bom_iteration_bindings
            SET ebom_behavior=CASE
                WHEN UPPER(TRIM(COALESCE(ebom_behavior,'')))
                     IN ('INHERIT','NORMAL','FLATTEN','EXCLUDE')
                THEN UPPER(TRIM(ebom_behavior)) ELSE 'INHERIT' END
            """
        )

    if "assembly_configuration_members" in tables:
        _ensure_column(
            conn, "assembly_configuration_members", "ebom_behavior",
            "ebom_behavior TEXT NOT NULL DEFAULT 'INHERIT'",
        )
        conn.execute(
            """
            UPDATE assembly_configuration_members
            SET ebom_behavior=CASE
                WHEN UPPER(TRIM(COALESCE(ebom_behavior,'')))
                     IN ('INHERIT','NORMAL','FLATTEN','EXCLUDE')
                THEN UPPER(TRIM(ebom_behavior)) ELSE 'INHERIT' END
            """
        )
    # Historical object snapshots need explicit compatibility policy values so
    # resolution never consults mutable current object data for old iterations.
    if "bom_iterations" in tables and "bom_revisions" in tables and "bom" in tables:
        rows = conn.execute(
            """
            SELECT i.id, i.object_data_json,
                   b.classification, b.default_ebom_behavior,
                   b.cad_requirement, b.drawing_requirement
            FROM bom_iterations i
            JOIN bom_revisions r ON r.id=i.revision_id
            JOIN bom b ON b.id=r.bom_id
            ORDER BY i.id
            """
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row[1] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            snapshot_defaults = {
                "classification": (
                    {"PHYSICAL", "CAD_ONLY", "REFERENCE", "SKELETON"},
                    str(row[2] or "PHYSICAL"),
                    "PHYSICAL",
                ),
                "default_ebom_behavior": (
                    {"NORMAL", "FLATTEN", "EXCLUDE"},
                    str(row[3] or "NORMAL"),
                    "NORMAL",
                ),
                "cad_requirement": (
                    {"REQUIRED", "OPTIONAL", "NOT_REQUIRED"},
                    str(row[4] or "OPTIONAL"),
                    "OPTIONAL",
                ),
                "drawing_requirement": (
                    {"REQUIRED", "OPTIONAL", "NOT_REQUIRED"},
                    str(row[5] or "OPTIONAL"),
                    "OPTIONAL",
                ),
            }
            for key, (allowed, current_default, fallback) in snapshot_defaults.items():
                value = str(payload.get(key, current_default) or "").strip().upper()
                payload[key] = value if value in allowed else fallback
            conn.execute(
                "UPDATE bom_iterations SET object_data_json=? WHERE id=?",
                (
                    json.dumps(
                        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                    ),
                    int(row[0]),
                ),
            )


def _migration_30(conn):
    """Link alternate CAD representations to their deliverable physical BOM item."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "bom" not in tables:
        return
    _ensure_column(conn, "bom", "represented_part_id", "represented_part_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bom_represented_part ON bom(represented_part_id)"
    )


def _migration_31(conn):
    """Add supplier-managed CAD package ownership without creating BOM rows."""
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "bom" not in tables:
        return
    _ensure_column(
        conn,
        "bom",
        "cad_control_mode",
        "cad_control_mode TEXT NOT NULL DEFAULT 'CONTROLLED'",
    )
    conn.execute(
        """
        UPDATE bom
        SET cad_control_mode=CASE
            WHEN UPPER(TRIM(COALESCE(cad_control_mode,'')))='SUPPLIER_PACKAGE'
            THEN 'SUPPLIER_PACKAGE' ELSE 'CONTROLLED' END
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
        """
    )
    if "bom_iterations" in tables:
        rows = conn.execute(
            "SELECT id, object_data_json FROM bom_iterations ORDER BY id"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(str(row[1] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            mode = str(payload.get("cad_control_mode") or "CONTROLLED").strip().upper()
            payload["cad_control_mode"] = (
                mode if mode in {"CONTROLLED", "SUPPLIER_PACKAGE"} else "CONTROLLED"
            )
            conn.execute(
                "UPDATE bom_iterations SET object_data_json=? WHERE id=?",
                (
                    json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    int(row[0]),
                ),
            )


def _migration_32(conn):
    """Separate CAD Documents, Item usages, and typed PDM associations.

    The legacy ``bom`` table remains the Item master so existing commits,
    baselines, revisions, and integrations keep their stable identifiers.
    Existing file-bearing BOM rows are projected into first-class CAD
    Documents and the old CAD tree is copied into CAD member links.  A
    persisted Item structure is then seeded from the legacy delivery policy.
    """
    conn.row_factory = sqlite3.Row
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "bom" not in tables:
        return
    _ensure_column(conn, "bom", "revision", "revision TEXT DEFAULT 'A'")
    _ensure_column(conn, "bom", "current_iteration_id", "current_iteration_id INTEGER")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cad_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            number TEXT NOT NULL COLLATE NOCASE,
            name TEXT NOT NULL,
            file_name TEXT NOT NULL COLLATE NOCASE,
            base_file_name TEXT NOT NULL COLLATE NOCASE,
            authoring_application TEXT NOT NULL DEFAULT 'CREO',
            category TEXT NOT NULL DEFAULT 'COMPONENT',
            document_type TEXT NOT NULL DEFAULT 'CAD_DOCUMENT',
            lifecycle_state TEXT NOT NULL DEFAULT 'IN_WORK',
            revision TEXT NOT NULL DEFAULT 'A',
            iteration INTEGER NOT NULL DEFAULT 1,
            build_excluded INTEGER NOT NULL DEFAULT 0,
            supplier_owner_item_id INTEGER,
            legacy_bom_id INTEGER,
            drawing_owner_cad_document_id INTEGER,
            checked_out_by INTEGER,
            checked_out_at TEXT,
            latest_creo_file_version INTEGER,
            latest_creo_file_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            modified_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(project_id, file_name),
            FOREIGN KEY (supplier_owner_item_id) REFERENCES bom(id),
            FOREIGN KEY (legacy_bom_id) REFERENCES bom(id),
            FOREIGN KEY (drawing_owner_cad_document_id) REFERENCES cad_documents(id)
        );

        CREATE TABLE IF NOT EXISTS cad_document_iterations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cad_document_id INTEGER NOT NULL,
            revision TEXT NOT NULL DEFAULT 'A',
            iteration INTEGER NOT NULL DEFAULT 1,
            lifecycle_state TEXT NOT NULL DEFAULT 'IN_WORK',
            primary_path TEXT,
            sha256 TEXT,
            size_bytes INTEGER,
            source_commit_id TEXT,
            checkin_note TEXT,
            created_by INTEGER,
            creo_file_version INTEGER,
            source_file_name TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(cad_document_id, revision, iteration),
            FOREIGN KEY (cad_document_id) REFERENCES cad_documents(id)
        );

        CREATE TABLE IF NOT EXISTS cad_document_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cad_document_id INTEGER NOT NULL,
            content_role TEXT NOT NULL DEFAULT 'SECONDARY',
            format TEXT NOT NULL,
            file_name TEXT NOT NULL,
            storage_path TEXT,
            delivery_required INTEGER NOT NULL DEFAULT 0,
            derived_from_content_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(cad_document_id, content_role, format, file_name),
            FOREIGN KEY (cad_document_id) REFERENCES cad_documents(id),
            FOREIGN KEY (derived_from_content_id) REFERENCES cad_document_contents(id)
        );

        CREATE TABLE IF NOT EXISTS cad_document_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_cad_document_id INTEGER NOT NULL,
            child_cad_document_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            reference_designator TEXT,
            component_path TEXT,
            build_excluded INTEGER NOT NULL DEFAULT 0,
            legacy_usage_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(parent_cad_document_id, child_cad_document_id),
            FOREIGN KEY (parent_cad_document_id) REFERENCES cad_documents(id),
            FOREIGN KEY (child_cad_document_id) REFERENCES cad_documents(id)
        );

        CREATE TABLE IF NOT EXISTS cad_item_associations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            cad_document_id INTEGER NOT NULL,
            association_type TEXT NOT NULL,
            drives_structure INTEGER NOT NULL DEFAULT 0,
            drives_attributes INTEGER NOT NULL DEFAULT 0,
            participates_in_structure INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            modified_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES bom(id),
            FOREIGN KEY (cad_document_id) REFERENCES cad_documents(id)
        );

        CREATE TABLE IF NOT EXISTS item_usages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            parent_item_id INTEGER NOT NULL,
            child_item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            unit TEXT NOT NULL DEFAULT 'EA',
            line_number INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'MANUAL',
            cad_member_id INTEGER,
            build_status TEXT NOT NULL DEFAULT 'COMPLETED',
            legacy_usage_id INTEGER,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            modified_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (parent_item_id) REFERENCES bom(id),
            FOREIGN KEY (child_item_id) REFERENCES bom(id),
            FOREIGN KEY (cad_member_id) REFERENCES cad_document_members(id)
        );

        CREATE TABLE IF NOT EXISTS item_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_usage_id INTEGER NOT NULL,
            occurrence_name TEXT,
            reference_designator TEXT,
            component_path TEXT,
            transform_json TEXT,
            source_cad_member_id INTEGER,
            build_status TEXT NOT NULL DEFAULT 'COMPLETED',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (item_usage_id) REFERENCES item_usages(id),
            FOREIGN KEY (source_cad_member_id) REFERENCES cad_document_members(id)
        );

        CREATE TABLE IF NOT EXISTS pdm_build_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            root_cad_document_id INTEGER NOT NULL,
            direction TEXT NOT NULL DEFAULT 'CAD_TO_EBOM',
            multi_level INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'RUNNING',
            created_by INTEGER,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            summary_json TEXT,
            FOREIGN KEY (root_cad_document_id) REFERENCES cad_documents(id)
        );

        CREATE TABLE IF NOT EXISTS pdm_build_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            build_run_id INTEGER NOT NULL,
            cad_member_id INTEGER,
            parent_item_id INTEGER,
            child_item_id INTEGER,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (build_run_id) REFERENCES pdm_build_runs(id),
            FOREIGN KEY (cad_member_id) REFERENCES cad_document_members(id)
        );

        CREATE TABLE IF NOT EXISTS item_structure_iterations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            parent_item_id INTEGER NOT NULL,
            structure_iteration INTEGER NOT NULL,
            item_revision TEXT NOT NULL DEFAULT 'A',
            item_iteration_id INTEGER,
            source TEXT NOT NULL,
            build_run_id INTEGER,
            structure_json TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(parent_item_id, structure_iteration),
            FOREIGN KEY (parent_item_id) REFERENCES bom(id),
            FOREIGN KEY (build_run_id) REFERENCES pdm_build_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_cad_documents_project
            ON cad_documents(project_id, base_file_name);
        CREATE INDEX IF NOT EXISTS idx_cad_members_parent
            ON cad_document_members(parent_cad_document_id, sort_order, id);
        CREATE INDEX IF NOT EXISTS idx_cad_members_child
            ON cad_document_members(child_cad_document_id);
        CREATE INDEX IF NOT EXISTS idx_cad_assoc_item
            ON cad_item_associations(item_id, active, association_type);
        CREATE INDEX IF NOT EXISTS idx_cad_assoc_document
            ON cad_item_associations(cad_document_id, active);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_cad_item_association
            ON cad_item_associations(cad_document_id) WHERE active=1;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_active_owner_per_item
            ON cad_item_associations(item_id)
            WHERE active=1 AND association_type='OWNER';
        CREATE INDEX IF NOT EXISTS idx_item_usages_parent
            ON item_usages(parent_item_id, sort_order, id);
        CREATE INDEX IF NOT EXISTS idx_item_usages_child
            ON item_usages(child_item_id);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_item_usage_cad_member
            ON item_usages(cad_member_id)
            WHERE source='CAD_BUILD' AND cad_member_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_item_structure_iterations_parent
            ON item_structure_iterations(parent_item_id, structure_iteration);
        """
    )
    _ensure_column(conn, "cad_documents", "checked_out_by", "checked_out_by INTEGER")
    _ensure_column(conn, "cad_documents", "checked_out_at", "checked_out_at TEXT")

    bom_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(bom)").fetchall()
    }

    def value(row, column, default=""):
        return row[column] if column in bom_columns and row[column] is not None else default

    def base_name(raw):
        text = os.path.basename(str(raw or "").replace("\\", "/")).strip()
        return os.path.splitext(text)[0].casefold()

    def file_name(raw, fallback_base, extension):
        text = os.path.basename(str(raw or "").replace("\\", "/")).strip()
        if text:
            match = re.match(
                r"^(.*\.(?:asm|prt|drw))\.\d+$", text, flags=re.IGNORECASE
            )
            return match.group(1) if match else text
        return f"{fallback_base}{extension}" if fallback_base else ""

    items = conn.execute("SELECT * FROM bom ORDER BY id").fetchall()
    primary_docs = {}
    for row in items:
        item_id = int(row["id"])
        project_id = int(value(row, "project_id", 0) or 0)
        raw_file = value(row, "filename") or value(row, "base_file_name")
        raw_base = value(row, "base_file_name") or base_name(raw_file)
        normalized_base = base_name(raw_base) or base_name(raw_file)
        if not normalized_base:
            continue
        item_type = str(value(row, "type", "prt") or "prt").strip().lower()
        extension = ".asm" if item_type in {"asm", "assembly"} else ".prt"
        actual_file = file_name(raw_file, normalized_base, extension)
        iteration_match = re.match(
            r"^.*\.(?:asm|prt|drw)\.(\d+)$",
            os.path.basename(str(raw_file or "").replace("\\", "/")),
            flags=re.IGNORECASE,
        )
        legacy_creo_version = int(iteration_match.group(1)) if iteration_match else None
        legacy_creo_file = (
            os.path.basename(str(raw_file or "").replace("\\", "/")).strip()
            if legacy_creo_version is not None else None
        )
        category = "ASSEMBLY" if item_type in {"asm", "assembly"} else "COMPONENT"
        represented_id = value(row, "represented_part_id", None)
        association_item_id = int(represented_id) if represented_id else item_id
        association_type = "IMAGE" if represented_id else "OWNER"
        flags = {
            "OWNER": (1, 1, 1),
            "IMAGE": (0, 0, 1),
        }[association_type]
        build_excluded = 1 if str(value(row, "default_ebom_behavior", "NORMAL")).upper() == "EXCLUDE" else 0
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO cad_documents(
                project_id,number,name,file_name,base_file_name,category,
                lifecycle_state,revision,iteration,build_excluded,legacy_bom_id,
                latest_creo_file_version,latest_creo_file_name
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                project_id,
                actual_file,
                str(value(row, "name") or normalized_base),
                actual_file,
                normalized_base,
                category,
                str(value(row, "lifecycle_state", "IN_WORK") or "IN_WORK"),
                str(value(row, "revision", "A") or "A"),
                1,
                build_excluded,
                item_id,
                legacy_creo_version,
                legacy_creo_file,
            ),
        )
        doc = conn.execute(
            "SELECT id FROM cad_documents WHERE project_id=? AND file_name=?",
            (project_id, actual_file),
        ).fetchone()
        if not doc:
            continue
        doc_id = int(doc[0])
        primary_docs[item_id] = doc_id
        conn.execute(
            """
            INSERT OR IGNORE INTO cad_document_iterations(
                cad_document_id,revision,iteration,lifecycle_state,primary_path,
                creo_file_version,source_file_name
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                doc_id,
                str(value(row, "revision", "A") or "A"),
                1,
                str(value(row, "lifecycle_state", "IN_WORK") or "IN_WORK"),
                actual_file,
                legacy_creo_version,
                legacy_creo_file,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO cad_item_associations(
                project_id,item_id,cad_document_id,association_type,
                drives_structure,drives_attributes,participates_in_structure
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (project_id, association_item_id, doc_id, association_type, *flags),
        )
        for fmt, path_value in (
            ("PDF", value(row, "pdf_path")),
            ("STEP", value(row, "step_path")),
        ):
            if not path_value:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO cad_document_contents(
                    cad_document_id,content_role,format,file_name,storage_path,
                    delivery_required
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    doc_id, "DERIVED", fmt,
                    os.path.basename(str(path_value).replace("\\", "/")),
                    str(path_value),
                    1 if association_type == "OWNER" else 0,
                ),
            )

        raw_drawing = value(row, "drawing") or value(row, "base_drw_name")
        drawing_base = base_name(value(row, "base_drw_name") or raw_drawing)
        if drawing_base:
            drawing_file = file_name(raw_drawing, drawing_base, ".drw")
            drawing_version_match = re.match(
                r"^.*\.drw\.(\d+)$",
                os.path.basename(str(raw_drawing or "").replace("\\", "/")),
                flags=re.IGNORECASE,
            )
            drawing_creo_version = (
                int(drawing_version_match.group(1)) if drawing_version_match else None
            )
            drawing_creo_file = (
                os.path.basename(str(raw_drawing or "").replace("\\", "/")).strip()
                if drawing_creo_version is not None else None
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO cad_documents(
                    project_id,number,name,file_name,base_file_name,category,
                    lifecycle_state,revision,legacy_bom_id,
                    drawing_owner_cad_document_id,
                    latest_creo_file_version,latest_creo_file_name
                ) VALUES(?,?,?,?,?,'DRAWING',?,?,?,?,?,?)
                """,
                (
                    project_id, drawing_file, f"{value(row, 'name') or drawing_base} drawing",
                    drawing_file, drawing_base,
                    str(value(row, "lifecycle_state", "IN_WORK") or "IN_WORK"),
                    str(value(row, "revision", "A") or "A"), item_id,
                    doc_id,
                    drawing_creo_version,
                    drawing_creo_file,
                ),
            )
            drawing_doc = conn.execute(
                "SELECT id FROM cad_documents WHERE project_id=? AND file_name=?",
                (project_id, drawing_file),
            ).fetchone()
            if drawing_doc:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cad_document_iterations(
                        cad_document_id,revision,iteration,lifecycle_state,primary_path,
                        creo_file_version,source_file_name
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        int(drawing_doc[0]),
                        str(value(row, "revision", "A") or "A"),
                        1,
                        str(value(row, "lifecycle_state", "IN_WORK") or "IN_WORK"),
                        drawing_file,
                        drawing_creo_version,
                        drawing_creo_file,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cad_item_associations(
                        project_id,item_id,cad_document_id,association_type,
                        drives_structure,drives_attributes,participates_in_structure
                    ) VALUES(?,?,?,'CONTENT',0,0,0)
                    """,
                    (project_id, association_item_id, int(drawing_doc[0])),
                )

    member_by_usage = {}
    seed_item_usages = (
        int(conn.execute("SELECT COUNT(*) FROM item_usages").fetchone()[0]) == 0
    )
    if "bom_children" in tables:
        child_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(bom_children)").fetchall()
        }
        relations = conn.execute("SELECT * FROM bom_children ORDER BY id").fetchall()
        for rel in relations:
            parent_doc = primary_docs.get(int(rel["parent_id"]))
            child_doc = primary_docs.get(int(rel["child_id"]))
            if not parent_doc or not child_doc:
                continue
            quantity = max(1, int(rel["quantity"] or 1))
            sort_order = int(rel["sort_order"] or rel["id"]) if "sort_order" in child_columns else int(rel["id"])
            behavior = str(rel["ebom_behavior"] or "INHERIT").upper() if "ebom_behavior" in child_columns else "INHERIT"
            conn.execute(
                """
                INSERT OR IGNORE INTO cad_document_members(
                    parent_cad_document_id,child_cad_document_id,quantity,
                    sort_order,build_excluded,legacy_usage_id
                ) VALUES(?,?,?,?,?,?)
                """,
                (parent_doc, child_doc, quantity, sort_order, 1 if behavior == "EXCLUDE" else 0, int(rel["id"])),
            )
            member = conn.execute(
                "SELECT id FROM cad_document_members WHERE legacy_usage_id=?",
                (int(rel["id"]),),
            ).fetchone()
            if member:
                member_by_usage[int(rel["id"])] = int(member[0])

        item_by_id = {int(row["id"]): row for row in items}
        children = {}
        for rel in relations:
            children.setdefault(int(rel["parent_id"]), []).append(rel)

        def resolved_behavior(rel):
            behavior = str(rel["ebom_behavior"] or "INHERIT").upper() if "ebom_behavior" in child_columns else "INHERIT"
            if behavior != "INHERIT":
                return behavior
            child = item_by_id.get(int(rel["child_id"]))
            return str(value(child, "default_ebom_behavior", "NORMAL") if child else "NORMAL").upper()

        for parent_id, parent_row in item_by_id.items():
            project_id = int(value(parent_row, "project_id", 0) or 0)
            aggregated = {}

            def collect(source_parent_id, multiplier=1, ancestors=()):
                if source_parent_id in ancestors:
                    return
                for rel in children.get(source_parent_id, []):
                    behavior = resolved_behavior(rel)
                    if behavior == "EXCLUDE":
                        continue
                    qty = multiplier * max(1, int(rel["quantity"] or 1))
                    child_id = int(rel["child_id"])
                    if behavior == "FLATTEN":
                        collect(child_id, qty, (*ancestors, source_parent_id))
                        continue
                    key = child_id
                    entry = aggregated.setdefault(key, {
                        "quantity": 0,
                        "sort_order": int(rel["sort_order"] or rel["id"]) if "sort_order" in child_columns else int(rel["id"]),
                        "legacy_usage_id": int(rel["id"]) if source_parent_id == parent_id else None,
                        "cad_member_id": member_by_usage.get(int(rel["id"])) if source_parent_id == parent_id else None,
                    })
                    entry["quantity"] += qty

            collect(parent_id)
            for child_id, entry in aggregated.items():
                if not seed_item_usages:
                    continue
                if child_id == parent_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO item_usages(
                        project_id,parent_item_id,child_item_id,quantity,
                        sort_order,source,cad_member_id,legacy_usage_id
                    ) VALUES(?,?,?,?,?,'CAD_BUILD',?,?)
                    """,
                    (
                        project_id, parent_id, child_id, entry["quantity"],
                        entry["sort_order"], entry["cad_member_id"],
                        entry["legacy_usage_id"],
                    ),
                )

    if "bom_cad_dependencies" in tables:
        dependencies = conn.execute(
            "SELECT * FROM bom_cad_dependencies ORDER BY id"
        ).fetchall()
        for dep in dependencies:
            project_id = int(dep["project_id"])
            owner_item_id = int(dep["owner_bom_id"])
            normalized_base = base_name(dep["base_file_name"] or dep["original_filename"])
            if not normalized_base:
                continue
            actual_file = file_name(dep["original_filename"], normalized_base, ".prt")
            conn.execute(
                """
                INSERT OR IGNORE INTO cad_documents(
                    project_id,number,name,file_name,base_file_name,category,
                    build_excluded,supplier_owner_item_id
                ) VALUES(?,?,?,?,?,'COMPONENT',1,?)
                """,
                (project_id, normalized_base, normalized_base, actual_file, normalized_base, owner_item_id),
            )
            doc = conn.execute(
                "SELECT id FROM cad_documents WHERE project_id=? AND file_name=?",
                (project_id, actual_file),
            ).fetchone()
            if doc:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cad_item_associations(
                        project_id,item_id,cad_document_id,association_type,
                        drives_structure,drives_attributes,participates_in_structure
                    ) VALUES(?,?,?,'CONTENT',0,0,0)
                    """,
                    (project_id, owner_item_id, int(doc[0])),
                )


def _migration_33(conn):
    """Coordinate Item and CAD Document working-copy ownership.

    Item locks remain in the legacy ``locks`` table so existing permission and
    history screens continue to work.  ``checkout_origin`` distinguishes a
    deliberate Item checkout from the temporary Item checkout opened by a CAD
    Document.  The CAD master stores the associated Item at checkout time so a
    later association edit cannot change which Item owns the working copy.
    """
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "locks" in tables:
        _ensure_column(
            conn,
            "locks",
            "checkout_origin",
            "checkout_origin TEXT NOT NULL DEFAULT 'ITEM'",
        )
        _ensure_column(conn, "locks", "checked_out_at", "checked_out_at TEXT")
        conn.execute(
            "UPDATE locks SET checkout_origin='ITEM' "
            "WHERE checkout_origin IS NULL OR trim(checkout_origin)=''"
        )
    if "cad_documents" not in tables:
        return

    _ensure_column(
        conn,
        "cad_documents",
        "checkout_item_id",
        "checkout_item_id INTEGER",
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cad_document_checkout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cad_document_id INTEGER NOT NULL,
            item_id INTEGER,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            cad_iteration_id INTEGER,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (cad_document_id) REFERENCES cad_documents(id),
            FOREIGN KEY (item_id) REFERENCES bom(id),
            FOREIGN KEY (cad_iteration_id) REFERENCES cad_document_iterations(id)
        );

        CREATE INDEX IF NOT EXISTS idx_cad_checkout_logs_document
            ON cad_document_checkout_logs(cad_document_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_cad_checkout_logs_item
            ON cad_document_checkout_logs(item_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_cad_documents_checkout_item
            ON cad_documents(checkout_item_id, checked_out_by);
        """
    )
    # Preserve the association that owns any working copies created before this
    # migration.  New checkouts always set this value explicitly.
    conn.execute(
        """
        UPDATE cad_documents
        SET checkout_item_id=(
            SELECT a.item_id
            FROM cad_item_associations a
            WHERE a.cad_document_id=cad_documents.id AND a.active=1
            ORDER BY a.id DESC LIMIT 1
        )
        WHERE checked_out_by IS NOT NULL AND checkout_item_id IS NULL
        """
    )


def _migration_34(conn):
    """Bind native drawings to their PRT/ASM CAD Document.

    Drawings are managed CAD data, but they are not structural CAD nodes.  A
    drawing therefore owns no independent position in the CAD assembly tree;
    it is related to exactly one model and is presented from that model's
    details.
    """
    conn.row_factory = sqlite3.Row
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "cad_documents" not in tables:
        return
    _ensure_column(
        conn,
        "cad_documents",
        "drawing_owner_cad_document_id",
        "drawing_owner_cad_document_id INTEGER",
    )
    conn.execute(
        "UPDATE cad_documents SET category='DRAWING' "
        "WHERE upper(category)<>'DRAWING' AND "
        "(lower(file_name) LIKE '%.drw' OR lower(file_name) GLOB '*.drw.[0-9]*')"
    )

    drawings = conn.execute(
        """
        SELECT id,project_id,base_file_name,legacy_bom_id
        FROM cad_documents
        WHERE upper(category)='DRAWING'
          AND drawing_owner_cad_document_id IS NULL
        ORDER BY id
        """
    ).fetchall()
    for drawing in drawings:
        owner = None
        if drawing["legacy_bom_id"] is not None:
            owner = conn.execute(
                """
                SELECT id FROM cad_documents
                WHERE project_id=? AND legacy_bom_id=?
                  AND upper(category) IN ('ASSEMBLY','COMPONENT')
                ORDER BY CASE upper(category) WHEN 'ASSEMBLY' THEN 0 ELSE 1 END,id
                LIMIT 1
                """,
                (int(drawing["project_id"]), int(drawing["legacy_bom_id"])),
            ).fetchone()
        if owner is None:
            owner = conn.execute(
                """
                SELECT id FROM cad_documents
                WHERE project_id=? AND lower(base_file_name)=lower(?)
                  AND upper(category) IN ('ASSEMBLY','COMPONENT')
                ORDER BY id LIMIT 1
                """,
                (int(drawing["project_id"]), str(drawing["base_file_name"] or "")),
            ).fetchone()
        if owner is None and "cad_item_associations" in tables:
            candidates = conn.execute(
                """
                SELECT DISTINCT model.id,model_assoc.association_type
                FROM cad_item_associations drawing_assoc
                JOIN cad_item_associations model_assoc
                  ON model_assoc.item_id=drawing_assoc.item_id
                 AND model_assoc.active=1
                JOIN cad_documents model ON model.id=model_assoc.cad_document_id
                WHERE drawing_assoc.cad_document_id=?
                  AND drawing_assoc.active=1
                  AND upper(model.category) IN ('ASSEMBLY','COMPONENT')
                ORDER BY model.id
                """,
                (int(drawing["id"]),),
            ).fetchall()
            owner_candidates = [
                candidate for candidate in candidates
                if str(candidate["association_type"] or "").upper() == "OWNER"
            ]
            if len(owner_candidates) == 1:
                owner = owner_candidates[0]
            elif len(candidates) == 1:
                owner = candidates[0]
        if owner is not None:
            conn.execute(
                "UPDATE cad_documents SET drawing_owner_cad_document_id=? WHERE id=?",
                (int(owner["id"]), int(drawing["id"])),
            )

    # Native drawings must never be assembly occurrences.
    if "cad_document_members" in tables:
        conn.execute(
            """
            DELETE FROM cad_document_members
            WHERE parent_cad_document_id IN (
                SELECT id FROM cad_documents WHERE upper(category)='DRAWING'
            ) OR child_cad_document_id IN (
                SELECT id FROM cad_documents WHERE upper(category)='DRAWING'
            )
            """
        )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_cad_documents_drawing_owner
            ON cad_documents(drawing_owner_cad_document_id,category,id);

        CREATE TRIGGER IF NOT EXISTS trg_cad_drawing_model_insert
        BEFORE INSERT ON cad_documents
        WHEN upper(NEW.category)='DRAWING' AND (
            NEW.drawing_owner_cad_document_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM cad_documents model
                WHERE model.id=NEW.drawing_owner_cad_document_id
                  AND model.project_id=NEW.project_id
                  AND upper(model.category) IN ('ASSEMBLY','COMPONENT')
            )
        )
        BEGIN
            SELECT RAISE(ABORT,'A drawing must be bound to a PRT or ASM CAD Document.');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_cad_drawing_model_update
        BEFORE UPDATE OF category,drawing_owner_cad_document_id,project_id
        ON cad_documents
        WHEN upper(NEW.category)='DRAWING' AND (
            NEW.drawing_owner_cad_document_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM cad_documents model
                WHERE model.id=NEW.drawing_owner_cad_document_id
                  AND model.project_id=NEW.project_id
                  AND upper(model.category) IN ('ASSEMBLY','COMPONENT')
            )
        )
        BEGIN
            SELECT RAISE(ABORT,'A drawing must be bound to a PRT or ASM CAD Document.');
        END;
        """
    )


def _migration_35(conn):
    """Add Windchill-style Item master attributes and generated numbering."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "bom" not in tables:
        return
    _ensure_column(conn, "bom", "part_number", "part_number TEXT")
    _ensure_column(conn, "bom", "project_id", "project_id INTEGER")
    _ensure_column(conn, "bom", "represented_part_id", "represented_part_id INTEGER")
    for name, definition in (
        ("item_type", "item_type TEXT NOT NULL DEFAULT 'MECHANICAL_PART'"),
        ("assembly_mode", "assembly_mode TEXT NOT NULL DEFAULT 'COMPONENT'"),
        ("procurement_source", "procurement_source TEXT NOT NULL DEFAULT 'MAKE'"),
        ("item_view", "item_view TEXT NOT NULL DEFAULT 'DESIGN'"),
        ("default_unit", "default_unit TEXT NOT NULL DEFAULT 'EA'"),
    ):
        _ensure_column(conn, "bom", name, definition)
    conn.execute(
        """
        UPDATE bom SET assembly_mode='SEPARABLE'
        WHERE lower(COALESCE(type,'')) IN ('asm','assembly')
          AND assembly_mode='COMPONENT'
        """
    )
    if "bom_iterations" in tables and "bom_revisions" in tables:
        rows = conn.execute(
            """
            SELECT i.id, i.object_data_json, b.part_number, b.item_type,
                   b.assembly_mode, b.procurement_source, b.item_view,
                   b.default_unit
            FROM bom_iterations i
            JOIN bom_revisions r ON r.id=i.revision_id
            JOIN bom b ON b.id=r.bom_id
            ORDER BY i.id
            """
        ).fetchall()
        allowed_values = {
            "item_type": ({
                "MECHANICAL_PART", "SOFTWARE_PART", "PURCHASED_PART",
                "REFERENCE_PART",
            }, "MECHANICAL_PART"),
            "assembly_mode": ({"COMPONENT", "SEPARABLE", "INSEPARABLE"}, "COMPONENT"),
            "procurement_source": ({"MAKE", "BUY", "MAKE_OR_BUY"}, "MAKE"),
            "item_view": ({"DESIGN", "MANUFACTURING", "SERVICE"}, "DESIGN"),
            "default_unit": ({"EA", "KG", "M", "MM", "L", "SET"}, "EA"),
        }
        for row in rows:
            try:
                payload = json.loads(str(row[1] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if not str(payload.get("part_number") or "").strip():
                payload["part_number"] = str(row[2] or "").strip()
            current_values = {
                "item_type": row[3],
                "assembly_mode": row[4],
                "procurement_source": row[5],
                "item_view": row[6],
                "default_unit": row[7],
            }
            for key, (allowed, fallback) in allowed_values.items():
                value = str(
                    payload.get(key) or current_values.get(key) or fallback
                ).strip().upper()
                payload[key] = value if value in allowed else fallback
            conn.execute(
                "UPDATE bom_iterations SET object_data_json=? WHERE id=?",
                (
                    json.dumps(
                        payload, ensure_ascii=True, sort_keys=True,
                        separators=(",", ":"),
                    ),
                    int(row[0]),
                ),
            )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS item_number_sequence (
            id INTEGER PRIMARY KEY CHECK(id=1),
            next_value INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    if conn.execute(
        "SELECT 1 FROM item_number_sequence WHERE id=1"
    ).fetchone() is None:
        highest = 49_999_999
        for row in conn.execute(
            "SELECT part_number FROM bom WHERE part_number IS NOT NULL"
        ).fetchall():
            raw = str(row[0] or "").strip()
            if raw.isdigit():
                highest = max(highest, int(raw))
        conn.execute(
            "INSERT INTO item_number_sequence(id,next_value) VALUES(1,?)",
            (max(50_000_000, highest + 1),),
        )
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
        # Existing duplicate numbers are surfaced by Diagnostics/release
        # validation and must be resolved deliberately; never renumber them here.
        pass
    if "cad_documents" in tables:
        try:
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_cad_documents_project_number
                ON cad_documents(project_id, number COLLATE NOCASE)
                """
            )
        except sqlite3.IntegrityError:
            # Preserve legacy CAD identities for deliberate resolution. New
            # registrations still reject duplicate Numbers in the repository.
            pass


def _migration_36(conn):
    """Make commit rows CAD-document aware and allow CAD-only commits."""
    def repair_backup_commit_foreign_keys() -> None:
        stale_name = "commits_part_id_not_null_backup"
        rows = conn.execute(
            """
            SELECT name,sql FROM sqlite_master
            WHERE type='table' AND sql LIKE ?
            ORDER BY name
            """,
            (f"%{stale_name}%",),
        ).fetchall()
        for row in rows:
            table_name = str(row[0])
            create_sql = str(row[1])
            if table_name == "commits":
                continue
            temp_table = f"{table_name}_fk_rebuild"
            conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
            repaired_sql = (
                create_sql
                .replace(f'"{stale_name}"', "commits")
                .replace(stale_name, "commits")
            )
            repaired_sql = repaired_sql.replace(
                f"CREATE TABLE {table_name}",
                f"CREATE TABLE {temp_table}",
                1,
            ).replace(
                f'CREATE TABLE "{table_name}"',
                f'CREATE TABLE "{temp_table}"',
                1,
            )
            conn.execute(repaired_sql)
            columns = [
                str(col[1])
                for col in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            ]
            if columns:
                column_csv = ", ".join(columns)
                conn.execute(
                    f"INSERT INTO {temp_table} ({column_csv}) "
                    f"SELECT {column_csv} FROM {table_name}"
                )
            conn.execute(f"DROP TABLE {table_name}")
            conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")

    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "commits" not in tables:
        restored = False
        for table_name in ("commits_part_id_not_null_backup", "commits_nullable_rebuild"):
            if table_name in tables:
                conn.execute(f"ALTER TABLE {table_name} RENAME TO commits")
                restored = True
                break
        if not restored:
            return
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    repair_backup_commit_foreign_keys()
    for table_name in ("commits_part_id_not_null_backup", "commits_nullable_rebuild"):
        if table_name in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    _ensure_column(conn, "commits", "cad_document_id", "cad_document_id INTEGER")
    _ensure_column(conn, "commits", "creo_file_version", "creo_file_version INTEGER")
    columns = conn.execute("PRAGMA table_info(commits)").fetchall()
    part_column = next((col for col in columns if col[1] == "part_id"), None)
    if part_column is not None and int(part_column[3] or 0):
        temp_table = "commits_nullable_rebuild"
        conn.execute(f"DROP TABLE IF EXISTS {temp_table}")
        column_defs = []
        column_names = []
        for col in columns:
            name = str(col[1])
            column_names.append(name)
            col_type = str(col[2] or "TEXT")
            if int(col[5] or 0):
                column_defs.append(
                    "id INTEGER PRIMARY KEY AUTOINCREMENT"
                    if name == "id" else
                    f"{name} {col_type} PRIMARY KEY"
                )
                continue
            definition = f"{name} {col_type}"
            if name != "part_id" and int(col[3] or 0):
                definition += " NOT NULL"
            default_value = col[4]
            if default_value is not None:
                definition += f" DEFAULT {default_value}"
            column_defs.append(definition)
        conn.execute(f"CREATE TABLE {temp_table} ({', '.join(column_defs)})")
        column_csv = ", ".join(column_names)
        conn.execute(
            f"INSERT INTO {temp_table} ({column_csv}) SELECT {column_csv} FROM commits"
        )
        conn.execute("DROP TABLE commits")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO commits")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_commits_cad_document
            ON commits(cad_document_id, project_id);
        CREATE INDEX IF NOT EXISTS idx_commits_step_part_project
            ON commits(part_id, project_id);
        """
    )


def _migration_37(conn):
    """Track Creo source file versions independently from CAD/Item iterations."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "commits" in tables:
        _ensure_column(conn, "commits", "creo_file_version", "creo_file_version INTEGER")
        conn.execute(
            """
            UPDATE commits
            SET creo_file_version=CAST(substr(filename, length(base_file_name) + 2) AS INTEGER)
            WHERE creo_file_version IS NULL
              AND base_file_name IS NOT NULL
              AND filename LIKE base_file_name || '.%'
              AND substr(filename, length(base_file_name) + 2) GLOB '[0-9]*'
            """
        )
    if "cad_documents" in tables:
        _ensure_column(
            conn,
            "cad_documents",
            "latest_creo_file_version",
            "latest_creo_file_version INTEGER",
        )
        _ensure_column(
            conn,
            "cad_documents",
            "latest_creo_file_name",
            "latest_creo_file_name TEXT",
        )
    if "cad_document_iterations" in tables:
        _ensure_column(
            conn,
            "cad_document_iterations",
            "creo_file_version",
            "creo_file_version INTEGER",
        )
        _ensure_column(
            conn,
            "cad_document_iterations",
            "source_file_name",
            "source_file_name TEXT",
        )


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

    17: _migration_17,

    18: _migration_18,

    19: _migration_19,

    20: _migration_20,

    21: _migration_21,

    22: _migration_22,

    23: _migration_23,

    24: _migration_24,

    25: _migration_25,

    26: _migration_26,

    27: _migration_27,

    28: _migration_28,

    29: _migration_29,

    30: _migration_30,

    31: _migration_31,

    32: _migration_32,

    33: _migration_33,

    34: _migration_34,

    35: _migration_35,

    36: _migration_36,

    37: _migration_37,

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
