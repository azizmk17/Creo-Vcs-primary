"""Session-local undo support for destructive Nexus PDM actions.

This is intentionally small and conservative.  It snapshots the database rows
directly affected by an operation, then restores them with INSERT OR REPLACE
when the user presses Ctrl+Z.  It is not a replacement for audit history or a
server-side transaction journal; it is a practical desktop undo stack for the
current user's most recent actions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from config import DB_NAME


@dataclass
class UndoRecord:
    label: str
    tables: list[tuple[str, list[dict]]]
    created_at: str


class UndoService:
    def __init__(self, db_name=DB_NAME, limit: int = 50):
        self.db_name = db_name
        self.limit = int(limit)
        self._stack: list[UndoRecord] = []

    def can_undo(self) -> bool:
        return bool(self._stack)

    def last_label(self) -> str:
        return self._stack[-1].label if self._stack else ""

    def push(self, record: UndoRecord | None) -> None:
        if not record or not any(rows for _table, rows in record.tables):
            return
        self._stack.append(record)
        if len(self._stack) > self.limit:
            del self._stack[0 : len(self._stack) - self.limit]

    def undo_last(self) -> UndoRecord:
        if not self._stack:
            raise ValueError("There is no Nexus action to undo.")
        record = self._stack.pop()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table, rows in record.tables:
                for row in rows:
                    self._insert_or_replace(conn, table, row)
        return record

    def snapshot_item_delete(self, item_id: int, label: str | None = None) -> UndoRecord:
        item_id = int(item_id)
        with self._conn() as conn:
            file_ids = [
                int(row["id"])
                for row in self._select(conn, "part_files", "part_id=?", (item_id,))
            ]
            usage_ids = [
                int(row["id"])
                for row in self._select(
                    conn,
                    "item_usages",
                    "parent_item_id=? OR child_item_id=?",
                    (item_id, item_id),
                )
            ]
            folder_ids = self._item_folder_ids(conn, item_id)
            tables: list[tuple[str, list[dict]]] = [
                ("bom", self._select(conn, "bom", "id=?", (item_id,))),
                ("bom_folders", self._select_ids(conn, "bom_folders", folder_ids)),
                ("bom_folder_items", self._select(conn, "bom_folder_items", "bom_id=?", (item_id,))),
                ("bom_item_categories", self._select(conn, "bom_item_categories", "bom_id=?", (item_id,))),
                ("bom_children", self._select(conn, "bom_children", "parent_id=? OR child_id=?", (item_id, item_id))),
                ("part_files", self._select_ids(conn, "part_files", file_ids)),
                ("part_file_versions", self._select_in(conn, "part_file_versions", "file_id", file_ids)),
                ("issue_parts", self._select(conn, "issue_parts", "part_id=?", (item_id,))),
                ("cad_item_associations", self._select(conn, "cad_item_associations", "item_id=?", (item_id,))),
                ("cad_document_checkout_items", self._select(conn, "cad_document_checkout_items", "item_id=?", (item_id,))),
                ("item_usages", self._select_ids(conn, "item_usages", usage_ids)),
                ("item_occurrences", self._select_in(conn, "item_occurrences", "item_usage_id", usage_ids)),
            ]
        return UndoRecord(
            label=label or f"Delete Item {item_id}",
            tables=tables,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def snapshot_item_update(self, item_id: int, label: str | None = None) -> UndoRecord:
        item_id = int(item_id)
        with self._conn() as conn:
            tables: list[tuple[str, list[dict]]] = [
                ("bom", self._select(conn, "bom", "id=?", (item_id,))),
                ("bom_item_categories", self._select(conn, "bom_item_categories", "bom_id=?", (item_id,))),
            ]
        return UndoRecord(
            label=label or f"Edit Item {item_id}",
            tables=tables,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def snapshot_cad_member_remove(self, member_id: int, label: str | None = None) -> UndoRecord:
        member_id = int(member_id)
        with self._conn() as conn:
            usage_ids = [
                int(row["id"])
                for row in self._select(conn, "item_usages", "cad_member_id=?", (member_id,))
            ]
            occurrence_ids = {
                int(row["id"])
                for row in self._select_in(conn, "item_occurrences", "item_usage_id", usage_ids)
            }
            occurrence_ids.update(
                int(row["id"])
                for row in self._select(
                    conn, "item_occurrences", "source_cad_member_id=?", (member_id,)
                )
            )
            tables: list[tuple[str, list[dict]]] = [
                ("cad_document_members", self._select(conn, "cad_document_members", "id=?", (member_id,))),
                ("item_usages", self._select_ids(conn, "item_usages", usage_ids)),
                ("item_occurrences", self._select_ids(conn, "item_occurrences", sorted(occurrence_ids))),
                ("pdm_build_results", self._select(conn, "pdm_build_results", "cad_member_id=?", (member_id,))),
            ]
        return UndoRecord(
            label=label or f"Remove CAD Component {member_id}",
            tables=tables,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _table_exists(self, conn, table: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (str(table),),
            ).fetchone()
        )

    def _select(self, conn, table: str, where: str, params: tuple) -> list[dict]:
        if not self._table_exists(conn, table):
            return []
        try:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {self._quote_ident(table)} WHERE {where}", params
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def _select_ids(self, conn, table: str, ids: list[int]) -> list[dict]:
        return self._select_in(conn, table, "id", ids)

    def _select_in(self, conn, table: str, column: str, values: list[int]) -> list[dict]:
        values = [int(value) for value in (values or [])]
        if not values or not self._table_exists(conn, table):
            return []
        placeholders = ",".join("?" for _ in values)
        try:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {self._quote_ident(table)} "
                    f"WHERE {self._quote_ident(column)} IN ({placeholders})",
                    values,
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            return []

    def _item_folder_ids(self, conn, item_id: int) -> list[int]:
        if not self._table_exists(conn, "bom_folders"):
            return []
        folder_ids: list[int] = []

        def collect(folder_id: int) -> None:
            if int(folder_id) in folder_ids:
                return
            folder_ids.append(int(folder_id))
            for row in conn.execute(
                "SELECT id FROM bom_folders WHERE parent_folder_id=?",
                (int(folder_id),),
            ).fetchall():
                collect(int(row["id"]))

        try:
            for row in conn.execute(
                "SELECT id FROM bom_folders WHERE parent_bom_id=?",
                (int(item_id),),
            ).fetchall():
                collect(int(row["id"]))
        except sqlite3.OperationalError:
            return []
        return folder_ids

    def _insert_or_replace(self, conn, table: str, row: dict) -> None:
        if not row or not self._table_exists(conn, table):
            return
        columns = [
            str(info[1])
            for info in conn.execute(f"PRAGMA table_info({self._quote_ident(table)})").fetchall()
        ]
        usable = [column for column in columns if column in row]
        if not usable:
            return
        placeholders = ",".join("?" for _ in usable)
        column_sql = ",".join(self._quote_ident(column) for column in usable)
        values = [row.get(column) for column in usable]
        conn.execute(
            f"INSERT OR REPLACE INTO {self._quote_ident(table)} "
            f"({column_sql}) VALUES ({placeholders})",
            values,
        )

    @staticmethod
    def _quote_ident(name: str) -> str:
        return '"' + str(name).replace('"', '""') + '"'
