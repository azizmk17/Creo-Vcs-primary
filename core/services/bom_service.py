from typing import List, Dict
from collections import defaultdict
import os
import re
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
from core.ebom_policy import (
    delivery_policy_label,
    normalize_cad_control_mode,
    normalize_classification,
    normalize_default_behavior,
    normalize_occurrence_behavior,
    normalize_requirement,
    requires_aes_number,
)
from core.item_policy import (
    normalize_assembly_mode,
    normalize_default_unit,
    normalize_item_type,
    normalize_item_view,
    normalize_procurement_source,
)
from core.services.ebom_service import EbomExportService, EbomService
from core.services.release_validation_service import ReleaseValidationService
from core.services.pdm_service import PdmService


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
        self.ebom_service = EbomService()
        self.ebom_export_service = EbomExportService(self.ebom_service)
        self.release_validation_service = ReleaseValidationService()
        self.pdm_service = PdmService()
        self.session = SessionManager()
        self._tree_cache: dict = {}    # project_id -> tree dict
        self._tree_dirty: set = set()  # project_ids that need re-fetch
        self._lazy_index_cache: dict = {}

    @staticmethod
    def _clean_drawing_number(value) -> str:
        """Return a real drawing number or blank.

        Creo/native document file names such as ``assy_x.drw.1`` are file
        references, not drawing-number metadata.  If no valid drawing number
        exists, the Item must keep this parameter blank.
        """
        text = str(value or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if re.search(r"\.(?:drw|prt|asm)(?:\.\d+)?$", lowered):
            return ""
        if re.search(r"\.(?:pdf|step|stp|iges|igs|dxf|dwg)$", lowered):
            return ""
        return text

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

    def reorder_bom_folders(self, ordered_folder_ids) -> List[int]:
        result = self.folder_repo.reorder(
            int(self.session.project_id), ordered_folder_ids
        )
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

    def list_cad_folders(self) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.folder_repo.list_cad_for_project(int(self.session.project_id))

    def create_cad_folder(
        self, name: str, parent_cad_document_id=None, parent_folder_id=None
    ) -> Dict:
        if not self.session.project_id:
            raise ValueError("Select a project before creating a CAD folder.")
        return self.folder_repo.create_cad(
            int(self.session.project_id),
            name,
            created_by=self.user_id,
            parent_cad_document_id=parent_cad_document_id,
            parent_folder_id=parent_folder_id,
        )

    def eligible_cad_folder_documents(self, folder_id: int) -> List[Dict]:
        return self.folder_repo.eligible_cad_documents(
            int(self.session.project_id), int(folder_id)
        )

    def set_cad_folder_documents(
        self, folder_id: int, cad_document_ids
    ) -> List[int]:
        return self.folder_repo.set_cad_documents(
            int(self.session.project_id),
            int(folder_id),
            cad_document_ids,
            self.user_id,
        )

    # -------------------------------
    # INSERT PART / ASM
    # -------------------------------
    def list_representation_targets(self, exclude_id=None) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.bom_repo.list_deliverable_parts(
            int(self.session.project_id), exclude_id=exclude_id
        )

    def _representation_values(self, part_data: dict, part_id=None) -> dict:
        """Validate and enforce the invariant for an alternate CAD representation."""
        values = dict(part_data or {})
        if part_id is not None and "represented_part_id" not in values:
            current = self.bom_repo.get_by_id(int(part_id))
            values["represented_part_id"] = (
                current.represented_part_id if current else None
            )
        raw_target = values.get("represented_part_id")
        if raw_target in (None, "", 0, "0"):
            values["represented_part_id"] = None
            return values
        try:
            target_id = int(raw_target)
        except (TypeError, ValueError):
            raise ValueError("Select a valid deliverable physical part.")
        if part_id is not None and int(part_id) == target_id:
            raise ValueError("A BOM item cannot be a representation of itself.")
        target = self.bom_repo.get_by_id(target_id)
        if not target or int(target.project_id or 0) != int(self.session.project_id or 0):
            raise ValueError("The represented physical part is not in this project.")
        if target.represented_part_id is not None:
            raise ValueError("A CAD representation must link directly to a deliverable physical part.")
        if str(target.classification or "PHYSICAL").upper() != "PHYSICAL":
            raise ValueError("The represented item must be classified as PHYSICAL.")
        if part_id is not None and any(
            str(values.get(field) or "").strip()
            for field in ("drawing_number", "drawing", "pdf_path", "step_path")
        ):
            raise ValueError(
                "A CAD-only representation cannot have a drawing, PDF, or STEP delivery file. "
                "Attach delivery files to the linked physical part instead."
            )

        values.update({
            "represented_part_id": target_id,
            "aes_number": str(target.aes_number or ""),
            "classification": "CAD_ONLY",
            "default_ebom_behavior": "EXCLUDE",
            "cad_requirement": "REQUIRED",
            "drawing_requirement": "NOT_REQUIRED",
            "drawing_number": "",
            "drawing": "",
            "pdf_path": "",
            "step_path": "",
            "cad_control_mode": "CONTROLLED",
        })
        return values

    def add_part(self, part_data: dict) -> int:
        part_data = self._representation_values(part_data)
        part_number = str(part_data.get("part_number") or "").strip()
        if part_number:
            existing_number = self.bom_repo.get_by_part_number(
                part_number, self.session.project_id
            )
            if existing_number:
                raise ValueError(
                    f"Item Number {part_number} already identifies another Item in this product."
                )
        part_data["part_number"] = part_number
        part_data["drawing_number"] = self._clean_drawing_number(
            part_data.get("drawing_number")
        )
        aes_number = str(part_data.get("aes_number") or "").strip()
        if requires_aes_number(
            part_data.get("default_ebom_behavior"),
            part_data.get("represented_part_id"),
        ) and not aes_number:
            raise ValueError("AES Number is required for Items that are for delivery.")

        existing = (
            self.bom_repo.get_by_aes(aes_number, self.session.project_id)
            if aes_number else None
        )
        if existing and not part_data.get("represented_part_id"):
            raise ValueError(
                f"AES Number {aes_number} is already assigned to another deliverable Item."
            )
        
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

        raw_assembly_mode = part_data.get("assembly_mode")
        if not raw_assembly_mode and str(part_data.get("type") or "").lower() in {
            "asm", "assembly"
        }:
            raw_assembly_mode = "SEPARABLE"

        bom_item = Bom(
            id=None,
            type=part_data.get("type", "prt"),
            name=part_data.get("name", "Unnamed"),
            part_number=part_number,
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
            project_id=self.session.project_id,
            classification=normalize_classification(part_data.get("classification")),
            default_ebom_behavior=normalize_default_behavior(
                part_data.get("default_ebom_behavior")
            ),
            cad_requirement=normalize_requirement(
                part_data.get("cad_requirement"), "CAD requirement"
            ),
            drawing_requirement=normalize_requirement(
                part_data.get("drawing_requirement"), "drawing requirement"
            ),
            represented_part_id=part_data.get("represented_part_id"),
            cad_control_mode=normalize_cad_control_mode(
                part_data.get("cad_control_mode")
            ),
            item_type=normalize_item_type(part_data.get("item_type")),
            assembly_mode=normalize_assembly_mode(raw_assembly_mode),
            procurement_source=normalize_procurement_source(
                part_data.get("procurement_source")
            ),
            item_view=normalize_item_view(part_data.get("item_view")),
            default_unit=normalize_default_unit(part_data.get("default_unit")),
        )
        try:
            result = self.bom_repo.insert(bom_item)
        except sqlite3.IntegrityError as exc:
            if "item_number" in str(exc).lower() or "part_number" in str(exc).lower():
                raise ValueError(
                    f"Item Number {part_number} was allocated by another operation. Try again."
                ) from exc
            raise
        if isinstance(result, int):
            if base_f_name:
                self.bom_repo.remove_cad_dependency_by_base(
                    int(self.session.project_id), base_f_name
                )
            self.revision_repo.ensure_bom(int(result), created_by=self.user_id)
            pdm_service = getattr(self, "pdm_service", None)
            if pdm_service is not None:
                pdm_service.sync_legacy_item(int(result))
            self.emit_project_event(
                "item.created",
                entity_type="ITEM",
                entity_id=int(result),
                payload={"item_ids": [int(result)]},
            )
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
        part_data = self._representation_values(part_data, part_id=part.id)
        requested_number = str(
            part_data.get("part_number", part.part_number) or ""
        ).strip()
        if requested_number:
            number_owner = self.bom_repo.get_by_part_number(
                requested_number, self.session.project_id
            )
            if number_owner and int(number_owner.id) != int(part.id):
                raise ValueError(
                    f"Item Number {requested_number} already identifies another Item in this product."
                )
        part_data["part_number"] = requested_number
        effective_behavior = part_data.get(
            "default_ebom_behavior", part.default_ebom_behavior
        )
        effective_representation = part_data.get(
            "represented_part_id", part.represented_part_id
        )
        effective_aes = str(part_data.get("aes_number", part.aes_number) or "").strip()
        if requires_aes_number(
            effective_behavior, effective_representation
        ) and not effective_aes:
            raise ValueError("AES Number is required for Items that are for delivery.")
        if effective_aes and effective_representation is None:
            aes_owner = self.bom_repo.get_by_aes(
                effective_aes, self.session.project_id
            )
            if aes_owner and int(aes_owner.id) != int(part.id):
                raise ValueError(
                    f"AES Number {effective_aes} is already assigned to another deliverable Item."
                )
        if "aes_number" in part_data:
            part_data["aes_number"] = effective_aes
        if "drawing_number" in part_data:
            part_data["drawing_number"] = self._clean_drawing_number(
                part_data.get("drawing_number")
            )
        if "cad_control_mode" in part_data:
            new_control_mode = normalize_cad_control_mode(part_data.get("cad_control_mode"))
            if (
                str(part.cad_control_mode or "CONTROLLED").upper() == "SUPPLIER_PACKAGE"
                and new_control_mode != "SUPPLIER_PACKAGE"
                and self.bom_repo.count_cad_dependencies(int(part.id))
            ):
                raise ValueError(
                    "Remove the owned CAD dependencies before changing this item back to CONTROLLED."
                )
            part_data["cad_control_mode"] = new_control_mode
        normalizers = {
            "item_type": normalize_item_type,
            "assembly_mode": normalize_assembly_mode,
            "procurement_source": normalize_procurement_source,
            "item_view": normalize_item_view,
            "default_unit": normalize_default_unit,
        }
        for field, normalizer in normalizers.items():
            if field in part_data:
                part_data[field] = normalizer(part_data.get(field))
            
        # Update part fields
        for key, value in part_data.items():
            if hasattr(part, key):
                setattr(part, key, value)
                
        result = self.bom_repo.update(part)
        if part.represented_part_id is None:
            self.bom_repo.sync_representation_aes(int(part.id), str(part.aes_number or ""))
        self.emit_project_event(
            "item.updated",
            entity_type="ITEM",
            entity_id=int(part.id),
            payload={"item_ids": [int(part.id)]},
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    # -------------------------------
    # DELETE PART
    # -------------------------------
    def assert_item_fully_checked_in_for_delete(self, part_id: int) -> None:
        """Require the Item and all associated CAD Documents to be checked in."""
        item_ids = [int(part_id)]
        try:
            item_ids.extend(
                int(row.id) for row in self.bom_repo.get_representations(int(part_id)) or []
            )
        except Exception:
            pass
        for item_id in sorted(set(item_ids)):
            lock = self.lock_repo.get_by_part(int(item_id))
            if lock:
                raise ValueError(
                    "This Item or one of its CAD-only representation Items is checked out. "
                    "Check it in or undo its checkout before deleting it."
                )
        self._assert_no_active_cad_checkouts(
            item_ids, "delete this Item"
        )

    def delete_part(self, part_id: str) -> bool:
        """
        Delete a part by AES number
        """
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return False
        # Deletion is never a working-copy operation.  The Item, legacy CAD-only
        # representation rows, and every connected CAD Document must be checked
        # in first.  Representation rows are implementation details of the old
        # architecture; they must not block a normal Item delete forever.
        representations = self.bom_repo.get_representations(int(part.id))
        delete_item_ids = [int(part.id)] + [
            int(item.id) for item in representations or [] if item and item.id
        ]
        self.assert_item_fully_checked_in_for_delete(int(part.id))
        dependency_count = self.bom_repo.count_cad_dependencies(int(part.id))
        if dependency_count:
            raise ValueError(
                f"This supplier package owns {dependency_count} CAD dependencies. "
                "Unassign them in Diagnostics before deleting the item."
            )
        context = self.revision_repo.get_current_context(int(part.id))
        if str(context.get("state") or "").strip().lower() == "released":
            raise ValueError(
                "Released BOM items cannot be deleted. Create or obsolete a controlled revision instead."
            )
            
        # First remove current product-structure and CAD/Item links.  Legacy
        # representation rows are deleted first so they cannot survive as
        # invisible stale children of the physical Item.
        for item_id in sorted(set(delete_item_ids) - {int(part.id)}):
            self.children_repo.delete_by_parent(item_id)
            self.children_repo.delete_by_child(item_id)
            try:
                self.pdm_service.repo.delete_item_pdm_links(int(item_id))
            except Exception:
                pass
            self.bom_repo.delete(item_id, deleted_by=self.user_id)

        self.children_repo.delete_by_parent(part.id)
        self.children_repo.delete_by_child(part.id)
        try:
            self.pdm_service.repo.delete_item_pdm_links(int(part.id))
        except Exception:
            pass
        
        # Then delete the part
        result = self.bom_repo.delete(part.id, deleted_by=self.user_id)
        try:
            self.pdm_service.repo.cleanup_orphan_item_associations()
        except Exception:
            pass
        self.emit_project_event(
            "item.deleted",
            entity_type="ITEM",
            entity_id=int(part.id),
            payload={"item_ids": sorted(set(delete_item_ids)), "deleted_item_id": int(part.id)},
        )
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
        relation = next(
            (
                row for row in self.children_repo.get_children(int(parent.id))
                if int(row.id) == int(result)
            ),
            None,
        )
        pdm_service = getattr(self, "pdm_service", None)
        if pdm_service is not None:
            pdm_service.sync_legacy_cad_relation(
                int(parent.id), int(child.id),
                quantity=int(getattr(relation, "quantity", 1) or 1),
                legacy_usage_id=int(result),
            )
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

    def _checkout_scope(self, part, exact_item: bool = False) -> List:
        """Return one Item for PDM workflows or the legacy shared-file family."""
        return [part] if exact_item else self._parts_sharing_base_file(part)

    def checked_out_cad_for_item(self, item_id: int) -> List[Dict]:
        return self.pdm_service.list_checked_out_cad_for_item(int(item_id))

    def _assert_no_active_cad_checkouts(self, item_ids, action: str) -> None:
        active = []
        for item_id in sorted({int(value) for value in (item_ids or [])}):
            active.extend(self.checked_out_cad_for_item(item_id))
        if not active:
            return
        labels = ", ".join(
            str(row.get("file_name") or row.get("name") or row.get("id"))
            for row in active[:5]
        )
        if len(active) > 5:
            labels += f", and {len(active) - 5} more"
        raise ValueError(
            f"Check in or undo the associated CAD working copies before you {action}: {labels}."
        )

    def _item_has_working_object_changes(self, item_id: int) -> bool:
        analysis = self.revision_repo.analyze_working_object(int(item_id))
        return bool(
            analysis.get("metadata_changes") or analysis.get("structure_changes")
        )

    def _release_auto_item_checkout_after_cad(
        self, item_id: int | None, actor_id: int
    ) -> dict:
        """Close an unused CAD-origin Item checkout after the last CAD closes."""
        if item_id is None:
            return {"item_id": None, "item_checkout": "NOT_APPLICABLE"}
        item_id = int(item_id)
        if self.checked_out_cad_for_item(item_id):
            return {"item_id": item_id, "item_checkout": "RETAINED_FOR_CAD"}
        lock = self.lock_repo.get_by_part(item_id)
        if not lock:
            return {"item_id": item_id, "item_checkout": "ALREADY_CLOSED"}
        if int(lock.user_id) != int(actor_id):
            return {"item_id": item_id, "item_checkout": "RETAINED_OTHER_OWNER"}
        if str(getattr(lock, "checkout_origin", "ITEM") or "ITEM").upper() != "CAD":
            return {"item_id": item_id, "item_checkout": "RETAINED_EXPLICIT"}
        try:
            changed = self._item_has_working_object_changes(item_id)
        except Exception:
            # Never discard an Item working copy when its state cannot be proven clean.
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
            return {"item_id": item_id, "item_checkout": "RETAINED_UNVERIFIED"}
        if changed:
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
            return {"item_id": item_id, "item_checkout": "RETAINED_WITH_CHANGES"}
        try:
            self.undo_checkout(item_id, exact_item=True)
        except Exception as exc:
            # CAD is already safely closed. Preserve the Item working copy so a
            # cleanup failure can never discard work or leave an ambiguous
            # expendable CAD-origin lock.
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
            return {
                "item_id": item_id,
                "item_checkout": "RETAINED_RELEASE_FAILED",
                "item_checkout_message": str(exc),
            }
        return {"item_id": item_id, "item_checkout": "AUTO_RELEASED"}

    def _release_auto_item_checkouts_after_cad(
        self, item_ids, actor_id: int
    ) -> dict:
        """Release every expendable CAD-origin Item lock after a CAD closes."""
        normalized_ids = sorted({int(value) for value in (item_ids or [])})
        results = [
            self._release_auto_item_checkout_after_cad(item_id, int(actor_id))
            for item_id in normalized_ids
        ]
        summary = {
            "associated_item_ids": normalized_ids,
            "item_checkouts": results,
            # Compatibility for callers written before shared CAD associations.
            "associated_item_id": normalized_ids[0] if normalized_ids else None,
        }
        if len(results) == 1:
            summary.update({
                key: value for key, value in results[0].items()
                if key != "item_id"
            })
        elif results:
            summary["item_checkout"] = "MULTIPLE"
        else:
            summary["item_checkout"] = "NOT_APPLICABLE"
        return summary

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
        exact_item: bool = False,
    ):
        if not source_commit_id:
            raise ValueError(
                "Check-in is created only by committing the checked-out item. Use Undo Checkout to discard work."
            )
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
        self._assert_no_active_cad_checkouts(
            [related.id for related in related_parts], "check in the Item"
        )
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
        affected = [int(related.id) for related in related_parts]
        self.emit_project_event(
            "item.checkin",
            entity_type="ITEM",
            entity_id=int(part.id),
            payload={"item_ids": affected},
            actor_user_id=effective_user_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_part(
        self,
        part_id: str,
        as_user_id: int | None = None,
        released_revision_code: str | None = None,
        exact_item: bool = False,
        checkout_origin: str = "ITEM",
    ):
        part = self.bom_repo.get_by_id(part_id)
        if not part:
            raise ValueError("Part not found")
        origin = str(checkout_origin or "ITEM").strip().upper()
        if origin not in {"ITEM", "CAD"}:
            raise ValueError(f"Unsupported checkout origin: {checkout_origin}.")
        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
        contexts = {}
        for related in related_parts:
            related_id = int(related.id)
            context = self.revision_repo.get_current_context(related_id)
            contexts[related_id] = context
            state = str(context.get("state") or "").strip().lower()
            if state == "released":
                pending_revision = str(
                    context.get("pending_revision_code") or ""
                ).strip()
                target_revision = str(
                    released_revision_code or pending_revision or ""
                ).strip()
                if not target_revision:
                    raise ValueError(
                        f"{context['version_label']} is Released. Enter the revision to create on commit."
                    )
                if pending_revision:
                    if pending_revision.casefold() != target_revision.casefold():
                        raise ValueError(
                            f"This Item checkout is already preparing revision {pending_revision}."
                        )
                else:
                    self.revision_repo.validate_released_checkout(
                        related_id, target_revision
                    )
            else:
                self.revision_repo.assert_mutable(related_id)
        actor_user_id = int(self.session.user_id) if self.session.user_id is not None else None
        if not actor_user_id:
            raise PermissionError("You must be logged in")

        if as_user_id is not None and int(as_user_id) != int(actor_user_id):
            if not self.permission_repo.user_has_permission(actor_user_id, "merge", self.session.project_id):
                raise PermissionError("Only Master/Admin can check out as another user")
            effective_user_id = int(as_user_id)
        else:
            effective_user_id = int(actor_user_id)

        existing_locks = [
            self.lock_repo.get_by_part(int(p.id))
            for p in related_parts
            if getattr(p, "id", None) is not None
        ]
        active_locks = [lock for lock in existing_locks if lock]
        if active_locks:
            if not exact_item or len(related_parts) != 1:
                raise ValueError("Part is already checked out.")
            lock = active_locks[0]
            if int(lock.user_id) != int(effective_user_id):
                raise ValueError("This Item is checked out by another user.")
            if origin == "ITEM":
                self.lock_repo.upgrade_to_item_checkout(
                    int(part.id), int(effective_user_id)
                )
                self.emit_project_event(
                    "item.checkout",
                    entity_type="ITEM",
                    entity_id=int(part.id),
                    payload={"item_ids": [int(part.id)], "origin": origin},
                    actor_user_id=effective_user_id,
                )
            return True

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
                checkout_origin=origin,
            )
            if not success:
                raise ValueError("Failed to check out part")
            self.bom_repo.checkout_bom(related_id)
            if released_checkout:
                if not str(
                    contexts[related_id].get("pending_revision_code") or ""
                ).strip():
                    self.revision_repo.prepare_released_checkout(
                        related_id, str(released_revision_code)
                    )
            self.revision_repo.initialize_checkout(related_id, effective_user_id)
        affected = [int(related.id) for related in related_parts]
        self.emit_project_event(
            "item.checkout",
            entity_type="ITEM",
            entity_id=int(part.id),
            payload={"item_ids": affected, "origin": origin},
            actor_user_id=effective_user_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_item(
        self,
        item_id: int,
        *,
        as_user_id: int | None = None,
        released_revision_code: str | None = None,
        include_owner_cad: bool = False,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        workspace_machine_id: str | None = None,
    ) -> bool:
        """Check out one EBOM Item, optionally including its OWNER CAD Document."""
        item_id = int(item_id)
        existing_lock = self.lock_repo.get_by_part(item_id)
        checked_out = bool(self.checkout_part(
            item_id,
            as_user_id=as_user_id,
            released_revision_code=released_revision_code,
            exact_item=True,
            checkout_origin="ITEM",
        ))
        owner_cad = self.pdm_service.owner_cad_for_item(item_id)
        if not include_owner_cad or not owner_cad:
            return checked_out
        try:
            self.checkout_pdm_cad_document(
                int(owner_cad["id"]),
                released_item_revision_code=released_revision_code,
                as_user_id=as_user_id,
                workspace_id=workspace_id,
                workspace_name=workspace_name,
                workspace_machine_id=workspace_machine_id,
            )
        except Exception:
            if existing_lock is None:
                try:
                    self.undo_checkout(item_id, as_user_id=as_user_id, exact_item=True)
                except Exception:
                    pass
            raise
        return checked_out

    def undo_item_checkout(
        self, item_id: int, *, as_user_id: int | None = None
    ) -> bool:
        return self.undo_checkout(
            int(item_id), as_user_id=as_user_id, exact_item=True
        )

    def checkin_item_data(self, item_id: int, note: str) -> Dict:
        """Check in Item metadata/structure/documents without a CAD checkout."""
        return self.checkin_non_cad_changes(
            int(item_id), note, exact_item=True
        )

    def checkin_by_part_id(
        self,
        part_id: int,
        user_id: int,
        note: str = "",
        source_commit_id: str | None = None,
        exact_item: bool = False,
    ):
        if not source_commit_id:
            raise ValueError("A commit reference is required to check in an item.")
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
        self._assert_no_active_cad_checkouts(
            [related.id for related in related_parts], "check in the Item"
        )
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

    def analyze_item_checkout(self, item_id: int) -> Dict:
        """Analyze one Item working copy without treating associated CAD as Item data."""
        analysis = dict(self.analyze_checkout(int(item_id)))
        analysis["requires_commit"] = False
        analysis["structure_requires_cad"] = False
        analysis["modified_paths"] = []
        analysis["has_any_changes"] = bool(analysis.get("has_non_cad_changes"))
        for key in ("native_cad", "drawing"):
            content = dict(analysis.get(key) or {})
            content["modified"] = False
            content["reason"] = "Managed through the associated CAD Document"
            analysis[key] = content
        return analysis

    def checkin_non_cad_changes(
        self, part_id: int, note: str, *, exact_item: bool = False
    ) -> Dict:
        """Finish a checkout when only controlled object data changed."""
        from core.services.checkout_change_service import CheckoutChangeService
        from core.services.managed_file_service import ManagedFileService

        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        note = str(note or "").strip()
        if not note:
            raise ValueError("A check-in comment is required.")

        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
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
        if exact_item:
            # A first-class Item working copy owns business data, EBOM usages,
            # and Item documents. Native CAD and drawing content belong to the
            # associated CAD Document working copies and must not redirect this
            # Item check-in through the legacy Commit page.
            for analysis in analyses.values():
                analysis["requires_commit"] = False
                analysis["structure_requires_cad"] = False
        selected = analyses[int(part_id)]
        if selected.get("requires_commit"):
            raise ValueError("Native CAD or drawing content changed. Continue through Commit.")
        if selected.get("structure_requires_cad"):
            raise ValueError(
                "The assembly structure changed without an updated native assembly file. "
                "Update the assembly in Creo before check-in."
            )
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
                    "checkin",
                    int(lock.user_id),
                    note=note or "Check-in completed without detected Item changes",
                )
                self.lock_repo.checkin(
                    related_id,
                    int(lock.user_id),
                    signature,
                    object_iteration_id=context.get("current_iteration_id"),
                )
            self.bom_repo.checkin_bom(related_id)
            affected_ids.append(related_id)

        self.emit_project_event(
            "item.checkin",
            entity_type="ITEM",
            entity_id=int(part_id),
            payload={"item_ids": sorted(set(affected_ids)), "non_cad": True},
        )
        self._tree_dirty.add(int(self.session.project_id))
        return {
            "context": result_context or self.revision_repo.get_current_context(int(part_id)),
            "affected_part_ids": sorted(set(affected_ids)),
            "analysis": selected,
        }

    def undo_checkout(
        self,
        part_id: int,
        as_user_id: int | None = None,
        *,
        exact_item: bool = False,
    ) -> bool:
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
        active_cad = []
        for related in related_parts:
            active_cad.extend(self.checked_out_cad_for_item(int(related.id)))
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
        if effective_user_id != actor and not self.permission_repo.user_has_permission(
            actor, "merge", self.session.project_id
        ):
            raise PermissionError("Only Master/Admin can undo checkout for another user.")
        seen_cad_ids = set()
        for cad in active_cad:
            cad_id = int(cad["id"])
            if cad_id in seen_cad_ids:
                continue
            seen_cad_ids.add(cad_id)
            cad_owner = cad.get("checked_out_by")
            if cad_owner is not None and int(cad_owner) != int(effective_user_id):
                raise ValueError(
                    f"Associated CAD Document {cad.get('file_name') or cad_id} "
                    "is checked out by another user."
                )

        # Restore first. If restoration fails, keep the lock so no partial working
        # configuration is exposed as checked in.
        restored_contexts = {}
        for related in related_parts:
            related_id = int(related.id)
            restored_contexts[related_id] = self.revision_repo.restore_checked_in_state(related_id)
        for cad in active_cad:
            cad_id = int(cad["id"])
            if cad_id not in seen_cad_ids:
                continue
            self.undo_checkout_pdm_cad_document(
                cad_id,
                "Associated Item checkout was undone",
                as_user_id=effective_user_id,
            )
            seen_cad_ids.discard(cad_id)
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
        affected = [int(related.id) for related in related_parts]
        self.emit_project_event(
            "item.undo_checkout",
            entity_type="ITEM",
            entity_id=int(part_id),
            payload={
                "item_ids": affected,
                "cad_document_ids": sorted(set(int(cad["id"]) for cad in active_cad if cad.get("id") is not None)),
            },
            actor_user_id=effective_user_id,
        )
        self._tree_dirty.add(int(self.session.project_id))
        return True

    def checkout_by_part_id(
        self,
        part_id: int,
        user_id: int,
        *,
        exact_item: bool = False,
        checkout_origin: str = "ITEM",
    ):
        part = self.bom_repo.get_by_id(int(part_id))
        if not part:
            raise ValueError("Part not found")
        origin = str(checkout_origin or "ITEM").strip().upper()
        if origin not in {"ITEM", "CAD"}:
            raise ValueError(f"Unsupported checkout origin: {checkout_origin}.")
        related_parts = self._checkout_scope(part, exact_item=bool(exact_item))
        if exact_item and len(related_parts) == 1:
            existing = self.lock_repo.get_by_part(int(part.id))
            if existing:
                if int(existing.user_id) != int(user_id):
                    raise ValueError("This Item is checked out by another user.")
                if origin == "ITEM":
                    self.lock_repo.upgrade_to_item_checkout(int(part.id), int(user_id))
                    self.emit_project_event(
                        "item.checkout",
                        entity_type="ITEM",
                        entity_id=int(part.id),
                        payload={"item_ids": [int(part.id)], "origin": origin},
                        actor_user_id=int(user_id),
                    )
                return True
        for related in related_parts:
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
                checkout_origin=origin,
            ):
                raise ValueError("Failed to check out part")
            self.bom_repo.checkout_bom(int(related.id))
            if released:
                self.revision_repo.prepare_released_checkout(int(related.id), target_revision)
            self.revision_repo.initialize_checkout(int(related.id), int(user_id))
        affected = [int(related.id) for related in related_parts]
        self.emit_project_event(
            "item.checkout",
            entity_type="ITEM",
            entity_id=int(part_id),
            payload={"item_ids": affected, "origin": origin},
            actor_user_id=int(user_id),
        )
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
        If include_all_revisions is True, it aggregates the same immutable Item Number
        across all projects in the current project's version family.
        """

        part = self.bom_repo.get_by_id(part_id)
        if not part:
            return []

        aes = (getattr(part, "aes_number", None) or "").strip()
        item_number = (getattr(part, "part_number", None) or "").strip()
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

            # Resolve Item ids across project versions by immutable Item Number.
            # AES is retained only as a compatibility fallback for legacy rows.
            part_ids: List[int] = [int(part_id)]
            if include_all_revisions and (item_number or aes) and family_project_ids:
                identity_column = "part_number" if item_number else "aes_number"
                identity_value = item_number or aes
                rows = conn.execute(
                    f"SELECT id, project_id, revision, lifecycle_state, released_by, released_at "
                    f"FROM bom WHERE lower(trim({identity_column}))=lower(?) "
                    f"AND project_id IN ({','.join(['?']*len(family_project_ids))})",
                    (identity_value, *family_project_ids),
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
            d["ebom_behavior"] = "INHERIT"
            d["resolved_ebom_behavior"] = normalize_default_behavior(
                d.get("default_ebom_behavior")
            )
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
        try:
            sources = self.pdm_service.item_where_used(
                int(self.session.project_id), int(child_id)
            )
            if sources:
                return [
                    {
                        "parent_id": int(row["effective_parent_bom_id"]),
                        "parent_name": str(row.get("parent_name") or ""),
                        "parent_part_number": str(row.get("parent_item_number") or ""),
                        "parent_aes_number": str(row.get("parent_aes_number") or ""),
                        "quantity": max(1, int(row.get("source_quantity") or 1)),
                    }
                    for row in sources
                    if row.get("effective_parent_bom_id") is not None
                ]
        except Exception:
            pass
        sources = []
        for relation in self.children_repo.get_parents(int(child_id)) or []:
            parent = self.bom_repo.get_by_id(int(relation.parent_id))
            if not parent or int(parent.project_id or 0) != int(self.session.project_id):
                continue
            sources.append({
                "parent_id": int(parent.id),
                "parent_name": str(parent.name or ""),
                "parent_part_number": str(parent.part_number or ""),
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

        children_by_parent = defaultdict(list)
        try:
            with self.pdm_service.repo.get_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT parent_item_id,child_item_id
                    FROM item_usages
                    WHERE project_id=?
                    """,
                    (int(self.session.project_id),),
                ).fetchall()
                for row in rows:
                    children_by_parent[int(row["parent_item_id"])].append(
                        int(row["child_item_id"])
                    )
        except Exception:
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

        result = self.pdm_service.repo.apply_item_usage_relations(
            int(self.session.project_id), int(target_parent_id), normalized, action
        )
        self.pdm_service.repo.capture_item_structure_iteration(
            int(target_parent_id), "MANUAL", created_by=self.user_id
        )
        for source_parent_id in result.get("source_parent_ids") or []:
            self.pdm_service.repo.capture_item_structure_iteration(
                int(source_parent_id), "MANUAL", created_by=self.user_id
            )
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
        segments = []
        for value in path or ():
            if isinstance(value, (tuple, list)):
                part_id = int(value[0])
                usage_id = value[1] if len(value) > 1 else None
                segment = str(part_id)
                if usage_id is not None:
                    segment += f":u{int(usage_id)}"
                segments.append(segment)
            else:
                segments.append(str(int(value)))
        return "/".join(segments)

    @staticmethod
    def _path_part_ids(path) -> tuple[int, ...]:
        raw_segments = str(path or "").split("/") if isinstance(path, str) else path or ()
        result = []
        for value in raw_segments:
            if isinstance(value, (tuple, list)):
                value = value[0]
            else:
                value = str(value).split(":u", 1)[0]
            if str(value).strip():
                result.append(int(value))
        return tuple(result)

    def _build_lazy_index(self, project_id: int) -> Dict:
        """Build a small ID-only index used by lazy rows and permanent numbering."""
        pid = int(project_id)
        if pid not in self._tree_dirty and pid in self._lazy_index_cache:
            return self._lazy_index_cache[pid]

        part_ids = self.bom_repo.get_project_ids(pid)
        part_set = set(part_ids)
        children = defaultdict(list)
        occurrences = defaultdict(list)
        all_children = set()
        for row in self.children_repo.get_structure_rows(pid):
            parent_id = int(row["parent_id"])
            child_id = int(row["child_id"])
            if parent_id in part_set and child_id in part_set:
                children[parent_id].append(child_id)
                occurrences[parent_id].append(dict(row))
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

        def walk_item(part_id: int, parent_path: tuple, usage_id=None):
            nonlocal row_number
            path = parent_path + ((int(part_id), usage_id),)
            if path in visited_paths:
                return
            visited_paths.add(path)
            row_number += 1
            path_rows[self._path_key(path)] = row_number
            part_rows[int(part_id)].append(row_number)
            if int(part_id) in self._path_part_ids(parent_path):
                return
            walk_context(int(part_id), occurrences.get(int(part_id), []), path)

        def walk_context(parent_id, item_occurrences, parent_path: tuple):
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

            for occurrence in item_occurrences:
                item_id = int(occurrence["child_id"])
                if int(item_id) not in assigned:
                    walk_item(
                        int(item_id), parent_path, occurrence.get("usage_id")
                    )

            def walk_folder(folder, folder_parent_path: str):
                nonlocal row_number
                row_number += 1
                folder_rows[int(folder["id"])] = row_number
                folder_path = f"{folder_parent_path}/f{int(folder['id'])}" if folder_parent_path else f"f{int(folder['id'])}"
                folder_path_rows[folder_path] = row_number
                allowed = {
                    int(occurrence["child_id"]) for occurrence in item_occurrences
                }
                for item_id in folder.get("item_ids") or []:
                    if int(item_id) not in allowed:
                        continue
                    for occurrence in item_occurrences:
                        if int(occurrence["child_id"]) == int(item_id):
                            walk_item(
                                int(item_id), parent_path,
                                occurrence.get("usage_id"),
                            )
                for child_folder in by_parent_folder.get(int(folder["id"]), []):
                    walk_folder(child_folder, folder_path)

            for folder in roots_for_context:
                walk_folder(folder, self._path_key(parent_path))

        walk_context(
            None,
            [
                {"child_id": root_id, "usage_id": None}
                for root_id in roots
            ],
            (),
        )
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
            "occurrences": {key: list(value) for key, value in occurrences.items()},
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

    def _lazy_level_nodes(
        self, project_id: int, ids, parent_path=(), occurrence_rows=None
    ) -> List[Dict]:
        pid = int(project_id)
        index = self._build_lazy_index(pid)
        path_prefix_key = self._path_key(parent_path)
        ancestor_ids = self._path_part_ids(path_prefix_key)
        occurrence_rows = list(occurrence_rows or [])
        requested_ids = (
            [int(row["child_id"]) for row in occurrence_rows]
            if occurrence_rows else list(ids or [])
        )
        parts = self.bom_repo.get_many(pid, requested_ids)
        parts_by_id = {int(part.id): part for part in parts}
        categories = self.bom_repo.get_categories_for_boms(part.id for part in parts)
        versions = self.revision_repo.get_current_contexts(part.id for part in parts)
        try:
            lock_owner = self.lock_repo.get_lock_owners_for_project(pid)
        except Exception:
            lock_owner = {}
        nodes = []
        sources = (
            [(parts_by_id.get(int(row["child_id"])), row) for row in occurrence_rows]
            if occurrence_rows else [(part, None) for part in parts]
        )
        for part, occurrence in sources:
            if part is None:
                continue
            node = part.__dict__.copy()
            usage_id = occurrence.get("usage_id") if occurrence else None
            segment = str(int(part.id))
            if usage_id is not None:
                segment += f":u{int(usage_id)}"
            path = f"{path_prefix_key}/{segment}" if path_prefix_key else segment
            node["children"] = []
            node["category_names"] = list(categories.get(int(part.id), []))
            version = versions.get(int(part.id), {})
            node["current_version"] = version.get("version_label") or node.get("revision")
            node["iteration_number"] = version.get("iteration_number")
            node["binding_update_count"] = int(
                (index.get("binding_update_counts") or {}).get(int(part.id), 0)
            )
            node["_tree_path"] = path
            node["_tree_row_number"] = index["path_rows"].get(node["_tree_path"], "")
            node["_has_children"] = (
                bool(index["children"].get(int(part.id)))
                and int(part.id) not in ancestor_ids
            )
            node["usage_id"] = int(usage_id) if usage_id is not None else None
            node["relation_parent_id"] = (
                int(occurrence["parent_id"]) if occurrence else None
            )
            node["quantity"] = (
                max(1, int(occurrence.get("quantity") or 1)) if occurrence else 1
            )
            node["ebom_behavior"] = normalize_occurrence_behavior(
                occurrence.get("ebom_behavior") if occurrence else None
            )
            node["resolved_ebom_behavior"] = (
                normalize_default_behavior(node.get("default_ebom_behavior"))
                if node["ebom_behavior"] == "INHERIT"
                else node["ebom_behavior"]
            )
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
        occurrences = index["occurrences"].get(int(parent_id), [])
        return self._lazy_level_nodes(
            int(project_id), child_ids, parent_path, occurrence_rows=occurrences
        )

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
        d["delivery_policy"] = delivery_policy_label(
            d.get("default_ebom_behavior")
        )
        d["cad_dependency_count"] = self.bom_repo.count_cad_dependencies(int(part_id))
        represented_part_id = d.get("represented_part_id")
        if represented_part_id:
            represented = self.bom_repo.get_by_id(int(represented_part_id))
            if represented:
                d["represented_part_name"] = str(represented.name or "")
                d["represented_part_aes"] = str(represented.aes_number or "")
                d["represented_part_number"] = str(represented.part_number or "")
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
                lock = self.lock_repo.get_by_part(int(part_id))
                if lock:
                    d["locked_by_user_id"] = int(lock.user_id)
                    d["checkout_origin"] = str(
                        getattr(lock, "checkout_origin", "ITEM") or "ITEM"
                    ).upper()
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
        for related_id in related_ids:
            self.release_validation_service.assert_bom_releasable(
                related_id, include_children=True
            )
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

    def set_occurrence_ebom_behavior(
        self, parent_id: int, usage_id: int, ebom_behavior: str
    ) -> Dict:
        """Change one usage policy; the checked-out parent owns the new iteration."""
        self._assert_checked_out_for_change(
            int(parent_id), "edit an occurrence's EBOM behavior"
        )
        relation = self.children_repo.set_ebom_behavior(
            int(parent_id), int(usage_id),
            normalize_occurrence_behavior(ebom_behavior),
        )
        self.revision_repo.sync_working_bindings(
            int(parent_id), int(self.user_id)
        )
        self._tree_dirty.add(int(self.session.project_id))
        return relation.__dict__.copy()

    def get_released_ebom(self, root_bom_id: int, iteration_id=None) -> Dict:
        return self.ebom_service.resolve_bom(int(root_bom_id), iteration_id)

    def get_released_ebom_project(self, project_id: int | None = None) -> Dict:
        project_id = int(project_id or self.session.project_id)
        structure = self.pdm_service.get_item_structure_project(project_id)
        nodes = []

        def collect(rows):
            for row in rows or []:
                nodes.append(row)
                collect(row.get("children") or [])

        collect(structure.get("roots") or [])
        item_ids = {
            int(row.get("bom_id") or row.get("id"))
            for row in nodes
            if row.get("bom_id") is not None or row.get("id") is not None
        }
        try:
            contexts = self.revision_repo.get_current_contexts(item_ids)
        except Exception:
            contexts = {}
        try:
            lock_owners = self.lock_repo.get_lock_owners_for_project(project_id)
        except Exception:
            lock_owners = {}
        try:
            category_map = self.bom_repo.get_categories_for_boms(item_ids)
        except Exception:
            category_map = {}
        for row in nodes:
            item_id = int(row.get("bom_id") or row.get("id"))
            context = contexts.get(item_id) or {}
            version_label = str(
                context.get("version_label") or row.get("version_label")
                or row.get("revision") or ""
            )
            row["version_label"] = version_label
            row["current_version"] = version_label
            row["iteration_number"] = context.get("iteration_number")
            row["revision_state"] = str(
                context.get("state") or row.get("lifecycle_state")
                or row.get("status") or ""
            )
            lock_owner = lock_owners.get(item_id)
            row["locked"] = bool(lock_owner)
            row["locked_by_username"] = lock_owner
            row["category_names"] = list(category_map.get(item_id, []))
        return structure

    # -------------------------------
    # PDM CAD DOCUMENT / ITEM DOMAIN
    # -------------------------------
    def list_pdm_cad_documents(self, project_id: int | None = None) -> List[Dict]:
        return self.pdm_service.list_cad_documents(
            int(project_id or self.session.project_id)
        )

    def list_pdm_items(self, project_id: int | None = None) -> List[Dict]:
        rows = self.bom_repo.get_all(int(project_id or self.session.project_id))
        return [
            row.__dict__.copy() for row in rows
            if getattr(row, "represented_part_id", None) is None
        ]

    def create_pdm_cad_document(self, **values) -> int:
        cad_id = self.pdm_service.create_cad_document(
            int(self.session.project_id), **values
        )
        self.emit_project_event(
            "cad.created",
            entity_type="CAD_DOCUMENT",
            entity_id=int(cad_id),
            payload={"cad_document_ids": [int(cad_id)]},
        )
        return cad_id

    def delete_pdm_cad_document(
        self, cad_document_id: int, *, delete_related_drawings: bool = False
    ) -> Dict:
        return self.pdm_service.delete_cad_document(
            int(cad_document_id),
            delete_related_drawings=bool(delete_related_drawings),
        )

    def bind_pdm_drawing_to_model(
        self, drawing_cad_document_id: int, model_cad_document_id: int
    ) -> Dict:
        return self.pdm_service.bind_drawing_to_model(
            int(drawing_cad_document_id), int(model_cad_document_id)
        )

    def get_pdm_cad_structure(self) -> Dict:
        return self.pdm_service.get_cad_structure_project(
            int(self.session.project_id)
        )

    def _assert_owned_cad_checkout(self, cad_document_id: int, action: str) -> Dict:
        document = self.pdm_service.repo.get_cad_document(int(cad_document_id))
        if not document:
            raise ValueError("The CAD Document was not found.")
        owner = document.get("checked_out_by")
        if owner is None:
            for item_id in self.pdm_service.checkout_target_item_ids(
                int(cad_document_id)
            ):
                item_lock = self.lock_repo.get_by_part(int(item_id))
                if item_lock and int(item_lock.user_id) == int(self.user_id):
                    return document
            raise ValueError(
                f"Check out the CAD assembly or its related Item before you {action}."
            )
        if int(owner) != int(self.user_id) and not self.permission_repo.user_has_permission(
            int(self.user_id), "merge", self.session.project_id
        ):
            raise ValueError("The CAD assembly is checked out by another user.")
        return document

    def add_pdm_cad_member(
        self, parent_cad_document_id: int, child_cad_document_id: int,
        quantity: int = 1, build_excluded: bool = False,
    ) -> int:
        self._assert_owned_cad_checkout(
            int(parent_cad_document_id), "change its CAD structure"
        )
        result = self.pdm_service.add_cad_member(
            int(parent_cad_document_id), int(child_cad_document_id),
            int(quantity), build_excluded=bool(build_excluded),
        )
        self.emit_project_event(
            "cad.structure_changed",
            entity_type="CAD_DOCUMENT",
            entity_id=int(parent_cad_document_id),
            payload={"cad_document_ids": [int(parent_cad_document_id), int(child_cad_document_id)]},
        )
        return result

    def ordered_pdm_cad_member_ids(self, parent_cad_document_id: int) -> List[int]:
        return self.pdm_service.repo.ordered_cad_member_ids(int(parent_cad_document_id))

    def reorder_pdm_cad_members(
        self, parent_cad_document_id: int, ordered_member_ids
    ) -> bool:
        self._assert_owned_cad_checkout(
            int(parent_cad_document_id), "reorder its CAD structure"
        )
        current = self.pdm_service.repo.ordered_cad_member_ids(
            int(parent_cad_document_id)
        )
        requested = [int(value) for value in ordered_member_ids or []]
        if set(current) != set(requested):
            raise ValueError("Reorder must keep the same CAD members.")
        result = self.pdm_service.repo.set_cad_member_order(
            int(parent_cad_document_id), requested
        )
        self.emit_project_event(
            "cad.structure_changed",
            entity_type="CAD_DOCUMENT",
            entity_id=int(parent_cad_document_id),
            payload={"cad_document_ids": [int(parent_cad_document_id)]},
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def remove_pdm_cad_member(self, member_id: int) -> bool:
        member = self.pdm_service.repo.get_cad_member(int(member_id))
        if not member:
            return False
        self._assert_owned_cad_checkout(
            int(member["parent_cad_document_id"]), "change its CAD structure"
        )
        result = self.pdm_service.remove_cad_member(int(member_id))
        if result:
            self.emit_project_event(
                "cad.structure_changed",
                entity_type="CAD_DOCUMENT",
                entity_id=int(member["parent_cad_document_id"]),
                payload={
                    "cad_document_ids": [
                        int(member["parent_cad_document_id"]),
                        int(member["child_cad_document_id"]),
                    ]
                },
            )
        return result

    def cad_occurrence_sources_for_document(self, child_cad_document_id: int) -> List[Dict]:
        """Return direct CAD assembly occurrences for a CAD Document."""
        if not self.session.project_id:
            return []
        with self.pdm_service.repo.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT m.id AS member_id,
                       m.parent_cad_document_id,
                       m.child_cad_document_id,
                       m.quantity,
                       m.build_excluded,
                       p.file_name AS parent_file_name,
                       p.name AS parent_name,
                       p.category AS parent_category
                FROM cad_document_members m
                JOIN cad_documents p ON p.id=m.parent_cad_document_id
                WHERE m.child_cad_document_id=?
                  AND p.project_id=?
                ORDER BY lower(COALESCE(p.file_name,'')),m.id
                """,
                (int(child_cad_document_id), int(self.session.project_id)),
            ).fetchall()
            return [dict(row) for row in rows]

    def apply_pdm_cad_member_operation(
        self,
        target_parent_cad_id: int,
        selections,
        mode: str,
    ) -> Dict:
        """Copy or move CAD occurrences under a checked-out ASM CAD Document."""
        action = str(mode or "").strip().lower()
        if action not in {"copy", "move"}:
            raise ValueError("CAD structure operation must be Copy or Move.")
        target = self._assert_owned_cad_checkout(
            int(target_parent_cad_id), "change its CAD structure"
        )
        if str(target.get("category") or "").upper() != "ASSEMBLY":
            raise ValueError("Only an ASM CAD Document can contain CAD members.")

        normalized = []
        affected_parent_ids = {int(target_parent_cad_id)}
        child_ids = set()
        for selection in selections or []:
            member_id = selection.get("member_id")
            member = (
                self.pdm_service.repo.get_cad_member(int(member_id))
                if member_id is not None else None
            )
            child_id = int(
                selection.get("child_cad_document_id")
                or (member or {}).get("child_cad_document_id")
            )
            source_parent_id = selection.get("source_parent_cad_id")
            if source_parent_id is None and member:
                source_parent_id = member.get("parent_cad_document_id")
            source_parent_id = int(source_parent_id) if source_parent_id is not None else None
            if source_parent_id == int(target_parent_cad_id):
                continue
            child = self.pdm_service.repo.get_cad_document(child_id)
            if not child:
                raise ValueError(f"CAD Document {child_id} was not found.")
            if str(child.get("category") or "").upper() not in {"ASSEMBLY", "COMPONENT"}:
                raise ValueError("Drawings cannot be inserted as CAD structure members.")
            if child_id == int(target_parent_cad_id):
                raise ValueError("A CAD assembly cannot contain itself.")
            quantity = max(1, int(selection.get("quantity") or (member or {}).get("quantity") or 1))
            build_excluded = bool(
                selection.get("build_excluded")
                if selection.get("build_excluded") is not None
                else (member or {}).get("build_excluded")
            )
            normalized.append({
                "member_id": int(member_id) if member_id is not None else None,
                "child_cad_document_id": child_id,
                "source_parent_cad_id": source_parent_id,
                "quantity": quantity,
                "build_excluded": build_excluded,
            })
            child_ids.add(child_id)
            if action == "move" and source_parent_id is not None:
                affected_parent_ids.add(source_parent_id)

        if not normalized:
            raise ValueError("Select at least one CAD occurrence outside the target assembly.")
        if action == "move":
            for source_parent_id in sorted(affected_parent_ids - {int(target_parent_cad_id)}):
                self._assert_owned_cad_checkout(
                    int(source_parent_id), "move CAD occurrences from it"
                )

        added_member_ids = []
        removed_member_ids = []
        for row in normalized:
            added_member_ids.append(
                self.pdm_service.add_cad_member(
                    int(target_parent_cad_id),
                    int(row["child_cad_document_id"]),
                    int(row["quantity"]),
                    build_excluded=bool(row["build_excluded"]),
                )
            )
            if action == "move" and row.get("member_id") is not None:
                if self.pdm_service.remove_cad_member(int(row["member_id"])):
                    removed_member_ids.append(int(row["member_id"]))

        return {
            "mode": action,
            "target_parent_cad_id": int(target_parent_cad_id),
            "source_parent_cad_ids": sorted(affected_parent_ids - {int(target_parent_cad_id)}),
            "child_cad_document_ids": sorted(child_ids),
            "added_member_ids": added_member_ids,
            "removed_member_ids": removed_member_ids,
        }

    def checkout_pdm_cad_document(
        self,
        cad_document_id: int,
        *,
        released_item_revision_code: str | None = None,
        released_item_revision_codes: dict | None = None,
        as_user_id: int | None = None,
        workspace_id: str | None = None,
        workspace_name: str | None = None,
        workspace_machine_id: str | None = None,
        checkout_item_ids: list[int] | tuple[int, ...] | None = None,
    ) -> Dict:
        """Check out CAD and coordinate every affected Item working copy."""
        cad_document_id = int(cad_document_id)
        if self.user_id is None:
            raise PermissionError("You must be logged in.")
        current_user_id = int(self.user_id)
        actor_id = int(as_user_id) if as_user_id is not None else current_user_id
        if actor_id != current_user_id and not self.permission_repo.user_has_permission(
            current_user_id, "merge", self.session.project_id
        ):
            raise PermissionError("Only Master/Admin can check out CAD for another user.")
        document = self.pdm_service.repo.get_cad_document(cad_document_id)
        if not document:
            raise ValueError("The CAD Document was not found.")
        owner = document.get("checked_out_by")
        if owner is not None and int(owner) != actor_id:
            raise ValueError("The CAD Document is checked out by another user.")
        state = str(document.get("lifecycle_state") or "").strip().upper()
        if state == "RELEASED":
            raise ValueError(
                "The CAD Document is Released. Create a new CAD revision before check out."
            )
        if state == "OBSOLETE":
            raise ValueError("An Obsolete CAD Document cannot be checked out.")

        associated_item_ids = (
            [int(value) for value in checkout_item_ids]
            if checkout_item_ids is not None
            else self.pdm_service.checkout_target_item_ids(cad_document_id)
        )
        revision_codes = {
            int(item_id): str(value or "").strip()
            for item_id, value in (released_item_revision_codes or {}).items()
        }
        effective_revision_codes = {}

        # Preflight every target before creating any working copy. A shared CAD
        # checkout is one operation and must not leave a partial set of locks.
        for associated_item_id in associated_item_ids:
            item_lock = self.lock_repo.get_by_part(int(associated_item_id))
            if item_lock and int(item_lock.user_id) != actor_id:
                raise ValueError(
                    f"Associated Item {associated_item_id} is checked out by another user. "
                    "The CAD Document was not checked out."
                )
            if item_lock:
                continue
            context = self.revision_repo.get_current_context(
                int(associated_item_id)
            )
            state = str(context.get("state") or "").strip().lower()
            revision_code = revision_codes.get(
                int(associated_item_id),
                str(released_item_revision_code or "").strip(),
            )
            effective_revision_codes[int(associated_item_id)] = revision_code
            if state == "released":
                pending_revision = str(
                    context.get("pending_revision_code") or ""
                ).strip()
                target_revision = revision_code or pending_revision
                if not target_revision:
                    raise ValueError(
                        f"Associated Item {context.get('version_label') or associated_item_id} "
                        "is Released. Provide its next Item revision before checking out the shared CAD Document."
                    )
                if pending_revision and pending_revision.casefold() != target_revision.casefold():
                    raise ValueError(
                        f"Associated Item {associated_item_id} is already preparing "
                        f"revision {pending_revision}."
                    )
                if not pending_revision:
                    self.revision_repo.validate_released_checkout(
                        int(associated_item_id), target_revision
                    )
                effective_revision_codes[int(associated_item_id)] = target_revision
            else:
                self.revision_repo.assert_mutable(int(associated_item_id))

        auto_item_checkout_ids = []
        try:
            for associated_item_id in associated_item_ids:
                if self.lock_repo.get_by_part(int(associated_item_id)):
                    continue
                revision_code = effective_revision_codes.get(
                    int(associated_item_id), ""
                )
                self.checkout_part(
                    int(associated_item_id),
                    as_user_id=actor_id,
                    released_revision_code=revision_code or None,
                    exact_item=True,
                    checkout_origin="CAD",
                )
                auto_item_checkout_ids.append(int(associated_item_id))
            result = self.pdm_service.checkout_cad_document(
                cad_document_id,
                actor_id,
                workspace_id=workspace_id,
                workspace_name=workspace_name,
                workspace_machine_id=workspace_machine_id,
            )
            related_drawing_ids = []
            if str(document.get("category") or "").upper() != "DRAWING":
                for drawing in self.pdm_service.repo.list_related_drawings(cad_document_id) or []:
                    drawing_id = int(drawing["id"])
                    drawing_owner = drawing.get("checked_out_by")
                    if drawing_owner is not None and int(drawing_owner) != actor_id:
                        raise ValueError(
                            f"Related drawing {drawing.get('file_name') or drawing_id} "
                            "is checked out by another user."
                        )
                    if str(drawing.get("lifecycle_state") or "").upper() == "RELEASED":
                        raise ValueError(
                            f"Related drawing {drawing.get('file_name') or drawing_id} "
                            "is Released. Create its next CAD revision before checkout."
                        )
                    self.pdm_service.checkout_cad_document(
                        drawing_id,
                        actor_id,
                        workspace_id=workspace_id,
                        workspace_name=workspace_name,
                        workspace_machine_id=workspace_machine_id,
                    )
                    related_drawing_ids.append(drawing_id)
        except Exception:
            for drawing_id in reversed(locals().get("related_drawing_ids", [])):
                try:
                    self.pdm_service.undo_checkout_cad_document(
                        int(drawing_id), actor_id, "Parent CAD checkout failed"
                    )
                except Exception:
                    pass
            try:
                if "result" in locals():
                    self.pdm_service.undo_checkout_cad_document(
                        cad_document_id, actor_id, "Related drawing checkout failed"
                    )
            except Exception:
                pass
            for associated_item_id in reversed(auto_item_checkout_ids):
                try:
                    self.undo_checkout(
                        int(associated_item_id),
                        as_user_id=actor_id,
                        exact_item=True,
                    )
                except Exception:
                    pass
            raise

        recorded_item_ids = self.pdm_service.cad_checkout_item_ids(
            cad_document_id
        )
        if not recorded_item_ids:
            recorded_item_ids = list(associated_item_ids)
        cad_ids = [int(cad_document_id), *related_drawing_ids]
        self.emit_project_event(
            "cad.checkout",
            entity_type="CAD_DOCUMENT",
            entity_id=int(cad_document_id),
            payload={
                "cad_document_ids": sorted(set(cad_ids)),
                "item_ids": sorted(set(int(value) for value in recorded_item_ids)),
            },
            actor_user_id=actor_id,
        )
        return {
            **result,
            "associated_item_ids": recorded_item_ids,
            "associated_item_id": (
                recorded_item_ids[0] if recorded_item_ids else None
            ),
            "related_drawing_checkout_ids": related_drawing_ids,
            "item_checkout_auto_created": bool(auto_item_checkout_ids),
            "item_checkout_auto_created_ids": auto_item_checkout_ids,
        }

    def checkin_pdm_cad_document(
        self,
        cad_document_id: int,
        source_path: str,
        note: str = "",
        *,
        as_user_id: int | None = None,
        source_commit_id: str | None = None,
        source_file_name: str | None = None,
        creo_file_version: int | None = None,
    ) -> Dict:
        cad_document_id = int(cad_document_id)
        current_user_id = int(self.user_id)
        actor_id = int(as_user_id) if as_user_id is not None else current_user_id
        if actor_id != current_user_id and not self.permission_repo.user_has_permission(
            current_user_id, "merge", self.session.project_id
        ):
            raise PermissionError("Only Master/Admin can check in CAD for another user.")
        document = self.pdm_service.repo.get_cad_document(cad_document_id)
        if not document:
            raise ValueError("The CAD Document was not found.")
        associated_item_ids = self.pdm_service.cad_checkout_item_ids(
            cad_document_id
        )
        if not associated_item_ids and document.get("checkout_item_id") is not None:
            associated_item_ids = [int(document["checkout_item_id"])]
        try:
            association_item_ids = self.pdm_service.checkout_target_item_ids(
                cad_document_id
            )
            associated_item_ids = sorted({
                int(value)
                for value in [
                    *(associated_item_ids or []),
                    *(association_item_ids or []),
                ]
                if value is not None
            })
        except Exception:
            pass
        result = self.pdm_service.checkin_cad_document(
            cad_document_id,
            actor_id,
            source_path,
            note,
            source_commit_id=source_commit_id,
            source_file_name=source_file_name,
            creo_file_version=creo_file_version,
        )
        returned_item_ids = [
            int(value) for value in (result.get("checkout_item_ids") or [])
        ]
        if returned_item_ids:
            associated_item_ids = sorted({
                int(value)
                for value in [*(associated_item_ids or []), *returned_item_ids]
                if value is not None
            })
        item_result = self._release_auto_item_checkouts_after_cad(
            associated_item_ids, actor_id
        )
        try:
            from core.services.cad_workspace_service import CadWorkspaceService
            workspace_service = CadWorkspaceService()
            workspace_service.release_cad_document(
                document.get("checkout_workspace_id"), cad_document_id
            )
            if str(document.get("category") or "").upper() != "DRAWING":
                for drawing in self.pdm_service.repo.list_related_drawings(cad_document_id) or []:
                    workspace_service.release_cad_document(
                        document.get("checkout_workspace_id"), int(drawing["id"])
                    )
        except Exception:
            pass
        combined = {**result, **item_result}
        item_ids = sorted(set(int(value) for value in (associated_item_ids or [])))
        cad_ids = [int(cad_document_id)]
        if str(document.get("category") or "").upper() != "DRAWING":
            try:
                cad_ids.extend(
                    int(drawing["id"])
                    for drawing in self.pdm_service.repo.list_related_drawings(cad_document_id) or []
                    if drawing.get("id") is not None
                )
            except Exception:
                pass
        self.emit_project_event(
            "cad.checkin",
            entity_type="CAD_DOCUMENT",
            entity_id=int(cad_document_id),
            payload={"cad_document_ids": sorted(set(cad_ids)), "item_ids": item_ids},
            actor_user_id=actor_id,
        )
        return combined

    def undo_checkout_pdm_cad_document(
        self, cad_document_id: int, note: str = "", *, as_user_id: int | None = None
    ) -> Dict:
        cad_document_id = int(cad_document_id)
        current_user_id = int(self.user_id)
        actor_id = int(as_user_id) if as_user_id is not None else current_user_id
        if actor_id != current_user_id and not self.permission_repo.user_has_permission(
            current_user_id, "merge", self.session.project_id
        ):
            raise PermissionError("Only Master/Admin can undo CAD checkout for another user.")
        document = self.pdm_service.repo.get_cad_document(cad_document_id)
        if not document:
            raise ValueError("The CAD Document was not found.")
        related_drawing_ids = []
        if str(document.get("category") or "").upper() != "DRAWING":
            for drawing in self.pdm_service.repo.list_related_drawings(cad_document_id) or []:
                if drawing.get("checked_out_by") is None:
                    continue
                if int(drawing.get("checked_out_by")) != actor_id:
                    continue
                try:
                    self.pdm_service.undo_checkout_cad_document(
                        int(drawing["id"]), actor_id, note or "Parent CAD checkout undone"
                    )
                    related_drawing_ids.append(int(drawing["id"]))
                except Exception:
                    pass
        result = self.pdm_service.undo_checkout_cad_document(
            cad_document_id, actor_id, note
        )
        try:
            from core.services.cad_workspace_service import CadWorkspaceService
            workspace_service = CadWorkspaceService()
            workspace_service.release_cad_document(
                document.get("checkout_workspace_id"), cad_document_id
            )
            for drawing_id in related_drawing_ids:
                workspace_service.release_cad_document(
                    document.get("checkout_workspace_id"), drawing_id
                )
        except Exception:
            pass
        payload = {
            "cad_document_ids": sorted(set([int(cad_document_id), *related_drawing_ids])),
            "item_ids": sorted(set(int(value) for value in (result.get("checkout_item_ids") or result.get("associated_item_ids") or []) if value is not None)),
        }
        self.emit_project_event(
            "cad.undo_checkout",
            entity_type="CAD_DOCUMENT",
            entity_id=int(cad_document_id),
            payload=payload,
            actor_user_id=actor_id,
        )
        return {
            **result,
            "related_drawing_checkout_ids": related_drawing_ids,
            "item_checkout": "RETAINED_BY_RULE",
        }

    def revise_pdm_cad_document(self, cad_document_id: int) -> Dict:
        return self.pdm_service.revise_cad_document(
            int(cad_document_id), int(self.user_id)
        )

    def release_pdm_cad_document(self, cad_document_id: int) -> Dict:
        return self.pdm_service.release_cad_document(int(cad_document_id))

    def list_item_cad_associations(self, item_id: int) -> List[Dict]:
        return self.pdm_service.list_item_cad_documents(int(item_id))

    def list_cad_item_associations(self, cad_document_id: int) -> List[Dict]:
        return self.pdm_service.list_cad_item_associations(
            int(cad_document_id)
        )

    def get_item_cad_association(
        self, item_id: int, cad_document_id: int
    ) -> Dict | None:
        return self.pdm_service.get_item_cad_association(
            int(item_id), int(cad_document_id)
        )

    def list_item_selected_drawings(
        self, item_id: int, model_cad_document_id: int | None = None
    ) -> List[Dict]:
        return self.pdm_service.list_item_selected_drawings(
            int(item_id),
            (
                int(model_cad_document_id)
                if model_cad_document_id is not None else None
            ),
        )

    def _assert_cad_documents_checked_in(self, cad_document_ids, action: str) -> None:
        for cad_document_id in sorted({int(value) for value in cad_document_ids}):
            document = self.pdm_service.repo.get_cad_document(cad_document_id)
            if not document:
                raise ValueError(f"CAD Document {cad_document_id} was not found.")
            if document.get("checked_out_by") is not None:
                raise ValueError(
                    f"Check in or undo {document.get('file_name') or document.get('name') or cad_document_id} "
                    f"before you {action}."
                )

    def set_item_model_drawings(
        self,
        item_id: int,
        model_cad_document_id: int,
        drawing_ids,
        primary_drawing_id: int | None = None,
    ) -> List[Dict]:
        """Replace the explicit drawings selected for one Item/model pair."""
        item_id = int(item_id)
        model_cad_document_id = int(model_cad_document_id)
        selected_ids = sorted({int(value) for value in (drawing_ids or [])})
        if primary_drawing_id is None and len(selected_ids) == 1:
            primary_drawing_id = selected_ids[0]
        primary_id = (
            int(primary_drawing_id)
            if primary_drawing_id is not None else None
        )
        if primary_id is not None and primary_id not in selected_ids:
            raise ValueError("The primary drawing must be included in the selected drawings.")

        self._assert_checked_out_for_change(
            item_id, "change its selected CAD drawings"
        )
        if not self.pdm_service.get_item_cad_association(
            item_id, model_cad_document_id
        ):
            raise ValueError(
                "Associate the PRT/ASM CAD Document with this Item before selecting its drawing."
            )
        current_drawing_ids = [
            int(row["id"])
            for row in self.pdm_service.list_item_selected_drawings(
                item_id, model_cad_document_id
            )
            if row.get("id") is not None
        ]
        self._assert_cad_documents_checked_in(
            [model_cad_document_id, *current_drawing_ids, *selected_ids],
            "change the Item drawing selection",
        )
        result = self.pdm_service.set_item_model_drawings(
            item_id,
            model_cad_document_id,
            selected_ids,
            primary_drawing_id=primary_id,
            actor_id=self.user_id,
        )
        self.lock_repo.set_checkout_origin(item_id, "ITEM")
        return result

    def set_item_primary_drawing(
        self,
        item_id: int,
        model_cad_document_id: int,
        drawing_cad_document_id: int,
    ) -> Dict:
        item_id = int(item_id)
        model_cad_document_id = int(model_cad_document_id)
        drawing_cad_document_id = int(drawing_cad_document_id)
        self._assert_checked_out_for_change(
            item_id, "change its primary drawing"
        )
        current_drawing_ids = [
            int(row["id"])
            for row in self.pdm_service.list_item_selected_drawings(
                item_id, model_cad_document_id
            )
            if row.get("id") is not None
        ]
        self._assert_cad_documents_checked_in(
            [
                model_cad_document_id,
                drawing_cad_document_id,
                *current_drawing_ids,
            ],
            "change the Item primary drawing",
        )
        result = self.pdm_service.set_primary_drawing(
            item_id,
            model_cad_document_id,
            drawing_cad_document_id,
            actor_id=self.user_id,
        )
        self.lock_repo.set_checkout_origin(item_id, "ITEM")
        return result

    def clear_item_primary_drawing(
        self, item_id: int, model_cad_document_id: int
    ) -> bool:
        item_id = int(item_id)
        model_cad_document_id = int(model_cad_document_id)
        self._assert_checked_out_for_change(
            item_id, "clear its primary drawing"
        )
        current_drawing_ids = [
            int(row["id"])
            for row in self.pdm_service.list_item_selected_drawings(
                item_id, model_cad_document_id
            )
            if row.get("id") is not None
        ]
        self._assert_cad_documents_checked_in(
            [model_cad_document_id, *current_drawing_ids],
            "clear the Item primary drawing",
        )
        changed = self.pdm_service.clear_primary_drawing(
            item_id, model_cad_document_id
        )
        if changed:
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
        return changed

    def associate_cad_document(
        self, item_id: int, cad_document_id: int, association_type: str
    ) -> Dict:
        item_id = int(item_id)
        cad_document_id = int(cad_document_id)
        current = self.pdm_service.get_item_cad_association(
            item_id, cad_document_id
        )
        document = self.pdm_service.repo.get_cad_document(cad_document_id)
        if not document:
            raise ValueError("The CAD Document was not found.")
        requested_type = str(association_type or "").strip().upper()
        if (
            current
            and str(current.get("association_type") or "").strip().upper()
            == requested_type
        ):
            return current
        if document.get("checked_out_by") is not None:
            raise ValueError(
                "Check in or undo the CAD Document before changing its Item association."
            )
        self._assert_checked_out_for_change(
            item_id, "change its CAD associations"
        )
        result = self.pdm_service.associate(
            int(self.session.project_id), item_id, cad_document_id,
            requested_type, self.user_id,
        )
        self.lock_repo.set_checkout_origin(item_id, "ITEM")
        self.emit_project_event(
            "association.changed",
            entity_type="ITEM",
            entity_id=int(item_id),
            payload={"item_ids": [int(item_id)], "cad_document_ids": [int(cad_document_id)]},
        )
        return result

    def remove_cad_item_association(self, association_id: int) -> bool:
        association = self.pdm_service.repo.get_association(int(association_id))
        if not association:
            return False
        document = self.pdm_service.repo.get_cad_document(
            int(association["cad_document_id"])
        )
        if document and document.get("checked_out_by") is not None:
            raise ValueError(
                "Check in or undo the CAD Document before removing its Item association."
            )
        self._assert_checked_out_for_change(
            int(association["item_id"]), "change its CAD associations"
        )
        removed = self.pdm_service.remove_association(int(association_id))
        if removed:
            item_id = int(association["item_id"])
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
            try:
                remaining = self.pdm_service.list_item_cad_documents(item_id) or []
            except Exception:
                remaining = []
            if not remaining:
                try:
                    self.bom_repo.clear_legacy_cad_links(item_id)
                except Exception:
                    pass
            self.emit_project_event(
                "association.changed",
                entity_type="ITEM",
                entity_id=int(item_id),
                payload={
                    "item_ids": [int(item_id)],
                    "cad_document_ids": [int(association["cad_document_id"])],
                },
            )
        return removed

    def auto_associate_cad_documents(self) -> Dict:
        proposals = self.pdm_service.auto_associate_candidates(
            int(self.session.project_id)
        )
        item_ids = {
            int(proposal["matches"][0]["id"])
            for proposal in proposals
            if proposal.get("status") == "MATCH"
            and len(proposal.get("matches") or []) == 1
        }
        for item_id in sorted(item_ids):
            self._assert_checked_out_for_change(
                item_id, "apply automatic CAD associations"
            )
        result = self.pdm_service.apply_auto_associate(
            int(self.session.project_id), self.user_id
        )
        for item_id in item_ids:
            self.lock_repo.set_checkout_origin(item_id, "ITEM")
        return result

    def compare_cad_to_item_structure(self, item_id: int) -> Dict:
        owner = self.pdm_service.owner_cad_for_item(int(item_id))
        if not owner:
            raise ValueError("This Item has no OWNER CAD Document.")
        return self.pdm_service.compare_cad_to_item(int(owner["id"]))

    def build_item_structure_from_cad(
        self, item_id: int, multi_level: bool = True
    ) -> Dict:
        self._assert_checked_out_for_change(
            int(item_id), "build its Item Structure from CAD"
        )
        owner = self.pdm_service.owner_cad_for_item(int(item_id))
        if not owner:
            raise ValueError("This Item has no OWNER CAD Document.")
        result = self.pdm_service.build_part_structure(
            int(owner["id"]), multi_level=bool(multi_level), actor_id=self.user_id
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def add_manual_item_usage(
        self, parent_item_id: int, child_item_id: int, quantity: int = 1
    ) -> int:
        self._assert_checked_out_for_change(
            int(parent_item_id), "change its Item Structure"
        )
        result = self.pdm_service.add_manual_item_usage(
            int(self.session.project_id), int(parent_item_id), int(child_item_id),
            int(quantity), self.user_id,
        )
        self.emit_project_event(
            "item.structure_changed",
            entity_type="ITEM",
            entity_id=int(parent_item_id),
            payload={"item_ids": [int(parent_item_id), int(child_item_id)]},
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def ordered_pdm_item_usage_ids(self, parent_item_id: int) -> List[int]:
        return self.pdm_service.repo.ordered_item_usage_ids(int(parent_item_id))

    def reorder_pdm_item_usages(self, parent_item_id: int, ordered_usage_ids) -> bool:
        self._assert_checked_out_for_change(
            int(parent_item_id), "reorder its Item Structure"
        )
        current = self.pdm_service.repo.ordered_item_usage_ids(int(parent_item_id))
        requested = [int(value) for value in ordered_usage_ids or []]
        if set(current) != set(requested):
            raise ValueError("Reorder must keep the same Item usages.")
        result = self.pdm_service.repo.set_item_usage_order(
            int(parent_item_id), requested
        )
        self.pdm_service.repo.capture_item_structure_iteration(
            int(parent_item_id), "MANUAL", created_by=self.user_id
        )
        self.emit_project_event(
            "item.structure_changed",
            entity_type="ITEM",
            entity_id=int(parent_item_id),
            payload={"item_ids": [int(parent_item_id)]},
        )
        self._tree_dirty.add(int(self.session.project_id))
        return result

    def get_effective_ebom_where_used(self, child_bom_id: int) -> List[Dict]:
        if not self.session.project_id:
            return []
        return self.pdm_service.item_where_used(
            int(self.session.project_id), int(child_bom_id)
        )

    def export_released_ebom(
        self, root_bom_id: int, file_path: str, iteration_id=None
    ) -> Dict:
        return self.ebom_export_service.export_bom(
            int(root_bom_id), file_path, iteration_id
        )

    def export_item_structure(self, root_item_id: int, file_path: str) -> Dict:
        return self.pdm_service.export_item_structure(
            int(root_item_id), file_path
        )

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
        result = self.pdm_service.repo.remove_item_usages_from_parent(
            int(self.session.project_id), int(parent_id), child_ids
        )
        self.pdm_service.repo.capture_item_structure_iteration(
            int(parent_id), "MANUAL", created_by=self.user_id
        )
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
        removed_child_ids = [int(value) for value in (result.get("removed_child_ids") or child_ids or [])]
        self.emit_project_event(
            "item.structure_changed",
            entity_type="ITEM",
            entity_id=int(parent_id),
            payload={
                "item_ids": sorted(set([int(parent_id), *removed_child_ids])),
                "removed_child_ids": removed_child_ids,
            },
        )
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
        effective_where_used = part_node(int(part_id), "Selected Item")
        effective_count = 0
        effective_error = ""
        if effective_where_used:
            try:
                for occurrence in self.get_effective_ebom_where_used(int(part_id)):
                    parent_id = int(occurrence["effective_parent_bom_id"])
                    parent = part_node(
                        parent_id,
                        "Effective EBOM Parent",
                        occurrence.get("effective_quantity"),
                        usage_id=occurrence.get("usage_id"),
                        relation_parent_id=parent_id,
                    )
                    if not parent:
                        continue
                    promoted = list(occurrence.get("promoted_through") or [])
                    parent["relation"] = (
                        "Effective Parent (promoted)" if promoted
                        else "Effective EBOM Parent"
                    )
                    parent["promotion_path"] = " > ".join(
                        str(
                            value.get("aes_number")
                            or value.get("name")
                            or value.get("bom_id")
                        )
                        for value in promoted
                    )
                    parent["source_quantity"] = occurrence.get("source_quantity")
                    parent["effective_quantity"] = occurrence.get("effective_quantity")
                    effective_where_used["children"].append(parent)
                    effective_count += 1
            except Exception as exc:
                effective_error = str(exc)
        return {
            "uses": uses,
            "where_used": where_used,
            "effective_where_used": effective_where_used,
            "uses_count": descendant_count(uses),
            "where_used_count": descendant_count(where_used),
            "effective_where_used_count": effective_count,
            "effective_where_used_error": effective_error,
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
                fieldnames = [
                    'part_number', 'name', 'aes_number', 'item_type', 'assembly_mode',
                    'procurement_source', 'item_view', 'default_unit', 'type',
                    'drawing_number', 'material', 'weight', 'status', 'notes',
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for part in self.bom_repo.get_all(int(self.session.project_id)):
                    writer.writerow({
                        'part_number': part.part_number or '',
                        'name': part.name or '',
                        'aes_number': part.aes_number or '',
                        'item_type': part.item_type or '',
                        'assembly_mode': part.assembly_mode or '',
                        'procurement_source': part.procurement_source or '',
                        'item_view': part.item_view or '',
                        'default_unit': part.default_unit or '',
                        'type': part.type or '',
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
