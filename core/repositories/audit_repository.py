import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from config import DB_NAME


class AuditRepository:
    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def audit_supported(self) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_log'"
            ).fetchone()
            return bool(row)

    def _compute_hash(
        self,
        prev_hash: str,
        created_at: str,
        project_id: Optional[int],
        part_id: int,
        user_id: Optional[int],
        event_type: str,
        entity_type: str,
        entity_id: Optional[str],
        message: str,
        payload_json: str,
    ) -> str:
        blob = "|".join(
            [
                prev_hash or "",
                created_at or "",
                str(project_id or ""),
                str(part_id or ""),
                str(user_id or ""),
                (event_type or ""),
                (entity_type or ""),
                (entity_id or ""),
                (message or ""),
                (payload_json or ""),
            ]
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def append_event(
        self,
        *,
        part_id: int,
        project_id: Optional[int],
        user_id: Optional[int],
        event_type: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> int:
        if not part_id:
            raise ValueError("part_id is required")
        if not event_type:
            raise ValueError("event_type is required")
        if not entity_type:
            raise ValueError("entity_type is required")

        payload_json = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))

        with self.get_conn() as conn:
            if created_at is None:
                created_at = (
                    conn.execute("SELECT datetime('now') AS now").fetchone()["now"]
                )

            prev = conn.execute(
                "SELECT hash FROM audit_log WHERE part_id = ? ORDER BY id DESC LIMIT 1",
                (int(part_id),),
            ).fetchone()
            prev_hash = (prev["hash"] if prev else "") or ""

            h = self._compute_hash(
                prev_hash,
                str(created_at),
                int(project_id) if project_id is not None else None,
                int(part_id),
                int(user_id) if user_id is not None else None,
                str(event_type),
                str(entity_type),
                str(entity_id) if entity_id is not None else None,
                str(message or ""),
                payload_json,
            )

            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO audit_log(
                    part_id, project_id, user_id,
                    event_type, entity_type, entity_id,
                    message, payload_json,
                    created_at, prev_hash, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(part_id),
                    int(project_id) if project_id is not None else None,
                    int(user_id) if user_id is not None else None,
                    str(event_type),
                    str(entity_type),
                    str(entity_id) if entity_id is not None else None,
                    str(message or ""),
                    payload_json,
                    str(created_at),
                    prev_hash,
                    h,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_events_for_part_ids(
        self, part_ids: List[int], limit: int = 500
    ) -> List[Dict[str, Any]]:
        ids = [int(x) for x in (part_ids or []) if x]
        if not ids:
            return []
        placeholders = ",".join(["?"] * len(ids))
        with self.get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM audit_log
                WHERE part_id IN ({placeholders})
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*ids, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]

    def verify_chain(self, part_id: int) -> Tuple[bool, Optional[int]]:
        """Verify hash chain for a part_id.

        Returns: (ok, first_bad_id)
        """
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE part_id = ? ORDER BY id ASC",
                (int(part_id),),
            ).fetchall()
            prev_hash = ""
            for r in rows:
                d = dict(r)
                expected = self._compute_hash(
                    d.get("prev_hash") or "",
                    d.get("created_at") or "",
                    d.get("project_id"),
                    d.get("part_id"),
                    d.get("user_id"),
                    d.get("event_type") or "",
                    d.get("entity_type") or "",
                    d.get("entity_id"),
                    d.get("message") or "",
                    d.get("payload_json") or "",
                )

                # prev_hash must match previous row hash
                if (d.get("prev_hash") or "") != prev_hash:
                    return False, int(d.get("id")) if d.get("id") is not None else None

                # hash must match computed
                if (d.get("hash") or "") != expected:
                    return False, int(d.get("id")) if d.get("id") is not None else None

                prev_hash = d.get("hash") or ""

        return True, None
