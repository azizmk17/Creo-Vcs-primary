from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models.dashboard_model import DashboardSnapshot, KpiResult, RiskItem
from core.services.dashboard_service import DashboardService
from core.session_manager import SessionManager


class _DashboardWorker(QObject):
    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, sequence: int, service: DashboardService, project_id: int | None, filters: dict | None):
        super().__init__()
        self.sequence = sequence
        self.service = service
        self.project_id = project_id
        self.filters = filters or {}

    def run(self):
        try:
            self.finished.emit(self.sequence, self.service.get_dashboard(self.project_id, self.filters))
        except Exception as exc:
            self.failed.emit(self.sequence, str(exc))


class KpiCard(QFrame):
    clicked = pyqtSignal(str)

    STATUS_COLORS = {
        "green": ("#168044", "#e8f5ee", "ON TRACK"),
        "amber": ("#b56a00", "#fff2da", "ATTENTION"),
        "red": ("#b42318", "#fde8e7", "BLOCKED"),
        "neutral": ("#496174", "#eef3f6", "TRACKED"),
    }

    def __init__(self, kpi: KpiResult, compact: bool = False, parent=None):
        super().__init__(parent)
        self.kpi = kpi
        self.setObjectName("dashboardKpiCard")
        self.setMinimumHeight(98 if compact else 122)
        self.setCursor(Qt.PointingHandCursor if kpi.drilldown_available else Qt.ArrowCursor)
        accent, _bg, badge = self.STATUS_COLORS.get(kpi.status or "neutral", self.STATUS_COLORS["neutral"])
        self.setStyleSheet(
            f"""
            QFrame#dashboardKpiCard {{
                background: #ffffff;
                border: 1px solid #b7c4cf;
                border-top: 3px solid {accent};
                border-radius: 3px;
            }}
            QLabel#kpiTitle {{ color: #24384a; font: 700 8pt "Segoe UI"; }}
            QLabel#kpiValue {{ color: {accent}; font: 700 {'16' if compact else '23'}pt "Segoe UI"; }}
            QLabel#kpiMeta {{ color: #5c6f80; font: 8pt "Segoe UI"; }}
            QLabel#kpiBadge {{
                color: {accent};
                background: rgba(255,255,255,0);
                border: 1px solid {accent};
                border-radius: 2px;
                padding: 1px 5px;
                font: 700 7pt "Segoe UI";
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 9)
        layout.setSpacing(5)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title = QLabel(kpi.title)
        title.setObjectName("kpiTitle")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        status = QLabel(badge)
        status.setObjectName("kpiBadge")
        status.setAlignment(Qt.AlignCenter)
        title_row.addWidget(status, 0, Qt.AlignTop)
        layout.addLayout(title_row)

        value = QLabel(self._format_value(kpi))
        value.setObjectName("kpiValue")
        value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(value)

        if kpi.percentage is not None:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, int(round(kpi.percentage)))))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(
                f"QProgressBar {{ background:#dfe6eb; border:0; border-radius:3px; }} "
                f"QProgressBar::chunk {{ background:{accent}; border-radius:3px; }}"
            )
            layout.addWidget(bar)

        meta_text = self._meta_text(kpi)
        if meta_text:
            meta = QLabel(meta_text)
            meta.setObjectName("kpiMeta")
            meta.setWordWrap(True)
            layout.addWidget(meta)

        layout.addStretch(1)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.kpi.drilldown_available:
            self.clicked.emit(self.kpi.key)
        super().mousePressEvent(event)

    @staticmethod
    def _format_value(kpi: KpiResult) -> str:
        if kpi.percentage is not None:
            return f"{float(kpi.percentage):.1f}%"
        return str(kpi.value)

    @staticmethod
    def _meta_text(kpi: KpiResult) -> str:
        if kpi.unavailable_reason:
            return kpi.unavailable_reason
        if kpi.completed_count is not None and kpi.total_count is not None:
            return f"{kpi.completed_count} / {kpi.total_count}"
        if kpi.description:
            return kpi.description
        return ""


class DashboardDrilldownDialog(QDialog):
    def __init__(self, title: str, rows: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(980, 620)
        self._all_rows = rows or []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter drilldown rows...")
        self.search.textChanged.connect(self._populate)
        header.addWidget(self.search, 1)

        export_btn = QPushButton("Export CSV")
        export_btn.clicked.connect(self._export)
        header.addWidget(export_btn)
        layout.addLayout(header)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        close_row.addWidget(close)
        layout.addLayout(close_row)

        self._populate()

    def _columns(self) -> list[str]:
        priority = [
            "issue",
            "recommended_action",
            "part_number",
            "name",
            "revision",
            "type",
            "lifecycle_state",
            "file_type",
            "cad_file",
            "association",
            "status",
            "priority",
            "assigned_to",
            "due_date",
            "age_days",
        ]
        keys = []
        for row in self._all_rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        ordered = [key for key in priority if key in keys]
        ordered.extend(key for key in keys if key not in ordered)
        return ordered or ["message"]

    def _filtered_rows(self) -> list[dict[str, Any]]:
        needle = self.search.text().strip().lower()
        if not needle:
            return self._all_rows
        return [
            row for row in self._all_rows
            if needle in " ".join(str(value or "") for value in row.values()).lower()
        ]

    def _populate(self):
        rows = self._filtered_rows()
        columns = self._columns()
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels([col.replace("_", " ").title() for col in columns])
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(columns):
                item = QTableWidgetItem(str(row.get(key, "") or ""))
                if str(row.get("severity", "")).lower() in {"critical", "red"}:
                    item.setForeground(QBrush(QColor("#b42318")))
                elif str(row.get("severity", "")).lower() in {"warning", "amber"}:
                    item.setForeground(QBrush(QColor("#b46a00")))
                self.table.setItem(r, c, item)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Drilldown", "", "CSV Files (*.csv)")
        if not path:
            return
        columns = self._columns()
        try:
            with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                for row in self._filtered_rows():
                    writer.writerow([row.get(key, "") for key in columns])
            QMessageBox.information(self, "Export", "Drilldown exported.")
        except Exception as exc:
            QMessageBox.critical(self, "Export", f"Could not export drilldown:\n{exc}")


class ManagerDashboardPage(QWidget):
    """Professional manager dashboard for project readiness and controlled risk."""

    def __init__(self, service: DashboardService | None = None, parent=None):
        super().__init__(parent)
        self.session = SessionManager()
        self.service = service or DashboardService()
        self._snapshot: DashboardSnapshot | None = None
        self._thread: QThread | None = None
        self._worker: _DashboardWorker | None = None
        self._load_sequence = 0
        self._building = False
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        self.setObjectName("managerDashboardPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 7)
        outer.setSpacing(6)

        command = QFrame()
        command.setObjectName("dashboardCommandBar")
        command_layout = QHBoxLayout(command)
        command_layout.setContentsMargins(12, 8, 10, 8)
        command_layout.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        self.title_label = QLabel("Program Readiness Dashboard")
        self.title_label.setObjectName("dashboardTitle")
        self.context_label = QLabel("Project readiness, risks and release health")
        self.context_label.setObjectName("dashboardContext")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.context_label)
        command_layout.addLayout(title_box, 1)

        range_label = QLabel("DATE RANGE")
        range_label.setObjectName("dashboardCommandLabel")
        command_layout.addWidget(range_label)
        self.range_combo = QComboBox()
        self.range_combo.setObjectName("dashboardRangeCombo")
        self.range_combo.addItems(["All time", "Last 7 days", "Last 30 days", "Last 90 days"])
        self.range_combo.currentIndexChanged.connect(lambda _=None: self.refresh())
        command_layout.addWidget(self.range_combo)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("secondaryDashboardButton")
        self.refresh_btn.clicked.connect(self.refresh)
        command_layout.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.setObjectName("primaryDashboardButton")
        self.export_btn.clicked.connect(self.export_dashboard)
        command_layout.addWidget(self.export_btn)
        outer.addWidget(command)

        self.loading_label = QLabel("")
        self.loading_label.setObjectName("dashboardLoading")
        outer.addWidget(self.loading_label)

        scroll = QScrollArea()
        scroll.setObjectName("dashboardScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.content.setObjectName("dashboardCanvas")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        scroll.setWidget(self.content)
        outer.addWidget(scroll, 1)

        self.setStyleSheet(
            """
            QWidget#managerDashboardPage { background:#e8eef2; }
            QScrollArea#dashboardScroll { background:#e8eef2; border:0; }
            QWidget#dashboardCanvas { background:#e8eef2; }
            QFrame#dashboardCommandBar {
                background:#ffffff;
                border:1px solid #aebbc6;
                border-left:4px solid #11699b;
                border-radius:2px;
            }
            QFrame#dashboardPanel {
                background:#ffffff;
                border:1px solid #b9c6d0;
                border-radius:2px;
            }
            QLabel#dashboardTitle { color:#0e2538; font:700 14pt "Segoe UI"; }
            QLabel#dashboardContext { color:#38546b; font:8pt "Segoe UI"; }
            QLabel#dashboardLoading { color:#607486; font:8pt "Segoe UI"; padding-left:2px; }
            QLabel#dashboardCommandLabel { color:#31495d; font:700 7pt "Segoe UI"; }
            QLabel#sectionTitle { color:#102b42; font:700 10pt "Segoe UI"; }
            QLabel#sectionSubtitle { color:#617386; font:8pt "Segoe UI"; }
            QFrame#dashboardReadinessStrip {
                background:#102b42;
                border:1px solid #0a1d2c;
                border-radius:2px;
            }
            QLabel#stripCaption {
                color:#a9c0d2;
                font:700 7pt "Segoe UI";
                letter-spacing:1px;
            }
            QLabel#stripScore {
                color:#ffffff;
                font:700 24pt "Segoe UI";
            }
            QLabel#stripValue {
                color:#ffffff;
                font:700 14pt "Segoe UI";
            }
            QLabel#stripMeta {
                color:#c6d5df;
                font:8pt "Segoe UI";
            }
            QComboBox#dashboardRangeCombo {
                min-height:24px;
                min-width:98px;
                background:#f8fafb;
                border:1px solid #aebbc6;
                padding-left:6px;
            }
            QPushButton#primaryDashboardButton {
                min-height:25px;
                background:#175d86;
                color:#ffffff;
                border:1px solid #114964;
                padding:2px 12px;
                font:700 8pt "Segoe UI";
            }
            QPushButton#secondaryDashboardButton {
                min-height:25px;
                background:#f7f9fb;
                color:#17324a;
                border:1px solid #aebbc6;
                padding:2px 12px;
            }
            QTableWidget {
                background:#ffffff;
                alternate-background-color:#f6f8fa;
                gridline-color:#d8e0e6;
                font:8pt "Segoe UI";
                border:1px solid #c4d0d8;
            }
            QHeaderView::section {
                background:#d8e2ea;
                color:#18324a;
                padding:4px;
                border:1px solid #c1cdd6;
                font-weight:700;
            }
            QPushButton { min-height:24px; padding:2px 10px; }
            """
        )

    def refresh(self):
        if self._building:
            return
        self._load_sequence += 1
        sequence = self._load_sequence
        self.loading_label.setText("Loading dashboard metrics in background...")
        self.refresh_btn.setEnabled(False)
        filters = {"date_range": self.range_combo.currentText()}
        project_id = getattr(self.session, "project_id", None)

        thread = QThread(self)
        worker = _DashboardWorker(sequence, self.service, project_id, filters)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_loaded(self, sequence: int, snapshot: DashboardSnapshot):
        if sequence != self._load_sequence:
            return
        self._snapshot = snapshot
        self.refresh_btn.setEnabled(True)
        self.loading_label.setText(f"Updated {snapshot.refreshed_at}")
        self.context_label.setText(
            f"{snapshot.project_name}  |  Version {snapshot.project_version}  |  {snapshot.phase}"
        )
        self._render_snapshot(snapshot)

    def _on_failed(self, sequence: int, message: str):
        if sequence != self._load_sequence:
            return
        self.refresh_btn.setEnabled(True)
        self.loading_label.setText("Dashboard could not load.")
        QMessageBox.critical(self, "Manager Dashboard", message)

    def _clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_snapshot(self, snapshot: DashboardSnapshot):
        self._building = True
        try:
            self._clear_content()
            self.content_layout.addWidget(self._executive_panel(snapshot))
            self.content_layout.addWidget(self._kpi_section(snapshot.manufacturing.title, snapshot.manufacturing.kpis, columns=4))

            two_col = QHBoxLayout()
            two_col.addWidget(self._kpi_section(snapshot.release.title, snapshot.release.kpis, columns=2), 1)
            two_col.addWidget(self._kpi_section(snapshot.quality.title, snapshot.quality.kpis, columns=2), 1)
            row_widget = QWidget()
            row_widget.setLayout(two_col)
            self.content_layout.addWidget(row_widget)

            two_col_2 = QHBoxLayout()
            two_col_2.addWidget(self._kpi_section(snapshot.checkout.title, snapshot.checkout.kpis, columns=2), 1)
            two_col_2.addWidget(self._kpi_section(snapshot.issue_health.title, snapshot.issue_health.kpis, columns=2), 1)
            row_widget_2 = QWidget()
            row_widget_2.setLayout(two_col_2)
            self.content_layout.addWidget(row_widget_2)

            self.content_layout.addWidget(self._risk_panel("Management Attention", snapshot.risks))
            self.content_layout.addWidget(self._risk_panel("Release Blockers", snapshot.release_blockers))
            self.content_layout.addWidget(self._workload_panel(snapshot))
            self.content_layout.addWidget(self._activity_panel(snapshot))
            self.content_layout.addWidget(self._kpi_section(snapshot.unsupported.title, snapshot.unsupported.kpis, columns=3))
            self.content_layout.addStretch(1)
        finally:
            self._building = False

    def _panel(self, title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("dashboardPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(7)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(label)
        header.addStretch(1)
        layout.addLayout(header)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("sectionSubtitle")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return panel, layout

    def _executive_panel(self, snapshot: DashboardSnapshot) -> QWidget:
        panel, layout = self._panel(
            "Executive Summary",
            "Release decision indicators calculated from controlled Nexus data. Click a card to inspect the affected objects.",
        )
        strip = QFrame()
        strip.setObjectName("dashboardReadinessStrip")
        strip_layout = QHBoxLayout(strip)
        strip_layout.setContentsMargins(12, 8, 12, 8)
        strip_layout.setSpacing(18)

        health = next((k for k in snapshot.executive_kpis if k.key == "project_health"), None)
        readiness = next((k for k in snapshot.executive_kpis if k.key == "release_readiness"), None)
        manufacturing = next((k for k in snapshot.executive_kpis if k.key == "manufacturing_readiness"), None)
        risks = next((k for k in snapshot.executive_kpis if k.key == "critical_risks"), None)

        score_box = QVBoxLayout()
        score_box.setSpacing(1)
        score_title = QLabel("CONTROL SCORE")
        score_title.setObjectName("stripCaption")
        score_value = QLabel(KpiCard._format_value(health) if health else "-")
        score_value.setObjectName("stripScore")
        score_meta = QLabel("Weighted project health")
        score_meta.setObjectName("stripMeta")
        score_box.addWidget(score_title)
        score_box.addWidget(score_value)
        score_box.addWidget(score_meta)
        strip_layout.addLayout(score_box)

        for label, kpi in [
            ("Release", readiness),
            ("Manufacturing", manufacturing),
            ("Critical risks", risks),
        ]:
            block = QVBoxLayout()
            block.setSpacing(1)
            caption = QLabel(label.upper())
            caption.setObjectName("stripCaption")
            value = QLabel(KpiCard._format_value(kpi) if kpi else "-")
            value.setObjectName("stripValue")
            meta = QLabel(KpiCard._meta_text(kpi) if kpi else "")
            meta.setObjectName("stripMeta")
            block.addWidget(caption)
            block.addWidget(value)
            block.addWidget(meta)
            strip_layout.addLayout(block)

        strip_layout.addStretch(1)
        layout.addWidget(strip)

        grid = QGridLayout()
        grid.setSpacing(8)
        for index, kpi in enumerate(snapshot.executive_kpis or []):
            card = KpiCard(kpi, compact=False)
            card.clicked.connect(self.open_drilldown)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        layout.addLayout(grid)
        return panel

    def _kpi_section(self, title: str, kpis: list[KpiResult], columns: int = 3, compact: bool = True) -> QWidget:
        panel, layout = self._panel(title)
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, kpi in enumerate(kpis or []):
            card = KpiCard(kpi, compact=compact)
            card.clicked.connect(self.open_drilldown)
            grid.addWidget(card, index // columns, index % columns)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        layout.addLayout(grid)
        return panel

    def _risk_panel(self, title: str, risks: list[RiskItem]) -> QWidget:
        panel, layout = self._panel(title, "Click a KPI card for the object-level list behind each number.")
        if not risks:
            empty = QLabel("No active risks in this group.")
            empty.setObjectName("sectionSubtitle")
            layout.addWidget(empty)
            return panel
        table = QTableWidget(len(risks), 4)
        table.setHorizontalHeaderLabels(["Severity", "Risk", "Affected", "Recommended Action"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, risk in enumerate(risks):
            values = [risk.severity.upper(), risk.title, str(risk.affected_count or ""), risk.action]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if risk.severity.lower() == "critical":
                    item.setForeground(QBrush(QColor("#b42318")))
                elif risk.severity.lower() == "warning":
                    item.setForeground(QBrush(QColor("#b46a00")))
                table.setItem(r, c, item)
        table.setMinimumHeight(min(178, 32 + len(risks) * 24))
        layout.addWidget(table)
        return panel

    def _workload_panel(self, snapshot: DashboardSnapshot) -> QWidget:
        panel, layout = self._panel("Engineering Workload", "Open work by responsible engineer, checkout ownership and issue ownership.")
        rows = snapshot.workload or []
        table = QTableWidget(len(rows), 6)
        table.setHorizontalHeaderLabels(["Engineer", "In Work", "Reviews", "Issues", "Overdue", "Stale Checkouts"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, row in enumerate(rows):
            values = [row.engineer, row.in_work, row.reviews, row.issues, row.overdue, row.stale_checkouts]
            for c, value in enumerate(values):
                table.setItem(r, c, QTableWidgetItem(str(value)))
        table.setMinimumHeight(max(86, min(210, 32 + len(rows) * 24)))
        layout.addWidget(table)
        return panel

    def _activity_panel(self, snapshot: DashboardSnapshot) -> QWidget:
        panel, layout = self._panel("Recent Activity", "Latest commits, issue changes and checkout activity from Nexus history.")
        rows = snapshot.recent_activity or []
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["When", "Type", "Title", "Actor"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, row in enumerate(rows):
            values = [row.get("when", ""), row.get("type", ""), row.get("title", ""), row.get("actor", "")]
            for c, value in enumerate(values):
                table.setItem(r, c, QTableWidgetItem(str(value or "")))
        table.setMinimumHeight(max(86, min(210, 32 + len(rows) * 24)))
        layout.addWidget(table)
        return panel

    def open_drilldown(self, metric_key: str):
        if not metric_key or not self._snapshot:
            return
        try:
            rows = self.service.get_drilldown_items(
                metric_key,
                project_id=self._snapshot.project_id,
                filters={"date_range": self.range_combo.currentText()},
            )
            definitions = self._snapshot.definitions or {}
            dialog = DashboardDrilldownDialog(
                definitions.get(metric_key, metric_key.replace("_", " ").title()),
                rows,
                self,
            )
            dialog.exec_()
        except Exception as exc:
            QMessageBox.critical(self, "Dashboard Drilldown", str(exc))

    def export_dashboard(self):
        if not self._snapshot:
            QMessageBox.information(self, "Manager Dashboard", "Dashboard is still loading.")
            return
        default_name = f"nexus_manager_dashboard_{self._snapshot.project_name}_{self._snapshot.project_version}.csv"
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in default_name)
        path, _ = QFileDialog.getSaveFileName(self, "Export Manager Dashboard", safe_name, "CSV Files (*.csv)")
        if not path:
            return
        try:
            self.service.export_dashboard_csv(self._snapshot, path)
            QMessageBox.information(self, "Manager Dashboard", "Dashboard exported.")
        except Exception as exc:
            QMessageBox.critical(self, "Manager Dashboard", f"Could not export dashboard:\n{exc}")
