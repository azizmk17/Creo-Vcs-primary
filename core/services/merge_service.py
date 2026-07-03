#!/usr/bin/env python3
import json
import os
import shutil
from datetime import datetime
import uuid
from core.services.permission_decorators import require_permission
from core.repositories.commit_repository import CommitRepository
from core.repositories.merge_repository import MergeRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.user_repository import UserRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.services.user_service import UserService
from core.services.bom_service import BomService
from core.services.base_service import BaseService
from core.services.issue_service import IssueService
from core.services.traceability_service import TraceabilityService
from core.services.part_file_service import PartFileService
from core.services.export_naming import is_project_revision
from utils import (
    is_creo_file,
    ensure_dir_exists,
    get_version_number,
    get_base_name,
    get_next_version_number,
    safe_copy2,
    safe_exists,
    safe_isdir,
    safe_listdir,
    safe_open,
    safe_remove,
    safe_rmtree,
)
class MergeService(BaseService):
    def __init__(self, working_dir, commits_dir, pr_dir):
        super().__init__()
        self.commit_repository = CommitRepository()
        self.merge_repository = MergeRepository()
        self.bom_repo = BomRepository()
        self.lock_repo = LockRepository()
        self.signature_repo = SignatureRepository()
        self.user_service = UserService(UserRepository())
        self.bom_service = BomService(BomRepository(), BomChildrenRepository(), LockRepository(), SignatureRepository())
        self.issue_service = IssueService()
        self.traceability_service = TraceabilityService()
        self.part_file_service = PartFileService()

        self.working_dir = working_dir
        self.commits_dir = commits_dir
        self.pr_dir = pr_dir

        self.merge_id = f"merge_{uuid.uuid4().hex[:8]}"

    def _commit_group_dir(self, commit) -> str:
        designer = getattr(commit, "designer_username", "") or ""
        title = getattr(commit, "title", "") or ""
        logical_id = getattr(commit, "commit_id", "") or ""
        if designer and title and logical_id:
            return os.path.join(self.commits_dir, designer, f"{title}_{logical_id}")
        return ""

    def _safe_filename(self, name: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in "._- ()" else "_" for ch in os.path.basename(name or ""))
        return cleaned or f"validation_{uuid.uuid4().hex[:8]}"

    def _drawing_index_for_part(self, part) -> str:
        for field_name in (
            "drawing_index",
            "draw_index",
            "drawing_indice",
            "indice",
            "index",
        ):
            value = str(getattr(part, field_name, "") or "").strip()
            if value:
                return value
        return ""

    def _project_version_label(self, project_id: int | None) -> str:
        if not project_id:
            return ""
        try:
            from core.services.project_service import ProjectService

            project = ProjectService().get_project_by_id(int(project_id)) or {}
            return str(project.get("version_label") or "").strip().upper()
        except Exception:
            return ""

    def _legacy_engineering_filename(
        self,
        part_id: int,
        file_role: str,
        file_type: str,
        source_path: str,
        drawing_revision: str = "",
        project_version_label: str = "",
    ) -> str:
        part = self.bom_repo.get_by_id(int(part_id))
        ext = os.path.splitext(source_path or "")[1].lower()
        if not ext:
            ext = ".pdf" if file_role == "exported_pdf" else ".step" if file_role == "exported_step" else ""

        drawing_number = str(getattr(part, "drawing_number", "") or "").strip()
        if not drawing_number:
            drawing_number = str(getattr(part, "base_drw_name", "") or getattr(part, "base_file_name", "") or f"part_{part_id}").strip()
        index = self._drawing_index_for_part(part)
        aes = str(getattr(part, "aes_number", "") or "").strip()
        part_name = str(getattr(part, "name", "") or "").strip()

        parts = [drawing_number, index, aes, part_name]
        if file_role == "exported_pdf":
            rev = str(drawing_revision or "").strip()
            if rev and not is_project_revision(rev, project_version_label):
                parts.append(rev)

        stem = "_".join(
            self._safe_filename(piece).strip("._- ")
            for piece in parts
            if str(piece or "").strip()
        )
        if not stem:
            stem = self._safe_filename(os.path.splitext(os.path.basename(source_path or ""))[0] or f"part_{part_id}")
        return f"{stem}{ext}"

    def _copy_with_legacy_engineering_name(
        self,
        commit_dir: str,
        source_path: str,
        part_id: int,
        file_role: str,
        file_type: str,
        drawing_revision: str = "",
        project_version_label: str = "",
    ) -> str:
        legacy_name = self._legacy_engineering_filename(
            part_id=part_id,
            file_role=file_role,
            file_type=file_type,
            source_path=source_path,
            drawing_revision=drawing_revision,
            project_version_label=project_version_label,
        )
        target_dir = os.path.join(commit_dir, "_engineering_attachments", "_legacy_names")
        ensure_dir_exists(target_dir)
        target = os.path.join(target_dir, legacy_name)
        if safe_exists(target):
            stem, ext = os.path.splitext(legacy_name)
            target = os.path.join(target_dir, f"{stem}_{uuid.uuid4().hex[:8]}{ext}")
        safe_copy2(source_path, target)
        return target

    def _store_validation_doc(self, source_path: str, commit_id: str, filename: str) -> str:
        root = os.path.join(self.working_dir, ".creo_vcs", "validation_docs", str(commit_id))
        ensure_dir_exists(root)
        safe_name = self._safe_filename(filename or source_path)
        destination = os.path.join(root, safe_name)
        if safe_exists(destination):
            stem, ext = os.path.splitext(safe_name)
            destination = os.path.join(root, f"{stem}_{uuid.uuid4().hex[:8]}{ext}")
        safe_copy2(source_path, destination)
        return destination

    def _process_commit_attachments(self, commit_dir: str, commit_id: str, project_id: int | None) -> list[int]:
        if not commit_dir:
            return []
        attachment_dir = os.path.join(commit_dir, "_engineering_attachments")
        manifest_path = os.path.join(attachment_dir, "manifest.json")
        if not safe_exists(manifest_path):
            return []

        with safe_open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle) or {}

        changed_part_ids = []
        linked_issue_ids = self.traceability_service.linked_issue_ids_for_commit(commit_id)
        project_version_label = self._project_version_label(project_id)
        for item in manifest.get("attachments") or []:
            part_id = int(item.get("part_id") or 0)
            source_path = os.path.join(commit_dir, item.get("stored_rel_path") or "")
            if not part_id or not safe_exists(source_path):
                raise ValueError(
                    f"Cannot vault engineering attachment; file is missing: {os.path.basename(source_path)}"
                )
            file_type = str(item.get("file_type") or "").strip().upper()
            file_role = str(item.get("file_role") or "").strip() or (
                "exported_pdf" if file_type == "PDF"
                else "exported_step" if file_type == "STEP"
                else "validation_doc"
            )
            if file_role in {"exported_pdf", "exported_step"}:
                vault_source_path = self._copy_with_legacy_engineering_name(
                    commit_dir=commit_dir,
                    source_path=source_path,
                    part_id=part_id,
                    file_role=file_role,
                    file_type=file_type,
                    drawing_revision=item.get("revision") or "",
                    project_version_label=project_version_label,
                )
                file_id, version_id = self.part_file_service.upsert_part_file_version(
                    part_id=part_id,
                    file_type=file_type,
                    source_path=vault_source_path,
                    note=item.get("note") or "",
                    revision=item.get("revision") or "",
                    display_name=os.path.splitext(os.path.basename(vault_source_path))[0],
                    description=item.get("description") or "Attached during commit push",
                )
                self.traceability_service.link_commit_to_engineering_file(
                    commit_id=commit_id,
                    project_id=project_id,
                    part_id=part_id,
                    part_file_id=int(file_id),
                    version_id=int(version_id) if version_id else None,
                    role=file_role,
                    note=item.get("note") or "",
                )
                for issue_id in linked_issue_ids:
                    self.traceability_service.link_issue_to_engineering_file(
                        int(issue_id),
                        int(file_id),
                        int(version_id) if version_id else None,
                        role=file_role,
                        note=item.get("note") or f"Attached from commit {commit_id}",
                    )
            else:
                stored_path = self._store_validation_doc(
                    source_path,
                    commit_id,
                    item.get("filename") or os.path.basename(source_path),
                )
                validation_doc_id = self.traceability_service.register_validation_doc(
                    commit_id=commit_id,
                    project_id=project_id,
                    part_id=part_id,
                    original_filename=item.get("filename") or os.path.basename(source_path),
                    stored_path=stored_path,
                    file_type=file_type,
                    doc_role=file_role,
                    note=item.get("note") or "",
                )
                for issue_id in linked_issue_ids:
                    self.traceability_service.link_validation_doc_to_issue(
                        int(validation_doc_id),
                        int(issue_id),
                        note=item.get("note") or f"Attached from commit {commit_id}",
                    )
            changed_part_ids.append(part_id)

        try:
            safe_rmtree(attachment_dir)
        except Exception as exc:
            print(f"Warning: Failed to remove temporary engineering attachments {attachment_dir}: {exc}")

        try:
            if safe_isdir(commit_dir) and not safe_listdir(commit_dir):
                safe_rmtree(commit_dir)
                user_dir = os.path.dirname(commit_dir)
                if safe_isdir(user_dir) and not safe_listdir(user_dir):
                    safe_rmtree(user_dir)
        except Exception as exc:
            print(f"Warning: Failed to remove empty commit attachment directory {commit_dir}: {exc}")

        return sorted(set(changed_part_ids))

    def get_last_approved_version(self, base_name):
        max_version = 0
        for f in safe_listdir(self.working_dir):
            if f.startswith(base_name + '.') and is_creo_file(f):
                version = get_version_number(f)
                if version > max_version:
                    max_version = version
        return max_version

    def prepare_merge(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pr_dir = os.path.join(self.pr_dir, f"{self.merge_id}_{timestamp}")
        ensure_dir_exists(pr_dir)
        return pr_dir

    def process_file(self, commit_entry, pr_dir):
        commit_path = os.path.join(self.commits_dir, commit_entry["path"])
        filename = os.path.basename(commit_path)
        base_name = get_base_name(filename)
        if not base_name:
            print(f"Invalid filename format: {filename}")
            return None

        commit_version = get_version_number(filename)
        new_version = get_next_version_number(self.working_dir,base_name)
        new_filename = f"{base_name}.{new_version}"

        #debugging info
        print(f"Processing commit file: {commit_path}")
        print(f"Base name: {base_name}, Commit version: {commit_version}, New version: {new_version}")

        if not safe_exists(commit_path):
            print(f"Commit file does not exist: {commit_path}")
            return None
        


        try:
            working_path = os.path.join(self.working_dir, new_filename)
            safe_copy2(commit_path, working_path)
            pr_path = os.path.join(pr_dir, new_filename)
            safe_copy2(commit_path, pr_path)

            #debugging info
            print(f"Merged {filename} to working as {new_filename}")
            print(f"Copied {filename} to PR directory as {new_filename}")
            print(f"From: {commit_path}")
            print(f"To Working: {working_path}")
            print(f"To PR: {pr_path}")

            try:
                safe_remove(commit_path)

            except Exception as e:
                print(f"Warning: Failed to remove commit file {commit_path}: {e}")

            commit_dir = os.path.dirname(commit_path)
            if safe_exists(commit_dir) and not safe_listdir(commit_dir):
                try:
                    print(f"Removing empty commit directory {commit_dir}")
                    safe_rmtree(commit_dir)

                except Exception as e:
                    print(f"Warning: Failed to remove empty commit directory {commit_dir}: {e}")
            
            user_dir = os.path.dirname(commit_dir)
            if safe_exists(user_dir) and not safe_listdir(user_dir):
                try:
                    print(f"Removing empty user directory {user_dir}")
                    safe_rmtree(user_dir)

                except Exception as e:
                    print(f"Warning: Failed to remove empty user directory {user_dir}: {e}")

            

            return {"new_version" : new_version, "pr_path" : pr_path, "new_filename" : new_filename}

        except Exception as e:
            print(f"Failed to merge {filename}: {str(e)}")
            return None

    # def merge_all(self, approver, message):
    #     pr_dir = self.prepare_merge()
    #     merged_entries = []

    #     for commit in self.repo_data["pending"]:
    #         if commit.get("status") == "merged":
    #             continue

    #         result = self.process_file(commit, pr_dir, approver, message)
    #         if result:
    #             merged_entries.append(result)

    #     if merged_entries:
    #         self.finalize_merge(merged_entries)
    #         return True
    #     return False

    # def merge_user(self, user, approver, message):
    #     pr_dir = self.prepare_merge()
    #     merged_entries = []

    #     for commit in self.repo_data["pending"]:
    #         if (
    #             commit.get("user", "").lower() == user.lower()
    #             and commit.get("status") != "merged"
    #         ):
    #             result = self.process_file(commit, pr_dir, approver, message)
    #             if result:
    #                 merged_entries.append(result)

    #     if merged_entries:
    #         return self.finalize_merge(merged_entries)
            
    #     return False
    
    def merge_parts(self, commit_entry):
        """Merge a single commit entry into working + PR directories."""
        pr_dir = self.prepare_merge()
        result = self.process_file(commit_entry, pr_dir)

        if result:
            # Update database status here (Approved, approver, message, timestamp, etc.)
            # Example: self.merge_repository.update_commit_status(commit_entry["id"], "Approved", approver, message)
            return result

        return None

    @staticmethod
    def _part_ids_for_issue_gate(commits):
        part_ids = []
        for commit in commits:
            if commit.part_id is None:
                label = commit.commit_id or commit.id
                raise ValueError(
                    f"Cannot merge commit {label}. It is not linked to a BOM part."
                )
            part_ids.append(int(commit.part_id))
        return part_ids

    @require_permission("merge")
    def excute_merge(self, selected_ids, message):
        merged_entries = []
        selected_commits = []
        for selected_id in selected_ids:
            commit = self.merge_repository.get_ready_to_merge_by_id(selected_id)
            if commit:
                selected_commits.append(commit)
        if not selected_commits:
            raise ValueError("No validated commits were found for the selected merge items.")
        candidate_parts = self._part_ids_for_issue_gate(selected_commits)
        self.issue_service.assert_no_critical_issues(candidate_parts, operation="merge", include_children=True)

        for commit_id in selected_ids:
            commit = self.merge_repository.get_ready_to_merge_by_id(commit_id)
            if commit:
                file_path = os.path.join(commit.designer_username, commit.filename)
                print(commit)
                print(file_path)
                commit_entry = {
                    "id": commit.id,
                    "part_id": commit.part_id,
                    "filename": commit.filename,
                    "path": file_path,
                    "user": commit.designer_username
                }
                result = self.merge_parts(commit_entry)

                db_processing = {
                    "item_id": commit.part_id,
                    "commit_id": commit.id,
                    "part_type": commit.type,
                    "new_filename": result["new_filename"],
                    "new_version": result["new_version"],
                    "pr_path": result["pr_path"],
                } if result else None
                if result:
                    merged_entries.append(db_processing)
            else:
                print(f"Commit ID {commit_id} not found or not pending.")

        if merged_entries:
            self.finalize_merge(merged_entries, self.user_id, self.merge_id, message)
            print(f"Merged {len(merged_entries)} commits.")
            return sorted({int(item["item_id"]) for item in merged_entries if item.get("item_id") is not None})

        return []
    

    @require_permission("merge")
    def excute_merge_by_commit_id(self, commit_id, message=""):
        merged_entries = []


        #get the ids of the commit from the commit_id
        commit_data = self.merge_repository.get_commit_ids_by_commitid(commit_id)
        if not commit_data:
            raise ValueError(f"No validated commit found for {commit_id}.")
        commit_group_dir = self._commit_group_dir(commit_data[0])
        logical_commit_id = str(getattr(commit_data[0], "commit_id", commit_id) or commit_id)
        logical_project_id = getattr(commit_data[0], "project_id", None)
        self.issue_service.assert_no_critical_issues(
            self._part_ids_for_issue_gate(commit_data), operation="merge", include_children=True
        )
        
        #store them
        selected_ids = [c.id for c in commit_data] 

        for commit_id in selected_ids:
            commit = self.merge_repository.get_ready_to_merge_by_id(commit_id)
            if commit:
                file_path = os.path.join(commit.designer_username, f"{commit.title}_{commit.commit_id}" , commit.filename)
                print(commit)
                print(file_path)
                commit_entry = {
                    "id": commit.id,
                    "part_id": commit.part_id,
                    "filename": commit.filename,
                    "path": file_path,
                    "user": commit.designer_username
                }
                result = self.merge_parts(commit_entry)

                db_processing = {"item_id": commit.part_id, "commit_id": commit.id,"part_type": commit.type,  "new_filename" : result["new_filename"],  "new_version": result["new_version"], "pr_path" : result["pr_path"]} if result else None
                if result:
                    merged_entries.append(db_processing)
            else:
                print(f"Commit ID {commit_id} not found or not pending.")

        if merged_entries:
            self.finalize_merge(merged_entries, self.user_id, self.merge_id, message)
            attachment_part_ids = self._process_commit_attachments(
                commit_group_dir,
                logical_commit_id,
                int(logical_project_id) if logical_project_id is not None else None,
            )
            print(f"Merged {len(merged_entries)} commits.")
            return sorted(
                {
                    int(item["item_id"])
                    for item in merged_entries
                    if item.get("item_id") is not None
                }
                | {int(pid) for pid in attachment_part_ids}
            )

        return []


   

    def finalize_merge(self, merged_entries, merge_user_id, merge_id, message):
        """Finalize the merge by updating database entries."""
        for item in merged_entries:
            print(f"Finalizing merge for part ID {item['item_id']} with commit ID {item['commit_id']} with new filename {item['new_filename']}")

            #update commit status merge_commit(self, part_id, merge_user_id, merge_id,  message, approved_version, pr_path)
            print(f"‼️ DB inserting for COMMIT ID:{item['commit_id']}, NEW VERSION:{item['new_version']}, ITEM PATH: {item['pr_path']}  ")
            self.merge_repository.merge_commit(item['commit_id'], merge_user_id, merge_id, message, item["new_version"], item["pr_path"])

            

            # Update BOM
            part = self.bom_repo.get_by_id(item['item_id'])   # fetch the existing BOM entry
            part_type = item["part_type"]

            if not part:
                print(f"BOM with ID {item['item_id']} not found.")
            else:
                if part_type == "Cad":
                    if hasattr(part, "filename"):
                        setattr(part, "filename", item['new_filename'])
                elif part_type == "Drw":
                    if hasattr(part, "drawing"):
                        setattr(part, "drawing", item['new_filename'])

                self.bom_repo.update(part)                    # reuse the generic update function
                print(f"Updated BOM for part ID {part.id} to new filename {item['new_filename']}")
                

            # Add signature entry
            self.signature_repo.add_signature('Merge', merge_user_id , message)
            print(f"Added signature")

            #checkout lock if exists
            lock = self.lock_repo.get_lock_by_part(item['item_id'])
            if lock:
                self.bom_service.checkout_by_part_id(item['item_id'], lock.user_id)
                print(f"Checked out lock for part ID {item['item_id']} by user ID {lock.user_id}")

        
        print(f"Finalized merge for entries: {merged_entries}")

    def _previous_working_filename(self, base_name: str, approved_version: int) -> str | None:
        previous_version = None
        try:
            names = safe_listdir(self.working_dir)
        except OSError:
            names = []
        for name in names:
            if not name.startswith(base_name + ".") or not is_creo_file(name):
                continue
            version = get_version_number(name)
            if version is None or version >= approved_version:
                continue
            if previous_version is None or version > previous_version:
                previous_version = version
        if previous_version is None:
            return None
        return f"{base_name}.{previous_version}"

    def _commit_restore_plan(self, commit_id: str, project_id: int | None = None) -> list[dict]:
        rows = self.commit_repository.get_rows_by_commit_id(str(commit_id), project_id)
        if not rows and project_id is not None:
            rows = self.commit_repository.get_rows_by_commit_id(str(commit_id))
        if not rows:
            raise ValueError(f"Commit {commit_id} was not found.")

        invalid = sorted({str(r.get("status") or "") for r in rows
                          if str(r.get("status") or "").lower() not in {"approved", "pushed", "released"}})
        if invalid:
            raise ValueError(
                "Restore is only available for commits already pushed to master. "
                f"Current status: {', '.join(invalid)}."
            )

        plan = []
        for row in rows:
            filename = os.path.basename(str(row.get("filename") or ""))
            base_name = get_base_name(filename)
            if not base_name:
                raise ValueError(f"Cannot restore {filename}: invalid Creo filename.")
            try:
                approved_version = int(row.get("approved_version") or 0)
            except Exception:
                approved_version = 0
            if approved_version <= 0:
                raise ValueError(f"Cannot restore {filename}: approved version is missing.")

            approved_filename = f"{base_name}.{approved_version}"
            approved_path = os.path.join(self.working_dir, approved_filename)
            if not safe_exists(approved_path):
                raise ValueError(f"Cannot restore {filename}: approved file is missing:\n{approved_path}")

            previous_filename = self._previous_working_filename(base_name, approved_version)
            if not previous_filename:
                raise ValueError(f"Cannot restore {filename}: no previous working version was found.")
            previous_path = os.path.join(self.working_dir, previous_filename)
            if not safe_exists(previous_path):
                raise ValueError(f"Cannot restore {filename}: previous file is missing:\n{previous_path}")

            part_id = row.get("part_id")
            if part_id is None:
                raise ValueError(f"Cannot restore {filename}: commit row is not linked to a BOM part.")
            part = self.bom_repo.get_by_id(int(part_id))
            if not part:
                raise ValueError(f"Cannot restore {filename}: BOM part {part_id} was not found.")

            part_type = str(row.get("type") or "").lower()
            attr = "drawing" if part_type == "drw" or ".drw." in filename.lower() else "filename"
            current_filename = getattr(part, attr, None)
            if current_filename != approved_filename:
                raise ValueError(
                    f"Cannot restore {filename}: {approved_filename} is no longer the current BOM file. "
                    "Restore the newest related commit first."
                )

            plan.append({
                "row": row,
                "part": part,
                "attr": attr,
                "approved_filename": approved_filename,
                "approved_path": approved_path,
                "previous_filename": previous_filename,
                "previous_path": previous_path,
            })
        return plan

    @require_permission("merge")
    def restore_commit_group(self, commit_id: str, project_id: int | None = None, note: str = "") -> dict:
        """Return the working BOM/files to the state immediately before an approved commit."""
        commit_id = str(commit_id or "").strip()
        if not commit_id:
            raise ValueError("Commit ID is required.")

        plan = self._commit_restore_plan(
            commit_id,
            int(project_id) if project_id is not None else None,
        )

        archive_dir = os.path.join(
            self.working_dir,
            ".creo_vcs",
            "restored_commits",
            commit_id,
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        ensure_dir_exists(archive_dir)

        moved_files = []
        updated_parts = []
        try:
            for item in plan:
                archived_path = os.path.join(archive_dir, item["approved_filename"])
                shutil.move(item["approved_path"], archived_path)
                moved_files.append((archived_path, item["approved_path"]))

            part_updates = {}
            for item in plan:
                part_id = int(item["row"]["part_id"])
                update = part_updates.setdefault(part_id, {"values": {}, "original": {}})
                update["values"][item["attr"]] = item["previous_filename"]
                update["original"].setdefault(item["attr"], getattr(item["part"], item["attr"], None))

            for part_id, update in part_updates.items():
                part = self.bom_repo.get_by_id(part_id)
                if not part:
                    raise ValueError(f"Cannot restore commit: BOM part {part_id} was not found.")
                original_values = {attr: getattr(part, attr, None) for attr in update["values"]}
                for attr, value in update["values"].items():
                    setattr(part, attr, value)
                self.bom_repo.update(part)
                updated_parts.append((part_id, original_values))

            self.traceability_service.mark_commit_reverted(
                commit_id,
                int(project_id) if project_id is not None else None,
                note or "Commit restored from master.",
            )
            reopened = self.issue_service.repo.reopen_for_restored_commit(
                commit_id,
                self.user_id,
                note or "Commit restored from master.",
            )
        except Exception:
            for part_id, original_values in reversed(updated_parts):
                try:
                    part = self.bom_repo.get_by_id(part_id)
                    if not part:
                        continue
                    for attr, original_value in original_values.items():
                        setattr(part, attr, original_value)
                    self.bom_repo.update(part)
                except Exception:
                    pass
            for archived_path, original_path in reversed(moved_files):
                try:
                    if safe_exists(archived_path) and not safe_exists(original_path):
                        shutil.move(archived_path, original_path)
                except Exception:
                    pass
            raise

        return {
            "commit_id": commit_id,
            "restored_files": [
                {
                    "part_id": item["row"].get("part_id"),
                    "from": item["approved_filename"],
                    "to": item["previous_filename"],
                }
                for item in plan
            ],
            "affected_part_ids": sorted({int(item["row"]["part_id"]) for item in plan if item["row"].get("part_id") is not None}),
            "reopened_issues": reopened,
            "archive_dir": archive_dir,
        }


