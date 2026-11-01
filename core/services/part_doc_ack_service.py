from typing import Optional, Dict

from core.repositories.part_doc_ack_repository import PartDocAckRepository
from core.session_manager import SessionManager

MANUAL_OUTDATED_PREFIX = "manual:outdated:"


class PartDocAckService:
    def __init__(self, repo: Optional[PartDocAckRepository] = None):
        self.repo = repo or PartDocAckRepository()
        self.session = SessionManager()

    def get_ack(self, part_id: int, doc_type: str) -> Optional[Dict]:
        return self.repo.get_ack(part_id, doc_type)

    def mark_up_to_date(self, part_id: int, doc_type: str, acknowledged_against: str):
        user_id = self.session.user_id
        self.repo.upsert_ack(part_id, doc_type, acknowledged_against=acknowledged_against, acknowledged_by=user_id)

    def mark_outdated(self, part_id: int, doc_type: str, applies_to: str):
        """Persist a user's manual outdated mark for the current Item revision."""
        user_id = self.session.user_id
        token = f"{MANUAL_OUTDATED_PREFIX}{str(applies_to or '').strip()}"
        self.repo.upsert_ack(part_id, doc_type, acknowledged_against=token, acknowledged_by=user_id)

    @staticmethod
    def is_manually_outdated(ack: Optional[Dict], applies_to: str) -> bool:
        if not ack:
            return False
        expected = f"{MANUAL_OUTDATED_PREFIX}{str(applies_to or '').strip()}"
        return str(ack.get("acknowledged_against") or "").strip() == expected
