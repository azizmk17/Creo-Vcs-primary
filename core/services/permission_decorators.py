# core/services/permission_decorators.py
from functools import wraps
from core.session_manager import SessionManager
from core.repositories.permission_repository import PermissionRepository
from config import DB_NAME

def require_permission(permission_name: str, project_arg_index: int = None, project_kwarg: str = "project_id", db_path: str = DB_NAME):
    """
    Decorator that enforces permission for the logged-in session user.
    - permission_name: string name in permissions table (e.g. "merge")
    - project_arg_index: if the wrapped function receives project_id as a positional arg, index it here
    - project_kwarg: if project_id is passed by kwarg use this name
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            session = SessionManager()
            if not session.is_active():
                raise PermissionError("User not logged in")

            user_id = session.user_id

            # Resolve project_id: kwarg > positional arg > session.project_id
            if project_kwarg and project_kwarg in kwargs:
                project_id = kwargs[project_kwarg]
            elif project_arg_index is not None and len(args) > project_arg_index:
                project_id = args[project_arg_index]
            else:
                project_id = session.project_id

            perm_repo = PermissionRepository()
            if not perm_repo.user_has_permission(user_id, permission_name, project_id):
                raise PermissionError(f"User {user_id} lacks permission '{permission_name}' for project {project_id}")

            return func(*args, **kwargs)
        return wrapper
    return decorator
