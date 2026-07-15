"""Released EBOM derivation over immutable CAD BOM iterations."""

import csv
import json
import sqlite3

from config import DB_NAME
from core.ebom_policy import (
    normalize_classification,
    normalize_default_behavior,
    normalize_occurrence_behavior,
    normalize_requirement,
)
from core.repositories.bom_revision_repository import BomRevisionRepository


class EbomResolutionError(ValueError):
    """Raised when an immutable CAD configuration cannot produce a valid EBOM."""


class EbomResolver:
    """Resolve an EBOM without mutating or duplicating the authoritative CAD BOM."""

    MAX_DEPTH = 100

    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        # Keep direct use in tests/tools compatible with databases opened before
        # the application migration runner.
        self.revision_repo = BomRevisionRepository(db_name)

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

    def _iteration_context_conn(self, conn, iteration_id: int) -> dict:
        row = conn.execute(
            """
            SELECT i.id AS iteration_id, i.revision_id, i.iteration_number,
                   i.object_data_json, r.bom_id, r.revision_code, r.state,
                   b.project_id, b.name AS current_name, b.type AS current_type,
                   b.aes_number AS current_aes_number,
                   b.part_number AS current_part_number,
                   b.drawing_number AS current_drawing_number
            FROM bom_iterations i
            JOIN bom_revisions r ON r.id=i.revision_id
            JOIN bom b ON b.id=r.bom_id
            WHERE i.id=?
            """,
            (int(iteration_id),),
        ).fetchone()
        if not row:
            raise EbomResolutionError(f"BOM iteration {iteration_id} was not found.")
        item = dict(row)
        snapshot = self._snapshot(item.pop("object_data_json", None))
        fallbacks = {
            "name": item.pop("current_name", ""),
            "type": item.pop("current_type", ""),
            "aes_number": item.pop("current_aes_number", ""),
            "part_number": item.pop("current_part_number", ""),
            "drawing_number": item.pop("current_drawing_number", ""),
        }
        for field, fallback in fallbacks.items():
            item[field] = snapshot.get(field, fallback)
        for field in (
            "filename", "drawing", "base_file_name", "base_drw_name",
            "material", "weight", "notes", "pdf_path", "step_path",
        ):
            item[field] = snapshot.get(field)
        item["classification"] = normalize_classification(
            snapshot.get("classification")
        )
        item["default_ebom_behavior"] = normalize_default_behavior(
            snapshot.get("default_ebom_behavior")
        )
        item["cad_requirement"] = normalize_requirement(
            snapshot.get("cad_requirement"), "CAD requirement"
        )
        item["drawing_requirement"] = normalize_requirement(
            snapshot.get("drawing_requirement"), "drawing requirement"
        )
        item["version_label"] = (
            f"{item['revision_code']}.{int(item['iteration_number'])}"
        )
        return item

    @staticmethod
    def _bindings_conn(conn, parent_iteration_id: int) -> list[dict]:
        rows = conn.execute(
            """
            SELECT ib.id AS binding_id, ib.parent_iteration_id, ib.usage_id,
                   ib.child_bom_id, ib.child_revision_id, ib.child_iteration_id,
                   COALESCE(ib.quantity, 1) AS quantity,
                   COALESCE(ib.sort_order, ib.id) AS sort_order,
                   COALESCE(ib.ebom_behavior, 'INHERIT') AS ebom_behavior
            FROM bom_iteration_bindings ib
            WHERE ib.parent_iteration_id=?
            ORDER BY COALESCE(ib.sort_order, ib.id), ib.id
            """,
            (int(parent_iteration_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _occurrence_segment(binding: dict) -> str:
        if binding.get("usage_id") is not None:
            return f"usage:{int(binding['usage_id'])}"
        return f"binding:{int(binding['binding_id'])}"

    @staticmethod
    def _display_label(context: dict) -> str:
        return str(
            context.get("aes_number")
            or context.get("name")
            or context.get("bom_id")
            or "item"
        )

    @staticmethod
    def _assert_visible_rows(rows: list[dict]) -> None:
        """Protect every consumer from accidentally receiving policy carrier rows."""
        for row in rows or []:
            behavior = normalize_default_behavior(
                row.get("resolved_ebom_behavior")
            )
            if behavior != "NORMAL":
                raise EbomResolutionError(
                    "Released EBOM resolution attempted to emit a "
                    f"{behavior} CAD object as a visible row."
                )

    @staticmethod
    def _decrement_visible_levels(node: dict) -> None:
        node["level"] = max(0, int(node.get("level") or 0) - 1)
        for child in node.get("children") or []:
            EbomResolver._decrement_visible_levels(child)

    def _apply_root_behavior(self, resolved: dict) -> dict:
        """Apply an object's default when it has no containing CAD occurrence."""
        if resolved.get("_root_policy_applied"):
            return resolved
        result = dict(resolved)
        source_root = result["root"]
        root_behavior = normalize_default_behavior(
            source_root.get("default_ebom_behavior")
        )
        root_summary = {
            "bom_id": int(source_root["bom_id"]),
            "iteration_id": int(source_root["iteration_id"]),
            "version_label": str(source_root.get("version_label") or ""),
            "name": str(source_root.get("name") or ""),
            "aes_number": str(source_root.get("aes_number") or ""),
            "classification": str(
                source_root.get("classification") or "PHYSICAL"
            ),
            "default_ebom_behavior": root_behavior,
        }
        visible_roots = []
        excluded_roots = []
        flattened_roots = []
        visible_rows = list(result.get("rows") or [])
        if root_behavior == "EXCLUDE":
            excluded_roots.append(root_summary)
            visible_rows = []
        elif root_behavior == "FLATTEN":
            flattened_roots.append(root_summary)
            promotion = {
                **root_summary,
                "usage_id": None,
                "source_quantity": 1,
                "effective_quantity": 1,
                "cad_occurrence_path": str(
                    source_root.get("cad_occurrence_path") or "root"
                ),
            }
            for promoted_root in source_root.get("children") or []:
                self._decrement_visible_levels(promoted_root)
                promoted_root["parent_occurrence_path"] = None
                promoted_root["effective_parent_bom_id"] = None
                promoted_root["effective_parent_iteration_id"] = None
                promoted_root["promoted"] = True
                promoted_root["promoted_through"] = [
                    promotion,
                    *(promoted_root.get("promoted_through") or []),
                ]
                visible_roots.append(promoted_root)
        else:
            visible_roots.append(source_root)

        self._assert_visible_rows(visible_rows)
        self._assert_visible_rows(visible_roots)
        result.update({
            "rows": visible_rows,
            "roots": visible_roots,
            "root_visible": root_behavior == "NORMAL",
            "root_behavior": root_behavior,
            "excluded_roots": excluded_roots,
            "flattened_roots": flattened_roots,
            "_root_policy_applied": True,
        })
        return result

    def resolve_iteration(self, root_iteration_id: int) -> dict:
        """Return one exact, occurrence-preserving EBOM tree and pre-order row list."""
        with self.get_conn() as conn:
            context_cache = {}
            binding_cache = {}

            def context(iteration_id: int) -> dict:
                iteration_id = int(iteration_id)
                if iteration_id not in context_cache:
                    context_cache[iteration_id] = self._iteration_context_conn(
                        conn, iteration_id
                    )
                return dict(context_cache[iteration_id])

            def bindings(iteration_id: int) -> list[dict]:
                iteration_id = int(iteration_id)
                if iteration_id not in binding_cache:
                    binding_cache[iteration_id] = self._bindings_conn(
                        conn, iteration_id
                    )
                return [dict(row) for row in binding_cache[iteration_id]]

            root_context = context(int(root_iteration_id))
            root = {
                **root_context,
                "id": int(root_context["bom_id"]),
                "occurrence_path": "root",
                "parent_occurrence_path": None,
                "effective_parent_bom_id": None,
                "effective_parent_iteration_id": None,
                "usage_id": None,
                "source_quantity": 1,
                "effective_quantity": 1,
                "level": 0,
                "ebom_behavior": "NORMAL",
                "resolved_ebom_behavior": "NORMAL",
                "promoted": False,
                "promoted_through": [],
                "children": [],
            }
            flat_rows = []

            def walk(
                parent_context: dict,
                output_children: list,
                visible_parent: dict,
                visible_parent_path: str,
                visible_level: int,
                parent_effective_quantity: int,
                ancestors: tuple[int, ...],
                promotion_chain: tuple[dict, ...],
            ) -> None:
                if len(ancestors) > self.MAX_DEPTH:
                    raise EbomResolutionError(
                        f"EBOM exceeds the supported depth of {self.MAX_DEPTH} levels."
                    )
                for binding in bindings(int(parent_context["iteration_id"])):
                    child = context(int(binding["child_iteration_id"]))
                    if int(child["bom_id"]) != int(binding["child_bom_id"]):
                        raise EbomResolutionError(
                            "An immutable EBOM binding points to the wrong child object."
                        )
                    if int(child["revision_id"]) != int(binding["child_revision_id"]):
                        raise EbomResolutionError(
                            "An immutable EBOM binding points to the wrong child revision."
                        )

                    occurrence_behavior = normalize_occurrence_behavior(
                        binding.get("ebom_behavior")
                    )
                    resolved_behavior = (
                        normalize_default_behavior(child["default_ebom_behavior"])
                        if occurrence_behavior == "INHERIT"
                        else occurrence_behavior
                    )
                    if resolved_behavior == "EXCLUDE":
                        continue

                    child_iteration_id = int(child["iteration_id"])
                    if child_iteration_id in ancestors:
                        labels = [
                            self._display_label(context(iteration_id))
                            for iteration_id in (*ancestors, child_iteration_id)
                        ]
                        raise EbomResolutionError(
                            "Circular EBOM structure detected: " + " -> ".join(labels)
                        )

                    source_quantity = max(1, int(binding.get("quantity") or 1))
                    effective_quantity = (
                        max(1, int(parent_effective_quantity or 1)) * source_quantity
                    )
                    segment = self._occurrence_segment(binding)
                    cad_path = f"{parent_context.get('cad_occurrence_path', 'root')}/{segment}"
                    child["cad_occurrence_path"] = cad_path
                    next_ancestors = (*ancestors, child_iteration_id)

                    if resolved_behavior == "FLATTEN":
                        promoted = {
                            "bom_id": int(child["bom_id"]),
                            "iteration_id": child_iteration_id,
                            "version_label": str(child["version_label"]),
                            "name": str(child.get("name") or ""),
                            "aes_number": str(child.get("aes_number") or ""),
                            "usage_id": binding.get("usage_id"),
                            "source_quantity": source_quantity,
                            "effective_quantity": effective_quantity,
                            "cad_occurrence_path": cad_path,
                        }
                        walk(
                            child,
                            output_children,
                            visible_parent,
                            visible_parent_path,
                            visible_level,
                            effective_quantity,
                            next_ancestors,
                            (*promotion_chain, promoted),
                        )
                        continue

                    occurrence_path = f"{visible_parent_path}/{segment}"
                    row = {
                        **child,
                        "id": int(child["bom_id"]),
                        "occurrence_path": occurrence_path,
                        "cad_occurrence_path": cad_path,
                        "parent_occurrence_path": visible_parent_path,
                        "effective_parent_bom_id": int(visible_parent["bom_id"]),
                        "effective_parent_iteration_id": int(
                            visible_parent["iteration_id"]
                        ),
                        "usage_id": (
                            int(binding["usage_id"])
                            if binding.get("usage_id") is not None else None
                        ),
                        "binding_id": int(binding["binding_id"]),
                        "source_quantity": source_quantity,
                        "effective_quantity": effective_quantity,
                        "level": int(visible_level) + 1,
                        "sort_order": int(binding.get("sort_order") or 0),
                        "ebom_behavior": occurrence_behavior,
                        "resolved_ebom_behavior": resolved_behavior,
                        "promoted": bool(promotion_chain),
                        "promoted_through": [dict(value) for value in promotion_chain],
                        "children": [],
                    }
                    output_children.append(row)
                    flat_rows.append(row)
                    walk(
                        child,
                        row["children"],
                        child,
                        occurrence_path,
                        int(row["level"]),
                        effective_quantity,
                        next_ancestors,
                        (),
                    )

            root_context["cad_occurrence_path"] = "root"
            walk(
                root_context,
                root["children"],
                root_context,
                "root",
                0,
                1,
                (int(root_context["iteration_id"]),),
                (),
            )
            self._assert_visible_rows(flat_rows)
            return self._apply_root_behavior({
                "project_id": int(root_context["project_id"]),
                "root_bom_id": int(root_context["bom_id"]),
                "root_iteration_id": int(root_context["iteration_id"]),
                "root_version": str(root_context["version_label"]),
                "root": root,
                "rows": flat_rows,
            })

    def resolve_bom(self, bom_id: int, iteration_id=None) -> dict:
        if iteration_id is None:
            current = self.revision_repo.get_current_context(int(bom_id))
            iteration_id = current.get("current_iteration_id")
        if iteration_id is None:
            raise EbomResolutionError("The selected BOM object has no checked-in iteration.")
        result = self.resolve_iteration(int(iteration_id))
        if int(result["root_bom_id"]) != int(bom_id):
            raise EbomResolutionError(
                "The selected root iteration belongs to a different BOM object."
            )
        return result

    def resolve_configuration_members(
        self, root_bom_id: int, members: list[dict]
    ) -> dict:
        """Resolve an exact named-configuration occurrence tree as an EBOM."""
        source_members = [dict(member) for member in (members or [])]
        roots = [
            member for member in source_members
            if not str(member.get("parent_occurrence_path") or "").strip()
        ]
        if len(roots) != 1:
            raise EbomResolutionError(
                "A configuration must contain exactly one root occurrence."
            )
        root_member = roots[0]
        if int(root_member.get("bom_id") or 0) != int(root_bom_id):
            raise EbomResolutionError(
                "The configuration root belongs to a different BOM object."
            )

        children_by_path = {}
        seen_paths = set()
        for member in source_members:
            path = str(member.get("occurrence_path") or "").strip()
            if not path or path in seen_paths:
                raise EbomResolutionError(
                    "Configuration occurrence paths must be present and unique."
                )
            seen_paths.add(path)
            parent_path = str(member.get("parent_occurrence_path") or "").strip()
            if parent_path:
                children_by_path.setdefault(parent_path, []).append(member)
        for values in children_by_path.values():
            values.sort(key=lambda row: (
                int(row.get("sort_order") or 0),
                int(row.get("position") or 0),
                int(row.get("sequence_no") or 0),
                str(row.get("occurrence_path") or ""),
            ))
        if any(parent_path not in seen_paths for parent_path in children_by_path):
            raise EbomResolutionError(
                "The configuration contains an occurrence with no parent."
            )
        reachable_paths = {str(root_member["occurrence_path"])}
        remaining = {
            str(member["occurrence_path"]): str(
                member.get("parent_occurrence_path") or ""
            )
            for member in source_members if member is not root_member
        }
        while remaining:
            newly_reachable = {
                path for path, parent_path in remaining.items()
                if parent_path in reachable_paths
            }
            if not newly_reachable:
                raise EbomResolutionError(
                    "The configuration contains a disconnected or circular branch."
                )
            reachable_paths.update(newly_reachable)
            for path in newly_reachable:
                remaining.pop(path, None)

        with self.get_conn() as conn:
            context_cache = {}

            def context(member: dict) -> dict:
                try:
                    iteration_id = int(member["iteration_id"])
                except (KeyError, TypeError, ValueError):
                    raise EbomResolutionError(
                        "Every configuration occurrence must bind an exact iteration."
                    )
                if iteration_id not in context_cache:
                    context_cache[iteration_id] = self._iteration_context_conn(
                        conn, iteration_id
                    )
                item = dict(context_cache[iteration_id])
                if int(item["bom_id"]) != int(member.get("bom_id") or 0):
                    raise EbomResolutionError(
                        "A configuration occurrence points to the wrong BOM object."
                    )
                revision_id = member.get("revision_id")
                if revision_id is not None and int(item["revision_id"]) != int(revision_id):
                    raise EbomResolutionError(
                        "A configuration occurrence points to the wrong BOM revision."
                    )
                return item

            root_context = context(root_member)
            root_path = str(root_member["occurrence_path"])
            root = {
                **root_context,
                "id": int(root_context["bom_id"]),
                "occurrence_path": root_path,
                "cad_occurrence_path": root_path,
                "parent_occurrence_path": None,
                "effective_parent_bom_id": None,
                "effective_parent_iteration_id": None,
                "usage_id": None,
                "source_quantity": 1,
                "effective_quantity": 1,
                "level": 0,
                "ebom_behavior": "NORMAL",
                "resolved_ebom_behavior": "NORMAL",
                "promoted": False,
                "promoted_through": [],
                "children": [],
            }
            flat_rows = []

            def walk(
                source_parent: dict,
                output_children: list,
                visible_parent: dict,
                visible_parent_path: str,
                visible_level: int,
                parent_effective_quantity: int,
                ancestors: tuple[int, ...],
                promotion_chain: tuple[dict, ...],
            ) -> None:
                if len(ancestors) > self.MAX_DEPTH:
                    raise EbomResolutionError(
                        f"EBOM exceeds the supported depth of {self.MAX_DEPTH} levels."
                    )
                source_parent_path = str(source_parent["occurrence_path"])
                for member in children_by_path.get(source_parent_path, []):
                    child = context(member)
                    occurrence_behavior = normalize_occurrence_behavior(
                        member.get("ebom_behavior")
                    )
                    resolved_behavior = (
                        normalize_default_behavior(child["default_ebom_behavior"])
                        if occurrence_behavior == "INHERIT"
                        else occurrence_behavior
                    )
                    if resolved_behavior == "EXCLUDE":
                        continue

                    child_iteration_id = int(child["iteration_id"])
                    if child_iteration_id in ancestors:
                        labels = [
                            self._display_label(context_cache[iteration_id])
                            for iteration_id in (*ancestors, child_iteration_id)
                        ]
                        raise EbomResolutionError(
                            "Circular EBOM configuration detected: "
                            + " -> ".join(labels)
                        )

                    source_quantity = max(1, int(member.get("quantity") or 1))
                    effective_quantity = (
                        max(1, int(parent_effective_quantity or 1)) * source_quantity
                    )
                    cad_path = str(member["occurrence_path"])
                    next_ancestors = (*ancestors, child_iteration_id)
                    if resolved_behavior == "FLATTEN":
                        promoted = {
                            "bom_id": int(child["bom_id"]),
                            "iteration_id": child_iteration_id,
                            "version_label": str(child["version_label"]),
                            "name": str(child.get("name") or ""),
                            "aes_number": str(child.get("aes_number") or ""),
                            "usage_id": member.get("usage_id"),
                            "source_quantity": source_quantity,
                            "effective_quantity": effective_quantity,
                            "cad_occurrence_path": cad_path,
                        }
                        walk(
                            member, output_children, visible_parent,
                            visible_parent_path, visible_level,
                            effective_quantity, next_ancestors,
                            (*promotion_chain, promoted),
                        )
                        continue

                    row = {
                        **child,
                        "id": int(child["bom_id"]),
                        "occurrence_path": cad_path,
                        "cad_occurrence_path": cad_path,
                        "parent_occurrence_path": visible_parent_path,
                        "effective_parent_bom_id": int(visible_parent["bom_id"]),
                        "effective_parent_iteration_id": int(
                            visible_parent["iteration_id"]
                        ),
                        "usage_id": (
                            int(member["usage_id"])
                            if member.get("usage_id") is not None else None
                        ),
                        "source_quantity": source_quantity,
                        "effective_quantity": effective_quantity,
                        "level": int(visible_level) + 1,
                        "sort_order": int(member.get("sort_order") or 0),
                        "ebom_behavior": occurrence_behavior,
                        "resolved_ebom_behavior": resolved_behavior,
                        "promoted": bool(promotion_chain),
                        "promoted_through": [
                            dict(value) for value in promotion_chain
                        ],
                        "children": [],
                    }
                    output_children.append(row)
                    flat_rows.append(row)
                    walk(
                        member, row["children"], child, cad_path,
                        int(row["level"]), effective_quantity,
                        next_ancestors, (),
                    )

            walk(
                root_member, root["children"], root_context, root_path, 0, 1,
                (int(root_context["iteration_id"]),), (),
            )
            self._assert_visible_rows(flat_rows)
            return self._apply_root_behavior({
                "project_id": int(root_context["project_id"]),
                "root_bom_id": int(root_context["bom_id"]),
                "root_iteration_id": int(root_context["iteration_id"]),
                "root_version": str(root_context["version_label"]),
                "root": root,
                "rows": flat_rows,
            })

    def resolve_project(self, project_id: int) -> dict:
        """Resolve each checked-in CAD root; organizational folders are not consulted."""
        with self.get_conn() as conn:
            objects = conn.execute(
                """
                SELECT id, current_iteration_id
                FROM bom
                WHERE project_id=?
                  AND LOWER(TRIM(COALESCE(type,''))) <> 'folder'
                ORDER BY id
                """,
                (int(project_id),),
            ).fetchall()
            object_ids = {int(row["id"]) for row in objects}
            child_ids = {
                int(row[0])
                for row in conn.execute(
                    """
                    SELECT DISTINCT ib.child_bom_id
                    FROM bom parent
                    JOIN bom_iteration_bindings ib
                      ON ib.parent_iteration_id=parent.current_iteration_id
                    JOIN bom child ON child.id=ib.child_bom_id
                    WHERE parent.project_id=? AND child.project_id=?
                      AND LOWER(TRIM(COALESCE(parent.type,''))) <> 'folder'
                      AND LOWER(TRIM(COALESCE(child.type,''))) <> 'folder'
                    """,
                    (int(project_id), int(project_id)),
                ).fetchall()
            }
        root_rows = [row for row in objects if int(row["id"]) not in child_ids]
        if object_ids and not root_rows:
            raise EbomResolutionError(
                "No CAD structure root exists; the checked-in project structure is circular."
            )
        roots = []
        rows = []
        excluded_roots = []
        flattened_roots = []
        for root_row in root_rows:
            iteration_id = root_row["current_iteration_id"]
            if iteration_id is None:
                continue
            resolved = self.resolve_iteration(int(iteration_id))
            roots.extend(resolved.get("roots") or [])
            rows.extend(resolved["rows"])
            excluded_roots.extend(resolved.get("excluded_roots") or [])
            flattened_roots.extend(resolved.get("flattened_roots") or [])
        self._assert_visible_rows(rows)
        self._assert_visible_rows(roots)
        return {
            "project_id": int(project_id),
            "roots": roots,
            "rows": rows,
            "excluded_roots": excluded_roots,
            "flattened_roots": flattened_roots,
        }

    def effective_where_used(self, project_id: int, child_bom_id: int) -> list[dict]:
        project = self.resolve_project(int(project_id))
        results = []
        for root in project["roots"]:
            stack = list(reversed(root.get("children") or []))
            while stack:
                row = stack.pop()
                if int(row["bom_id"]) == int(child_bom_id):
                    results.append({
                        "child_bom_id": int(child_bom_id),
                        "occurrence_path": row["occurrence_path"],
                        "cad_occurrence_path": row["cad_occurrence_path"],
                        "usage_id": row.get("usage_id"),
                        "effective_parent_bom_id": row["effective_parent_bom_id"],
                        "effective_parent_iteration_id": row[
                            "effective_parent_iteration_id"
                        ],
                        "root_bom_id": int(root["bom_id"]),
                        "root_iteration_id": int(root["iteration_id"]),
                        "source_quantity": int(row["source_quantity"]),
                        "effective_quantity": int(row["effective_quantity"]),
                        "promoted": bool(row.get("promoted")),
                        "promoted_through": [
                            dict(value) for value in row.get("promoted_through") or []
                        ],
                    })
                stack.extend(reversed(row.get("children") or []))
        return results


class EbomService:
    def __init__(self, resolver=None, db_name=DB_NAME):
        self.resolver = resolver or EbomResolver(db_name)

    def resolve_iteration(self, iteration_id: int) -> dict:
        return self.resolver.resolve_iteration(int(iteration_id))

    def resolve_bom(self, bom_id: int, iteration_id=None) -> dict:
        return self.resolver.resolve_bom(int(bom_id), iteration_id)

    def resolve_project(self, project_id: int) -> dict:
        return self.resolver.resolve_project(int(project_id))

    def resolve_configuration_members(
        self, root_bom_id: int, members: list[dict]
    ) -> dict:
        return self.resolver.resolve_configuration_members(
            int(root_bom_id), members
        )

    def effective_where_used(self, project_id: int, child_bom_id: int) -> list[dict]:
        return self.resolver.effective_where_used(int(project_id), int(child_bom_id))


class EbomExportService:
    FIELDNAMES = (
        "occurrence_path", "level", "parent_bom_id", "bom_id", "aes_number",
        "name", "part_number", "type", "classification", "version",
        "source_quantity", "effective_quantity", "occurrence_behavior",
        "resolved_behavior", "promoted_through",
    )

    def __init__(self, ebom_service=None, db_name=DB_NAME):
        self.ebom_service = ebom_service or EbomService(db_name=db_name)

    @staticmethod
    def _export_row(row: dict) -> dict:
        promoted = " > ".join(
            str(value.get("aes_number") or value.get("name") or value.get("bom_id"))
            for value in (row.get("promoted_through") or [])
        )
        return {
            "occurrence_path": row.get("occurrence_path") or "",
            "level": int(row.get("level") or 0),
            "parent_bom_id": row.get("effective_parent_bom_id") or "",
            "bom_id": row.get("bom_id") or "",
            "aes_number": row.get("aes_number") or "",
            "name": row.get("name") or "",
            "part_number": row.get("part_number") or "",
            "type": row.get("type") or "",
            "classification": row.get("classification") or "",
            "version": row.get("version_label") or "",
            "source_quantity": int(row.get("source_quantity") or 1),
            "effective_quantity": int(row.get("effective_quantity") or 1),
            "occurrence_behavior": row.get("ebom_behavior") or "",
            "resolved_behavior": row.get("resolved_ebom_behavior") or "",
            "promoted_through": promoted,
        }

    def export_bom(self, bom_id: int, file_path: str, iteration_id=None) -> dict:
        resolved = self.ebom_service.resolve_bom(int(bom_id), iteration_id)
        rows = [self._export_row(row) for row in resolved["rows"]]
        with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "file_path": file_path,
            "root_bom_id": int(resolved["root_bom_id"]),
            "root_iteration_id": int(resolved["root_iteration_id"]),
            "row_count": len(rows),
        }
