import os
import tempfile
import unittest
from unittest.mock import patch

import utils
from core.services.managed_file_service import ManagedFileService
from core.services.part_file_service import PartFileService


class LongPathFileSizeTests(unittest.TestCase):
    def test_safe_getsize_uses_extended_length_path(self):
        with patch.object(utils, "long_path", return_value=r"\\?\C:\very-long-file") as convert:
            with patch.object(utils.os.path, "getsize", return_value=42) as getsize:
                self.assertEqual(utils.safe_getsize(r"C:\very-long-file"), 42)

        convert.assert_called_once_with(r"C:\very-long-file")
        getsize.assert_called_once_with(r"\\?\C:\very-long-file")

    def test_managed_file_blob_uses_safe_getsize(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "drawing.pdf")
            with open(source, "wb") as handle:
                handle.write(b"managed drawing")

            service = object.__new__(ManagedFileService)
            service._family_root = lambda _bom_id: root
            with patch(
                "core.services.managed_file_service.safe_getsize", return_value=123
            ) as getsize:
                stored = service.store_blob(1, source)

        self.assertEqual(stored["size_bytes"], 123)
        getsize.assert_called_once_with(stored["path"])

    def test_part_file_blob_uses_safe_getsize(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "drawing.pdf")
            with open(source, "wb") as handle:
                handle.write(b"generated drawing")

            service = object.__new__(PartFileService)
            service._family_working_dir = lambda _root_project_id: root
            with patch(
                "core.services.part_file_service.safe_getsize", return_value=456
            ) as getsize:
                stored = service._store_managed_blob(source, 1)

        self.assertEqual(stored["size_bytes"], 456)
        getsize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
