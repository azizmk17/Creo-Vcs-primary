import os
import unittest
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
except (ImportError, ModuleNotFoundError):
    PYQT_AVAILABLE = False
else:
    from pages.bom_page import (
        BOM_COL_AES,
        BOM_COL_FILES,
        BOM_COL_INTEGRITY,
        BOM_COL_NAME,
        BOM_COL_REV,
        BOM_COL_ROW,
        BOM_COL_STATUS,
        BOM_COL_TYPE,
        BOM_TREE_FILES_ROLE,
        BOM_TREE_INTEGRITY_ROLE,
        CAD_COL_ASSOCIATION,
        CAD_COL_BUILD,
        CAD_COL_CATEGORY,
        CAD_COL_CHECKOUT,
        CAD_COL_DESCRIPTION,
        CAD_COL_FILE,
        CAD_COL_NUMBER,
        CAD_COL_QTY,
        CAD_COL_REV,
        CAD_COL_STATE,
        EBOM_COL_EFFECTIVE_QTY,
        EBOM_COL_LEVEL,
        EBOM_COL_SOURCE_QTY,
        PDM_ASSOCIATED_ITEM_ID_ROLE,
        PDM_ASSOCIATION_ID_ROLE,
        PDM_ASSOCIATION_TYPE_ROLE,
        PDM_CAD_DOCUMENT_ID_ROLE,
        PDM_CAD_MEMBER_ID_ROLE,
        PDM_CAD_PAYLOAD_ROLE,
        PDM_OBJECT_CAD,
        PDM_OBJECT_ITEM,
        PDM_OBJECT_KIND_ROLE,
        BomPage,
        _pdm_cad_icon,
    )

    PYQT_AVAILABLE = True


@unittest.skipUnless(PYQT_AVAILABLE, "PyQt5 is required for offscreen PDM tree tests")
class PdmTreeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.page = _PdmTreeHarness()

    def test_native_cad_row_uses_cad_schema_and_roles_without_delivery_roles(self):
        document = _cad_document(
            id=501,
            association_id=601,
            association_type="OWNER",
            item_id=101,
            item_number="AES-101",
            item_name="Pump",
            member_id=701,
            quantity=2,
        )

        row = self.page._add_pdm_cad_node(document)

        self.assertEqual(row.columnCount(), CAD_COL_QTY + 1)
        self.assertEqual(row.data(0, PDM_OBJECT_KIND_ROLE), PDM_OBJECT_CAD)
        self.assertEqual(row.data(0, PDM_CAD_DOCUMENT_ID_ROLE), 501)
        self.assertEqual(row.data(0, PDM_ASSOCIATION_ID_ROLE), 601)
        self.assertEqual(row.data(0, PDM_ASSOCIATION_TYPE_ROLE), "OWNER")
        self.assertEqual(row.data(0, PDM_ASSOCIATED_ITEM_ID_ROLE), 101)
        self.assertEqual(row.data(0, PDM_CAD_MEMBER_ID_ROLE), 701)
        self.assertEqual(row.data(0, PDM_CAD_PAYLOAD_ROLE)["file_name"], "pump.asm")
        self.assertEqual(row.text(CAD_COL_NUMBER), "pump.asm")
        self.assertEqual(row.text(CAD_COL_FILE), "pump.asm")
        self.assertEqual(row.text(CAD_COL_DESCRIPTION), "Pump CAD")
        self.assertEqual(row.text(CAD_COL_CATEGORY), "ASSEMBLY")
        self.assertEqual(row.text(CAD_COL_REV), "B.3")
        self.assertEqual(row.text(CAD_COL_STATE), "IN_WORK")
        self.assertIn("OWNER", row.text(CAD_COL_ASSOCIATION))
        self.assertEqual(row.text(CAD_COL_CHECKOUT), "Checked in")
        self.assertEqual(row.text(CAD_COL_BUILD), "Included")
        self.assertEqual(row.text(CAD_COL_QTY), "2")
        self.assertFalse(row.icon(CAD_COL_NUMBER).isNull())

        # Native CAD rows carry CAD metadata only. Item delivery and integrity
        # delegate payloads must never leak into this structure.
        for column in range(row.columnCount()):
            self.assertIsNone(row.data(column, BOM_TREE_FILES_ROLE))
            self.assertIsNone(row.data(column, BOM_TREE_INTEGRITY_ROLE))
        rendered = " ".join(row.text(column).upper() for column in range(row.columnCount()))
        self.assertNotIn("PDF", rendered)
        self.assertNotIn("STEP", rendered)

    def test_ebom_associated_cad_child_is_non_structural_and_has_blank_item_health_cells(self):
        item_parent = QTreeWidgetItem([""] * self.page._ebom_tree.columnCount())
        self.page._ebom_tree.addTopLevelItem(item_parent)
        document = _cad_document(id=502, association_id=602, association_type="IMAGE")

        cad_row = self.page._add_ebom_associated_cad_node(
            document, item_parent, item_id=102
        )

        self.assertIs(item_parent.child(0), cad_row)
        self.assertEqual(cad_row.data(0, PDM_OBJECT_KIND_ROLE), PDM_OBJECT_CAD)
        self.assertEqual(cad_row.data(0, PDM_CAD_DOCUMENT_ID_ROLE), 502)
        self.assertEqual(cad_row.data(0, PDM_ASSOCIATION_ID_ROLE), 602)
        self.assertEqual(cad_row.data(0, PDM_ASSOCIATION_TYPE_ROLE), "IMAGE")
        self.assertEqual(cad_row.data(0, PDM_ASSOCIATED_ITEM_ID_ROLE), 102)
        self.assertEqual(cad_row.text(BOM_COL_FILES), "")
        self.assertEqual(cad_row.text(BOM_COL_INTEGRITY), "")
        self.assertIsNone(cad_row.data(BOM_COL_FILES, BOM_TREE_FILES_ROLE))
        self.assertIsNone(cad_row.data(BOM_COL_INTEGRITY, BOM_TREE_INTEGRITY_ROLE))
        self.assertEqual(cad_row.text(EBOM_COL_SOURCE_QTY), "")
        self.assertEqual(cad_row.text(EBOM_COL_EFFECTIVE_QTY), "")
        self.assertEqual(cad_row.text(EBOM_COL_LEVEL), "")
        self.assertFalse(cad_row.icon(BOM_COL_NAME).isNull())
        self.assertIn("not an EBOM usage", cad_row.toolTip(BOM_COL_NAME))

    def test_ebom_item_keeps_item_identity_icon_and_delivery_health_cells(self):
        row = self.page._add_released_ebom_node(_item_payload(103, "Pump Item"))

        self.assertEqual(row.data(0, PDM_OBJECT_KIND_ROLE), PDM_OBJECT_ITEM)
        self.assertEqual(row.data(0, Qt.UserRole), 103)
        self.assertFalse(row.icon(BOM_COL_NAME).isNull())
        self.assertNotEqual(
            row.icon(BOM_COL_NAME).cacheKey(),
            _pdm_cad_icon("ASSEMBLY").cacheKey(),
        )
        self.assertEqual(row.text(BOM_COL_FILES), "delivery-status")
        self.assertEqual(row.text(BOM_COL_INTEGRITY), "integrity-status")
        self.assertEqual(row.text(EBOM_COL_SOURCE_QTY), "1")
        self.assertEqual(row.text(EBOM_COL_EFFECTIVE_QTY), "1")

    def test_ebom_renumbering_skips_associated_cad_rows(self):
        child = _item_payload(202, "Child Item", level=1)
        root = _item_payload(201, "Root Item", children=[child])
        associations = {
            201: [_cad_document(id=801, association_id=901, association_type="OWNER")],
            202: [_cad_document(id=802, association_id=902, association_type="CONTENT")],
        }
        root_row = self.page._add_released_ebom_node(
            root, associations_by_item=associations
        )

        self.page._renumber_tree_rows(self.page._ebom_tree)

        root_cad = root_row.child(0)
        child_item = root_row.child(1)
        child_cad = child_item.child(0)
        self.assertEqual(root_row.text(BOM_COL_ROW), "1")
        self.assertEqual(root_cad.text(BOM_COL_ROW), "")
        self.assertEqual(child_item.text(BOM_COL_ROW), "2")
        self.assertEqual(child_cad.text(BOM_COL_ROW), "")

    def test_related_cad_does_not_make_an_item_bypass_advanced_filters(self):
        root_row = self.page._add_released_ebom_node(
            _item_payload(301, "Filtered Item"),
            associations_by_item={
                301: [_cad_document(id=803, association_id=903)]
            },
        )
        cad_row = root_row.child(0)

        self.page._matching_item_ids = set()
        self.page._refresh_ebom_filters()
        self.assertTrue(root_row.isHidden())
        self.assertTrue(cad_row.isHidden())

        self.page._matching_item_ids = {301}
        self.page._refresh_ebom_filters()
        self.assertFalse(root_row.isHidden())
        self.assertFalse(cad_row.isHidden())


class _PdmTreeHarness:
    """Bind only the deterministic BomPage tree helpers under test."""

    _pdm_cad_checkout_text = BomPage._pdm_cad_checkout_text if PYQT_AVAILABLE else None
    _add_pdm_cad_node = BomPage._add_pdm_cad_node if PYQT_AVAILABLE else None
    _add_ebom_associated_cad_node = (
        BomPage._add_ebom_associated_cad_node if PYQT_AVAILABLE else None
    )
    _add_released_ebom_node = BomPage._add_released_ebom_node if PYQT_AVAILABLE else None
    _renumber_tree_rows = BomPage._renumber_tree_rows if PYQT_AVAILABLE else None
    _refresh_ebom_filters = BomPage._refresh_ebom_filters if PYQT_AVAILABLE else None

    def __init__(self):
        self.session = SimpleNamespace(user_id=7)
        self._cad_tree = QTreeWidget()
        self._cad_tree.setColumnCount(CAD_COL_QTY + 1)
        self._ebom_tree = QTreeWidget()
        self._ebom_tree.setColumnCount(EBOM_COL_LEVEL + 1)
        self._bom_row_numbers = {}
        self.search_input = SimpleNamespace(text=lambda: "")
        self._bom_advanced_filters = {
            "status": "Released",
            "show_parent_matches": True,
        }
        self._matching_item_ids = set()

    @staticmethod
    def _default_bom_advanced_filters():
        return {}

    @staticmethod
    def _is_default_bom_advanced_filter(_filters):
        return False

    def _bom_tree_item_matches_advanced_filter(self, item, _filters):
        item_id = item.data(0, Qt.UserRole)
        return item_id is not None and int(item_id) in self._matching_item_ids

    def _apply_tree_item_data(self, item, payload):
        """Minimal Item hydration; delivery columns are intentional sentinels."""
        item.setData(0, Qt.UserRole, int(payload["id"]))
        item.setText(BOM_COL_NAME, str(payload.get("name") or ""))
        item.setText(BOM_COL_FILES, "delivery-status")
        item.setText(BOM_COL_AES, str(payload.get("aes_number") or ""))
        item.setText(BOM_COL_TYPE, str(payload.get("type") or ""))
        item.setText(BOM_COL_REV, str(payload.get("current_version") or ""))
        item.setText(BOM_COL_STATUS, str(payload.get("status") or ""))
        item.setText(BOM_COL_INTEGRITY, "integrity-status")


def _cad_document(**overrides):
    values = {
        "id": 500,
        "number": "CAD-501",
        "name": "Pump CAD",
        "file_name": "pump.asm",
        "category": "ASSEMBLY",
        "revision": "B",
        "iteration": 3,
        "lifecycle_state": "IN_WORK",
        "association_id": None,
        "association_type": None,
        "item_id": None,
        "item_number": "",
        "item_name": "",
        "member_id": None,
        "quantity": 1,
        "build_excluded": False,
        "checked_out_by": None,
        "related_drawings": [],
        "children": [],
    }
    values.update(overrides)
    return values


def _item_payload(item_id, name, *, level=0, children=None):
    return {
        "bom_id": int(item_id),
        "name": str(name),
        "aes_number": f"AES-{item_id}",
        "type": "asm" if level == 0 else "prt",
        "version_label": "A.1",
        "state": "IN_WORK",
        "source_quantity": 1,
        "effective_quantity": 1,
        "level": int(level),
        "children": list(children or []),
    }


if __name__ == "__main__":
    unittest.main()
