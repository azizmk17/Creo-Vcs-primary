import hashlib
import json
import os
import uuid

from core.repositories.assembly_configuration_repository import (
    AssemblyConfigurationRepository,
)
from core.repositories.bom_repository import BomRepository
from core.repositories.bom_revision_repository import BomRevisionRepository
from core.services.audit_service import AuditService
from core.services.base_service import BaseService
from core.services.project_service import ProjectService
from utils import (
    ensure_dir_exists,
    safe_copy2,
    safe_exists,
    safe_isfile,
    safe_open,
    safe_rmtree,
)


class ConfigurationCancelled(RuntimeError):
    pass


class AssemblyConfigurationService(BaseService):
    """Manage editable and frozen Creo assembly configuration versions."""

    PURPOSES = (
        "Prototype",
        "3D Printing",
        "Manufacturing Trial",
        "Customer Variant",
        "Validation",
        "Other",
    )

    def __init__(
        self,
        repo=None,
        revision_repo=None,
        bom_repo=None,
        project_service=None,
        audit_service=None,
    ):
        super().__init__()
        self.repo = repo or AssemblyConfigurationRepository()
        self.revision_repo = revision_repo or BomRevisionRepository()
        self.bom_repo = bom_repo or BomRepository()
        self.project_service = project_service or ProjectService()
        self.audit_service = audit_service or AuditService()

    @staticmethod
    def _clean_name(value: str) -> str:
        name = " ".join(str(value or "").strip().split())
        if not name:
            raise ValueError("Configuration name is required.")
        if len(name) > 100:
            raise ValueError("Configuration name cannot exceed 100 characters.")
        if any(char in name for char in '<>:"/\\|?*'):
            raise ValueError(
                "Configuration name contains characters that are not allowed in folders."
            )
        return name

    @staticmethod
    def _safe_folder_name(value: str) -> str:
        result = "".join(
            char if char.isalnum() or char in "._- " else "_"
            for char in str(value or "")
        ).strip(" .")
        return result[:100].rstrip(" .") or "configuration"

    @staticmethod
    def _is_within(path: str, parent: str) -> bool:
        try:
            return os.path.normcase(os.path.commonpath([path, parent])) == os.path.normcase(parent)
        except (ValueError, OSError):
            return False

    @staticmethod
    def _hash_file(path: str) -> str:
        digest = hashlib.sha256()
        with safe_open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _project_record(self, project_id: int) -> dict:
        project = self.project_service.get_project_by_id(int(project_id)) or {}
        if not project:
            raise ValueError("The active project was not found.")
        return project

    def _project_context(self, project_id: int) -> tuple[dict, str]:
        project = self._project_record(int(project_id))
        raw_working_directory = str(project.get("working_directory") or "").strip()
        if not raw_working_directory:
            raise ValueError("The project working directory is not configured.")
        working_directory = os.path.abspath(os.path.normpath(raw_working_directory))
        if not os.path.isdir(working_directory):
            raise ValueError("The project working directory is not available.")
        return project, working_directory

    def _source_path(self, working_directory: str, captured_path: str) -> str:
        raw_path = str(captured_path or "").strip()
        if not raw_path:
            return ""
        source = os.path.abspath(
            os.path.normpath(
                raw_path
                if os.path.isabs(raw_path)
                else os.path.join(working_directory, raw_path)
            )
        )
        if not self._is_within(source, working_directory):
            raise ValueError(
                f"Captured Creo file is outside the project working directory: {raw_path}"
            )
        return source

    @staticmethod
    def _notify_progress(progress_cb, current: int, total: int, label: str) -> None:
        if callable(progress_cb):
            progress_cb(int(current), int(total), str(label))

    @staticmethod
    def _check_cancel(cancel_cb) -> None:
        if callable(cancel_cb) and cancel_cb():
            raise ConfigurationCancelled("Configuration operation was cancelled.")

    def _archive_root(self, working_directory: str, storage_rel_path: str) -> str:
        allowed_root = os.path.abspath(
            os.path.join(working_directory, ".nexus", "configurations")
        )
        archive_root = os.path.abspath(
            os.path.normpath(
                os.path.join(working_directory, str(storage_rel_path or ""))
            )
        )
        if archive_root == allowed_root or not self._is_within(
            archive_root, allowed_root
        ):
            raise ValueError("The configuration archive path is outside controlled storage.")
        return archive_root

    def _audit(
        self, configuration: dict, event_type: str, message: str, payload=None
    ) -> None:
        try:
            if self.audit_service.supported():
                self.audit_service.log(
                    part_id=int(configuration["root_bom_id"]),
                    project_id=int(configuration["project_id"]),
                    event_type=event_type,
                    entity_type="ASSEMBLY_CONFIGURATION",
                    entity_id=str(configuration.get("id") or ""),
                    message=message,
                    payload=payload or {},
                )
        except Exception:
            pass

    @staticmethod
    def _empty_capture_fields(member: dict) -> None:
        for field in (
            "native_source_rel_path", "drawing_source_rel_path",
            "native_frozen_rel_path", "drawing_frozen_rel_path",
            "native_sha256", "drawing_sha256",
        ):
            member[field] = ""

    def _normalize_members(
        self, project_id: int, root_bom_id: int, members,
        *, require_assembly_root: bool = True
    ) -> list[dict]:
        raw_members = [dict(member) for member in (members or [])]
        if not raw_members:
            raise ValueError("A configuration must contain a root assembly.")
        if len(raw_members) > 20000:
            raise ValueError("The configuration exceeds the supported size of 20,000 occurrences.")

        paths = {}
        original_order = {}
        for index, member in enumerate(raw_members):
            path = str(member.get("occurrence_path") or "").strip()
            if not path:
                raise ValueError("Every configuration occurrence requires a path.")
            if path in paths:
                raise ValueError(f"Duplicate configuration occurrence path: {path}")
            member["occurrence_path"] = path
            member["parent_occurrence_path"] = (
                str(member.get("parent_occurrence_path") or "").strip() or None
            )
            try:
                member["bom_id"] = int(member.get("bom_id"))
                member["iteration_id"] = int(member.get("iteration_id"))
            except (TypeError, ValueError):
                raise ValueError(f"{path} does not identify an exact BOM iteration.")
            paths[path] = member
            original_order[path] = index

        root = paths.get("root")
        if not root or root.get("parent_occurrence_path") is not None:
            raise ValueError("The configuration must have exactly one root occurrence.")
        if int(root["bom_id"]) != int(root_bom_id):
            raise ValueError("The configuration root cannot be replaced with another BOM object.")

        contexts = self.revision_repo.get_iteration_object_contexts(
            member["iteration_id"] for member in raw_members
        )
        for member in raw_members:
            path = member["occurrence_path"]
            context = contexts.get(int(member["iteration_id"]))
            if not context:
                raise ValueError(f"The selected iteration for {path} no longer exists.")
            if int(context["bom_id"]) != int(member["bom_id"]):
                raise ValueError(f"The selected iteration does not belong to {path}.")
            if int(context.get("project_id") or 0) != int(project_id):
                raise ValueError("A configuration cannot contain items from another project.")
            member.update({
                "revision_id": int(context["revision_id"]),
                "version_label": str(context.get("version_label") or ""),
                "type": str(context.get("type") or ""),
                "name": str(context.get("name") or ""),
                "aes_number": str(context.get("aes_number") or ""),
                "part_number": str(context.get("part_number") or ""),
                "drawing_number": str(context.get("drawing_number") or ""),
                "filename": str(context.get("filename") or ""),
                "drawing": str(context.get("drawing") or ""),
                "quantity": max(1, int(member.get("quantity") or 1)),
                "position": max(0, int(member.get("position") or 0)),
                "sort_order": max(0, int(member.get("sort_order") or 0)),
                "usage_id": (
                    int(member["usage_id"])
                    if member.get("usage_id") is not None
                    else None
                ),
            })
            self._empty_capture_fields(member)

        if (
            require_assembly_root
            and str(root.get("type") or "").strip().lower() not in {"asm", "assembly"}
        ):
            raise ValueError("A configuration must start from an assembly iteration.")

        children = {}
        for member in raw_members:
            path = member["occurrence_path"]
            if path == "root":
                continue
            parent_path = member.get("parent_occurrence_path")
            parent = paths.get(parent_path)
            if not parent:
                raise ValueError(f"Configuration occurrence {path} has no parent.")
            if not path.startswith(f"{parent_path}/"):
                raise ValueError(f"Configuration occurrence {path} is outside its parent path.")
            if str(parent.get("type") or "").strip().lower() not in {
                "asm", "assembly", "folder"
            }:
                raise ValueError(f"{parent.get('name') or parent_path} cannot contain children.")
            children.setdefault(parent_path, []).append(member)

        for values in children.values():
            values.sort(
                key=lambda value: (
                    int(value.get("sort_order") or 0),
                    int(value.get("position") or 0),
                    original_order[value["occurrence_path"]],
                )
            )

        normalized = []

        def walk(member: dict, ancestor_bom_ids: tuple[int, ...]) -> None:
            bom_id = int(member["bom_id"])
            if bom_id in ancestor_bom_ids:
                raise ValueError(
                    f"Circular configuration structure detected at {member.get('name') or bom_id}."
                )
            member["sequence_no"] = len(normalized) + 1
            normalized.append(member)
            next_ancestors = (*ancestor_bom_ids, bom_id)
            for position, child in enumerate(
                children.get(member["occurrence_path"], []), start=1
            ):
                child["position"] = position
                child["sort_order"] = position
                walk(child, next_ancestors)

        walk(root, ())
        if len(normalized) != len(raw_members):
            raise ValueError("The configuration contains a disconnected structure branch.")
        return normalized

    def prepare_draft_structure(
        self, project_id: int, root_bom_id: int, root_iteration_id: int
    ) -> list[dict]:
        snapshot = self.revision_repo.get_iteration_structure_snapshot(
            int(root_bom_id), int(root_iteration_id)
        )
        if int(snapshot["project_id"]) != int(project_id):
            raise ValueError("The selected assembly iteration belongs to another project.")
        return self._normalize_members(
            int(project_id), int(root_bom_id), snapshot.get("members") or []
        )

    def get_component_structure(
        self, project_id: int, bom_id: int, iteration_id: int
    ) -> list[dict]:
        contexts = self.revision_repo.get_iteration_object_contexts([int(iteration_id)])
        context = contexts.get(int(iteration_id))
        if not context or int(context["bom_id"]) != int(bom_id):
            raise ValueError("The selected component iteration is no longer available.")
        if int(context.get("project_id") or 0) != int(project_id):
            raise ValueError("The selected component belongs to another project.")
        if str(context.get("type") or "").strip().lower() in {"asm", "assembly"}:
            return self.prepare_draft_structure(project_id, bom_id, iteration_id)
        member = {
            "occurrence_path": "root",
            "parent_occurrence_path": None,
            "usage_id": None,
            "bom_id": int(bom_id),
            "iteration_id": int(iteration_id),
            "quantity": 1,
            "position": 0,
            "sort_order": 0,
        }
        return self._normalize_members(
            project_id, bom_id, [member], require_assembly_root=False
        )

    def list_available_components(
        self, project_id: int, query: str = "", limit: int = 300
    ) -> list[dict]:
        parts = self.bom_repo.search_project(
            int(project_id), str(query or ""), limit=max(1, int(limit))
        )
        contexts = self.revision_repo.get_current_contexts(part.id for part in parts)
        result = []
        for part in parts:
            if str(part.type or "").strip().lower() == "folder":
                continue
            context = contexts.get(int(part.id)) or {}
            result.append({
                "bom_id": int(part.id),
                "name": str(part.name or ""),
                "aes_number": str(part.aes_number or ""),
                "part_number": str(part.part_number or ""),
                "type": str(part.type or ""),
                "iteration_id": context.get("current_iteration_id"),
                "version_label": str(context.get("version_label") or ""),
                "state": str(context.get("state") or ""),
            })
        return result

    def list_component_iterations(self, bom_id: int) -> list[dict]:
        return self.revision_repo.list_iterations(int(bom_id))

    def create_configuration(
        self,
        *,
        project_id: int,
        root_bom_id: int,
        root_iteration_id: int,
        name: str,
        purpose: str = "",
        description: str = "",
        members=None,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        name = self._clean_name(name)
        self._check_cancel(cancel_cb)
        project = self._project_record(int(project_id))
        normalized = (
            self._normalize_members(int(project_id), int(root_bom_id), members)
            if members is not None
            else self.prepare_draft_structure(
                int(project_id), int(root_bom_id), int(root_iteration_id)
            )
        )
        root = normalized[0]
        series_key = uuid.uuid4().hex
        configuration_id = self.repo.create_configuration(
            {
                "project_id": int(project_id),
                "name": name,
                "series_key": series_key,
                "configuration_name": name,
                "version_number": 1,
                "purpose": str(purpose or ""),
                "description": str(description or ""),
                "root_bom_id": int(root_bom_id),
                "root_iteration_id": int(root["iteration_id"]),
                "root_version_label": str(root.get("version_label") or ""),
                "root_name": str(root.get("name") or ""),
                "source_project_version": str(project.get("version_label") or ""),
                "created_by": self.user_id,
            },
            normalized,
        )
        configuration = self.repo.get_configuration(configuration_id, int(project_id))
        self._audit(
            configuration,
            "configuration_draft_created",
            f"Created Draft v1 of {name}.",
            {"member_count": len(normalized)},
        )
        return configuration

    def list_configurations(self, project_id=None) -> list[dict]:
        project_id = int(project_id or self.project_id or 0)
        return self.repo.list_for_project(project_id) if project_id else []

    def get_configuration(self, configuration_id: int, project_id=None) -> dict:
        project_id = int(project_id or self.project_id or 0)
        configuration = self.repo.get_configuration(int(configuration_id), project_id)
        if not configuration:
            raise ValueError("Configuration was not found in the active project.")
        return configuration

    def list_members(self, configuration_id: int, project_id=None) -> list[dict]:
        self.get_configuration(int(configuration_id), project_id)
        return self.repo.list_members(int(configuration_id))

    def save_draft(
        self, configuration_id: int, members, purpose=None, description=None
    ) -> dict:
        configuration = self.get_configuration(int(configuration_id))
        if str(configuration.get("state") or "").strip().lower() != "draft":
            raise ValueError("Frozen configuration versions cannot be edited.")
        normalized = self._normalize_members(
            int(configuration["project_id"]),
            int(configuration["root_bom_id"]),
            members,
        )
        root = normalized[0]
        self.repo.save_draft(
            int(configuration_id),
            int(configuration["project_id"]),
            {
                "purpose": configuration.get("purpose") if purpose is None else purpose,
                "description": (
                    configuration.get("description")
                    if description is None
                    else description
                ),
                "root_bom_id": int(root["bom_id"]),
                "root_iteration_id": int(root["iteration_id"]),
                "root_version_label": str(root.get("version_label") or ""),
                "root_name": str(root.get("name") or ""),
            },
            normalized,
        )
        result = self.get_configuration(int(configuration_id))
        self._audit(
            result,
            "configuration_draft_saved",
            f"Saved Draft v{result.get('version_number') or 1} of {result.get('display_name') or ''}.",
            {"member_count": len(normalized)},
        )
        return result

    def _capture_member_files(
        self, project_id: int, members: list[dict], progress_cb=None, cancel_cb=None
    ) -> tuple[list[dict], int]:
        _, working_directory = self._project_context(int(project_id))
        captured_members = [dict(member) for member in members]
        missing_native = []
        file_plan = {}
        for member in captured_members:
            self._empty_capture_fields(member)
            member_type = str(member.get("type") or "").strip().lower()
            native = str(member.get("filename") or "").strip()
            if member_type != "folder" and not native:
                missing_native.append(
                    str(
                        member.get("aes_number")
                        or member.get("name")
                        or member.get("occurrence_path")
                    )
                )
            for role, field in (("native", "filename"), ("drawing", "drawing")):
                captured = str(member.get(field) or "").strip()
                if not captured:
                    continue
                source = self._source_path(working_directory, captured)
                basename = os.path.basename(source)
                key = basename.casefold()
                entry = file_plan.setdefault(
                    key,
                    {
                        "basename": basename,
                        "source": source,
                        "alternate_sources": [],
                        "references": [],
                    },
                )
                if os.path.normcase(entry["source"]) != os.path.normcase(source):
                    entry["alternate_sources"].append(source)
                entry["references"].append((member, role, source))

        if missing_native:
            raise ValueError(
                "Native Creo files are not captured for:\n"
                + "\n".join(missing_native[:20])
                + ("\n..." if len(missing_native) > 20 else "")
            )
        missing_files = [
            source
            for entry in file_plan.values()
            for source in [entry["source"], *entry["alternate_sources"]]
            if not safe_isfile(source)
        ]
        if missing_files:
            raise ValueError(
                "Captured Creo files are missing:\n"
                + "\n".join(missing_files[:20])
                + ("\n..." if len(missing_files) > 20 else "")
            )

        total = max(1, len(file_plan))
        for index, entry in enumerate(file_plan.values(), start=1):
            self._check_cancel(cancel_cb)
            entry_hash = self._hash_file(entry["source"])
            for alternate_source in entry["alternate_sources"]:
                if self._hash_file(alternate_source) != entry_hash:
                    raise ValueError(
                        f"Two different files use the Creo workspace name {entry['basename']}."
                    )
            for member, role, source in entry["references"]:
                member[f"{role}_source_rel_path"] = os.path.relpath(
                    source, working_directory
                )
                member[f"{role}_sha256"] = entry_hash
            self._notify_progress(
                progress_cb, index, total, f"Capturing {entry['basename']}"
            )
        self._check_cancel(cancel_cb)
        return captured_members, len(file_plan)

    def freeze_configuration(
        self, configuration_id: int, *, progress_cb=None, cancel_cb=None
    ) -> dict:
        configuration = self.get_configuration(int(configuration_id))
        if str(configuration.get("state") or "").strip().lower() != "draft":
            raise ValueError("Only a Draft configuration version can be frozen.")
        members = self.repo.list_members(int(configuration_id))
        normalized = self._normalize_members(
            int(configuration["project_id"]),
            int(configuration["root_bom_id"]),
            members,
        )
        captured, file_count = self._capture_member_files(
            int(configuration["project_id"]),
            normalized,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        self.repo.freeze_draft(
            int(configuration_id),
            int(configuration["project_id"]),
            captured,
            file_count,
            frozen_by=self.user_id,
        )
        result = self.get_configuration(int(configuration_id))
        self._audit(
            result,
            "configuration_frozen",
            f"Froze v{result.get('version_number') or 1} of {result.get('display_name') or ''}.",
            {"member_count": len(captured), "file_count": file_count},
        )
        return result

    def create_new_version(self, configuration_id: int) -> dict:
        configuration = self.get_configuration(int(configuration_id))
        new_id = self.repo.create_next_version(
            int(configuration_id), int(configuration["project_id"]), self.user_id
        )
        result = self.get_configuration(new_id, int(configuration["project_id"]))
        self._audit(
            result,
            "configuration_version_created",
            f"Created Draft v{result.get('version_number')} of {result.get('display_name') or ''}.",
            {"based_on_configuration_id": int(configuration_id)},
        )
        return result

    def build_configuration(
        self,
        configuration_id: int,
        destination_parent: str,
        *,
        progress_cb=None,
        cancel_cb=None,
    ) -> dict:
        configuration = self.get_configuration(int(configuration_id))
        if str(configuration.get("state") or "").strip().lower() != "frozen":
            raise ValueError("Freeze this configuration version before building it.")
        _, working_directory = self._project_context(int(configuration["project_id"]))
        storage_rel_path = str(configuration.get("storage_rel_path") or "").strip()
        archive_root = (
            self._archive_root(working_directory, storage_rel_path)
            if storage_rel_path
            else ""
        )
        members = self.repo.list_members(int(configuration_id))
        if not members:
            raise ValueError("The configuration manifest is empty.")

        files = {}
        for member in members:
            for role, captured_field in (("native", "filename"), ("drawing", "drawing")):
                captured = str(member.get(captured_field) or "").strip()
                if not captured:
                    continue
                source = ""
                frozen_rel_path = str(
                    member.get(f"{role}_frozen_rel_path") or ""
                ).strip()
                source_rel_path = str(
                    member.get(f"{role}_source_rel_path") or ""
                ).strip()
                if frozen_rel_path and archive_root:
                    legacy_source = os.path.abspath(
                        os.path.normpath(
                            os.path.join(working_directory, frozen_rel_path)
                        )
                    )
                    if not self._is_within(legacy_source, archive_root):
                        raise ValueError(
                            "A legacy configuration file path is outside controlled storage."
                        )
                    if safe_isfile(legacy_source) or not source_rel_path:
                        source = legacy_source
                if not source and source_rel_path:
                    source = os.path.abspath(
                        os.path.normpath(
                            os.path.join(working_directory, source_rel_path)
                        )
                    )
                    if not self._is_within(source, working_directory):
                        raise ValueError(
                            "A configuration source path is outside the project working directory."
                        )
                if not source:
                    raise ValueError(
                        f"Configuration source path is missing for {os.path.basename(captured)}."
                    )
                basename = os.path.basename(captured)
                key = basename.casefold()
                expected_hash = str(member.get(f"{role}_sha256") or "")
                previous = files.get(key)
                if previous and (
                    previous["sha256"] != expected_hash
                    or (
                        not expected_hash
                        and os.path.normcase(previous["source"])
                        != os.path.normcase(source)
                    )
                ):
                    raise ValueError(f"Configuration contains conflicting file {basename}.")
                if previous is None:
                    files[key] = {
                        "basename": basename,
                        "source": source,
                        "sha256": expected_hash,
                    }

        total_file_steps = max(1, len(files) * 2)
        for index, entry in enumerate(files.values(), start=1):
            self._check_cancel(cancel_cb)
            if not safe_isfile(entry["source"]):
                raise ValueError(
                    f"The exact source file is no longer available: {entry['basename']}"
                )
            if entry["sha256"] and self._hash_file(entry["source"]) != entry["sha256"]:
                raise ValueError(
                    "The source file changed after this configuration was frozen: "
                    f"{entry['basename']}"
                )
            self._notify_progress(
                progress_cb, index, total_file_steps, f"Verifying {entry['basename']}"
            )

        raw_destination_parent = str(destination_parent or "").strip()
        if not raw_destination_parent:
            raise ValueError("Select a destination parent folder.")
        destination_parent = os.path.abspath(os.path.normpath(raw_destination_parent))
        ensure_dir_exists(destination_parent)
        if not os.path.isdir(destination_parent):
            raise ValueError("The selected destination is not a directory.")
        controlled_root = os.path.abspath(
            os.path.join(working_directory, ".nexus", "configurations")
        )
        if self._is_within(destination_parent, controlled_root):
            raise ValueError("Choose a destination outside Nexus internal storage.")

        folder_base = self._safe_folder_name(
            f"{configuration.get('display_name') or 'configuration'}_v"
            f"{configuration.get('version_number') or 1}_"
            f"{configuration.get('root_version_label') or ''}"
        )
        target_directory = os.path.join(destination_parent, folder_base)
        suffix = 2
        while safe_exists(target_directory):
            target_directory = os.path.join(
                destination_parent, f"{folder_base}_{suffix}"
            )
            suffix += 1
        if not self._is_within(os.path.abspath(target_directory), destination_parent):
            raise ValueError("The generated build directory is outside the selected destination.")
        ensure_dir_exists(target_directory)
        try:
            for index, entry in enumerate(files.values(), start=1):
                self._check_cancel(cancel_cb)
                safe_copy2(
                    entry["source"], os.path.join(target_directory, entry["basename"])
                )
                self._notify_progress(
                    progress_cb,
                    len(files) + index,
                    total_file_steps,
                    f"Copying {entry['basename']}",
                )
            build_manifest = {
                "format": "Nexus Assembly Configuration Build",
                "format_version": 2,
                "configuration": {
                    key: configuration.get(key)
                    for key in (
                        "id", "display_name", "series_key", "version_number", "state",
                        "purpose", "description", "root_bom_id", "root_iteration_id",
                        "root_version_label", "root_name", "source_project_version",
                        "created_at", "created_by_name", "frozen_at", "frozen_by_name",
                    )
                },
                "members": members,
            }
            with safe_open(
                os.path.join(target_directory, "nexus_configuration_manifest.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(build_manifest, handle, indent=2, ensure_ascii=True)
        except Exception:
            if safe_exists(target_directory) and self._is_within(
                os.path.abspath(target_directory), destination_parent
            ):
                safe_rmtree(target_directory)
            raise

        root_member = next(
            (member for member in members if member.get("occurrence_path") == "root"),
            {},
        )
        root_filename = os.path.basename(str(root_member.get("filename") or ""))
        root_file_path = (
            os.path.join(target_directory, root_filename) if root_filename else ""
        )
        self.repo.mark_built(int(configuration_id), target_directory)
        self._audit(
            configuration,
            "configuration_built",
            f"Built v{configuration.get('version_number') or 1} of "
            f"{configuration.get('display_name') or configuration_id}.",
            {"build_path": target_directory, "file_count": len(files)},
        )
        return {
            "configuration_id": int(configuration_id),
            "target_directory": target_directory,
            "root_file_path": root_file_path,
            "file_count": len(files),
            "member_count": len(members),
        }

    def rebuild_configuration(
        self, configuration_id: int, destination_parent: str, **kwargs
    ) -> dict:
        return self.build_configuration(configuration_id, destination_parent, **kwargs)

    def delete_configuration(self, configuration_id: int) -> bool:
        configuration = self.get_configuration(int(configuration_id))
        storage_rel_path = str(configuration.get("storage_rel_path") or "").strip()
        archive_root = ""
        if storage_rel_path:
            _, working_directory = self._project_context(int(configuration["project_id"]))
            archive_root = self._archive_root(working_directory, storage_rel_path)
        deleted = self.repo.delete_configuration(
            int(configuration_id), int(configuration["project_id"])
        )
        if deleted and archive_root and safe_exists(archive_root):
            safe_rmtree(archive_root)
        if deleted:
            self._audit(
                configuration,
                "configuration_draft_deleted",
                f"Deleted Draft v{configuration.get('version_number') or 1} of "
                f"{configuration.get('display_name') or configuration_id}.",
            )
        return deleted
