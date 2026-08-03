from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DB_NAME
from core.models.dashboard_model import (
    DashboardSection,
    DashboardSnapshot,
    KpiResult,
    KpiThresholds,
    RiskItem,
    WorkloadRow,
)
from core.session_manager import SessionManager


DASHBOARD_THRESHOLDS = {
    "default": KpiThresholds(green=90.0, amber=70.0),
}

DASHBOARD_WEIGHTS = {
    "release_readiness": 0.25,
    "manufacturing_completeness": 0.20,
    "bom_cad_integrity": 0.20,
    "issue_health": 0.15,
    "review_status": 0.10,
    "checkout_health": 0.05,
    "metadata_completeness": 0.05,
}

STALE_CHECKOUT_DAYS = 7


class DashboardService:
    """Manager dashboard aggregates.

    The service intentionally uses aggregate SQL and scoped drilldown queries.
    UI widgets must not load the full EBOM/CAD structures for dashboard KPIs.
    """

    def __init__(self, db_name: str = DB_NAME):
        self.db_name = db_name
        self.session = SessionManager()

    def get_conn(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _table_exists(conn, name: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
        )

    @staticmethod
    def _columns(conn, table: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    @staticmethod
    def _pct(completed: int, total: int) -> float | None:
        if total <= 0:
            return None
        return round((float(completed) / float(total)) * 100.0, 1)

    @staticmethod
    def _status(percentage: float | None, *, reverse: bool = False) -> str:
        if percentage is None:
            return "neutral"
        thresholds = DASHBOARD_THRESHOLDS["default"]
        if reverse:
            if percentage <= 0:
                return "green"
            if percentage <= 10:
                return "amber"
            return "red"
        if percentage >= thresholds.green:
            return "green"
        if percentage >= thresholds.amber:
            return "amber"
        return "red"

    @staticmethod
    def _kpi(
        key: str,
        title: str,
        completed: int,
        total: int,
        description: str,
        *,
        value: float | int | str | None = None,
        reverse: bool = False,
        drilldown: bool = True,
    ) -> KpiResult:
        pct = DashboardService._pct(int(completed), int(total))
        display_value: float | int | str = value if value is not None else (pct if pct is not None else "N/A")
        return KpiResult(
            key=key,
            title=title,
            value=display_value,
            percentage=pct,
            completed_count=int(completed),
            total_count=int(total),
            status=DashboardService._status(pct, reverse=reverse),
            description=description,
            drilldown_available=drilldown,
        )

    @staticmethod
    def _unavailable(key: str, title: str, reason: str) -> KpiResult:
        return KpiResult(
            key=key,
            title=title,
            value="N/A",
            status="neutral",
            description=reason,
            unavailable_reason=reason,
            drilldown_available=False,
        )

    def _project_row(self, conn, project_id: int) -> dict[str, Any]:
        if not self._table_exists(conn, "projects"):
            return {}
        row = conn.execute("SELECT * FROM projects WHERE id=?", (int(project_id),)).fetchone()
        return dict(row) if row else {}

    def _item_scope_where(self, conn) -> str:
        cols = self._columns(conn, "bom")
        clauses = ["b.project_id=?"]
        if "represented_part_id" in cols:
            clauses.append("b.represented_part_id IS NULL")
        return " AND ".join(clauses)

    def _bom_value_expr(self, conn, column: str, default: str = "") -> str:
        if column in self._columns(conn, "bom"):
            escaped = str(default).replace("'", "''")
            return f"upper(TRIM(COALESCE(b.{column},'{escaped}')))"
        escaped = str(default).replace("'", "''")
        return f"'{escaped.upper()}'"

    def _lifecycle_expr(self, conn) -> str:
        cols = self._columns(conn, "bom")
        if "lifecycle_state" in cols and "status" in cols:
            return "COALESCE(b.lifecycle_state,b.status,'')"
        if "lifecycle_state" in cols:
            return "COALESCE(b.lifecycle_state,'')"
        if "status" in cols:
            return "COALESCE(b.status,'')"
        return "''"

    def _deliverable_item_condition(self, conn) -> str:
        behavior = self._bom_value_expr(conn, "default_ebom_behavior", "NORMAL")
        return f"{behavior}='NORMAL'"

    def _pdf_applicable_condition(self, conn) -> str:
        behavior = self._bom_value_expr(conn, "default_ebom_behavior", "NORMAL")
        item_type = self._bom_value_expr(conn, "item_type", "MECHANICAL_PART")
        procurement = self._bom_value_expr(conn, "procurement_source", "MAKE")
        drawing_req = self._bom_value_expr(conn, "drawing_requirement", "OPTIONAL")
        control_mode = self._bom_value_expr(conn, "cad_control_mode", "CONTROLLED")
        return (
            f"({drawing_req}='REQUIRED' OR ("
            f"{behavior}='NORMAL' "
            f"AND {drawing_req}<>'NOT_REQUIRED' "
            f"AND {item_type} NOT IN ('PURCHASED_PART','REFERENCE_PART','SOFTWARE_PART') "
            f"AND {procurement}<>'BUY' "
            f"AND {control_mode}<>'SUPPLIER_PACKAGE'"
            f"))"
        )

    def _step_applicable_condition(self, conn) -> str:
        behavior = self._bom_value_expr(conn, "default_ebom_behavior", "NORMAL")
        item_type = self._bom_value_expr(conn, "item_type", "MECHANICAL_PART")
        procurement = self._bom_value_expr(conn, "procurement_source", "MAKE")
        cad_req = self._bom_value_expr(conn, "cad_requirement", "OPTIONAL")
        control_mode = self._bom_value_expr(conn, "cad_control_mode", "CONTROLLED")
        return (
            f"({cad_req}='REQUIRED' OR ("
            f"{behavior}='NORMAL' "
            f"AND {cad_req}<>'NOT_REQUIRED' "
            f"AND {item_type}='MECHANICAL_PART' "
            f"AND {procurement} IN ('MAKE','MAKE_OR_BUY') "
            f"AND {control_mode}<>'SUPPLIER_PACKAGE'"
            f"))"
        )

    def _cad_applicable_condition(self, conn) -> str:
        return self._step_applicable_condition(conn)

    def _manufacturing_metadata_condition(self, conn) -> str:
        behavior = self._bom_value_expr(conn, "default_ebom_behavior", "NORMAL")
        item_type = self._bom_value_expr(conn, "item_type", "MECHANICAL_PART")
        procurement = self._bom_value_expr(conn, "procurement_source", "MAKE")
        return (
            f"{behavior}='NORMAL' "
            f"AND {item_type}='MECHANICAL_PART' "
            f"AND {procurement} IN ('MAKE','MAKE_OR_BUY')"
        )

    def _applicability_condition(self, conn, kind: str) -> str:
        kind = str(kind or "").lower()
        if kind in {"pdf", "drawing"}:
            return self._pdf_applicable_condition(conn)
        if kind in {"step", "cad"}:
            return self._step_applicable_condition(conn)
        if kind in {"material", "weight"}:
            return self._manufacturing_metadata_condition(conn)
        if kind in {"release", "revision", "package"}:
            return self._deliverable_item_condition(conn)
        return "1=1"

    def _count_applicable_items(self, conn, project_id: int, kind: str) -> int:
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, kind)
        return int(conn.execute(
            f"SELECT COUNT(*) FROM bom b WHERE {where} AND {applicable}",
            (project_id,),
        ).fetchone()[0] or 0)

    def get_dashboard(self, project_id: int | None = None, filters: dict | None = None) -> DashboardSnapshot:
        project_id = int(project_id or self.session.project_id or 0)
        refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not project_id:
            empty = self._unavailable("no_project", "No Project", "No project is selected.")
            return DashboardSnapshot(
                project_id=None,
                project_name="No project selected",
                project_version="-",
                phase="No data",
                date_range_label="All time",
                refreshed_at=refreshed_at,
                executive_kpis=[empty],
                manufacturing=DashboardSection("Manufacturing Readiness"),
                release=DashboardSection("Release Readiness"),
                quality=DashboardSection("Data and Design Quality"),
                checkout=DashboardSection("Checkout Health"),
                issue_health=DashboardSection("Risks and Issues"),
                unsupported=DashboardSection("Unavailable Metrics", [empty]),
                risks=[],
                release_blockers=[],
                workload=[],
                recent_activity=[],
                definitions=self.metric_definitions(),
            )

        with self.get_conn() as conn:
            project = self._project_row(conn, project_id)
            item_total = self._count_items(conn, project_id)
            delivery_total = self._count_applicable_items(conn, project_id, "release")
            cad_applicable = self._count_applicable_items(conn, project_id, "cad")
            pdf_applicable = self._count_applicable_items(conn, project_id, "pdf")
            step_applicable = self._count_applicable_items(conn, project_id, "step")
            material_applicable = self._count_applicable_items(conn, project_id, "material")
            weight_applicable = self._count_applicable_items(conn, project_id, "weight")
            released = self._count_released(conn, project_id)
            owner_cad = self._count_owner_cad_items(conn, project_id)
            pdf = self._deliverable_count(conn, project_id, "PDF")
            step = self._deliverable_count(conn, project_id, "STEP")
            material = self._not_empty_count(conn, project_id, "material", applicability="material")
            weight = self._not_empty_count(conn, project_id, "weight", applicability="weight")
            revision = self._not_empty_count(conn, project_id, "revision", applicability="revision")
            package_complete = self._complete_manufacturing_package_count(conn, project_id)
            critical_issues = self._issue_count(conn, project_id, priority="Critical", open_only=True)
            open_issues = self._issue_count(conn, project_id, open_only=True)
            overdue_issues = self._overdue_issue_count(conn, project_id)
            checked_out = self._checkout_count(conn, project_id)
            stale_checkouts = self._stale_checkout_count(conn, project_id)
            metadata_complete = self._mandatory_metadata_count(conn, project_id)
            integrity_counts = self._integrity_counts(conn, project_id)

            manufacturing_kpis = [
                self._kpi("cad_coverage", "CAD Coverage", owner_cad, cad_applicable, "Items requiring controlled native CAD and having an active OWNER CAD association."),
                self._kpi("pdf_coverage", "PDF Coverage", pdf, pdf_applicable, "Items requiring delivery drawings/PDF and having an active PDF managed file."),
                self._kpi("step_coverage", "STEP Coverage", step, step_applicable, "Items requiring neutral 3D delivery and having an active STEP managed file."),
                self._kpi("material_coverage", "Material Coverage", material, material_applicable, "Manufactured mechanical Items with material/specification metadata."),
                self._kpi("weight_coverage", "Weight Coverage", weight, weight_applicable, "Manufactured mechanical Items with weight metadata."),
                self._kpi("revision_coverage", "Revision Coverage", revision, delivery_total, "Deliverable Items with an assigned revision."),
                self._kpi("released_coverage", "Released Items", released, delivery_total, "Deliverable Items whose lifecycle state is Released."),
                self._kpi("manufacturing_package", "Complete Manufacturing Package", package_complete, delivery_total, "Deliverable Items satisfying only the requirements applicable to their type/source/policy."),
            ]

            release_score = 100.0 if delivery_total == 0 else (self._pct(released, delivery_total) or 0.0)
            manufacturing_score = 100.0 if delivery_total == 0 else (self._pct(package_complete, delivery_total) or 0.0)
            integrity_score = max(0.0, 100.0 - self._integrity_penalty(item_total, integrity_counts))
            issue_score = max(0.0, 100.0 - (critical_issues * 18.0) - (overdue_issues * 6.0) - max(0, open_issues - critical_issues) * 1.5)
            checkout_score = max(0.0, 100.0 - stale_checkouts * 12.0 - max(0, checked_out - stale_checkouts) * 1.0)
            metadata_score = self._pct(metadata_complete, item_total) or 0.0
            review_score = None
            health_score = self._project_health_score(
                release_score=release_score,
                manufacturing_score=manufacturing_score,
                integrity_score=integrity_score,
                issue_score=issue_score,
                review_score=review_score,
                checkout_score=checkout_score,
                metadata_score=metadata_score,
            )

            executive = [
                KpiResult("project_health", "Project Health", round(health_score, 1), health_score, status=self._status(health_score), description="Weighted project-health score. Critical risks are still shown separately.", drilldown_available=True),
                KpiResult("release_readiness", "Release Readiness", round(release_score, 1), release_score, released, delivery_total, self._status(release_score), description="Released deliverable EBOM Items divided by deliverable Items in scope.", drilldown_available=True),
                KpiResult("manufacturing_readiness", "Manufacturing Readiness", round(manufacturing_score, 1), manufacturing_score, package_complete, delivery_total, self._status(manufacturing_score), description="Complete manufacturing packages divided by deliverable Items in scope.", drilldown_available=True),
                KpiResult("critical_risks", "Open Critical Risks", critical_issues, None, critical_issues, None, "red" if critical_issues else "green", severity="critical" if critical_issues else None, description="Open Critical engineering issues.", drilldown_available=True),
            ]

            release_kpis = [
                self._kpi("released_items", "Released", released, delivery_total, "Released deliverable EBOM Items."),
                self._count_kpi(conn, project_id, "items_in_work", "In Work", "Items currently checked out or in WIP state."),
                KpiResult("blocked_items", "Blocked / Critical", critical_issues, None, critical_issues, None, "red" if critical_issues else "green", description="Items linked to Critical open issues.", drilldown_available=True),
                KpiResult("missing_deliverables", "Missing Deliverables", max(0, delivery_total - package_complete), None, max(0, delivery_total - package_complete), delivery_total, self._status(self._pct(package_complete, delivery_total)), description="Deliverable Items missing at least one applicable manufacturing-package requirement.", drilldown_available=True),
            ]

            quality_kpis = [
                KpiResult("bom_cad_integrity", "BOM / CAD Integrity", round(integrity_score, 1), integrity_score, status=self._status(integrity_score), description="Integrity score derived from orphan CAD, owner association, drawing and metadata problems.", drilldown_available=True),
                KpiResult("orphan_cad", "Orphan CAD", integrity_counts["orphan_cad"], None, integrity_counts["orphan_cad"], None, "red" if integrity_counts["orphan_cad"] else "green", description="Managed CAD models without an active Item association.", drilldown_available=True),
                KpiResult("orphan_drawings", "Orphan Drawings", integrity_counts["orphan_drawings"], None, integrity_counts["orphan_drawings"], None, "amber" if integrity_counts["orphan_drawings"] else "green", description="Drawing CAD Documents not bound to a model.", drilldown_available=True),
                KpiResult("multiple_owner", "Multiple OWNER CAD", integrity_counts["multiple_owner"], None, integrity_counts["multiple_owner"], None, "red" if integrity_counts["multiple_owner"] else "green", description="Items with more than one active OWNER CAD association.", drilldown_available=True),
                KpiResult("missing_metadata", "Missing Metadata", item_total - metadata_complete, None, item_total - metadata_complete, item_total, self._status(self._pct(metadata_complete, item_total)), description="Items missing required managerial metadata.", drilldown_available=True),
            ]

            checkout_kpis = [
                KpiResult("checked_out", "Currently Checked Out", checked_out, None, checked_out, None, "amber" if checked_out else "green", description="Active item checkouts.", drilldown_available=True),
                KpiResult("stale_checkouts", f"Stale Checkouts > {STALE_CHECKOUT_DAYS}d", stale_checkouts, None, stale_checkouts, None, "red" if stale_checkouts else "green", description="Active checkouts older than the configured stale threshold.", drilldown_available=True),
                KpiResult("checkout_health", "Checkout Health", round(checkout_score, 1), checkout_score, status=self._status(checkout_score), description="Operational checkout health score.", drilldown_available=True),
            ]

            issue_kpis = [
                KpiResult("open_issues", "Open Issues", open_issues, None, open_issues, None, "amber" if open_issues else "green", description="Open engineering issues in the project.", drilldown_available=True),
                KpiResult("critical_issues", "Critical Issues", critical_issues, None, critical_issues, None, "red" if critical_issues else "green", description="Open Critical issues.", drilldown_available=True),
                KpiResult("overdue_issues", "Overdue Issues", overdue_issues, None, overdue_issues, None, "red" if overdue_issues else "green", description="Open issues past due date.", drilldown_available=True),
                KpiResult("issue_health", "Issue Health", round(issue_score, 1), issue_score, status=self._status(issue_score), description="Issue health score penalized by critical and overdue issues.", drilldown_available=True),
            ]

            unsupported = DashboardSection("Future Workflow Metrics", [
                self._unavailable("review_health", "Review Bottlenecks", "Formal review workflow tables are not available yet."),
                self._unavailable("change_health", "Engineering Change Lead Time", "Formal change objects are not available yet; commits/revisions can be mapped later."),
                self._unavailable("readiness_trends", "Readiness Trends", "Historical dashboard KPI snapshots are not configured yet."),
            ])

            return DashboardSnapshot(
                project_id=project_id,
                project_name=str(project.get("name") or f"Project {project_id}"),
                project_version=str(project.get("version_label") or project.get("revision") or "-"),
                phase=str(project.get("version_state") or project.get("phase") or "WIP"),
                date_range_label=str((filters or {}).get("date_range") or "All time"),
                refreshed_at=refreshed_at,
                executive_kpis=executive,
                manufacturing=DashboardSection("Manufacturing Readiness", manufacturing_kpis),
                release=DashboardSection("Release Readiness", release_kpis),
                quality=DashboardSection("Data and Design Quality", quality_kpis),
                checkout=DashboardSection("Checkout and Workspace Health", checkout_kpis),
                issue_health=DashboardSection("Risks and Management Attention", issue_kpis),
                unsupported=unsupported,
                risks=self._top_risks(conn, project_id, delivery_total, package_complete, critical_issues, stale_checkouts, integrity_counts),
                release_blockers=self._release_blockers(delivery_total, package_complete, pdf, step, critical_issues, stale_checkouts, integrity_counts),
                workload=self._workload(conn, project_id),
                recent_activity=self._recent_activity(conn, project_id),
                definitions=self.metric_definitions(),
            )

    def _project_health_score(self, **scores) -> float:
        weighted = 0.0
        total_weight = 0.0
        score_map = {
            "release_readiness": scores.get("release_score"),
            "manufacturing_completeness": scores.get("manufacturing_score"),
            "bom_cad_integrity": scores.get("integrity_score"),
            "issue_health": scores.get("issue_score"),
            "review_status": scores.get("review_score"),
            "checkout_health": scores.get("checkout_score"),
            "metadata_completeness": scores.get("metadata_score"),
        }
        for key, weight in DASHBOARD_WEIGHTS.items():
            value = score_map.get(key)
            if value is None:
                continue
            weighted += float(value) * float(weight)
            total_weight += float(weight)
        return round(weighted / total_weight, 1) if total_weight else 0.0

    def _count_items(self, conn, project_id: int) -> int:
        where = self._item_scope_where(conn)
        return int(conn.execute(f"SELECT COUNT(*) FROM bom b WHERE {where}", (project_id,)).fetchone()[0] or 0)

    def _count_released(self, conn, project_id: int) -> int:
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, "release")
        lifecycle = self._lifecycle_expr(conn)
        return int(conn.execute(
            f"SELECT COUNT(*) FROM bom b WHERE {where} AND {applicable} AND upper({lifecycle})='RELEASED'",
            (project_id,),
        ).fetchone()[0] or 0)

    def _not_empty_count(self, conn, project_id: int, column: str, applicability: str | None = None) -> int:
        if column not in self._columns(conn, "bom"):
            return 0
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, applicability or column)
        return int(conn.execute(
            f"SELECT COUNT(*) FROM bom b WHERE {where} AND {applicable} AND TRIM(COALESCE(b.{column},''))<>''",
            (project_id,),
        ).fetchone()[0] or 0)

    def _count_owner_cad_items(self, conn, project_id: int) -> int:
        if not self._table_exists(conn, "cad_item_associations"):
            return 0
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, "cad")
        return int(conn.execute(
            f"""
            SELECT COUNT(DISTINCT a.item_id)
            FROM cad_item_associations a
            JOIN bom b ON b.id=a.item_id
            WHERE {where} AND {applicable}
              AND a.active=1 AND upper(a.association_type)='OWNER'
            """,
            (project_id,),
        ).fetchone()[0] or 0)

    def _deliverable_count(self, conn, project_id: int, file_type: str) -> int:
        if not (self._table_exists(conn, "part_files") and self._table_exists(conn, "part_file_versions")):
            return 0
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, file_type)
        return int(conn.execute(
            f"""
            SELECT COUNT(DISTINCT b.id)
            FROM bom b
            JOIN part_files pf ON pf.part_id=b.id AND pf.deleted_at IS NULL
            JOIN part_file_versions v ON v.id=pf.active_version_id AND v.deleted_at IS NULL
            WHERE {where} AND {applicable}
              AND upper(pf.file_type)=upper(?)
              AND COALESCE(v.size_bytes, 1) > 0
            """,
            (project_id, file_type),
        ).fetchone()[0] or 0)

    def _complete_manufacturing_package_count(self, conn, project_id: int) -> int:
        if not (
            self._table_exists(conn, "cad_item_associations")
            and self._table_exists(conn, "part_files")
            and self._table_exists(conn, "part_file_versions")
        ):
            return 0
        where = self._item_scope_where(conn)
        package_applicable = self._applicability_condition(conn, "package")
        cad_applicable = self._applicability_condition(conn, "cad")
        pdf_applicable = self._applicability_condition(conn, "pdf")
        step_applicable = self._applicability_condition(conn, "step")
        material_applicable = self._applicability_condition(conn, "material")
        weight_applicable = self._applicability_condition(conn, "weight")
        lifecycle = self._lifecycle_expr(conn)
        return int(conn.execute(
            f"""
            SELECT COUNT(*)
            FROM bom b
            WHERE {where} AND {package_applicable}
              AND (
                NOT ({cad_applicable}) OR EXISTS (
                  SELECT 1 FROM cad_item_associations a
                  WHERE a.item_id=b.id AND a.active=1 AND upper(a.association_type)='OWNER'
                )
              )
              AND (
                NOT ({pdf_applicable}) OR EXISTS (
                  SELECT 1 FROM part_files pf JOIN part_file_versions v ON v.id=pf.active_version_id
                  WHERE pf.part_id=b.id AND pf.deleted_at IS NULL AND v.deleted_at IS NULL
                    AND upper(pf.file_type)='PDF' AND COALESCE(v.size_bytes,1)>0
                )
              )
              AND (
                NOT ({step_applicable}) OR EXISTS (
                  SELECT 1 FROM part_files pf JOIN part_file_versions v ON v.id=pf.active_version_id
                  WHERE pf.part_id=b.id AND pf.deleted_at IS NULL AND v.deleted_at IS NULL
                    AND upper(pf.file_type)='STEP' AND COALESCE(v.size_bytes,1)>0
                )
              )
              AND (NOT ({material_applicable}) OR TRIM(COALESCE(b.material,''))<>'')
              AND (NOT ({weight_applicable}) OR TRIM(COALESCE(b.weight,''))<>'')
              AND TRIM(COALESCE(b.revision,''))<>''
              AND upper({lifecycle})='RELEASED'
            """,
            (project_id,),
        ).fetchone()[0] or 0)

    def _mandatory_metadata_count(self, conn, project_id: int) -> int:
        where = self._item_scope_where(conn)
        clauses = [
            "TRIM(COALESCE(b.name,''))<>''",
            "TRIM(COALESCE(b.revision,''))<>''",
            "TRIM(COALESCE(b.type,''))<>''",
        ]
        if "item_type" in self._columns(conn, "bom"):
            clauses.append("TRIM(COALESCE(b.item_type,''))<>''")
        return int(conn.execute(
            f"SELECT COUNT(*) FROM bom b WHERE {where} AND {' AND '.join(clauses)}",
            (project_id,),
        ).fetchone()[0] or 0)

    def _issue_count(self, conn, project_id: int, *, priority: str | None = None, open_only: bool = False) -> int:
        if not self._table_exists(conn, "issues"):
            return 0
        clauses = ["project_id=?", "COALESCE(archived,0)=0"]
        params: list[Any] = [project_id]
        if open_only:
            clauses.append("status<>'Closed'")
        if priority:
            clauses.append("priority=?")
            params.append(priority)
        return int(conn.execute(f"SELECT COUNT(*) FROM issues WHERE {' AND '.join(clauses)}", params).fetchone()[0] or 0)

    def _overdue_issue_count(self, conn, project_id: int) -> int:
        if not self._table_exists(conn, "issues"):
            return 0
        return int(conn.execute(
            """
            SELECT COUNT(*) FROM issues
            WHERE project_id=? AND COALESCE(archived,0)=0 AND status<>'Closed'
              AND due_date IS NOT NULL AND date(due_date)<date('now')
            """,
            (project_id,),
        ).fetchone()[0] or 0)

    def _checkout_count(self, conn, project_id: int) -> int:
        if not self._table_exists(conn, "locks"):
            return 0
        return int(conn.execute(
            "SELECT COUNT(*) FROM locks l JOIN bom b ON b.id=l.part_id WHERE b.project_id=?",
            (project_id,),
        ).fetchone()[0] or 0)

    def _stale_checkout_count(self, conn, project_id: int) -> int:
        if not self._table_exists(conn, "locks"):
            return 0
        return int(conn.execute(
            """
            SELECT COUNT(*) FROM locks l JOIN bom b ON b.id=l.part_id
            WHERE b.project_id=?
              AND l.checked_out_at IS NOT NULL
              AND datetime(l.checked_out_at) < datetime('now', ?)
            """,
            (project_id, f"-{STALE_CHECKOUT_DAYS} days"),
        ).fetchone()[0] or 0)

    def _integrity_counts(self, conn, project_id: int) -> dict[str, int]:
        out = {"orphan_cad": 0, "orphan_drawings": 0, "multiple_owner": 0}
        has_cad = self._table_exists(conn, "cad_documents")
        has_assoc = self._table_exists(conn, "cad_item_associations")
        if has_cad and has_assoc:
            out["orphan_cad"] = int(conn.execute(
                """
                SELECT COUNT(*) FROM cad_documents d
                WHERE d.project_id=? AND upper(COALESCE(d.category,''))<>'DRAWING'
                  AND NOT EXISTS (
                    SELECT 1 FROM cad_item_associations a
                    WHERE a.cad_document_id=d.id AND a.active=1
                  )
                """,
                (project_id,),
            ).fetchone()[0] or 0)
        if has_cad:
            out["orphan_drawings"] = int(conn.execute(
                """
                SELECT COUNT(*) FROM cad_documents d
                WHERE d.project_id=? AND upper(COALESCE(d.category,''))='DRAWING'
                  AND d.drawing_owner_cad_document_id IS NULL
                """,
                (project_id,),
            ).fetchone()[0] or 0)
        if has_assoc:
            out["multiple_owner"] = int(conn.execute(
                """
                SELECT COUNT(*) FROM (
                  SELECT item_id FROM cad_item_associations
                  WHERE project_id=? AND active=1 AND upper(association_type)='OWNER'
                  GROUP BY item_id HAVING COUNT(*)>1
                )
                """,
                (project_id,),
            ).fetchone()[0] or 0)
        return out

    @staticmethod
    def _integrity_penalty(item_total: int, counts: dict[str, int]) -> float:
        denominator = max(1, item_total)
        weighted = counts.get("multiple_owner", 0) * 10 + counts.get("orphan_cad", 0) * 3 + counts.get("orphan_drawings", 0) * 1.5
        return min(100.0, (weighted / denominator) * 10.0)

    def _count_kpi(self, conn, project_id: int, key: str, title: str, description: str) -> KpiResult:
        if key == "items_in_work":
            if not self._table_exists(conn, "locks"):
                count = 0
                return KpiResult(key, title, count, None, count, None, "green", description=description, drilldown_available=True)
            where = self._item_scope_where(conn)
            lifecycle = self._lifecycle_expr(conn)
            count = int(conn.execute(
                f"""
                SELECT COUNT(DISTINCT b.id)
                FROM bom b
                LEFT JOIN locks l ON l.part_id=b.id
                WHERE {where}
                  AND (l.part_id IS NOT NULL OR upper({lifecycle}) IN ('WIP','IN_WORK'))
                """,
                (project_id,),
            ).fetchone()[0] or 0)
            return KpiResult(key, title, count, None, count, None, "amber" if count else "green", description=description, drilldown_available=True)
        return self._unavailable(key, title, "Metric not configured.")

    def _top_risks(self, conn, project_id: int, item_total: int, package_complete: int, critical_issues: int, stale_checkouts: int, integrity: dict[str, int]) -> list[RiskItem]:
        risks = []
        missing_packages = max(0, item_total - package_complete)
        if critical_issues:
            risks.append(RiskItem("Critical", "Critical open issues", f"{critical_issues} Critical issue(s) are still open.", "critical_issues", critical_issues, action="Review owners and unblock release decisions."))
        if integrity.get("multiple_owner"):
            risks.append(RiskItem("Critical", "Multiple OWNER CAD associations", "Some Items have more than one OWNER CAD document.", "multiple_owner", integrity["multiple_owner"], action="Resolve ownership before release."))
        if missing_packages:
            risks.append(RiskItem("High", "Missing manufacturing packages", f"{missing_packages} Item(s) are not manufacturing-package complete.", "missing_deliverables", missing_packages, action="Drive deliverable completion by assembly/work package."))
        if stale_checkouts:
            risks.append(RiskItem("High", "Stale checkouts", f"{stale_checkouts} active checkout(s) are older than {STALE_CHECKOUT_DAYS} days.", "stale_checkouts", stale_checkouts, action="Ask owners to check in or undo checkout."))
        if integrity.get("orphan_cad"):
            risks.append(RiskItem("Medium", "Orphan CAD files", "Managed CAD exists without EBOM association.", "orphan_cad", integrity["orphan_cad"], action="Associate, mark supplier package, or remove from managed scope."))
        return risks[:6]

    def _release_blockers(self, item_total: int, package_complete: int, pdf: int, step: int, critical_issues: int, stale_checkouts: int, integrity: dict[str, int]) -> list[RiskItem]:
        blockers = []
        missing_pdf = max(0, item_total - pdf)
        missing_step = max(0, item_total - step)
        missing_pack = max(0, item_total - package_complete)
        for count, title, key in (
            (missing_step, "Items missing STEP files", "missing_step"),
            (missing_pdf, "Items missing PDF drawings", "missing_pdf"),
            (missing_pack, "Incomplete manufacturing packages", "missing_deliverables"),
            (critical_issues, "Critical release issues", "critical_issues"),
            (stale_checkouts, "Stale checkouts", "stale_checkouts"),
            (integrity.get("multiple_owner", 0), "Invalid OWNER CAD assignments", "multiple_owner"),
        ):
            if count:
                severity = "Critical" if key in {"critical_issues", "multiple_owner"} else "High"
                blockers.append(RiskItem(severity, title, f"{count} affected Item(s).", key, count, action="Open drilldown and assign corrective action."))
        return blockers[:6]

    def _workload(self, conn, project_id: int) -> list[WorkloadRow]:
        rows: dict[str, dict[str, int]] = {}
        if self._table_exists(conn, "locks"):
            for row in conn.execute(
                """
                SELECT COALESCE(u.username,'Unassigned') AS user_name,
                       COUNT(*) AS checkout_count,
                       SUM(CASE WHEN l.checked_out_at IS NOT NULL AND datetime(l.checked_out_at)<datetime('now', ?) THEN 1 ELSE 0 END) AS stale_count
                FROM locks l
                JOIN bom b ON b.id=l.part_id
                LEFT JOIN users u ON u.id=l.user_id
                WHERE b.project_id=?
                GROUP BY COALESCE(u.username,'Unassigned')
                """,
                (f"-{STALE_CHECKOUT_DAYS} days", project_id),
            ):
                bucket = rows.setdefault(row["user_name"], {"in_work": 0, "issues": 0, "overdue": 0, "stale": 0})
                bucket["in_work"] += int(row["checkout_count"] or 0)
                bucket["stale"] += int(row["stale_count"] or 0)
        if self._table_exists(conn, "issues"):
            for row in conn.execute(
                """
                SELECT COALESCE(u.username,'Unassigned') AS user_name,
                       COUNT(*) AS issue_count,
                       SUM(CASE WHEN i.due_date IS NOT NULL AND date(i.due_date)<date('now') THEN 1 ELSE 0 END) AS overdue_count
                FROM issues i
                LEFT JOIN users u ON u.id=i.assigned_to
                WHERE i.project_id=? AND COALESCE(i.archived,0)=0 AND i.status<>'Closed'
                GROUP BY COALESCE(u.username,'Unassigned')
                """,
                (project_id,),
            ):
                bucket = rows.setdefault(row["user_name"], {"in_work": 0, "issues": 0, "overdue": 0, "stale": 0})
                bucket["issues"] += int(row["issue_count"] or 0)
                bucket["overdue"] += int(row["overdue_count"] or 0)
        result = [
            WorkloadRow(name, values["in_work"], 0, values["issues"], values["overdue"], values["stale"])
            for name, values in rows.items()
        ]
        result.sort(key=lambda row: (row.overdue, row.stale_checkouts, row.in_work + row.issues), reverse=True)
        return result[:12]

    def _recent_activity(self, conn, project_id: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self._table_exists(conn, "lock_logs"):
            for row in conn.execute(
                """
                SELECT ll.timestamp AS event_time,
                       'Checkout' AS event_type,
                       COALESCE(u.username,'Unknown') AS actor,
                       b.part_number,b.name,ll.action
                FROM lock_logs ll
                JOIN bom b ON b.id=ll.part_id
                LEFT JOIN users u ON u.id=ll.user_id
                WHERE b.project_id=?
                ORDER BY ll.timestamp DESC
                LIMIT 8
                """,
                (project_id,),
            ):
                events.append(dict(row))
        if self._table_exists(conn, "issues"):
            for row in conn.execute(
                """
                SELECT i.updated_at AS event_time,
                       'Issue' AS event_type,
                       COALESCE(u.username,'Unknown') AS actor,
                       i.issue_number AS part_number,
                       i.title AS name,
                       i.status AS action
                FROM issues i
                LEFT JOIN users u ON u.id=i.assigned_to
                WHERE i.project_id=?
                ORDER BY i.updated_at DESC
                LIMIT 8
                """,
                (project_id,),
            ):
                events.append(dict(row))
        events.sort(key=lambda row: str(row.get("event_time") or ""), reverse=True)
        return events[:10]

    def metric_definitions(self) -> dict[str, str]:
        return {
            "project_health": "Weighted score: release readiness, manufacturing completeness, BOM/CAD integrity, issue health, checkout health and mandatory metadata. Applicability rules exclude non-required purchased/reference/software/supplier-package deliverables.",
            "manufacturing_package": "A deliverable Item is complete when only its applicable requirements are satisfied: release/revision plus required CAD, PDF, STEP, material and weight rules.",
            "pdf_coverage": "PDF coverage counts only Items that require drawing/PDF delivery. Purchased, reference, software, excluded, flattened and supplier-package Items are excluded unless PDF/drawing is explicitly REQUIRED.",
            "step_coverage": "STEP coverage counts only Items that require neutral 3D delivery. Bought/standard/reference/software and supplier-package Items are excluded unless native CAD is explicitly REQUIRED.",
            "bom_cad_integrity": "Score penalized by multiple OWNER associations, orphan CAD models and orphan drawings.",
            "stale_checkouts": f"Active checkouts older than {STALE_CHECKOUT_DAYS} days.",
        }

    def get_drilldown_items(self, metric_key: str, project_id: int | None = None, filters: dict | None = None) -> list[dict[str, Any]]:
        project_id = int(project_id or self.session.project_id or 0)
        if not project_id:
            return []
        key = str(metric_key or "")
        with self.get_conn() as conn:
            if key in {"missing_pdf", "pdf_coverage"}:
                return self._missing_file_rows(conn, project_id, "PDF", "Missing or invalid PDF")
            if key in {"missing_step", "step_coverage"}:
                return self._missing_file_rows(conn, project_id, "STEP", "Missing or invalid STEP")
            if key in {"cad_coverage", "missing_cad"}:
                return self._missing_owner_cad_rows(conn, project_id)
            if key in {"released_coverage", "released_items", "release_readiness", "not_released"}:
                return self._not_released_rows(conn, project_id)
            if key in {"manufacturing_package", "manufacturing_readiness", "missing_deliverables"}:
                return self._missing_package_rows(conn, project_id)
            if key in {"stale_checkouts", "checked_out", "checkout_health"}:
                return self._checkout_rows(conn, project_id, stale_only=(key == "stale_checkouts"))
            if key in {"critical_issues", "critical_risks", "blocked_items", "open_issues", "overdue_issues", "issue_health"}:
                return self._issue_rows(conn, project_id, key)
            if key == "orphan_cad":
                return self._orphan_cad_rows(conn, project_id)
            if key == "orphan_drawings":
                return self._orphan_drawing_rows(conn, project_id)
            if key == "multiple_owner":
                return self._multiple_owner_rows(conn, project_id)
            if key == "missing_metadata":
                return self._missing_metadata_rows(conn, project_id)
            if key == "project_health":
                return self._missing_package_rows(conn, project_id)[:100]
        return []

    def _base_item_select(self, conn) -> str:
        cols = self._columns(conn, "bom")

        def expr(column: str, alias: str | None = None, default: str = "") -> str:
            target = alias or column
            if column in cols:
                return f"COALESCE(b.{column},'{default}') AS {target}"
            escaped = str(default).replace("'", "''")
            return f"'{escaped}' AS {target}"

        part_number = expr("part_number")
        aes_number = expr("aes_number")
        revision = expr("revision")
        type_expr = expr("type")
        lifecycle = f"{self._lifecycle_expr(conn)} AS lifecycle_state"
        material = expr("material")
        weight = expr("weight")
        modified = expr("modified")
        return f"""
            SELECT b.id,{part_number},{aes_number},b.name,{revision},{type_expr},
                   {expr('item_type')},
                   {expr('procurement_source')},
                   {expr('default_ebom_behavior')},
                   {expr('cad_requirement')},
                   {expr('drawing_requirement')},
                   {lifecycle},
                   {material},{weight},{modified}
            FROM bom b
        """

    @staticmethod
    def _item_dict(row, issue: str, action: str) -> dict[str, Any]:
        data = dict(row)
        data["missing_deliverable"] = issue
        data["recommended_action"] = action
        return data

    def _missing_file_rows(self, conn, project_id: int, file_type: str, issue: str) -> list[dict[str, Any]]:
        if not (self._table_exists(conn, "part_files") and self._table_exists(conn, "part_file_versions")):
            return []
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, file_type)
        rows = conn.execute(
            self._base_item_select(conn)
            + f"""
            WHERE {where} AND {applicable}
              AND NOT EXISTS (
                  SELECT 1 FROM part_files pf JOIN part_file_versions v ON v.id=pf.active_version_id
                  WHERE pf.part_id=b.id AND pf.deleted_at IS NULL AND v.deleted_at IS NULL
                    AND upper(pf.file_type)=upper(?) AND COALESCE(v.size_bytes,1)>0
              )
            ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name)
            """,
            (project_id, file_type),
        ).fetchall()
        return [self._item_dict(row, issue, f"Attach or generate a current {file_type} file.") for row in rows]

    def _missing_owner_cad_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "cad_item_associations"):
            return []
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, "cad")
        rows = conn.execute(
            self._base_item_select(conn)
            + f"""
            WHERE {where} AND {applicable}
              AND NOT EXISTS (
                  SELECT 1 FROM cad_item_associations a
                  WHERE a.item_id=b.id AND a.active=1 AND upper(a.association_type)='OWNER'
              )
            ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name)
            """,
            (project_id,),
        ).fetchall()
        return [self._item_dict(row, "Missing OWNER CAD", "Associate the correct owner CAD document.") for row in rows]

    def _not_released_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        where = self._item_scope_where(conn)
        applicable = self._applicability_condition(conn, "release")
        lifecycle = self._lifecycle_expr(conn)
        rows = conn.execute(
            self._base_item_select(conn)
            + f"""
            WHERE {where} AND {applicable}
              AND upper({lifecycle})<>'RELEASED'
            ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name)
            """,
            (project_id,),
        ).fetchall()
        return [self._item_dict(row, "Not released", "Complete review and release the item when ready.") for row in rows]

    def _missing_package_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        if not (
            self._table_exists(conn, "cad_item_associations")
            and self._table_exists(conn, "part_files")
            and self._table_exists(conn, "part_file_versions")
        ):
            return []
        where = self._item_scope_where(conn)
        package_applicable = self._applicability_condition(conn, "package")
        cad_applicable = self._applicability_condition(conn, "cad")
        pdf_applicable = self._applicability_condition(conn, "pdf")
        step_applicable = self._applicability_condition(conn, "step")
        material_applicable = self._applicability_condition(conn, "material")
        weight_applicable = self._applicability_condition(conn, "weight")
        lifecycle = self._lifecycle_expr(conn)
        rows = conn.execute(
            self._base_item_select(conn)
            + f"""
            WHERE {where} AND {package_applicable}
              AND (
                (({cad_applicable}) AND NOT EXISTS (SELECT 1 FROM cad_item_associations a WHERE a.item_id=b.id AND a.active=1 AND upper(a.association_type)='OWNER'))
                OR (({pdf_applicable}) AND NOT EXISTS (SELECT 1 FROM part_files pf JOIN part_file_versions v ON v.id=pf.active_version_id WHERE pf.part_id=b.id AND pf.deleted_at IS NULL AND v.deleted_at IS NULL AND upper(pf.file_type)='PDF' AND COALESCE(v.size_bytes,1)>0))
                OR (({step_applicable}) AND NOT EXISTS (SELECT 1 FROM part_files pf JOIN part_file_versions v ON v.id=pf.active_version_id WHERE pf.part_id=b.id AND pf.deleted_at IS NULL AND v.deleted_at IS NULL AND upper(pf.file_type)='STEP' AND COALESCE(v.size_bytes,1)>0))
                OR (({material_applicable}) AND TRIM(COALESCE(b.material,''))='')
                OR (({weight_applicable}) AND TRIM(COALESCE(b.weight,''))='')
                OR TRIM(COALESCE(b.revision,''))=''
                OR upper({lifecycle})<>'RELEASED'
              )
            ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name)
            """,
            (project_id,),
        ).fetchall()
        return [self._item_dict(row, "Incomplete package", "Complete missing CAD/files/metadata/release state.") for row in rows]

    def _checkout_rows(self, conn, project_id: int, *, stale_only: bool = False) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "locks"):
            return []
        where = "b.project_id=?"
        params: list[Any] = [project_id]
        if stale_only:
            where += " AND l.checked_out_at IS NOT NULL AND datetime(l.checked_out_at)<datetime('now', ?)"
            params.append(f"-{STALE_CHECKOUT_DAYS} days")
        rows = conn.execute(
            f"""
            SELECT b.id,b.part_number,b.name,b.revision,b.type,
                   {self._lifecycle_expr(conn)} AS lifecycle_state,
                   COALESCE(u.username,'Unknown') AS responsible_engineer,
                   l.checkout_origin,l.checked_out_at,
                   CASE WHEN l.checked_out_at IS NOT NULL THEN CAST(julianday('now')-julianday(l.checked_out_at) AS INTEGER) ELSE NULL END AS age_days
            FROM locks l
            JOIN bom b ON b.id=l.part_id
            LEFT JOIN users u ON u.id=l.user_id
            WHERE {where}
            ORDER BY l.checked_out_at ASC
            """,
            params,
        ).fetchall()
        return [self._item_dict(row, "Stale checkout" if stale_only else "Checked out", "Ask owner to check in or undo checkout if no longer active.") for row in rows]

    def _issue_rows(self, conn, project_id: int, key: str) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "issues"):
            return []
        clauses = ["i.project_id=?", "COALESCE(i.archived,0)=0"]
        params: list[Any] = [project_id]
        if key != "open_issues":
            clauses.append("i.status<>'Closed'")
        if key in {"critical_issues", "critical_risks", "blocked_items"}:
            clauses.append("i.priority='Critical'")
        if key == "overdue_issues":
            clauses.append("i.due_date IS NOT NULL AND date(i.due_date)<date('now')")
        rows = conn.execute(
            f"""
            SELECT i.id,i.issue_number,i.title,i.status,i.priority,i.category,
                   COALESCE(u.username,'Unassigned') AS responsible_engineer,
                   i.created_at,i.due_date,
                   GROUP_CONCAT(DISTINCT b.part_number || ' ' || b.name) AS affected_items
            FROM issues i
            LEFT JOIN users u ON u.id=i.assigned_to
            LEFT JOIN issue_parts ip ON ip.issue_id=i.id
            LEFT JOIN bom b ON b.id=ip.part_id
            WHERE {' AND '.join(clauses)}
            GROUP BY i.id
            ORDER BY CASE i.priority WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, i.due_date
            """,
            params,
        ).fetchall()
        return [dict(row, missing_deliverable="Issue", recommended_action="Assign owner, target date and closure path.") for row in rows]

    def _orphan_cad_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        if not (self._table_exists(conn, "cad_documents") and self._table_exists(conn, "cad_item_associations")):
            return []
        rows = conn.execute(
            """
            SELECT d.id,d.file_name AS part_number,d.name,d.revision,d.category AS type,
                   d.lifecycle_state,'Orphan CAD' AS missing_deliverable
            FROM cad_documents d
            WHERE d.project_id=? AND upper(COALESCE(d.category,''))<>'DRAWING'
              AND NOT EXISTS (SELECT 1 FROM cad_item_associations a WHERE a.cad_document_id=d.id AND a.active=1)
            ORDER BY lower(d.file_name)
            """,
            (project_id,),
        ).fetchall()
        return [dict(row, recommended_action="Associate to an Item or remove from managed CAD scope.") for row in rows]

    def _orphan_drawing_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "cad_documents"):
            return []
        rows = conn.execute(
            """
            SELECT d.id,d.file_name AS part_number,d.name,d.revision,d.category AS type,
                   d.lifecycle_state,'Orphan drawing' AS missing_deliverable
            FROM cad_documents d
            WHERE d.project_id=? AND upper(COALESCE(d.category,''))='DRAWING'
              AND d.drawing_owner_cad_document_id IS NULL
            ORDER BY lower(d.file_name)
            """,
            (project_id,),
        ).fetchall()
        return [dict(row, recommended_action="Bind the drawing to its PRT/ASM CAD document.") for row in rows]

    def _multiple_owner_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        if not (self._table_exists(conn, "cad_item_associations") and self._table_exists(conn, "cad_documents")):
            return []
        where = self._item_scope_where(conn)
        rows = conn.execute(
            f"""
            SELECT b.id,b.part_number,b.name,b.revision,b.type,
                   {self._lifecycle_expr(conn)} AS lifecycle_state,
                   COUNT(a.id) AS owner_count,
                   GROUP_CONCAT(d.file_name, ', ') AS owner_cad
            FROM bom b
            JOIN cad_item_associations a ON a.item_id=b.id AND a.active=1 AND upper(a.association_type)='OWNER'
            JOIN cad_documents d ON d.id=a.cad_document_id
            WHERE {where}
            GROUP BY b.id
            HAVING COUNT(a.id)>1
            ORDER BY owner_count DESC, lower(b.name)
            """,
            (project_id,),
        ).fetchall()
        return [self._item_dict(row, "Multiple OWNER CAD", "Keep exactly one owner CAD association unless policy explicitly allows another.") for row in rows]

    def _missing_metadata_rows(self, conn, project_id: int) -> list[dict[str, Any]]:
        where = self._item_scope_where(conn)
        missing = [
            "TRIM(COALESCE(b.name,''))=''",
            "TRIM(COALESCE(b.revision,''))=''",
            "TRIM(COALESCE(b.type,''))=''",
        ]
        if "item_type" in self._columns(conn, "bom"):
            missing.append("TRIM(COALESCE(b.item_type,''))=''")
        rows = conn.execute(
            self._base_item_select(conn)
            + f"""
            WHERE {where}
              AND ({' OR '.join(missing)})
            ORDER BY lower(COALESCE(b.part_number,'')), lower(b.name)
            """,
            (project_id,),
        ).fetchall()
        return [self._item_dict(row, "Missing mandatory metadata", "Complete name, type, item type and revision metadata.") for row in rows]

    def export_dashboard_csv(self, snapshot: DashboardSnapshot, destination_path: str) -> str:
        path = Path(destination_path)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Nexus Manager Dashboard"])
            writer.writerow(["Project", snapshot.project_name])
            writer.writerow(["Version", snapshot.project_version])
            writer.writerow(["Generated", snapshot.refreshed_at])
            writer.writerow([])
            writer.writerow(["Section", "Metric", "Value", "Percent", "Completed", "Total", "Status", "Definition"])
            for section in [
                DashboardSection("Executive", snapshot.executive_kpis),
                snapshot.manufacturing,
                snapshot.release,
                snapshot.quality,
                snapshot.checkout,
                snapshot.issue_health,
                snapshot.unsupported,
            ]:
                for kpi in section.kpis:
                    writer.writerow([
                        section.title,
                        kpi.title,
                        kpi.value,
                        kpi.percentage if kpi.percentage is not None else "",
                        kpi.completed_count if kpi.completed_count is not None else "",
                        kpi.total_count if kpi.total_count is not None else "",
                        kpi.status,
                        snapshot.definitions.get(kpi.key, kpi.description),
                    ])
            writer.writerow([])
            writer.writerow(["Risks"])
            writer.writerow(["Severity", "Title", "Affected", "Action"])
            for risk in snapshot.risks:
                writer.writerow([risk.severity, risk.title, risk.affected_count or "", risk.action])
        return str(path)
