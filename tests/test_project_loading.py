"""Startup and project-loading checks using only an isolated fixture database."""
import contextlib
import io
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, pyqtSignal
from PyQt5.QtWidgets import QApplication, QWidget

import config
from core.session_manager import SessionManager
from main3 import BomGUI, StartupWindow
from pages.bom_page import (
    BomPage, _InitialDiagWorker, _ProjectStructureWorker, _PROJECT_LOAD_THREADS,
    _start_project_worker,
)
from pages.login_page import LoginPage
from setup.migrations import migrate


class ProjectLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait_until(self, predicate, timeout=5):
        until = time.monotonic() + timeout
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "Timed out waiting for background work")

    def test_shell_readiness_does_not_wait_for_a_bom_result(self):
        shell = SimpleNamespace(session=SimpleNamespace(project_id=12))
        startup = SimpleNamespace(
            _set_loading_status=Mock(), _mark_page_ready=Mock(), main_page=None,
        )
        with patch("main3.BomGUI", return_value=shell):
            StartupWindow._build_main_window(startup)
        self.assertIs(startup.main_page, shell)
        startup._mark_page_ready.assert_called_once()

    def test_first_login_does_not_load_is_current_project(self):
        page = SimpleNamespace(user_repo=Mock())
        page.user_repo.get_last_project_id.return_value = None
        with patch("pages.login_page.os.path.isdir", return_value=True):
            self.assertIsNone(LoginPage._choose_project(page, 7, [
                {"id": 12, "is_current": 1, "working_directory": "fixture"},
            ]))

    def test_login_resumes_only_the_explicit_saved_version(self):
        page = SimpleNamespace(user_repo=Mock())
        page.user_repo.get_last_project_id.return_value = 13
        with patch("pages.login_page.os.path.isdir", return_value=True):
            self.assertEqual(LoginPage._choose_project(page, 7, [
                {"id": 12, "is_current": 1, "working_directory": "fixtureA"},
                {"id": 13, "working_directory": "fixtureB"},
            ]), 13)

    def test_slow_structure_query_keeps_gui_timer_running_and_captures_project(self):
        session = SimpleNamespace(project_id=12, user_id=7)
        entered, release = threading.Event(), threading.Event()
        seen = []

        class Service:
            def __init__(self):
                self.session = session

            def get_released_ebom_project(self, pid):
                entered.set()
                release.wait(5)
                seen.append((pid, self.session.project_id))
                return {"roots": [{"id": 99}]}

            def list_pdm_cad_documents(self, pid, **kwargs):
                seen.append(("documents", pid))
                return []

            def list_bom_folders(self):
                seen.append(("folders", self.session.project_id))
                return []

            def list_product_variants(self):
                return []

        worker = _ProjectStructureWorker(1, 12, "ebom", Service(), Mock(session=session))
        result, failures, ticks = [], [], []
        worker.finished.connect(lambda *args: result.append(args))
        worker.failed.connect(lambda *args: failures.append(args))
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        thread = _start_project_worker(worker)
        try:
            self.wait_until(entered.is_set)
            session.project_id = 13
            self.wait_until(lambda: len(ticks) >= 3)
            self.assertFalse(result)
        finally:
            timer.stop()
            release.set()
            self.wait_until(lambda: thread not in _PROJECT_LOAD_THREADS)
        self.assertFalse(failures)
        self.assertEqual(result[0][:3], (1, 12, "ebom"))
        self.assertEqual(seen, [(12, 12), ("documents", 12), ("folders", 12)])

    def test_cancelled_worker_always_finishes_thread_without_delivering_result(self):
        session = SimpleNamespace(project_id=12, user_id=7)
        worker = _ProjectStructureWorker(1, 12, "ebom", Mock(session=session), Mock())
        result = Mock()
        worker.finished.connect(result)
        worker.cancel()
        thread = _start_project_worker(worker)
        self.wait_until(lambda: thread not in _PROJECT_LOAD_THREADS)
        result.assert_not_called()

    def test_stale_and_cancelled_structure_results_are_ignored(self):
        page = SimpleNamespace(
            _project_load_cancelled=False, _structure_load_seq=3,
            session=SimpleNamespace(project_id=13),
        )
        self.assertFalse(BomPage._structure_result_is_current(page, 2, 13))
        self.assertFalse(BomPage._structure_result_is_current(page, 3, 12))
        self.assertTrue(BomPage._structure_result_is_current(page, 3, 13))
        page._project_load_cancelled = True
        self.assertFalse(BomPage._structure_result_is_current(page, 3, 13))

    def test_diagnostics_keep_their_original_project_and_emit_done_when_cancelled(self):
        session = SimpleNamespace(project_id=12, user_id=7)
        service = Mock(session=session)
        with patch("pages.bom_page.DiagService", return_value=service):
            worker = _InitialDiagWorker("fixture")
        session.project_id = 13
        self.assertEqual(worker._service.session.project_id, 12)
        done, finished = Mock(), Mock()
        worker.done.connect(done)
        worker.finished.connect(finished)
        worker.cancel()
        worker.run()
        done.assert_called_once()
        finished.assert_not_called()


class ProjectShellIntegrationTests(unittest.TestCase):
    """Exercise the real toolbar, page construction, signals, and worker UI."""

    wait_until = ProjectLoadingTests.wait_until

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nexus-project-loading-")
        self.root = Path(self.temp.name)
        self.previous_database = str(config.DB_NAME)
        self.session = SessionManager()
        self.previous_session = vars(self.session).copy()
        config.DB_NAME.set(str(self.root / "fixture.db"))
        with contextlib.redirect_stdout(io.StringIO()):
            migrate()
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.execute("INSERT INTO users(id,username,email,password,is_admin) VALUES(7,'fixture','fixture@example.test','unused',1)")
            for pid, label in [(12, "A"), (13, "B")]:
                working = self.root / label
                working.mkdir()
                conn.execute(
                    "INSERT INTO projects(id,name,working_directory,root_project_id,version_label) VALUES(?,?,?,?,?)",
                    (pid, "Fixture", str(working), 12, label),
                )
                conn.execute("INSERT INTO user_projects(user_id,project_id) VALUES(7,?)", (pid,))
        self.session.start_session(7, "fixture", None, is_admin=True)
        self.window = None
        self.patches = [
            patch.object(BomGUI, "_start_deferred_services"),
            patch.object(BomGUI, "_check_version_notification"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        if self.window is not None:
            self.window.bom_page._cancel_background_work()
        self.wait_until(lambda: not _PROJECT_LOAD_THREADS)
        if self.window is not None:
            self.window.close()
            self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()
        vars(self.session).clear()
        vars(self.session).update(self.previous_session)
        config.DB_NAME.set(self.previous_database)
        self.temp.cleanup()

    def test_select_project_and_version_then_load_exactly_once(self):
        self.window = BomGUI()
        self.assertIsNone(self.session.project_id)
        with patch.object(self.window, "reload_main_window") as reload:
            self.window.project_combo.setCurrentIndex(self.window.project_combo.findData(12))
            self.window.project_version_combo.setCurrentIndex(self.window.project_version_combo.findData(13))
            self.assertIsNone(self.session.project_id)
            reload.assert_not_called()
            self.assertTrue(self.window.load_project_button.isEnabled())
            self.window.load_project_button.click()
            self.assertEqual(self.session.project_id, 13)
            reload.assert_called_once()
            self.assertFalse(self.window.load_project_button.isEnabled())
        self.assertFalse(self.window._commit_loaded)
        self.assertFalse(self.window._issue_loaded)
        self.assertFalse(self.window._dashboard_loaded)

    def test_hidden_page_starts_no_queries_then_visible_page_shows_loader(self):
        self.session.update_project(13)
        self.window = BomGUI()
        page = self.window.bom_page
        started, release = threading.Event(), threading.Event()

        def slow_ebom(_pid):
            started.set()
            release.wait(5)
            return {"roots": []}

        with patch.object(page.bom_service, "get_released_ebom_project", side_effect=slow_ebom) as query, \
                patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]):
            self.app.processEvents()
            query.assert_not_called()
            self.assertFalse(page._initial_load_scheduled)
            self.window.show()
            try:
                self.wait_until(started.is_set)
                self.assertIs(page._tree_stack.currentWidget(), page._tree_loading_widget)
                self.assertFalse(page.bom_mode_selector.isEnabled())
                self.assertFalse(page.isEnabled())
                self.assertTrue(self.window.project_version_combo.isEnabled())
                self.assertTrue(self.window.isVisible())
            finally:
                release.set()
                self.wait_until(lambda: page.initial_tree_is_ready)
                self.wait_until(lambda: not _PROJECT_LOAD_THREADS)
            self.assertIs(page._tree_stack.currentWidget(), page._ebom_tree)
            self.assertTrue(page.bom_mode_selector.isEnabled())
            self.assertTrue(page.isEnabled())
            self.assertEqual(query.call_args.args, (13,))

    def test_structure_failure_stops_loader_and_shows_retry_message(self):
        self.session.update_project(13)
        self.window = BomGUI()
        page = self.window.bom_page
        with patch.object(page.bom_service, "get_released_ebom_project", side_effect=OSError("share unavailable")), \
                patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]), \
                patch.object(page, "show_alert") as alert:
            self.window.show()
            self.wait_until(lambda: page.initial_tree_is_ready)
            self.assertFalse(page._structure_loading)
            self.assertTrue(page.bom_mode_selector.isEnabled())
            self.assertTrue(any("Refresh to retry" in call.args[0] for call in alert.call_args_list))

    def test_real_item_rows_and_cad_mode_load_from_the_selected_project(self):
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.execute("INSERT INTO bom(id,name,type,project_id,status,created,modified) VALUES(101,'Fixture Item','prt',13,'Released',datetime('now'),datetime('now'))")
        self.session.update_project(13)
        self.window = BomGUI()
        page = self.window.bom_page
        with patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]):
            self.window.show()
            self.wait_until(lambda: page.initial_tree_is_ready)
            self.assertIsNone(page._structure_error)
            self.assertEqual(page._ebom_tree.topLevelItemCount(), 1)
            page.bom_mode_selector.setCurrentIndex(page.bom_mode_selector.findData("cad"))
            self.wait_until(lambda: not page._structure_loading)
            self.assertIsNone(page._structure_error)
            self.assertIs(page._tree_stack.currentWidget(), page._cad_tree)

    def test_switch_while_query_is_running_discards_the_old_project_result(self):
        self.session.update_project(12)
        self.window = BomGUI()
        old_page = self.window.bom_page
        entered, release = threading.Event(), threading.Event()

        def slow_ebom(pid):
            if pid == 12:
                entered.set()
                release.wait(5)
                return {"roots": [{"id": 101, "bom_id": 101, "name": "Old Item"}]}
            return {"roots": []}

        with patch.object(self.window.bom_service, "get_released_ebom_project", side_effect=slow_ebom), \
                patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]):
            self.window.show()
            try:
                self.wait_until(entered.is_set)
                self.window.project_version_combo.setCurrentIndex(self.window.project_version_combo.findData(13))
                self.window.load_project_button.click()
                new_page = self.window.bom_page
                self.assertIsNot(new_page, old_page)
                self.assertTrue(old_page._project_load_cancelled)
                self.wait_until(lambda: new_page.initial_tree_is_ready)
                self.assertEqual(self.session.project_id, 13)
            finally:
                release.set()
                self.wait_until(lambda: not _PROJECT_LOAD_THREADS)
            self.assertEqual(new_page._ebom_tree.topLevelItemCount(), 0)

    def test_load_failure_can_be_retried_without_restarting_the_app(self):
        self.session.update_project(13)
        self.window = BomGUI()
        page = self.window.bom_page
        with patch.object(page.bom_service, "get_released_ebom_project", side_effect=OSError("share unavailable")), \
                patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]):
            self.window.show()
            self.wait_until(lambda: page.initial_tree_is_ready)
            self.assertEqual(page._structure_error, "share unavailable")
        with patch.object(page.bom_service, "get_released_ebom_project", return_value={"roots": []}):
            page._load_released_ebom_tree()
            self.wait_until(lambda: not page._structure_loading)
            self.assertIsNone(page._structure_error)
            self.assertIs(page._tree_stack.currentWidget(), page._ebom_tree)

    def test_secondary_modules_load_once_on_navigation_and_keep_issue_signals(self):
        class Page(QWidget):
            issue_changed = pyqtSignal(object)

        commit, issue, dashboard = Page(), Page(), Page()
        commit._refresh_resolved_issues = Mock()
        dashboard.refresh = Mock()
        with patch("pages.commit_page.CommitPage", return_value=commit) as make_commit, \
                patch("pages.issue_page.EngineeringIssuePage", return_value=issue) as make_issue, \
                patch("pages.dashboard_page.ManagerDashboardPage", return_value=dashboard) as make_dashboard:
            self.window = BomGUI()
            make_commit.assert_not_called()
            make_issue.assert_not_called()
            make_dashboard.assert_not_called()
            self.window.switch_page(1)
            self.window.switch_page(0)
            self.window.switch_page(1)
            make_commit.assert_called_once()
            self.assertIs(self.window.pages.currentWidget(), commit)
            self.window.switch_page(2)
            issue.issue_changed.emit([])
            commit._refresh_resolved_issues.assert_called_once()
            make_dashboard.assert_not_called()
            self.window.switch_page(6)
            issue.issue_changed.emit([])
            dashboard.refresh.assert_called_once()
            self.assertIs(self.window.pages.currentWidget(), dashboard)

    def test_close_cancels_loading_and_waits_without_destroying_a_running_thread(self):
        self.session.update_project(13)
        self.window = BomGUI()
        entered, release = threading.Event(), threading.Event()

        def slow_ebom(pid):
            entered.set()
            release.wait(5)
            return {"roots": []}

        with patch.object(self.window.bom_service, "get_released_ebom_project", side_effect=slow_ebom), \
                patch("pages.bom_page.DiagService.sync_bom_files", return_value=[]), \
                patch("pages.bom_page.DiagService.check_working_directory", return_value=[]):
            self.window.show()
            try:
                self.wait_until(entered.is_set)
                self.assertFalse(self.window.close())
                self.assertTrue(self.window.bom_page._project_load_cancelled)
                self.assertTrue(self.window.isVisible())
            finally:
                release.set()
                self.wait_until(lambda: not _PROJECT_LOAD_THREADS)
            self.wait_until(lambda: not self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
