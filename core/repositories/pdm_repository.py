import os
import re
import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Optional

from config import DB_NAME
from setup.migrations import (
    _migration_32,
    _migration_33,
    _migration_34,
    _migration_35,
    _migration_38,
    _migration_39,
)
from utils import get_base_name, get_version_number, is_creo_file


ASSOCIATION_RULES = {
    "OWNER": (1, 1, 1),
    "CONTRIBUTING_IMAGE": (0, 1, 1),
    "IMAGE": (0, 0, 1),
    "CONTRIBUTING_CONTENT": (0, 1, 0),
    "CONTENT": (0, 0, 0),
}


class PdmRepository:
    """Persistence for Windchill-style CAD Documents and Item structures."""

    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self._ensure_schema()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self.get_conn() as conn:
            def ensure_column(table_name: str, column_name: str, column_sql: str) -> None:
                try:
                    columns = [
                        str(row[1])
                        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                    ]
                    if column_name not in columns:
                        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise

            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_documents'"
            ).fetchone()
            if not exists:
                _migration_32(conn)
            _migration_33(conn)
            _migration_34(conn)
            migration_35_applied = False
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone():
                migration_35_applied = bool(
                    conn.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=35"
                    ).fetchone()
                )
            if not migration_35_applied:
                _migration_35(conn)
            _migration_38(conn)
            migration_39_applied = False
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone():
                migration_39_applied = bool(
                    conn.execute(
                        "SELECT 1 FROM schema_migrations WHERE version=39"
                    ).fetchone()
                )
            if not migration_39_applied:
                _migration_39(conn)
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_documents'"
            ).fetchone():
                ensure_column(
                    "cad_documents",
                    "latest_creo_file_version",
                    "latest_creo_file_version INTEGER",
                )
                ensure_column(
                    "cad_documents",
                    "latest_creo_file_name",
                    "latest_creo_file_name TEXT",
                )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_document_iterations'"
            ).fetchone():
                ensure_column(
                    "cad_document_iterations",
                    "creo_file_version",
                    "creo_file_version INTEGER",
                )
                ensure_column(
                    "cad_document_iterations",
                    "source_file_name",
                    "source_file_name TEXT",
                )
            self._backfill_legacy_approved_creo_files(conn)

    @staticmethod
    def _dict(row) -> Optional[dict]:
        return dict(row) if row else None

    @staticmethod
    def _clean_creo_file(value: str) -> str:
        name = os.path.basename(str(value or "").replace("\\", "/")).strip()
        if not name:
            return ""
        base = get_base_name(name)
        return base or name

    @staticmethod
    def _clean_creo_key(value: str) -> str:
        return PdmRepository._clean_creo_file(value).casefold()

    @staticmethod
    def _approved_creo_from_values(
        *,
        filename: str = "",
        base_file_name: str = "",
        approved_version=None,
        pr_path: str = "",
    ) -> dict | None:
        try:
            version = (
                int(str(approved_version).strip())
                if approved_version is not None and str(approved_version).strip()
                else None
            )
        except Exception:
            version = None
        approved_name = os.path.basename(str(pr_path or "").replace("\\", "/")).strip()
        if not is_creo_file(approved_name):
            approved_name = ""
        if version is None and approved_name:
            version = get_version_number(approved_name)
        if not approved_name and version is not None:
            base_name = (
                PdmRepository._clean_creo_file(base_file_name)
                or get_base_name(os.path.basename(str(filename or "").replace("\\", "/")))
                or PdmRepository._clean_creo_file(filename)
            )
            if base_name:
                approved_name = f"{base_name}.{version}"
        if version is None or not approved_name:
            return None
        return {
            "latest_creo_file_version": int(version),
            "latest_creo_file_name": approved_name,
        }

    @staticmethod
    def _commit_sort_key(row: dict) -> tuple[str, str, int]:
        return (
            str(row.get("merged_at") or ""),
            str(row.get("committed_at") or ""),
            int(row.get("id") or 0),
        )

    def _legacy_approved_creo_for_document(self, conn, document: dict) -> dict | None:
        """Resolve the last approved Creo file from pre-PDM commit history."""
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "commits" not in tables:
            return self._legacy_current_creo_for_document(conn, document, tables)
        commit_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(commits)").fetchall()
        }
        required = {"id", "filename", "approved_version", "status"}
        if not required.issubset(commit_columns):
            return self._legacy_current_creo_for_document(conn, document, tables)
        project_id = document.get("project_id")
        doc_id = document.get("id")
        legacy_item_id = document.get("legacy_bom_id") or document.get("item_id")
        doc_key = self._clean_creo_key(document.get("file_name"))
        if not doc_key:
            return self._legacy_current_creo_for_document(conn, document, tables)

        select_columns = [
            "id", "filename", "approved_version", "status",
        ]
        for column in (
            "project_id", "part_id", "base_file_name", "pr_path",
            "merged_at", "committed_at", "cad_document_id",
        ):
            if column in commit_columns:
                select_columns.append(column)
        clauses = [
            "lower(COALESCE(status,''))='approved'",
            "approved_version IS NOT NULL",
            "trim(CAST(approved_version AS TEXT))<>''",
        ]
        params = []
        if "project_id" in commit_columns and project_id is not None:
            clauses.append("project_id=?")
            params.append(int(project_id))
        broad_clauses = []
        if "cad_document_id" in commit_columns and doc_id is not None:
            broad_clauses.append("cad_document_id=?")
            params.append(int(doc_id))
        if "part_id" in commit_columns and legacy_item_id is not None:
            broad_clauses.append("part_id=?")
            params.append(int(legacy_item_id))
        if "base_file_name" in commit_columns:
            broad_clauses.append("lower(COALESCE(base_file_name,''))=?")
            params.append(doc_key)
        broad_clauses.append("lower(COALESCE(filename,'')) LIKE ?")
        params.append(f"{doc_key}.%")
        if broad_clauses:
            clauses.append("(" + " OR ".join(broad_clauses) + ")")
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT {", ".join(select_columns)}
                FROM commits
                WHERE {" AND ".join(clauses)}
                """,
                params,
            ).fetchall()
        ]
        candidates = []
        for row in rows:
            row_doc_id = row.get("cad_document_id")
            direct_cad = (
                row_doc_id is not None and doc_id is not None
                and int(row_doc_id) == int(doc_id)
            )
            row_base = self._clean_creo_key(
                row.get("base_file_name") or get_base_name(str(row.get("filename") or ""))
                or row.get("filename")
            )
            same_base = row_base == doc_key
            same_legacy_item = (
                legacy_item_id is not None
                and row.get("part_id") is not None
                and int(row.get("part_id")) == int(legacy_item_id)
            )
            if not direct_cad and not same_base:
                continue
            if same_legacy_item or same_base or direct_cad:
                candidate = self._approved_creo_from_values(
                    filename=row.get("filename"),
                    base_file_name=row.get("base_file_name"),
                    approved_version=row.get("approved_version"),
                    pr_path=row.get("pr_path"),
                )
                if candidate:
                    candidates.append((self._commit_sort_key(row), candidate))
        if candidates:
            return sorted(candidates, key=lambda item: item[0])[-1][1]
        return self._legacy_current_creo_for_document(conn, document, tables)

    def _legacy_current_creo_for_document(
        self, conn, document: dict, tables: set[str] | None = None
    ) -> dict | None:
        """Fallback to the current legacy BOM file when no approved commit row exists."""
        tables = tables or {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "bom" not in tables:
            return None
        legacy_item_id = document.get("legacy_bom_id") or document.get("item_id")
        project_id = document.get("project_id")
        doc_key = self._clean_creo_key(document.get("file_name"))
        if not doc_key:
            return None
        bom_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(bom)").fetchall()
        }
        select_columns = ["id"]
        for column in (
            "project_id", "filename", "drawing", "base_file_name", "base_drw_name",
        ):
            if column in bom_columns:
                select_columns.append(column)
        clauses = []
        params = []
        if legacy_item_id is not None:
            clauses.append("id=?")
            params.append(int(legacy_item_id))
        elif project_id is not None and "project_id" in bom_columns:
            clauses.append("project_id=?")
            params.append(int(project_id))
        if not clauses:
            return None
        candidates = []
        for row in conn.execute(
            f"SELECT {', '.join(select_columns)} FROM bom WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall():
            data = dict(row)
            for field in ("filename", "drawing"):
                value = str(data.get(field) or "").strip()
                if not value or self._clean_creo_key(value) != doc_key:
                    continue
                candidate = self._approved_creo_from_values(
                    filename=value,
                    base_file_name=get_base_name(value) or value,
                    approved_version=get_version_number(value),
                    pr_path=value,
                )
                if candidate:
                    candidates.append(candidate)
        return candidates[-1] if candidates else None

    def _apply_legacy_approved_creo_fallback(self, conn, records: list[dict]) -> list[dict]:
        for record in records or []:
            if record.get("latest_creo_file_version") and record.get("latest_creo_file_name"):
                continue
            candidate = self._legacy_approved_creo_for_document(conn, record)
            if not candidate:
                continue
            record["latest_creo_file_version"] = candidate["latest_creo_file_version"]
            record["latest_creo_file_name"] = candidate["latest_creo_file_name"]
        return records

    def _backfill_legacy_approved_creo_files(self, conn) -> None:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_documents'"
        ).fetchone():
            return
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM cad_documents
                WHERE latest_creo_file_version IS NULL
                   OR latest_creo_file_name IS NULL
                   OR trim(COALESCE(latest_creo_file_name,''))=''
                """
            ).fetchall()
        ]
        for row in rows:
            candidate = self._legacy_approved_creo_for_document(conn, row)
            if not candidate:
                continue
            conn.execute(
                """
                UPDATE cad_documents
                SET latest_creo_file_version=?,
                    latest_creo_file_name=?,
                    modified_at=datetime('now')
                WHERE id=?
                  AND (latest_creo_file_version IS NULL
                       OR latest_creo_file_name IS NULL
                       OR trim(COALESCE(latest_creo_file_name,''))='')
                """,
                (
                    int(candidate["latest_creo_file_version"]),
                    str(candidate["latest_creo_file_name"]),
                    int(row["id"]),
                ),
            )

    @staticmethod
    def _add_checkout_usernames(conn, records: list[dict]) -> list[dict]:
        """Enrich CAD rows when the host database includes application users.

        Some diagnostic/unit-test databases intentionally contain only the PDM
        and BOM tables.  Keeping this lookup optional lets those databases use
        the same repository while the full application still displays the
        checkout owner's user name.
        """
        for record in records:
            record["checked_out_by_username"] = None
        if not records:
            return records
        has_users = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if not has_users:
            return records
        user_ids = sorted(
            {
                int(record["checked_out_by"])
                for record in records
                if record.get("checked_out_by") is not None
            }
        )
        if not user_ids:
            return records
        placeholders = ",".join("?" for _ in user_ids)
        usernames = {
            int(row["id"]): str(row["username"] or "")
            for row in conn.execute(
                f"SELECT id,username FROM users WHERE id IN ({placeholders})",
                user_ids,
            ).fetchall()
        }
        for record in records:
            owner_id = record.get("checked_out_by")
            if owner_id is not None:
                record["checked_out_by_username"] = usernames.get(int(owner_id))
        return records

    @staticmethod
    def _association_sort_key(row: dict) -> tuple[int, int]:
        order = {
            "OWNER": 0,
            "CONTRIBUTING_IMAGE": 1,
            "IMAGE": 2,
            "CONTRIBUTING_CONTENT": 3,
            "CONTENT": 4,
        }
        return (
            order.get(str(row.get("association_type") or "").upper(), 9),
            int(row.get("id") or row.get("association_id") or 0),
        )

    def _attach_item_associations(
        self, conn, records: list[dict]
    ) -> list[dict]:
        """Attach every active Item association while retaining legacy aliases."""
        document_ids = sorted({int(record["id"]) for record in records})
        for record in records:
            record["associations"] = []
            record["association_count"] = 0
        if not document_ids:
            return records
        placeholders = ",".join("?" for _ in document_ids)
        has_item_versions = all(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            for name in ("bom_revisions", "bom_iterations")
        )
        version_select = (
            ", br.revision_code || '.' || bi.iteration_number AS item_version_label"
            if has_item_versions else ", NULL AS item_version_label"
        )
        version_join = (
            """
            LEFT JOIN bom_revisions br ON br.id=b.current_revision_id
            LEFT JOIN bom_iterations bi ON bi.id=b.current_iteration_id
            """
            if has_item_versions else ""
        )
        rows = conn.execute(
            f"""
            SELECT a.*,a.id AS association_id,
                   b.part_number AS item_number,
                   b.aes_number AS item_aes_number,b.name AS item_name
                   {version_select}
            FROM cad_item_associations a
            JOIN bom b ON b.id=a.item_id
            {version_join}
            WHERE a.active=1
              AND b.deleted_at IS NULL
              AND lower(COALESCE(b.status,''))<>'deleted'
              AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
              AND a.cad_document_id IN ({placeholders})
            ORDER BY a.cad_document_id,
                     CASE upper(a.association_type)
                         WHEN 'OWNER' THEN 0
                         WHEN 'CONTRIBUTING_IMAGE' THEN 1
                         WHEN 'IMAGE' THEN 2
                         WHEN 'CONTRIBUTING_CONTENT' THEN 3
                         WHEN 'CONTENT' THEN 4 ELSE 9 END,
                     a.id
            """,
            document_ids,
        ).fetchall()
        by_document = defaultdict(list)
        for row in rows:
            association = dict(row)
            by_document[int(association["cad_document_id"])].append(association)
        legacy_fields = (
            "association_id", "item_id", "association_type", "item_number",
            "item_aes_number", "item_name", "item_version_label",
            "drives_structure", "drives_attributes", "participates_in_structure",
            "is_primary_drawing", "drawing_model_cad_document_id",
        )
        for record in records:
            associations = sorted(
                by_document.get(int(record["id"]), []),
                key=self._association_sort_key,
            )
            record["associations"] = associations
            record["association_count"] = len(associations)
            primary = associations[0] if associations else {}
            for field in legacy_fields:
                record[field] = primary.get(field)
        return records

    @staticmethod
    def _attach_checkout_items(conn, records: list[dict]) -> list[dict]:
        """Attach every Item reserved by each active CAD working copy."""
        document_ids = sorted({int(record["id"]) for record in records})
        for record in records:
            record["checkout_item_ids"] = []
        if not document_ids:
            return records
        placeholders = ",".join("?" for _ in document_ids)
        rows = conn.execute(
            f"""
            SELECT cad_document_id,item_id
            FROM cad_document_checkout_items
            WHERE cad_document_id IN ({placeholders})
            ORDER BY cad_document_id,item_id
            """,
            document_ids,
        ).fetchall()
        by_document = defaultdict(list)
        for row in rows:
            by_document[int(row["cad_document_id"])].append(int(row["item_id"]))
        for record in records:
            record["checkout_item_ids"] = list(
                by_document.get(int(record["id"]), [])
            )
        return records

    def _attach_related_drawings(
        self, conn, records: list[dict], *, item_id: int | None = None
    ) -> list[dict]:
        """Attach managed DRW metadata to its owning PRT/ASM record."""
        model_ids = [
            int(record["id"])
            for record in records
            if str(record.get("category") or "").upper()
            in {"ASSEMBLY", "COMPONENT"}
        ]
        for record in records:
            record["related_drawings"] = []
        if not model_ids:
            return records
        placeholders = ",".join("?" for _ in model_ids)
        if item_id is None:
            drawing_sql = f"""
                SELECT d.*,NULL AS drawing_association_id,
                       NULL AS drawing_association_type,
                       0 AS is_primary_drawing
                FROM cad_documents d
                WHERE upper(d.category)='DRAWING'
                  AND d.drawing_owner_cad_document_id IN ({placeholders})
                ORDER BY lower(d.name),lower(d.file_name),d.id
            """
            params = model_ids
        else:
            drawing_sql = f"""
                SELECT d.*,a.id AS drawing_association_id,
                       a.association_type AS drawing_association_type,
                       COALESCE(a.is_primary_drawing,0) AS is_primary_drawing
                FROM cad_documents d
                LEFT JOIN cad_item_associations a
                  ON a.cad_document_id=d.id AND a.item_id=? AND a.active=1
                WHERE upper(d.category)='DRAWING'
                  AND d.drawing_owner_cad_document_id IN ({placeholders})
                ORDER BY lower(d.name),lower(d.file_name),d.id
            """
            params = [int(item_id), *model_ids]
        drawings = [dict(row) for row in conn.execute(drawing_sql, params).fetchall()]
        for drawing in drawings:
            drawing["selected_for_item"] = bool(
                drawing.get("drawing_association_id")
            )
        self._add_checkout_usernames(conn, drawings)
        self._apply_legacy_approved_creo_fallback(conn, drawings)
        by_owner = {}
        for drawing in drawings:
            owner_id = drawing.get("drawing_owner_cad_document_id")
            if owner_id is not None:
                by_owner.setdefault(int(owner_id), []).append(drawing)
        for record in records:
            record["related_drawings"] = list(
                by_owner.get(int(record["id"]), [])
            )
        return records

    @staticmethod
    def normalize_base(value: str) -> str:
        name = os.path.basename(str(value or "").replace("\\", "/")).strip()
        return os.path.splitext(name)[0].casefold()

    def create_cad_document(
        self,
        project_id: int,
        number: str,
        name: str,
        file_name: str,
        *,
        category: str = "COMPONENT",
        authoring_application: str = "CREO",
        document_type: str = "CAD_DOCUMENT",
        build_excluded: bool = False,
        supplier_owner_item_id=None,
        legacy_bom_id=None,
        drawing_owner_cad_document_id=None,
    ) -> int:
        source_file = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
        version_match = re.match(
            r"^(.*\.(?:asm|prt|drw))\.(\d+)$", source_file, flags=re.IGNORECASE
        )
        clean_file = version_match.group(1) if version_match else source_file
        if not clean_file:
            raise ValueError("CAD file name is required.")
        # CAD Documents are file-identity objects.  The historical ``number``
        # column is still populated as an internal compatibility key because
        # older databases require it, but it is not the PLM/Item number.
        clean_number = clean_file
        clean_category = str(category or "COMPONENT").strip().upper()
        native_extension = os.path.splitext(clean_file)[1].casefold()
        clean_category = {
            ".asm": "ASSEMBLY",
            ".prt": "COMPONENT",
            ".drw": "DRAWING",
        }.get(native_extension, clean_category)
        if clean_category not in {"ASSEMBLY", "COMPONENT", "DRAWING", "OTHER"}:
            raise ValueError(f"Unsupported CAD category: {category}.")
        with self.get_conn() as conn:
            duplicate_number = conn.execute(
                """
                SELECT id,file_name FROM cad_documents
                WHERE project_id=? AND lower(trim(number))=lower(?)
                LIMIT 1
                """,
                (int(project_id), clean_number),
            ).fetchone()
            if duplicate_number:
                raise ValueError(
                    f"CAD file {clean_number} already identifies "
                    f"{duplicate_number['file_name']} in this product."
                )
            duplicate_file = conn.execute(
                """
                SELECT id,number FROM cad_documents
                WHERE project_id=? AND lower(file_name)=lower(?)
                LIMIT 1
                """,
                (int(project_id), clean_file),
            ).fetchone()
            if duplicate_file:
                raise ValueError(
                    f"CAD file {clean_file} is already registered in this product."
                )
            drawing_owner_id = None
            if clean_category == "DRAWING":
                if drawing_owner_cad_document_id in (None, "", 0, "0"):
                    raise ValueError(
                        "Select the PRT or ASM model owned by this drawing."
                    )
                drawing_owner_id = int(drawing_owner_cad_document_id)
                owner = conn.execute(
                    """
                    SELECT id FROM cad_documents
                    WHERE id=? AND project_id=?
                      AND upper(category) IN ('ASSEMBLY','COMPONENT')
                    """,
                    (drawing_owner_id, int(project_id)),
                ).fetchone()
                if not owner:
                    raise ValueError(
                        "A drawing must be bound to a PRT or ASM in the same project."
                    )
            try:
                cur = conn.execute(
                    """
                    INSERT INTO cad_documents(
                        project_id,number,name,file_name,base_file_name,
                        authoring_application,category,document_type,build_excluded,
                        supplier_owner_item_id,legacy_bom_id,
                        drawing_owner_cad_document_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(project_id), clean_number, str(name or clean_number).strip(),
                        clean_file, self.normalize_base(clean_file),
                        str(authoring_application or "CREO").strip().upper(),
                        clean_category, str(document_type or "CAD_DOCUMENT").strip().upper(),
                        int(bool(build_excluded)),
                        int(supplier_owner_item_id) if supplier_owner_item_id else None,
                        int(legacy_bom_id) if legacy_bom_id else None,
                        drawing_owner_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                message = str(exc).lower()
                if "number" in message:
                    raise ValueError(
                        f"CAD file {clean_number} already exists in this product."
                    ) from exc
                if "file_name" in message:
                    raise ValueError(
                        f"CAD file {clean_file} is already registered in this product."
                    ) from exc
                raise
            cad_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO cad_document_iterations(
                    cad_document_id,revision,iteration,lifecycle_state,primary_path
                ) VALUES(?,'A',1,'IN_WORK',?)
                """,
                (cad_id, clean_file),
            )
            return cad_id

    def get_cad_document(self, cad_document_id: int) -> Optional[dict]:
        with self.get_conn() as conn:
            record = self._dict(conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone())
            if not record:
                return None
            self._attach_item_associations(conn, [record])
            self._attach_checkout_items(conn, [record])
            self._add_checkout_usernames(conn, [record])
            self._apply_legacy_approved_creo_fallback(conn, [record])
            self._attach_related_drawings(conn, [record])
            return record

    def get_current_cad_iteration(self, cad_document_id: int) -> Optional[dict]:
        with self.get_conn() as conn:
            document = conn.execute(
                "SELECT revision,iteration FROM cad_documents WHERE id=?",
                (int(cad_document_id),),
            ).fetchone()
            if not document:
                return None
            return self._dict(conn.execute(
                """
                SELECT * FROM cad_document_iterations
                WHERE cad_document_id=? AND revision=? AND iteration=?
                ORDER BY id DESC LIMIT 1
                """,
                (
                    int(cad_document_id),
                    str(document["revision"] or "A"),
                    int(document["iteration"] or 1),
                ),
            ).fetchone())

    def get_cad_document_by_file(self, project_id: int, file_name: str) -> Optional[dict]:
        clean_file = os.path.basename(str(file_name or "").replace("\\", "/")).strip()
        version_match = re.match(
            r"^(.*\.(?:asm|prt|drw))\.\d+$", clean_file, flags=re.IGNORECASE
        )
        if version_match:
            clean_file = version_match.group(1)
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                """
                SELECT * FROM cad_documents
                WHERE project_id=? AND lower(file_name)=lower(?)
                """,
                (int(project_id), clean_file),
            ).fetchone())

    def get_cad_document_by_legacy_item(self, item_id: int) -> Optional[dict]:
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                """
                SELECT * FROM cad_documents
                WHERE legacy_bom_id=? AND category<>'DRAWING'
                ORDER BY id LIMIT 1
                """,
                (int(item_id),),
            ).fetchone())

    def get_cad_document_by_base(self, project_id: int, base_name: str) -> Optional[dict]:
        normalized = self.normalize_base(base_name)
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                """
                SELECT * FROM cad_documents
                WHERE project_id=? AND lower(base_file_name)=lower(?)
                ORDER BY id LIMIT 1
                """,
                (int(project_id), normalized),
            ).fetchone())

    def remove_supplier_cad_document(
        self, project_id: int, owner_item_id: int, base_name: str
    ) -> bool:
        document = self.get_cad_document_by_base(int(project_id), base_name)
        if not document or int(document.get("supplier_owner_item_id") or 0) != int(owner_item_id):
            return False
        cad_id = int(document["id"])
        with self.get_conn() as conn:
            current = conn.execute(
                "SELECT checked_out_by FROM cad_documents WHERE id=?", (cad_id,)
            ).fetchone()
            if current and current["checked_out_by"] is not None:
                raise ValueError(
                    "Check in or undo the CAD Document before removing it from the supplier package."
                )
            used = conn.execute(
                """
                SELECT 1 FROM cad_document_members
                WHERE parent_cad_document_id=? OR child_cad_document_id=? LIMIT 1
                """,
                (cad_id, cad_id),
            ).fetchone()
            if used:
                conn.execute(
                    "UPDATE cad_documents SET supplier_owner_item_id=NULL WHERE id=?",
                    (cad_id,),
                )
                conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET active=0,is_primary_drawing=0,modified_at=datetime('now')
                    WHERE cad_document_id=? AND item_id=? AND active=1
                    """,
                    (cad_id, int(owner_item_id)),
                )
                return False
            other_association = conn.execute(
                """
                SELECT 1 FROM cad_item_associations
                WHERE cad_document_id=? AND item_id<>? AND active=1 LIMIT 1
                """,
                (cad_id, int(owner_item_id)),
            ).fetchone()
            if other_association:
                conn.execute(
                    "UPDATE cad_documents SET supplier_owner_item_id=NULL WHERE id=?",
                    (cad_id,),
                )
                conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET active=0,is_primary_drawing=0,modified_at=datetime('now')
                    WHERE cad_document_id=? AND item_id=? AND active=1
                    """,
                    (cad_id, int(owner_item_id)),
                )
                return False
            conn.execute(
                "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                (cad_id,),
            )
            conn.execute("DELETE FROM cad_item_associations WHERE cad_document_id=?", (cad_id,))
            conn.execute("DELETE FROM cad_document_contents WHERE cad_document_id=?", (cad_id,))
            conn.execute("DELETE FROM cad_document_iterations WHERE cad_document_id=?", (cad_id,))
            conn.execute("DELETE FROM cad_documents WHERE id=?", (cad_id,))
            return True

    def delete_cad_document(
        self, cad_document_id: int, *, delete_related_drawings: bool = False
    ) -> dict:
        cad_id = int(cad_document_id)
        deleted_ids = []
        with self.get_conn() as conn:
            def delete_one(document_id: int) -> None:
                row = conn.execute(
                    "SELECT * FROM cad_documents WHERE id=?",
                    (int(document_id),),
                ).fetchone()
                if not row:
                    return
                if row["checked_out_by"] is not None:
                    raise ValueError(
                        f"Check in or undo checkout for {row['file_name']} before deleting it."
                    )
                if str(row["category"] or "").upper() in {"ASSEMBLY", "COMPONENT"}:
                    related = conn.execute(
                        """
                        SELECT id,file_name,checked_out_by FROM cad_documents
                        WHERE drawing_owner_cad_document_id=?
                          AND upper(category)='DRAWING'
                        ORDER BY lower(file_name),id
                        """,
                        (int(document_id),),
                    ).fetchall()
                    if related and not delete_related_drawings:
                        names = ", ".join(str(drawing["file_name"] or drawing["id"]) for drawing in related[:5])
                        if len(related) > 5:
                            names += f", +{len(related) - 5} more"
                        raise ValueError(
                            "Delete or include the related drawing CAD Documents first: "
                            + names
                        )
                    for drawing in related:
                        if drawing["checked_out_by"] is not None:
                            raise ValueError(
                                f"Check in or undo checkout for {drawing['file_name']} before deleting it."
                            )
                    for drawing in related:
                        delete_one(int(drawing["id"]))

                member_rows = conn.execute(
                    """
                    SELECT id FROM cad_document_members
                    WHERE parent_cad_document_id=? OR child_cad_document_id=?
                    """,
                    (int(document_id), int(document_id)),
                ).fetchall()
                member_ids = [int(member["id"]) for member in member_rows]
                if member_ids:
                    placeholders = ",".join("?" for _ in member_ids)
                    usage_ids = [
                        int(row["id"])
                        for row in conn.execute(
                            f"""
                            SELECT id FROM item_usages
                            WHERE upper(source)='CAD_BUILD' AND cad_member_id IN ({placeholders})
                            """,
                            member_ids,
                        ).fetchall()
                    ]
                    if usage_ids:
                        usage_placeholders = ",".join("?" for _ in usage_ids)
                        conn.execute(
                            f"DELETE FROM item_occurrences WHERE item_usage_id IN ({usage_placeholders})",
                            usage_ids,
                        )
                        conn.execute(
                            f"DELETE FROM item_usages WHERE id IN ({usage_placeholders})",
                            usage_ids,
                        )
                    conn.execute(
                        f"DELETE FROM item_occurrences WHERE source_cad_member_id IN ({placeholders})",
                        member_ids,
                    )
                    conn.execute(
                        f"""
                        UPDATE item_usages
                        SET cad_member_id=NULL,modified_at=datetime('now')
                        WHERE cad_member_id IN ({placeholders})
                        """,
                        member_ids,
                    )
                    conn.execute(
                        f"DELETE FROM pdm_build_results WHERE cad_member_id IN ({placeholders})",
                        member_ids,
                    )
                    conn.execute(
                        f"DELETE FROM cad_document_members WHERE id IN ({placeholders})",
                        member_ids,
                    )

                build_run_ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM pdm_build_runs WHERE root_cad_document_id=?",
                        (int(document_id),),
                    ).fetchall()
                ]
                if build_run_ids:
                    placeholders = ",".join("?" for _ in build_run_ids)
                    conn.execute(
                        f"DELETE FROM pdm_build_results WHERE build_run_id IN ({placeholders})",
                        build_run_ids,
                    )
                    conn.execute(
                        f"DELETE FROM item_structure_iterations WHERE build_run_id IN ({placeholders})",
                        build_run_ids,
                    )
                    conn.execute(
                        f"DELETE FROM pdm_build_runs WHERE id IN ({placeholders})",
                        build_run_ids,
                    )

                content_ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM cad_document_contents WHERE cad_document_id=?",
                        (int(document_id),),
                    ).fetchall()
                ]
                if content_ids:
                    placeholders = ",".join("?" for _ in content_ids)
                    conn.execute(
                        f"DELETE FROM cad_document_contents WHERE derived_from_content_id IN ({placeholders})",
                        content_ids,
                    )
                conn.execute(
                    "DELETE FROM cad_document_contents WHERE cad_document_id=?",
                    (int(document_id),),
                )
                conn.execute(
                    "DELETE FROM cad_item_associations WHERE cad_document_id=?",
                    (int(document_id),),
                )
                conn.execute(
                    "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                    (int(document_id),),
                )
                conn.execute(
                    "DELETE FROM cad_document_checkout_logs WHERE cad_document_id=?",
                    (int(document_id),),
                )
                conn.execute(
                    "DELETE FROM cad_document_iterations WHERE cad_document_id=?",
                    (int(document_id),),
                )
                conn.execute(
                    "UPDATE cad_documents SET drawing_owner_cad_document_id=NULL WHERE drawing_owner_cad_document_id=?",
                    (int(document_id),),
                )
                cur = conn.execute(
                    "DELETE FROM cad_documents WHERE id=?",
                    (int(document_id),),
                )
                if cur.rowcount:
                    deleted_ids.append(int(document_id))

            delete_one(cad_id)
        return {"deleted_ids": deleted_ids, "deleted_count": len(deleted_ids)}

    @staticmethod
    def _next_revision(value: str) -> str:
        text = str(value or "A").strip().upper()
        if not text.isalpha():
            text = "A"
        digits = [ord(char) - ord("A") for char in text]
        carry = 1
        for index in range(len(digits) - 1, -1, -1):
            digits[index] += carry
            if digits[index] >= 26:
                digits[index] = 0
            else:
                carry = 0
                break
        if carry:
            digits.insert(0, 0)
        return "".join(chr(value + ord("A")) for value in digits)

    @staticmethod
    def _checkout_target_item_ids_conn(conn, cad_document_id: int) -> list[int]:
        document = conn.execute(
            """
            SELECT id,drawing_owner_cad_document_id
            FROM cad_documents WHERE id=?
            """,
            (int(cad_document_id),),
        ).fetchone()
        if not document:
            return []
        try:
            conn.execute(
                """
                UPDATE cad_item_associations
                SET active=0,modified_at=datetime('now')
                WHERE active=1
                  AND item_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM bom b
                      WHERE b.id=cad_item_associations.item_id
                        AND b.deleted_at IS NULL
                        AND lower(COALESCE(b.status,''))<>'deleted'
                        AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                  )
                """
            )
        except Exception:
            pass
        rows = conn.execute(
            """
            SELECT a.item_id,a.association_type,a.id
            FROM cad_item_associations a
            JOIN bom b ON b.id=a.item_id
            WHERE a.cad_document_id=? AND a.active=1
              AND b.deleted_at IS NULL
              AND lower(COALESCE(b.status,''))<>'deleted'
              AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
            ORDER BY CASE upper(a.association_type)
                WHEN 'OWNER' THEN 0
                WHEN 'CONTRIBUTING_IMAGE' THEN 1
                WHEN 'IMAGE' THEN 2
                WHEN 'CONTRIBUTING_CONTENT' THEN 3
                WHEN 'CONTENT' THEN 4 ELSE 9 END,a.id
            """,
            (int(cad_document_id),),
        ).fetchall()
        # A drawing's explicit Item relationships are authoritative.  Falling
        # back to the model is retained only for legacy drawings that have not
        # yet received an explicit per-Item selection.
        if not rows and document["drawing_owner_cad_document_id"] is not None:
            rows = conn.execute(
                """
                SELECT a.item_id,a.association_type,a.id
                FROM cad_item_associations a
                JOIN bom b ON b.id=a.item_id
                WHERE a.cad_document_id=? AND a.active=1
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                ORDER BY CASE upper(a.association_type)
                    WHEN 'OWNER' THEN 0
                    WHEN 'CONTRIBUTING_IMAGE' THEN 1
                    WHEN 'IMAGE' THEN 2
                    WHEN 'CONTRIBUTING_CONTENT' THEN 3
                    WHEN 'CONTENT' THEN 4 ELSE 9 END,a.id
                LIMIT 1
                """,
                (int(document["drawing_owner_cad_document_id"]),),
            ).fetchall()
        result = []
        seen = set()
        for row in rows:
            item_id = int(row["item_id"])
            if item_id not in seen:
                seen.add(item_id)
                result.append(item_id)
        return result

    def list_checkout_target_item_ids(self, cad_document_id: int) -> list[int]:
        """Return Items that must be reserved when this CAD is checked out."""
        with self.get_conn() as conn:
            return self._checkout_target_item_ids_conn(
                conn, int(cad_document_id)
            )

    def list_cad_checkout_item_ids(self, cad_document_id: int) -> list[int]:
        """Return the Item reservations captured for an active CAD checkout."""
        with self.get_conn() as conn:
            return [
                int(row["item_id"])
                for row in conn.execute(
                    """
                    SELECT item_id FROM cad_document_checkout_items
                    WHERE cad_document_id=? ORDER BY item_id
                    """,
                    (int(cad_document_id),),
                ).fetchall()
            ]

    def checkout_cad_document(
        self,
        cad_document_id: int,
        user_id: int,
        *,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        workspace_machine_id: str | None = None,
    ) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone()
            if not row:
                raise ValueError("The CAD Document was not found.")
            state = str(row["lifecycle_state"] or "").strip().upper()
            if state == "RELEASED":
                raise ValueError(
                    "The CAD Document is Released. Create a new CAD revision before check out."
                )
            if state == "OBSOLETE":
                raise ValueError("An Obsolete CAD Document cannot be checked out.")
            owner = row["checked_out_by"]
            if owner is not None and int(owner) != int(user_id):
                raise ValueError("The CAD Document is checked out by another user.")
            checkout_item_ids = self._checkout_target_item_ids_conn(
                conn, int(cad_document_id)
            )
            checkout_item_id = checkout_item_ids[0] if checkout_item_ids else None
            if owner is not None:
                # Repeated checkout by the owner is intentionally idempotent.
                updates = []
                params = []
                if row["checkout_item_id"] is None and checkout_item_id is not None:
                    updates.append("checkout_item_id=?")
                    params.append(checkout_item_id)
                if workspace_id:
                    current_workspace = str(row["checkout_workspace_id"] or "").strip()
                    if current_workspace and current_workspace != str(workspace_id):
                        raise ValueError(
                            "The CAD Document is already checked out in another workspace."
                        )
                    updates.extend([
                        "checkout_workspace_id=?",
                        "checkout_workspace_name=?",
                        "checkout_workspace_machine_id=?",
                    ])
                    params.extend([
                        str(workspace_id), str(workspace_name or ""),
                        str(workspace_machine_id or ""),
                    ])
                if updates:
                    conn.execute(
                        f"UPDATE cad_documents SET {', '.join(updates)} WHERE id=?",
                        (*params, int(cad_document_id)),
                    )
                for item_id in checkout_item_ids:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO cad_document_checkout_items(
                            cad_document_id,item_id,user_id
                        ) VALUES(?,?,?)
                        """,
                        (int(cad_document_id), int(item_id), int(user_id)),
                    )
                repeated = self._dict(conn.execute(
                    "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
                ).fetchone())
                repeated["checkout_item_ids"] = list(checkout_item_ids)
                return repeated
            conn.execute(
                """
                UPDATE cad_documents SET checked_out_by=?,
                    checked_out_at=datetime('now'),checkout_item_id=?,
                    checkout_workspace_id=?,checkout_workspace_name=?,
                    checkout_workspace_machine_id=?
                WHERE id=?
                """,
                (
                    int(user_id), checkout_item_id,
                    str(workspace_id or "") or None,
                    str(workspace_name or "") or None,
                    str(workspace_machine_id or "") or None,
                    int(cad_document_id),
                ),
            )
            conn.execute(
                "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                (int(cad_document_id),),
            )
            for item_id in checkout_item_ids:
                conn.execute(
                    """
                    INSERT INTO cad_document_checkout_items(
                        cad_document_id,item_id,user_id
                    ) VALUES(?,?,?)
                    """,
                    (int(cad_document_id), int(item_id), int(user_id)),
                )
            conn.execute(
                """
                INSERT INTO cad_document_checkout_logs(
                    cad_document_id,item_id,user_id,action,workspace_id,
                    workspace_name,workspace_machine_id
                ) VALUES(?,?,?,'CHECKOUT',?,?,?)
                """,
                (
                    int(cad_document_id), checkout_item_id, int(user_id),
                    str(workspace_id or "") or None,
                    str(workspace_name or "") or None,
                    str(workspace_machine_id or "") or None,
                ),
            )
        return self.get_cad_document(int(cad_document_id))

    def checkin_cad_document(
        self, cad_document_id: int, user_id: int, source_path: str,
        note: str = "", source_commit_id=None, source_file_name=None,
        creo_file_version=None,
    ) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone()
            if not row:
                raise ValueError("The CAD Document was not found.")
            if row["checked_out_by"] is None or int(row["checked_out_by"]) != int(user_id):
                raise ValueError("Check out the CAD Document before check-in.")
            if str(row["lifecycle_state"] or "").strip().upper() == "RELEASED":
                raise ValueError(
                    "A Released CAD revision cannot be changed. Create a new CAD revision."
                )
            revision = str(row["revision"] or "A")
            next_iteration = int(conn.execute(
                """
                SELECT COALESCE(MAX(iteration),0)+1 FROM cad_document_iterations
                WHERE cad_document_id=? AND revision=?
                """,
                (int(cad_document_id), revision),
            ).fetchone()[0])
            path = str(source_path or "").strip()
            source_file = (
                os.path.basename(str(source_file_name or "").replace("\\", "/")).strip()
                or os.path.basename(path)
                or str(row["file_name"] or "")
            )
            try:
                creo_version = (
                    int(creo_file_version)
                    if creo_file_version is not None and str(creo_file_version).strip() != ""
                    else None
                )
            except Exception:
                creo_version = None
            if creo_version is None:
                match = re.match(
                    r"^.*\.(?:asm|prt|drw)\.(\d+)$",
                    source_file,
                    flags=re.IGNORECASE,
                )
                if match:
                    creo_version = int(match.group(1))
            sha256 = None
            size = None
            if path and os.path.isfile(path):
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                sha256 = digest.hexdigest()
                size = os.path.getsize(path)
            cur = conn.execute(
                """
                INSERT INTO cad_document_iterations(
                    cad_document_id,revision,iteration,lifecycle_state,
                    primary_path,sha256,size_bytes,source_commit_id,
                    checkin_note,created_by,creo_file_version,source_file_name
                ) VALUES(?,?,?,'IN_WORK',?,?,?,?,?,?,?,?)
                """,
                (
                    int(cad_document_id), revision, next_iteration, path or None,
                    sha256, size, source_commit_id, str(note or "") or None,
                    int(user_id), creo_version, source_file or None,
                ),
            )
            iteration_id = int(cur.lastrowid)
            checkout_item_id = (
                int(row["checkout_item_id"])
                if row["checkout_item_id"] is not None else None
            )
            checkout_item_ids = [
                int(item["item_id"])
                for item in conn.execute(
                    """
                    SELECT item_id FROM cad_document_checkout_items
                    WHERE cad_document_id=? ORDER BY item_id
                    """,
                    (int(cad_document_id),),
                ).fetchall()
            ]
            if not checkout_item_ids and checkout_item_id is not None:
                checkout_item_ids = [checkout_item_id]
            conn.execute(
                """
                UPDATE cad_documents SET iteration=?,lifecycle_state='IN_WORK',
                    checked_out_by=NULL,checked_out_at=NULL,checkout_item_id=NULL,
                    checkout_workspace_id=NULL,checkout_workspace_name=NULL,
                    checkout_workspace_machine_id=NULL,
                    latest_creo_file_version=?,latest_creo_file_name=?,
                    modified_at=datetime('now')
                WHERE id=?
                """,
                (next_iteration, creo_version, source_file or None, int(cad_document_id)),
            )
            conn.execute(
                """
                INSERT INTO cad_document_checkout_logs(
                    cad_document_id,item_id,user_id,action,cad_iteration_id,note,
                    workspace_id,workspace_name,workspace_machine_id
                ) VALUES(?,?,?,'CHECKIN',?,?,?,?,?)
                """,
                (
                    int(cad_document_id), checkout_item_id, int(user_id),
                    iteration_id, str(note or "").strip() or None,
                    row["checkout_workspace_id"], row["checkout_workspace_name"],
                    row["checkout_workspace_machine_id"],
                ),
            )
            conn.execute(
                "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                (int(cad_document_id),),
            )
        return {
            **self.get_cad_document(int(cad_document_id)),
            "iteration_id": iteration_id,
            "checkout_item_ids": checkout_item_ids,
        }

    def undo_checkout_cad_document(
        self, cad_document_id: int, user_id: int, note: str = ""
    ) -> dict:
        """Release a CAD working copy without creating an iteration."""
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone()
            if not row:
                raise ValueError("The CAD Document was not found.")
            owner = row["checked_out_by"]
            if owner is None:
                raise ValueError("The CAD Document is not checked out.")
            if int(owner) != int(user_id):
                raise ValueError("The CAD Document is checked out by another user.")
            checkout_item_id = (
                int(row["checkout_item_id"])
                if row["checkout_item_id"] is not None else None
            )
            checkout_item_ids = [
                int(item["item_id"])
                for item in conn.execute(
                    """
                    SELECT item_id FROM cad_document_checkout_items
                    WHERE cad_document_id=? ORDER BY item_id
                    """,
                    (int(cad_document_id),),
                ).fetchall()
            ]
            if not checkout_item_ids and checkout_item_id is not None:
                checkout_item_ids = [checkout_item_id]
            conn.execute(
                """
                UPDATE cad_documents
                SET checked_out_by=NULL,checked_out_at=NULL,checkout_item_id=NULL,
                    checkout_workspace_id=NULL,checkout_workspace_name=NULL,
                    checkout_workspace_machine_id=NULL,
                    modified_at=datetime('now')
                WHERE id=?
                """,
                (int(cad_document_id),),
            )
            conn.execute(
                """
                INSERT INTO cad_document_checkout_logs(
                    cad_document_id,item_id,user_id,action,note,workspace_id,
                    workspace_name,workspace_machine_id
                ) VALUES(?,?,?,'UNDO_CHECKOUT',?,?,?,?)
                """,
                (
                    int(cad_document_id), checkout_item_id, int(user_id),
                    str(note or "").strip() or None,
                    row["checkout_workspace_id"], row["checkout_workspace_name"],
                    row["checkout_workspace_machine_id"],
                ),
            )
            conn.execute(
                "DELETE FROM cad_document_checkout_items WHERE cad_document_id=?",
                (int(cad_document_id),),
            )
        return {
            **self.get_cad_document(int(cad_document_id)),
            "checkout_item_ids": checkout_item_ids,
        }

    def list_checked_out_cad_by_workspace(self, workspace_id: str) -> list[dict]:
        with self.get_conn() as conn:
            return [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM cad_documents
                    WHERE checked_out_by IS NOT NULL AND checkout_workspace_id=?
                    ORDER BY lower(file_name),id
                    """,
                    (str(workspace_id),),
                ).fetchall()
            ]

    def list_checked_out_cad_for_item(self, item_id: int) -> list[dict]:
        """Return every active CAD working copy associated with one Item.

        The checkout link is authoritative for current working copies, while
        the active association also covers CAD data created by older Nexus
        versions before checkout links were persisted.
        """
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT d.*,
                       (
                           SELECT a.association_type
                           FROM cad_item_associations a
                           WHERE a.cad_document_id=d.id
                             AND a.item_id=? AND a.active=1
                           ORDER BY CASE upper(a.association_type)
                               WHEN 'OWNER' THEN 0
                               WHEN 'CONTRIBUTING_IMAGE' THEN 1
                               WHEN 'IMAGE' THEN 2
                               WHEN 'CONTRIBUTING_CONTENT' THEN 3
                               WHEN 'CONTENT' THEN 4 ELSE 9 END,
                               a.id
                           LIMIT 1
                       ) AS association_type
                FROM cad_documents d
                WHERE d.checked_out_by IS NOT NULL
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM cad_document_checkout_items checkout_link
                          WHERE checkout_link.cad_document_id=d.id
                            AND checkout_link.item_id=?
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM cad_item_associations association_link
                          WHERE association_link.cad_document_id=d.id
                            AND association_link.item_id=?
                            AND association_link.active=1
                      )
                  )
                ORDER BY lower(d.file_name),d.id
                """,
                (int(item_id), int(item_id), int(item_id)),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_cad_checkout_history(self, cad_document_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cad_document_checkout_logs
                WHERE cad_document_id=? ORDER BY id DESC
                """,
                (int(cad_document_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def revise_cad_document(self, cad_document_id: int, user_id: int) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone()
            if not row:
                raise ValueError("The CAD Document was not found.")
            if row["checked_out_by"] is not None:
                raise ValueError("Check in the CAD Document before creating a revision.")
            revision = self._next_revision(str(row["revision"] or "A"))
            cur = conn.execute(
                """
                INSERT INTO cad_document_iterations(
                    cad_document_id,revision,iteration,lifecycle_state,
                    primary_path,created_by,checkin_note,creo_file_version,
                    source_file_name
                ) VALUES(?,?,1,'IN_WORK',?,?,?,?,?)
                """,
                (
                    int(cad_document_id), revision, row["file_name"],
                    int(user_id), "New CAD revision",
                    row["latest_creo_file_version"], row["latest_creo_file_name"],
                ),
            )
            conn.execute(
                """
                UPDATE cad_documents SET revision=?,iteration=1,
                    lifecycle_state='IN_WORK',modified_at=datetime('now') WHERE id=?
                """,
                (revision, int(cad_document_id)),
            )
        return {**self.get_cad_document(int(cad_document_id)), "iteration_id": int(cur.lastrowid)}

    def release_cad_document(self, cad_document_id: int) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (int(cad_document_id),)
            ).fetchone()
            if not row:
                raise ValueError("The CAD Document was not found.")
            if row["checked_out_by"] is not None:
                raise ValueError("Check in the CAD Document before release.")
            conn.execute(
                "UPDATE cad_documents SET lifecycle_state='RELEASED',modified_at=datetime('now') WHERE id=?",
                (int(cad_document_id),),
            )
            conn.execute(
                """
                UPDATE cad_document_iterations SET lifecycle_state='RELEASED'
                WHERE cad_document_id=? AND revision=? AND iteration=?
                """,
                (int(cad_document_id), str(row["revision"]), int(row["iteration"])),
            )
        return self.get_cad_document(int(cad_document_id))

    def list_cad_documents(self, project_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT d.*
                FROM cad_documents d
                WHERE d.project_id=?
                ORDER BY lower(d.file_name), lower(d.name), d.id
                """,
                (int(project_id),),
            ).fetchall()
            records = [dict(row) for row in rows]
            self._attach_item_associations(conn, records)
            self._attach_checkout_items(conn, records)
            self._add_checkout_usernames(conn, records)
            self._apply_legacy_approved_creo_fallback(conn, records)
            return self._attach_related_drawings(conn, records)

    def list_item_cad_documents(self, item_id: int) -> list[dict]:
        with self.get_conn() as conn:
            has_item_versions = all(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()
                for name in ("bom_revisions", "bom_iterations")
            )
            item_version_select = (
                ", br.revision_code || '.' || bi.iteration_number AS item_version_label"
                if has_item_versions else ", NULL AS item_version_label"
            )
            item_version_join = (
                """
                LEFT JOIN bom b ON b.id=a.item_id
                LEFT JOIN bom_revisions br ON br.id=b.current_revision_id
                LEFT JOIN bom_iterations bi ON bi.id=b.current_iteration_id
                """
                if has_item_versions else
                "LEFT JOIN bom b ON b.id=a.item_id"
            )
            rows = conn.execute(
                f"""
                SELECT d.*,a.id AS association_id,a.association_type,
                       a.drives_structure,a.drives_attributes,
                       a.participates_in_structure,a.is_primary_drawing,
                       a.drawing_model_cad_document_id,
                       b.part_number AS item_number,
                       b.aes_number AS item_aes_number,
                       b.name AS item_name
                       {item_version_select}
                FROM cad_item_associations a
                JOIN cad_documents d ON d.id=a.cad_document_id
                {item_version_join}
                WHERE a.item_id=? AND a.active=1
                  AND (b.id IS NULL OR b.deleted_at IS NULL)
                  AND (b.id IS NULL OR lower(COALESCE(b.status,''))<>'deleted')
                  AND (b.id IS NULL OR lower(COALESCE(b.lifecycle_state,''))<>'deleted')
                ORDER BY CASE a.association_type
                    WHEN 'OWNER' THEN 0 WHEN 'CONTRIBUTING_IMAGE' THEN 1
                    WHEN 'IMAGE' THEN 2 WHEN 'CONTRIBUTING_CONTENT' THEN 3
                    ELSE 4 END, lower(d.file_name)
                """,
                (int(item_id),),
            ).fetchall()
            records = self._add_checkout_usernames(
                conn, [dict(row) for row in rows]
            )
            self._attach_checkout_items(conn, records)
            self._apply_legacy_approved_creo_fallback(conn, records)
            return self._attach_related_drawings(
                conn, records, item_id=int(item_id)
            )

    def list_related_drawings(self, model_cad_document_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cad_documents
                WHERE drawing_owner_cad_document_id=?
                  AND upper(category)='DRAWING'
                ORDER BY lower(name),lower(file_name),id
                """,
                (int(model_cad_document_id),),
            ).fetchall()
            records = self._add_checkout_usernames(
                conn, [dict(row) for row in rows]
            )
            self._attach_item_associations(conn, records)
            self._attach_checkout_items(conn, records)
            return self._apply_legacy_approved_creo_fallback(conn, records)

    def bind_drawing_to_model(
        self, drawing_cad_document_id: int, model_cad_document_id: int
    ) -> dict:
        with self.get_conn() as conn:
            drawing = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?",
                (int(drawing_cad_document_id),),
            ).fetchone()
            model = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?",
                (int(model_cad_document_id),),
            ).fetchone()
            if not drawing or str(drawing["category"] or "").upper() != "DRAWING":
                raise ValueError("Select a managed DRW CAD Document.")
            if not model or str(model["category"] or "").upper() not in {
                "ASSEMBLY", "COMPONENT"
            }:
                raise ValueError("Select a managed PRT or ASM CAD Document.")
            if int(drawing["project_id"]) != int(model["project_id"]):
                raise ValueError("The drawing and model must belong to the same project.")
            if drawing["checked_out_by"] is not None or model["checked_out_by"] is not None:
                raise ValueError(
                    "Check in both the drawing and model before changing their relationship."
                )
            drawing_associations = conn.execute(
                """
                SELECT item_id,is_primary_drawing
                FROM cad_item_associations
                WHERE cad_document_id=? AND active=1
                ORDER BY item_id
                """,
                (int(drawing_cad_document_id),),
            ).fetchall()
            invalid_items = []
            primary_conflicts = []
            for association in drawing_associations:
                item_id = int(association["item_id"])
                model_association = conn.execute(
                    """
                    SELECT 1 FROM cad_item_associations
                    WHERE cad_document_id=? AND item_id=? AND active=1
                    LIMIT 1
                    """,
                    (int(model_cad_document_id), item_id),
                ).fetchone()
                if not model_association:
                    invalid_items.append(item_id)
                    continue
                if association["is_primary_drawing"]:
                    conflict = conn.execute(
                        """
                        SELECT 1 FROM cad_item_associations
                        WHERE item_id=? AND drawing_model_cad_document_id=?
                          AND active=1 AND is_primary_drawing=1
                          AND cad_document_id<>?
                        LIMIT 1
                        """,
                        (
                            item_id, int(model_cad_document_id),
                            int(drawing_cad_document_id),
                        ),
                    ).fetchone()
                    if conflict:
                        primary_conflicts.append(item_id)
            if invalid_items:
                raise ValueError(
                    "The target model is not associated with Item(s): "
                    + ", ".join(str(value) for value in invalid_items)
                    + ". Associate the model first."
                )
            if primary_conflicts:
                raise ValueError(
                    "The target model already has a different primary drawing for Item(s): "
                    + ", ".join(str(value) for value in primary_conflicts)
                    + ". Clear that primary drawing first."
                )
            conn.execute(
                """
                UPDATE cad_documents
                SET drawing_owner_cad_document_id=?,modified_at=datetime('now')
                WHERE id=?
                """,
                (int(model_cad_document_id), int(drawing_cad_document_id)),
            )
            conn.execute(
                """
                UPDATE cad_item_associations
                SET drawing_model_cad_document_id=?,modified_at=datetime('now')
                WHERE cad_document_id=? AND active=1
                """,
                (int(model_cad_document_id), int(drawing_cad_document_id)),
            )
        return self.get_cad_document(int(drawing_cad_document_id))

    @staticmethod
    def _active_associations_for_cad_conn(
        conn, cad_document_id: int
    ) -> list[dict]:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.*,a.id AS association_id,
                       b.part_number AS item_number,
                       b.aes_number AS item_aes_number,b.name AS item_name
                FROM cad_item_associations a
                JOIN bom b ON b.id=a.item_id
                WHERE a.cad_document_id=? AND a.active=1
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                ORDER BY CASE upper(a.association_type)
                    WHEN 'OWNER' THEN 0
                    WHEN 'CONTRIBUTING_IMAGE' THEN 1
                    WHEN 'IMAGE' THEN 2
                    WHEN 'CONTRIBUTING_CONTENT' THEN 3
                    WHEN 'CONTENT' THEN 4 ELSE 9 END,a.id
                """,
                (int(cad_document_id),),
            ).fetchall()
        ]

    def list_active_associations_for_cad(
        self, cad_document_id: int
    ) -> list[dict]:
        with self.get_conn() as conn:
            return self._active_associations_for_cad_conn(
                conn, int(cad_document_id)
            )

    def get_active_association(
        self, cad_document_id: int, item_id: int
    ) -> Optional[dict]:
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                """
                SELECT a.*,a.id AS association_id,
                       b.part_number AS item_number,
                       b.aes_number AS item_aes_number,b.name AS item_name
                FROM cad_item_associations a
                JOIN bom b ON b.id=a.item_id
                WHERE a.cad_document_id=? AND a.item_id=? AND a.active=1
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                LIMIT 1
                """,
                (int(cad_document_id), int(item_id)),
            ).fetchone())

    def get_active_association_for_cad(self, cad_document_id: int) -> Optional[dict]:
        """Compatibility lookup; OWNER is deterministic when several exist."""
        with self.get_conn() as conn:
            rows = self._active_associations_for_cad_conn(
                conn, int(cad_document_id)
            )
            return rows[0] if rows else None

    def associate(
        self,
        project_id: int,
        item_id: int,
        cad_document_id: int,
        association_type: str,
        created_by=None,
    ) -> dict:
        kind = str(association_type or "").strip().upper()
        if kind not in ASSOCIATION_RULES:
            raise ValueError(f"Unsupported CAD–Item association: {association_type}.")
        structure, attributes, representation = ASSOCIATION_RULES[kind]
        with self.get_conn() as conn:
            item = conn.execute(
                "SELECT id,project_id FROM bom WHERE id=?", (int(item_id),)
            ).fetchone()
            cad = conn.execute(
                """
                SELECT id,project_id,checked_out_by,category,
                       drawing_owner_cad_document_id
                FROM cad_documents WHERE id=?
                """,
                (int(cad_document_id),),
            ).fetchone()
            if not item or not cad:
                raise ValueError("The Item or CAD Document was not found.")
            if int(item["project_id"] or 0) != int(project_id) or int(cad["project_id"]) != int(project_id):
                raise ValueError("CAD Documents can only be associated inside their project.")
            if cad["checked_out_by"] is not None:
                raise ValueError("Check in the CAD Document before changing its Item association.")
            if str(cad["category"] or "").upper() == "DRAWING":
                if cad["drawing_owner_cad_document_id"] is None:
                    raise ValueError(
                        "Bind the drawing to its PRT/ASM model before associating it."
                    )
                if kind not in {"CONTENT", "CONTRIBUTING_CONTENT"}:
                    raise ValueError(
                        "A related drawing can only be CONTENT of its model's Item."
                    )
                model_association = conn.execute(
                    """
                    SELECT id FROM cad_item_associations
                    WHERE cad_document_id=? AND item_id=? AND active=1
                    LIMIT 1
                    """,
                    (
                        int(cad["drawing_owner_cad_document_id"]),
                        int(item_id),
                    ),
                ).fetchone()
                if model_association is None:
                    raise ValueError(
                        "Associate the drawing's PRT/ASM model with this Item first."
                    )
            if kind == "OWNER":
                owner = conn.execute(
                    """
                    SELECT cad_document_id FROM cad_item_associations
                    WHERE item_id=? AND active=1 AND association_type='OWNER'
                      AND cad_document_id<>?
                    """,
                    (int(item_id), int(cad_document_id)),
                ).fetchone()
                if owner:
                    raise ValueError("This Item already has an OWNER CAD Document.")
                other_item = conn.execute(
                    """
                    SELECT item_id FROM cad_item_associations
                    WHERE cad_document_id=? AND active=1
                      AND association_type='OWNER' AND item_id<>?
                    LIMIT 1
                    """,
                    (int(cad_document_id), int(item_id)),
                ).fetchone()
                if other_item:
                    raise ValueError(
                        "This CAD Document is already the OWNER of another Item."
                    )
            existing = conn.execute(
                """
                SELECT * FROM cad_item_associations
                WHERE cad_document_id=? AND item_id=? AND active=1
                LIMIT 1
                """,
                (int(cad_document_id), int(item_id)),
            ).fetchone()
            drawing_model_id = (
                int(cad["drawing_owner_cad_document_id"])
                if str(cad["category"] or "").upper() == "DRAWING"
                else None
            )
            keep_primary = int(
                bool(existing and existing["is_primary_drawing"])
            )
            if existing and str(existing["association_type"] or "").upper() == kind:
                conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET drives_structure=?,drives_attributes=?,
                        participates_in_structure=?,
                        drawing_model_cad_document_id=?,
                        modified_at=datetime('now')
                    WHERE id=?
                    """,
                    (
                        structure, attributes, representation,
                        drawing_model_id, int(existing["id"]),
                    ),
                )
                return self._dict(conn.execute(
                    "SELECT * FROM cad_item_associations WHERE id=?",
                    (int(existing["id"]),),
                ).fetchone())
            conn.execute(
                """
                UPDATE cad_item_associations
                SET active=0,is_primary_drawing=0,modified_at=datetime('now')
                WHERE cad_document_id=? AND item_id=? AND active=1
                """,
                (int(cad_document_id), int(item_id)),
            )
            cur = conn.execute(
                """
                INSERT INTO cad_item_associations(
                    project_id,item_id,cad_document_id,association_type,
                    drives_structure,drives_attributes,participates_in_structure,
                    active,created_by,is_primary_drawing,
                    drawing_model_cad_document_id
                ) VALUES(?,?,?,?,?,?,?,1,?,?,?)
                """,
                (
                    int(project_id), int(item_id), int(cad_document_id), kind,
                    structure, attributes, representation,
                    int(created_by) if created_by else None,
                    keep_primary, drawing_model_id,
                ),
            )
            association_id = int(cur.lastrowid)
        return self.get_association(association_id)

    def get_association(self, association_id: int) -> Optional[dict]:
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                "SELECT * FROM cad_item_associations WHERE id=?",
                (int(association_id),),
            ).fetchone())

    def list_item_selected_drawings(
        self, item_id: int, model_cad_document_id: int | None = None
    ) -> list[dict]:
        predicates = [
            "a.item_id=?", "a.active=1", "upper(d.category)='DRAWING'"
        ]
        params = [int(item_id)]
        if model_cad_document_id is not None:
            predicates.append(
                "COALESCE(a.drawing_model_cad_document_id,"
                "d.drawing_owner_cad_document_id)=?"
            )
            params.append(int(model_cad_document_id))
        with self.get_conn() as conn:
            records = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT d.*,a.id AS drawing_association_id,
                           a.association_type AS drawing_association_type,
                           a.is_primary_drawing,
                           a.drawing_model_cad_document_id
                    FROM cad_item_associations a
                    JOIN cad_documents d ON d.id=a.cad_document_id
                    WHERE {' AND '.join(predicates)}
                    ORDER BY a.is_primary_drawing DESC,lower(d.file_name),d.id
                    """,
                    params,
                ).fetchall()
            ]
            for record in records:
                record["selected_for_item"] = True
            self._add_checkout_usernames(conn, records)
            self._apply_legacy_approved_creo_fallback(conn, records)
            return records

    def set_item_model_drawings(
        self,
        item_id: int,
        model_cad_document_id: int,
        drawing_ids,
        primary_drawing_id=None,
        created_by=None,
    ) -> list[dict]:
        item_id = int(item_id)
        model_id = int(model_cad_document_id)
        selected_ids = sorted({int(value) for value in (drawing_ids or [])})
        primary_id = (
            int(primary_drawing_id)
            if primary_drawing_id not in (None, "", 0, "0") else None
        )
        if primary_id is not None and primary_id not in selected_ids:
            raise ValueError("The primary drawing must be one of the selected drawings.")
        with self.get_conn() as conn:
            model = conn.execute(
                """
                SELECT id,project_id,category,checked_out_by
                FROM cad_documents WHERE id=?
                """,
                (model_id,),
            ).fetchone()
            if not model or str(model["category"] or "").upper() not in {
                "ASSEMBLY", "COMPONENT"
            }:
                raise ValueError("Select a managed PRT or ASM CAD Document.")
            if model["checked_out_by"] is not None:
                raise ValueError(
                    "Check in the model before changing its Item drawing selections."
                )
            model_association = conn.execute(
                """
                SELECT 1 FROM cad_item_associations
                WHERE item_id=? AND cad_document_id=? AND active=1
                LIMIT 1
                """,
                (item_id, model_id),
            ).fetchone()
            if not model_association:
                raise ValueError("Associate the model with this Item first.")
            related = {
                int(row["id"]): row
                for row in conn.execute(
                    """
                    SELECT id,checked_out_by FROM cad_documents
                    WHERE drawing_owner_cad_document_id=?
                      AND upper(category)='DRAWING'
                    """,
                    (model_id,),
                ).fetchall()
            }
            invalid = [value for value in selected_ids if value not in related]
            if invalid:
                raise ValueError(
                    "Drawing(s) are not related to the selected model: "
                    + ", ".join(str(value) for value in invalid)
                )
            current = {
                int(row["cad_document_id"]): row
                for row in conn.execute(
                    """
                    SELECT a.*,d.checked_out_by
                    FROM cad_item_associations a
                    JOIN cad_documents d ON d.id=a.cad_document_id
                    WHERE a.item_id=? AND a.active=1
                      AND upper(d.category)='DRAWING'
                      AND d.drawing_owner_cad_document_id=?
                    """,
                    (item_id, model_id),
                ).fetchall()
            }
            changing_ids = set(current).union(selected_ids)
            checked_out = []
            for drawing_id in changing_ids:
                row = current.get(drawing_id) or related.get(drawing_id)
                if row is not None and row["checked_out_by"] is not None:
                    checked_out.append(drawing_id)
            if checked_out:
                raise ValueError(
                    "Check in the selected/previous drawing CAD Document(s) before changing associations."
                )
            for drawing_id, association in current.items():
                if drawing_id not in selected_ids:
                    conn.execute(
                        """
                        UPDATE cad_item_associations
                        SET active=0,is_primary_drawing=0,
                            modified_at=datetime('now')
                        WHERE id=?
                        """,
                        (int(association["id"]),),
                    )
            conn.execute(
                """
                UPDATE cad_item_associations
                SET is_primary_drawing=0,modified_at=datetime('now')
                WHERE item_id=? AND active=1
                  AND drawing_model_cad_document_id=?
                """,
                (item_id, model_id),
            )
            for drawing_id in selected_ids:
                association = current.get(drawing_id)
                if association is not None:
                    conn.execute(
                        """
                        UPDATE cad_item_associations
                        SET drawing_model_cad_document_id=?,
                            is_primary_drawing=0,modified_at=datetime('now')
                        WHERE id=?
                        """,
                        (model_id, int(association["id"])),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO cad_item_associations(
                        project_id,item_id,cad_document_id,association_type,
                        drives_structure,drives_attributes,
                        participates_in_structure,active,created_by,
                        is_primary_drawing,drawing_model_cad_document_id
                    ) VALUES(?,?,?,'CONTENT',0,0,0,1,?,0,?)
                    """,
                    (
                        int(model["project_id"]), item_id, drawing_id,
                        int(created_by) if created_by else None, model_id,
                    ),
                )
            if primary_id is not None:
                conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET is_primary_drawing=1,modified_at=datetime('now')
                    WHERE item_id=? AND cad_document_id=? AND active=1
                    """,
                    (item_id, primary_id),
                )
        return self.list_item_selected_drawings(item_id, model_id)

    def set_primary_drawing(
        self, item_id: int, model_cad_document_id: int,
        drawing_cad_document_id: int, created_by=None,
    ) -> dict:
        selected_ids = [
            int(row["id"])
            for row in self.list_item_selected_drawings(
                int(item_id), int(model_cad_document_id)
            )
        ]
        drawing_id = int(drawing_cad_document_id)
        if drawing_id not in selected_ids:
            selected_ids.append(drawing_id)
        rows = self.set_item_model_drawings(
            int(item_id), int(model_cad_document_id), selected_ids,
            primary_drawing_id=drawing_id, created_by=created_by,
        )
        return next(row for row in rows if int(row["id"]) == drawing_id)

    def clear_primary_drawing(
        self, item_id: int, model_cad_document_id: int
    ) -> bool:
        selected_ids = [
            int(row["id"])
            for row in self.list_item_selected_drawings(
                int(item_id), int(model_cad_document_id)
            )
        ]
        self.set_item_model_drawings(
            int(item_id), int(model_cad_document_id), selected_ids,
            primary_drawing_id=None,
        )
        return True

    def remove_association(self, association_id: int) -> bool:
        with self.get_conn() as conn:
            association = conn.execute(
                """
                SELECT a.id,a.item_id,a.cad_document_id,
                       d.checked_out_by,d.category
                FROM cad_item_associations a
                JOIN cad_documents d ON d.id=a.cad_document_id
                WHERE a.id=? AND a.active=1
                """,
                (int(association_id),),
            ).fetchone()
            if not association:
                return False
            if association["checked_out_by"] is not None:
                raise ValueError("Check in the CAD Document before removing its Item association.")
            if str(association["category"] or "").upper() in {
                "ASSEMBLY", "COMPONENT"
            }:
                checked_out_drawing = conn.execute(
                    """
                    SELECT d.file_name
                    FROM cad_item_associations drawing_assoc
                    JOIN cad_documents d ON d.id=drawing_assoc.cad_document_id
                    WHERE drawing_assoc.item_id=? AND drawing_assoc.active=1
                      AND upper(d.category)='DRAWING'
                      AND d.drawing_owner_cad_document_id=?
                      AND d.checked_out_by IS NOT NULL
                    LIMIT 1
                    """,
                    (
                        int(association["item_id"]),
                        int(association["cad_document_id"]),
                    ),
                ).fetchone()
                if checked_out_drawing:
                    raise ValueError(
                        "Check in the selected drawing before removing the model association."
                    )
            cur = conn.execute(
                """
                UPDATE cad_item_associations
                SET active=0,is_primary_drawing=0,modified_at=datetime('now')
                WHERE id=? AND active=1
                """,
                (int(association_id),),
            )
            if (
                cur.rowcount
                and str(association["category"] or "").upper()
                in {"ASSEMBLY", "COMPONENT"}
            ):
                conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET active=0,is_primary_drawing=0,
                        modified_at=datetime('now')
                    WHERE item_id=? AND active=1 AND cad_document_id IN (
                        SELECT id FROM cad_documents
                        WHERE drawing_owner_cad_document_id=?
                          AND upper(category)='DRAWING'
                    )
                    """,
                    (
                        int(association["item_id"]),
                        int(association["cad_document_id"]),
                    ),
                )
            return bool(cur.rowcount)

    def add_cad_member(
        self,
        parent_cad_document_id: int,
        child_cad_document_id: int,
        quantity: int = 1,
        *,
        sort_order: int = 0,
        reference_designator: str = "",
        component_path: str = "",
        build_excluded: bool = False,
        legacy_usage_id=None,
    ) -> int:
        if int(parent_cad_document_id) == int(child_cad_document_id):
            raise ValueError("A CAD Document cannot contain itself.")
        with self.get_conn() as conn:
            categories = {
                int(row["id"]): str(row["category"] or "").upper()
                for row in conn.execute(
                    "SELECT id,category FROM cad_documents WHERE id IN (?,?)",
                    (int(parent_cad_document_id), int(child_cad_document_id)),
                ).fetchall()
            }
            if categories.get(int(parent_cad_document_id)) != "ASSEMBLY":
                raise ValueError("Only an ASM CAD Document can contain CAD members.")
            if categories.get(int(child_cad_document_id)) not in {
                "ASSEMBLY", "COMPONENT"
            }:
                raise ValueError(
                    "CAD Structure members must be PRT or ASM models; drawings are related files."
                )
            existing = conn.execute(
                """
                SELECT id,quantity FROM cad_document_members
                WHERE parent_cad_document_id=? AND child_cad_document_id=?
                """,
                (int(parent_cad_document_id), int(child_cad_document_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE cad_document_members
                    SET quantity=?,sort_order=?,reference_designator=?,
                        component_path=?,build_excluded=?
                    WHERE id=?
                    """,
                    (
                        max(1, int(quantity)), int(sort_order),
                        str(reference_designator or "") or None,
                        str(component_path or "") or None,
                        int(bool(build_excluded)), int(existing["id"]),
                    ),
                )
                return int(existing["id"])
            cur = conn.execute(
                """
                INSERT INTO cad_document_members(
                    parent_cad_document_id,child_cad_document_id,quantity,
                    sort_order,reference_designator,component_path,
                    build_excluded,legacy_usage_id
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    int(parent_cad_document_id), int(child_cad_document_id),
                    max(1, int(quantity)), int(sort_order),
                    str(reference_designator or "") or None,
                    str(component_path or "") or None,
                    int(bool(build_excluded)),
                    int(legacy_usage_id) if legacy_usage_id else None,
                ),
            )
            return int(cur.lastrowid)

    def apply_item_usage_relations(
        self, project_id: int, target_parent_id: int, selections, mode: str
    ) -> dict:
        """Atomically copy or move persisted EBOM usages to a target Item."""
        action = str(mode or "").strip().lower()
        if action not in {"copy", "move"}:
            raise ValueError("Relation action must be Copy or Move.")

        normalized = []
        seen = set()
        for selection in selections or []:
            child_id = int(selection.get("child_id"))
            source_value = selection.get("source_parent_id")
            source_parent_id = int(source_value) if source_value is not None else None
            key = (child_id, source_parent_id)
            if key in seen:
                continue
            seen.add(key)
            normalized.append((child_id, source_parent_id))
        if not normalized:
            raise ValueError("Select at least one child usage.")

        affected_sources = set()
        changed_children = set()
        skipped = []
        with self.get_conn() as conn:
            usage_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(item_usages)").fetchall()
            }
            for child_id, source_parent_id in normalized:
                if action == "move" and source_parent_id == int(target_parent_id):
                    skipped.append(child_id)
                    continue

                quantity = 1
                source_type = "MANUAL"
                build_status = "EXCLUDED"
                cad_member_id = None
                if source_parent_id is not None:
                    source = conn.execute(
                        """
                        SELECT *
                        FROM item_usages
                        WHERE project_id=? AND parent_item_id=? AND child_item_id=?
                        """,
                        (int(project_id), int(source_parent_id), int(child_id)),
                    ).fetchone()
                    if not source:
                        raise ValueError(
                            f"The selected source usage for item {child_id} no longer exists."
                        )
                    quantity = max(1, int(source["quantity"] or 1))
                    # A user-driven EBOM move/copy is an Item Structure edit.
                    # Do not duplicate the CAD-built occurrence ownership:
                    # item_usages.cad_member_id is intentionally unique for
                    # CAD_BUILD rows, so copied/moved usages must be manual.
                    source_type = "MANUAL"
                    build_status = "EXCLUDED"
                    cad_member_id = None

                target = conn.execute(
                    """
                    SELECT id, quantity
                    FROM item_usages
                    WHERE project_id=? AND parent_item_id=? AND child_item_id=?
                    """,
                    (int(project_id), int(target_parent_id), int(child_id)),
                ).fetchone()
                if target:
                    conn.execute(
                        """
                        UPDATE item_usages
                        SET quantity=?,modified_at=datetime('now')
                        WHERE id=?
                        """,
                        (int(target["quantity"] or 0) + quantity, int(target["id"])),
                    )
                else:
                    order = conn.execute(
                        """
                        SELECT COALESCE(MAX(sort_order),0)+10
                        FROM item_usages
                        WHERE project_id=? AND parent_item_id=?
                        """,
                        (int(project_id), int(target_parent_id)),
                    ).fetchone()[0]
                    insert_columns = [
                        "project_id", "parent_item_id", "child_item_id",
                        "quantity", "sort_order",
                    ]
                    insert_values = [
                        int(project_id), int(target_parent_id), int(child_id),
                        quantity, int(order or 10),
                    ]
                    if "source" in usage_columns:
                        insert_columns.append("source")
                        insert_values.append(source_type)
                    if "cad_member_id" in usage_columns:
                        insert_columns.append("cad_member_id")
                        insert_values.append(int(cad_member_id) if cad_member_id else None)
                    if "build_status" in usage_columns:
                        insert_columns.append("build_status")
                        insert_values.append(build_status)
                    placeholders = ",".join("?" for _ in insert_columns)
                    conn.execute(
                        f"""
                        INSERT INTO item_usages({','.join(insert_columns)})
                        VALUES({placeholders})
                        """,
                        insert_values,
                    )

                if action == "move" and source_parent_id is not None:
                    conn.execute(
                        """
                        DELETE FROM item_usages
                        WHERE project_id=? AND parent_item_id=? AND child_item_id=?
                        """,
                        (int(project_id), int(source_parent_id), int(child_id)),
                    )
                    affected_sources.add(int(source_parent_id))
                changed_children.add(int(child_id))

        return {
            "mode": action,
            "target_parent_id": int(target_parent_id),
            "child_ids": sorted(changed_children),
            "source_parent_ids": sorted(affected_sources),
            "skipped_child_ids": skipped,
            "had_root_sources": any(source is None for _child, source in normalized),
        }

    def remove_item_usages_from_parent(
        self, project_id: int, parent_item_id: int, child_item_ids
    ) -> dict:
        child_ids = sorted({int(value) for value in (child_item_ids or [])})
        if not child_ids:
            return {"removed_child_ids": [], "moved_to_root_ids": []}
        placeholders = ",".join("?" for _ in child_ids)
        moved_to_root = []
        with self.get_conn() as conn:
            conn.execute(
                f"""
                DELETE FROM item_usages
                WHERE project_id=? AND parent_item_id=? AND child_item_id IN ({placeholders})
                """,
                [int(project_id), int(parent_item_id), *child_ids],
            )
            rows = conn.execute(
                f"""
                SELECT b.id
                FROM bom b
                WHERE b.project_id=? AND b.id IN ({placeholders})
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                  AND NOT EXISTS (
                      SELECT 1 FROM item_usages u
                      WHERE u.project_id=b.project_id AND u.child_item_id=b.id
                  )
                """,
                [int(project_id), *child_ids],
            ).fetchall()
            moved_to_root = [int(row["id"]) for row in rows]
        return {
            "removed_child_ids": child_ids,
            "moved_to_root_ids": moved_to_root,
        }

    def delete_item_pdm_links(self, item_id: int) -> dict:
        """Remove PDM-side links owned by an Item before deleting the Item master."""
        item_id = int(item_id)
        with self.get_conn() as conn:
            counts = {}
            cur = conn.execute(
                "DELETE FROM cad_item_associations WHERE item_id=?",
                (item_id,),
            )
            counts["cad_item_associations"] = int(cur.rowcount or 0)
            try:
                cur = conn.execute(
                    "DELETE FROM cad_document_checkout_items WHERE item_id=?",
                    (item_id,),
                )
                counts["cad_document_checkout_items"] = int(cur.rowcount or 0)
            except Exception:
                counts["cad_document_checkout_items"] = 0
            try:
                cur = conn.execute(
                    """
                    DELETE FROM item_occurrences
                    WHERE parent_item_id=? OR child_item_id=?
                    """,
                    (item_id, item_id),
                )
                counts["item_occurrences_direct"] = int(cur.rowcount or 0)
            except Exception:
                counts["item_occurrences_direct"] = 0
            try:
                cur = conn.execute(
                    """
                    DELETE FROM item_occurrences
                    WHERE item_usage_id IN (
                        SELECT id FROM item_usages
                        WHERE parent_item_id=? OR child_item_id=?
                    )
                    """,
                    (item_id, item_id),
                )
                counts["item_occurrences_by_usage"] = int(cur.rowcount or 0)
            except Exception:
                counts["item_occurrences_by_usage"] = 0
            try:
                cur = conn.execute(
                    """
                    DELETE FROM item_usages
                    WHERE parent_item_id=? OR child_item_id=?
                    """,
                    (item_id, item_id),
                )
                counts["item_usages"] = int(cur.rowcount or 0)
            except Exception:
                counts["item_usages"] = 0
            return counts

    def cleanup_orphan_item_associations(self) -> int:
        """Deactivate active CAD associations whose Item master no longer exists."""
        with self.get_conn() as conn:
            try:
                cur = conn.execute(
                    """
                    UPDATE cad_item_associations
                    SET active=0,modified_at=datetime('now')
                    WHERE active=1
                      AND item_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM bom b WHERE b.id=cad_item_associations.item_id
                            AND b.deleted_at IS NULL
                            AND lower(COALESCE(b.status,''))<>'deleted'
                            AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                      )
                    """
                )
                return int(cur.rowcount or 0)
            except Exception:
                return 0

    def list_cad_members(self, parent_cad_document_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT m.*,d.id AS cad_document_id,d.number,d.name,
                       d.file_name,d.category,
                       d.build_excluded AS document_build_excluded
                FROM cad_document_members m
                JOIN cad_documents d ON d.id=m.child_cad_document_id
                WHERE m.parent_cad_document_id=?
                ORDER BY COALESCE(m.sort_order,m.id),m.id
                """,
                (int(parent_cad_document_id),),
            ).fetchall()
            records = [dict(row) for row in rows]
            document_ids = sorted({
                int(record["child_cad_document_id"]) for record in records
            })
            if not document_ids:
                return records
            placeholders = ",".join("?" for _ in document_ids)
            associations = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT a.*,a.id AS association_id,
                           b.part_number AS item_number,
                           b.aes_number AS item_aes_number,b.name AS item_name
                    FROM cad_item_associations a
                    JOIN bom b ON b.id=a.item_id
                    WHERE a.active=1
                      AND b.deleted_at IS NULL
                      AND lower(COALESCE(b.status,''))<>'deleted'
                      AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                      AND a.cad_document_id IN ({placeholders})
                    ORDER BY a.cad_document_id,
                             CASE upper(a.association_type)
                                WHEN 'OWNER' THEN 0
                                WHEN 'CONTRIBUTING_IMAGE' THEN 1
                                WHEN 'IMAGE' THEN 2
                                WHEN 'CONTRIBUTING_CONTENT' THEN 3
                                WHEN 'CONTENT' THEN 4 ELSE 9 END,a.id
                    """,
                    document_ids,
                ).fetchall()
            ]
            by_document = defaultdict(list)
            for association in associations:
                by_document[int(association["cad_document_id"])].append(
                    association
                )
            legacy_fields = (
                "association_id", "item_id", "association_type",
                "participates_in_structure", "drives_structure",
                "drives_attributes", "item_number", "item_aes_number",
                "item_name",
            )
            for record in records:
                related = list(
                    by_document.get(int(record["child_cad_document_id"]), [])
                )
                owners = [
                    row for row in related
                    if str(row.get("association_type") or "").upper() == "OWNER"
                ]
                participating = [
                    row for row in related
                    if bool(row.get("participates_in_structure"))
                ]
                ambiguous = not owners and len(participating) > 1
                if owners:
                    selected = owners[0]
                elif len(participating) == 1:
                    selected = participating[0]
                elif not participating and related:
                    # Preserve NOT_PARTICIPATING diagnostics for CONTENT-only
                    # CAD rather than incorrectly reporting it unassociated.
                    selected = related[0]
                else:
                    selected = {}
                record["associations"] = related
                record["association_count"] = len(related)
                record["association_ambiguous"] = bool(ambiguous)
                for field in legacy_fields:
                    record[field] = selected.get(field)
            return records

    def ordered_cad_member_ids(self, parent_cad_document_id: int) -> list[int]:
        with self.get_conn() as conn:
            return [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM cad_document_members
                    WHERE parent_cad_document_id=?
                    ORDER BY COALESCE(sort_order,id),id
                    """,
                    (int(parent_cad_document_id),),
                ).fetchall()
            ]

    def set_cad_member_order(self, parent_cad_document_id: int, ordered_member_ids) -> bool:
        ordered = []
        seen = set()
        for value in ordered_member_ids or []:
            try:
                member_id = int(value)
            except Exception:
                continue
            if member_id in seen:
                continue
            ordered.append(member_id)
            seen.add(member_id)
        if not ordered:
            return False
        with self.get_conn() as conn:
            existing = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM cad_document_members
                    WHERE parent_cad_document_id=?
                    ORDER BY COALESCE(sort_order,id),id
                    """,
                    (int(parent_cad_document_id),),
                ).fetchall()
            ]
            existing_set = set(existing)
            final_order = [member_id for member_id in ordered if member_id in existing_set]
            final_order.extend(member_id for member_id in existing if member_id not in set(final_order))
            for index, member_id in enumerate(final_order):
                conn.execute(
                    """
                    UPDATE cad_document_members
                    SET sort_order=?
                    WHERE id=? AND parent_cad_document_id=?
                    """,
                    ((index + 1) * 10, int(member_id), int(parent_cad_document_id)),
                )
            return True

    def get_cad_member(self, member_id: int) -> Optional[dict]:
        with self.get_conn() as conn:
            return self._dict(conn.execute(
                "SELECT * FROM cad_document_members WHERE id=?",
                (int(member_id),),
            ).fetchone())

    def remove_cad_member(self, member_id: int) -> bool:
        with self.get_conn() as conn:
            member_id = int(member_id)
            # CAD members can drive generated EBOM usages, item occurrences,
            # and historical build-result rows.  With FK enforcement enabled
            # those dependents must be removed/detached before the occurrence
            # itself is deleted.
            try:
                usage_ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id FROM item_usages WHERE cad_member_id=?",
                        (member_id,),
                    ).fetchall()
                ]
                if usage_ids:
                    placeholders = ",".join("?" for _ in usage_ids)
                    conn.execute(
                        f"DELETE FROM item_occurrences WHERE item_usage_id IN ({placeholders})",
                        usage_ids,
                    )
                    conn.execute(
                        f"DELETE FROM item_usages WHERE id IN ({placeholders}) AND upper(COALESCE(source,''))='CAD_BUILD'",
                        usage_ids,
                    )
                    conn.execute(
                        "UPDATE item_usages SET cad_member_id=NULL,modified_at=datetime('now') WHERE cad_member_id=?",
                        (member_id,),
                    )
            except Exception:
                pass
            try:
                conn.execute(
                    "DELETE FROM item_occurrences WHERE source_cad_member_id=?",
                    (member_id,),
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "DELETE FROM pdm_build_results WHERE cad_member_id=?",
                    (member_id,),
                )
            except Exception:
                pass
            cur = conn.execute(
                "DELETE FROM cad_document_members WHERE id=?", (member_id,)
            )
            return bool(cur.rowcount)

    def capture_item_structure_iteration(
        self, parent_item_id: int, source: str, *, created_by=None,
        build_run_id=None,
    ) -> dict:
        with self.get_conn() as conn:
            parent = conn.execute(
                """
                SELECT project_id,revision,current_iteration_id
                FROM bom WHERE id=?
                """,
                (int(parent_item_id),),
            ).fetchone()
            if not parent:
                raise ValueError("The parent Item was not found.")
            usages = [dict(row) for row in conn.execute(
                """
                SELECT * FROM item_usages WHERE parent_item_id=?
                ORDER BY COALESCE(sort_order,id),id
                """,
                (int(parent_item_id),),
            ).fetchall()]
            for usage in usages:
                usage["occurrences"] = [dict(row) for row in conn.execute(
                    "SELECT * FROM item_occurrences WHERE item_usage_id=? ORDER BY id",
                    (int(usage["id"]),),
                ).fetchall()]
            next_iteration = int(conn.execute(
                """
                SELECT COALESCE(MAX(structure_iteration),0)+1
                FROM item_structure_iterations WHERE parent_item_id=?
                """,
                (int(parent_item_id),),
            ).fetchone()[0])
            cur = conn.execute(
                """
                INSERT INTO item_structure_iterations(
                    project_id,parent_item_id,structure_iteration,item_revision,
                    item_iteration_id,source,build_run_id,structure_json,created_by
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(parent["project_id"] or 0), int(parent_item_id), next_iteration,
                    str(parent["revision"] or "A"), parent["current_iteration_id"],
                    str(source or "MANUAL").upper(),
                    int(build_run_id) if build_run_id else None,
                    json.dumps(usages, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    int(created_by) if created_by else None,
                ),
            )
            snapshot_id = int(cur.lastrowid)
        return {
            "id": snapshot_id, "parent_item_id": int(parent_item_id),
            "structure_iteration": next_iteration, "usages": usages,
        }

    def list_item_structure_iterations(self, parent_item_id: int) -> list[dict]:
        with self.get_conn() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT * FROM item_structure_iterations
                WHERE parent_item_id=? ORDER BY structure_iteration DESC
                """,
                (int(parent_item_id),),
            ).fetchall()]

    def list_item_usages(self, parent_item_id: int) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT u.*,b.name,b.aes_number,b.part_number,b.type,b.revision,
                       b.lifecycle_state
                FROM item_usages u JOIN bom b ON b.id=u.child_item_id
                WHERE u.parent_item_id=?
                  AND b.deleted_at IS NULL
                  AND lower(COALESCE(b.status,''))<>'deleted'
                  AND lower(COALESCE(b.lifecycle_state,''))<>'deleted'
                ORDER BY COALESCE(u.sort_order,u.id),u.id
                """,
                (int(parent_item_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def ordered_item_usage_ids(self, parent_item_id: int) -> list[int]:
        with self.get_conn() as conn:
            return [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM item_usages
                    WHERE parent_item_id=?
                    ORDER BY COALESCE(sort_order,id),id
                    """,
                    (int(parent_item_id),),
                ).fetchall()
            ]

    def set_item_usage_order(self, parent_item_id: int, ordered_usage_ids) -> bool:
        ordered = []
        seen = set()
        for value in ordered_usage_ids or []:
            try:
                usage_id = int(value)
            except Exception:
                continue
            if usage_id in seen:
                continue
            ordered.append(usage_id)
            seen.add(usage_id)
        if not ordered:
            return False
        with self.get_conn() as conn:
            existing = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM item_usages
                    WHERE parent_item_id=?
                    ORDER BY COALESCE(sort_order,id),id
                    """,
                    (int(parent_item_id),),
                ).fetchall()
            ]
            existing_set = set(existing)
            final_order = [usage_id for usage_id in ordered if usage_id in existing_set]
            final_order.extend(usage_id for usage_id in existing if usage_id not in set(final_order))
            for index, usage_id in enumerate(final_order):
                conn.execute(
                    """
                    UPDATE item_usages
                    SET sort_order=?,modified_at=datetime('now')
                    WHERE id=? AND parent_item_id=?
                    """,
                    ((index + 1) * 10, int(usage_id), int(parent_item_id)),
                )
            return True

    def add_manual_item_usage(
        self, project_id: int, parent_item_id: int, child_item_id: int,
        quantity: int = 1, created_by=None,
    ) -> int:
        if int(parent_item_id) == int(child_item_id):
            raise ValueError("An Item cannot contain itself.")
        with self.get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id,quantity FROM item_usages
                WHERE parent_item_id=? AND child_item_id=? AND source='MANUAL'
                """,
                (int(parent_item_id), int(child_item_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE item_usages SET quantity=?,modified_at=datetime('now') WHERE id=?",
                    (max(1, int(quantity)), int(existing["id"])),
                )
                return int(existing["id"])
            order = conn.execute(
                "SELECT COALESCE(MAX(sort_order),0)+10 FROM item_usages WHERE parent_item_id=?",
                (int(parent_item_id),),
            ).fetchone()[0]
            cur = conn.execute(
                """
                INSERT INTO item_usages(
                    project_id,parent_item_id,child_item_id,quantity,sort_order,
                    source,build_status,created_by
                ) VALUES(?,?,?,?,?,'MANUAL','EXCLUDED',?)
                """,
                (
                    int(project_id), int(parent_item_id), int(child_item_id),
                    max(1, int(quantity)), int(order or 10),
                    int(created_by) if created_by else None,
                ),
            )
            return int(cur.lastrowid)
