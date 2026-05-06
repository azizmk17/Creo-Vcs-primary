# core/services/ui_permission.py
from core.session_manager import SessionManager
from core.repositories.permission_repository import PermissionRepository
from core.services.role_service import RoleService
from core.repositories.user_repository import UserRepository

class UIPermissionHelper:
    def __init__(self):
        self.session = SessionManager()
        self.perm_repo = PermissionRepository()
        self.role_service = RoleService()
        self.user_repo = UserRepository()

        # Role hierarchy (lowest -> highest)
        self._role_level = {
            "designer": 1,
            "checker": 2,
            "master": 3,
            "admin": 4,
        }

        # Minimum role required per action
        self._permission_min_role = {
            "commit": "designer",
            "manage_parts": "checker",   # add/edit/delete parts, add child
            "validate": "checker",
            # push/merge actions in UI use "merge" permission
            "merge": "master",
            "admin_panel": "admin",      # admin tab visibility
        }

    def _current_user_level(self) -> int:
        if not self.session.is_active():
            return 0

        # If user is flagged admin in users table, treat as admin.
        try:
            u = self.user_repo.find_by_id(int(self.session.user_id))
            if u and int(getattr(u, "is_admin", 0) or 0) == 1:
                return self._role_level["admin"]
        except Exception:
            pass

        try:
            roles = self.role_service.get_role_for_user(self.session.user_id) or []
        except Exception:
            roles = []

        max_level = 0
        for r in roles:
            try:
                name = str((r.get("name") if isinstance(r, dict) else "") or "").strip().lower()
            except Exception:
                name = ""
            max_level = max(max_level, int(self._role_level.get(name, 0)))
        return max_level

    def can(self, permission_name: str, project_id: int = None) -> bool:
        if not self.session.is_active():
            return False

        pname = (permission_name or "").strip().lower()
        level = self._current_user_level()

        # Admin can do everything.
        if level >= self._role_level["admin"]:
            return True

        # Role-hierarchy enforcement for key actions.
        min_role = self._permission_min_role.get(pname)
        if min_role:
            return level >= int(self._role_level.get(min_role, 99))

        # Fallback to DB-configured permissions for other actions.
        return self.perm_repo.user_has_permission(self.session.user_id, permission_name, project_id or self.session.project_id)
