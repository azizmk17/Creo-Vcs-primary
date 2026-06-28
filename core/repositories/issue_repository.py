import json
import sqlite3
from typing import Iterable, Optional

from config import DB_NAME


class IssueRepository:
    """SQLite persistence for engineering issues and their immutable audit history."""

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self.ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _table_columns(conn, table_name: str) -> set[str]:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}

    def _ensure_column(self, conn, table_name: str, column_name: str, definition: str):
        if column_name not in self._table_columns(conn, table_name):
            try:
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')
            except sqlite3.OperationalError as exc:
                # Startup migrations and repositories can initialize concurrently.
                if "duplicate column name" not in str(exc).lower():
                    raise

    def _upgrade_legacy_schema(self, conn):
        """Preserve and normalize issue data created by pre-v12 issue prototypes."""
        original_issue_columns = self._table_columns(conn, "issues")
        for name, definition in (
            ("issue_number", "TEXT"),
            ("priority", "TEXT"),
            ("closed_by", "INTEGER"),
            ("closed_at", "TEXT"),
            ("archived", "INTEGER NOT NULL DEFAULT 0"),
            ("archive_reason", "TEXT"),
            ("source_type", "TEXT"),
            ("source_key", "TEXT"),
        ):
            self._ensure_column(conn, "issues", name, definition)

        issue_columns = self._table_columns(conn, "issues")
        if "issue_key" in issue_columns:
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

        if "severity" in issue_columns and "priority" not in original_issue_columns:
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
        if "fixed_by" in issue_columns:
            conn.execute(
                "UPDATE issues SET closed_by=fixed_by "
                "WHERE status='Closed' AND closed_by IS NULL AND fixed_by IS NOT NULL"
            )
        if "resolved_at" in issue_columns:
            conn.execute(
                "UPDATE issues SET closed_at=resolved_at "
                "WHERE status='Closed' AND closed_at IS NULL AND resolved_at IS NOT NULL"
            )
        elif "fixed_at" in issue_columns:
            conn.execute(
                "UPDATE issues SET closed_at=fixed_at "
                "WHERE status='Closed' AND closed_at IS NULL AND fixed_at IS NOT NULL"
            )

        if "part_id" in issue_columns:
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

        for table_name, columns in {
            "issue_comments": (
                ("comment", "TEXT"),
                ("user_id", "INTEGER"),
            ),
            "issue_attachments": (("created_at", "TEXT"),),
            "issue_history": (("user_id", "INTEGER"),),
        }.items():
            for name, definition in columns:
                self._ensure_column(conn, table_name, name, definition)

        comment_columns = self._table_columns(conn, "issue_comments")
        if "body" in comment_columns:
            conn.execute("UPDATE issue_comments SET comment=body WHERE comment IS NULL")
        if "created_by" in comment_columns:
            conn.execute("UPDATE issue_comments SET user_id=created_by WHERE user_id IS NULL")

        attachment_columns = self._table_columns(conn, "issue_attachments")
        if "uploaded_at" in attachment_columns:
            conn.execute("UPDATE issue_attachments SET created_at=uploaded_at WHERE created_at IS NULL")

        history_columns = self._table_columns(conn, "issue_history")
        if "actor_id" in history_columns:
            conn.execute("UPDATE issue_history SET user_id=actor_id WHERE user_id IS NULL")

    def ensure_schema(self):
        with self.get_conn() as conn:
            conn.executescript(
                """
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
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                );

                CREATE TABLE IF NOT EXISTS issue_parts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER NOT NULL,
                    part_id INTEGER NOT NULL,
                    linked_by INTEGER,
                    linked_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(issue_id, part_id),
                    FOREIGN KEY (issue_id) REFERENCES issues(id),
                    FOREIGN KEY (part_id) REFERENCES bom(id)
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
                    FOREIGN KEY (issue_id) REFERENCES issues(id)
                );

                CREATE TABLE IF NOT EXISTS issue_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    uploaded_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (issue_id) REFERENCES issues(id)
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
                    FOREIGN KEY (issue_id) REFERENCES issues(id)
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
                    FOREIGN KEY (issue_id) REFERENCES issues(id)
                );
                """
            )
            self._upgrade_legacy_schema(conn)
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

    @staticmethod
    def _dict(row):
        return dict(row) if row else None

    def _next_number(self, conn, project_id: int) -> str:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM issues",
        ).fetchone()
        return f"ISS-{int(row[0]):06d}"

    def _history(self, conn, issue_id, action, user_id=None, field_name=None,
                 old_value=None, new_value=None, details=None):
        columns = ["issue_id", "action", "field_name", "old_value", "new_value", "user_id", "details_json"]
        values = [
            int(issue_id), action, field_name, old_value, new_value, user_id,
            json.dumps(details, default=str) if details is not None else None,
        ]
        if "actor_id" in self._table_columns(conn, "issue_history"):
            columns.append("actor_id")
            values.append(user_id)
        conn.execute(
            f"INSERT INTO issue_history({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )

    def create(self, data: dict, part_ids: Iterable[int], actor_id: Optional[int]) -> dict:
        with self.get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            selected_parts = sorted({int(x) for x in part_ids or []})
            issue_number = data.get("issue_number") or self._next_number(conn, data["project_id"])
            created_by = data.get("created_by") or actor_id
            priority = data.get("priority", "Medium")
            columns = [
                "issue_number", "title", "description", "status", "priority", "category",
                "created_by", "assigned_to", "due_date", "project_id", "source_type", "source_key",
            ]
            values = [
                issue_number, data["title"].strip(), data.get("description", "").strip(),
                data.get("status", "Open"), priority, data.get("category", "Design"),
                created_by, data.get("assigned_to"), data.get("due_date"), int(data["project_id"]),
                data.get("source_type"), data.get("source_key"),
            ]
            issue_columns = self._table_columns(conn, "issues")

            legacy_values = {
                "issue_key": issue_number,
                "severity": str(priority).upper(),
                "part_id": selected_parts[0] if selected_parts else None,
                "updated_by": actor_id,
                "release_blocking": 1 if priority == "Critical" else 0,
                "validation_generated": 1 if data.get("source_type") == "validation" else 0,
                "priority_score": {
                    "Low": 20, "Medium": 40, "High": 70, "Critical": 100,
                }.get(priority, 40),
            }
            if selected_parts and "revision_introduced" in issue_columns:
                row = conn.execute("SELECT revision FROM bom WHERE id=?", (selected_parts[0],)).fetchone()
                legacy_values["revision_introduced"] = row[0] if row else None
            for name, value in legacy_values.items():
                if name in issue_columns and name not in columns:
                    columns.append(name)
                    values.append(value)

            cur = conn.execute(
                f"INSERT INTO issues({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                values,
            )
            issue_id = cur.lastrowid
            for part_id in selected_parts:
                conn.execute(
                    "INSERT OR IGNORE INTO issue_parts(issue_id, part_id, linked_by) VALUES (?, ?, ?)",
                    (issue_id, part_id, actor_id),
                )
            self._history(conn, issue_id, "Issue created", actor_id, details=data)
            if data.get("assigned_to"):
                self._notify(conn, issue_id, int(data["assigned_to"]), "assigned",
                             f"{issue_number} was assigned to you")
            return self.get_by_id(issue_id, conn=conn)

    def get_by_id(self, issue_id: int, conn=None) -> Optional[dict]:
        owns = conn is None
        conn = conn or self.get_conn()
        try:
            row = conn.execute(
                """
                SELECT i.*, cu.username AS created_by_name, au.username AS assigned_to_name,
                       cl.username AS closed_by_name
                FROM issues i
                LEFT JOIN users cu ON cu.id = i.created_by
                LEFT JOIN users au ON au.id = i.assigned_to
                LEFT JOIN users cl ON cl.id = i.closed_by
                WHERE i.id = ?
                """,
                (int(issue_id),),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["parts"] = self.parts_for_issue(issue_id, conn=conn)
            return result
        finally:
            if owns:
                conn.close()

    def get_by_source(self, project_id: int, source_type: str, source_key: str) -> Optional[dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM issues
                WHERE project_id=? AND source_type=? AND source_key=? AND archived=0
                ORDER BY id DESC LIMIT 1
                """,
                (int(project_id), source_type, source_key),
            ).fetchone()
            return self.get_by_id(int(row["id"]), conn=conn) if row else None

    def validation_source_issues(self, project_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT * FROM issues
                    WHERE project_id=? AND source_type='validation' AND archived=0
                    """,
                    (int(project_id),),
                ).fetchall()
            ]

    def list_issues(self, project_id: int, filters: Optional[dict] = None) -> list[dict]:
        filters = filters or {}
        where = [
            """(i.project_id = ? OR EXISTS (
                SELECT 1 FROM issue_parts ips JOIN bom bs ON bs.id=ips.part_id
                WHERE ips.issue_id=i.id AND bs.project_id=?
            ))"""
        ]
        params = [int(project_id), int(project_id)]
        if not filters.get("include_archived"):
            where.append("i.archived = 0")
        for key in ("status", "priority", "assigned_to", "created_by"):
            if filters.get(key) not in (None, "", "All"):
                where.append(f"i.{key} = ?")
                params.append(filters[key])
        if filters.get("active_only"):
            where.append("i.status <> 'Closed'")
        if filters.get("overdue"):
            where.append("i.due_date IS NOT NULL AND date(i.due_date) < date('now') AND i.status <> 'Closed'")
        if filters.get("created_after"):
            where.append("date(i.created_at) >= date(?)")
            params.append(filters["created_after"])
        if filters.get("part_id"):
            where.append("EXISTS (SELECT 1 FROM issue_parts ipx WHERE ipx.issue_id=i.id AND ipx.part_id=?)")
            params.append(int(filters["part_id"]))
        if filters.get("keyword"):
            where.append("(i.issue_number LIKE ? OR i.title LIKE ? OR i.description LIKE ?)")
            term = f"%{filters['keyword']}%"
            params.extend([term, term, term])
        rows = []
        with self.get_conn() as conn:
            for row in conn.execute(
                f"""
                SELECT i.*, cu.username AS created_by_name, au.username AS assigned_to_name,
                       GROUP_CONCAT(DISTINCT b.name) AS affected_parts,
                       COUNT(DISTINCT ip.part_id) AS part_count
                FROM issues i
                LEFT JOIN users cu ON cu.id=i.created_by
                LEFT JOIN users au ON au.id=i.assigned_to
                LEFT JOIN issue_parts ip ON ip.issue_id=i.id
                LEFT JOIN bom b ON b.id=ip.part_id
                WHERE {' AND '.join(where)}
                GROUP BY i.id
                ORDER BY CASE i.priority
                    WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END,
                    i.updated_at DESC
                """,
                params,
            ).fetchall():
                rows.append(dict(row))
        return rows

    def parts_for_issue(self, issue_id: int, conn=None) -> list[dict]:
        owns = conn is None
        conn = conn or self.get_conn()
        try:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT b.id, b.name, b.type, b.filename, b.drawing,
                           b.base_file_name, b.base_drw_name, b.revision, b.lifecycle_state
                    FROM issue_parts ip JOIN bom b ON b.id=ip.part_id
                    WHERE ip.issue_id=? ORDER BY b.name
                    """,
                    (int(issue_id),),
                ).fetchall()
            ]
        finally:
            if owns:
                conn.close()

    def set_parts(self, issue_id: int, part_ids: Iterable[int], actor_id: Optional[int],
                  project_id: Optional[int] = None):
        with self.get_conn() as conn:
            if project_id is None:
                before = [r[0] for r in conn.execute(
                    "SELECT part_id FROM issue_parts WHERE issue_id=?", (int(issue_id),)
                ).fetchall()]
                conn.execute("DELETE FROM issue_parts WHERE issue_id=?", (int(issue_id),))
            else:
                before = [r[0] for r in conn.execute(
                    """
                    SELECT ip.part_id FROM issue_parts ip JOIN bom b ON b.id=ip.part_id
                    WHERE ip.issue_id=? AND b.project_id=?
                    """,
                    (int(issue_id), int(project_id)),
                ).fetchall()]
                conn.execute(
                    """
                    DELETE FROM issue_parts
                    WHERE issue_id=? AND part_id IN (SELECT id FROM bom WHERE project_id=?)
                    """,
                    (int(issue_id), int(project_id)),
                )
            after = sorted({int(x) for x in part_ids or []})
            for part_id in after:
                conn.execute(
                    "INSERT INTO issue_parts(issue_id, part_id, linked_by) VALUES (?, ?, ?)",
                    (int(issue_id), part_id, actor_id),
                )
            self._history(conn, issue_id, "Affected parts changed", actor_id,
                          "parts", json.dumps(before), json.dumps(after))

    def update(self, issue_id: int, changes: dict, actor_id: Optional[int]) -> dict:
        allowed = {"title", "description", "priority", "category", "assigned_to", "due_date"}
        with self.get_conn() as conn:
            old = conn.execute("SELECT * FROM issues WHERE id=?", (int(issue_id),)).fetchone()
            if not old:
                raise ValueError("Issue not found")
            sets, params = [], []
            issue_columns = self._table_columns(conn, "issues")
            for key, value in changes.items():
                if key not in allowed or value == old[key]:
                    continue
                sets.append(f"{key}=?")
                params.append(value)
                if key == "priority":
                    if "severity" in issue_columns:
                        sets.append("severity=?")
                        params.append(str(value).upper())
                    if "priority_score" in issue_columns:
                        sets.append("priority_score=?")
                        params.append({
                            "Low": 20, "Medium": 40, "High": 70, "Critical": 100,
                        }.get(value, 40))
                    if "release_blocking" in issue_columns:
                        sets.append("release_blocking=?")
                        params.append(1 if value == "Critical" else 0)
                self._history(conn, issue_id, "Issue updated", actor_id, key, old[key], value)
            if sets:
                if "updated_by" in issue_columns:
                    sets.append("updated_by=?")
                    params.append(actor_id)
                sets.append("updated_at=datetime('now')")
                conn.execute(f"UPDATE issues SET {', '.join(sets)} WHERE id=?", params + [int(issue_id)])
                if old["assigned_to"]:
                    self._notify(conn, issue_id, int(old["assigned_to"]), "modified",
                                 f"{old['issue_number']} was modified")
            if changes.get("assigned_to") and changes.get("assigned_to") != old["assigned_to"]:
                self._notify(conn, issue_id, int(changes["assigned_to"]), "assigned",
                             f"{old['issue_number']} was assigned to you")
            return self.get_by_id(issue_id, conn=conn)

    def transition(self, issue_id: int, status: str, actor_id: Optional[int], note="") -> dict:
        with self.get_conn() as conn:
            old = conn.execute("SELECT * FROM issues WHERE id=?", (int(issue_id),)).fetchone()
            if not old:
                raise ValueError("Issue not found")
            closed_by = actor_id if status == "Closed" else None
            closed_at = "datetime('now')" if status == "Closed" else "NULL"
            conn.execute(
                f"""
                UPDATE issues SET status=?, updated_at=datetime('now'), closed_by=?,
                    closed_at={closed_at} WHERE id=?
                """,
                (status, closed_by, int(issue_id)),
            )
            self._history(conn, issue_id, f"Status changed to {status}", actor_id,
                          "status", old["status"], status, {"note": note})
            if old["assigned_to"]:
                self._notify(conn, issue_id, int(old["assigned_to"]), "status_changed",
                             f"{old['issue_number']} moved to {status}")
            return self.get_by_id(issue_id, conn=conn)

    def archive(self, issue_id: int, actor_id: Optional[int], reason: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE issues SET archived=1, archive_reason=?, updated_at=datetime('now') WHERE id=?",
                (reason, int(issue_id)),
            )
            self._history(conn, issue_id, "Issue archived", actor_id, details={"reason": reason})

    def add_comment(self, issue_id: int, user_id: Optional[int], comment: str):
        with self.get_conn() as conn:
            columns = ["issue_id", "user_id", "comment"]
            values = [int(issue_id), user_id, comment.strip()]
            comment_columns = self._table_columns(conn, "issue_comments")
            if "created_by" in comment_columns:
                columns.append("created_by")
                values.append(user_id)
            if "body" in comment_columns:
                columns.append("body")
                values.append(comment.strip())
            conn.execute(
                f"INSERT INTO issue_comments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                values,
            )
            self._history(conn, issue_id, "Comment added", user_id)
            issue = conn.execute("SELECT issue_number,assigned_to FROM issues WHERE id=?", (int(issue_id),)).fetchone()
            if issue and issue["assigned_to"] and int(issue["assigned_to"]) != int(user_id or 0):
                self._notify(conn, issue_id, int(issue["assigned_to"]), "comment",
                             f"New comment on {issue['issue_number']}")

    def comments(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT c.*, u.username FROM issue_comments c
                    LEFT JOIN users u ON u.id=c.user_id
                    WHERE c.issue_id=? ORDER BY c.created_at
                    """,
                    (int(issue_id),),
                ).fetchall()
            ]

    def history(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT h.*, u.username FROM issue_history h
                    LEFT JOIN users u ON u.id=h.user_id
                    WHERE h.issue_id=? ORDER BY h.created_at DESC, h.id DESC
                    """,
                    (int(issue_id),),
                ).fetchall()
            ]

    def add_attachment(self, issue_id: int, file_name: str, file_path: str, actor_id: Optional[int]):
        with self.get_conn() as conn:
            columns = ["issue_id", "file_name", "file_path", "uploaded_by"]
            values = [int(issue_id), file_name, file_path, actor_id]
            attachment_columns = self._table_columns(conn, "issue_attachments")
            timestamp = conn.execute("SELECT datetime('now')").fetchone()[0]
            if "created_at" in attachment_columns:
                columns.append("created_at")
                values.append(timestamp)
            if "uploaded_at" in attachment_columns:
                columns.append("uploaded_at")
                values.append(timestamp)
            conn.execute(
                f"INSERT INTO issue_attachments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                values,
            )
            self._history(conn, issue_id, "Attachment added", actor_id, details={"file_name": file_name})

    def attachments(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM issue_attachments WHERE issue_id=? ORDER BY created_at DESC",
                (int(issue_id),),
            ).fetchall()]

    def link_to_commit(self, issue_ids: Iterable[int], commit_id: str, actor_id: Optional[int],
                       resolution_comment=""):
        with self.get_conn() as conn:
            for issue_id in sorted({int(x) for x in issue_ids or []}):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO issue_commit_links(
                        issue_id,commit_id,resolution_comment,linked_by
                    ) VALUES(?,?,?,?)
                    """,
                    (issue_id, commit_id, resolution_comment, actor_id),
                )
                old = conn.execute("SELECT status FROM issues WHERE id=?", (issue_id,)).fetchone()
                conn.execute(
                    "UPDATE issues SET status='Ready For Validation', updated_at=datetime('now') WHERE id=?",
                    (issue_id,),
                )
                self._history(conn, issue_id, "Linked to commit", actor_id,
                              details={"commit_id": commit_id, "resolution_comment": resolution_comment})
                issue = conn.execute("SELECT issue_number,assigned_to FROM issues WHERE id=?", (issue_id,)).fetchone()
                if issue and issue["assigned_to"]:
                    self._notify(conn, issue_id, int(issue["assigned_to"]), "linked_to_commit",
                                 f"{issue['issue_number']} was linked to {commit_id}")
                if old and old["status"] != "Ready For Validation":
                    self._history(conn, issue_id, "Status changed to Ready For Validation", actor_id,
                                  "status", old["status"], "Ready For Validation")

    def issues_for_commit(self, commit_id: str) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT i.*, l.validation_status, l.resolution_comment, l.validation_comment,
                           l.relation_type, l.note
                    FROM issue_commit_links l JOIN issues i ON i.id=l.issue_id
                    WHERE l.commit_id=? ORDER BY i.issue_number
                    """,
                    (commit_id,),
                ).fetchall()
            ]

    def commit_links_for_issue(self, issue_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    """
                    SELECT l.*, MAX(c.status) AS commit_status, MAX(c.merge_id) AS merge_id,
                           MAX(c.merged_at) AS merged_at, MAX(c.snapshotted_in) AS snapshotted_in
                    FROM issue_commit_links l
                    LEFT JOIN commits c ON c.commit_id=l.commit_id
                    WHERE l.issue_id=?
                    GROUP BY l.id
                    ORDER BY l.linked_at DESC
                    """,
                    (int(issue_id),),
                ).fetchall()
            ]

    def validate_commit_issue(self, issue_id: int, commit_id: str, solved: bool,
                              actor_id: Optional[int], comment=""):
        state = "Confirmed" if solved else "Rejected"
        with self.get_conn() as conn:
            link = conn.execute(
                "SELECT relation_type FROM issue_commit_links WHERE issue_id=? AND commit_id=?",
                (int(issue_id), commit_id),
            ).fetchone()
            relation_type = (link["relation_type"] if link else "solves") or "solves"
            old = conn.execute("SELECT status FROM issues WHERE id=?", (int(issue_id),)).fetchone()
            if relation_type in {"solves", "partial_fix"}:
                target = "Closed" if solved else "In Progress"
            else:
                target = old["status"] if old else "Open"
            conn.execute(
                """
                UPDATE issue_commit_links SET validation_status=?, validated_by=?,
                    validated_at=datetime('now'), validation_comment=?
                WHERE issue_id=? AND commit_id=?
                """,
                (state, actor_id, comment, int(issue_id), commit_id),
            )
            conn.execute(
                """
                UPDATE issues SET status=?, updated_at=datetime('now'), closed_by=?,
                    closed_at=CASE WHEN ?='Closed' THEN datetime('now') ELSE NULL END
                WHERE id=?
                """,
                (target, actor_id if solved else None, target, int(issue_id)),
            )
            self._history(conn, issue_id, f"Commit resolution {state}", actor_id,
                          "status", old["status"] if old else None, target,
                          {"commit_id": commit_id, "comment": comment})

    def reopen_for_restored_commit(self, commit_id: str, actor_id: Optional[int],
                                   note: str = "") -> list[dict]:
        """Reopen linked closed issues and write history for every affected issue."""
        reopened = []
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT i.*
                FROM issue_commit_links l
                JOIN issues i ON i.id=l.issue_id
                WHERE l.commit_id=? AND COALESCE(i.archived, 0)=0
                ORDER BY i.issue_number
                """,
                (str(commit_id),),
            ).fetchall()
            for row in rows:
                issue = dict(row)
                old_status = issue.get("status") or "Open"
                details = {"commit_id": str(commit_id), "note": note or ""}
                if old_status == "Closed":
                    conn.execute(
                        """
                        UPDATE issues
                        SET status='Open', updated_at=datetime('now'),
                            closed_by=NULL, closed_at=NULL
                        WHERE id=?
                        """,
                        (int(issue["id"]),),
                    )
                    self._history(
                        conn,
                        int(issue["id"]),
                        "Issue reopened after commit restore",
                        actor_id,
                        "status",
                        old_status,
                        "Open",
                        details,
                    )
                    issue["previous_status"] = old_status
                    issue["status"] = "Open"
                    reopened.append(issue)
                else:
                    self._history(
                        conn,
                        int(issue["id"]),
                        "Linked commit restored",
                        actor_id,
                        details=details,
                    )
        return reopened

    def summary_by_part(self, project_id: int) -> dict[int, dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT ip.part_id,
                       SUM(CASE WHEN i.archived=0 AND i.status<>'Closed' THEN 1 ELSE 0 END) active_count,
                       SUM(CASE WHEN i.archived=0 THEN 1 ELSE 0 END) total_count,
                       SUM(CASE WHEN i.archived=0 AND i.status<>'Closed' AND i.priority='Critical' THEN 1 ELSE 0 END) critical_count
                FROM issue_parts ip JOIN issues i ON i.id=ip.issue_id
                JOIN bom b ON b.id=ip.part_id
                WHERE b.project_id=?
                GROUP BY ip.part_id
                """,
                (int(project_id),),
            ).fetchall()
            return {int(r["part_id"]): dict(r) for r in rows}

    def issue_ids_by_part(self, project_id: int) -> dict[int, dict[str, set[int]]]:
        result = {}
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT ip.part_id, i.id, i.status, i.priority
                FROM issue_parts ip JOIN issues i ON i.id=ip.issue_id
                JOIN bom b ON b.id=ip.part_id
                WHERE b.project_id=? AND i.archived=0
                """,
                (int(project_id),),
            ).fetchall()
        for row in rows:
            part = result.setdefault(
                int(row["part_id"]), {"all": set(), "active": set(), "critical": set()}
            )
            issue_id = int(row["id"])
            part["all"].add(issue_id)
            if row["status"] != "Closed":
                part["active"].add(issue_id)
                if row["priority"] == "Critical":
                    part["critical"].add(issue_id)
        return result

    def blockers(self, project_id: int, part_ids: Optional[Iterable[int]] = None) -> list[dict]:
        params = [int(project_id), int(project_id)]
        part_clause = ""
        ids = sorted({int(x) for x in part_ids or []})
        if ids:
            placeholders = ",".join("?" for _ in ids)
            part_clause = f" AND EXISTS (SELECT 1 FROM issue_parts ip WHERE ip.issue_id=i.id AND ip.part_id IN ({placeholders}))"
            params.extend(ids)
        with self.get_conn() as conn:
            return [
                dict(r) for r in conn.execute(
                    f"""
                    SELECT i.* FROM issues i
                    WHERE (i.project_id=? OR EXISTS (
                        SELECT 1 FROM issue_parts ips JOIN bom bs ON bs.id=ips.part_id
                        WHERE ips.issue_id=i.id AND bs.project_id=?
                    )) AND i.archived=0 AND i.status<>'Closed'
                      AND i.priority='Critical' {part_clause}
                    ORDER BY i.issue_number
                    """,
                    params,
                ).fetchall()
            ]

    def metrics(self, project_id: int) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN archived=0 AND status<>'Closed' THEN 1 ELSE 0 END) open_count,
                    SUM(CASE WHEN archived=0 AND status='Closed' THEN 1 ELSE 0 END) closed_count,
                    SUM(CASE WHEN archived=0 AND status<>'Closed' AND priority='Critical' THEN 1 ELSE 0 END) critical_count,
                    SUM(CASE WHEN archived=0 AND status<>'Closed' AND due_date IS NOT NULL AND date(due_date)<date('now') THEN 1 ELSE 0 END) overdue_count,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN julianday(closed_at)-julianday(created_at) END) avg_resolution_days
                FROM issues i WHERE (
                    i.project_id=? OR EXISTS (
                        SELECT 1 FROM issue_parts ips JOIN bom bs ON bs.id=ips.part_id
                        WHERE ips.issue_id=i.id AND bs.project_id=?
                    )
                )
                """,
                (int(project_id), int(project_id)),
            ).fetchone()
            return {k: (row[k] or 0) for k in row.keys()}

    def analytics(self, project_id: int) -> dict:
        with self.get_conn() as conn:
            top_parts = [
                dict(r) for r in conn.execute(
                    """
                    SELECT b.id, b.name,
                           COUNT(DISTINCT i.id) AS active_count,
                           COUNT(DISTINCT CASE WHEN i.priority='Critical' THEN i.id END) AS critical_count
                    FROM bom b
                    JOIN issue_parts ip ON ip.part_id=b.id
                    JOIN issues i ON i.id=ip.issue_id
                    WHERE b.project_id=? AND i.archived=0 AND i.status<>'Closed'
                    GROUP BY b.id, b.name
                    ORDER BY critical_count DESC, active_count DESC, b.name
                    LIMIT 8
                    """,
                    (int(project_id),),
                ).fetchall()
            ]
            by_assignee = [
                dict(r) for r in conn.execute(
                    """
                    SELECT COALESCE(u.username, 'Unassigned') AS username,
                           COUNT(DISTINCT i.id) AS active_count
                    FROM issues i
                    LEFT JOIN users u ON u.id=i.assigned_to
                    WHERE i.archived=0 AND i.status<>'Closed' AND (
                        i.project_id=? OR EXISTS (
                            SELECT 1 FROM issue_parts ips JOIN bom bs ON bs.id=ips.part_id
                            WHERE ips.issue_id=i.id AND bs.project_id=?
                        )
                    )
                    GROUP BY i.assigned_to, u.username
                    ORDER BY active_count DESC
                    LIMIT 8
                    """,
                    (int(project_id), int(project_id)),
                ).fetchall()
            ]
            return {"top_parts": top_parts, "by_assignee": by_assignee}

    def snapshot_state(self, project_id: int) -> dict:
        issues = self.list_issues(project_id, {"include_archived": False})
        metrics = self.metrics(project_id)
        return {
            "summary": metrics,
            "issues": [
                {
                    "issue_number": x["issue_number"], "title": x["title"],
                    "status": x["status"], "priority": x["priority"],
                    "affected_parts": x.get("affected_parts"),
                }
                for x in issues
            ],
        }

    def _notify(self, conn, issue_id: int, user_id: int, event_type: str, message: str):
        conn.execute(
            "INSERT INTO issue_notifications(issue_id,user_id,event_type,message) VALUES(?,?,?,?)",
            (int(issue_id), int(user_id), event_type, message),
        )
