import json
import sqlite3
from typing import Iterable

from config import DB_NAME


class ProjectEventRepository:
    """Small append-only event feed used for multi-user UI synchronization.

    This is intentionally not a replacement for the real domain tables.  It is
    a cheap invalidation channel: clients poll only this tiny table, then reload
    the affected rows from the authoritative tables.
    """

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self.ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        return conn

    def ensure_schema(self) -> None:
        try:
            with self.get_conn() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS project_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER,
                        actor_user_id INTEGER,
                        event_type TEXT NOT NULL,
                        entity_type TEXT DEFAULT '',
                        entity_id TEXT DEFAULT '',
                        payload_json TEXT DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_project_events_project_id_id
                        ON project_events(project_id, id);
                    CREATE INDEX IF NOT EXISTS idx_project_events_created_at
                        ON project_events(created_at);
                    """
                )
        except Exception:
            # Event sync is a convenience layer; domain operations must not fail
            # because the invalidation feed could not be initialized.
            pass

    def emit(
        self,
        project_id,
        actor_user_id,
        event_type: str,
        entity_type: str = "",
        entity_id=None,
        payload: dict | None = None,
    ) -> int | None:
        try:
            with self.get_conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO project_events(
                        project_id, actor_user_id, event_type, entity_type,
                        entity_id, payload_json
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        int(project_id) if project_id is not None else None,
                        int(actor_user_id) if actor_user_id is not None else None,
                        str(event_type or "changed"),
                        str(entity_type or ""),
                        "" if entity_id is None else str(entity_id),
                        json.dumps(payload or {}, ensure_ascii=False, default=str),
                    ),
                )
                return int(cur.lastrowid)
        except Exception:
            return None

    def current_id(self, project_id=None) -> int:
        try:
            with self.get_conn() as conn:
                if project_id is None:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(id),0) FROM project_events"
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT COALESCE(MAX(id),0)
                        FROM project_events
                        WHERE project_id=? OR project_id IS NULL
                        """,
                        (int(project_id),),
                    ).fetchone()
                return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def list_after(self, last_seen_id: int, project_id=None, limit: int = 100) -> list[dict]:
        try:
            with self.get_conn() as conn:
                params: list = [int(last_seen_id)]
                project_clause = ""
                if project_id is not None:
                    project_clause = "AND (project_id=? OR project_id IS NULL)"
                    params.append(int(project_id))
                params.append(max(1, int(limit)))
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM project_events
                    WHERE id>? {project_clause}
                    ORDER BY id
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        except Exception:
            return []
        events = []
        for row in rows:
            event = dict(row)
            try:
                payload = json.loads(event.get("payload_json") or "{}")
            except Exception:
                payload = {}
            event["payload"] = payload if isinstance(payload, dict) else {}
            events.append(event)
        return events

    def prune_old(self, keep_last: int = 5000) -> None:
        """Best-effort cleanup so the feed stays tiny in long-running projects."""
        try:
            with self.get_conn() as conn:
                threshold = conn.execute(
                    """
                    SELECT id FROM project_events
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                    """,
                    (max(100, int(keep_last)),),
                ).fetchone()
                if threshold:
                    conn.execute(
                        "DELETE FROM project_events WHERE id<?",
                        (int(threshold["id"]),),
                    )
        except Exception:
            pass


def unique_ints(values: Iterable) -> list[int]:
    result = []
    seen = set()
    for value in values or []:
        try:
            number = int(value)
        except Exception:
            continue
        if number not in seen:
            seen.add(number)
            result.append(number)
    return result
