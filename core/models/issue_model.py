from dataclasses import dataclass
from typing import Optional


ISSUE_STATUSES = (
    "Open",
    "In Progress",
    "Ready For Validation",
    "Closed",
    "Rejected",
)

ISSUE_PRIORITIES = ("Low", "Medium", "High", "Critical")

ISSUE_CATEGORIES = (
    "Design",
    "Manufacturing",
    "Documentation",
    "Assembly",
    "Validation",
    "Customer Request",
    "Change Request",
)

ISSUE_TRANSITIONS = {
    "Open": {"In Progress", "Rejected"},
    "In Progress": {"Ready For Validation", "Rejected"},
    "Ready For Validation": {"Closed", "In Progress", "Rejected"},
    "Closed": {"Open"},
    "Rejected": {"In Progress"},
}


@dataclass
class Issue:
    id: Optional[int]
    issue_number: str
    title: str
    description: str
    status: str
    priority: str
    category: str
    created_by: Optional[int]
    assigned_to: Optional[int]
    created_at: Optional[str]
    updated_at: Optional[str]
    due_date: Optional[str]
    project_id: Optional[int]
    closed_by: Optional[int] = None
    closed_at: Optional[str] = None
    archived: int = 0
    archive_reason: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return not self.archived and self.status != "Closed"

