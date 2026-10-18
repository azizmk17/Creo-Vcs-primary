import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("nexus_folder_builder", Path(__file__).resolve().parents[1] / "tools/build_exe.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class FolderBuilderTests(unittest.TestCase):
    def test_cleanup_is_limited_to_workspace_children(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory) / "project"
            root.mkdir()
            with patch.object(builder, "ROOT", root):
                self.assertEqual(builder.workspace_path(root / "build/folder"), root / "build/folder")
                for outside in (root, root / "../project-other", Path(directory)):
                    with self.assertRaises(ValueError):
                        builder.workspace_path(outside)

    def test_smoke_environment_removes_host_dependency_paths(self):
        host = {"PATH": "C:/conda/Library/bin", "CONDA_PREFIX": "C:/conda",
                "PYTHONPATH": "C:/source", "QT_PLUGIN_PATH": "C:/qt",
                "CSF_ShadersDirectory": "C:/occt", "CASROOT": "C:/occt",
                "SystemRoot": "C:/Windows", "APPDATA": "C:/users/test"}
        with patch.dict(builder.os.environ, host, clear=True):
            env = builder.smoke_environment()
        self.assertNotIn("conda", env["PATH"].lower())
        for name in ("CONDA_PREFIX", "PYTHONPATH", "QT_PLUGIN_PATH", "CSF_ShadersDirectory", "CASROOT"):
            self.assertNotIn(name, env)
        self.assertEqual(env["QT_QPA_PLATFORM"], "windows")
        self.assertEqual(env["APPDATA"], host["APPDATA"])

    def test_manifest_rejects_private_workspace_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            release = Path(directory)
            (release / "creo_vcs.db").write_bytes(b"private")
            with patch.object(builder, "RELEASE", release):
                with self.assertRaisesRegex(RuntimeError, "workspace files"):
                    builder.release_manifest("4.0.10", {})
            self.assertEqual((release / "creo_vcs.db").read_bytes(), b"private")

    def test_version_is_read_without_importing_database_config(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root = Path(directory)
            (root / "config.py").write_text('raise RuntimeError("must not import")\nAPP_VERSION = "4.0.10"\n')
            with patch.object(builder, "ROOT", root):
                self.assertEqual(builder.app_version(), "4.0.10")


if __name__ == "__main__":
    unittest.main()
