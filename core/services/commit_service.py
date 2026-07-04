#!/usr/bin/env python3
import os
import re
import shutil
from datetime import datetime
import uuid
import json
from dataclasses import asdict
from core.repositories.commit_repository import CommitRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.user_repository import UserRepository
from core.repositories.signature_repository import SignatureRepository
from core.services.user_service import UserService
from core.services.base_service import BaseService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager
from core.services.permission_decorators import require_permission
from core.services.issue_service import IssueService
from core.services.traceability_service import TraceabilityService
from core.services.part_file_service import PartFileService

from utils import (
    is_creo_file,
    ensure_dir_exists,
    safe_copy2,
    safe_exists,
    safe_isdir,
    safe_open,
    safe_rmtree,
)
class CommitService(BaseService):
    def __init__(self):
        super().__init__()
        self.commit_repository = CommitRepository()
        self.bom_repo = BomRepository()
        self.lock_repo = LockRepository()
        self.signature_repo = SignatureRepository()
        self.user_service = UserService(UserRepository())
        self.project_service = ProjectService()
        self.issue_service = IssueService()
        self.traceability_service = TraceabilityService()
        self.part_file_service = PartFileService()

        self.session = SessionManager()

    def _canonical_part_root(self, value: str) -> str:
        base = os.path.basename(value or "")
        return (base.split(".")[0] if base else "").strip().lower()

    def _step_summary(self, diff_obj) -> dict:
        return {
            "added_count": len(getattr(diff_obj, "added_surfaces", []) or []),
            "removed_count": len(getattr(diff_obj, "removed_surfaces", []) or []),
            "modified_count": len(getattr(diff_obj, "modified_surfaces", []) or []),
            "volume_before": float(getattr(diff_obj, "volume_before", 0.0) or 0.0),
            "volume_after": float(getattr(diff_obj, "volume_after", 0.0) or 0.0),
            "volume_delta": float(getattr(diff_obj, "volume_delta", 0.0) or 0.0),
        }

    @staticmethod
    def _build_face_map(model) -> list[dict]:
        """Build a face-index → fingerprint lookup table from a parsed model.

        Each entry: {index, fingerprint, base_fingerprint, surface_type, area, center}
        The list order matches the OCC TopExp_Explorer face iteration order,
        so face_map[i] corresponds to the i-th face in the STEP file.
        """
        from tools.CAD.step_diff_engine.geometry_fingerprint import fingerprint_face
        face_map = []
        for face in model.faces:
            fp = fingerprint_face(face)
            face_map.append({
                "index": face.index,
                "fingerprint": fp.fingerprint,
                "base_fingerprint": fp.base_fingerprint,
                "surface_type": face.surface_type,
                "area": round(face.area, 6),
                "center": list(face.center),
            })
        return face_map

    def _process_step_compare(
        self,
        *,
        commit_dir: str,
        part_id: int,
        commit_id: str,
        current_step_source_path: str,
        current_commit_label: str,
        previous_commit_hint: str = "prev",
        previous_step_path: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        from tools.CAD.step_diff_engine.step_parser import parse_step_file
        from tools.CAD.step_diff_engine.diff_engine import compare_models
        from tools.CAD.step_diff_engine.database import JsonDiffDatabase

        if not current_step_source_path or not safe_exists(current_step_source_path):
            raise ValueError("STEP file path is invalid or missing.")

        step_store_dir = os.path.join(commit_dir, "_step_artifacts")
        ensure_dir_exists(step_store_dir)

        step_ext = os.path.splitext(current_step_source_path)[1] or ".step"
        copied_step_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}{step_ext}")
        safe_copy2(current_step_source_path, copied_step_path)

        if not previous_step_path or not safe_exists(previous_step_path):
            # BASELINE — no previous STEP to compare, but still build face map
            try:
                baseline_model = parse_step_file(copied_step_path, commit_id=current_commit_label)
                face_map = self._build_face_map(baseline_model)
                face_map_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}_face_map.json")
                with safe_open(face_map_path, "w", encoding="utf-8") as fh:
                    json.dump(face_map, fh, indent=2)
            except Exception:
                face_map_path = None

            return {
                "step_compare_enabled": 1,
                "step_file_path": copied_step_path,
                "step_prev_file_path": previous_step_path,
                "step_diff_path": None,
                "step_diff_summary": json.dumps({"mode": "baseline", "compared_against_commit_id": None}),
                "step_diff_status": "BASELINE",
                "step_error": None,
                "step_face_map_path": face_map_path,
            }

        model_a = parse_step_file(previous_step_path, commit_id=previous_commit_hint)
        model_b = parse_step_file(copied_step_path, commit_id=current_commit_label)
        diff = compare_models(model_a, model_b)

        diff_dir = os.path.join(step_store_dir, "diffs")
        ensure_dir_exists(diff_dir)
        diff_path = os.path.join(diff_dir, f"{part_id}_{commit_id}.json")
        with safe_open(diff_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(diff), handle, indent=2)

        # Save face map for the current model (model_b)
        try:
            face_map = self._build_face_map(model_b)
            face_map_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}_face_map.json")
            with safe_open(face_map_path, "w", encoding="utf-8") as fh:
                json.dump(face_map, fh, indent=2)
        except Exception:
            face_map_path = None

        db = JsonDiffDatabase()
        db.append_comparison(
            model_a=model_a,
            model_b=model_b,
            diff=diff,
            metadata=metadata or {},
        )

        return {
            "step_compare_enabled": 1,
            "step_file_path": copied_step_path,
            "step_prev_file_path": previous_step_path,
            "step_diff_path": diff_path,
            "step_diff_summary": json.dumps(
                {
                    **self._step_summary(diff),
                    "compared_against_commit_id": previous_commit_hint if previous_commit_hint and previous_commit_hint != "prev" else None,
                }
            ),
            "step_diff_status": "COMPARED",
            "step_error": None,
            "step_face_map_path": face_map_path,
        }

    def _safe_attachment_name(self, name: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in os.path.basename(name or ""))
        return cleaned or f"attachment_{uuid.uuid4().hex[:8]}"

    def _normalize_engineering_attachments(self, engineering_attachments) -> list[dict]:
        if not engineering_attachments:
            return []
        rows = []
        if isinstance(engineering_attachments, dict):
            iterable = []
            for part_id, items in engineering_attachments.items():
                for item in items or []:
                    data = dict(item or {})
                    data.setdefault("part_id", part_id)
                    iterable.append(data)
        else:
            iterable = engineering_attachments or []
        for item in iterable:
            try:
                part_id = int(item.get("part_id"))
            except Exception:
                continue
            source_path = item.get("source_path") or item.get("path")
            file_type = str(item.get("file_type") or "").strip().upper()
            if not source_path or not file_type:
                continue
            rows.append({
                "part_id": part_id,
                "file_type": file_type,
                "file_role": item.get("file_role") or item.get("role") or (
                    "exported_pdf" if file_type == "PDF"
                    else "exported_step" if file_type == "STEP"
                    else "validation_doc"
                ),
                "source_path": source_path,
                "filename": item.get("filename") or os.path.basename(source_path),
                "note": item.get("note") or "",
                "revision": item.get("revision") or "",
                "display_name": item.get("display_name") or f"{file_type} attachment",
                "description": item.get("description") or "Attached from Commit page",
            })
        return rows

    def _write_engineering_attachment_manifest(
        self,
        commit_user_dir: str,
        attachments: list[dict],
        allowed_part_ids: set[int],
        commit_id: str,
    ) -> list[dict]:
        if not attachments:
            return []

        root_dir = os.path.join(commit_user_dir, "_engineering_attachments")
        manifest_rows = []
        seen = set()
        for item in attachments:
            part_id = int(item["part_id"])
            if part_id not in allowed_part_ids:
                continue
            source_path = item["source_path"]
            if not source_path or not safe_exists(source_path):
                raise ValueError(f"Commit blocked: engineering attachment is missing: {os.path.basename(source_path or '')}")
            file_type = str(item["file_type"] or "").strip().upper()
            dedupe_key = (part_id, file_type, os.path.abspath(source_path).lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            part_dir = os.path.join(root_dir, f"part_{part_id}")
            ensure_dir_exists(part_dir)
            safe_name = self._safe_attachment_name(item.get("filename") or source_path)
            dest_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
            dest_path = os.path.join(part_dir, dest_name)
            safe_copy2(source_path, dest_path)
            manifest_rows.append({
                "commit_id": commit_id,
                "part_id": part_id,
                "file_type": file_type,
                "file_role": item.get("file_role") or "other",
                "filename": safe_name,
                "stored_rel_path": os.path.relpath(dest_path, commit_user_dir),
                "note": item.get("note") or "",
                "revision": item.get("revision") or "",
                "display_name": item.get("display_name") or f"{file_type} attachment",
                "description": item.get("description") or f"Attached during commit {commit_id}",
            })

        if manifest_rows:
            ensure_dir_exists(root_dir)
            manifest_path = os.path.join(root_dir, "manifest.json")
            with safe_open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump({"commit_id": commit_id, "attachments": manifest_rows}, handle, indent=2)
        return manifest_rows

    @require_permission("commit")
    def commit_file(
        self,
        commit_dir,
        uncommitted_parts,
        designer,
        message,
        title,
        step_compare_enabled: bool = False,
        step_file_path: str | None = None,
        resolved_issue_ids=None,
        resolved_issue_relation_type: str = "solves",
        jira_key: str | None = None,
        jira_url: str | None = None,
        engineering_attachments=None,
    ):

        commit_id = f"commit_{uuid.uuid4().hex[:8]}"

        designer_user = self.user_service.get_user_by_username(designer)
        if not designer_user:
            raise ValueError("Unknown designer user")
        designer_id = designer_user.id

        project_version_label = None
        try:
            proj = self.project_service.get_project_by_id(self.session.project_id) if self.session.project_id else None
            if isinstance(proj, dict):
                lbl = (proj.get("version_label") or "").strip().upper()
                if lbl and lbl.isalpha():
                    project_version_label = lbl
        except Exception:
            project_version_label = None

        if not uncommitted_parts:
            raise ValueError("No files staged for commit.")

        user_dir = os.path.join(commit_dir, designer)
        commit_user_dir = os.path.join(user_dir, f"{title}_{commit_id}")
        uncommitted_bases = [".".join(os.path.basename(f).split(".")[:-1]) for f in uncommitted_parts]
        commit_plan = []

        # Phase 1: preflight every file before creating commit artifacts.
        for filepath in uncommitted_parts:
            print(f"Processing file: {filepath}")
            filename = os.path.basename(filepath)

            if not safe_exists(filepath):
                raise ValueError(f"Commit blocked: source file is missing: {filename}")

            if not is_creo_file(filename):
                raise ValueError(f"Error: {filename} is not a valid Creo file")

            base_f_name = ".".join(filename.split(".")[:-1])
            file_extension = ".".join(base_f_name.split(".")[1:])
            print(f"Base file name: {base_f_name}, Extension: {file_extension}")
            if self.commit_repository.is_duplicate_commit(base_f_name, self.session.project_id):
                raise ValueError(f"Commit already exists for {base_f_name}. Use --force to overwrite.")

            if file_extension.lower() == "drw":
                bom_entry = self.bom_repo.get_by_drawing_file_name_for_commit(
                    base_f_name,
                    self.session.project_id,
                    designer_id,
                )
                if bom_entry:
                    cad_file = bom_entry.base_file_name
                    print(f"Related CAD file for drawing {filename} is {cad_file}")
                    print(f"Uncommitted parts: {uncommitted_bases}")
                    if cad_file not in uncommitted_bases:
                        raise ValueError(f"Error: Cannot Commit drawing without its related CAD,\n Please provide the CAD file {cad_file} related to drawing {filename} in the uncommitted parts.")
                part_type = "Drw"
                print(f"Identified as Drawing file: {filename}")
            else:
                part_type = "Cad"
                bom_entry = self.bom_repo.get_by_base_file_name_for_commit(
                    base_f_name,
                    self.session.project_id,
                    designer_id,
                )

            if not bom_entry:
                if part_type == "Cad":
                    raise ValueError(f"cad404:{filename}")
                elif part_type == "Drw":
                    raise ValueError(f"drw404:{filename}")

            part_id = bom_entry.id
            locked = self.lock_repo.get_by_part(part_id)
            if not locked:
                raise ValueError(f"Commit blocked: {filename} is not checked in.")
            elif locked.user_id != designer_id:
                print(locked.user_id)
                raise ValueError(f"Commit blocked: {filename} is checked in by another user.")

            commit_plan.append({
                "filepath": filepath,
                "filename": filename,
                "dest_path": os.path.join(commit_user_dir, filename),
                "base_f_name": base_f_name,
                "part_type": part_type,
                "part_id": part_id,
            })

        delayed_attachments = self._normalize_engineering_attachments(engineering_attachments)
        step_attachment_by_part = {}
        if bool(step_compare_enabled):
            for attachment in delayed_attachments:
                file_type = str(attachment.get("file_type") or "").strip().upper()
                role = str(attachment.get("file_role") or "").strip()
                if file_type != "STEP" and role != "exported_step":
                    continue
                part_id = int(attachment.get("part_id") or 0)
                if part_id in step_attachment_by_part:
                    raise ValueError(
                        "Commit blocked: multiple STEP files are attached to the same BOM item. "
                        "Keep one STEP per affected item before running comparison."
                    )
                step_attachment_by_part[part_id] = attachment

        inserted_any = False
        try:
            ensure_dir_exists(commit_user_dir)

            # Phase 2: copy/process all files. No DB rows are visible until every staged file succeeds.
            for item in commit_plan:
                safe_copy2(item["filepath"], item["dest_path"])
                print(f"Committed {item['filename']} for approval.")
                print(f"File copied to {item['dest_path']}")

                step_meta = {
                    "step_compare_enabled": 0,
                    "step_file_path": None,
                    "step_prev_file_path": None,
                    "step_diff_path": None,
                    "step_diff_summary": None,
                    "step_diff_status": None,
                    "step_error": None,
                    "step_face_map_path": None,
                }
                step_source_path = step_file_path
                if bool(step_compare_enabled) and item["part_type"] == "Cad":
                    step_source_path = (step_attachment_by_part.get(int(item["part_id"])) or {}).get("source_path")
                if bool(step_compare_enabled) and item["part_type"] == "Cad" and step_source_path:
                    try:
                        previous_step_commit = self.commit_repository.get_latest_step_commit_for_part(
                            int(item["part_id"]),
                            int(self.session.project_id),
                            exclude_commit_id=str(commit_id),
                        )
                        previous_commit_hint = getattr(previous_step_commit, "commit_id", None) or "prev"
                        previous_step_path = getattr(previous_step_commit, "step_file_path", None)
                        step_meta = self._process_step_compare(
                            commit_dir=commit_dir,
                            part_id=int(item["part_id"]),
                            commit_id=str(commit_id),
                            current_step_source_path=step_source_path,
                            current_commit_label=str(commit_id),
                            previous_commit_hint=previous_commit_hint,
                            previous_step_path=previous_step_path,
                            metadata={
                                "project_id": self.session.project_id,
                                "part_id": int(item["part_id"]),
                                "base_file_name": item["base_f_name"],
                                "filename": item["filename"],
                                "commit_id": str(commit_id),
                                "title": title,
                                "designer_id": designer_id,
                            },
                        )
                    except Exception as e:
                        raise ValueError(f"STEP compare failed for {item['filename']}: {str(e)}")
                item["step_meta"] = step_meta

            compared_step_part_ids = {
                int(item["part_id"])
                for item in commit_plan
                if (item.get("step_meta") or {}).get("step_compare_enabled")
            }
            if compared_step_part_ids:
                delayed_attachments = [
                    attachment for attachment in delayed_attachments
                    if not (
                        int(attachment.get("part_id") or 0) in compared_step_part_ids
                        and (
                            str(attachment.get("file_type") or "").strip().upper() == "STEP"
                            or str(attachment.get("file_role") or "").strip() == "exported_step"
                        )
                    )
                ]
            for item in commit_plan:
                step_meta = item.get("step_meta") or {}
                step_path = step_meta.get("step_file_path")
                if (
                    step_meta.get("step_compare_enabled")
                    and item["part_type"] == "Cad"
                    and step_path
                    and safe_exists(step_path)
                ):
                    delayed_attachments.append({
                        "part_id": int(item["part_id"]),
                        "file_type": "STEP",
                        "file_role": "exported_step",
                        "source_path": step_path,
                        "note": "compared",
                        "revision": "",
                        "display_name": f"{item['base_f_name']} STEP",
                        "description": f"STEP captured during commit {commit_id}",
                    })

            self._write_engineering_attachment_manifest(
                commit_user_dir,
                delayed_attachments,
                {int(item["part_id"]) for item in commit_plan},
                commit_id,
            )

            # Phase 3: persist all rows and traceability links.
            for item in commit_plan:
                signature = self.signature_repo.add_signature("commit", designer_id, message)
                step_meta = item.get("step_meta") or {}
                self.commit_repository.insert(
                    item["part_id"],
                    item["part_type"],
                    item["filename"],
                    item["filepath"],
                    item["base_f_name"],
                    designer_id,
                    self.user_id,
                    message,
                    signature,
                    self.session.project_id,
                    title,
                    commit_id,
                    step_compare_enabled=step_meta.get("step_compare_enabled", 0),
                    step_file_path=step_meta.get("step_file_path"),
                    step_prev_file_path=step_meta.get("step_prev_file_path"),
                    step_diff_path=step_meta.get("step_diff_path"),
                    step_diff_summary=step_meta.get("step_diff_summary"),
                    step_diff_status=step_meta.get("step_diff_status"),
                    step_error=step_meta.get("step_error"),
                    step_face_map_path=step_meta.get("step_face_map_path"),
                )
                inserted_any = True

            if project_version_label:
                for item in commit_plan:
                    try:
                        self.bom_repo.set_revision(item["part_id"], project_version_label)
                    except Exception:
                        pass

            self.traceability_service.repo.backfill_commit_groups()
            if resolved_issue_ids:
                self.issue_service.link_to_commit_with_relation(
                    resolved_issue_ids,
                    commit_id,
                    relation_type=resolved_issue_relation_type,
                    note=message,
                )
                if (jira_key or jira_url):
                    for issue_id in resolved_issue_ids:
                        self.traceability_service.link_jira(issue_id, jira_key or "", jira_url or "")
        except Exception as e:
            if inserted_any:
                try:
                    self.commit_repository.hard_delete_by_commit_id(commit_id, self.session.project_id)
                except Exception:
                    pass
            try:
                if safe_isdir(commit_user_dir):
                    safe_rmtree(commit_user_dir)
            except Exception:
                pass
            msg = str(e)
            if msg.startswith(("cad404:", "drw404:", "Error:", "Commit blocked:", "STEP compare failed")):
                raise ValueError(msg)
            raise ValueError(f"Commit failed: {msg}")
        return {
            "commit_id": commit_id,
            "affected_part_ids": sorted({
                int(item["part_id"])
                for item in commit_plan
                if item.get("part_id") is not None
            }),
        }


    def get_commit_history (self):
        project_id = self.session.project_id
        root_project_id = None
        try:
            proj = self.project_service.get_project_by_id(project_id) if project_id else None
            if isinstance(proj, dict):
                root_project_id = proj.get("root_project_id") or proj.get("id")
        except Exception:
            root_project_id = None

        if root_project_id:
            commits = self.commit_repository.get_all_commits_for_root(int(root_project_id))
        else:
            commits = self.commit_repository.get_all_commits(project_id)
        history = []
        for c in commits:
            designer = self.user_service.get_user_by_id(c.designer)
            checker = self.user_service.get_user_by_id(c.checked_by)
            compared_against_commit_id = None
            try:
                summary_obj = json.loads(c.step_diff_summary) if c.step_diff_summary else {}
                if isinstance(summary_obj, dict):
                    compared_against_commit_id = summary_obj.get("compared_against_commit_id")
            except Exception:
                compared_against_commit_id = None
            history.append({
                "id": c.id,
                "status": c.status,
                "filename": c.filename,
                "title": c.title,
                "project_id": c.project_id,
                "part_id": c.part_id,
                "type": c.type,
                "date": c.committed_at,
                "designed_by": designer.username if designer else "Unknown",
                "checked_by": checker.username if checker else "Unknown",
                "message": c.message,
                "commit_id": c.commit_id,
                "step_diff_status": c.step_diff_status,
                "step_diff_path": c.step_diff_path,
                "step_file_path": c.step_file_path,
                "step_prev_file_path": c.step_prev_file_path,
                "step_diff_summary": c.step_diff_summary,
                "compared_against_commit_id": compared_against_commit_id,
            })
        return history
    
    def get_pending_commits(self, own_only=False):
        all_commits = self.commit_repository.get_by_status("Pending", self.session.project_id)
        if own_only:
            commits = [c for c in all_commits if int(c.designer) == self.user_id]
        else:
            commits = all_commits

        commits_list = []
        for c in commits:
            designer = self.user_service.get_user_by_id(c.designer)
            checker = self.user_service.get_user_by_id(c.checked_by)
            commits_list.append({
                "id": c.id,
                "status": c.status,
                "filename": c.filename,
                "date": c.committed_at,
                "designed_by": designer.username if designer else "Unknown",
                "checked_by": checker.username if checker else "Unknown",
                "message": c.message
            })
        return commits_list
    
    def get_pending_commits_grouped(self, project_id, user_id=None, is_designer=False):
        all_commits = self.commit_repository.get_pending_and_validated_for_family(project_id)
       
        if is_designer:
            try:
                user_id_int = int(user_id) if user_id is not None else None
            except Exception:
                user_id_int = None
            if user_id_int is not None:
                def _designer_id(x):
                    try:
                        return int(getattr(x, "designer", None))
                    except Exception:
                        return None

                all_commits = [c for c in all_commits if _designer_id(c) == user_id_int]
        
        grouped = {}
        for c in all_commits:
            key = (c.designer, c.commit_id)
            if key not in grouped:
                grouped[key] = {
                    "designer": c.designer,
                    "status": c.status,
                    "username": c.username,
                    "commit_id": c.commit_id,
                    "project_id": getattr(c, "project_id", None),
                    "title": c.title,
                    "date": c.committed_at,
                    "parts": [],
                    "related_issues": self.issue_service.issues_for_commit(c.commit_id),
                }
            grouped[key]["parts"].append(c.filename)
        return list(grouped.values())

    def get_commit_group_details(self, commit_id: str, project_id: int | None = None) -> dict:
        rows = self.commit_repository.get_rows_by_commit_id(
            str(commit_id),
            int(project_id) if project_id is not None else None,
        )
        if not rows and project_id is not None:
            rows = self.commit_repository.get_rows_by_commit_id(str(commit_id))
        if not rows:
            return {"commit_id": str(commit_id), "files": [], "issues": []}

        first = rows[0]
        status_order = {"Reverted": 5, "Approved": 4, "Validated": 3, "Pending": 2, "Integrated": 1}
        statuses = [str(r.get("status") or "") for r in rows]
        group_status = max(statuses, key=lambda s: status_order.get(s, 0)) if statuses else ""
        issues = self.issue_service.issues_for_commit(str(commit_id))
        for issue in issues:
            try:
                jira_links = self.issue_service.jira_links(int(issue.get("id")))
            except Exception:
                jira_links = []
            issue["jira_links"] = jira_links
            primary_jira = next((link for link in jira_links if link.get("jira_url")), jira_links[0] if jira_links else {})
            issue["jira_key"] = primary_jira.get("jira_key") or ""
            issue["jira_url"] = primary_jira.get("jira_url") or ""
        engineering_files = self.traceability_service.engineering_files_for_commit(str(commit_id))
        for item in engineering_files:
            self._resolve_engineering_file_path(item)
        validation_docs = self.traceability_service.validation_docs_for_commit(str(commit_id))
        for item in validation_docs:
            path = item.get("stored_path") or ""
            item["source_path"] = path
            item["exists"] = bool(path and safe_exists(path))
        if not engineering_files and not validation_docs:
            pending_items = self._pending_commit_attachments_for_commit(rows, str(commit_id))
            engineering_files = [
                item for item in pending_items
                if item.get("file_role") in {"exported_pdf", "exported_step"}
            ]
            validation_docs = [
                item for item in pending_items
                if item.get("file_role") not in {"exported_pdf", "exported_step"}
            ]
        return {
            "commit_id": str(commit_id),
            "title": first.get("title") or "",
            "message": first.get("message") or "",
            "status": group_status,
            "project_id": first.get("project_id"),
            "project_name": first.get("project_name") or "",
            "project_version_label": first.get("project_version_label") or "",
            "root_project_id": first.get("root_project_id"),
            "author": first.get("committed_by_name") or first.get("designer_name") or "",
            "designer": first.get("designer_name") or "",
            "checker": first.get("checked_by_name") or "",
            "committed_at": first.get("committed_at") or "",
            "merged_by": first.get("merged_by_name") or "",
            "merged_at": first.get("merged_at") or "",
            "merge_id": first.get("merge_id") or "",
            "merge_message": first.get("merge_message") or "",
            "approved_version": first.get("approved_version") or "",
            "pr_path": first.get("pr_path") or "",
            "signature": first.get("signature") or "",
            "last_snapshot": first.get("last_snapshot"),
            "snapshotted_in": first.get("snapshotted_in") or "",
            "files": rows,
            "issues": issues,
            "engineering_files": engineering_files,
            "validation_docs": validation_docs,
        }

    def _resolve_engineering_file_path(self, item: dict):
        if item.get("source_path"):
            item["exists"] = bool(safe_exists(item.get("source_path")))
            return
        version_id = item.get("resolved_version_id") or item.get("part_file_version_id")
        path = ""
        if version_id:
            try:
                version = self.part_file_service.repo.get_version_by_id(int(version_id))
                path = self.part_file_service.resolve_version_path(version) if version else ""
            except Exception:
                path = ""
        item["source_path"] = path
        item["exists"] = bool(path and safe_exists(path))

    def _pending_commit_attachments_for_commit(self, rows: list[dict], commit_id: str) -> list[dict]:
        if not rows:
            return []
        first = rows[0]
        title = first.get("title") or ""
        designer = first.get("designer_name") or first.get("committed_by_name") or ""
        project_id = first.get("project_id") or self.session.project_id
        if not title or not designer:
            return []
        try:
            project = self.project_service.get_project_by_id(int(project_id)) if project_id else None
            working_dir = (project or {}).get("working_directory", "") or ""
        except Exception:
            working_dir = ""
        if not working_dir:
            return []
        commit_folder_names = [f"{title}_{commit_id}"]
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
        if safe_title != title:
            commit_folder_names.append(f"{safe_title}_{commit_id}")
        manifest_path = ""
        for folder_name in commit_folder_names:
            candidate = os.path.join(
                working_dir,
                "commits",
                designer,
                folder_name,
                "_engineering_attachments",
                "manifest.json",
            )
            if safe_exists(candidate):
                manifest_path = candidate
                break
        if not safe_exists(manifest_path):
            return []
        try:
            with safe_open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle) or {}
        except Exception:
            return []
        base_dir = os.path.dirname(os.path.dirname(manifest_path))
        result = []
        for idx, item in enumerate(manifest.get("attachments") or [], start=1):
            source_path = os.path.join(base_dir, item.get("stored_rel_path") or "")
            row = dict(item)
            row.update({
                "id": f"pending-{idx}",
                "commit_id": commit_id,
                "file_role": item.get("file_role") or "other",
                "original_filename": item.get("filename"),
                "stored_path": source_path,
                "source_path": source_path,
                "exists": bool(source_path and safe_exists(source_path)),
                "pending": True,
            })
            result.append(row)
        return result

    
    def revert_commit(self, commit_id: int, project_id: int = None):
        return self.traceability_service.mark_commit_reverted(
            str(commit_id),
            int(project_id) if project_id is not None else self.session.project_id,
            "Reverted from Commit page",
        )
    
    @require_permission("validate")
    def validate_commit(self, commit_id: int, project_id: int = None,
                        confirmed_issue_ids=None, rejected_issue_ids=None, validation_comment=""):
        result = self.commit_repository.validate(
            commit_id,
            self.session.user_id,
            int(project_id) if project_id is not None else self.session.project_id,
        )
        self.issue_service.validate_commit_issues(
            str(commit_id), confirmed_issue_ids or [], rejected_issue_ids or [], validation_comment
        )
        return result
