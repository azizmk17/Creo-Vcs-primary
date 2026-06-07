#!/usr/bin/env python3
import os
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

from utils import (
    is_creo_file,
    ensure_dir_exists
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

        if not current_step_source_path or not os.path.exists(current_step_source_path):
            raise ValueError("STEP file path is invalid or missing.")

        step_store_dir = os.path.join(commit_dir, "_step_artifacts")
        ensure_dir_exists(step_store_dir)

        step_ext = os.path.splitext(current_step_source_path)[1] or ".step"
        copied_step_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}{step_ext}")
        shutil.copy2(current_step_source_path, copied_step_path)

        if not previous_step_path or not os.path.exists(previous_step_path):
            # BASELINE — no previous STEP to compare, but still build face map
            try:
                baseline_model = parse_step_file(copied_step_path, commit_id=current_commit_label)
                face_map = self._build_face_map(baseline_model)
                face_map_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}_face_map.json")
                with open(face_map_path, "w", encoding="utf-8") as fh:
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
        with open(diff_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(diff), handle, indent=2)

        # Save face map for the current model (model_b)
        try:
            face_map = self._build_face_map(model_b)
            face_map_path = os.path.join(step_store_dir, f"{part_id}_{commit_id}_face_map.json")
            with open(face_map_path, "w", encoding="utf-8") as fh:
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
    ):

        #base filename list
        base_uncommitted = [os.path.basename(f) for f in uncommitted_parts]

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

        for filepath in uncommitted_parts:
            print(f"Processing file: {filepath}")
            filename = os.path.basename(filepath)

            if not is_creo_file(filename):
                raise ValueError(f"Error: {filename} is not a valid Creo file")
            

            user_dir = os.path.join(commit_dir, designer)
            commit_user_dir = os.path.join(user_dir, f"{title}_{commit_id}")
            ensure_dir_exists(commit_user_dir)
            dest_path = os.path.join(commit_user_dir, filename)

            # Check for duplicate (regardless of user)
            base_f_name = ".".join(filename.split(".")[:-1])
            file_extension = ".".join(base_f_name.split(".")[1:])
            print(f"Base file name: {base_f_name}, Extension: {file_extension}")
            if self.commit_repository.is_duplicate_commit(base_f_name, self.session.project_id):
                raise ValueError(f"Commit already exists for {base_f_name}. Use --force to overwrite.")
                
            if file_extension.lower() == "drw":
                #get related part from bom
                bom_entry = self.bom_repo.get_by_drawing_file_name_for_commit(
                    base_f_name,
                    self.session.project_id,
                    designer_id,
                )
                # check if the cad of the drawing is in the uncomiitted list
                if bom_entry:
                    cad_file = bom_entry.base_file_name
                    print(f"Related CAD file for drawing {filename} is {cad_file}")
                    uncommitted_bases = [".".join(os.path.basename(f).split(".")[:-1]) for f in uncommitted_parts]
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
                    # raise ValueError(f"Error: No BOM item found for {base_f_name}")
                    raise ValueError(f"cad404:{filename}")
                elif part_type == "Drw":
                    # raise ValueError(f"Error: No BOM item found for {base_f_name}")
                    raise ValueError(f"drw404:{filename}")
            else:
                part_id = bom_entry.id
                # Check if part is locked by designer
                locked = self.lock_repo.get_by_part(part_id)
                if not locked:
                    raise ValueError("Part is not checked in.")
                elif locked.user_id != designer_id: 
                    print(locked.user_id)
                    raise ValueError("Part is checked in by another user.")
                
                try:
                    
                        #perform commit
                        # Copy file to commits directory
                        shutil.copy2(filepath, dest_path)
                        
                        print(f"Committed {filename} for approval.")
                        print(f"File copied to {dest_path}")
                        signature = self.signature_repo.add_signature("commit", designer_id, message)
                        step_meta = {
                            "step_compare_enabled": 0,
                            "step_file_path": None,
                            "step_prev_file_path": None,
                            "step_diff_path": None,
                            "step_diff_summary": None,
                            "step_diff_status": None,
                            "step_error": None,
                        }

                        if bool(step_compare_enabled) and part_type == "Cad" and step_file_path:
                            try:
                                previous_step_commit = self.commit_repository.get_latest_step_commit_for_base_name_family(
                                    str(base_f_name),
                                    int(self.session.project_id),
                                    exclude_commit_id=str(commit_id),
                                )
                                previous_commit_hint = (
                                    getattr(previous_step_commit, "commit_id", None) or "prev"
                                )
                                previous_step_path = getattr(previous_step_commit, "step_file_path", None)

                                step_meta = self._process_step_compare(
                                    commit_dir=commit_dir,
                                    part_id=int(part_id),
                                    commit_id=str(commit_id),
                                    current_step_source_path=step_file_path,
                                    current_commit_label=str(commit_id),
                                    previous_commit_hint=previous_commit_hint,
                                    previous_step_path=previous_step_path,
                                    metadata={
                                        "project_id": self.session.project_id,
                                        "part_id": int(part_id),
                                        "base_file_name": base_f_name,
                                        "filename": filename,
                                        "commit_id": str(commit_id),
                                        "title": title,
                                        "designer_id": designer_id,
                                    },
                                )
                            except Exception as e:
                                raise ValueError(f"STEP compare failed for {filename}: {str(e)}")

                        self.commit_repository.insert(
                            part_id,
                            part_type,
                            filename,
                            filepath,
                            base_f_name,
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

                        # PLM-lite: part revision follows project version only when the part is committed.
                        if project_version_label:
                            try:
                                self.bom_repo.set_revision(part_id, project_version_label)
                            except Exception:
                                pass
                    
                except Exception as e:
                    raise ValueError(f"Commit failed: {str(e)}")
        if resolved_issue_ids:
            self.issue_service.link_to_commit(resolved_issue_ids, commit_id, message)
        return commit_id


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

    
    def revert_commit(self, commit_id: int, project_id: int = None):
        return self.commit_repository.delete(
            commit_id,
            int(project_id) if project_id is not None else self.session.project_id,
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
