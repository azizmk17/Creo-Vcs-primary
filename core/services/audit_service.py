from __future__ import annotations

from typing import Any, Dict, Optional

from core.repositories.audit_repository import AuditRepository
from core.session_manager import SessionManager


class AuditService:
    """Append-only, per-part audit log with hash chain."""

    def __init__(self, repo: Optional[AuditRepository] = None):
        self.repo = repo or AuditRepository()
        self.session = SessionManager()

    def supported(self) -> bool:
        return self.repo.audit_supported()

    def log(
        self,
        *,
        part_id: int,
        event_type: str,
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
        entity_type: str = "PART",
        entity_id: Optional[str] = None,
        project_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> int:
        if project_id is None:
            project_id = getattr(self.session, "project_id", None)
        if user_id is None:
            user_id = getattr(self.session, "user_id", None)
        return self.repo.append_event(
            part_id=int(part_id),
            project_id=int(project_id) if project_id is not None else None,
            user_id=int(user_id) if user_id is not None else None,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            payload=payload or {},
        )
