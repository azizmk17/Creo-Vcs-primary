"""Review-first synchronization of native CAD, independent of authored EBOMs."""

import hashlib
import json
import sqlite3
from contextlib import nullcontext
import re
from collections import Counter, defaultdict
from pathlib import Path

from core.integrations.creo_file_validation import is_native_creo_file
from core.repositories.pdm_repository import PdmRepository


_NATIVE = re.compile(r"^([^/\\]+\.(prt|asm|drw))(?:\.(\d+))?$", re.I)
_CATEGORY = {"prt": "COMPONENT", "asm": "ASSEMBLY", "drw": "DRAWING"}


def logical_name(name):
    match = _NATIVE.fullmatch(str(name))
    if not match:
        raise ValueError("Invalid native CAD filename: " + str(name))
    return match.group(1).casefold()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CadStructureSyncService:
    def __init__(self, db_name, runner=None):
        self.repo = PdmRepository(db_name)
        self.runner = runner

    @staticmethod
    def _normalize_pending_payload(payload):
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise ValueError("Creo CAD relationship metadata is missing or unsupported.")
        members = []
        for raw in payload.get("members") or []:
            if not isinstance(raw, dict) or str(raw.get("status") or "ACTIVE") != "ACTIVE":
                raise ValueError("Creo reported an inactive or invalid assembly occurrence.")
            parent = logical_name(raw.get("parent_file_name"))
            child = logical_name(raw.get("child_file_name"))
            if not parent.endswith(".asm") or child.endswith(".drw"):
                raise ValueError("Assembly occurrences must reference an ASM parent and PRT/ASM child.")
            feature_id = raw.get("feature_id")
            if type(feature_id) is not int or feature_id < 0:
                raise ValueError("Creo reported an invalid component feature identifier.")
            members.append({
                "parent_file_name": parent,
                "child_file_name": child,
                "feature_id": feature_id,
            })
        drawings = []
        for raw in payload.get("drawings") or []:
            if not isinstance(raw, dict):
                raise ValueError("Creo reported an invalid drawing relationship.")
            drawing = logical_name(raw.get("drawing_file_name"))
            model = logical_name(raw.get("model_file_name"))
            if not drawing.endswith(".drw") or model.endswith(".drw"):
                raise ValueError("Drawing relationships must connect a DRW to a PRT or ASM.")
            drawings.append({
                "drawing_file_name": drawing,
                "model_file_name": model,
            })
        complete_assemblies = sorted({
            logical_name(value) for value in (payload.get("complete_assemblies") or [])
        })
        if any(not name.endswith(".asm") for name in complete_assemblies):
            raise ValueError("Only assembly documents can have complete occurrence snapshots.")
        dependency_baselines = []
        for raw in payload.get("dependency_baselines") or []:
            if not isinstance(raw, dict):
                raise ValueError("Creo dependency baseline is invalid.")
            cad_id = raw.get("cad_document_id")
            iteration = raw.get("iteration")
            if type(cad_id) is not int or cad_id <= 0 or type(iteration) is not int or iteration < 0:
                raise ValueError("Creo dependency baseline identity or iteration is invalid.")
            dependency_baselines.append({
                "cad_document_id": cad_id,
                "file_name": logical_name(raw.get("file_name")),
                "revision": str(raw.get("revision") or ""),
                "iteration": iteration,
                "sha256": str(raw.get("sha256") or "").lower(),
            })
        return {"schema": 1, "members": members, "drawings": drawings,
                "complete_assemblies": complete_assemblies,
                "dependency_baselines": dependency_baselines}

    @staticmethod
    def _current_iteration_hash(conn, document):
        try:
            row = conn.execute("""
                SELECT sha256 FROM cad_document_iterations
                WHERE cad_document_id=? AND revision=? AND iteration=?
            """, (int(document["id"]), str(document.get("revision") or ""),
                  int(document.get("iteration") or 0))).fetchone()
        except sqlite3.OperationalError:
            return ""
        return str(row["sha256"] or "").lower() if row else ""

    def _capture_dependency_baselines(self, conn, payload, by_name, required_ids, previous=()):
        required = {int(value) for value in required_ids}
        existing = {int(row["cad_document_id"]): row for row in previous}
        names = set()
        for edge in payload["members"]:
            names.update((edge["parent_file_name"], edge["child_file_name"]))
        for relation in payload["drawings"]:
            names.update((relation["drawing_file_name"], relation["model_file_name"]))
        baselines = []
        for name in sorted(names):
            document = by_name.get(name)
            if not document:
                continue
            cad_id = int(document["id"])
            if cad_id in required:
                continue
            baseline = existing.get(cad_id)
            if baseline is None:
                baseline = {
                    "cad_document_id": cad_id,
                    "file_name": logical_name(document["file_name"]),
                    "revision": str(document.get("revision") or ""),
                    "iteration": int(document.get("iteration") or 0),
                    "sha256": self._current_iteration_hash(conn, document),
                }
            baselines.append(dict(baseline))
        return sorted(baselines, key=lambda row: row["cad_document_id"])

    def _validate_dependency_baselines(self, conn, payload, merged_ids):
        for baseline in payload["dependency_baselines"]:
            cad_id = int(baseline["cad_document_id"])
            if cad_id in merged_ids:
                continue
            document = conn.execute(
                "SELECT * FROM cad_documents WHERE id=?", (cad_id,)
            ).fetchone()
            if not document:
                raise ValueError("A referenced CAD dependency was deleted after staging: "
                                 + baseline["file_name"])
            current = dict(document)
            if (str(current.get("revision") or "") != baseline["revision"]
                or int(current.get("iteration") or 0) != baseline["iteration"]):
                raise ValueError("CAD dependency changed since review or staging; refresh and submit again: "
                                 + baseline["file_name"])
            expected_hash = baseline["sha256"]
            if expected_hash and self._current_iteration_hash(conn, current) != expected_hash:
                raise ValueError("CAD dependency content changed since review or staging; refresh and submit again: "
                                 + baseline["file_name"])

    def review_pending_structure(self, project_id, actor_id, required_ids, payload):
        """Return a server-authoritative CAD relationship diff before check-in staging."""
        normalized = self._normalize_pending_payload(payload)
        required = {int(value) for value in required_ids if int(value) > 0}
        with self.repo.get_conn() as conn:
            documents = conn.execute(
                "SELECT * FROM cad_documents WHERE project_id=?", (int(project_id),)
            ).fetchall()
            by_name = {logical_name(row["file_name"]): dict(row) for row in documents}
            by_id = {int(row["id"]): dict(row) for row in documents}
            if not required.issubset(by_id):
                raise ValueError("A selected CAD Document no longer belongs to this project.")
            complete_ids = {}
            for name in normalized["complete_assemblies"]:
                assembly = by_name.get(name)
                if not assembly or str(assembly.get("category") or "").upper() != "ASSEMBLY":
                    raise ValueError("Complete Creo assembly is not registered in this project: " + name)
                cad_id = int(assembly["id"])
                if cad_id not in required:
                    raise ValueError("Check in the complete assembly in the same batch: " + name)
                if assembly.get("checked_out_by") is None or int(assembly["checked_out_by"]) != int(actor_id):
                    raise ValueError("The complete assembly must be checked out by you: " + name)
                complete_ids[name] = cad_id

            submitted_counts = Counter()
            for edge in normalized["members"]:
                parent = by_name.get(edge["parent_file_name"])
                child = by_name.get(edge["child_file_name"])
                if not parent or str(parent.get("category") or "").upper() != "ASSEMBLY":
                    raise ValueError("Assembly not registered in this project: " + edge["parent_file_name"])
                if not child or str(child.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
                    raise ValueError("Assembly child not registered in this project: " + edge["child_file_name"])
                submitted_counts[(int(parent["id"]), int(child["id"]))] += 1

            changes = []
            current_rows = conn.execute("""
                SELECT m.parent_cad_document_id,m.child_cad_document_id,m.quantity,
                       p.file_name AS parent_name,c.file_name AS child_name
                FROM cad_document_members m
                JOIN cad_documents p ON p.id=m.parent_cad_document_id
                JOIN cad_documents c ON c.id=m.child_cad_document_id
                WHERE p.project_id=?
            """, (int(project_id),)).fetchall()
            current_counts = {
                (int(row["parent_cad_document_id"]), int(row["child_cad_document_id"])):
                int(row["quantity"] or 0) for row in current_rows
            }
            display_names = {
                (int(row["parent_cad_document_id"]), int(row["child_cad_document_id"])):
                (str(row["parent_name"]), str(row["child_name"])) for row in current_rows
            }
            for name, parent_id in complete_ids.items():
                children = {
                    child_id for (candidate_parent, child_id) in current_counts
                    if candidate_parent == parent_id
                } | {
                    child_id for (candidate_parent, child_id) in submitted_counts
                    if candidate_parent == parent_id
                }
                for child_id in children:
                    key = (parent_id, child_id)
                    before = current_counts.get(key, 0)
                    after = submitted_counts.get(key, 0)
                    if before == after:
                        continue
                    if key in display_names:
                        parent_name, child_name = display_names[key]
                    else:
                        parent_name = name
                        child_name = str(by_id[child_id]["file_name"])
                    changes.append({
                        "action": "ADD" if before == 0 else "REMOVE" if after == 0 else "QUANTITY",
                        "parent_file_name": parent_name,
                        "child_file_name": child_name,
                        "before_quantity": before,
                        "after_quantity": after,
                    })
            for (parent_id, child_id), after in submitted_counts.items():
                if parent_id in complete_ids.values():
                    continue
                before = current_counts.get((parent_id, child_id), 0)
                if before == after:
                    continue
                parent = by_id[parent_id]
                if parent_id not in required:
                    raise ValueError("Check in the containing assembly in the same batch: "
                                     + str(parent["file_name"]))
                if parent.get("checked_out_by") is None or int(parent["checked_out_by"]) != int(actor_id):
                    raise ValueError("The containing assembly must be checked out by you before its structure can change: "
                                     + str(parent["file_name"]))
                parent_name = str(by_id[parent_id]["file_name"])
                child_name = str(by_id[child_id]["file_name"])
                changes.append({
                    "action": "ADD" if before == 0 else "QUANTITY",
                    "parent_file_name": parent_name,
                    "child_file_name": child_name,
                    "before_quantity": before,
                    "after_quantity": after,
                })

            dependencies = {}
            referenced_names = set()
            for edge in normalized["members"]:
                referenced_names.update((edge["parent_file_name"], edge["child_file_name"]))
            for relation in normalized["drawings"]:
                referenced_names.update((relation["drawing_file_name"], relation["model_file_name"]))
            for name in referenced_names:
                document = by_name.get(name)
                if not document:
                    raise ValueError("Creo structure references a CAD Document not registered in this project: " + name)
                cad_id = int(document["id"])
                if cad_id in required:
                    continue
                dependencies[cad_id] = {
                    "cad_document_id": cad_id,
                    "file_name": name,
                    "revision": str(document.get("revision") or ""),
                    "iteration": int(document.get("iteration") or 0),
                    "sha256": self._current_iteration_hash(conn, document),
                }
            drawing_rows = []
            for relation in normalized["drawings"]:
                drawing = by_name.get(relation["drawing_file_name"])
                model = by_name.get(relation["model_file_name"])
                if not drawing or str(drawing.get("category") or "").upper() != "DRAWING":
                    raise ValueError("Related drawing is not registered in this project: "
                                     + relation["drawing_file_name"])
                if not model or str(model.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
                    raise ValueError("Drawing model is not registered in this project: "
                                     + relation["model_file_name"])
                owner_id = drawing.get("drawing_owner_cad_document_id")
                owner = by_id.get(int(owner_id)) if owner_id is not None else None
                model_id = int(model["id"])
                if owner_id is not None and int(owner_id) != model_id:
                    raise ValueError("Drawing is already bound to a different model: "
                                     + relation["drawing_file_name"])
                if owner_id is None and (
                    int(drawing["id"]) not in required or model_id not in required
                ):
                    raise ValueError("Check in the drawing and its related model in the same batch: "
                                     + relation["drawing_file_name"])
                drawing_rows.append({
                    "drawing_file_name": relation["drawing_file_name"],
                    "model_file_name": relation["model_file_name"],
                    "current_owner_file_name": str(owner["file_name"]) if owner else "",
                    "model_revision": str(model.get("revision") or ""),
                    "model_iteration": int(model.get("iteration") or 0),
                })
            return {
                "changes": sorted(changes, key=lambda item: (
                    item["parent_file_name"].casefold(), item["child_file_name"].casefold()
                )),
                "dependencies": sorted(dependencies.values(), key=lambda item: item["file_name"].casefold()),
                "drawings": drawing_rows,
            }

    def stage_pending_commit(self, commit_id, project_id, actor_id, required_ids, payload):
        """Stage Creo structure evidence beside a Pending commit, without changing live links."""
        normalized = self._normalize_pending_payload(payload)
        commit_key = str(commit_id or "").strip()
        if not commit_key:
            raise ValueError("The Pending commit has no identifier for CAD relationship metadata.")
        required = sorted({int(value) for value in (required_ids or []) if int(value) > 0})
        if not required:
            raise ValueError("Select the CAD Documents that contain these relationships.")
        with self.repo.get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            documents = conn.execute(
                "SELECT * FROM cad_documents WHERE project_id=?", (int(project_id),)
            ).fetchall()
            by_name = {logical_name(row["file_name"]): dict(row) for row in documents}
            by_id = {int(row["id"]): dict(row) for row in documents}
            if any(value not in by_id for value in required):
                raise ValueError("A selected CAD Document no longer belongs to this project.")
            required_set = set(required)
            complete_ids = set()
            for assembly_name in normalized["complete_assemblies"]:
                assembly = by_name.get(assembly_name)
                if not assembly or str(assembly.get("category") or "").upper() != "ASSEMBLY":
                    raise ValueError("Complete Creo assembly is not registered in this project: " + assembly_name)
                assembly_id = int(assembly["id"])
                if assembly_id not in required_set:
                    raise ValueError("Check in the complete assembly in the same Pending commit: " + assembly_name)
                if assembly.get("checked_out_by") is None or int(assembly["checked_out_by"]) != int(actor_id):
                    raise ValueError("The complete assembly must be checked out by you: " + assembly_name)
                complete_ids.add(assembly_id)
            member_counts = Counter()
            for edge in normalized["members"]:
                parent = by_name.get(edge["parent_file_name"])
                child = by_name.get(edge["child_file_name"])
                if not parent or str(parent.get("category") or "").upper() != "ASSEMBLY":
                    raise ValueError("Assembly not registered in this project: " + edge["parent_file_name"])
                if not child or str(child.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
                    raise ValueError("Assembly child not registered in this project: " + edge["child_file_name"])
                member_counts[(int(parent["id"]), int(child["id"]))] += 1
            for (parent_id, child_id), quantity in member_counts.items():
                parent = by_id[parent_id]
                existing_member = conn.execute("""
                    SELECT quantity FROM cad_document_members
                    WHERE parent_cad_document_id=? AND child_cad_document_id=?
                """, (parent_id, child_id)).fetchone()
                if not existing_member or int(existing_member["quantity"] or 0) != quantity:
                    if parent_id not in required_set:
                        raise ValueError(
                            "Check in the containing assembly in the same Pending commit: "
                            + str(parent.get("file_name") or parent_id)
                        )
                    if parent.get("checked_out_by") is None or int(parent["checked_out_by"]) != int(actor_id):
                        raise ValueError(
                            "The containing assembly must be checked out by you before its structure can change: "
                            + str(parent.get("file_name") or parent_id)
                        )
            drawing_refs = defaultdict(set)
            for relation in normalized["drawings"]:
                drawing_refs[relation["drawing_file_name"]].add(relation["model_file_name"])
            for drawing_name, model_names in drawing_refs.items():
                drawing = by_name.get(drawing_name)
                if not drawing or str(drawing.get("category") or "").upper() != "DRAWING":
                    raise ValueError("Related drawing is not registered in this project: " + drawing_name)
                models = [by_name.get(name) for name in model_names]
                if any(not model or str(model.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}
                       for model in models):
                    raise ValueError("A drawing references an unmanaged model: " + drawing_name)
                owner = drawing.get("drawing_owner_cad_document_id")
                if owner is not None and int(owner) not in {
                    int(model["id"]) for model in models
                }:
                    raise ValueError(
                        "Drawing is already bound to a model Creo does not list: " + drawing_name
                    )
                if owner is None:
                    if len(models) != 1:
                        raise ValueError("Select a primary model for the multi-model drawing: " + drawing_name)
                    model = models[0]
                    if int(drawing["id"]) not in required_set or int(model["id"]) not in required_set:
                        raise ValueError(
                            "Check in the drawing and its related model in the same Pending commit: "
                            + drawing_name
                        )
            existing = conn.execute("""
                SELECT submitted_by,required_cad_document_ids_json,payload_json
                FROM cad_pending_structure_changes WHERE project_id=? AND commit_id=?
                  AND status='PENDING'
            """, (int(project_id), commit_key)).fetchone()
            previous_baselines = list(normalized["dependency_baselines"])
            if existing:
                if int(existing["submitted_by"]) != int(actor_id):
                    raise ValueError("Only the original submitter can extend this Pending CAD structure update.")
                old_payload = self._normalize_pending_payload(json.loads(existing["payload_json"]))
                baseline_by_id = {
                    int(row["cad_document_id"]): row
                    for row in old_payload["dependency_baselines"]
                }
                for row in previous_baselines:
                    baseline_by_id.setdefault(int(row["cad_document_id"]), row)
                previous_baselines = list(baseline_by_id.values())
                merged_members = {
                    (row["parent_file_name"], row["child_file_name"], row["feature_id"]): row
                    for row in old_payload["members"]
                }
                merged_members.update({
                    (row["parent_file_name"], row["child_file_name"], row["feature_id"]): row
                    for row in normalized["members"]
                })
                merged_drawings = {
                    (row["drawing_file_name"], row["model_file_name"]): row
                    for row in old_payload["drawings"]
                }
                merged_drawings.update({
                    (row["drawing_file_name"], row["model_file_name"]): row
                    for row in normalized["drawings"]
                })
                replaced_assemblies = set(normalized["complete_assemblies"])
                if replaced_assemblies:
                    merged_members = {
                        key: row for key, row in merged_members.items()
                        if row["parent_file_name"] not in replaced_assemblies
                    }
                    merged_members.update({
                        (row["parent_file_name"], row["child_file_name"], row["feature_id"]): row
                        for row in normalized["members"]
                        if row["parent_file_name"] in replaced_assemblies
                    })
                normalized = {
                    "schema": 1,
                    "members": list(merged_members.values()),
                    "drawings": list(merged_drawings.values()),
                    "complete_assemblies": sorted(
                        set(old_payload["complete_assemblies"]) | replaced_assemblies
                    ),
                    "dependency_baselines": previous_baselines,
                }
                old_required = json.loads(existing["required_cad_document_ids_json"])
                required = sorted(set(int(value) for value in old_required) | set(required))
            normalized["dependency_baselines"] = self._capture_dependency_baselines(
                conn, normalized, by_name, required, previous_baselines
            )
            self._validate_dependency_baselines(conn, normalized, set())
            encoded = encode(normalized)
            if existing:
                conn.execute("""
                    UPDATE cad_pending_structure_changes
                    SET required_cad_document_ids_json=?,payload_json=?
                    WHERE project_id=? AND commit_id=? AND status='PENDING'
                """, (encode(required), encoded, int(project_id), commit_key))
            else:
                conn.execute("""
                    INSERT INTO cad_pending_structure_changes(
                        project_id,commit_id,submitted_by,required_cad_document_ids_json,payload_json
                    ) VALUES(?,?,?,?,?)
                """, (int(project_id), commit_key, int(actor_id), encode(required), encoded))
        return {"staged": True, "commit_id": commit_key}

    def apply_pending_commit(self, commit_id, project_id, merged_cad_ids, *, validate_only=False,
                             connection=None):
        """Validate or apply Creo structure changes for a complete logical submission."""
        commit_key = str(commit_id or "").strip()
        merged_ids = {int(value) for value in (merged_cad_ids or [])}
        context = self.repo.get_conn() if connection is None else nullcontext(connection)
        with context as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            if connection is None:
                conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute("""
                SELECT * FROM cad_pending_structure_changes
                WHERE project_id=? AND commit_id=? AND status='PENDING'
            """, (int(project_id), commit_key)).fetchone()
            if not pending:
                return {"applied": False, "reason": "no_pending_relationships"}
            required = {int(value) for value in json.loads(pending["required_cad_document_ids_json"])}
            if not required.issubset(merged_ids):
                raise ValueError("Merge is missing one or more CAD Documents required by the Creo structure update.")
            raw_payload = json.loads(pending["payload_json"])
            payload = self._normalize_pending_payload(raw_payload)
            if ((payload["members"] or payload["drawings"])
                and "dependency_baselines" not in raw_payload):
                raise ValueError(
                    "This Pending Creo structure predates dependency baselines; restage its CAD structure before approval."
                )
            documents = conn.execute(
                "SELECT * FROM cad_documents WHERE project_id=?", (int(project_id),)
            ).fetchall()
            by_name = {logical_name(row["file_name"]): dict(row) for row in documents}
            self._validate_dependency_baselines(conn, payload, merged_ids)
            member_counts = Counter()
            feature_ids = defaultdict(list)
            for edge in payload["members"]:
                parent = by_name.get(edge["parent_file_name"])
                child = by_name.get(edge["child_file_name"])
                if not parent or not child:
                    raise ValueError("Creo structure references a CAD Document that no longer exists.")
                if str(parent.get("category") or "").upper() != "ASSEMBLY" or str(child.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
                    raise ValueError("Creo structure contains an invalid parent-child CAD type.")
                pair = (int(parent["id"]), int(child["id"]))
                member_counts[pair] += 1
                feature_ids[pair].append(str(edge["feature_id"]))
            graph = defaultdict(set)
            for row in conn.execute("""
                SELECT m.parent_cad_document_id,m.child_cad_document_id
                FROM cad_document_members m JOIN cad_documents p
                  ON p.id=m.parent_cad_document_id WHERE p.project_id=?
            """, (int(project_id),)):
                graph[int(row["parent_cad_document_id"])].add(int(row["child_cad_document_id"]))
            complete_parent_ids = set()
            for name in payload["complete_assemblies"]:
                parent = by_name.get(name)
                if not parent or str(parent.get("category") or "").upper() != "ASSEMBLY":
                    raise ValueError("Complete Creo assembly no longer exists: " + name)
                parent_id = int(parent["id"])
                if parent_id not in required:
                    raise ValueError("Merge is missing the complete Creo assembly: " + name)
                complete_parent_ids.add(parent_id)
                graph.pop(parent_id, None)
            def reaches(start, target, seen):
                if start == target:
                    return True
                if start in seen:
                    return False
                seen.add(start)
                return any(reaches(value, target, seen) for value in graph.get(start, ()))
            for (parent_id, child_id), quantity in member_counts.items():
                if reaches(child_id, parent_id, set()):
                    raise ValueError("Creo structure update would create a circular assembly relationship.")
                graph[parent_id].add(child_id)

            drawing_bindings = {}
            drawing_refs = defaultdict(set)
            for relation in payload["drawings"]:
                drawing_refs[relation["drawing_file_name"]].add(relation["model_file_name"])
            for drawing_name, model_names in drawing_refs.items():
                drawing = by_name.get(drawing_name)
                if not drawing or str(drawing.get("category") or "").upper() != "DRAWING":
                    raise ValueError("Creo drawing relationship references a missing or invalid CAD Document.")
                models = [by_name.get(name) for name in model_names]
                if any(model is None or str(model.get("category") or "").upper()
                       not in {"ASSEMBLY", "COMPONENT"} for model in models):
                    raise ValueError("Creo drawing relationship references a missing or invalid CAD Document.")
                owner = drawing.get("drawing_owner_cad_document_id")
                model_ids = {int(model["id"]) for model in models}
                if owner is None:
                    if len(model_ids) != 1:
                        raise ValueError("Select a primary model for the multi-model drawing: " + drawing_name)
                    drawing_bindings[int(drawing["id"])] = next(iter(model_ids))
                elif int(owner) not in model_ids:
                    raise ValueError("Creo drawing relationship conflicts with its registered model owner.")

            if validate_only:
                return {"validated": True, "member_relations": len(member_counts),
                        "drawing_relations": len(payload["drawings"])}

            for parent_id in complete_parent_ids:
                desired_children = {
                    child_id for (candidate_parent, child_id) in member_counts
                    if candidate_parent == parent_id
                }
                obsolete = conn.execute("""
                    SELECT id,child_cad_document_id FROM cad_document_members
                    WHERE parent_cad_document_id=?
                """, (parent_id,)).fetchall()
                for member in obsolete:
                    if int(member["child_cad_document_id"]) in desired_children:
                        continue
                    member_id = int(member["id"])
                    conn.execute("UPDATE item_usages SET cad_member_id=NULL WHERE cad_member_id=?", (member_id,))
                    conn.execute("UPDATE item_occurrences SET source_cad_member_id=NULL WHERE source_cad_member_id=?", (member_id,))
                    conn.execute("UPDATE pdm_build_results SET cad_member_id=NULL WHERE cad_member_id=?", (member_id,))
                    conn.execute("DELETE FROM cad_document_members WHERE id=?", (member_id,))
            for (parent_id, child_id), quantity in member_counts.items():
                existing = conn.execute("""
                    SELECT id,sort_order FROM cad_document_members
                    WHERE parent_cad_document_id=? AND child_cad_document_id=?
                """, (parent_id, child_id)).fetchone()
                if existing:
                    conn.execute("UPDATE cad_document_members SET quantity=?,component_path=? WHERE id=?",
                                 (quantity, ",".join(feature_ids[(parent_id, child_id)]), int(existing["id"])))
                else:
                    next_order = conn.execute("""
                        SELECT COALESCE(MAX(sort_order),0)+10 FROM cad_document_members
                        WHERE parent_cad_document_id=?
                    """, (parent_id,)).fetchone()[0]
                    conn.execute("""
                        INSERT INTO cad_document_members(
                            parent_cad_document_id,child_cad_document_id,quantity,
                            sort_order,component_path
                        ) VALUES(?,?,?,?,?)
                    """, (parent_id, child_id, quantity, int(next_order),
                          ",".join(feature_ids[(parent_id, child_id)])))
                graph[parent_id].add(child_id)
            for drawing_id, model_id in drawing_bindings.items():
                conn.execute("UPDATE cad_documents SET drawing_owner_cad_document_id=? WHERE id=?",
                             (model_id, drawing_id))
            conn.execute("""
                UPDATE cad_pending_structure_changes SET status='APPLIED',applied_at=datetime('now')
                WHERE id=?
            """, (int(pending["id"]),))
        return {"applied": True, "member_relations": len(member_counts),
                "drawing_relations": len(payload["drawings"])}

    def validate_pending_commit(self, commit_id, project_id, submitted_cad_ids):
        """Run approval-time structure checks without changing live relationships."""
        return self.apply_pending_commit(
            commit_id, project_id, submitted_cad_ids, validate_only=True
        )

    @staticmethod
    def _state(conn, project_id):
        # Include identity, version, locks and association policy, not UI/cache fields.
        documents = [dict(row) for row in conn.execute("""
            SELECT id, file_name, category, revision, iteration, lifecycle_state,
                   checked_out_by, drawing_owner_cad_document_id, build_excluded,
                   latest_creo_file_name
            FROM cad_documents WHERE project_id=? ORDER BY id
        """, (project_id,))]
        members = [dict(row) for row in conn.execute("""
            SELECT m.* FROM cad_document_members m
            JOIN cad_documents d ON d.id=m.parent_cad_document_id
            WHERE d.project_id=? ORDER BY m.id
        """, (project_id,))]
        associations = [dict(row) for row in conn.execute("""
            SELECT * FROM cad_item_associations WHERE project_id=? ORDER BY id
        """, (project_id,))]
        iterations = [dict(row) for row in conn.execute("""
            SELECT i.cad_document_id, i.revision, i.iteration, i.primary_path, i.sha256
            FROM cad_document_iterations i JOIN cad_documents d ON d.id=i.cad_document_id
            AND d.revision=i.revision AND d.iteration=i.iteration
            WHERE d.project_id=? ORDER BY d.id
        """, (project_id,))]
        return {"documents": documents, "members": members, "associations": associations,
                "iterations": iterations}

    def inventory(self, project_id):
        """Pin managed files; discover only native files directly in the project folder."""
        with self.repo.get_conn() as conn:
            project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not project:
                raise ValueError("The project was not found.")
            project = dict(project)
            state = self._state(conn, project_id)
            iterations = {int(row["cad_document_id"]): dict(row) for row in conn.execute("""
                SELECT i.* FROM cad_document_iterations i JOIN cad_documents d
                ON d.id=i.cad_document_id AND d.revision=i.revision AND d.iteration=i.iteration
                WHERE d.project_id=?
            """, (project_id,))}
        directory = Path(str(project.get("working_directory") or ""))
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("The project working directory is unavailable.")
        sources = {}
        managed = {logical_name(row["file_name"]): row for row in state["documents"]
                   if _NATIVE.fullmatch(row["file_name"])}
        for name, document in managed.items():
            iteration = iterations.get(document["id"], {})
            # An explicit approved suffix is authoritative. Never substitute a newer file.
            raw = iteration.get("primary_path")
            if not raw or str(raw).casefold() == name:
                raw = document.get("latest_creo_file_name") or raw or name
            path = Path(raw)
            if not path.is_absolute():
                path = directory / path
            if path.is_file():
                if logical_name(path.name) != name:
                    raise ValueError("Managed source identity mismatch for " + name)
                sources[name] = {"path": str(path.resolve()), "cad_document_id": document["id"]}
        # No recursion: branches, Pending commits, exports and user folders are excluded.
        candidates = {}
        warnings = []
        for path in directory.iterdir():
            match = _NATIVE.fullmatch(path.name)
            if path.is_file() and not path.is_symlink() and match:
                name = match.group(1).casefold()
                if name not in managed:
                    extension = match.group(2)
                    if not is_native_creo_file(path, extension):
                        warnings.append("Ignored non-Creo file with CAD extension: " + path.name)
                        continue
                    candidates.setdefault(name, []).append((int(match.group(3) or 0), path))
        for name, values in candidates.items():
            version = max(number for number, _ in values)
            paths = [path for number, path in values if number == version]
            if len(paths) != 1:
                raise ValueError("Ambiguous source files for " + name)
            sources[name] = {"path": str(paths[0].resolve()), "cad_document_id": None}
        for source in sources.values():
            source["sha256"] = file_hash(source["path"])
        return project, state, sources, sorted(warnings)

    def scan(self, project_id, root_id, actor_id, *, cancel=None, force=False):
        project, baseline, sources, warnings = self.inventory(int(project_id))
        root = next((row for row in baseline["documents"] if row["id"] == int(root_id)), None)
        if not root:
            raise ValueError("Select a CAD Document in this project.")
        name = logical_name(root["file_name"])
        if name not in sources:
            raise ValueError("The exact controlled file is missing: " + name)
        if self.runner is None:
            from core.integrations.creo_structure_worker import CreoStructureWorker
            self.runner = CreoStructureWorker()
        digest = hashlib.sha256(encode({"sources": sources, "warnings": warnings, "root": name,
                                       "worker": self.runner.signature()}).encode()).hexdigest()
        snapshot = None
        if not force:
            with self.repo.get_conn() as conn:
                cached = conn.execute("""
                    SELECT snapshot_json FROM cad_structure_scans
                    WHERE project_id=? AND root_cad_document_id=? AND input_digest=?
                    ORDER BY id DESC LIMIT 1
                """, (project_id, root_id, digest)).fetchone()
            if cached:
                candidate = json.loads(cached[0])
                if candidate.get("complete") is True:
                    snapshot = candidate
        if snapshot is None:
            # Scan drawings too: filenames do not tell us which models they document.
            roots = [name] + sorted(key for key in sources if key.endswith(".drw") and key != name)
            snapshot = self.runner.run(sources, roots, cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise ValueError("CAD scan cancelled.")
        snapshot["sources"] = sources
        snapshot["root"] = name
        snapshot["warnings"] = warnings
        plan = self._plan(baseline, snapshot)
        with self.repo.get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO cad_structure_scans(project_id,root_cad_document_id,
                    created_by,input_digest,snapshot_json,baseline_json,plan_json)
                VALUES(?,?,?,?,?,?,?)
            """, (project_id, root_id, actor_id, digest, encode(snapshot), encode(baseline), encode(plan)))
            scan_id = int(cursor.lastrowid)
        return {"id": scan_id, "plan": plan, "snapshot": snapshot,
                "readonly": bool(project.get("is_readonly"))}

    @staticmethod
    def _plan(state, snapshot):
        problems = list(snapshot.get("errors") or [])
        warnings = list(snapshot.get("warnings") or [])
        changes = []
        if snapshot.get("schema") != 1 or snapshot.get("complete") is not True:
            problems.append("The native scan is incomplete; no changes can be applied.")
        docs = {logical_name(row["file_name"]): row for row in state["documents"]
                if _NATIVE.fullmatch(row["file_name"])}
        by_id = {row["id"]: name for name, row in docs.items()}
        scanned = {}
        for row in snapshot.get("documents", []):
            name = logical_name(row["file_name"])
            if name in scanned:
                problems.append("Duplicate scan identity: " + name)
            scanned[name] = row
            if name not in snapshot["sources"]:
                problems.append("CAD loaded outside the pinned inventory: " + name)
            if row.get("complete") is not True:
                problems.append("Incomplete model: " + name)
            if name not in docs:
                changes.append({"action": "REGISTER", "parent": name, "child": "", "before": "", "after": "CAD Document"})
        startup_failed = any(str(error).startswith("Creo startup/scan:") for error in problems)
        if snapshot.get("root") not in scanned and not startup_failed:
            problems.append("The selected root is missing from the scan.")
        desired = {}
        for name, row in scanned.items():
            if name.endswith(".asm"):
                counts = Counter()
                features = set()
                for occurrence in row.get("occurrences", []):
                    child = logical_name(occurrence["child"])
                    feature = occurrence.get("feature_id")
                    if type(feature) is not int or feature < 0 or feature in features:
                        problems.append("Invalid or duplicate component feature: " + name)
                    features.add(feature)
                    if child not in scanned or child.endswith(".drw"):
                        problems.append("Unresolved component: " + child)
                    if occurrence.get("status") != "ACTIVE":
                        problems.append("Suppressed or unresolved component requires review: " + name + " / " + child)
                    counts[child] += 1
                desired[name] = dict(counts)
                current = {by_id.get(member["child_cad_document_id"], "?"): member["quantity"]
                           for member in state["members"]
                           if member["parent_cad_document_id"] == docs.get(name, {}).get("id")}
                for child in sorted(set(current) | set(counts)):
                    before, after = current.get(child, 0), counts.get(child, 0)
                    if before != after:
                        changes.append({"action": "REMOVE" if not after else "ADD" if not before else "QUANTITY",
                                        "parent": name, "child": child, "before": before, "after": after})
            elif name.endswith(".drw"):
                refs = sorted({logical_name(value) for value in row.get("drawing_models", [])})
                old_owner = by_id.get(docs.get(name, {}).get("drawing_owner_cad_document_id"))
                if not refs or any(value not in scanned or value.endswith(".drw") for value in refs):
                    problems.append("Unresolved drawing models: " + name)
                elif old_owner not in refs:
                    if len(refs) != 1:
                        problems.append("Select a primary model for multi-model drawing: " + name)
                    elif any(a["cad_document_id"] == docs.get(name, {}).get("id") and a["active"]
                             for a in state["associations"]):
                        problems.append("Review existing Item drawing assignments before rebinding: " + name)
                    else:
                        changes.append({"action": "BIND", "parent": name, "child": refs[0],
                                        "before": old_owner or "", "after": refs[0]})
        graph = {name: [by_id.get(m["child_cad_document_id"]) for m in state["members"]
                        if m["parent_cad_document_id"] == row["id"]]
                 for name, row in docs.items()}
        graph.update({name: list(children) for name, children in desired.items()})
        visited = set()
        def visit(name, stack):
            if name in stack or len(stack) >= 100:
                raise ValueError("Circular or excessively deep CAD structure: " + str(name))
            if name not in visited:
                for child in graph.get(name, []):
                    visit(child, stack | {name})
                visited.add(name)
        for name in desired:
            visit(name, set())
        return {"changes": changes, "problems": sorted(set(str(p) for p in problems)),
                "warnings": sorted(set(str(w) for w in warnings)), "desired": desired}

    def apply(self, scan_id, actor_id, *, can_manage, can_merge=False):
        if not can_manage:
            raise PermissionError("Managing CAD Documents is not permitted.")
        with self.repo.get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM cad_structure_scans WHERE id=?", (scan_id,)).fetchone()
            if not row or row["status"] != "PREVIEW":
                raise ValueError("This scan has already been applied or no longer exists.")
            project = dict(conn.execute("SELECT * FROM projects WHERE id=?", (row["project_id"],)).fetchone())
            if project.get("is_readonly") or str(project.get("version_state", "")).upper() in {"RELEASED", "ARCHIVED"}:
                raise ValueError("This project version is read-only.")
            baseline = json.loads(row["baseline_json"])
            if self._state(conn, row["project_id"]) != baseline:
                raise ValueError("Nexus changed after the scan. Scan again before applying.")
            snapshot = json.loads(row["snapshot_json"])
            for name, source in snapshot["sources"].items():
                if not Path(source["path"]).is_file() or file_hash(source["path"]) != source["sha256"]:
                    raise ValueError("CAD files changed after the scan: " + name)
            plan = self._plan(baseline, snapshot)
            if plan["problems"]:
                raise ValueError("Resolve scan conflicts before applying: " + "; ".join(plan["problems"]))
            docs = {logical_name(d["file_name"]): d for d in baseline["documents"]
                    if _NATIVE.fullmatch(d["file_name"])}
            changed = {change["parent"] for change in plan["changes"]}
            for name in changed:
                document = docs.get(name)
                if document:
                    if str(document["lifecycle_state"]).upper() not in {"IN_WORK", "WIP"}:
                        raise ValueError("Revise released CAD before changing structure: " + name)
                    owner = document["checked_out_by"]
                    if owner is not None and int(owner) != int(actor_id):
                        raise ValueError("CAD is checked out by another user: " + name)
                    if owner is None and not can_merge:
                        raise ValueError("Check out the changed CAD document first, then rescan: " + name)
            ids = {name: doc["id"] for name, doc in docs.items()}
            scanned = {logical_name(d["file_name"]): d for d in snapshot["documents"]}
            # Register solids first so new drawing owners always resolve in this transaction.
            for name in sorted(scanned, key=lambda n: (n.endswith(".drw"), n)):
                if name in ids:
                    continue
                owner = None
                if name.endswith(".drw"):
                    owner = ids[logical_name(scanned[name]["drawing_models"][0])]
                revision = str(project.get("version_label") or "A")
                cur = conn.execute("""
                    INSERT INTO cad_documents(project_id,number,name,file_name,base_file_name,
                        category,revision,drawing_owner_cad_document_id)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (row["project_id"], name, name, name, self.repo.normalize_base(name),
                      _CATEGORY[name.rsplit(".", 1)[1]], revision, owner))
                ids[name] = int(cur.lastrowid)
                source = snapshot["sources"][name]
                conn.execute("""
                    INSERT INTO cad_document_iterations(cad_document_id,revision,iteration,
                        primary_path,sha256,created_by) VALUES(?,?,1,?,?,?)
                """, (ids[name], revision, source["path"], source["sha256"], actor_id))
            for name, children in plan["desired"].items():
                parent = ids[name]
                existing = {m["child_cad_document_id"]: m for m in baseline["members"]
                            if m["parent_cad_document_id"] == parent}
                for order, (child, count) in enumerate(children.items(), start=len(existing) + 1):
                    if ids[child] in existing:
                        conn.execute("UPDATE cad_document_members SET quantity=? WHERE id=?",
                                     (count, existing[ids[child]]["id"]))
                    else:
                        conn.execute("""INSERT INTO cad_document_members(
                            parent_cad_document_id,child_cad_document_id,quantity,sort_order)
                            VALUES(?,?,?,?)""", (parent, ids[child], count, order * 10))
                for child_id, member in existing.items():
                    if child_id in {ids[c] for c in children}:
                        continue
                    # Preserve authored/generated EBOM and history until an explicit Build.
                    conn.execute("UPDATE item_usages SET cad_member_id=NULL WHERE cad_member_id=?", (member["id"],))
                    conn.execute("UPDATE item_occurrences SET source_cad_member_id=NULL WHERE source_cad_member_id=?", (member["id"],))
                    conn.execute("UPDATE pdm_build_results SET cad_member_id=NULL WHERE cad_member_id=?", (member["id"],))
                    conn.execute("DELETE FROM cad_document_members WHERE id=?", (member["id"],))
            for change in plan["changes"]:
                if change["action"] == "BIND":
                    conn.execute("UPDATE cad_documents SET drawing_owner_cad_document_id=? WHERE id=?",
                                 (ids[change["child"]], ids[change["parent"]]))
            conn.execute("""UPDATE cad_structure_scans SET status='APPLIED',applied_at=datetime('now'),
                applied_by=?,applied_state_json=? WHERE id=?""",
                         (actor_id, encode(self._state(conn, row["project_id"])), scan_id))
        return plan

    def assert_current(self, root_id):
        """Opt-in guard: a previously synchronized CAD must not build from stale evidence."""
        with self.repo.get_conn() as conn:
            root = conn.execute("SELECT project_id FROM cad_documents WHERE id=?", (root_id,)).fetchone()
            if not root:
                raise ValueError("CAD Document not found.")
            scans = conn.execute("""SELECT snapshot_json,applied_state_json FROM cad_structure_scans
                WHERE project_id=? AND status='APPLIED' ORDER BY id DESC""", (root[0],)).fetchall()
            if not scans:
                return
            state = self._state(conn, root[0])
        docs = {d["id"]: d for d in state["documents"]}
        selected, pending = set(), [int(root_id)]
        while pending:
            current = pending.pop()
            if current in selected:
                continue
            selected.add(current)
            pending.extend(m["child_cad_document_id"] for m in state["members"]
                           if m["parent_cad_document_id"] == current)
        covered, hashes = set(), {}
        for raw in scans:
            snapshot, applied = json.loads(raw[0]), json.loads(raw[1])
            evidence = {logical_name(d["file_name"]): d for d in snapshot["documents"]}
            old_docs = {d["id"]: d for d in applied["documents"]}
            old_iterations = {d["cad_document_id"]: d for d in applied["iterations"]}
            iterations = {d["cad_document_id"]: d for d in state["iterations"]}
            for doc_id in selected - covered:
                document = docs[doc_id]
                name = logical_name(document["file_name"])
                if name not in evidence:
                    continue
                covered.add(doc_id)
                old = old_docs.get(doc_id, {})
                if any(document.get(k) != old.get(k) for k in ("revision", "iteration", "latest_creo_file_name", "drawing_owner_cad_document_id")):
                    raise ValueError("CAD structure needs a fresh scan after revision/check-in: " + name)
                if iterations.get(doc_id) != old_iterations.get(doc_id):
                    raise ValueError("CAD structure source changed; scan again: " + name)
                def members(values):
                    return sorted((m["child_cad_document_id"], m["quantity"]) for m in values
                                  if m["parent_cad_document_id"] == doc_id)
                if members(state["members"]) != members(applied["members"]):
                    raise ValueError("CAD membership changed after synchronization; scan again: " + name)
                source = snapshot["sources"][name]
                path = source["path"]
                if path not in hashes:
                    hashes[path] = file_hash(path) if Path(path).is_file() else None
                if hashes[path] != source["sha256"]:
                    raise ValueError("Native CAD changed; scan again before building or releasing: " + name)
