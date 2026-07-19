"""Requirement-aware release validation for immutable BOM iterations."""

import json
import sqlite3

from config import DB_NAME
from core.ebom_policy import (
    normalize_classification,
    normalize_default_behavior,
    normalize_requirement,
    requires_aes_number,
)
from core.repositories.bom_revision_repository import BomRevisionRepository


class ReleaseValidationService:
    MAX_DEPTH = 100

    def __init__(self, db_name=DB_NAME, revision_repo=None):
        self.db_name = db_name
        self.revision_repo = revision_repo or BomRevisionRepository(db_name)

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _snapshot(raw_value) -> dict:
        try:
            value = json.loads(str(raw_value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _label(row, snapshot) -> str:
        return str(
            snapshot.get("part_number")
            or snapshot.get("aes_number")
            or snapshot.get("name")
            or row["bom_id"]
        )

    def validate_iteration(
        self, iteration_id: int, include_children: bool = True
    ) -> list[dict]:
        findings = []
        with self.get_conn() as conn:
            cache = {}
            tables = {
                str(value[0]) for value in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            has_pdm = {
                "cad_documents", "cad_item_associations", "item_usages"
            }.issubset(tables)
            cad_columns = (
                {
                    str(value[1])
                    for value in conn.execute(
                        "PRAGMA table_info(cad_documents)"
                    ).fetchall()
                }
                if "cad_documents" in tables else set()
            )
            has_drawing_model_link = (
                "drawing_owner_cad_document_id" in cad_columns
            )

            def context(selected_iteration_id: int):
                selected_iteration_id = int(selected_iteration_id)
                if selected_iteration_id not in cache:
                    row = conn.execute(
                        """
                        SELECT i.id AS iteration_id, i.revision_id,
                               i.iteration_number, i.object_data_json,
                               r.bom_id, r.revision_code
                        FROM bom_iterations i
                        JOIN bom_revisions r ON r.id=i.revision_id
                        WHERE i.id=?
                        """,
                        (selected_iteration_id,),
                    ).fetchone()
                    if not row:
                        raise ValueError(
                            f"BOM iteration {selected_iteration_id} was not found."
                        )
                    cache[selected_iteration_id] = (
                        dict(row), self._snapshot(row["object_data_json"])
                    )
                row, snapshot = cache[selected_iteration_id]
                return dict(row), dict(snapshot)

            def walk(selected_iteration_id: int, ancestors: tuple[int, ...]):
                selected_iteration_id = int(selected_iteration_id)
                if selected_iteration_id in ancestors:
                    labels = []
                    for ancestor in (*ancestors, selected_iteration_id):
                        ancestor_row, ancestor_snapshot = context(ancestor)
                        labels.append(self._label(ancestor_row, ancestor_snapshot))
                    raise ValueError(
                        "Circular CAD structure detected during release validation: "
                        + " -> ".join(labels)
                    )
                if len(ancestors) >= self.MAX_DEPTH:
                    raise ValueError(
                        f"CAD structure exceeds {self.MAX_DEPTH} levels during release validation."
                    )

                row, snapshot = context(selected_iteration_id)
                classification = normalize_classification(
                    snapshot.get("classification")
                )
                cad_requirement = normalize_requirement(
                    snapshot.get("cad_requirement"), "CAD requirement"
                )
                drawing_requirement = normalize_requirement(
                    snapshot.get("drawing_requirement"), "drawing requirement"
                )
                label = self._label(row, snapshot)
                version = f"{row['revision_code']}.{int(row['iteration_number'])}"
                item_number = str(snapshot.get("part_number") or "").strip()
                if not item_number:
                    findings.append({
                        "bom_id": int(row["bom_id"]),
                        "iteration_id": selected_iteration_id,
                        "version": version,
                        "classification": classification,
                        "requirement": "part_number",
                        "message": f"{label} {version} has no Item Number.",
                    })
                else:
                    duplicate = conn.execute(
                        """
                        SELECT other.id
                        FROM bom current
                        JOIN bom other ON other.project_id=current.project_id
                          AND other.id<>current.id
                          AND lower(trim(other.part_number))=lower(?)
                          AND other.represented_part_id IS NULL
                        WHERE current.id=?
                        LIMIT 1
                        """,
                        (item_number, int(row["bom_id"])),
                    ).fetchone()
                    if duplicate:
                        findings.append({
                            "bom_id": int(row["bom_id"]),
                            "iteration_id": selected_iteration_id,
                            "version": version,
                            "classification": classification,
                            "requirement": "part_number_unique",
                            "message": (
                                f"Item Number {item_number} is also assigned to Item "
                                f"{int(duplicate['id'])}."
                            ),
                        })
                default_behavior = normalize_default_behavior(
                    snapshot.get("default_ebom_behavior")
                )
                if requires_aes_number(
                    default_behavior, snapshot.get("represented_part_id")
                ) and not str(snapshot.get("aes_number") or "").strip():
                    findings.append({
                        "bom_id": int(row["bom_id"]),
                        "iteration_id": selected_iteration_id,
                        "version": version,
                        "classification": classification,
                        "requirement": "aes_number",
                        "message": f"{label} {version} is deliverable and requires an AES Number.",
                    })
                pdm_cad = None
                pdm_drawing = None
                if has_pdm:
                    pdm_cad = conn.execute(
                        """
                        SELECT 1 FROM cad_item_associations a
                        JOIN cad_documents d ON d.id=a.cad_document_id
                        WHERE a.item_id=? AND a.active=1
                          AND upper(d.category) IN ('ASSEMBLY','COMPONENT')
                        LIMIT 1
                        """,
                        (int(row["bom_id"]),),
                    ).fetchone()
                    if has_drawing_model_link:
                        pdm_drawing = conn.execute(
                            """
                            SELECT 1
                            FROM cad_item_associations model_assoc
                            JOIN cad_documents model
                              ON model.id=model_assoc.cad_document_id
                            JOIN cad_documents drawing
                              ON drawing.drawing_owner_cad_document_id=model.id
                             AND upper(drawing.category)='DRAWING'
                            WHERE model_assoc.item_id=?
                              AND model_assoc.active=1
                              AND upper(model.category) IN ('ASSEMBLY','COMPONENT')
                            LIMIT 1
                            """,
                            (int(row["bom_id"]),),
                        ).fetchone()
                    else:
                        pdm_drawing = conn.execute(
                            """
                            SELECT 1 FROM cad_item_associations a
                            JOIN cad_documents d ON d.id=a.cad_document_id
                            WHERE a.item_id=? AND a.active=1
                              AND upper(d.category)='DRAWING'
                            LIMIT 1
                            """,
                            (int(row["bom_id"]),),
                        ).fetchone()
                if cad_requirement == "REQUIRED" and not str(
                    snapshot.get("filename") or ""
                ).strip() and not pdm_cad:
                    findings.append({
                        "bom_id": int(row["bom_id"]),
                        "iteration_id": selected_iteration_id,
                        "version": version,
                        "classification": classification,
                        "requirement": "cad_requirement",
                        "message": f"{label} {version} requires native CAD.",
                    })
                drawing_is_available = (
                    bool(pdm_drawing)
                    if pdm_cad else bool(str(snapshot.get("drawing") or "").strip())
                )
                if drawing_requirement == "REQUIRED" and not drawing_is_available:
                    findings.append({
                        "bom_id": int(row["bom_id"]),
                        "iteration_id": selected_iteration_id,
                        "version": version,
                        "classification": classification,
                        "requirement": "drawing_requirement",
                        "message": f"{label} {version} requires a drawing.",
                    })

                if not include_children:
                    return
                next_ancestors = (*ancestors, selected_iteration_id)
                bindings = []
                if has_pdm:
                    bindings = conn.execute(
                        """
                        SELECT u.child_item_id AS child_bom_id,
                               b.current_revision_id AS child_revision_id,
                               b.current_iteration_id AS child_iteration_id
                        FROM item_usages u JOIN bom b ON b.id=u.child_item_id
                        WHERE u.parent_item_id=?
                        ORDER BY COALESCE(u.sort_order,u.id),u.id
                        """,
                        (int(row["bom_id"]),),
                    ).fetchall()
                    bindings = [
                        binding for binding in bindings
                        if binding["child_revision_id"] is not None
                        and binding["child_iteration_id"] is not None
                    ]
                if not bindings:
                    bindings = conn.execute(
                        """
                        SELECT child_bom_id, child_revision_id, child_iteration_id
                        FROM bom_iteration_bindings
                        WHERE parent_iteration_id=? ORDER BY sort_order, id
                        """,
                        (selected_iteration_id,),
                    ).fetchall()
                for binding in bindings:
                    child_row, _snapshot = context(int(binding["child_iteration_id"]))
                    if int(child_row["bom_id"]) != int(binding["child_bom_id"]):
                        raise ValueError(
                            "An immutable release binding points to the wrong BOM object."
                        )
                    if int(child_row["revision_id"]) != int(binding["child_revision_id"]):
                        raise ValueError(
                            "An immutable release binding points to the wrong BOM revision."
                        )
                    walk(int(binding["child_iteration_id"]), next_ancestors)

            walk(int(iteration_id), ())
        return findings

    def validate_bom(self, bom_id: int, include_children: bool = True) -> list[dict]:
        context = self.revision_repo.get_current_context(int(bom_id))
        return self.validate_iteration(
            int(context["current_iteration_id"]), include_children=include_children
        )

    def assert_bom_releasable(
        self, bom_id: int, include_children: bool = True
    ) -> None:
        findings = self.validate_bom(int(bom_id), include_children=include_children)
        if findings:
            messages = "; ".join(row["message"] for row in findings[:5])
            suffix = "" if len(findings) <= 5 else f"; and {len(findings) - 5} more"
            raise ValueError(f"Release requirements are not satisfied: {messages}{suffix}")
