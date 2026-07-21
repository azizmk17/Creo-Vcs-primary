import json
import os
import csv
from collections import defaultdict

from config import DB_NAME
from core.repositories.pdm_repository import ASSOCIATION_RULES, PdmRepository


class PdmBuildError(ValueError):
    pass


class PdmService:
    """Windchill-like CAD/Item association and structure build service."""

    MAX_DEPTH = 100

    def __init__(self, db_name: str = DB_NAME, repository=None):
        self.db_name = db_name
        self.repo = repository or PdmRepository(db_name)

    def list_cad_documents(self, project_id: int) -> list[dict]:
        return self.repo.list_cad_documents(int(project_id))

    def list_item_cad_documents(self, item_id: int) -> list[dict]:
        return self.repo.list_item_cad_documents(int(item_id))

    def list_cad_item_associations(self, cad_document_id: int) -> list[dict]:
        """Return every active Item relationship for one CAD Document."""
        return self.repo.list_active_associations_for_cad(int(cad_document_id))

    def get_item_cad_association(
        self, item_id: int, cad_document_id: int
    ) -> dict | None:
        """Return the exact Item/CAD relationship, never an arbitrary match."""
        return self.repo.get_active_association(
            int(cad_document_id), int(item_id)
        )

    def create_cad_document(self, project_id: int, **values) -> int:
        return self.repo.create_cad_document(int(project_id), **values)

    def delete_cad_document(
        self, cad_document_id: int, *, delete_related_drawings: bool = False
    ) -> dict:
        return self.repo.delete_cad_document(
            int(cad_document_id),
            delete_related_drawings=bool(delete_related_drawings),
        )

    def bind_drawing_to_model(
        self, drawing_cad_document_id: int, model_cad_document_id: int
    ) -> dict:
        return self.repo.bind_drawing_to_model(
            int(drawing_cad_document_id), int(model_cad_document_id)
        )

    def list_item_selected_drawings(
        self, item_id: int, model_cad_document_id: int | None = None
    ) -> list[dict]:
        return self.repo.list_item_selected_drawings(
            int(item_id),
            (
                int(model_cad_document_id)
                if model_cad_document_id is not None else None
            ),
        )

    def set_primary_drawing(
        self,
        item_id: int,
        model_cad_document_id: int,
        drawing_cad_document_id: int,
        actor_id=None,
    ) -> dict:
        return self.repo.set_primary_drawing(
            int(item_id),
            int(model_cad_document_id),
            int(drawing_cad_document_id),
            created_by=(int(actor_id) if actor_id is not None else None),
        )

    def clear_primary_drawing(
        self, item_id: int, model_cad_document_id: int
    ) -> bool:
        return self.repo.clear_primary_drawing(
            int(item_id), int(model_cad_document_id)
        )

    def set_item_model_drawings(
        self,
        item_id: int,
        model_cad_document_id: int,
        drawing_ids,
        primary_drawing_id: int | None = None,
        actor_id=None,
    ) -> list[dict]:
        """Atomically replace one Item's selected drawings for a model."""
        normalized_ids = sorted({int(value) for value in (drawing_ids or [])})
        primary_id = (
            int(primary_drawing_id)
            if primary_drawing_id is not None else None
        )
        if primary_id is not None and primary_id not in normalized_ids:
            raise ValueError("The primary drawing must be included in the selected drawings.")
        return self.repo.set_item_model_drawings(
            int(item_id),
            int(model_cad_document_id),
            normalized_ids,
            primary_drawing_id=primary_id,
            created_by=(int(actor_id) if actor_id is not None else None),
        )

    def associate(
        self, project_id: int, item_id: int, cad_document_id: int,
        association_type: str, actor_id=None,
    ) -> dict:
        return self.repo.associate(
            int(project_id), int(item_id), int(cad_document_id),
            association_type, actor_id,
        )

    def remove_association(self, association_id: int) -> bool:
        return self.repo.remove_association(int(association_id))

    def auto_associate_candidates(self, project_id: int) -> list[dict]:
        """Return deterministic proposals; never silently creates Items."""
        documents = self.repo.list_cad_documents(int(project_id))
        with self.repo.get_conn() as conn:
            items = [dict(row) for row in conn.execute(
                """
                SELECT id,aes_number,part_number,name,base_file_name
                FROM bom WHERE project_id=? AND represented_part_id IS NULL
                ORDER BY id
                """,
                (int(project_id),),
            ).fetchall()]

        number_index = defaultdict(set)
        legacy_file_index = defaultdict(set)
        for item in items:
            number_key = self.repo.normalize_base(item.get("part_number"))
            if number_key:
                number_index[number_key].add(int(item["id"]))
            legacy_key = self.repo.normalize_base(item.get("base_file_name"))
            if legacy_key:
                legacy_file_index[legacy_key].add(int(item["id"]))
        by_id = {int(item["id"]): item for item in items}
        proposals = []
        for document in documents:
            if str(document.get("category") or "").upper() == "DRAWING":
                continue
            if document.get("association_id"):
                continue
            file_keys = {
                self.repo.normalize_base(document.get("file_name")),
                self.repo.normalize_base(document.get("base_file_name")),
            }
            matches = set()
            for key in file_keys:
                if key:
                    matches.update(number_index.get(key, set()))
            match_basis = "CAD_FILE_TO_ITEM_NUMBER" if matches else ""
            if not matches:
                for key in file_keys:
                    if key:
                        matches.update(legacy_file_index.get(key, set()))
                if matches:
                    match_basis = "CAD_FILE_NAME"
            if document.get("build_excluded"):
                status = "BUILD_EXCLUDED"
            elif len(matches) == 1:
                status = "MATCH"
            elif len(matches) > 1:
                status = "AMBIGUOUS"
            else:
                status = "NO_MATCH"
            match_rows = [by_id[item_id] for item_id in sorted(matches)]
            proposals.append({
                "cad_document": document,
                "status": status,
                "match_basis": match_basis,
                "matches": match_rows,
                "proposed_item_id": int(match_rows[0]["id"]) if len(match_rows) == 1 else None,
                "proposed_association_type": "OWNER" if len(match_rows) == 1 else None,
            })
        return proposals

    def apply_auto_associate(self, project_id: int, actor_id=None) -> dict:
        associated = []
        unresolved = []
        for proposal in self.auto_associate_candidates(int(project_id)):
            if proposal["status"] != "MATCH":
                unresolved.append(proposal)
                continue
            document = proposal["cad_document"]
            try:
                association = self.associate(
                    int(project_id), int(proposal["proposed_item_id"]),
                    int(document["id"]), "OWNER", actor_id,
                )
                associated.append(association)
            except ValueError as exc:
                proposal = dict(proposal)
                proposal["status"] = "CONFLICT"
                proposal["message"] = str(exc)
                unresolved.append(proposal)
        return {"associated": associated, "unresolved": unresolved}

    def _owner_association(self, conn, cad_document_id: int):
        return conn.execute(
            """
            SELECT * FROM cad_item_associations
            WHERE cad_document_id=? AND active=1 AND association_type='OWNER'
            """,
            (int(cad_document_id),),
        ).fetchone()

    def build_part_structure(
        self, root_cad_document_id: int, *, multi_level: bool = True,
        actor_id=None,
    ) -> dict:
        """Build CAD-owned Item usages while preserving every manual usage."""
        root = self.repo.get_cad_document(int(root_cad_document_id))
        if not root:
            raise PdmBuildError("The root CAD Document was not found.")
        project_id = int(root["project_id"])
        summary = {
            "created": 0, "updated": 0, "removed": 0,
            "excluded": 0, "no_related_item": 0, "conflicts": 0,
        }
        built_item_ids = set()
        with self.repo.get_conn() as conn:
            owner = self._owner_association(conn, int(root_cad_document_id))
            if not owner:
                raise PdmBuildError(
                    "Build requires an OWNER association on the root CAD Document."
                )
            run = conn.execute(
                """
                INSERT INTO pdm_build_runs(
                    project_id,root_cad_document_id,multi_level,created_by
                ) VALUES(?,?,?,?)
                """,
                (
                    project_id, int(root_cad_document_id), int(bool(multi_level)),
                    int(actor_id) if actor_id else None,
                ),
            )
            run_id = int(run.lastrowid)

            def result(member_id, parent_item_id, child_item_id, status, message):
                conn.execute(
                    """
                    INSERT INTO pdm_build_results(
                        build_run_id,cad_member_id,parent_item_id,child_item_id,
                        status,message
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (run_id, member_id, parent_item_id, child_item_id, status, message),
                )

            visited = set()

            def build(cad_document_id: int, depth: int, ancestors: tuple[int, ...]):
                if depth > self.MAX_DEPTH:
                    raise PdmBuildError("CAD structure exceeds the supported build depth.")
                if cad_document_id in ancestors:
                    raise PdmBuildError("Circular CAD structure detected during build.")
                if cad_document_id in visited:
                    return
                visited.add(cad_document_id)
                parent_assoc = self._owner_association(conn, cad_document_id)
                if not parent_assoc:
                    return
                parent_item_id = int(parent_assoc["item_id"])
                built_item_ids.add(parent_item_id)
                # The repository collapses the many Item associations of a CAD
                # Document into one deterministic structure-driving projection
                # while retaining the complete association list on each member.
                # A raw join here would duplicate one CAD occurrence for every
                # associated Item and could silently build the wrong EBOM usage.
                members = self.repo.list_cad_members(cad_document_id)
                member_ids = {int(row["id"]) for row in members}
                stale = conn.execute(
                    """
                    SELECT id,cad_member_id FROM item_usages
                    WHERE parent_item_id=? AND source='CAD_BUILD'
                    """,
                    (parent_item_id,),
                ).fetchall()
                for usage in stale:
                    if usage["cad_member_id"] is None or int(usage["cad_member_id"]) not in member_ids:
                        conn.execute("DELETE FROM item_occurrences WHERE item_usage_id=?", (int(usage["id"]),))
                        conn.execute("DELETE FROM item_usages WHERE id=?", (int(usage["id"]),))
                        summary["removed"] += 1

                for member in members:
                    member_id = int(member["id"])
                    child_cad_id = int(member["child_cad_document_id"])
                    if member.get("build_excluded") or member.get("document_build_excluded"):
                        conn.execute(
                            "DELETE FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                            (member_id,),
                        )
                        summary["excluded"] += 1
                        result(member_id, parent_item_id, None, "EXCLUDED", "CAD member is excluded from EBOM build.")
                    elif member.get("association_ambiguous"):
                        conn.execute(
                            "DELETE FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                            (member_id,),
                        )
                        summary["conflicts"] += 1
                        result(
                            member_id,
                            parent_item_id,
                            None,
                            "AMBIGUOUS_ITEM_ASSOCIATION",
                            "CAD Document has several structure-participating Item associations; assign an OWNER association to select the EBOM Item.",
                        )
                    elif not member.get("item_id"):
                        conn.execute(
                            "DELETE FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                            (member_id,),
                        )
                        summary["no_related_item"] += 1
                        result(member_id, parent_item_id, None, "NO_RELATED_ITEM", "CAD Document has no associated Item.")
                    elif not member.get("participates_in_structure"):
                        conn.execute(
                            "DELETE FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                            (member_id,),
                        )
                        summary["excluded"] += 1
                        result(member_id, parent_item_id, int(member["item_id"]), "NOT_PARTICIPATING", "Association does not participate in Item structure.")
                    else:
                        child_item_id = int(member["item_id"])
                        if child_item_id == parent_item_id:
                            summary["conflicts"] += 1
                            result(member_id, parent_item_id, child_item_id, "CONFLICT", "Build would create a self-referencing Item usage.")
                        else:
                            existing = conn.execute(
                                "SELECT id FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                                (member_id,),
                            ).fetchone()
                            if existing:
                                conn.execute(
                                    """
                                    UPDATE item_usages SET child_item_id=?,quantity=?,
                                        sort_order=?,build_status='COMPLETED',
                                        modified_at=datetime('now') WHERE id=?
                                    """,
                                    (
                                        child_item_id, max(1, int(member.get("quantity") or 1)),
                                        int(member.get("sort_order") or member_id), int(existing["id"]),
                                    ),
                                )
                                summary["updated"] += 1
                                usage_id = int(existing["id"])
                            else:
                                cur = conn.execute(
                                    """
                                    INSERT INTO item_usages(
                                        project_id,parent_item_id,child_item_id,quantity,
                                        sort_order,source,cad_member_id,build_status,
                                        created_by
                                    ) VALUES(?,?,?,?,?,'CAD_BUILD',?,'COMPLETED',?)
                                    """,
                                    (
                                        project_id, parent_item_id, child_item_id,
                                        max(1, int(member.get("quantity") or 1)),
                                        int(member.get("sort_order") or member_id), member_id,
                                        int(actor_id) if actor_id else None,
                                    ),
                                )
                                usage_id = int(cur.lastrowid)
                                summary["created"] += 1
                            if member.get("reference_designator") or member.get("component_path"):
                                conn.execute("DELETE FROM item_occurrences WHERE item_usage_id=?", (usage_id,))
                                conn.execute(
                                    """
                                    INSERT INTO item_occurrences(
                                        item_usage_id,occurrence_name,reference_designator,
                                        component_path,source_cad_member_id
                                    ) VALUES(?,?,?,?,?)
                                    """,
                                    (
                                        usage_id, member.get("reference_designator"),
                                        member.get("reference_designator"),
                                        member.get("component_path"), member_id,
                                    ),
                                )
                            result(member_id, parent_item_id, child_item_id, "COMPLETED", "Item usage synchronized from CAD.")
                    if multi_level:
                        child_owner = self._owner_association(conn, child_cad_id)
                        if child_owner:
                            build(child_cad_id, depth + 1, (*ancestors, cad_document_id))

            try:
                build(int(root_cad_document_id), 0, ())
                conn.execute(
                    """
                    UPDATE pdm_build_runs SET status='COMPLETED',
                        completed_at=datetime('now'),summary_json=? WHERE id=?
                    """,
                    (json.dumps(summary, sort_keys=True), run_id),
                )
            except Exception as exc:
                conn.execute(
                    """
                    UPDATE pdm_build_runs SET status='FAILED',
                        completed_at=datetime('now'),summary_json=? WHERE id=?
                    """,
                    (json.dumps({"error": str(exc)}, sort_keys=True), run_id),
                )
                raise
        snapshots = [
            self.repo.capture_item_structure_iteration(
                item_id, "CAD_BUILD", created_by=actor_id, build_run_id=run_id
            )
            for item_id in sorted(built_item_ids)
        ]
        return {"build_run_id": run_id, "structure_snapshots": snapshots, **summary}

    def compare_cad_to_item(self, root_cad_document_id: int) -> dict:
        root = self.repo.get_cad_document(int(root_cad_document_id))
        if not root:
            raise ValueError("The CAD Document was not found.")
        rows = []
        with self.repo.get_conn() as conn:
            parent_assoc = self._owner_association(conn, int(root_cad_document_id))
            if not parent_assoc:
                return {"root": root, "status": "NO_OWNER", "rows": []}
            parent_item_id = int(parent_assoc["item_id"])
            members = self.repo.list_cad_members(int(root_cad_document_id))
            for member in members:
                usage = conn.execute(
                    "SELECT * FROM item_usages WHERE source='CAD_BUILD' AND cad_member_id=?",
                    (int(member["id"]),),
                ).fetchone()
                if member.get("build_excluded") or member.get("document_build_excluded"):
                    status = "EXCLUDED"
                elif member.get("association_ambiguous"):
                    status = "AMBIGUOUS_ITEM_ASSOCIATION"
                elif not member.get("item_id"):
                    status = "NO_RELATED_ITEM"
                elif not member.get("participates_in_structure"):
                    status = "NOT_PARTICIPATING"
                elif not usage:
                    status = "TO_BE_BUILT"
                elif int(usage["child_item_id"]) != int(member["item_id"]) or int(usage["quantity"]) != max(1, int(member.get("quantity") or 1)):
                    status = "UPDATE_REQUIRED"
                else:
                    status = "COMPLETED"
                rows.append({**member, "parent_item_id": parent_item_id, "status": status})
            current_member_ids = {int(row["id"]) for row in members}
            stale_usages = conn.execute(
                """
                SELECT u.*,b.part_number AS item_number,
                       b.aes_number AS item_aes_number,b.name AS item_name
                FROM item_usages u JOIN bom b ON b.id=u.child_item_id
                WHERE u.parent_item_id=? AND u.source='CAD_BUILD'
                """,
                (parent_item_id,),
            ).fetchall()
            for usage in stale_usages:
                member_id = usage["cad_member_id"]
                if member_id is not None and int(member_id) in current_member_ids:
                    continue
                rows.append({
                    "id": None,
                    "cad_member_id": member_id,
                    "parent_item_id": parent_item_id,
                    "item_id": int(usage["child_item_id"]),
                    "item_number": usage["item_number"],
                    "item_name": usage["item_name"],
                    "quantity": int(usage["quantity"] or 1),
                    "association_type": "—",
                    "number": "—",
                    "file_name": "—",
                    "status": "NOT_NEEDED_IN_ITEM_STRUCTURE",
                })
        return {"root": root, "status": "COMPARED", "rows": rows}

    def get_item_structure_project(self, project_id: int) -> dict:
        """Return persisted EBOM in the shape consumed by the existing tree UI."""
        with self.repo.get_conn() as conn:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(bom)")}
            predicates = ["project_id=?"]
            if "represented_part_id" in columns:
                predicates.append("represented_part_id IS NULL")
            items = [dict(row) for row in conn.execute(
                f"SELECT * FROM bom WHERE {' AND '.join(predicates)} ORDER BY id",
                (int(project_id),),
            ).fetchall()]
            usages = [dict(row) for row in conn.execute(
                """
                SELECT * FROM item_usages WHERE project_id=?
                ORDER BY parent_item_id,COALESCE(sort_order,id),id
                """,
                (int(project_id),),
            ).fetchall()]
        item_by_id = {int(row["id"]): row for row in items}
        children = defaultdict(list)
        child_ids = set()
        for usage in usages:
            parent_id = int(usage["parent_item_id"])
            child_id = int(usage["child_item_id"])
            if parent_id in item_by_id and child_id in item_by_id:
                children[parent_id].append(usage)
                child_ids.add(child_id)

        def node(item_id: int, usage=None, level=0, effective_quantity=1, ancestors=()):
            if item_id in ancestors:
                raise ValueError("Circular persisted Item structure detected.")
            item = dict(item_by_id[item_id])
            source_qty = max(1, int((usage or {}).get("quantity") or 1))
            effective = effective_quantity * source_qty if usage else 1
            result = {
                **item,
                "bom_id": item_id,
                "version_label": str(item.get("revision") or "A"),
                "state": str(item.get("lifecycle_state") or item.get("status") or ""),
                "source_quantity": source_qty,
                "effective_quantity": effective,
                "level": level,
                "source": str((usage or {}).get("source") or "ROOT"),
                "item_usage_id": (usage or {}).get("id"),
                "effective_parent_bom_id": (usage or {}).get("parent_item_id"),
                "children": [],
            }
            for child_usage in children.get(item_id, []):
                result["children"].append(node(
                    int(child_usage["child_item_id"]), child_usage, level + 1,
                    effective, (*ancestors, item_id),
                ))
            return result

        roots = [
            node(item_id) for item_id in item_by_id
            if item_id not in child_ids
        ]
        return {
            "project_id": int(project_id), "roots": roots,
            "excluded_roots": [], "flattened_roots": [],
            "source": "PERSISTED_ITEM_STRUCTURE",
        }

    def export_item_structure(self, root_item_id: int, file_path: str) -> dict:
        with self.repo.get_conn() as conn:
            row = conn.execute(
                "SELECT project_id FROM bom WHERE id=?", (int(root_item_id),)
            ).fetchone()
        if not row:
            raise ValueError("The root Item was not found.")
        project = self.get_item_structure_project(int(row["project_id"]))

        def find(nodes):
            for candidate in nodes:
                if int(candidate["bom_id"]) == int(root_item_id):
                    return candidate
                result = find(candidate.get("children") or [])
                if result:
                    return result
            return None

        root = find(project.get("roots") or [])
        if root is None:
            raise ValueError("The selected Item is not visible in the persisted Item Structure.")
        rows = []

        def flatten(node, path):
            label = str(
                node.get("part_number")
                or node.get("aes_number")
                or node.get("name")
                or node.get("bom_id")
            )
            current_path = f"{path}/{label}" if path else label
            rows.append({
                "level": int(node.get("level") or 0),
                "path": current_path,
                "item_id": int(node["bom_id"]),
                "item_number": str(node.get("part_number") or ""),
                "aes_number": str(node.get("aes_number") or ""),
                "name": str(node.get("name") or ""),
                "item_type": str(node.get("item_type") or ""),
                "procurement_source": str(node.get("procurement_source") or ""),
                "view": str(node.get("item_view") or ""),
                "default_unit": str(node.get("default_unit") or ""),
                "type": str(node.get("type") or ""),
                "revision": str(node.get("version_label") or ""),
                "lifecycle_state": str(node.get("state") or ""),
                "quantity": int(node.get("source_quantity") or 1),
                "effective_quantity": int(node.get("effective_quantity") or 1),
                "usage_source": str(node.get("source") or ""),
            })
            for child in node.get("children") or []:
                flatten(child, current_path)

        flatten(root, "")
        fields = (
            "level", "path", "item_id", "item_number", "aes_number", "name",
            "item_type", "procurement_source", "view", "default_unit", "type",
            "revision", "lifecycle_state", "quantity", "effective_quantity",
            "usage_source",
        )
        with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return {"file_path": file_path, "root_item_id": int(root_item_id), "row_count": len(rows)}

    def add_manual_item_usage(
        self, project_id: int, parent_item_id: int, child_item_id: int,
        quantity: int = 1, actor_id=None,
    ) -> int:
        usage_id = self.repo.add_manual_item_usage(
            int(project_id), int(parent_item_id), int(child_item_id),
            int(quantity), actor_id,
        )
        self.repo.capture_item_structure_iteration(
            int(parent_item_id), "MANUAL", created_by=actor_id
        )
        return usage_id

    def item_where_used(self, project_id: int, child_item_id: int) -> list[dict]:
        with self.repo.get_conn() as conn:
            return [dict(row) for row in conn.execute(
                """
                SELECT u.id AS usage_id,u.parent_item_id AS effective_parent_bom_id,
                       u.child_item_id AS bom_id,u.quantity AS source_quantity,
                       u.quantity AS effective_quantity,u.source,
                       p.part_number AS parent_item_number,
                       p.aes_number AS parent_aes_number,p.name AS parent_name
                FROM item_usages u JOIN bom p ON p.id=u.parent_item_id
                WHERE u.project_id=? AND u.child_item_id=?
                ORDER BY lower(COALESCE(p.part_number,'')),lower(p.name),u.id
                """,
                (int(project_id), int(child_item_id)),
            ).fetchall()]

    def sync_legacy_item(self, item_id: int) -> list[dict]:
        """Project a newly created legacy Item's CAD fields into the PDM domain."""
        with self.repo.get_conn() as conn:
            row = conn.execute("SELECT * FROM bom WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return []
            item = dict(row)
        file_name = str(item.get("filename") or "").strip()
        if not file_name:
            return self.repo.list_item_cad_documents(int(item_id))
        target_item_id = int(item.get("represented_part_id") or item_id)
        existing = self.repo.get_cad_document_by_file(int(item.get("project_id") or 0), file_name)
        if existing:
            cad_id = int(existing["id"])
        else:
            cad_id = self.repo.create_cad_document(
                int(item.get("project_id") or 0),
                "",
                str(item.get("name") or self.repo.normalize_base(file_name)),
                file_name,
                category="ASSEMBLY" if str(item.get("type") or "").lower() in {"asm", "assembly"} else "COMPONENT",
                build_excluded=str(item.get("default_ebom_behavior") or "NORMAL").upper() == "EXCLUDE",
                legacy_bom_id=int(item_id),
            )
        if not self.repo.get_active_association(int(cad_id), target_item_id):
            cad_has_owner = any(
                str(row.get("association_type") or "").upper() == "OWNER"
                for row in self.repo.list_active_associations_for_cad(int(cad_id))
            )
            target_has_owner = any(
                str(row.get("association_type") or "").upper() == "OWNER"
                for row in self.repo.list_item_cad_documents(target_item_id)
            )
            association_type = (
                "IMAGE"
                if item.get("represented_part_id") or cad_has_owner or target_has_owner
                else "OWNER"
            )
            self.associate(
                int(item.get("project_id") or 0), target_item_id, cad_id,
                association_type,
            )

        drawing_file = str(item.get("drawing") or "").strip()
        if drawing_file:
            drawing = self.repo.get_cad_document_by_file(
                int(item.get("project_id") or 0), drawing_file
            )
            if drawing is None:
                drawing_id = self.repo.create_cad_document(
                    int(item.get("project_id") or 0),
                    "",
                    f"{item.get('name') or self.repo.normalize_base(drawing_file)} drawing",
                    drawing_file,
                    category="DRAWING",
                    legacy_bom_id=int(item_id),
                    drawing_owner_cad_document_id=int(cad_id),
                )
            else:
                drawing_id = int(drawing["id"])
                if drawing.get("drawing_owner_cad_document_id") is None:
                    self.repo.bind_drawing_to_model(drawing_id, int(cad_id))
            if not self.repo.get_active_association(
                int(drawing_id), target_item_id
            ):
                self.associate(
                    int(item.get("project_id") or 0), target_item_id,
                    int(drawing_id), "CONTENT",
                )
            # The legacy Item has one explicit drawing field, so its exact DRW
            # is the primary selection for this Item/model. Other Items that
            # share the model keep their own drawing assignments unchanged.
            self.repo.set_primary_drawing(
                target_item_id, int(cad_id), int(drawing_id)
            )
        return self.repo.list_item_cad_documents(target_item_id)

    def sync_legacy_cad_relation(
        self, parent_item_id: int, child_item_id: int, *,
        quantity: int = 1, legacy_usage_id=None,
    ) -> int | None:
        """Mirror a legacy CAD-tree relation without creating an EBOM usage."""
        parent_cad = self.repo.get_cad_document_by_legacy_item(int(parent_item_id))
        child_cad = self.repo.get_cad_document_by_legacy_item(int(child_item_id))
        if not parent_cad or not child_cad:
            return None
        return self.repo.add_cad_member(
            int(parent_cad["id"]), int(child_cad["id"]), max(1, int(quantity)),
            legacy_usage_id=legacy_usage_id,
        )

    def owner_cad_for_item(self, item_id: int) -> dict | None:
        for document in self.repo.list_item_cad_documents(int(item_id)):
            if str(document.get("association_type") or "").upper() == "OWNER":
                return document
        return None

    def add_cad_member(
        self, parent_cad_document_id: int, child_cad_document_id: int,
        quantity: int = 1, *, build_excluded: bool = False,
    ) -> int:
        parent_id = int(parent_cad_document_id)
        child_id = int(child_cad_document_id)

        def reaches(current: int, target: int, seen: set[int]) -> bool:
            if current == target:
                return True
            if current in seen:
                return False
            seen.add(current)
            return any(
                reaches(int(row["child_cad_document_id"]), target, seen)
                for row in self.repo.list_cad_members(current)
            )

        if reaches(child_id, parent_id, set()):
            raise ValueError("This relation would create a circular CAD structure.")
        return self.repo.add_cad_member(
            parent_id, child_id, max(1, int(quantity)),
            build_excluded=bool(build_excluded),
        )

    def remove_cad_member(self, member_id: int) -> bool:
        return self.repo.remove_cad_member(int(member_id))

    def get_cad_structure_project(self, project_id: int) -> dict:
        all_documents = self.repo.list_cad_documents(int(project_id))
        documents = [
            document for document in all_documents
            if str(document.get("category") or "").upper()
            in {"ASSEMBLY", "COMPONENT"}
        ]
        by_id = {int(row["id"]): row for row in documents}
        members = []
        for cad_id in by_id:
            members.extend(self.repo.list_cad_members(cad_id))
        children = defaultdict(list)
        child_ids = set()
        for member in members:
            parent_id = int(member["parent_cad_document_id"])
            child_id = int(member["child_cad_document_id"])
            if parent_id in by_id and child_id in by_id:
                children[parent_id].append(member)
                child_ids.add(child_id)

        def node(cad_id: int, member=None, ancestors=()):
            if cad_id in ancestors:
                return {**by_id[cad_id], "cycle": True, "children": []}
            document = dict(by_id[cad_id])
            document["member_id"] = (member or {}).get("id")
            document["quantity"] = max(1, int((member or {}).get("quantity") or 1))
            document["member_build_excluded"] = bool((member or {}).get("build_excluded"))
            document["children"] = [
                node(
                    int(child["child_cad_document_id"]), child,
                    (*ancestors, cad_id),
                )
                for child in children.get(cad_id, [])
            ]
            return document

        roots = [node(cad_id) for cad_id in by_id if cad_id not in child_ids]
        return {
            "project_id": int(project_id),
            "roots": roots,
            "document_count": len(documents),
            "drawing_count": sum(
                1 for document in all_documents
                if str(document.get("category") or "").upper() == "DRAWING"
            ),
            "unbound_drawing_count": sum(
                1 for document in all_documents
                if str(document.get("category") or "").upper() == "DRAWING"
                and document.get("drawing_owner_cad_document_id") is None
            ),
        }

    def register_supplier_dependency(
        self, project_id: int, owner_item_id: int, file_name: str
    ) -> dict:
        document = self.repo.get_cad_document_by_base(int(project_id), file_name)
        if document is None:
            base = self.repo.normalize_base(file_name)
            cad_id = self.repo.create_cad_document(
                int(project_id), "", base, file_name,
                category="ASSEMBLY" if ".asm" in str(file_name).lower() else "COMPONENT",
                build_excluded=True,
                supplier_owner_item_id=int(owner_item_id),
            )
            document = self.repo.get_cad_document(cad_id)
        association = self.repo.get_active_association(
            int(document["id"]), int(owner_item_id)
        )
        if association is None:
            self.associate(
                int(project_id), int(owner_item_id), int(document["id"]), "CONTENT"
            )
        return self.repo.get_cad_document(int(document["id"]))

    def unregister_supplier_dependency(
        self, project_id: int, owner_item_id: int, base_name: str
    ) -> bool:
        return self.repo.remove_supplier_cad_document(
            int(project_id), int(owner_item_id), base_name
        )

    def checkout_cad_document(
        self,
        cad_document_id: int,
        actor_id: int,
        *,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        workspace_machine_id: str | None = None,
    ) -> dict:
        return self.repo.checkout_cad_document(
            int(cad_document_id),
            int(actor_id),
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            workspace_machine_id=workspace_machine_id,
        )

    def checkout_target_item_ids(self, cad_document_id: int) -> list[int]:
        """Items whose working-copy locks must accompany this CAD checkout."""
        return [
            int(value)
            for value in self.repo.list_checkout_target_item_ids(
                int(cad_document_id)
            )
        ]

    def cad_checkout_item_ids(self, cad_document_id: int) -> list[int]:
        """Items recorded against the active CAD working copy."""
        return [
            int(value)
            for value in self.repo.list_cad_checkout_item_ids(
                int(cad_document_id)
            )
        ]

    def checkin_cad_document(
        self, cad_document_id: int, actor_id: int, source_path: str,
        note: str = "", source_commit_id: str | None = None,
        source_file_name: str | None = None,
        creo_file_version: int | None = None,
    ) -> dict:
        return self.repo.checkin_cad_document(
            int(cad_document_id),
            int(actor_id),
            source_path,
            note,
            source_commit_id=source_commit_id,
            source_file_name=source_file_name,
            creo_file_version=creo_file_version,
        )

    def undo_checkout_cad_document(
        self, cad_document_id: int, actor_id: int, note: str = ""
    ) -> dict:
        return self.repo.undo_checkout_cad_document(
            int(cad_document_id), int(actor_id), note
        )

    def list_checked_out_cad_for_item(self, item_id: int) -> list[dict]:
        return self.repo.list_checked_out_cad_for_item(int(item_id))

    def cad_checkout_history(self, cad_document_id: int) -> list[dict]:
        return self.repo.list_cad_checkout_history(int(cad_document_id))

    def revise_cad_document(self, cad_document_id: int, actor_id: int) -> dict:
        return self.repo.revise_cad_document(int(cad_document_id), int(actor_id))

    def release_cad_document(self, cad_document_id: int) -> dict:
        return self.repo.release_cad_document(int(cad_document_id))
