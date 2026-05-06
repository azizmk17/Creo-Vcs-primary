from typing import Optional, Dict

from core.repositories.part_doc_ack_repository import PartDocAckRepository
from core.session_manager import SessionManager


class PartDocAckService:
    def __init__(self, repo: Optional[PartDocAckRepository] = None):
        self.repo = repo or PartDocAckRepository()
        self.session = SessionManager()

    def get_ack(self, part_id: int, doc_type: str) -> Optional[Dict]:
        return self.repo.get_ack(part_id, doc_type)

    def mark_up_to_date(self, part_id: int, doc_type: str, acknowledged_against: str):
        user_id = self.session.user_id
        self.repo.upsert_ack(part_id, doc_type, acknowledged_against=acknowledged_against, acknowledged_by=user_id)
