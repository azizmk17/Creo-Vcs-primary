from core.repositories.user_role_repository import UserRoleRepository
from core.repositories.role_permission_repository import RolePermissionRepository

class AdminService:
    def __init__(self):
        self.user_role_repo = UserRoleRepository()
        self.role_perm_repo = RolePermissionRepository()

    # User ↔ Role
    def assign_role_to_user(self, user_id: int, role_id: int):
        self.user_role_repo.assign_role(user_id, role_id)

    def remove_role_from_user(self, user_id: int, role_id: int):
        self.user_role_repo.remove_role(user_id, role_id)

    def get_roles_for_user(self, user_id: int):
        return self.user_role_repo.get_roles_for_user(user_id)

    # Role ↔ Permission
    def add_permission_to_role(self, role_id: int, permission_id: int):
        self.role_perm_repo.add_permission_to_role(role_id, permission_id)

    def remove_permission_from_role(self, role_id: int, permission_id: int):
        self.role_perm_repo.remove_permission_from_role(role_id, permission_id)

    def get_permissions_for_role(self, role_id: int):
        return self.role_perm_repo.get_permissions_for_role(role_id)
