from typing import List, Dict
from collections import defaultdict
import os
import sqlite3
from datetime import datetime
from core.models.bom_model import Bom
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.signature_repository import SignatureRepository
from core.repositories.permission_repository import PermissionRepository
from core.repositories.bom_folder_repository import BomFolderRepository
from core.repositories.bom_filter_repository import BomFilterRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.session_manager import SessionManager
from config import DB_NAME

from core.services.base_service import BaseService


class BomService(BaseService):
    def __init__(self, bom_repo: BomRepository, children_repo: BomChildrenRepository, lock_repo: LockRepository, signature_repo: SignatureRepository):
        super().__init__()
        self.bom_repo = bom_repo
        self.children_repo = children_repo
        self.lock_repo = lock_repo
        self.signature_repo = signature_repo
        self.permission_repo = PermissionRepository()
        self.folder_repo = BomFolderRepository()
        self.filter_repo = BomFilterRepository()
        self.revision_repo = BomRevisionRepository()
        self.session = SessionManager()
        self._tree_cache: dict = {}    # project_id -> tree dict
        self._tree_dirty: set = set()  # project_ids that need re-fetch
        self._lazy_index_cache: dict = {}
        print(self.user_id)

    def _assert_checked_out_for_change(self, part_id: int, action: str = "modify this item"):
        """Require a mutable revision and an owned (or administratively controlled) lock."""
        context = self.revision_repo.assert_checkout_mutable(int(part_id))
        lock = self.lock_repo.get_by_part(int(part_id))
        if not lock:
            raise ValueError(f"Check out {context['version_label']} before you {action}.")
        actor = int(self.user_id) if self.user_id is not None else None
        if actor is None:
            raise PermissionError("You must be logged in.")
        if int(lock.user_id) != actor and not self.permission_repo.user_has_permission(
            actor, "merge", self.session.project_id
        ):
            raise ValueError("This item is checked out by another user.")
        return context

    # -------------------------------
    # SAVED BOM FILTERS
    # -------------------------------
    def _saved_filter_context(self) -> tuple[int, int]:
        if not self.session.project_id:
            raise ValueError("Select a project before managing saved filters.")
        if not self.user_id:
            raise PermissionError("You must be logged in to manage saved filters.")
        return int(self.session.project_id), int(self.user_id)

    def list_saved_bom_filters(self) -> List[Dict]:
        project_id, user_id = self._saved_filter_context()
        return self.filter_repo.list_visible(project_id, user_id)

    def get_saved_bom_filter(self, filter_id: int) -> Dict:
        project_id, user_id = self._saved_filter_context()
        return self.filter_repo.get_visible(project_id, user_id, int(filter_id))

    def create_saved_bom_filter(self, name: str, definition: dict, is_shared: bool = False) -> Dict:
        project_id, user_id = self._saved_filter_context()
        return self.filter_repo.create(project_id, user_id, name, definition, is_shared)

    def update_saved_bom_filter(self, filter_id: int, **changes) -> Dict:
        project_id, user_id = self._saved_filter_context()
        allowed = {key: changes[key] for key in ("name", "definition", "is_shared") if key in changes}
        return self.filter_repo.update_owned(project_id, user_id, int(filter_id), **allowed)

    def delete_saved_bom_filter(self, filter_id: int) -> None:
        project_id, user_id = self._saved_filter_context()
        self.filter_repo.delete_owned(project_id, user_id, int(filter_id))

    def move_saved_bom_filter(self, filter_id: int, direction: int) -> None:
        project_id, user_id = self._saved_filter_context()
        self.filter_repo.move_owned(project_id, user_id, int(filter_id), int(direction))

    def duplicate_saved_bom_filter(self, filter_id: int, name: str, is_shared: bool = False) -> Dict:
        source = self.get_saved_bom_filter(filter_id)
        return self.create_saved_bom_filter(name, source.get("definition") or {}, is_shared)

    # -------------------------------
    # ORGANIZATIONAL FOLDERS
    # -------------------------------
    def list_bom_folders(self) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.folder_repo.list_for_project(int(self.session.project_id))

    def create_bom_folder(self, name: str, parent_bom_id=None, parent_folder_id=None) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before creating a folder.")
        result = self.folder_repo.create(
            int(self.session.project_id), name, self.user_id,
            parent_bom_id=parent_bom_id, parent_folder_id=parent_folder_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def rename_bom_folder(self, folder_id: int, name: str) -> Dict:
        result = self.folder_repo.rename(int(self.session.project_id), int(folder_id), name)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def eligible_bom_folder_items(self, folder_id: int) -> List[Dict]:
        return self.folder_repo.eligible_items(int(self.session.project_id), int(folder_id))

    def set_bom_folder_items(self, folder_id: int, bom_ids) -> List[int]:
        result = self.folder_repo.set_items(
            int(self.session.project_id), int(folder_id), bom_ids, self.user_id
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def delete_bom_folder(self, folder_id: int) -> List[int]:
        result = self.folder_repo.delete(int(self.session.project_id), int(folder_id))
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # INSERT PART / ASM
    # -------------------------------
    def add_part(self, part_data: dict) -> int:
        aes_number = part_data.get("aes_number") or part_data.get("id")
        
        existing = self.bom_repo.get_by_aes(aes_number, self.session.project_id)
        if existing:
            return "existing"
        
        if part_data.get("filename"):
            filename = os.path.basename(part_data.get("filename"))
            base_f_name = ".".join(filename.split(".")[:-1])
        else:
            filename = None
            base_f_name = None

        if part_data.get("drawing"):
            drawing_filename = os.path.basename(part_data.get("drawing"))
            base_drw_name = ".".join(drawing_filename.split(".")[:-1])
        else:
            drawing_filename = None
            base_drw_name = None

        bom_item = Bom(
            id=None,
            type=part_data.get("type", "prt"),
            name=part_data.get("name", "Unnamed"),
            part_number=part_data.get("part_number"),
            drawing_number=part_data.get("drawing_number"),
            aes_number=aes_number,
            filename=filename,
            base_file_name=base_f_name,
            drawing=drawing_filename,
            base_drw_name=base_drw_name,
            material=part_data.get("material"),
            weight=part_data.get("weight"),
            notes=part_data.get("notes"),
            pdf_path=part_data.get("pdf_path"),
            step_path=part_data.get("step_path"),
            status=part_data.get("status", "Design"),
            created=part_data.get("created"),
            modified=part_data.get("modified"),
            project_id=self.session.project_id
        )
        result = self.bom_repo.insert(bom_item)
        if isinstance(result, int):
            self.revision_repo.ensure_bom(int(result), created_by=self.user_id)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # UPDATE PART
    # -------------------------------
    def update_part(self, part_id: str, part_data: dict) -> bool:
        """
        Update an existing part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return False
        self._assert_checked_out_for_change(int(part.id), "edit its attributes")
            
        # Update part fields
        for key, value in part_data.items():
            if hasattr(part, key):
                setattr(part, key, value)
                
        result = self.bom_repo.update(part)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # DELETE PART
    # -------------------------------
    def delete_part(self, part_id: str) -> bool:
        """
        Delete a part by AES number
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return False
        context = self.revision_repo.get_current_context(int(part.id))
        if str(context.get("state") or "").strip().lower() == "released":
            raise ValueError(
                "Released BOM items cannot be deleted. Create or obsolete a controlled revision instead."
            )
            
        # First remove any child relationships
        self.children_repo.delete_by_parent(part.id)
        self.children_repo.delete_by_child(part.id)
        
        # Then delete the part
        result = self.bom_repo.delete(part.id)
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # ADD CHILD RELATIONSHIP
    # -------------------------------
    
    def add_child_by_id(self, parent_id: int, child_id: int):
        """
        Add a child under a parent by their IDs. Skip if child has underscore.
        """
        parent = self.bom_repo.get_by_id(parent_id)
        child = self.bom_repo.get_by_id(child_id)
        if not parent or not child:
            return -1
        self._assert_checked_out_for_change(int(parent.id), "change its structure")

        result = self.children_repo.insert(parent.id, child.id)
        self.revision_repo.ensure_bom(int(child.id), created_by=self.user_id)
        self.revision_repo.sync_working_bindings(int(parent.id), int(self.user_id))
        self._tree_dirty.add(int(self.session.project_id))
        return result
    
    # -------------------------------
    def _parts_sharing_base_file(self, part) -> List:
        base = str(getattr(part, "base_file_name", "") or "").strip()
        project_id = getattr(part, "project_id", None) or self.session.project_id
        if not base or not project_id:
            return [part]
        try:
            related = self.bom_repo.get_all_by_base_file_name_for_commit(base, int(project_id), self.session.user_id) or []
        except Exception:
            related = []
        return related or [part]

    def part_ids_sharing_base_file(self, part_id: int) -> List[int]:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            return []
        return [int(p.id) for p in self._parts_sharing_base_file(part) if getattr(p, "id", None) is not None]

    # CHECKIN PART
    # -------------------------------
    def checkin_part(
        self,
        part_id: str,
        as_user_id: int | None = None,
        note: str = "",
        source_commit_id: str | None = None,
    ):
        if not source_commit_id:
            raise ValueError(
                "Check-in is created only by committing the checked-out item. Use Undo Checkout to discard work."
            )
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        checkin_log_ids = {}
        for related in related_parts:
            self.revision_repo.assert_checkout_mutable(int(related.id))
        locked_by_part = {
            int(p.id): self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        }
        active_locks = [lock for lock in locked_by_part.values() if lock]
        if not active_locks:
            raise ValueError("Part is not checked out.")
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        other_locks = [lock for lock in active_locks if int(lock.user_id) != actor_user_id]
        if other_locks:
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise ValueError("Part is checked out by another user.")
            effective_user_id = int(as_user_id) if as_user_id is not None else int(actor_user_id)
        else:
            effective_user_id = int(actor_user_id)

        for related in related_parts:
            related_id = int(related.id)
            if not locked_by_part.get(related_id):
                self.bom_repo.checkin_bom(related_id)
                continue
            signature = self.signature_repo.add_signature(
                "checkin",
                effective_user_id,
                note=str(note or "").strip() or (
                    "Checked in shared CAD family part" if len(related_parts) > 1 else "Checked in part"
                ),
            )
            log_id = self.lock_repo.checkin(related_id, effective_user_id, signature)
            if not log_id:
                raise ValueError("Failed to check in part")
            checkin_log_ids[related_id] = int(log_id)
            self.bom_repo.checkin_bom(related_id)
        for related in related_parts:
            related_id = int(related.id)
            context = self.revision_repo.record_checkin(
                related_id,
                effective_user_id,
                note=note,
                source_commit_id=source_commit_id,
            )
            log_id = checkin_log_ids.get(related_id)
            iteration_id = context.get("current_iteration_id")
            if log_id and iteration_id is not None:
                self.lock_repo.set_log_object_iteration(log_id, int(iteration_id))
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_part(
        self,
        part_id: str,
        as_user_id: int | None = None,
        released_revision_code: str | None = None,
    ):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        contexts = {}
        for related in related_parts:
            related_id = int(related.id)
            context = self.revision_repo.get_current_context(related_id)
            contexts[related_id] = context
            state = str(context.get("state") or "").strip().lower()
            if state == "released":
                if not str(released_revision_code or "").strip():
                    raise ValueError(
                        f"{context['version_label']} is Released. Enter the revision to create on commit."
                    )
                self.revision_repo.validate_released_checkout(
                    related_id, str(released_revision_code)
                )
            else:
                self.revision_repo.assert_mutable(related_id)
        existing_locks = [
            self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        ]
        if any(existing_locks):
            raise ValueError("Part is already checked out.")
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        if as_user_id is not None and int(as_user_id) != int(actor_user_id):
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise PermissionError("Only Master/Admin can check out as another user")
            effective_user_id = int(as_user_id)
        else:
            effective_user_id = int(actor_user_id)

        for related in related_parts:
            related_id = int(related.id)
            released_checkout = (
                str(contexts[related_id].get("state") or "").strip().lower() == "released"
            )
            signature = self.signature_repo.add_signature(
                "checkout",
                effective_user_id,
                note=(
                    f"Checked out Released item for revision {released_revision_code}"
                    if released_checkout else
                    ("Checked out shared CAD family part" if len(related_parts) > 1 else "Checked out part")
                ),
            )
            success = self.lock_repo.checkout(
                related_id,
                effective_user_id,
                signature,
                object_iteration_id=contexts[related_id].get("current_iteration_id"),
            )
            if not success:
                raise ValueError("Failed to check out part")
            self.bom_repo.checkout_bom(related_id)
            if released_checkout:
                self.revision_repo.prepare_released_checkout(
                    related_id, str(released_revision_code)
                )
            self.revision_repo.initialize_checkout(related_id, effective_user_id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkin_by_part_id(
        self,
        part_id: int,
        user_id: int,
        note: str = "",
        source_commit_id: str | None = None,
    ):
        if not source_commit_id:
            raise ValueError("A commit reference is required to check in an item.")
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        checkin_log_ids = {}
        for related in related_parts:
            self.revision_repo.assert_checkout_mutable(int(related.id))
        locked_by_part = {
            int(p.id): self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        }
        if not any(locked_by_part.values()):
            raise ValueError("Part is not checked out.")

        for related in related_parts:
            related_id = int(related.id)
            if not locked_by_part.get(related_id):
                self.bom_repo.checkin_bom(related_id)
                continue
            signature = self.signature_repo.add_signature(
                "checkin",
                user_id,
                note=str(note or "").strip() or (
                    "Checked in shared CAD family part" if len(related_parts) > 1 else "Checked in part"
                ),
            )
            log_id = self.lock_repo.checkin(related_id, user_id, signature)
            if not log_id:
                raise ValueError("Failed to check in part")
            checkin_log_ids[related_id] = int(log_id)
            self.bom_repo.checkin_bom(related_id)
        for related in related_parts:
            related_id = int(related.id)
            context = self.revision_repo.record_checkin(
                related_id, int(user_id), note=note, source_commit_id=source_commit_id
            )
            log_id = checkin_log_ids.get(related_id)
            iteration_id = context.get("current_iteration_id")
            if log_id and iteration_id is not None:
                self.lock_repo.set_log_object_iteration(log_id, int(iteration_id))
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def analyze_checkout(self, part_id: int) -> Dict:
        from core.services.checkout_change_service import CheckoutChangeService

        return CheckoutChangeService().analyze(int(part_id))

    def checkin_non_cad_changes(self, part_id: int, note: str) -> Dict:
        """Finish a checkout when only controlled object data changed."""
        from core.services.checkout_change_service import CheckoutChangeService
        from core.services.managed_file_service import ManagedFileService

        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        note = str(note or "").strip()
        if not note:
            raise ValueError("A check-in comment is required.")

        related_parts = self._parts_sharing_base_file(part)
        locks = {
            int(related.id): self.lock_repo.get_by_part(int(related.id))
            for related in related_parts
        }
        active_ids = [part_id for part_id, lock in locks.items() if lock]
        if int(part_id) not in active_ids:
            raise ValueError("The selected item is not checked out.")

        actor = int(self.user_id) if self.user_id is not None else None
        if actor is None:
            raise PermissionError("You must be logged in.")
        for lock in (locks[related_id] for related_id in active_ids):
            if int(lock.user_id) != actor and not self.permission_repo.user_has_permission(
                actor, "merge", self.session.project_id
            ):
                raise ValueError("This item is checked out by another user.")

        analyzer = CheckoutChangeService()
        analyses = {related_id: analyzer.analyze(related_id) for related_id in active_ids}
        selected = analyses[int(part_id)]
        if selected.get("requires_commit"):
            raise ValueError("Native CAD or drawing content changed. Continue through Commit.")
        if selected.get("structure_requires_cad"):
            raise ValueError(
                "The assembly structure changed without an updated native assembly file. "
                "Update the assembly in Creo before check-in."
            )
        if not selected.get("has_non_cad_changes"):
            raise ValueError("No metadata, document, or structure changes were detected.")

        for analysis in analyses.values():
            if analysis.get("requires_commit"):
                raise ValueError(
                    "A shared CAD-family item has modified native content. Continue through Commit."
                )
            if analysis.get("structure_requires_cad"):
                raise ValueError(
                    "A shared CAD-family assembly has structure changes without an updated native file."
                )

        managed_files = ManagedFileService()
        result_context = None
        affected_ids = []
        for related_id in active_ids:
            lock = locks[related_id]
            analysis = analyses[related_id]
            previous_iteration_id = int(analysis["current_iteration_id"])
            if analysis.get("has_non_cad_changes"):
                context = self.revision_repo.record_checkin(
                    related_id,
                    int(lock.user_id),
                    note=note,
                    source_commit_id=None,
                )
                managed_files.capture_iteration(
                    related_id,
                    int(context["current_iteration_id"]),
                    inherit_from_iteration_id=previous_iteration_id,
                )
                signature = self.signature_repo.add_signature(
                    "checkin", int(lock.user_id), note=note
                )
                log_id = self.lock_repo.checkin(
                    related_id,
                    int(lock.user_id),
                    signature,
                    object_iteration_id=int(context["current_iteration_id"]),
                )
                if not log_id:
                    raise ValueError("Failed to release the checkout after creating the iteration.")
                if related_id == int(part_id):
                    result_context = context
            else:
                context = self.revision_repo.restore_checked_in_state(related_id)
                signature = self.signature_repo.add_signature(
                    "undo_checkout",
                    int(lock.user_id),
                    note="Shared CAD-family checkout released without BOM-object changes",
                )
                self.lock_repo.undo_checkout(
                    related_id,
                    int(lock.user_id),
                    signature,
                    object_iteration_id=context.get("current_iteration_id"),
                )
            self.bom_repo.checkin_bom(related_id)
            affected_ids.append(related_id)

        self._tree_dirty.add(int(self.session.project_id))
        return {
            "context": result_context or self.revision_repo.get_current_context(int(part_id)),
            "affected_part_ids": sorted(set(affected_ids)),
            "analysis": selected,
        }

    def undo_checkout(self, part_id: int, as_user_id: int | None = None) -> bool:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        locks = {
            int(related.id): self.lock_repo.get_by_part(int(related.id))
            for related in related_parts
        }
        active = [lock for lock in locks.values() if lock]
        if not active:
            raise ValueError("Part is not checked out.")
        actor = int(self.user_id) if self.user_id is not None else None
        if actor is None:
            raise PermissionError("You must be logged in.")
        for lock in active:
            if int(lock.user_id) != actor and not self.permission_repo.user_has_permission(
                actor, "merge", self.session.project_id
            ):
                raise ValueError("Part is checked out by another user.")
        effective_user_id = int(as_user_id) if as_user_id is not None else actor

        # Restore first. If restoration fails, keep the lock so no partial working
        # configuration is exposed as checked in.
        restored_contexts = {}
        for related in related_parts:
            related_id = int(related.id)
            restored_contexts[related_id] = self.revision_repo.restore_checked_in_state(related_id)
        for related in related_parts:
            related_id = int(related.id)
            lock = locks.get(related_id)
            if not lock:
                self.bom_repo.checkin_bom(related_id)
                continue
            signature = self.signature_repo.add_signature(
                "undo_checkout", effective_user_id, note="Checkout undone; working changes discarded"
            )
            self.lock_repo.undo_checkout(
                related_id,
                effective_user_id,
                signature,
                object_iteration_id=restored_contexts.get(related_id, {}).get(
                    "current_iteration_id"
                ),
            )
            self.bom_repo.checkin_bom(related_id)
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_by_part_id(self, part_id: int, user_id: int):
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        for related in self._parts_sharing_base_file(part):
            context = self.revision_repo.get_current_context(int(related.id))
            released = str(context.get("state") or "").strip().lower() == "released"
            target_revision = None
            if released:
                target_revision = self.revision_repo.suggest_next_revision_code(
                    str(context.get("revision_code") or "A")
                )
                self.revision_repo.validate_released_checkout(int(related.id), target_revision)
            else:
                self.revision_repo.assert_mutable(int(related.id))
            signature = self.signature_repo.add_signature("checkout", int(user_id), note="Checked out part")
            if not self.lock_repo.checkout(
                int(related.id),
                int(user_id),
                signature,
                object_iteration_id=context.get("current_iteration_id"),
            ):
                raise ValueError("Failed to check out part")
            self.bom_repo.checkout_bom(int(related.id))
            if released:
                self.revision_repo.prepare_released_checkout(int(related.id), target_revision)
            self.revision_repo.initialize_checkout(int(related.id), int(user_id))
        self._tree_dirty.add(int(self.session.project_id))
        return True

    # -------------------------------
    # GET CHILDREN OF A PART
    # -------------------------------
    def get_children(self, part_id: str) -> List[Dict]:
        """
        Get all direct children of a part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []
            
        children_relations = self.children_repo.get_children(part.id)
        result = []
        
        # for relation in children_relations:
        #     # Extract the child_id from the relation object
        #     child_id = relation.child_id if hasattr(relation, 'child_id') else relation
        #     child_part = self.bom_repo.get_by_id(child_id)
        #     result.append(child_part.__dict__)

        for relation in children_relations:
            # Extract the child_id
            child_id = getattr(relation, "child_id", relation)
            child_part = self.bom_repo.get_by_id(child_id)

            if not child_part:
                continue

            # Convert part object to dict
            child_dict = child_part.__dict__.copy()

            # Add the quantity from the relation (default = 1 if not set)
            quantity = getattr(relation, "quantity", 1)
            child_dict["quantity"] = quantity

            result.append(child_dict)

        print(f"Children of part {part_id}: {result}")
                
        return result
    
    # -------------------------------
    # GET HISTORY OF A PART
    # -------------------------------
    def get_history(self, part_id: int) -> List[Dict]:
        """
        Get all history of a part
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []
            
        part_history = self.signature_repo.get_all_history_by_part(part.id)
        result = []
        for record in part_history:
            result.append(record.__dict__)
        return result

    # -------------------------------
    # DETAILED HISTORY / ANALYTICS
    # -------------------------------
    def _conn(self):
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _parse_dt(s: str):
        if not s:
            return None
        try:
            # ISO format: 2026-01-27T12:34:56 or with offset
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            pass
        try:
            # sqlite datetime('now'): 2026-01-27 12:34:56
            return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _project_family_ids(self, project_id: int) -> List[Dict]:
        """Return projects in the same family as project_id.

        Output rows are dicts: {id, name, root_project_id, version_label}
        """
        if not project_id:
            return []
        with self._conn() as conn:
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
            except Exception:
                cols = []

            if "root_project_id" not in cols or "version_label" not in cols:
                row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (int(project_id),)).fetchone()
                if not row:
                    return []
                return [
                    {
                        "id": int(row["id"]),
                        "name": row["name"],
                        "root_project_id": int(row["id"]),
                        "version_label": "A",
                    }
                ]

            row = conn.execute(
                "SELECT id, root_project_id FROM projects WHERE id = ?",
                (int(project_id),),
            ).fetchone()
            if not row:
                return []
            root_id = int(row["root_project_id"] or row["id"])
            rows = conn.execute(
                "SELECT id, name, root_project_id, version_label FROM projects WHERE root_project_id = ?",
                (root_id,),
            ).fetchall()
            out = [dict(r) for r in rows]
            for r in out:
                r["id"] = int(r.get("id"))
                r["root_project_id"] = int(r.get("root_project_id") or r["id"])
                r["version_label"] = (r.get("version_label") or "").strip() or "A"
            return out

    def get_history_detailed(self, part_id: int, include_all_revisions: bool = True) -> List[Dict]:
        """Unified event timeline for a part.

        Includes commits, checkin/checkout logs, releases, and attachment version events.
        If include_all_revisions is True, it also aggregates the same AES number across
        all projects in the current project's version family.
        """

        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []

        aes = (getattr(part, "aes_number", None) or "").strip()
        project_id = getattr(part, "project_id", None) or self.session.project_id

        family = self._project_family_ids(int(project_id)) if include_all_revisions else []
        family_project_ids = [int(p["id"]) for p in family] if family else [int(project_id)]

        events: List[Dict] = []

        with self._conn() as conn:
            # Map projects for label display
            project_map = {}
            if family_project_ids:
                rows = conn.execute(
                    f"SELECT id, name, COALESCE(version_label,'A') AS version_label FROM projects WHERE id IN ({','.join(['?']*len(family_project_ids))})",
                    tuple(family_project_ids),
                ).fetchall()
                for r in rows:
                    project_map[int(r["id"])] = {
                        "project_name": r["name"],
                        "project_version": (r["version_label"] or "A"),
                    }

            # Resolve part ids across revisions via AES
            part_ids: List[int] = [int(part_id)]
            if include_all_revisions and aes and family_project_ids:
                rows = conn.execute(
                    f"SELECT id, project_id, revision, lifecycle_state, released_by, released_at FROM bom WHERE aes_number = ? AND project_id IN ({','.join(['?']*len(family_project_ids))})",
                    (aes, *family_project_ids),
                ).fetchall()
                part_rows = [dict(r) for r in rows]
                part_ids = sorted({int(r["id"]) for r in part_rows})
            else:
                part_rows = [
                    dict(
                        conn.execute(
                            "SELECT id, project_id, revision, lifecycle_state, released_by, released_at FROM bom WHERE id = ?",
                            (int(part_id),),
                        ).fetchone()
                        or {}
                    )
                ]

            if not part_ids:
                return []

            # Release events from BOM rows
            user_cache: dict[int, str] = {}

            def username_for(uid):
                if uid is None:
                    return ""
                try:
                    uid = int(uid)
                except Exception:
                    return ""
                if uid in user_cache:
                    return user_cache[uid]
                rowu = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
                user_cache[uid] = (rowu[0] if rowu else "")
                return user_cache[uid]

            tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            table_columns = {
                table_name: {
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
                }
                for table_name in ("commits", "lock_logs", "part_file_versions")
                if table_name in tables
            }
            iteration_by_id = {}
            iterations_by_bom = defaultdict(list)
            iteration_by_commit = {}
            latest_iteration_by_revision = {}

            def object_version_for(
                bom_id,
                timestamp=None,
                explicit_iteration_id=None,
                source_commit_id=None,
                prefer_next=False,
            ):
                try:
                    bom_id = int(bom_id)
                except Exception:
                    return ""
                source_key = str(source_commit_id or "").strip()
                if source_key:
                    linked = iteration_by_commit.get((bom_id, source_key))
                    if linked:
                        return linked.get("version_label", "")
                if explicit_iteration_id is not None:
                    try:
                        explicit = iteration_by_id.get(int(explicit_iteration_id))
                    except Exception:
                        explicit = None
                    if explicit and int(explicit.get("bom_id") or 0) == bom_id:
                        return explicit.get("version_label", "")

                candidates = iterations_by_bom.get(bom_id, [])
                if not candidates:
                    return ""
                target_dt = self._parse_dt(timestamp)
                if target_dt is None:
                    return candidates[-1].get("version_label", "")

                if prefer_next:
                    future = [
                        item for item in candidates
                        if item.get("_created_dt") is not None
                        and item["_created_dt"] >= target_dt
                    ]
                    if future:
                        seconds = (future[0]["_created_dt"] - target_dt).total_seconds()
                        if seconds <= 300:
                            return future[0].get("version_label", "")

                previous = [
                    item for item in candidates
                    if item.get("_created_dt") is not None
                    and item["_created_dt"] <= target_dt
                ]
                if previous:
                    return previous[-1].get("version_label", "")
                return candidates[0].get("version_label", "")

            if {"bom_revisions", "bom_iterations"}.issubset(tables):
                iteration_rows = conn.execute(
                    f"""
                    SELECT i.*, r.revision_code, r.bom_id, b.project_id, u.username
                    FROM bom_iterations i
                    JOIN bom_revisions r ON r.id=i.revision_id
                    JOIN bom b ON b.id=r.bom_id
                    LEFT JOIN users u ON u.id=i.created_by
                    WHERE r.bom_id IN ({','.join(['?'] * len(part_ids))})
                    ORDER BY i.created_at, i.id
                    """,
                    tuple(part_ids),
                ).fetchall()
                normalized_iterations = []
                for iteration_row in iteration_rows:
                    iteration = dict(iteration_row)
                    iteration_number = int(iteration.get("iteration_number") or 1)
                    iteration["version_label"] = (
                        f"{iteration.get('revision_code')}.{iteration_number}"
                    )
                    iteration["_created_dt"] = self._parse_dt(iteration.get("created_at"))
                    iteration_by_id[int(iteration["id"])] = iteration
                    iterations_by_bom[int(iteration["bom_id"])].append(iteration)
                    latest_iteration_by_revision[int(iteration["revision_id"])] = iteration
                    source_commit_id = str(iteration.get("source_commit_id") or "").strip()
                    if source_commit_id:
                        iteration_by_commit[(int(iteration["bom_id"]), source_commit_id)] = iteration
                    normalized_iterations.append(iteration)

                revision_rows = conn.execute(
                    f"""
                    SELECT r.*, b.project_id
                    FROM bom_revisions r
                    JOIN bom b ON b.id=r.bom_id
                    WHERE r.bom_id IN ({','.join(['?'] * len(part_ids))})
                    ORDER BY r.id
                    """,
                    tuple(part_ids),
                ).fetchall()
                for revision_row in revision_rows:
                    revision = dict(revision_row)
                    pid = int(revision.get("project_id") or 0)
                    info = project_map.get(pid, {"project_name": "", "project_version": ""})
                    if revision.get("released_at"):
                        released_iteration = latest_iteration_by_revision.get(int(revision["id"]))
                        events.append({
                            "timestamp": revision.get("released_at"),
                            "event": "PART_RELEASED",
                            "user": username_for(revision.get("released_by")),
                            "project": info.get("project_name", ""),
                            "version": info.get("project_version", ""),
                            "object_version": (
                                released_iteration.get("version_label", "")
                                if released_iteration else ""
                            ),
                            "details": (
                                f"Revision {revision.get('revision_code') or ''} | "
                                f"{revision.get('release_note') or ''}"
                            ).strip(" |"),
                        })
                for iteration in normalized_iterations:
                    pid = int(iteration.get("project_id") or 0)
                    info = project_map.get(pid, {"project_name": "", "project_version": ""})
                    iteration_number = int(iteration.get("iteration_number") or 1)
                    version_label = iteration.get("version_label", "")
                    events.append({
                        "timestamp": iteration.get("created_at"),
                        "event": "REVISION_CREATED" if iteration_number == 1 else "OBJECT_ITERATION",
                        "user": iteration.get("username") or "",
                        "project": info.get("project_name", ""),
                        "version": info.get("project_version", ""),
                        "object_version": version_label,
                        "details": (
                            f"{version_label} | {iteration.get('checkin_note') or ''}"
                        ).strip(" |"),
                        "commit_id": iteration.get("source_commit_id"),
                    })
            else:
                for pr in part_rows:
                    ts = pr.get("released_at")
                    if ts:
                        pid = int(pr.get("project_id") or 0)
                        info = project_map.get(pid, {"project_name": "", "project_version": ""})
                        events.append(
                            {
                                "timestamp": ts,
                                "event": "PART_RELEASED",
                                "user": username_for(pr.get("released_by")),
                                "project": info.get("project_name", ""),
                                "version": info.get("project_version", ""),
                                "object_version": (
                                    f"{pr.get('revision')}.1" if pr.get("revision") else ""
                                ),
                                "details": f"Revision {pr.get('revision') or ''}".strip(),
                            }
                        )

            # Commits affecting this part (across the family via part_ids)
            try:
                rows = conn.execute(
                    f"""
                    SELECT c.*, p.name AS project_name, COALESCE(p.version_label,'A') AS project_version,
                           u1.username AS designer_name, u2.username AS checker_name
                    FROM commits c
                    LEFT JOIN projects p ON p.id = c.project_id
                    LEFT JOIN users u1 ON u1.id = c.designer
                    LEFT JOIN users u2 ON u2.id = c.checked_by
                    WHERE c.part_id IN ({','.join(['?']*len(part_ids))})
                    ORDER BY c.committed_at DESC
                    """,
                    tuple(part_ids),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    step_status = (d.get("step_diff_status") or "").strip()
                    step_suffix = f" | STEP:{step_status}" if step_status else ""
                    events.append(
                        {
                            "timestamp": d.get("committed_at"),
                            "event": "COMMIT",
                            "user": (d.get("designer_name") or ""),
                            "project": (d.get("project_name") or ""),
                            "version": (d.get("project_version") or "A"),
                            "object_version": object_version_for(
                                d.get("part_id"),
                                d.get("committed_at"),
                                explicit_iteration_id=d.get("object_iteration_id"),
                                source_commit_id=d.get("commit_id"),
                            ),
                            "details": f"{(d.get('status') or '').strip()} | {(d.get('title') or '').strip()} | {(d.get('message') or '').strip()}{step_suffix}".strip(" |"),
                            "commit_id": d.get("commit_id"),
                            "commit_unique_id": d.get("id"),
                            "part_id": d.get("part_id"),
                            "cad_type": d.get("type"),
                            "step_diff_status": d.get("step_diff_status"),
                            "step_file_path": d.get("step_file_path"),
                            "step_prev_file_path": d.get("step_prev_file_path"),
                            "step_diff_path": d.get("step_diff_path"),
                            "step_diff_summary": d.get("step_diff_summary"),
                            "step_error": d.get("step_error"),
                        }
                    )
            except Exception:
                pass

            # Lock logs (checkin/checkout)
            try:
                lock_iteration_select = (
                    ", ll.object_iteration_id AS object_iteration_id"
                    if "object_iteration_id" in table_columns.get("lock_logs", set())
                    else ""
                )
                rows = conn.execute(
                    f"""
                    SELECT ll.part_id, ll.action, ll.timestamp, u.username AS username,
                            b.project_id AS project_id,
                            sig.note AS note{lock_iteration_select}
                    FROM lock_logs ll
                    LEFT JOIN users u ON u.id = ll.user_id
                    LEFT JOIN signature sig ON sig.id = ll.signature
                    LEFT JOIN bom b ON b.id = ll.part_id
                    WHERE ll.part_id IN ({','.join(['?']*len(part_ids))})
                    ORDER BY ll.timestamp DESC
                    """,
                    tuple(part_ids),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    pid = int(d.get("project_id") or 0)
                    info = project_map.get(pid, {"project_name": "", "project_version": ""})
                    action = str(d.get("action") or "").strip().lower()
                    event_name = {
                        "checkin": "CHECKIN",
                        "checkout": "CHECKOUT",
                        "undo_checkout": "UNDO_CHECKOUT",
                    }.get(action, action.upper() or "LOCK_EVENT")
                    events.append(
                        {
                            "timestamp": d.get("timestamp"),
                            "event": event_name,
                            "user": (d.get("username") or ""),
                            "project": info.get("project_name", ""),
                            "version": info.get("project_version", ""),
                            "object_version": object_version_for(
                                d.get("part_id"),
                                d.get("timestamp"),
                                explicit_iteration_id=d.get("object_iteration_id"),
                                prefer_next=(event_name == "CHECKIN"),
                            ),
                            "details": (d.get("note") or "").strip(),
                        }
                    )
            except Exception:
                pass

            # Attachment versions (created + released)
            try:
                tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "part_files" in tables and "part_file_versions" in tables:
                    file_iteration_select = (
                        ", pv.object_iteration_id AS object_iteration_id"
                        if "object_iteration_id" in table_columns.get("part_file_versions", set())
                        else ""
                    )
                    rows = conn.execute(
                        f"""
                        SELECT pf.part_id, pf.file_type, pf.display_name,
                               pv.version_no, pv.original_filename, pv.note,
                               pv.created_at, pv.created_by,
                               pv.lifecycle_state, pv.released_at, pv.released_by,
                               b.project_id AS project_id{file_iteration_select}
                        FROM part_files pf
                        JOIN part_file_versions pv ON pv.file_id = pf.id
                        LEFT JOIN bom b ON b.id = pf.part_id
                        WHERE pf.part_id IN ({','.join(['?']*len(part_ids))})
                        ORDER BY pv.created_at DESC
                        """,
                        tuple(part_ids),
                    ).fetchall()
                    for r in rows:
                        d = dict(r)
                        pid = int(d.get("project_id") or 0)
                        info = project_map.get(pid, {"project_name": "", "project_version": ""})

                        events.append(
                            {
                                "timestamp": d.get("created_at"),
                                "event": "ATTACHMENT_VERSION",
                                "user": username_for(d.get("created_by")),
                                "project": info.get("project_name", ""),
                                "version": info.get("project_version", ""),
                                "object_version": object_version_for(
                                    d.get("part_id"),
                                    d.get("created_at"),
                                    explicit_iteration_id=d.get("object_iteration_id"),
                                ),
                                "details": f"{(d.get('file_type') or '').upper()} | {(d.get('display_name') or '').strip()} | v{d.get('version_no')} | {(d.get('original_filename') or '').strip()} | {(d.get('note') or '').strip()}".strip(" |"),
                            }
                        )

                        if str(d.get("lifecycle_state") or "").upper() == "RELEASED" and d.get("released_at"):
                            events.append(
                                {
                                    "timestamp": d.get("released_at"),
                                    "event": "ATTACHMENT_RELEASED",
                                    "user": username_for(d.get("released_by")),
                                    "project": info.get("project_name", ""),
                                    "version": info.get("project_version", ""),
                                    "object_version": object_version_for(
                                        d.get("part_id"),
                                        d.get("released_at"),
                                        explicit_iteration_id=d.get("object_iteration_id"),
                                    ),
                                    "details": f"{(d.get('file_type') or '').upper()} | {(d.get('display_name') or '').strip()} | v{d.get('version_no')}".strip(" |"),
                                }
                            )
            except Exception:
                pass

        # Sort by parsed datetime when possible; fallback to raw string
        def _sort_key(ev):
            dt = self._parse_dt(ev.get("timestamp"))
            return (dt is None, dt or datetime.min, str(ev.get("timestamp") or ""))

        for event in events:
            event.setdefault("object_version", "")
        events.sort(key=_sort_key, reverse=True)
        return events

    def get_history_analytics(self, part_id: int, include_all_revisions: bool = True) -> Dict:
        """Small analytics summary for the part history tab."""
        events = self.get_history_detailed(part_id, include_all_revisions=include_all_revisions)
        out = {
            "events_total": len(events),
            "commits": 0,
            "checkins": 0,
            "checkouts": 0,
            "attachment_versions": 0,
            "attachment_releases": 0,
            "part_releases": 0,
            "unique_users": 0,
            "last_activity": "",
        }
        users = set()
        last_ts = ""
        last_dt = None
        for ev in events:
            et = (ev.get("event") or "").upper()
            if et == "COMMIT":
                out["commits"] += 1
            elif et == "CHECKIN":
                out["checkins"] += 1
            elif et == "CHECKOUT":
                out["checkouts"] += 1
            elif et == "ATTACHMENT_VERSION":
                out["attachment_versions"] += 1
            elif et == "ATTACHMENT_RELEASED":
                out["attachment_releases"] += 1
            elif et == "PART_RELEASED":
                out["part_releases"] += 1

            u = (ev.get("user") or "").strip()
            if u:
                users.add(u)

            dt = self._parse_dt(ev.get("timestamp"))
            if dt and (last_dt is None or dt > last_dt):
                last_dt = dt
                last_ts = str(ev.get("timestamp") or "")

        out["unique_users"] = len(users)
        out["last_activity"] = last_ts
        return out

    # -------------------------------
    # SEARCH PARTS
    # -------------------------------
    def search_parts(self, query: str) -> List[Dict]:
        """
        Search parts by AES number, name, or part number
        """
        all_parts = self.bom_repo.search_project(self.session.project_id, query)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(self.session.project_id) if self.session.project_id else {}
        except Exception:
            lock_owner = {}
        results = []
        category_map = self.bom_repo.get_categories_for_boms(part.id for part in all_parts)
        version_map = self.revision_repo.get_current_contexts(part.id for part in all_parts)
        try:
            binding_updates = self.revision_repo.get_parent_binding_update_counts(
                int(self.session.project_id)
            )
        except Exception:
            binding_updates = {}
        for part in all_parts:
            d = part.__dict__.copy()
            version = version_map.get(int(part.id), {})
            d["current_version"] = version.get("version_label") or d.get("revision")
            d["iteration_number"] = version.get("iteration_number")
            d["category_names"] = list(category_map.get(int(part.id), []))
            d["binding_update_count"] = int(binding_updates.get(int(part.id), 0))
            if d.get("locked"):
                d["locked_by_username"] = lock_owner.get(int(d.get("id")))
            results.append(d)

        return results

    # -------------------------------
    # PROJECT CATEGORIES
    # -------------------------------
    def list_categories(self) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.bom_repo.list_categories(int(self.session.project_id))

    def categories_for_part(self, part_id: int) -> List[str]:
        return self.bom_repo.get_categories_for_bom(int(part_id))

    def set_part_categories(self, part_id: int, category_names) -> List[str]:
        if not self.session.project_id:
            raise ValueError("Select a project before assigning categories.")
        categories = self.bom_repo.set_categories_for_bom(
            int(part_id),
            int(self.session.project_id),
            category_names,
            assigned_by=self.user_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return categories

    def category_usage(self, category_id: int) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before managing categories.")
        return self.bom_repo.get_category_usage(int(self.session.project_id), int(category_id))

    def delete_category(self, category_id: int) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before managing categories.")
        result = self.bom_repo.delete_category(int(self.session.project_id), int(category_id))
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def search_relation_parents(self, query: str = "") -> List[Dict]:
        if not self.session.project_id:
            return []
        return [
            part.__dict__.copy()
            for part in self.bom_repo.search_project(int(self.session.project_id), query, limit=501)
            if str(getattr(part, "type", "") or "").strip().lower() in {"asm", "assembly"}
        ]

    def search_relation_occurrences(self, query: str = "") -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.children_repo.search_occurrences(int(self.session.project_id), query, limit=501)

    def relation_sources_for_part(self, child_id: int) -> List[Dict]:
        """Return direct source occurrences for resolving a flat search result."""
        if not self.session.project_id:
            return []
        sources = []
        for relation in self.children_repo.get_parents(int(child_id)) or []:
            parent = self.bom_repo.get_by_id(int(relation.parent_id))
            if not parent or int(parent.project_id or 0) != int(self.session.project_id):
                continue
            sources.append({
                "parent_id": int(parent.id),
                "parent_name": str(parent.name or ""),
                "parent_aes_number": str(parent.aes_number or ""),
                "quantity": max(1, int(relation.quantity or 1)),
            })
        return sources

    def apply_child_relation_operation(self, target_parent_id: int, selections, mode: str) -> Dict:
        """Copy or move one or more direct occurrences under an assembly."""
        if not self.session.project_id:
            raise ValueError("Select a project before changing the BOM structure.")
        target = self.bom_repo.get_by_id(int(target_parent_id))
        if not target or int(target.project_id or 0) != int(self.session.project_id):
            raise ValueError("The target parent was not found in the current project.")
        if str(target.type or "").strip().lower() not in {"asm", "assembly"}:
            raise ValueError("Only an assembly can contain child items.")
        self._assert_checked_out_for_change(int(target_parent_id), "change its structure")

        normalized = []
        for selection in selections or []:
            child_id = int(selection.get("child_id"))
            source_value = selection.get("source_parent_id")
            source_parent_id = int(source_value) if source_value is not None else None
            child = self.bom_repo.get_by_id(child_id)
            if not child or int(child.project_id or 0) != int(self.session.project_id):
                raise ValueError(f"Child item {child_id} was not found in the current project.")
            if child_id == int(target_parent_id):
                raise ValueError("An assembly cannot be a child of itself.")
            normalized.append({"child_id": child_id, "source_parent_id": source_parent_id})
        if not normalized:
            raise ValueError("Select at least one child occurrence.")
        action = str(mode or "").strip().lower()
        if action == "copy" and any(row["source_parent_id"] is None for row in normalized):
            raise ValueError(
                "A top-level component cannot be copied because top-level membership is derived from having no parent. "
                "Use Move to place it under the target assembly."
            )

        affected_parent_ids = {int(target_parent_id)}
        if action == "move":
            affected_parent_ids.update(
                int(row["source_parent_id"])
                for row in normalized
                if row.get("source_parent_id") is not None
                and int(row["source_parent_id"]) != int(target_parent_id)
            )
        for affected_parent_id in sorted(affected_parent_ids):
            self._assert_checked_out_for_change(affected_parent_id, "change its structure")

        preferred_by_child = {}
        for row in normalized:
            source_parent_id = row.get("source_parent_id")
            if source_parent_id is None:
                continue
            binding = self.revision_repo.get_effective_child_binding(
                int(source_parent_id), int(row["child_id"])
            )
            if binding:
                preferred_by_child[int(row["child_id"])] = binding

        children_by_parent = defaultdict(list)
        for row in self.children_repo.get_structure_rows(int(self.session.project_id)):
            children_by_parent[int(row["parent_id"])].append(int(row["child_id"]))

        def contains_descendant(start_id: int, wanted_id: int) -> bool:
            pending = list(children_by_parent.get(int(start_id), []))
            visited = set()
            while pending:
                current = int(pending.pop())
                if current == int(wanted_id):
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(children_by_parent.get(current, []))
            return False

        for selection in normalized:
            if contains_descendant(int(selection["child_id"]), int(target_parent_id)):
                child = self.bom_repo.get_by_id(int(selection["child_id"]))
                name = str(getattr(child, "name", "") or selection["child_id"])
                raise ValueError(f"Cannot place {name} here because it would create a circular BOM structure.")

        result = self.children_repo.apply_child_relations(
            int(target_parent_id), normalized, action
        )
        self.revision_repo.sync_working_bindings(
            int(target_parent_id), int(self.user_id), preferred_by_child=preferred_by_child
        )
        for source_parent_id in result.get("source_parent_ids") or []:
            self.revision_repo.sync_working_bindings(int(source_parent_id), int(self.user_id))
        if action == "move":
            for selection in normalized:
                source_parent_id = selection.get("source_parent_id")
                if source_parent_id is None or int(source_parent_id) == int(target_parent_id):
                    continue
                try:
                    self.folder_repo.unassign_from_context(
                        int(self.session.project_id),
                        int(source_parent_id),
                        int(selection["child_id"]),
                    )
                except Exception:
                    pass
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # GET BOM TREE
    # -------------------------------
    @staticmethod
    def _path_key(path) -> str:
        if isinstance(path, str):
            return path
        return "/".join(str(int(value)) for value in (path or ()))

    def _build_lazy_index(self, project_id: int) -> Dict:
        """Build a small ID-only index used by lazy rows and permanent numbering."""
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._lazy_index_cache:
            return self._lazy_index_cache[pid]

        part_ids = self.bom_repo.get_project_ids(pid)
        part_set = set(part_ids)
        children = defaultdict(list)
        all_children = set()
        for row in self.children_repo.get_structure_rows(pid):
            parent_id = int(row["parent_id"])
            child_id = int(row["child_id"])
            if parent_id in part_set and child_id in part_set:
                children[parent_id].append(child_id)
                all_children.add(child_id)
        roots = [part_id for part_id in part_ids if part_id not in all_children]

        try:
            folders = self.folder_repo.list_for_project(pid)
        except Exception:
            folders = []
        folders_by_context = defaultdict(list)
        for folder in folders:
            folders_by_context[folder.get("effective_parent_bom_id")].append(folder)

        row_number = 0
        path_rows = {}
        part_rows = defaultdict(list)
        folder_rows = {}
        folder_path_rows = {}
        visited_paths = set()

        def walk_item(part_id: int, parent_path: tuple):
            nonlocal row_number
            path = parent_path + (int(part_id),)
            if path in visited_paths:
                return
            visited_paths.add(path)
            row_number += 1
            path_rows[self._path_key(path)] = row_number
            part_rows[int(part_id)].append(row_number)
            if int(part_id) in parent_path:
                return
            walk_context(int(part_id), children.get(int(part_id), []), path)

        def walk_context(parent_id, item_ids, parent_path: tuple):
            context_folders = folders_by_context.get(parent_id, [])
            by_parent_folder = defaultdict(list)
            roots_for_context = []
            assigned = set()
            for folder in context_folders:
                assigned.update(int(value) for value in (folder.get("item_ids") or []))
                parent_folder_id = folder.get("parent_folder_id")
                if parent_folder_id is None:
                    roots_for_context.append(folder)
                else:
                    by_parent_folder[int(parent_folder_id)].append(folder)
            sort_key = lambda value: (int(value.get("sort_order") or 0), int(value["id"]))
            roots_for_context.sort(key=sort_key)
            for values in by_parent_folder.values():
                values.sort(key=sort_key)

            for item_id in item_ids:
                if int(item_id) not in assigned:
                    walk_item(int(item_id), parent_path)

            def walk_folder(folder, folder_parent_path: str):
                nonlocal row_number
                row_number += 1
                folder_rows[int(folder["id"])] = row_number
                folder_path = f"{folder_parent_path}/f{int(folder['id'])}" if folder_parent_path else f"f{int(folder['id'])}"
                folder_path_rows[folder_path] = row_number
                allowed = set(int(value) for value in item_ids)
                for item_id in folder.get("item_ids") or []:
                    if int(item_id) in allowed:
                        walk_item(int(item_id), parent_path)
                for child_folder in by_parent_folder.get(int(folder["id"]), []):
                    walk_folder(child_folder, folder_path)

            for folder in roots_for_context:
                walk_folder(folder, self._path_key(parent_path))

        walk_context(None, roots, ())
        # Corrupt/circular legacy structures may have no natural root. Keep them locatable.
        indexed_parts = set(part_rows)
        for part_id in part_ids:
            if part_id not in indexed_parts:
                roots.append(part_id)
                walk_item(part_id, ())

        try:
            binding_update_counts = self.revision_repo.get_parent_binding_update_counts(pid)
        except Exception:
            binding_update_counts = {}

        index = {
            "project_id": pid,
            "roots": roots,
            "children": dict(children),
            "path_rows": path_rows,
            "part_rows": {key: list(value) for key, value in part_rows.items()},
            "folder_rows": folder_rows,
            "folder_path_rows": folder_path_rows,
            "folders": folders,
            "binding_update_counts": binding_update_counts,
        }
        self._lazy_index_cache[pid] = index
        self._tree_cache.pop(pid, None)
        self._tree_dirty.discard(pid)
        return index

    def _lazy_level_nodes(self, project_id: int, ids, parent_path=()) -> List[Dict]:
        pid = int(project_id)
        index = self._build_lazy_index(pid)
        path_prefix = tuple(int(value) for value in (parent_path or ()))
        parts = self.bom_repo.get_many(pid, ids)
        categories = self.bom_repo.get_categories_for_boms(part.id for part in parts)
        versions = self.revision_repo.get_current_contexts(part.id for part in parts)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(pid)
        except Exception:
            lock_owner = {}
        nodes = []
        for part in parts:
            node = part.__dict__.copy()
            path = path_prefix + (int(part.id),)
            node["children"] = []
            node["category_names"] = list(categories.get(int(part.id), []))
            version = versions.get(int(part.id), {})
            node["current_version"] = version.get("version_label") or node.get("revision")
            node["iteration_number"] = version.get("iteration_number")
            node["binding_update_count"] = int(
                (index.get("binding_update_counts") or {}).get(int(part.id), 0)
            )
            node["_tree_path"] = self._path_key(path)
            node["_tree_row_number"] = index["path_rows"].get(node["_tree_path"], "")
            node["_has_children"] = bool(index["children"].get(int(part.id))) and int(part.id) not in path_prefix
            node["_defer_indicators"] = True
            if node.get("locked"):
                node["locked_by_username"] = lock_owner.get(int(part.id))
            nodes.append(node)
        return nodes

    def get_bom_lazy_tree(self, project_id: int) -> Dict:
        index = self._build_lazy_index(int(project_id))
        roots = self._lazy_level_nodes(int(project_id), index["roots"], ())
        return {
            "roots": roots,
            "row_numbers": index["part_rows"],
            "folder_rows": index["folder_rows"],
            "folder_path_rows": index["folder_path_rows"],
            "folders": index["folders"],
        }

    def get_bom_lazy_children(self, project_id: int, parent_id: int, parent_path=()) -> List[Dict]:
        index = self._build_lazy_index(int(project_id))
        child_ids = index["children"].get(int(parent_id), [])
        if isinstance(parent_path, str):
            parent_path = tuple(int(value) for value in parent_path.split("/") if value)
        return self._lazy_level_nodes(int(project_id), child_ids, parent_path)

    def get_bom_lazy_numbering(self, project_id: int) -> Dict:
        index = self._build_lazy_index(int(project_id))
        return {
            "row_numbers": index["part_rows"],
            "path_rows": index["path_rows"],
            "folder_rows": index["folder_rows"],
            "folder_path_rows": index["folder_path_rows"],
            "folders": index["folders"],
            "has_children": {part_id for part_id, values in index["children"].items() if values},
        }

    def get_bom_tree(self, project_id) -> Dict:
        """
        Returns a nested dict representing the BOM tree.
        Built by part ID instead of AES number.
        """
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._tree_cache:
            return self._tree_cache[pid]
        self._lazy_index_cache.pop(pid, None)

        # Get all parts for the project
        all_parts = {b.id: b for b in self.bom_repo.get_all(project_id)}
        version_map = self.revision_repo.get_current_contexts(all_parts.keys())
        category_map = self.bom_repo.get_categories_for_boms(all_parts.keys())
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(int(project_id))
        except Exception:
            lock_owner = {}
        try:
            binding_updates = self.revision_repo.get_parent_binding_update_counts(int(project_id))
        except Exception:
            binding_updates = {}
        children_map = {}

        # Get relationships filtered to this project (avoids cross-project full-table scan)
        all_relations = self.children_repo.get_all_for_project(project_id)

        for rel in all_relations:
            parent_id = rel.parent_id if hasattr(rel, 'parent_id') else rel[0]
            child_id = rel.child_id if hasattr(rel, 'child_id') else rel[1]

            if parent_id not in all_parts or child_id not in all_parts:
                continue

            children_map.setdefault(parent_id, []).append(child_id)

        # Recursive builder
        def build_tree(part_id):
            if part_id not in all_parts:
                return None

            part = all_parts[part_id]
            node = part.__dict__.copy()
            version = version_map.get(int(part_id), {})
            node["current_version"] = version.get("version_label") or node.get("revision")
            node["iteration_number"] = version.get("iteration_number")
            node["binding_update_count"] = int(binding_updates.get(int(part_id), 0))
            node["categories"] = list(category_map.get(int(part_id), []))
            node["children"] = []

            if node.get("locked"):
                try:
                    node["locked_by_username"] = lock_owner.get(int(part_id))
                except Exception:
                    node["locked_by_username"] = None

            if part_id in children_map:
                for child_id in children_map[part_id]:
                    child_node = build_tree(child_id)
                    if child_node:
                        node["children"].append(child_node)

            return node

        # Roots: parts that are not children of anyone
        all_children = {c for cs in children_map.values() for c in cs}
        roots = [p for p in all_parts.keys() if p not in all_children]

        result = {r: build_tree(r) for r in roots}
        self._tree_cache[pid] = result
        self._tree_dirty.discard(pid)
        return result


    # -------------------------------
    # GET PART DETAILS
    # -------------------------------
    def get_part_details(self, part_id: int) -> Dict:
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return {}

        d = part.__dict__.copy()
        try:
            version_context = self.revision_repo.get_current_context(int(part_id))
        except Exception:
            version_context = {}
        d["current_revision_id"] = version_context.get("current_revision_id")
        d["current_iteration_id"] = version_context.get("current_iteration_id")
        d["iteration_number"] = version_context.get("iteration_number")
        d["current_version"] = version_context.get("version_label") or str(d.get("revision") or "")
        d["revision_state"] = version_context.get("state") or d.get("lifecycle_state")
        if str(d.get("type") or "").strip().lower() in {"asm", "assembly"}:
            try:
                d["binding_update_count"] = self.revision_repo.count_parent_binding_updates(
                    int(part_id)
                )
            except Exception:
                d["binding_update_count"] = 0
        else:
            d["binding_update_count"] = 0
        category_names = self.bom_repo.get_categories_for_bom(int(part_id))
        d["category_names"] = list(category_names)
        d["categories"] = ", ".join(category_names)
        try:
            if d.get("locked") and self.session.project_id:
                lock_owner = self.lock_repo.get_lock_owners_for_project(int(self.session.project_id))
                d["locked_by_username"] = lock_owner.get(int(part_id))
        except Exception:
            pass
        return d

    def suggest_next_revision(self, part_id: int) -> str:
        context = self.revision_repo.get_current_context(int(part_id))
        return self.revision_repo.suggest_next_revision_code(
            str(context.get("revision_code") or "A")
        )

    def get_iteration_cad_files(self, iteration_id: int) -> Dict:
        return self.revision_repo.get_iteration_cad_files(int(iteration_id))

    # -------------------------------
    # PLM-lite: Revision / Release
    # -------------------------------
    def set_revision(self, part_id: int, revision: str):
        return self.create_revision(part_id, revision)

    def create_revision(self, part_id: int, revision: str, note: str = "") -> Dict:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        revision_code = self.revision_repo.normalize_revision_code(revision)
        if any(self.lock_repo.get_by_part(int(related.id)) for related in related_parts):
            raise ValueError("Check in the shared CAD item before creating a new revision.")
        for related in related_parts:
            context = self.revision_repo.get_current_context(int(related.id))
            if str(context.get("state") or "").strip().lower() != "released":
                raise ValueError(
                    f"{context.get('version_label') or related.id} must be released before creating a new revision."
                )
            if any(
                str(row.get("revision_code") or "").casefold() == revision_code.casefold()
                for row in self.revision_repo.list_revisions(int(related.id))
            ):
                raise ValueError(f"Revision {revision_code} already exists for {related.name}.")
        created = None
        for related in related_parts:
            result = self.revision_repo.create_revision(
                int(related.id), revision_code, int(self.user_id), note=note
            )
            if int(related.id) == int(part_id):
                created = result
        self._tree_dirty.add(int(self.session.project_id))
        return created or self.revision_repo.get_current_context(int(part_id))

    def release_part(self, part_id: int, note: str = ""):
        from core.services.issue_service import IssueService
        from core.services.managed_file_service import ManagedFileService
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._parts_sharing_base_file(part)
        related_ids = [int(related.id) for related in related_parts]
        if any(self.lock_repo.get_by_part(related_id) for related_id in related_ids):
            raise ValueError("Check in the item before releasing its revision.")
        for related_id in related_ids:
            context = self.revision_repo.get_current_context(related_id)
            if str(context.get("state") or "").strip().lower() == "released":
                raise ValueError(f"{context.get('version_label')} is already released.")
        IssueService().assert_no_critical_issues(related_ids, operation="release", include_children=True)
        managed_files = ManagedFileService()
        for related_id in related_ids:
            managed_files.capture_current_iteration(related_id)
        released = None
        for related_id in related_ids:
            result = self.revision_repo.release_current_revision(
                related_id, int(self.user_id), note=note
            )
            if related_id == int(part_id):
                released = result
            context = self.revision_repo.get_current_context(related_id)
            managed_files.repo.set_iteration_lifecycle(
                int(context["current_iteration_id"]), "Released"
            )
        self._tree_dirty.add(int(self.session.project_id))
        return released

    def list_part_iterations(self, part_id: int) -> List[Dict]:
        return self.revision_repo.list_iterations(int(part_id))

    def compare_assembly_iterations(
        self, part_id: int, left_iteration_id: int, right_iteration_id: int
    ) -> Dict:
        return self.revision_repo.compare_assembly_iterations(
            int(part_id), int(left_iteration_id), int(right_iteration_id)
        )

    def get_child_version_status(self, parent_id: int) -> List[Dict]:
        return self.revision_repo.list_child_version_status(int(parent_id))

    def update_children_to_latest(self, parent_id: int, child_ids) -> List[int]:
        self._assert_checked_out_for_change(int(parent_id), "update child versions")
        changed = self.revision_repo.update_children_to_latest(
            int(parent_id), child_ids, int(self.user_id)
        )
        self._tree_dirty.add(int(self.session.project_id))
        return changed

    # -------------------------------
    # REMOVE CHILD RELATIONSHIP
    # -------------------------------
    def remove_child_by_id(self, parent_id: int, child_id: int) -> bool:
        self.remove_children_from_parent(int(parent_id), [int(child_id)])
        return True

    def remove_children_from_parent(self, parent_id: int, child_ids) -> Dict:
        """Remove direct relations; last occurrences become top-level BOM items."""
        if not self.session.project_id:
            raise ValueError("Select a project before changing the BOM structure.")
        self._assert_checked_out_for_change(int(parent_id), "change its structure")
        result = self.children_repo.remove_children_from_parent(
            int(self.session.project_id), int(parent_id), child_ids
        )
        self.revision_repo.sync_working_bindings(int(parent_id), int(self.user_id))
        for child_id in result.get("removed_child_ids") or []:
            try:
                self.folder_repo.unassign_from_context(
                    int(self.session.project_id), int(parent_id), int(child_id)
                )
            except Exception:
                pass
        moved_items = []
        for child_id in result.get("moved_to_root_ids") or []:
            child = self.bom_repo.get_by_id(int(child_id))
            if child:
                moved_items.append({
                    "id": int(child.id),
                    "name": str(child.name or ""),
                    "aes_number": str(child.aes_number or ""),
                })
        result["moved_to_root_items"] = moved_items
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # alias
    def remove_child(self, parent_id: int, child_id: int) -> bool:
        return self.remove_child_by_id(parent_id, child_id)

    def reorder_children(self, parent_id: int, ordered_child_ids: list[int]) -> bool:
        parent = self.bom_repo.get_by_id(int(parent_id))
        if not parent:
            raise ValueError("Parent not found")
        if str(getattr(parent, "type", "") or "").lower() != "asm":
            raise ValueError("Only assembly children can be reordered.")
        self._assert_checked_out_for_change(int(parent_id), "reorder its structure")
        current = self.children_repo.ordered_child_ids(int(parent_id))
        if set(map(int, current)) != set(map(int, ordered_child_ids or [])):
            raise ValueError("Reorder must keep the same child associations.")
        result = self.children_repo.set_child_order(int(parent_id), [int(x) for x in ordered_child_ids])
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def ordered_child_ids(self, parent_id: int) -> List[int]:
        return self.children_repo.ordered_child_ids(int(parent_id))

    def direct_parent_ids(self, child_ids) -> List[int]:
        parents = set()
        project_id = int(self.session.project_id) if self.session.project_id else None
        for child_id in {int(value) for value in (child_ids or [])}:
            for relation in self.children_repo.get_parents(child_id) or []:
                parent_id = int(relation.parent_id)
                if project_id is not None:
                    parent = self.bom_repo.get_by_id(parent_id)
                    if not parent or int(parent.project_id or 0) != project_id:
                        continue
                parents.add(parent_id)
        return sorted(parents)

    def get_structure_context(self, part_id: int) -> Dict:
        """Return recursive Uses and direct Where Used relations for one BOM item."""
        selected = self.bom_repo.get_by_id(int(part_id))
        if not selected:
            return {"uses": None, "where_used": None, "uses_count": 0, "where_used_count": 0}

        project_id = int(getattr(selected, "project_id", None) or self.session.project_id)
        parts = {int(part.id): part for part in self.bom_repo.get_all(project_id)}
        version_contexts = self.revision_repo.get_current_contexts(parts.keys())
        relations = self.children_repo.get_all_for_project(project_id)
        try:
            binding_status = self.revision_repo.get_project_binding_status(project_id)
        except Exception:
            binding_status = {}
        children_map = {}
        parents_map = {}
        for relation in relations:
            usage_id = int(relation.id)
            parent_id = int(relation.parent_id)
            child_id = int(relation.child_id)
            quantity = int(getattr(relation, "quantity", 1) or 1)
            sort_order = int(getattr(relation, "sort_order", 0) or 0)
            children_map.setdefault(parent_id, []).append((sort_order, child_id, quantity, usage_id))
            parents_map.setdefault(child_id, []).append((parent_id, quantity, usage_id))

        for values in children_map.values():
            values.sort(key=lambda row: (row[0], row[1]))
        for values in parents_map.values():
            values.sort(
                key=lambda row: (
                    str(getattr(parts.get(row[0]), "aes_number", "") or "").casefold(),
                    str(getattr(parts.get(row[0]), "name", "") or "").casefold(),
                    row[0],
                )
            )

        def part_node(
            node_id: int,
            relation_label: str,
            quantity=None,
            cycle=False,
            usage_id=None,
            relation_parent_id=None,
        ) -> Dict:
            part = parts.get(int(node_id))
            if not part:
                return {}
            node = part.__dict__.copy()
            node.update({
                "relation": relation_label,
                "quantity": quantity,
                "cycle": bool(cycle),
                "usage_id": usage_id,
                "relation_parent_id": relation_parent_id,
                "children": [],
            })
            version = version_contexts.get(int(node_id), {})
            node["current_version"] = version.get("version_label") or node.get("revision")
            node["current_iteration_id"] = version.get("current_iteration_id")
            status = binding_status.get(int(usage_id)) if usage_id is not None else None
            if status:
                node["bound_version"] = status.get("bound_version")
                node["latest_version"] = status.get("latest_version")
                node["bound_iteration_id"] = status.get("bound_iteration_id")
                node["latest_iteration_id"] = status.get("latest_iteration_id")
                node["binding_status"] = "Current" if status.get("is_latest") else "Update available"
                node["binding_source"] = status.get("binding_source")
            return node

        def build_uses(node_id: int, path: set) -> Dict:
            node = part_node(node_id, "Selected Item" if not path else "Uses")
            if not node:
                return {}
            next_path = set(path)
            next_path.add(int(node_id))
            for _order, child_id, quantity, usage_id in children_map.get(int(node_id), []):
                if child_id in next_path:
                    child = part_node(
                        child_id, "Cycle", quantity, cycle=True,
                        usage_id=usage_id, relation_parent_id=int(node_id),
                    )
                else:
                    child = build_uses(child_id, next_path)
                    if child:
                        child["relation"] = "Uses"
                        child["quantity"] = quantity
                        child["usage_id"] = usage_id
                        child["relation_parent_id"] = int(node_id)
                        status = binding_status.get(int(usage_id), {})
                        child["bound_version"] = status.get("bound_version")
                        child["latest_version"] = status.get("latest_version")
                        child["bound_iteration_id"] = status.get("bound_iteration_id")
                        child["latest_iteration_id"] = status.get("latest_iteration_id")
                        child["binding_status"] = (
                            "Current" if status.get("is_latest") else "Update available"
                        ) if status else ""
                        child["binding_source"] = status.get("binding_source")
                if child:
                    node["children"].append(child)
            return node

        def build_where_used(node_id: int) -> Dict:
            node = part_node(node_id, "Selected Item")
            if not node:
                return {}
            for parent_id, quantity, usage_id in parents_map.get(int(node_id), []):
                parent = part_node(
                    parent_id, "Used By", quantity,
                    usage_id=usage_id, relation_parent_id=parent_id,
                )
                if parent:
                    node["children"].append(parent)
            return node

        def descendant_count(node: Dict) -> int:
            children = list((node or {}).get("children") or [])
            return len(children) + sum(descendant_count(child) for child in children)

        uses = build_uses(int(part_id), set())
        where_used = build_where_used(int(part_id))
        return {
            "uses": uses,
            "where_used": where_used,
            "uses_count": descendant_count(uses),
            "where_used_count": descendant_count(where_used),
        }

    # -------------------------------
    # EXPORT BOM
    # -------------------------------
    def export_bom(self, file_path: str) -> bool:
        """
        Export BOM to a CSV file
        """
        try:
            import csv
            
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['aes_number', 'name', 'type', 'part_number', 'drawing_number', 
                             'material', 'weight', 'status', 'notes']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for part in self.bom_repo.get_all():
                    if "_" not in (part.aes_number or ""):
                        writer.writerow({
                            'aes_number': part.aes_number or '',
                            'name': part.name or '',
                            'type': part.type or '',
                            'part_number': part.part_number or '',
                            'drawing_number': part.drawing_number or '',
                            'material': part.material or '',
                            'weight': part.weight or '',
                            'status': part.status or '',
                            'notes': part.notes or ''
                        })
            
            return True
        except Exception as e:
            print(f"Error exporting BOM: {e}")
            return False
