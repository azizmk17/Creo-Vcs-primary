from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class KpiThresholds:
    green: float = 90.0
    amber: float = 70.0


@dataclass(frozen=True)
class KpiResult:
    key: str
    title: str
    value: float | int | str
    percentage: float | None = None
    completed_count: int | None = None
    total_count: int | None = None
    status: str = "neutral"
    severity: str | None = None
    description: str = ""
    trend: float | None = None
    drilldown_available: bool = False
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class RiskItem:
    severity: str
    title: str
    description: str
    metric_key: str | None = None
    affected_count: int | None = None
    owner: str | None = None
    age: str | None = None
    due_date: str | None = None
    action: str = ""


@dataclass(frozen=True)
class WorkloadRow:
    engineer: str
    in_work: int = 0
    reviews: int = 0
    issues: int = 0
    overdue: int = 0
    stale_checkouts: int = 0


@dataclass(frozen=True)
class DashboardSection:
    title: str
    kpis: list[KpiResult] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardSnapshot:
    project_id: int | None
    project_name: str
    project_version: str
    phase: str
    date_range_label: str
    refreshed_at: str
    executive_kpis: list[KpiResult]
    manufacturing: DashboardSection
    release: DashboardSection
    quality: DashboardSection
    checkout: DashboardSection
    issue_health: DashboardSection
    unsupported: DashboardSection
    risks: list[RiskItem]
    release_blockers: list[RiskItem]
    workload: list[WorkloadRow]
    recent_activity: list[dict[str, Any]]
    definitions: dict[str, str]

