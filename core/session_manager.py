# session_manager.py

class SessionManager:
    """
    Singleton session manager for storing the current user session.
    Accessible from any page or service.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
            cls._instance.user_id = None
            cls._instance.username = None
            cls._instance.role_id = None
            cls._instance.project_id = None
            cls._instance.is_admin = False
        return cls._instance

    def start_session(self, user_id, username, role_id, project_id=None, is_admin=False):
        self.user_id = user_id
        self.username = username
        self.role_id = role_id
        self.project_id = project_id
        self.is_admin = bool(is_admin)

    def update_project(self, project_id):
        """Update the current project in session."""
        self.project_id = project_id

    def end_session(self):
        self.user_id = None
        self.username = None
        self.role_id = None
        self.project_id = None
        self.is_admin = False

    def is_active(self):
        return self.user_id is not None
