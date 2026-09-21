"""Authenticated localhost bridge used by the Creo J-Link client.

The Java client never opens the Nexus SQLite database. Every mutating request
is routed through the same domain services used by the desktop application so
checkout ownership, lifecycle, Item coordination, and audit rules remain
centralized in Nexus.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import socket
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.repositories.bom_children_repository import BomChildrenRepository
from core.repositories.bom_repository import BomRepository
from core.repositories.lock_repository import LockRepository
from core.repositories.permission_repository import PermissionRepository
from core.repositories.signature_repository import SignatureRepository
from core.services.bom_service import BomService
from core.services.cad_workspace_service import CadWorkspaceService
from core.services.pdm_service import PdmService
from core.services.project_service import ProjectService
from core.session_manager import SessionManager


API_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
_CAD_VERSION_RE = re.compile(r"\.(?:prt|asm|drw)\.(\d+)$", re.IGNORECASE)
logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BridgeApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.details = details


class CreoBridgeController:
    """Translate bridge requests into existing Nexus domain-service calls."""

    def __init__(
        self,
        *,
        session=None,
        project_service=None,
        permission_repo=None,
        bom_service_factory=None,
        workspace_service_factory=None,
        pdm_service_factory=None,
    ) -> None:
        self.session = session or SessionManager()
        self.project_service = project_service or ProjectService()
        self.permission_repo = permission_repo or PermissionRepository()
        self._bom_service_factory = bom_service_factory or self._build_bom_service
        self._workspace_service_factory = (
            workspace_service_factory or CadWorkspaceService
        )
        self._pdm_service_factory = pdm_service_factory or PdmService
        self._operation_lock = threading.RLock()

    @staticmethod
    def _build_bom_service() -> BomService:
        return BomService(
            BomRepository(),
            BomChildrenRepository(),
            LockRepository(),
            SignatureRepository(),
        )

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        method = str(method or "").upper()
        path = "/" + str(path or "").strip("/")

        if method == "GET" and path == "/api/v1/context":
            return self.context()
        if method == "GET" and path == "/api/v1/projects":
            return self.projects()
        if method == "GET" and path == "/api/v1/workspaces":
            self._require_session()
            return {"workspaces": self._workspace_service().list_workspaces()}
        if method == "POST" and path == "/api/v1/workspaces":
            self._require_session()
            workspace = self._workspace_service().create_workspace(
                str(body.get("name") or ""), str(body.get("description") or "")
            )
            return {"workspace": workspace}
        workspace_match = re.fullmatch(r"/api/v1/workspaces/([^/]+)/checkouts", path)
        if method == "GET" and workspace_match:
            return self.workspace_checkouts(workspace_match.group(1))
        if method == "GET" and path == "/api/v1/cad":
            return self.project_cad_documents()
        if method == "GET" and path == "/api/v1/cad/resolve":
            values = query.get("file_name") or []
            file_name = values[0] if values else ""
            return self.resolve_cad(file_name)

        match = re.fullmatch(
            r"/api/v1/cad/(\d+)(?:/(retrieve|checkout|checkin|undo|history|revise|release))?",
            path,
        )
        if match:
            cad_document_id = int(match.group(1))
            action = match.group(2)
            if method == "GET" and action is None:
                return {"cad": self.cad_status(cad_document_id)}
            if method == "POST" and action == "retrieve":
                return self.retrieve(cad_document_id, body)
            if method == "POST" and action == "checkout":
                return self.checkout(cad_document_id, body)
            if method == "POST" and action == "checkin":
                return self.checkin(cad_document_id, body)
            if method == "POST" and action == "undo":
                return self.undo_checkout(cad_document_id, body)
            if method == "GET" and action == "history":
                return self.cad_history(cad_document_id)
            if method == "POST" and action == "revise":
                return self.revise(cad_document_id)
            if method == "POST" and action == "release":
                return self.release(cad_document_id)

        raise BridgeApiError(404, "route_not_found", "The bridge route was not found.")

    def _workspace_service(self) -> CadWorkspaceService:
        return self._workspace_service_factory()

    def _pdm_service(self) -> PdmService:
        return self._pdm_service_factory()

    def _bom_service(self) -> BomService:
        return self._bom_service_factory()

    def _require_session(self) -> int:
        user_id = getattr(self.session, "user_id", None)
        if user_id is None:
            raise BridgeApiError(
                401, "login_required", "Sign in to Nexus before connecting Creo."
            )
        return int(user_id)

    def _require_project(self) -> tuple[int, int, dict]:
        user_id = self._require_session()
        project_id = getattr(self.session, "project_id", None)
        if project_id is None:
            raise BridgeApiError(
                409,
                "project_required",
                "Select a product and version in Nexus before using Creo.",
            )
        project = self.project_service.get_project_by_id(int(project_id)) or {}
        if not project:
            raise BridgeApiError(404, "project_not_found", "The active Nexus project was not found.")
        return user_id, int(project_id), dict(project)

    def _require_commit_permission(self) -> tuple[int, int, dict]:
        user_id, project_id, project = self._require_project()
        if not self.permission_repo.user_has_permission(
            user_id, "commit", project_id
        ):
            raise BridgeApiError(
                403,
                "permission_denied",
                "The current Nexus user cannot modify CAD in this project.",
            )
        return user_id, project_id, project

    def context(self) -> dict:
        logged_in = getattr(self.session, "user_id", None) is not None
        result = {
            "api_version": API_VERSION,
            "logged_in": bool(logged_in),
            "user": None,
            "project": None,
        }
        if not logged_in:
            return result
        result["user"] = {
            "id": int(self.session.user_id),
            "username": str(getattr(self.session, "username", "") or ""),
            "is_admin": bool(getattr(self.session, "is_admin", False)),
        }
        project_id = getattr(self.session, "project_id", None)
        if project_id is not None:
            project = self.project_service.get_project_by_id(int(project_id)) or {}
            if project:
                result["project"] = self._project_payload(project)
        return result

    def projects(self) -> dict:
        user_id = self._require_session()
        projects = self.project_service.get_projects_for_user(user_id) or []
        return {"projects": [self._project_payload(row) for row in projects]}

    @staticmethod
    def _project_payload(project: dict) -> dict:
        return {
            "id": int(project["id"]),
            "name": str(project.get("name") or ""),
            "product_number": str(project.get("product_number") or ""),
            "version_label": str(project.get("version_label") or ""),
            "working_directory": str(project.get("working_directory") or ""),
            "root_project_id": project.get("root_project_id"),
        }

    def _document(self, cad_document_id: int) -> tuple[int, int, dict, dict]:
        user_id, project_id, project = self._require_project()
        document = self._pdm_service().repo.get_cad_document(int(cad_document_id)) or {}
        if not document:
            raise BridgeApiError(404, "cad_not_found", "The CAD Document was not found.")
        if int(document.get("project_id") or 0) != project_id:
            raise BridgeApiError(
                409,
                "wrong_project",
                "The CAD Document does not belong to the active Nexus project.",
            )
        return user_id, project_id, project, dict(document)

    def _workspace(self, workspace_id: str) -> tuple[CadWorkspaceService, dict]:
        value = str(workspace_id or "").strip().lower()
        if not value:
            raise BridgeApiError(400, "workspace_required", "Select a Nexus CAD workspace.")
        service = self._workspace_service()
        workspace = service.get_workspace(value)
        if not workspace or not workspace.get("available"):
            raise BridgeApiError(
                404,
                "workspace_not_found",
                "The selected CAD workspace is not available for the current user on this machine.",
            )
        workspace_machine = str(workspace.get("machine_id") or "").strip()
        if workspace_machine and workspace_machine.casefold() != str(
            service.machine_id
        ).strip().casefold():
            raise BridgeApiError(
                409,
                "workspace_machine_mismatch",
                "The selected CAD workspace belongs to another machine.",
            )
        return service, workspace

    def _status_payload(self, document: dict) -> dict:
        user_id = self._require_session()
        owner = document.get("checked_out_by")
        owner_id = int(owner) if owner is not None else None
        workspace_id = str(document.get("checkout_workspace_id") or "").strip().lower()
        workspace_machine = str(
            document.get("checkout_workspace_machine_id") or ""
        ).strip()
        local_machine = socket.gethostname().strip() or "unknown-machine"
        workspace = None
        if workspace_id:
            try:
                workspace = self._workspace_service().get_workspace(workspace_id)
            except Exception:
                workspace = None
        owned_here = bool(
            owner_id == user_id
            and workspace_id
            and workspace
            and workspace.get("available")
            and (not workspace_machine or workspace_machine.casefold() == local_machine.casefold())
        )
        if owner_id is None:
            checkout_state = "CHECKED_IN"
        elif owner_id == user_id:
            checkout_state = "CHECKED_OUT_BY_ME"
        else:
            checkout_state = "CHECKED_OUT_BY_OTHER"
        lifecycle = str(document.get("lifecycle_state") or "WIP").upper()
        can_commit = self.permission_repo.user_has_permission(
            user_id, "commit", int(document.get("project_id") or 0)
        )
        reason = ""
        if checkout_state == "CHECKED_OUT_BY_OTHER":
            reason = "Checked out by another Nexus user."
        elif checkout_state == "CHECKED_IN":
            reason = "Check out this CAD Document before saving changes."
        elif not workspace_id:
            reason = "The checkout is not assigned to a managed CAD workspace."
        elif not owned_here:
            reason = "The checkout belongs to another workspace or machine."
        elif not can_commit:
            reason = "The current Nexus user no longer has permission to modify CAD."
        return {
            "managed": True,
            "id": int(document["id"]),
            "project_id": int(document.get("project_id") or 0),
            "number": str(document.get("number") or ""),
            "name": str(document.get("name") or ""),
            "file_name": str(document.get("file_name") or ""),
            "latest_creo_file_name": str(document.get("latest_creo_file_name") or ""),
            "category": str(document.get("category") or ""),
            "revision": str(document.get("revision") or ""),
            "iteration": int(document.get("iteration") or 0),
            "lifecycle_state": lifecycle,
            "checkout_state": checkout_state,
            "checked_out_by": owner_id,
            "checked_out_by_username": str(
                document.get("checked_out_by_username") or ""
            ),
            "checkout_workspace_id": workspace_id or None,
            "checkout_workspace_name": str(
                document.get("checkout_workspace_name") or ""
            ),
            "checkout_workspace_machine_id": workspace_machine or None,
            "workspace_path": str((workspace or {}).get("path") or "") or None,
            "can_checkout": bool(
                can_commit and owner_id is None and lifecycle not in {"RELEASED", "OBSOLETE"}
            ),
            "can_modify": bool(can_commit and owned_here),
            "can_checkin": bool(can_commit and owned_here),
            "read_only_reason": reason,
        }

    @staticmethod
    def _checked_out_in_workspace(
        document: dict,
        user_id: int,
        workspace: dict,
        workspace_service: CadWorkspaceService,
    ) -> bool:
        owner = document.get("checked_out_by")
        checkout_workspace = str(
            document.get("checkout_workspace_id") or ""
        ).strip()
        checkout_machine = str(
            document.get("checkout_workspace_machine_id") or ""
        ).strip()
        return bool(
            owner is not None
            and int(owner) == int(user_id)
            and checkout_workspace
            and checkout_workspace.casefold()
            == str(workspace.get("id") or "").strip().casefold()
            and (
                not checkout_machine
                or checkout_machine.casefold()
                == str(workspace_service.machine_id).strip().casefold()
            )
        )

    def _cad_dependency_ids(self, root_cad_document_id: int) -> list[int]:
        """Return each recursive managed assembly member once, parent first."""
        repo = self._pdm_service().repo
        root_id = int(root_cad_document_id)
        seen = {root_id}
        pending = [root_id]
        dependencies = []
        while pending:
            parent_id = pending.pop(0)
            for member in repo.list_cad_members(parent_id) or []:
                child_id = int(member["child_cad_document_id"])
                if child_id in seen:
                    continue
                seen.add(child_id)
                dependencies.append(child_id)
                pending.append(child_id)
        return dependencies

    def _materialize_cad_package(
        self,
        workspace_service: CadWorkspaceService,
        workspace: dict,
        root_cad_document_id: int,
        user_id: int,
        *,
        include_related_drawings: bool,
        include_dependencies: bool,
    ) -> tuple[list[dict], list[int]]:
        """Copy a root model and its controlled dependency closure safely."""
        pdm = self._pdm_service()
        document_ids = [int(root_cad_document_id)]
        if include_dependencies:
            document_ids.extend(self._cad_dependency_ids(root_cad_document_id))

        materialized = []
        materialized_ids = set()
        dependency_ids = document_ids[1:]
        expected_project_id = None
        for document_id in document_ids:
            if document_id in materialized_ids:
                continue
            document = pdm.repo.get_cad_document(document_id) or {}
            if not document:
                raise BridgeApiError(
                    404,
                    "cad_dependency_not_found",
                    f"CAD dependency {document_id} was not found.",
                )
            document_project_id = int(document.get("project_id") or 0)
            if expected_project_id is None:
                expected_project_id = document_project_id
            elif document_project_id != expected_project_id:
                raise BridgeApiError(
                    409,
                    "cross_project_dependency",
                    "A CAD assembly dependency belongs to another Nexus project.",
                    {"cad_document_id": document_id},
                )
            editable = self._checked_out_in_workspace(
                document, user_id, workspace, workspace_service
            )
            copied = workspace_service.materialize_cad_document_package(
                workspace["id"],
                document_id,
                preserve_existing=False,
                include_related_drawings=include_related_drawings,
                editable=editable,
            )
            for row in copied:
                copied_id = int(row["cad_document_id"])
                if copied_id not in materialized_ids:
                    materialized.append(row)
                    materialized_ids.add(copied_id)
        return materialized, dependency_ids

    def resolve_cad(self, file_name: str) -> dict:
        _user_id, project_id, _project = self._require_project()
        clean_name = CadWorkspaceService.logical_name(str(file_name or ""))
        if not clean_name:
            raise BridgeApiError(400, "file_name_required", "A Creo file name is required.")
        document = self._pdm_service().repo.get_cad_document_by_file(
            project_id, clean_name
        )
        if not document:
            return {"cad": {"managed": False, "file_name": clean_name}}
        return {"cad": self._status_payload(dict(document))}

    def cad_status(self, cad_document_id: int) -> dict:
        _user_id, _project_id, _project, document = self._document(cad_document_id)
        return self._status_payload(document)

    def cad_history(self, cad_document_id: int) -> dict:
        """Return the append-only checkout history for a project CAD Document."""
        _user_id, _project_id, _project, document = self._document(cad_document_id)
        history = self._pdm_service().cad_checkout_history(int(cad_document_id))
        return {
            "cad": self._status_payload(document),
            "history": [dict(row) for row in history or []],
        }

    def revise(self, cad_document_id: int) -> dict:
        """Create the next CAD revision only when no working copy is active."""
        with self._operation_lock:
            self._require_commit_permission()
            _user_id, _project_id, _project, document = self._document(cad_document_id)
            if document.get("checked_out_by") is not None:
                raise BridgeApiError(
                    409,
                    "checkout_active",
                    "Check in or undo the active CAD checkout before creating a revision.",
                )
            result = self._bom_service().revise_pdm_cad_document(int(cad_document_id))
            return {"revision": result, "cad": self.cad_status(cad_document_id)}

    def release(self, cad_document_id: int) -> dict:
        """Promote a checked-in CAD Document through the Nexus lifecycle."""
        with self._operation_lock:
            self._require_commit_permission()
            _user_id, _project_id, _project, document = self._document(cad_document_id)
            if document.get("checked_out_by") is not None:
                raise BridgeApiError(
                    409,
                    "checkout_active",
                    "Check in or undo the active CAD checkout before releasing it.",
                )
            result = self._bom_service().release_pdm_cad_document(int(cad_document_id))
            return {"release": result, "cad": self.cad_status(cad_document_id)}

    def project_cad_documents(self) -> dict:
        _user_id, project_id, _project = self._require_project()
        rows = self._pdm_service().list_cad_documents(
            int(project_id),
            include_related_drawings=True,
            include_legacy_fallback=True,
        )
        documents = [self._status_payload(dict(row)) for row in rows or []]
        documents.sort(
            key=lambda row: (
                str(row.get("category") or ""),
                str(row.get("file_name") or "").casefold(),
                int(row.get("id") or 0),
            )
        )
        return {"cad_documents": documents}

    def workspace_checkouts(self, workspace_id: str) -> dict:
        user_id, project_id, _project = self._require_project()
        workspace_service, workspace = self._workspace(workspace_id)
        rows = self._pdm_service().repo.list_checked_out_cad_by_workspace(
            str(workspace["id"])
        )
        documents = [
            self._status_payload(dict(row))
            for row in rows or []
            if int(row.get("project_id") or 0) == int(project_id)
        ]
        return {
            "workspace": workspace,
            "cad_documents": documents,
            "local_files": workspace_service.scan_workspace(
                str(workspace["id"]), int(project_id), int(user_id)
            ),
        }

    def retrieve(self, cad_document_id: int, body: dict) -> dict:
        with self._operation_lock:
            user_id, _project_id, _project, document = self._document(cad_document_id)
            workspace_service, workspace = self._workspace(body.get("workspace_id"))
            owner = document.get("checked_out_by")
            checked_out_here = self._checked_out_in_workspace(
                document, user_id, workspace, workspace_service
            )
            if owner is not None and int(owner) == user_id and not checked_out_here:
                raise BridgeApiError(
                    409,
                    "workspace_conflict",
                    "This CAD Document is checked out by you in another workspace.",
                )
            files, dependency_ids = self._materialize_cad_package(
                workspace_service,
                workspace,
                int(cad_document_id),
                user_id,
                include_related_drawings=bool(body.get("include_drawings", True)),
                include_dependencies=bool(body.get("include_dependencies", True)),
            )
            return {
                "workspace": workspace,
                "files": files,
                "root_path": files[0]["path"] if files else None,
                "dependency_document_ids": dependency_ids,
                "cad": self.cad_status(cad_document_id),
            }

    def checkout(self, cad_document_id: int, body: dict) -> dict:
        with self._operation_lock:
            _user_id, _project_id, _project = self._require_commit_permission()
            _uid, _pid, _project_row, document = self._document(cad_document_id)
            workspace_service, workspace = self._workspace(body.get("workspace_id"))
            bom_service = self._bom_service()
            revision_candidates = [document]
            if str(document.get("category") or "").upper() != "DRAWING":
                revision_candidates.extend(
                    self._pdm_service().repo.list_related_drawings(
                        int(cad_document_id)
                    )
                    or []
                )
            released_documents = [
                row
                for row in revision_candidates
                if str(row.get("lifecycle_state") or "").upper() == "RELEASED"
            ]
            if released_documents and not bool(body.get("revise_released")):
                labels = ", ".join(
                    str(row.get("file_name") or row.get("number") or row["id"])
                    for row in released_documents
                )
                raise BridgeApiError(
                    409,
                    "cad_revision_required",
                    "Create the next CAD revision before checkout for: " + labels,
                    {"cad_document_ids": [int(row["id"]) for row in released_documents]},
                )
            revised_documents = [
                bom_service.revise_pdm_cad_document(int(row["id"]))
                for row in released_documents
            ]

            descriptor = workspace_service.checkout_descriptor(workspace["id"])
            revision_codes = body.get("released_item_revision_codes") or {}
            revision_codes = {
                int(key): str(value or "") for key, value in revision_codes.items()
            }
            checked_out = False
            try:
                result = bom_service.checkout_pdm_cad_document(
                    int(cad_document_id),
                    released_item_revision_code=(
                        str(body.get("released_item_revision_code") or "").strip() or None
                    ),
                    released_item_revision_codes=revision_codes,
                    **descriptor,
                )
                checked_out = True
                files, dependency_ids = self._materialize_cad_package(
                    workspace_service,
                    workspace,
                    int(cad_document_id),
                    int(_user_id),
                    include_related_drawings=bool(body.get("include_drawings", True)),
                    include_dependencies=bool(body.get("include_dependencies", True)),
                )
            except Exception:
                if checked_out:
                    try:
                        bom_service.undo_checkout_pdm_cad_document(
                            int(cad_document_id), "Creo workspace materialization failed"
                        )
                    except Exception:
                        pass
                raise
            return {
                "checkout": result,
                "revised": revised_documents[0] if revised_documents else None,
                "revised_documents": revised_documents,
                "workspace": workspace,
                "files": files,
                "root_path": files[0]["path"] if files else None,
                "dependency_document_ids": dependency_ids,
                "cad": self.cad_status(cad_document_id),
            }

    def _workspace_source_path(
        self,
        workspace_service: CadWorkspaceService,
        workspace: dict,
        document: dict,
        requested_path: str,
    ) -> Path:
        workspace_root = Path(str(workspace["path"])).resolve()
        if requested_path:
            candidate = Path(str(requested_path)).expanduser().resolve()
            try:
                candidate.relative_to(workspace_root)
            except ValueError:
                raise BridgeApiError(
                    400,
                    "invalid_source_path",
                    "The check-in file must be inside the checkout workspace.",
                )
            candidates = [candidate]
        else:
            logical = workspace_service.logical_name(document.get("file_name") or "").casefold()
            candidates = [
                path for path in workspace_root.iterdir()
                if path.is_file()
                and workspace_service.logical_name(path.name).casefold() == logical
                and (
                    _CAD_VERSION_RE.search(path.name)
                    or re.search(r"\.(?:prt|asm|drw)$", path.name, re.IGNORECASE)
                )
            ]

            def creo_version(path: Path) -> int:
                match = _CAD_VERSION_RE.search(path.name)
                return int(match.group(1)) if match else 0

            candidates.sort(
                key=lambda path: (
                    creo_version(path),
                    path.name.casefold(),
                ),
                reverse=True,
            )
        if not candidates or not candidates[0].is_file():
            raise BridgeApiError(404, "source_not_found", "No saved Creo file was found for check-in.")
        candidate = candidates[0]
        expected = workspace_service.logical_name(document.get("file_name") or "").casefold()
        if workspace_service.logical_name(candidate.name).casefold() != expected:
            raise BridgeApiError(
                400,
                "wrong_source_file",
                "The selected file does not match the checked-out CAD Document.",
            )
        return candidate

    def checkin(self, cad_document_id: int, body: dict) -> dict:
        with self._operation_lock:
            user_id, _project_id, _project = self._require_commit_permission()
            _uid, _pid, _project_row, document = self._document(cad_document_id)
            if document.get("checked_out_by") is None or int(document["checked_out_by"]) != user_id:
                raise BridgeApiError(
                    409,
                    "not_checkout_owner",
                    "Only the user who checked out this CAD Document can check it in.",
                )
            workspace_id = str(document.get("checkout_workspace_id") or "")
            workspace_service, workspace = self._workspace(workspace_id)
            requested_workspace = str(body.get("workspace_id") or workspace_id)
            if requested_workspace.lower() != workspace_id.lower():
                raise BridgeApiError(
                    409,
                    "workspace_conflict",
                    "The check-in request does not match the checkout workspace.",
                )
            note = str(body.get("note") or "").strip()
            if not note:
                raise BridgeApiError(400, "note_required", "A check-in comment is required.")
            source = self._workspace_source_path(
                workspace_service,
                workspace,
                document,
                str(body.get("path") or "").strip(),
            )
            match = _CAD_VERSION_RE.search(source.name)
            creo_version = int(match.group(1)) if match else None
            result = self._bom_service().checkin_pdm_cad_document(
                int(cad_document_id),
                str(source),
                note,
                source_file_name=source.name,
                creo_file_version=creo_version,
            )
            return {
                "checkin": result,
                "source_path": str(source),
                "cad": self.cad_status(cad_document_id),
            }

    def undo_checkout(self, cad_document_id: int, body: dict) -> dict:
        with self._operation_lock:
            user_id, _project_id, _project = self._require_commit_permission()
            _uid, _pid, _project_row, document = self._document(cad_document_id)
            owner = document.get("checked_out_by")
            if owner is None or int(owner) != user_id:
                raise BridgeApiError(
                    409,
                    "not_checkout_owner",
                    "Only the user who checked out this CAD Document can undo its checkout.",
                )
            note = str(body.get("note") or "Creo undo checkout").strip()
            result = self._bom_service().undo_checkout_pdm_cad_document(
                int(cad_document_id), note
            )
            return {
                "undo": result,
                "cad": self.cad_status(cad_document_id),
                "local_files_retained": True,
            }


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "NexusCreoBridge/1"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_OPTIONS(self):
        self._send_json(204, None)

    def log_message(self, _format, *_args):
        return

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        if method == "GET" and parsed.path == "/api/v1/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "data": {
                        "service": "nexus-creo-bridge",
                        "api_version": API_VERSION,
                    },
                },
            )
            return
        if not self._authorized():
            self._send_error_payload(401, "invalid_token", "The Nexus bridge token is invalid.")
            return
        try:
            body = self._read_body() if method == "POST" else {}
            controller = getattr(self.server, "bridge_controller")
            data = controller.dispatch(
                method, parsed.path, parse_qs(parsed.query, keep_blank_values=True), body
            )
            self._send_json(200, {"ok": True, "data": data})
        except BridgeApiError as exc:
            self._send_error_payload(exc.status, exc.code, exc.message, exc.details)
        except PermissionError as exc:
            self._send_error_payload(403, "permission_denied", str(exc))
        except ValueError as exc:
            message = str(exc)
            lowered = message.casefold()
            if "another user" in lowered or "checked out by" in lowered:
                status, code = 409, "checkout_conflict"
            elif "released" in lowered and "revision" in lowered:
                status, code = 409, "item_revision_required"
            else:
                status, code = 400, "validation_error"
            self._send_error_payload(status, code, message)
        except FileNotFoundError as exc:
            self._send_error_payload(404, "file_not_found", str(exc))
        except Exception:
            logger.exception("Unhandled Nexus Creo bridge request failure")
            self._send_error_payload(
                500,
                "internal_error",
                "Nexus could not complete the Creo request. Review the Nexus diagnostics.",
            )

    def _authorized(self) -> bool:
        expected = str(getattr(self.server, "bridge_token", ""))
        provided = str(self.headers.get("X-Nexus-Token") or "")
        return bool(expected and hmac.compare_digest(expected, provided))

    def _read_body(self) -> dict:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            raise BridgeApiError(400, "invalid_length", "Invalid request length.")
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise BridgeApiError(413, "request_too_large", "The request body is too large.")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BridgeApiError(400, "invalid_json", "The request body is not valid JSON.")
        if not isinstance(value, dict):
            raise BridgeApiError(400, "invalid_json", "The request body must be a JSON object.")
        return value

    def _send_error_payload(self, status: int, code: str, message: str, details=None):
        error = {"code": str(code), "message": str(message)}
        if details is not None:
            error["details"] = details
        self._send_json(int(status), {"ok": False, "error": error})

    def _send_json(self, status: int, payload) -> None:
        raw = b"" if payload is None else json.dumps(
            payload, ensure_ascii=True, default=str, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if raw:
            self.wfile.write(raw)


class NexusCreoBridge:
    """Own the localhost server and its per-process connection descriptor."""

    def __init__(
        self,
        *,
        controller=None,
        host: str = "127.0.0.1",
        port: int | None = None,
        connection_file: str | os.PathLike | None = None,
    ) -> None:
        self.controller = controller or CreoBridgeController()
        self.host = "127.0.0.1" if host not in {"127.0.0.1", "localhost"} else host
        configured_port = os.environ.get("NEXUS_CREO_BRIDGE_PORT", "").strip()
        self.port = int(port if port is not None else (configured_port or 0))
        self.token = secrets.token_urlsafe(32)
        self.connection_file = Path(
            connection_file or os.environ.get("NEXUS_CREO_BRIDGE_FILE") or self.default_connection_file()
        ).expanduser().resolve()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def default_connection_file() -> Path:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "CreoVCS" / "bridge.json"

    @property
    def api_url(self) -> str:
        if self._server is None:
            return ""
        return f"http://127.0.0.1:{int(self._server.server_address[1])}/api/v1"

    def start(self) -> dict:
        if self._server is not None:
            return self.descriptor()
        server = ThreadingHTTPServer((self.host, self.port), _BridgeRequestHandler)
        server.daemon_threads = True
        server.bridge_controller = self.controller
        server.bridge_token = self.token
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="nexus-creo-bridge",
            daemon=True,
        )
        thread_started = False
        try:
            self._thread.start()
            thread_started = True
            descriptor = self.descriptor()
            self._write_connection_file(descriptor)
            return descriptor
        except Exception:
            self._server = None
            thread = self._thread
            self._thread = None
            if thread_started:
                server.shutdown()
            server.server_close()
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
            raise

    def descriptor(self) -> dict:
        return {
            "api_version": API_VERSION,
            "api_url": self.api_url,
            "token": self.token,
            "pid": os.getpid(),
            "started_at": _utc_now(),
            "connection_file": str(self.connection_file),
        }

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._remove_own_connection_file()

    def _write_connection_file(self, descriptor: dict) -> None:
        self.connection_file.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".bridge.", suffix=".tmp", dir=str(self.connection_file.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(descriptor, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.connection_file)
        except Exception:
            try:
                os.remove(temporary)
            except OSError:
                pass
            raise

    def _remove_own_connection_file(self) -> None:
        try:
            with open(self.connection_file, "r", encoding="utf-8") as handle:
                current = json.load(handle)
            if hmac.compare_digest(str(current.get("token") or ""), self.token):
                self.connection_file.unlink()
        except (OSError, ValueError, TypeError):
            pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.stop()
