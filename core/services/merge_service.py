#!/usr/bin/env python3
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
from utils import (
    is_creo_file,
    ensure_dir_exists,
    get_version_number,
    get_base_name,
    get_next_version_number
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

        self.working_dir = working_dir
        self.commits_dir = commits_dir
        self.pr_dir = pr_dir

        self.merge_id = f"merge_{uuid.uuid4().hex[:8]}"

    def get_last_approved_version(self, base_name):
        max_version = 0
        for f in os.listdir(self.working_dir):
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

        if not os.path.exists(commit_path):
            print(f"Commit file does not exist: {commit_path}")
            return None
        


        try:
            working_path = os.path.join(self.working_dir, new_filename)
            shutil.copy2(commit_path, working_path)
            pr_path = os.path.join(pr_dir, new_filename)
            shutil.copy2(commit_path, pr_path)

            #debugging info
            print(f"Merged {filename} to working as {new_filename}")
            print(f"Copied {filename} to PR directory as {new_filename}")
            print(f"From: {commit_path}")
            print(f"To Working: {working_path}")
            print(f"To PR: {pr_path}")

            try:
                os.remove(commit_path)

            except Exception as e:
                print(f"Warning: Failed to remove commit file {commit_path}: {e}")

            commit_dir = os.path.dirname(commit_path)
            if os.path.exists(commit_dir) and not os.listdir(commit_dir):
                try:
                    print(f"Removing empty commit directory {commit_dir}")
                    shutil.rmtree(commit_dir)

                except Exception as e:
                    print(f"Warning: Failed to remove empty commit directory {commit_dir}: {e}")
            
            user_dir = os.path.dirname(commit_dir)
            if os.path.exists(user_dir) and not os.listdir(user_dir):
                try:
                    print(f"Removing empty user directory {user_dir}")
                    shutil.rmtree(user_dir)

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
            print(f"Merged {len(merged_entries)} commits.")
            return sorted({int(item["item_id"]) for item in merged_entries if item.get("item_id") is not None})

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


