import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


_HELPER_PATH = Path(__file__).resolve().parents[1] / "tools" / "frozen_build" / "dependencies.py"
_SPEC = importlib.util.spec_from_file_location("nexus_packaging_dependencies", _HELPER_PATH)
dependencies = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dependencies)


class PackagingDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.environment = Path(self.temporary.name)
        self.package = self.environment / "Lib" / "site-packages" / "OCC"
        self.package.mkdir(parents=True)
        self.native = self.environment / "Library" / "bin"
        self.native.mkdir(parents=True)
        (self.native / "TKOpenGl.dll").write_bytes(b"OpenCascade renderer")
        self.resources = self.environment / "Library" / "share" / "opencascade" / "resources"
        (self.resources / "Shaders").mkdir(parents=True)
        (self.resources / "UnitsAPI").mkdir()
        (self.resources / "UnitsAPI" / "Units.dat").write_text("units", encoding="utf-8")

        self.hooks = types.ModuleType("PyInstaller.utils.hooks")
        self.hooks.get_package_paths = Mock(return_value=(str(self.package.parent), str(self.package)))
        self.hooks.collect_data_files = Mock(return_value=[])
        self.hooks.collect_dynamic_libs = Mock(return_value=[])
        self.hooks.collect_submodules = Mock(side_effect=lambda package, **kwargs: [package])
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {
            "PyInstaller": types.ModuleType("PyInstaller"),
            "PyInstaller.utils": types.ModuleType("PyInstaller.utils"),
            "PyInstaller.utils.hooks": self.hooks,
        }).start()
        self.available = {"OCC", "fitz"}
        patch.object(dependencies, "find_spec", side_effect=lambda name: object() if name in self.available else None).start()

    def collect(self):
        return dependencies.collect_runtime_dependencies(prefix=self.environment)

    def test_older_pymupdf_with_only_fitz_is_supported(self):
        _, _, hidden = self.collect()
        self.assertIn("fitz", hidden)
        self.assertNotIn("pymupdf", hidden)
        self.assertEqual(self.hooks.collect_data_files.call_args.args[0], "fitz")

    def test_modern_pymupdf_packages_and_native_files_are_included(self):
        self.available.add("pymupdf")
        self.hooks.collect_dynamic_libs.side_effect = lambda package: (
            [(str(self.environment / "mupdf.dll"), "pymupdf")] if package == "pymupdf" else []
        )
        _, binaries, hidden = self.collect()
        self.assertIn("fitz", hidden)
        self.assertIn("pymupdf", hidden)
        self.assertIn((str(self.environment / "mupdf.dll"), "pymupdf"), binaries)

    def test_native_pdf_libraries_are_not_duplicated_as_ordinary_data(self):
        library = (str(self.environment / "mupdfcpp64.dll"), "fitz")
        self.hooks.collect_data_files.return_value = [library, (str(self.environment / "license.txt"), "fitz")]
        self.hooks.collect_dynamic_libs.side_effect = lambda package: [library] if package == "fitz" else []
        datas, binaries, _ = self.collect()
        self.assertNotIn(library, datas)
        self.assertEqual(binaries.count(library), 1)
        self.assertIn((str(self.environment / "license.txt"), "fitz"), datas)

    def test_required_pdf_or_occ_dependency_fails_clearly(self):
        for available, message in (({"OCC"}, "PyMuPDF is missing"), ({"fitz"}, "OCC is missing")):
            with self.subTest(available=available):
                self.available = available
                with self.assertRaisesRegex(RuntimeError, message):
                    self.collect()

    def test_resources_keep_the_expected_runtime_destination(self):
        datas, _, _ = self.collect()
        self.assertIn((str(self.resources.resolve()), "occ-resources"), datas)

    def test_incomplete_resources_stop_the_build(self):
        (self.resources / "UnitsAPI" / "Units.dat").unlink()
        with self.assertRaisesRegex(RuntimeError, "OpenCascade resources"):
            self.collect()

    def test_dynamically_loaded_renderer_is_seeded_without_copying_other_dlls(self):
        (self.native / "unrelated_notebook.dll").write_bytes(b"must not ship")
        _, binaries, _ = self.collect()
        self.assertEqual(binaries, [(str((self.native / "TKOpenGl.dll").resolve()), ".")])

    def test_missing_renderer_stops_instead_of_silently_shipping_broken_cad(self):
        (self.native / "TKOpenGl.dll").unlink()
        with self.assertRaisesRegex(RuntimeError, "TKOpenGl.dll is missing"):
            self.collect()

    def test_only_relevant_occ_modules_are_explicitly_collected(self):
        _, _, hidden = self.collect()
        self.assertIn("OCC.Core.STEPControl", hidden)
        self.assertIn("OCC.Core.Visualization", hidden)
        self.assertIn("OCC.Display.qtDisplay", hidden)
        self.assertNotIn("OCC.Display.wxDisplay", hidden)
        self.assertNotIn("OCC.Display.SimpleGui", hidden)
        self.assertNotIn("OCC.Core.IVtk", hidden)
        self.assertEqual(len(hidden), len(set(hidden)))

    def test_search_paths_include_only_existing_native_locations(self):
        paths = dependencies.native_library_search_paths(self.environment)
        self.assertEqual(paths, [str(self.native.resolve()), str(self.environment.resolve())])


if __name__ == "__main__":
    unittest.main()
