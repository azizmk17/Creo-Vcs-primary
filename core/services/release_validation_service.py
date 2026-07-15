"""Requirement-aware release validation for immutable BOM iterations."""

import json
import sqlite3

from config import DB_NAME
from core.ebom_policy import (
    normalize_classification,
    normalize_requirement,
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
            snapshot.get("aes_number")
            or snapshot.get("name")
            or row["bom_id"]
        )

    def validate_iteration(
        self, iteration_id: int, include_children: bool = True
    ) -> list[dict]:
        findings = []
        with self.get_conn() as conn:
            cache = {}

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
                if cad_requirement == "REQUIRED" and not str(
                    snapshot.get("filename") or ""
                ).strip():
                    findings.append({
                        "bom_id": int(row["bom_id"]),
                        "iteration_id": selected_iteration_id,
                        "version": version,
                        "classification": classification,
                        "requirement": "cad_requirement",
                        "message": f"{label} {version} requires native CAD.",
                    })
                if drawing_requirement == "REQUIRED" and not str(
                    snapshot.get("drawing") or ""
                ).strip():
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
