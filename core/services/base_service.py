# base_service.py
from core.session_manager import SessionManager

class BaseService:
    def __init__(self):
        self.session = SessionManager()
        if not self.session.is_active():
            raise PermissionError("You must be logged in")

    @property
    def user_id(self):
        return self.session.user_id

    @property
    def project_id(self):
        return self.session.project_id

    @property
    def role_id(self):
        return self.session.role_id
