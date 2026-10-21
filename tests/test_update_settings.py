from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.updates import settings


class UpdateSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": temporary.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_parallel_clients_preserve_preferences_and_all_sequence_high_water_marks(self):
        settings.save_settings(feed="company-share")
        with ThreadPoolExecutor(max_workers=8) as workers:
            futures = [workers.submit(settings.remember_sequence, str(key), sequence)
                       for key in range(4) for sequence in (20, 3, 8)]
            futures.append(workers.submit(settings.save_settings, extra="kept"))
            for future in futures:
                future.result()
        for key in range(4):
            self.assertEqual(settings.last_sequence(str(key)), 20)
        self.assertEqual(settings.read_settings()["feed"], "company-share")
        self.assertEqual(settings.read_settings()["extra"], "kept")

    def test_temporary_windows_sharing_error_retries_without_losing_settings(self):
        settings.save_settings(feed="company-share")
        replace = os.replace
        attempts = []

        def scanned_file(source, destination):
            attempts.append(destination)
            if len(attempts) == 1:
                raise PermissionError("File temporarily held by another reader")
            return replace(source, destination)

        with patch("core.updates.settings.os.replace", side_effect=scanned_file):
            settings.remember_sequence("release-key", 9)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(settings.last_sequence("release-key"), 9)
        self.assertEqual(settings.update_feed(), "company-share")
        self.assertEqual(list(settings.settings_path().parent.glob("updates-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
