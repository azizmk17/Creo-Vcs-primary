import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")

try:
    from PyQt5.QtWidgets import QDialog, QMessageBox
except ImportError:
    QT_AVAILABLE = False
else:
    from pages.bom_page import BomPage

    QT_AVAILABLE = True


@unittest.skipUnless(QT_AVAILABLE, "PyQt5 is required for BOM checkout UI tests")
class BomCheckoutUiTests(unittest.TestCase):
    def page(self):
        return SimpleNamespace(
            current_part_id=1,
            current_cad_document_id=None,
            session=SimpleNamespace(user_id=7),
            perm=SimpleNamespace(can=lambda permission: permission == "manage_parts"),
            bom_service=Mock(),
            _nexus_checkout_service=Mock(return_value=Mock(active_branch_map=Mock(return_value={}))),
            _refresh_loaded_part_branch=Mock(),
            _invalidate_doc_indicator=Mock(),
            _renumber_full_bom_tree_rows=Mock(),
            _sync_search_tree_row_numbers=Mock(),
            _refresh_pdm_context_rows=Mock(),
            display_details=Mock(),
            _prompt_actor_user_id=Mock(return_value=7),
            _sync_action_ribbon_menus=Mock(),
        )

    def test_checkin_refreshes_all_released_cad_without_redirecting_to_commit(self):
        page = self.page()
        analysis = {"has_non_cad_changes": False, "current_version": "A.2", "related_cad_only": True}
        page.bom_service.analyze_item_checkout.return_value = analysis
        page.bom_service.checkin_item_data.return_value = {
            "context": {"version_label": "A.2"},
            "analysis": analysis,
            "affected_part_ids": [1],
            "released_cad_ids": [11, 12],
        }
        with patch("pages.bom_page.CheckoutReviewDialog") as review, patch(
            "pages.bom_page.QMessageBox.information"
        ) as info, patch("pages.bom_page.QMessageBox.warning") as warning:
            review.ACTION_CHECKIN = "checkin"
            review.ACTION_COMMIT = "commit"
            review.return_value.exec_.return_value = QDialog.Accepted
            review.return_value.action = "checkin"
            review.return_value.comment.return_value = "Finish after publishing 3D"

            BomPage.checkin_part(page)

        page.bom_service.checkin_item_data.assert_called_once_with(1, "Finish after publishing 3D")
        page._refresh_pdm_context_rows.assert_called_once_with(cad_ids=[11, 12])
        page.display_details.assert_called_once_with(1)
        self.assertIn("All related CAD checkouts were undone", info.call_args.args[2])
        warning.assert_not_called()

    def test_undo_snapshots_drawing_ids_before_service_clears_checkouts(self):
        page = self.page()
        page.bom_service.get_part_details.return_value = {"locked": True, "locked_by_user_id": 7}
        active = [
            {"id": 11, "file_name": "pump.prt", "checked_out_by": 7},
            {"id": 12, "file_name": "pump.drw", "checked_out_by": 9, "checkout_workspace_id": "other"},
        ]
        page.bom_service.checked_out_cad_for_item.side_effect = lambda _item: list(active)
        page.bom_service.undo_item_checkout.side_effect = lambda *_args, **_kwargs: active.clear()
        with patch("pages.bom_page.QMessageBox.question", return_value=QMessageBox.Yes) as question, patch(
            "pages.bom_page.QMessageBox.information"
        ), patch("pages.bom_page.QMessageBox.warning") as warning:
            BomPage.undo_checkout(page)

        page.bom_service.undo_item_checkout.assert_called_once_with(1, as_user_id=7)
        page._refresh_pdm_context_rows.assert_called_once_with(item_ids=[1], cad_ids=[11, 12])
        self.assertIn("including drawings", question.call_args.args[2])
        self.assertNotIn("will stay checked out", question.call_args.args[2])
        warning.assert_not_called()

    def lifecycle_page(self):
        page = self.page()
        for name in (
            "checkout_part_btn", "checkin_part_btn", "undo_checkout_btn", "edit_part_btn",
            "delete_part_btn", "add_child_btn", "compare_iterations_btn", "create_configuration_btn",
            "update_child_versions_btn", "set_revision_btn", "release_revision_btn",
        ):
            setattr(page, name, Mock())
        page.bom_service.checked_out_cad_for_item.return_value = [
            {"id": 12, "file_name": "pump.drw", "checked_out_by": 9, "checkout_workspace_id": "other"}
        ]
        return page

    def test_remaining_drawing_enables_item_cleanup_after_item_checkout_closed(self):
        page = self.lifecycle_page()

        BomPage._update_lifecycle_action_states(page, {"locked": False, "type": "prt"})

        page.checkin_part_btn.setEnabled.assert_called_once_with(True)
        page.undo_checkout_btn.setEnabled.assert_called_once_with(True)
        page.edit_part_btn.setEnabled.assert_called_once_with(False)
        self.assertIn("including drawings", page.undo_checkout_btn.setToolTip.call_args.args[0])

    def test_related_cad_does_not_bypass_another_users_item_lock(self):
        page = self.lifecycle_page()

        BomPage._update_lifecycle_action_states(
            page, {"locked": True, "locked_by_user_id": 9, "type": "prt"}
        )

        page.checkin_part_btn.setEnabled.assert_called_once_with(False)
        page.undo_checkout_btn.setEnabled.assert_called_once_with(False)


if __name__ == "__main__":
    unittest.main()
