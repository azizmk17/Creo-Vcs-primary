# base_service.py
from core.session_manager import SessionManager
from core.repositories.project_event_repository import ProjectEventRepository, unique_ints

class BaseService:
    def __init__(self):
        self.session = SessionManager()
        if not self.session.is_active():
            raise PermissionError("You must be logged in")
        self._project_event_repo = None

    @property
    def user_id(self):
        return self.session.user_id

    @property
    def project_id(self):
        return self.session.project_id

    @property
    def role_id(self):
        return self.session.role_id

    def emit_project_event(
        self,
        event_type: str,
        *,
        entity_type: str = "",
        entity_id=None,
        payload: dict | None = None,
        project_id=None,
        actor_user_id=None,
    ) -> int | None:
        """Best-effort multi-user invalidation event.

        Domain operations must never fail because live UI synchronization could
        not write a notification row, so every failure is swallowed here.
        """
        try:
            if self._project_event_repo is None:
                self._project_event_repo = ProjectEventRepository()
            return self._project_event_repo.emit(
                project_id if project_id is not None else self.project_id,
                actor_user_id if actor_user_id is not None else self.user_id,
                event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload or {},
            )
        except Exception:
            return None

    @staticmethod
    def _sync_ids(values) -> list[int]:
        return unique_ints(values or [])
