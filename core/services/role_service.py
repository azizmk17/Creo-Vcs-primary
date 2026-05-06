from core.repositories.role_repository import RoleRepository

class RoleService:
    def __init__(self):
        self.role_repo = RoleRepository()

    def get_all_roles(self):
        return self.role_repo.get_all()

    def create_role(self, name: str) -> int:
        return self.role_repo.create_role(name)

    def rename_role(self, role_id: int, new_name: str) -> None:
        return self.role_repo.rename_role(role_id, new_name)

    def delete_role(self, role_id: int) -> None:
        return self.role_repo.delete_role(role_id)
    
    def get_role_for_user(self, user_id):
        return self.role_repo.get_roles_for_user(user_id)

    def get_permissions_for_role(self, role_id):
        return self.role_repo.get_permissions(role_id)

    def add_permission_to_role(self, role_id, permission_id):
        return self.role_repo.add_permission(role_id, permission_id)

    def remove_permission_from_role(self, role_id, permission_id):
        return self.role_repo.remove_permission(role_id, permission_id)
