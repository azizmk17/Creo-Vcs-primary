import contextlib
import sqlite3
import threading
import unittest
from unittest.mock import patch

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

import config
from main3 import BomGUI
from pages.bom_page import BOM_TREE_LOADING_ROLE, BOM_TREE_FOLDER_ROLE, PDM_NODE_PAYLOAD_ROLE, _PROJECT_LOAD_THREADS
from tests import test_project_loading as fixtures


class LargeProjectLoadingTests(unittest.TestCase):
    setUp = fixtures.ProjectShellIntegrationTests.setUp
    tearDown = fixtures.ProjectShellIntegrationTests.tearDown
    wait_until = fixtures.ProjectShellIntegrationTests.wait_until

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def seed(self, ids, edges=()):
        parents = {p for p, _, _ in edges}
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.executemany(
                "INSERT INTO bom(id,project_id,type,name,part_number,created,modified) VALUES(?,12,?,?,?,?,?)",
                [(i, "asm" if i in parents else "prt", f"Item {i}", f"PN-{i}", "now", "now") for i in ids],
            )
            conn.executemany(
                "INSERT INTO item_usages(project_id,parent_item_id,child_item_id,quantity) VALUES(12,?,?,?)", edges,
            )

    def load(self):
        self.session.update_project(12)
        self.window = BomGUI()
        page = self.window.bom_page
        page._load_released_ebom_tree()
        self.wait_until(lambda: not page._structure_loading)
        self.assertIsNone(page._structure_error)
        return page

    def test_branch_load_and_search_cover_unloaded_items(self):
        self.seed([100, 101, 102], [(100, 101, 2), (101, 102, 5)])
        page = self.load()
        root = page._ebom_tree.topLevelItem(0)
        self.assertTrue(root.data(0, PDM_NODE_PAYLOAD_ROLE)["_children_deferred"])
        with patch.object(page.bom_service, "get_released_ebom_project", side_effect=AssertionError("Full tree should not be queried")):
            root.setExpanded(True)
            self.wait_until(lambda: not page._lazy_level_requests)
            child = root.child(0)
            self.assertEqual(child.data(0, Qt.UserRole), 101)
            self.assertEqual(child.data(0, PDM_NODE_PAYLOAD_ROLE)["effective_quantity"], 2)
            # The grandchild has not been loaded, but SQL search must find it.
            page.search_input.setText("PN-102")
            page._refresh_ebom_filters()
            self.assertTrue(any(row.data(0, Qt.UserRole) == 102
                                for row in page._iter_tree_items(page._ebom_filter_tree)))

    def test_root_and_child_pagination(self):
        self.seed(range(1000, 1205))
        page = self.load()
        self.assertEqual(len(page._pdm_ebom_roots), 200)
        more = page._ebom_tree.topLevelItem(200)
        more.setExpanded(True)
        self.wait_until(lambda: not page._lazy_level_requests)
        self.assertEqual(len(page._pdm_ebom_roots), 205)
        self.assertEqual(page._ebom_tree.topLevelItemCount(), 205)
        self.assertEqual(len({n["bom_id"] for n in page._pdm_ebom_roots}), 205)
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.executemany(
                "INSERT INTO item_usages(project_id,parent_item_id,child_item_id) VALUES(12,1000,?)",
                [(i,) for i in range(1001, 1205)],
            )
        page._load_released_ebom_tree()
        self.wait_until(lambda: not page._structure_loading)
        root = page._ebom_tree.topLevelItem(0)
        root.setExpanded(True)
        self.wait_until(lambda: not page._lazy_level_requests)
        self.assertEqual(root.childCount(), 201)
        root.child(200).setExpanded(True)
        self.wait_until(lambda: not page._lazy_level_requests)
        self.assertEqual(root.childCount(), 204)
        self.assertEqual(len({root.child(i).data(0, Qt.UserRole) for i in range(204)}), 204)

    def test_failed_branch_retries_and_complete_actions_include_unloaded_pages(self):
        self.seed([100, 101, 102], [(100, 101, 2), (101, 102, 5)])
        page = self.load()
        root = page._ebom_tree.topLevelItem(0)
        with patch.object(page.bom_service, "get_released_ebom_level", side_effect=OSError("offline")):
            root.setExpanded(True)
            self.wait_until(lambda: not page._lazy_level_requests)
            self.assertFalse(root.data(0, BOM_TREE_LOADING_ROLE))
            self.assertTrue(root.data(0, PDM_NODE_PAYLOAD_ROLE)["_children_deferred"])
        root.setExpanded(True)
        self.wait_until(lambda: not page._lazy_level_requests)
        self.assertEqual(root.child(0).data(0, Qt.UserRole), 101)
        page._materialize_pdm_tree_for_search(page._ebom_tree)
        self.assertTrue(any(row.data(0, Qt.UserRole) == 102 for row in page._iter_tree_items(page._ebom_tree)))

    def test_level_context_preserves_reuse_quantities_and_rejects_cycles(self):
        self.seed([100, 101, 102, 200], [(100, 101, 2), (200, 101, 3), (101, 102, 5)])
        page = self.load()
        svc = page.bom_service.pdm_service
        roots = svc.get_item_structure_level(12)["roots"]
        self.assertEqual({r["bom_id"] for r in roots}, {100, 200})
        totals = []
        for root in roots:
            branch = svc.get_item_structure_level(12, root)["roots"][0]
            totals.append(svc.get_item_structure_level(12, branch)["roots"][0]["effective_quantity"])
        self.assertEqual(sorted(totals), [10, 15])
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.execute("INSERT INTO item_usages(project_id,parent_item_id,child_item_id) VALUES(12,102,100)")
        with self.assertRaisesRegex(ValueError, "Circular"):
            svc.get_item_structure_level(12, {"id": 102, "_ancestor_ids": [100, 101]})

    def test_refresh_during_a_branch_request_discards_its_late_result(self):
        self.seed([100, 101], [(100, 101, 1)])
        page = self.load()
        entered, release = threading.Event(), threading.Event()
        original = page.bom_service.get_released_ebom_level
        def delayed(project, parent=None, **kwargs):
            if parent is not None:
                entered.set()
                release.wait(5)
            return original(project, parent, **kwargs)
        with patch.object(page.bom_service, "get_released_ebom_level", side_effect=delayed):
            page._ebom_tree.topLevelItem(0).setExpanded(True)
            try:
                self.wait_until(entered.is_set)
                page._load_released_ebom_tree()
                self.wait_until(lambda: not page._structure_loading)
            finally:
                release.set()
                self.wait_until(lambda: not _PROJECT_LOAD_THREADS)
        fresh = page._ebom_tree.topLevelItem(0)
        self.assertTrue(fresh.data(0, PDM_NODE_PAYLOAD_ROLE)["_children_deferred"])
        self.assertFalse(fresh.isExpanded())

    def test_folder_navigation_includes_members_on_unloaded_root_pages(self):
        self.seed(range(1000, 1205))
        with contextlib.closing(sqlite3.connect(config.DB_NAME)) as conn, conn:
            conn.execute("INSERT INTO bom_folders(id,project_id,name) VALUES(1,12,'Fixture folder')")
            conn.executemany("INSERT INTO bom_folder_items(folder_id,bom_id) VALUES(1,?)", [(1001,), (1204,)])
        page = self.load()
        folder = next(row for row in page._iter_tree_items(page._ebom_tree)
                      if row.data(0, BOM_TREE_FOLDER_ROLE) == 1)
        self.assertEqual(folder.childCount(), 1)
        page._render_pdm_folder_context("EBOM", None, [page._ebom_tree])
        self.assertEqual(sum(row.data(0, BOM_TREE_FOLDER_ROLE) == 1
                             for row in page._iter_tree_items(page._ebom_tree)), 1)
        page._open_pdm_folder_item(folder)
        shown = {row.data(0, Qt.UserRole) for row in page._iter_tree_items(page._ebom_tree)}
        self.assertIn(1001, shown)
        self.assertIn(1204, shown)


if __name__ == "__main__":
    unittest.main()
