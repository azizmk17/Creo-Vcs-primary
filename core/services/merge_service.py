#!/usr/bin/env python3
import json
import os
import shutil
import socket
import tempfile
from datetime import datetime
import uuid
from core.services.permission_decorators import require_permission
from core.repositories.commit_repository import CommitRepository
from core.repositories.merge_repository import MergeRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.user_repository import UserRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.project_event_repository import ProjectEventRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.services.user_service import UserService
from core.services.bom_service import BomService
from core.services.base_service import BaseService
from core.services.issue_service import IssueService
from core.services.traceability_service import TraceabilityService
from core.services.part_file_service import PartFileService
from core.services.managed_file_service import ManagedFileService
from core.services.approval_journal_service import ApprovalJournalService
from core.services.export_naming import exported_document_filename
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
    safe_move,
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
        self.managed_file_service = ManagedFileService(
            part_file_service=self.part_file_service
        )

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

    def _project_version_label(self, project_id: int | None) -> str:
        if not project_id:
            return ""
        try:
            from core.services.project_service import ProjectService

            project = ProjectService().get_project_by_id(int(project_id)) or {}
            return str(project.get("version_label") or "").strip().upper()
        except Exception:
            return ""

    def _associated_item_ids_for_cad(self, cad_document_id: int | None) -> list[int]:
        """Return all active EBOM Items affected by a CAD Document approval."""
        if cad_document_id is None:
            return []
        try:
            return [
                int(value)
                for value in self.bom_service.pdm_service.checkout_target_item_ids(
                    int(cad_document_id)
                )
                if value is not None
            ]
        except Exception:
            return []

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

        if part:
            return exported_document_filename(
                part=part,
                file_type=file_type,
                source_path=source_path,
                revision=drawing_revision if file_role == "exported_pdf" else "",
                project_version_label=project_version_label,
                include_date=False,
            )

        return f"part_{part_id}{ext}"

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
                    file_role=(
                        "generated_pdf" if file_role == "exported_pdf"
                        else "generated_step" if file_role == "exported_step"
                        else "document"
                    ),
                    source_kind="generated",
                    source_commit_id=commit_id,
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

        changed_part_ids = sorted(set(changed_part_ids))
        for part_id in changed_part_ids:
            try:
                self.managed_file_service.capture_current_iteration(
                    int(part_id), source_commit_id=commit_id
                )
            except Exception as exc:
                print(f"Warning: Failed to update managed-file manifest for BOM {part_id}: {exc}")
        return changed_part_ids

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
        filename = os.path.basename(str(commit_entry.get("filename") or commit_path))
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
        expected_snapshot_hash = str(commit_entry.get("snapshot_sha256") or "").strip()
        if expected_snapshot_hash:
            from core.services.commit_snapshot_service import _digest

            if _digest(commit_path) != expected_snapshot_hash:
                raise ValueError(f"Frozen submission file changed before approval: {filename}")
        


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

            if not commit_entry.get("preserve_source"):
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

    def _part_ids_for_issue_gate(self, commits):
        part_ids = set()
        for commit in commits:
            cad_document_id = getattr(commit, "cad_document_id", None)
            if cad_document_id is not None:
                try:
                    part_ids.update(
                        int(value)
                        for value in self.bom_service.pdm_service.repo.list_checkout_target_item_ids(
                            int(cad_document_id)
                        )
                    )
                except Exception:
                    pass
            if commit.part_id is None:
                if cad_document_id is not None:
                    continue
                label = commit.commit_id or commit.id
                raise ValueError(
                    f"Cannot merge commit {label}. It is not linked to an EBOM Item or CAD Document."
                )
            part_ids.add(int(commit.part_id))
        return sorted(part_ids)

    def _preflight_submissions(self, commits):
        """Reject incomplete or unavailable submissions before copying any source file."""
        selected_by_submission = {}
        for commit in commits:
            logical_id = str(getattr(commit, "commit_id", "") or "").strip()
            project_id = getattr(commit, "project_id", None)
            if not logical_id or project_id is None:
                raise ValueError("A selected file has no logical submission or project identity.")
            if int(project_id) != int(self.session.project_id):
                raise PermissionError("A submission from another project cannot be approved here.")
            key = (int(project_id), logical_id)
            selected_by_submission.setdefault(key, []).append(commit)

        commits_root = os.path.realpath(self.commits_dir)
        snapshot_records = {}
        snapshot_hashes = {}
        for (project_id, logical_id), selected in selected_by_submission.items():
            rows = self.commit_repository.get_rows_by_commit_id(logical_id, project_id)
            if not rows:
                raise ValueError(f"Submission {logical_id} no longer exists in this project.")
            statuses = {str(row.get("status") or "").casefold() for row in rows}
            if statuses != {"validated"}:
                raise ValueError(
                    f"Submission {logical_id} is not wholly awaiting approval; refresh it before merging."
                )
            required_row_ids = {int(row["id"]) for row in rows}
            selected_row_ids = {int(commit.id) for commit in selected}
            if selected_row_ids != required_row_ids:
                raise ValueError(
                    f"Select every file in submission {logical_id}; partial approval is not allowed."
                )

            from core.services.commit_snapshot_service import CommitSnapshotService

            snapshot_files = CommitSnapshotService(
                self.bom_service.pdm_service.db_name
            ).verify_pending_snapshot(self.commits_dir, project_id, logical_id, rows)
            snapshot_hashes[f"{project_id}:{logical_id}"] = snapshot_files["_snapshot_sha256"]
            for commit in selected:
                snapshot = snapshot_files.get(os.path.basename(str(commit.filename)).casefold())
                if not snapshot:
                    raise ValueError("A selected file is missing from its frozen submission snapshot.")
                snapshot_records[int(commit.id)] = snapshot

            cad_ids = {
                int(commit.cad_document_id)
                for commit in selected
                if getattr(commit, "cad_document_id", None) is not None
            }
            if cad_ids:
                from core.services.cad_structure_sync_service import CadStructureSyncService

                db_name = self.bom_service.pdm_service.db_name
                CadStructureSyncService(db_name).validate_pending_commit(
                    logical_id, project_id, cad_ids
                )

            for commit in selected:
                source = os.path.realpath(os.path.join(
                    self.commits_dir, snapshot_records[int(commit.id)]["path"]
                ))
                try:
                    inside_root = os.path.commonpath((commits_root, source)) == commits_root
                except ValueError:
                    inside_root = False
                if not inside_root or not os.path.isfile(source):
                    raise ValueError(
                        f"A frozen file in submission {logical_id} is missing or outside its snapshot store: "
                        + os.path.basename(str(commit.filename))
                    )
        snapshot_records["_snapshot_sha256_by_submission"] = snapshot_hashes
        return snapshot_records

    def _build_approval_plan(self, journal, commits, snapshot_records):
        """Persist exact target versions and destinations before copying bytes."""
        approval_id = str(journal["approval_id"])
        sources_by_row = {}
        results_by_path = {}
        reserved_versions = {}
        for commit in commits:
            row_id = int(commit.id)
            snapshot = snapshot_records[row_id]
            source_path = str(snapshot["path"])
            filename = os.path.basename(str(commit.filename or ""))
            path_key = self._approval_source_key(source_path, filename)
            sources_by_row[str(row_id)] = {
                "path": source_path,
                "sha256": str(snapshot["sha256"]),
                "filename": filename,
                "cad_document_id": getattr(commit, "cad_document_id", None),
                "part_id": getattr(commit, "part_id", None),
                "project_id": getattr(commit, "project_id", None),
                "commit_id": str(getattr(commit, "commit_id", "") or ""),
                "type": str(getattr(commit, "type", "") or ""),
            }
            if path_key in results_by_path:
                continue
            base_name = get_base_name(filename)
            if not base_name:
                raise ValueError("Cannot approve a staged file with an invalid Creo filename: " + filename)
            version = reserved_versions.get(base_name)
            if version is None:
                version = get_next_version_number(self.working_dir, base_name)
            reserved_versions[base_name] = int(version) + 1
            new_filename = f"{base_name}.{int(version)}"
            pr_path = os.path.join(
                self.pr_dir, "approvals", approval_id, new_filename
            )
            results_by_path[path_key] = {
                "source_path": source_path,
                "source_sha256": str(snapshot["sha256"]),
                "source_filename": filename,
                "new_filename": new_filename,
                "new_version": int(version),
                "pr_path": pr_path,
            }
        return {
            "schema": 1,
            "approval_id": approval_id,
            "merge_id": str(journal["merge_id"]),
            "row_ids": sorted(sources_by_row, key=int),
            "sources_by_row": sources_by_row,
            "results_by_path": results_by_path,
        }

    @staticmethod
    def _approval_source_key(path, filename):
        return (
            os.path.normcase(os.path.normpath(str(path)))
            + "|"
            + os.path.normcase(os.path.basename(str(filename or "")))
        )

    @staticmethod
    def _copy_approval_file(source, target, expected_sha256):
        from core.services.commit_snapshot_service import _digest

        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target):
            if _digest(target) != expected_sha256:
                raise ValueError("An approval destination exists with different file content: " + target)
            return
        fd, temporary = tempfile.mkstemp(prefix=".approval-", dir=os.path.dirname(target))
        os.close(fd)
        try:
            shutil.copy2(source, temporary)
            if _digest(temporary) != expected_sha256:
                raise ValueError("A frozen CAD file changed while approval files were being prepared.")
            try:
                os.link(temporary, target)
            except FileExistsError:
                if _digest(target) != expected_sha256:
                    raise ValueError("An approval destination was concurrently created with different content.")
            except OSError:
                if not os.path.exists(target):
                    os.replace(temporary, target)
            if _digest(target) != expected_sha256:
                raise ValueError("The prepared approval file failed its integrity check.")
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def _stage_approval_files(self, plan):
        from core.services.commit_snapshot_service import _digest

        commits_root = os.path.realpath(self.commits_dir)
        for path_key, result in plan["results_by_path"].items():
            source = os.path.realpath(os.path.join(commits_root, result["source_path"]))
            try:
                inside_root = os.path.commonpath((commits_root, source)) == commits_root
            except ValueError:
                inside_root = False
            if not inside_root or not os.path.isfile(source):
                raise ValueError("A frozen approval source is missing or outside its snapshot store.")
            expected = str(result["source_sha256"])
            if _digest(source) != expected:
                raise ValueError("A frozen approval source failed its hash check.")
            working_path = os.path.join(self.working_dir, result["new_filename"])
            self._copy_approval_file(source, working_path, expected)
            self._copy_approval_file(source, result["pr_path"], expected)

    def _snapshot_records_from_plan(self, journal, commits):
        from core.services.commit_snapshot_service import CommitSnapshotService, _digest

        plan = ApprovalJournalService(self.bom_service.pdm_service.db_name).plan(journal)
        if not plan or plan.get("schema") != 1:
            return None
        current_snapshot = CommitSnapshotService(
            self.bom_service.pdm_service.db_name
        ).snapshot_identity(journal["project_id"], journal["commit_id"])
        if current_snapshot != str(journal["snapshot_sha256"]):
            raise ValueError("The submission snapshot changed after this approval was prepared.")
        current_ids = sorted(str(int(commit.id)) for commit in commits)
        if current_ids != sorted(str(value) for value in plan.get("row_ids") or []):
            raise ValueError("The retry submission rows differ from the saved approval plan.")
        snapshots = {"_snapshot_sha256": str(journal["snapshot_sha256"])}
        commits_root = os.path.realpath(self.commits_dir)
        for commit in commits:
            item = plan.get("sources_by_row", {}).get(str(int(commit.id)))
            if not item or os.path.basename(str(commit.filename)).casefold() != str(item.get("filename") or "").casefold():
                raise ValueError("The retry submission filenames differ from the saved approval plan.")
            for field in ("cad_document_id", "part_id", "project_id"):
                expected = item.get(field)
                actual = getattr(commit, field, None)
                if (int(actual) if actual is not None else None) != (int(expected) if expected is not None else None):
                    raise ValueError("The retry submission identities differ from the saved approval plan.")
            if str(getattr(commit, "commit_id", "") or "") != str(item.get("commit_id") or ""):
                raise ValueError("The retry submission ID differs from the saved approval plan.")
            if str(getattr(commit, "type", "") or "") != str(item.get("type") or ""):
                raise ValueError("The retry submission document types differ from the saved approval plan.")
            source = os.path.realpath(os.path.join(commits_root, str(item.get("path") or "")))
            try:
                inside_root = os.path.commonpath((commits_root, source)) == commits_root
            except ValueError:
                inside_root = False
            if not inside_root or not os.path.isfile(source) or _digest(source) != str(item.get("sha256") or ""):
                raise ValueError("A frozen file required to resume approval is missing or changed.")
            snapshots[int(commit.id)] = {
                "path": str(item["path"]), "sha256": str(item["sha256"])
            }
        return snapshots

    def _approve_submission(self, commit_data, message="", *, process_attachments=False):
        if not commit_data:
            raise ValueError("No validated CAD submission was found.")
        first = commit_data[0]
        logical_id = str(getattr(first, "commit_id", "") or "").strip()
        project_id = int(getattr(first, "project_id", 0) or 0)
        if not logical_id or not project_id:
            raise ValueError("The CAD submission is missing its project or logical ID.")
        if int(self.session.project_id) != project_id:
            raise PermissionError("A submission from another project cannot be approved here.")

        db_name = self.bom_service.pdm_service.db_name
        journal_service = ApprovalJournalService(db_name)
        journal = journal_service.latest(project_id, logical_id)
        plan = None
        snapshot_records = None

        if journal and journal.get("status") != "COMPLETED" and journal.get("plan_json") not in (None, "", "{}"):
            commit_data = self.merge_repository.get_commit_ids_by_commitid(
                logical_id, project_id, include_approved=True
            )
            snapshot_records = self._snapshot_records_from_plan(journal, commit_data)
            plan = journal_service.plan(journal)
            if journal.get("status") == "PREPARING":
                verified = self._preflight_submissions(commit_data)
                key = f"{project_id}:{logical_id}"
                if verified["_snapshot_sha256_by_submission"].get(key) != journal["snapshot_sha256"]:
                    raise ValueError("The frozen submission changed before approval files were prepared.")
                snapshot_records = verified
        elif journal and journal.get("status") == "COMPLETED":
            if all(str(getattr(row, "status", "")).casefold() == "approved" for row in commit_data):
                commit_data = self.merge_repository.get_commit_ids_by_commitid(
                    logical_id, project_id, include_approved=True
                )
                snapshot_records = self._snapshot_records_from_plan(journal, commit_data)
                return self._merge_refresh_payload(self._merge_commit_rows(
                    commit_data,
                    lambda commit: snapshot_records[int(commit.id)]["path"],
                    journal_service.plan(journal)["results_by_path"],
                    snapshot_records_by_row_id=snapshot_records,
                ))
            snapshot_records = self._preflight_submissions(commit_data)
            snapshot_hash = snapshot_records["_snapshot_sha256_by_submission"].get(
                f"{project_id}:{logical_id}"
            )
            if snapshot_hash == journal.get("snapshot_sha256"):
                plan = journal_service.plan(journal)
                return self._merge_refresh_payload(self._merge_commit_rows(
                    commit_data,
                    lambda commit: snapshot_records[int(commit.id)]["path"],
                    plan.get("results_by_path", {}),
                    snapshot_records_by_row_id=snapshot_records,
                ))
            parts = self._part_ids_for_issue_gate(commit_data)
            if parts:
                self.issue_service.assert_no_critical_issues(
                    parts, operation="merge", include_children=True
                )
            journal = journal_service.begin(
                project_id, logical_id, snapshot_hash, self.user_id, message
            )
            plan = journal_service.plan(journal)
            if not plan:
                plan = journal_service.save_plan(
                    journal["approval_id"],
                    self._build_approval_plan(journal, commit_data, snapshot_records),
                )
        else:
            commit_data = self.merge_repository.get_commit_ids_by_commitid(logical_id, project_id)
            if not commit_data:
                raise ValueError(f"No validated commit found for {logical_id}.")
            snapshot_records = self._preflight_submissions(commit_data)
            snapshot_hash = snapshot_records["_snapshot_sha256_by_submission"].get(
                f"{project_id}:{logical_id}"
            )
            if not snapshot_hash:
                raise ValueError("The frozen submission hash is missing.")
            parts = self._part_ids_for_issue_gate(commit_data)
            if parts:
                self.issue_service.assert_no_critical_issues(
                    parts, operation="merge", include_children=True
                )
            journal = journal_service.begin(
                project_id, logical_id, snapshot_hash, self.user_id, message
            )
            plan = journal_service.plan(journal)
            if not plan:
                plan = journal_service.save_plan(
                    journal["approval_id"],
                    self._build_approval_plan(journal, commit_data, snapshot_records),
                )

        self.merge_id = str(journal["merge_id"])
        if not plan:
            plan = journal_service.plan(journal)
        if not plan or not plan.get("results_by_path"):
            raise ValueError("The approval journal has no recoverable file plan.")

        try:
            self._stage_approval_files(plan)
            journal_service.advance(journal["approval_id"], "FILES_READY")
            merged_entries = self._merge_commit_rows(
                commit_data,
                lambda commit: snapshot_records[int(commit.id)]["path"],
                plan["results_by_path"],
                snapshot_records_by_row_id=snapshot_records,
            )
            if len(merged_entries) != len(commit_data):
                raise ValueError("The approval plan did not resolve every submission file.")
            self.finalize_merge(
                merged_entries, int(journal["approver_id"]), self.merge_id,
                str(journal.get("message") or ""), approval_id=journal["approval_id"],
            )
            attachment_part_ids = []
            if process_attachments:
                attachment_part_ids = self._process_commit_attachments(
                    self._commit_group_dir(commit_data[0]), logical_id, project_id
                )
            journal_service.advance(journal["approval_id"], "COMPLETED")
            return self._merge_refresh_payload(
                merged_entries, extra_part_ids=attachment_part_ids
            )
        except Exception as exc:
            journal_service.record_error(journal["approval_id"], exc)
            raise

    @require_permission("merge")
    def excute_merge(self, selected_ids, message):
        selected = []
        for selected_id in dict.fromkeys(selected_ids):
            commit = self.merge_repository.get_ready_to_merge_by_id(selected_id)
            if commit:
                selected.append(commit)
        if not selected:
            raise ValueError("No validated commits were found for the selected merge items.")

        groups = {}
        for commit in selected:
            key = (int(commit.project_id), str(commit.commit_id or ""))
            groups.setdefault(key, []).append(commit)
        affected_parts, affected_cad = set(), set()
        for (project_id, logical_id), selected_group in groups.items():
            complete_group = self.merge_repository.get_commit_ids_by_commitid(
                logical_id, project_id
            )
            if {int(row.id) for row in selected_group} != {int(row.id) for row in complete_group}:
                raise ValueError(f"Select every file in submission {logical_id}; partial approval is not allowed.")
            payload = self._approve_submission(complete_group, message)
            affected_parts.update(payload["affected_part_ids"])
            affected_cad.update(payload["affected_cad_document_ids"])
        return {
            "affected_part_ids": sorted(affected_parts),
            "affected_cad_document_ids": sorted(affected_cad),
        }

    @require_permission("merge")
    def excute_merge_by_commit_id(self, commit_id, message=""):
        logical_id = str(commit_id or "").strip()
        project_id = int(self.session.project_id)
        commit_data = self.merge_repository.get_commit_ids_by_commitid(
            logical_id, project_id
        )
        journal = ApprovalJournalService(
            self.bom_service.pdm_service.db_name
        ).latest(project_id, logical_id)
        if not commit_data and journal:
            commit_data = self.merge_repository.get_commit_ids_by_commitid(
                logical_id, project_id, include_approved=True
            )
        if not commit_data:
            raise ValueError(f"No validated or recoverable submission found for {logical_id}.")
        return self._approve_submission(
            commit_data, message, process_attachments=True
        )

    def _merge_refresh_payload(self, merged_entries, extra_part_ids=None) -> dict:
        part_ids = set()
        cad_document_ids = set()
        for item in merged_entries or []:
            try:
                if item.get("item_id") is not None:
                    part_ids.add(int(item["item_id"]))
            except Exception:
                pass
            try:
                if item.get("cad_document_id") is not None:
                    cad_document_id = int(item["cad_document_id"])
                    cad_document_ids.add(cad_document_id)
                    part_ids.update(
                        int(value)
                        for value in self.bom_service.pdm_service.repo.list_checkout_target_item_ids(
                            cad_document_id
                        )
                    )
            except Exception:
                pass
        for pid in extra_part_ids or []:
            try:
                part_ids.add(int(pid))
            except Exception:
                pass
        return {
            "affected_part_ids": sorted(part_ids),
            "affected_cad_document_ids": sorted(cad_document_ids),
        }


    def _approved_merge_results_for_commit(self, commit_id: str, project_id: int | None = None):
        rows = self.commit_repository.get_rows_by_commit_id(
            str(commit_id),
            int(project_id) if project_id is not None else None,
        )
        results = {}
        for row in rows:
            if str(row.get("status") or "").lower() != "approved":
                continue
            filename = str(row.get("filename") or "")
            approved_version = row.get("approved_version")
            pr_path = str(row.get("pr_path") or "")
            new_filename = os.path.basename(pr_path) if pr_path else ""
            if not new_filename and approved_version:
                base_name = get_base_name(filename)
                if base_name:
                    new_filename = f"{base_name}.{approved_version}"
            if not filename or not new_filename or not approved_version:
                continue
            file_path = os.path.join(
                str(row.get("designer_name") or row.get("committed_by_name") or ""),
                f"{row.get('title') or ''}_{row.get('commit_id') or commit_id}",
                filename,
            )
            results[os.path.normcase(os.path.normpath(file_path))] = {
                "new_filename": new_filename,
                "new_version": approved_version,
                "pr_path": pr_path,
            }
        return results

    def _merge_commit_rows(self, commits, path_builder, existing_results_by_path=None,
                           snapshot_records_by_row_id=None):
        """Merge each staged file once, then apply that result to every linked BOM row."""
        merged_entries = []
        results_by_path = dict(existing_results_by_path or {})

        for commit in commits:
            file_path = path_builder(commit)
            print(commit)
            print(file_path)
            path_key = self._approval_source_key(file_path, commit.filename)

            if path_key not in results_by_path:
                commit_entry = {
                    "id": commit.id,
                    "part_id": commit.part_id,
                    "filename": commit.filename,
                    "path": file_path,
                    "user": commit.designer_username,
                    "snapshot_sha256": (snapshot_records_by_row_id or {}).get(
                        int(commit.id), {}
                    ).get("sha256"),
                    "preserve_source": True,
                }
                results_by_path[path_key] = self.merge_parts(commit_entry)

            result = results_by_path.get(path_key)
            if result:
                merged_entries.append({
                    "item_id": commit.part_id,
                    "cad_document_id": getattr(commit, "cad_document_id", None),
                    "commit_id": commit.id,
                    "source_commit_id": getattr(commit, "commit_id", None),
                    "project_id": getattr(commit, "project_id", None),
                    "part_type": commit.type,
                    # The Creo file version stored on the CAD Document is the
                    # approved/master file suffix created by the merge, not the
                    # transient staged filename uploaded by the designer.
                    "source_file_name": result["new_filename"],
                    "creo_file_version": result["new_version"]
                    or get_version_number(result["new_filename"]),
                    "new_filename": result["new_filename"],
                    "new_version": result["new_version"],
                    "pr_path": result["pr_path"],
                })
            else:
                print(f"Commit row ID {commit.id} could not be merged.")

        return merged_entries

    def finalize_merge(self, merged_entries, merge_user_id, merge_id, message, *, approval_id=None):
        """Finalize the merge by updating database entries."""
        journal_service = (
            ApprovalJournalService(self.bom_service.pdm_service.db_name)
            if approval_id else None
        )
        checkin_sources = {}
        cad_checkin_sources = {}
        pdm_item_checkin_sources = {}
        associated_items_by_cad = {}
        for item in merged_entries:
            part_id = item.get("item_id")
            cad_document_id = item.get("cad_document_id")
            source_commit_id = str(
                item.get("source_commit_id") or item.get("commit_id") or ""
            )
            if cad_document_id is not None:
                cad_checkin_sources.setdefault(
                    int(cad_document_id),
                    {
                        "source_commit_id": source_commit_id,
                        "source_path": item.get("pr_path"),
                        "source_file_name": item.get("source_file_name"),
                        "creo_file_version": item.get("creo_file_version"),
                        "project_id": item.get("project_id"),
                    },
                )
                if part_id is not None:
                    pdm_item_checkin_sources.setdefault(int(part_id), source_commit_id)
                for associated_item_id in self._associated_item_ids_for_cad(
                    cad_document_id
                ):
                    associated_items_by_cad.setdefault(
                        int(cad_document_id), set()
                    ).add(int(associated_item_id))
                    pdm_item_checkin_sources.setdefault(
                        int(associated_item_id), source_commit_id
                    )
            elif part_id is not None:
                checkin_sources.setdefault(int(part_id), source_commit_id)

        from core.services.cad_structure_sync_service import CadStructureSyncService

        cad_ids_by_commit = {}
        for entry in merged_entries:
            commit_id = str(entry.get("source_commit_id") or "").strip()
            cad_document_id = entry.get("cad_document_id")
            if commit_id and cad_document_id is not None:
                project_id = entry.get("project_id")
                if project_id is None:
                    raise ValueError("CAD submission is missing its project identity.")
                cad_ids_by_commit.setdefault(
                    (int(project_id), commit_id), set()
                ).add(int(cad_document_id))

        structure_service = CadStructureSyncService(
            self.bom_service.pdm_service.db_name
        )
        event_repository = ProjectEventRepository(
            self.bom_service.pdm_service.db_name
        )
        approval_key = str(approval_id or merge_id)
        cad_checkouts = {}
        for cad_document_id, payload in cad_checkin_sources.items():
            document = self.bom_service.pdm_service.repo.get_cad_document(
                int(cad_document_id)
            )
            if document and document.get("checked_out_by") is not None:
                cad_checkouts[int(cad_document_id)] = {
                    "document": document,
                    "user_id": int(document["checked_out_by"]),
                    "drawings": (
                        self.bom_service.pdm_service.repo.list_related_drawings(
                            int(cad_document_id)
                        ) or []
                        if str(document.get("category") or "").upper() != "DRAWING"
                        else []
                    ),
                }
        # These database pointers and approval records become visible together.
        # File bytes were already staged and hash-verified before opening this transaction.
        with self.merge_repository.get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            for (project_id, commit_id), cad_ids in cad_ids_by_commit.items():
                structure_service.apply_pending_commit(
                    commit_id, project_id, cad_ids, connection=conn
                )
            for cad_document_id, checkout in cad_checkouts.items():
                payload = cad_checkin_sources[cad_document_id]
                self.bom_service.pdm_service.repo.checkin_cad_document(
                    cad_document_id,
                    checkout["user_id"],
                    str(payload.get("source_path") or ""),
                    message,
                    source_commit_id=payload.get("source_commit_id"),
                    source_file_name=payload.get("source_file_name"),
                    creo_file_version=payload.get("creo_file_version"),
                    connection=conn,
                )
                associated_item_ids = sorted(
                    associated_items_by_cad.get(cad_document_id, set())
                )
                event_repository.emit(
                    payload.get("project_id") or checkout["document"].get("project_id"),
                    checkout["user_id"],
                    "cad.checkin",
                    entity_type="CAD_DOCUMENT",
                    entity_id=cad_document_id,
                    payload={
                        "cad_document_ids": sorted({
                            cad_document_id,
                            *[
                                int(drawing["id"])
                                for drawing in checkout["drawings"]
                                if drawing.get("id") is not None
                            ],
                        }),
                        "item_ids": associated_item_ids,
                    },
                    conn=conn,
                    event_key=f"cad-approval:{approval_key}:checkin:{cad_document_id}",
                )
                workspace_id = str(
                    checkout["document"].get("checkout_workspace_id") or ""
                ).strip()
                if workspace_id:
                    cad_ids_to_release = [cad_document_id]
                    cad_ids_to_release.extend(
                        int(drawing["id"])
                        for drawing in checkout["drawings"]
                        if drawing.get("id") is not None
                    )
                    for release_id in sorted(set(cad_ids_to_release)):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO cad_workspace_release_queue(
                                approval_key,project_id,workspace_id,machine_id,cad_document_id
                            ) VALUES(?,?,?,?,?)
                            """,
                            (
                                approval_key,
                                int(payload.get("project_id") or checkout["document"].get("project_id") or 0),
                                workspace_id,
                                str(
                                    checkout["document"].get("checkout_workspace_machine_id")
                                    or socket.gethostname()
                                ),
                                int(release_id),
                            ),
                        )

            for item in merged_entries:
                print(
                    f"Finalizing merge for part ID {item.get('item_id')} "
                    f"with commit ID {item['commit_id']} and {item['new_filename']}"
                )
                status_updated = self.merge_repository.merge_commit(
                    item['commit_id'], merge_user_id, merge_id, message,
                    item["new_version"], item["pr_path"], conn=conn,
                )
                if not status_updated:
                    raise RuntimeError(
                        f"Commit row {item['commit_id']} was not marked approved."
                    )

                part_id = item.get("item_id")
                part = self.bom_repo.get_by_id(int(part_id)) if part_id is not None else None
                part_type = item["part_type"]
                if part:
                    if part_type == "Cad" and hasattr(part, "filename"):
                        part.filename = item['new_filename']
                    elif part_type == "Drw" and hasattr(part, "drawing"):
                        part.drawing = item['new_filename']
                    self.bom_repo.update(part, conn=conn)

                signature_id = self.signature_repo.add_signature(
                    'Merge', merge_user_id, message,
                    idempotency_key=(f"{approval_id}:{item['commit_id']}" if approval_id else None),
                    conn=conn,
                )
                if int(signature_id or -1) <= 0:
                    raise RuntimeError(
                        f"Could not record the approval signature for commit row {item['commit_id']}."
                    )

            for part_id, source_commit_id in pdm_item_checkin_sources.items():
                if self.bom_service.checked_out_cad_for_item(
                    int(part_id), connection=conn
                ):
                    continue
                lock = self.lock_repo.get_by_part(int(part_id), conn=conn)
                if not lock:
                    continue
                self.bom_service.checkin_by_part_id(
                    int(part_id),
                    int(lock.user_id),
                    note=message,
                    source_commit_id=source_commit_id,
                    exact_item=True,
                    connection=conn,
                    signature_key_prefix=(
                        f"{approval_id}:item" if approval_id else None
                    ),
                )
            for part_id, source_commit_id in checkin_sources.items():
                lock = self.lock_repo.get_by_part(int(part_id), conn=conn)
                if not lock:
                    continue
                self.bom_service.checkin_by_part_id(
                    int(part_id),
                    int(lock.user_id),
                    note=message,
                    source_commit_id=source_commit_id,
                    connection=conn,
                    signature_key_prefix=(
                        f"{approval_id}:item" if approval_id else None
                    ),
                )

            if journal_service:
                journal_service.advance(
                    approval_id, "RECORDS_FINALIZED", conn=conn
                )

        from core.services.cad_workspace_service import CadWorkspaceService

        effects = CadWorkspaceService().process_pending_approval_releases(
            self.bom_service.pdm_service.db_name,
            approval_key=approval_key,
        )
        if effects["failed_ids"]:
            raise RuntimeError(
                "Could not release CAD checkout files in local workspace; "
                f"cleanup is queued for retry (entries: {effects['failed_ids']})."
            )

        # Check in managed CAD Documents. Item locks created only for CAD are
        # released by the PDM service after the last associated CAD closes.
        for cad_document_id, payload in cad_checkin_sources.items():
            try:
                document = self.bom_service.pdm_service.repo.get_cad_document(
                    int(cad_document_id)
                )
                checkout_user_id = (
                    int(document["checked_out_by"])
                    if document and document.get("checked_out_by") is not None
                    else None
                )
                if checkout_user_id is not None:
                    self.bom_service.checkin_pdm_cad_document(
                        int(cad_document_id),
                        str(payload.get("source_path") or ""),
                        message,
                        as_user_id=checkout_user_id,
                        source_commit_id=str(payload.get("source_commit_id") or ""),
                        source_file_name=str(payload.get("source_file_name") or ""),
                        creo_file_version=payload.get("creo_file_version"),
                    )
                    print(f"Checked in CAD Document {cad_document_id} by user ID {checkout_user_id}")
            except Exception:
                raise

        # Capture one object iteration only after every legacy CAD and drawing
        # row is updated. PDM CAD rows are handled above.
        for part_id, source_commit_id in checkin_sources.items():
            try:
                self.managed_file_service.capture_current_iteration(
                    part_id, source_commit_id=source_commit_id
                )
            except Exception as exc:
                print(f"Warning: Failed to capture managed files for BOM {part_id}: {exc}")

        if journal_service:
            journal_service.advance(approval_id, "CHECKINS_COMPLETED")
        
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
                safe_move(item["approved_path"], archived_path)
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
                        safe_move(archived_path, original_path)
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


