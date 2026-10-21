import base64
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.updates import shortcuts


class SetupShortcutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "Nexus à l'équipe $(unsafe) `!"
        self.launcher = self.root / "bootstrap/NexusLauncher.exe"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_bytes(b"trusted launcher")
        self.desktop = self.base / "Redirected Desktop à l'équipe"
        self.programs = self.base / "User Start Menu/Programs"
        self.scripts = []

    def run_powershell(self, command, **kwargs):
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(command[1:5], ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
        script = base64.b64decode(command[5]).decode("utf-16-le")
        self.scripts.append(script)
        match = re.search(r"\$link = \$shell.CreateShortcut\('((?:[^']|'')*)'\)", script)
        self.assertIsNotNone(match)
        Path(match.group(1).replace("''", "'")).write_bytes(b"generated shortcut")
        return subprocess.CompletedProcess(command, 0)

    def create(self, **kwargs):
        return shortcuts.create_shortcuts(self.root, desktop_dir=self.desktop,
                                          programs_dir=self.programs, **kwargs)

    @patch.object(subprocess, "run")
    def test_creates_start_menu_and_redirected_desktop_with_stable_launcher(self, run):
        run.side_effect = self.run_powershell
        paths = self.create()
        self.assertEqual(paths, [self.programs / "Nexus/Nexus.lnk", self.desktop / "Nexus.lnk"])
        self.assertEqual(run.call_count, 2)
        for path, script in zip(paths, self.scripts):
            self.assertEqual(path.read_bytes(), b"generated shortcut")
            self.assertIn("$link.TargetPath = " + shortcuts._literal(self.launcher), script)
            self.assertIn("$link.WorkingDirectory = " + shortcuts._literal(self.launcher.parent), script)
            self.assertIn("$link.Save()", script)
            self.assertIn("OrdinalIgnoreCase", script)
            self.assertFalse(list(path.parent.glob(".nexus-shortcut-*")))
        self.assertIn("à l''équipe $(unsafe) `!", self.scripts[0])

    @patch.object(subprocess, "run")
    def test_start_menu_is_always_created_and_desktop_is_optional(self, run):
        run.side_effect = self.run_powershell
        self.assertEqual(self.create(desktop=False), [self.programs / "Nexus/Nexus.lnk"])
        self.assertFalse(self.desktop.exists())
        self.assertEqual(run.call_count, 1)

    @patch.object(shortcuts, "_known_folder")
    @patch.object(subprocess, "run")
    def test_default_destinations_use_windows_known_folders(self, run, known):
        run.side_effect = self.run_powershell
        known.side_effect = lambda identifier: {
            shortcuts._PROGRAMS: self.programs, shortcuts._DESKTOP: self.desktop
        }[identifier]
        shortcuts.create_shortcuts(self.root)
        self.assertEqual([call.args[0] for call in known.call_args_list],
                         [shortcuts._PROGRAMS, shortcuts._DESKTOP])

    @patch.object(shortcuts, "_known_folder")
    @patch.object(subprocess, "run")
    def test_missing_launcher_does_not_resolve_destinations_or_write(self, run, known):
        with self.assertRaisesRegex(shortcuts.ShortcutError, "launcher is missing"):
            shortcuts.create_shortcuts(self.base / "missing")
        known.assert_not_called()
        run.assert_not_called()
        self.assertFalse(self.desktop.exists())
        self.assertFalse(self.programs.exists())

    @patch.object(subprocess, "run")
    def test_failed_com_does_not_replace_existing_shortcut_or_delete_other_files(self, run):
        destination = self.programs / "Nexus/Nexus.lnk"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"existing shortcut")
        unrelated = destination.parent / "Other.lnk"
        unrelated.write_bytes(b"unrelated shortcut")
        run.side_effect = subprocess.CalledProcessError(1, ["powershell"])
        with self.assertRaisesRegex(shortcuts.ShortcutError, "could not create the shortcut"):
            self.create()
        self.assertEqual(destination.read_bytes(), b"existing shortcut")
        self.assertEqual(unrelated.read_bytes(), b"unrelated shortcut")
        self.assertFalse(list(destination.parent.glob(".nexus-shortcut-*")))

    @patch.object(subprocess, "run")
    def test_replaces_existing_own_shortcut_only_after_new_link_is_ready(self, run):
        destination = self.programs / "Nexus/Nexus.lnk"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"existing own shortcut")

        def generate(command, **kwargs):
            self.assertEqual(destination.read_bytes(), b"existing own shortcut")
            return self.run_powershell(command, **kwargs)

        run.side_effect = generate
        self.create(desktop=False)
        self.assertEqual(destination.read_bytes(), b"generated shortcut")
        self.assertIn("$existing.TargetPath", self.scripts[0])
        self.assertIn("A different shortcut named Nexus already exists", self.scripts[0])

    @patch.object(subprocess, "run")
    def test_successful_process_without_link_is_reported(self, run):
        with self.assertRaisesRegex(shortcuts.ShortcutError, "did not create"):
            self.create()
        self.assertFalse((self.programs / "Nexus/Nexus.lnk").exists())

    @patch.object(shortcuts, "_known_folder")
    @patch.object(subprocess, "run")
    def test_desktop_disabled_does_not_query_desktop_folder(self, run, known):
        run.side_effect = self.run_powershell
        known.return_value = self.programs
        shortcuts.create_shortcuts(self.root, desktop=False)
        known.assert_called_once_with(shortcuts._PROGRAMS)


if __name__ == "__main__":
    unittest.main()
