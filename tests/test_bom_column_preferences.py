import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.repositories.user_repository import UserRepository


class BomColumnRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "preferences.db"
        self.repo = UserRepository(self.db)

    def test_profile_survives_new_repository_and_is_separate_for_each_user(self):
        self.repo.set_bom_column_preferences(1, {"visible_extra_columns": ["material"]})
        self.repo.set_bom_column_preferences(2, {"visible_extra_columns": ["weight"]})
        reopened = UserRepository(self.db)
        self.assertEqual(reopened.get_bom_column_preferences(1)["visible_extra_columns"], ["material"])
        self.assertEqual(reopened.get_bom_column_preferences(2)["visible_extra_columns"], ["weight"])

    def test_existing_user_settings_are_preserved_when_schema_is_extended(self):
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE user_settings(user_id INTEGER PRIMARY KEY,last_project_id INTEGER,cli_enabled INTEGER,updated_at TEXT);
                INSERT INTO user_settings VALUES(1,7,1,'existing');
            """)
        conn.close()
        self.repo.set_bom_column_preferences(1, {"visible_extra_columns": []})
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT last_project_id,cli_enabled FROM user_settings WHERE user_id=1").fetchone()
        conn.close()
        self.assertEqual(row, (7, 1))
        self.assertEqual(self.repo.get_bom_column_preferences(1)["visible_extra_columns"], [])

    def test_corrupt_saved_json_falls_back_to_defaults(self):
        self.repo.set_bom_column_preferences(1, {})
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE user_settings SET bom_column_preferences_json='not-json' WHERE user_id=1")
        conn.close()
        self.assertEqual(self.repo.get_bom_column_preferences(1), {})


os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
try:
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtWidgets import QApplication, QWidget, QTreeWidget, QListWidget, QDialogButtonBox
except ImportError:
    QT_AVAILABLE = False
else:
    from pages.bom_page import BomPage, BOM_COL_NAME, BOM_COL_EXTRA_START, BOM_EXTRA_COLUMN_SPECS
    QT_AVAILABLE = True


@unittest.skipUnless(QT_AVAILABLE, "PyQt5 is required for saved-column tests")
class BomColumnUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "preferences.db"
        self.repo = UserRepository(self.db)
        self.settings_patch = patch("pages.bom_page.QSettings", return_value=Mock(value=Mock(return_value="")))
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def page(self, user_id=1):
        page = _ColumnHarness(self.repo, user_id)
        self.addCleanup(page.close)
        return page

    def column(self, key):
        return BOM_COL_EXTRA_START + next(index for index, spec in enumerate(BOM_EXTRA_COLUMN_SPECS) if spec[0] == key)

    def test_saved_visibility_width_and_order_restore_in_new_session(self):
        page = self.page()
        header = page._ebom_tree.header()
        header.resizeSection(BOM_COL_NAME, 389)
        header.moveSection(header.visualIndex(self.column("material")), 2)
        page._save_personal_bom_columns(page._ebom_tree, {"material"})
        reopened = self.page()
        tree = reopened._ebom_tree
        self.assertEqual(tree.columnWidth(BOM_COL_NAME), 389)
        self.assertEqual(tree.header().visualIndex(self.column("material")), 2)
        self.assertFalse(tree.isColumnHidden(self.column("material")))
        self.assertTrue(tree.isColumnHidden(self.column("cad_revision")))
        self.assertEqual(reopened._ebom_filter_tree.columnWidth(BOM_COL_NAME), 389)

    def test_switching_users_restores_their_own_layout_and_defaults(self):
        page = self.page()
        default_width = page._ebom_tree.columnWidth(BOM_COL_NAME)
        page._ebom_tree.setColumnWidth(BOM_COL_NAME, 390)
        page._save_personal_bom_columns(page._ebom_tree, {"material"})
        page.session.user_id = 2
        page._restore_saved_bom_columns()
        self.assertEqual(page._ebom_tree.columnWidth(BOM_COL_NAME), default_width)
        page._ebom_tree.setColumnWidth(BOM_COL_NAME, 420)
        page._save_personal_bom_columns(page._ebom_tree, {"weight"})
        page.session.user_id = 1
        page._restore_saved_bom_columns()
        self.assertEqual(page._ebom_tree.columnWidth(BOM_COL_NAME), 390)
        self.assertEqual(page._visible_bom_column_keys(), {"material"})

    def test_all_optional_columns_can_remain_hidden_after_reopen(self):
        page = self.page()
        page._save_personal_bom_columns(page._ebom_tree, set())
        reopened = self.page()
        self.assertEqual(reopened._visible_bom_column_keys(), set())
        for offset in range(len(BOM_EXTRA_COLUMN_SPECS)):
            self.assertTrue(reopened._ebom_tree.isColumnHidden(BOM_COL_EXTRA_START + offset))

    def test_cad_layout_is_saved_separately_from_ebom(self):
        page = self.page()
        page._cad_tree.setColumnWidth(1, 345)
        page._save_personal_bom_columns(page._cad_tree, {1})
        page._ebom_tree.setColumnWidth(BOM_COL_NAME, 415)
        page._save_personal_bom_columns(page._ebom_tree, {"material"})
        reopened = self.page()
        self.assertEqual(reopened._cad_tree.columnWidth(1), 345)
        self.assertFalse(reopened._cad_tree.isColumnHidden(0))
        self.assertTrue(reopened._cad_tree.isColumnHidden(2))
        self.assertEqual(reopened._ebom_tree.columnWidth(BOM_COL_NAME), 415)

    def test_actual_save_for_me_button_persists_checkbox_choices(self):
        page = self.page()

        def save():
            dialog = QApplication.activeModalWidget()
            listing = dialog.findChild(QListWidget)
            for row in range(listing.count()):
                item = listing.item(row)
                item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == "material" else Qt.Unchecked)
            buttons = dialog.findChild(QDialogButtonBox)
            self.assertEqual(buttons.button(QDialogButtonBox.Save).text(), "Save for Me")
            buttons.button(QDialogButtonBox.Save).click()

        QTimer.singleShot(0, save)
        page.show_bom_column_dialog(tree=page._ebom_tree)
        self.assertEqual(self.repo.get_bom_column_preferences(1)["visible_extra_columns"], ["material"])

    def test_cancel_does_not_overwrite_saved_preferences(self):
        page = self.page()
        page._save_personal_bom_columns(page._ebom_tree, {"material"})
        before = self.repo.get_bom_column_preferences(1)
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        page.show_bom_column_dialog(tree=page._ebom_tree)
        self.assertEqual(self.repo.get_bom_column_preferences(1), before)

    def test_changed_schema_keeps_visibility_but_ignores_incompatible_header_state(self):
        page = self.page()
        page._ebom_tree.setColumnWidth(BOM_COL_NAME, 390)
        page._save_personal_bom_columns(page._ebom_tree, {"material"})
        prefs = self.repo.get_bom_column_preferences(1)
        prefs["layouts"]["ebom"]["headers"] = ["Old schema"]
        self.repo.set_bom_column_preferences(1, prefs)
        reopened = self.page()
        self.assertEqual(reopened._ebom_tree.columnWidth(BOM_COL_NAME), 260)
        self.assertEqual(reopened._visible_bom_column_keys(), {"material"})


if QT_AVAILABLE:
    class _ColumnHarness(QWidget):
        def __init__(self, repository, user_id):
            super().__init__()
            self.session = SimpleNamespace(user_id=user_id)
            self._bom_column_repo = repository
            for name, ebom in (("tree", False), ("_search_tree", False), ("_ebom_tree", True), ("_ebom_filter_tree", True)):
                tree = QTreeWidget(self)
                setattr(self, name, tree)
                self._configure_bom_tree_columns(tree, ebom)
            self._cad_tree = QTreeWidget(self)
            self._cad_tree.setHeaderLabels(["CAD Name", "Description", "Category"])
            self._restore_saved_bom_columns()

    for name in (
        "_bom_tree_headers", "_default_visible_bom_column_keys", "_visible_bom_column_keys",
        "_bom_column_preferences", "_save_visible_bom_column_keys", "_bom_column_tree_kind",
        "_install_bom_column_menu", "_restore_saved_bom_columns", "_save_personal_bom_columns",
        "_configure_bom_tree_columns", "_apply_bom_column_visibility", "show_bom_column_dialog",
    ):
        setattr(_ColumnHarness, name, getattr(BomPage, name))


if __name__ == "__main__":
    unittest.main()
