"""Durable item timeline, independent development branches, and frozen releases."""
from contextlib import contextmanager
import json
import sqlite3

from config import DB_NAME


SCHEMA = """
CREATE TABLE IF NOT EXISTS nexus_branches (
    id TEXT PRIMARY KEY, project_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE, owner_id INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'Active'
        CHECK(state IN ('Active','Paused','Merged','Abandoned')),
    head_snapshot_id TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, merge_commit_id TEXT,
    UNIQUE(project_id,name)
);
CREATE TABLE IF NOT EXISTS nexus_branch_items (
    branch_id TEXT NOT NULL REFERENCES nexus_branches(id),
    bom_id INTEGER NOT NULL REFERENCES bom(id),
    PRIMARY KEY(branch_id,bom_id)
);
CREATE INDEX IF NOT EXISTS idx_nexus_branch_items_item
    ON nexus_branch_items(bom_id,branch_id);
CREATE TABLE IF NOT EXISTS nexus_branch_checkouts (
    branch_id TEXT NOT NULL REFERENCES nexus_branches(id),
    project_id INTEGER NOT NULL, bom_id INTEGER NOT NULL REFERENCES bom(id),
    user_id INTEGER NOT NULL, head_snapshot_id TEXT NOT NULL,
    workspace_path TEXT NOT NULL, checked_out_at TEXT NOT NULL,
    PRIMARY KEY(branch_id,bom_id)
);
CREATE INDEX IF NOT EXISTS idx_nexus_branch_checkouts_item
    ON nexus_branch_checkouts(project_id,bom_id,branch_id);
CREATE TABLE IF NOT EXISTS nexus_branch_snapshots (
    id TEXT PRIMARY KEY, branch_id TEXT NOT NULL REFERENCES nexus_branches(id),
    parent_id TEXT, manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
    note TEXT NOT NULL, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nexus_snapshots_branch
    ON nexus_branch_snapshots(branch_id,created_at);
CREATE TABLE IF NOT EXISTS nexus_releases (
    id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, bom_id INTEGER NOT NULL,
    cad_revision TEXT NOT NULL, document_revision TEXT NOT NULL,
    note TEXT NOT NULL, manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
    actor_id INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nexus_releases_item
    ON nexus_releases(project_id,bom_id,created_at);
CREATE TABLE IF NOT EXISTS nexus_item_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
    bom_id INTEGER NOT NULL, branch_id TEXT, event_type TEXT NOT NULL,
    title TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', actor_id INTEGER NOT NULL,
    created_at TEXT NOT NULL, snapshot_id TEXT, release_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_nexus_events_item
    ON nexus_item_events(project_id,bom_id,created_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS nexus_active_branches (
    user_id INTEGER NOT NULL, project_id INTEGER NOT NULL, bom_id INTEGER NOT NULL,
    branch_id TEXT NOT NULL REFERENCES nexus_branches(id), updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id,project_id,bom_id)
);
CREATE INDEX IF NOT EXISTS idx_nexus_active_branch
    ON nexus_active_branches(project_id,user_id,branch_id);
"""


def ensure_history_schema(conn):
    conn.executescript(SCHEMA)
    for table in ("nexus_branch_snapshots", "nexus_releases", "nexus_item_events"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT,'Frozen Nexus history is immutable'); END
            """)


class NexusHistoryRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def columns(conn, table):
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def tables(conn):
        return {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}

    def branch(self, conn, branch_id, project_id):
        row = conn.execute(
            "SELECT * FROM nexus_branches WHERE id=? AND project_id=?",
            (str(branch_id), int(project_id)),
        ).fetchone()
        if not row:
            raise ValueError("The Nexus branch was not found in the selected project.")
        return dict(row)

    @staticmethod
    def event(conn, project_id, item_ids, actor_id, now, event_type, title,
              note="", branch_id=None, snapshot_id=None, release_id=None, payload=None):
        ids = sorted(set(map(int, item_ids)))
        for item_id in ids:
            conn.execute("""
                INSERT INTO nexus_item_events(
                    project_id,bom_id,actor_id,created_at,event_type,title,note,
                    branch_id,snapshot_id,release_id,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (int(project_id), item_id, int(actor_id), now, event_type, title,
                  note, branch_id, snapshot_id, release_id,
                  json.dumps(payload or {}, sort_keys=True)))
        if "project_events" in NexusHistoryRepository.tables(conn):
            conn.execute("""
                INSERT INTO project_events(project_id,actor_user_id,event_type,entity_type,entity_id,payload_json)
                VALUES(?,?,'nexus.history.changed','ITEM',?,?)
            """, (int(project_id), int(actor_id), str(ids[0]) if ids else "",
                  json.dumps({"item_ids": ids, "branch_id": branch_id,
                              "master_changed": event_type == "BRANCH_MERGED"})))
