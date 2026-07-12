import unittest

from core.startup_gate import MINIMUM_STARTUP_LOADER_MS, StartupGate


class StartupGateTests(unittest.TestCase):
    def test_loader_minimum_is_four_seconds(self):
        self.assertEqual(MINIMUM_STARTUP_LOADER_MS, 4000)

    def test_ready_before_minimum_releases_when_minimum_elapses(self):
        gate = StartupGate()
        self.assertFalse(gate.mark_page_ready())
        self.assertTrue(gate.mark_minimum_elapsed())

    def test_ready_after_minimum_releases_when_page_becomes_ready(self):
        gate = StartupGate()
        self.assertFalse(gate.mark_minimum_elapsed())
        self.assertTrue(gate.mark_page_ready())

    def test_gate_releases_only_once(self):
        gate = StartupGate()
        self.assertFalse(gate.mark_page_ready())
        self.assertTrue(gate.mark_minimum_elapsed())
        self.assertFalse(gate.mark_page_ready())
        self.assertFalse(gate.mark_minimum_elapsed())

    def test_reset_allows_a_new_startup_attempt(self):
        gate = StartupGate()
        gate.mark_page_ready()
        self.assertTrue(gate.mark_minimum_elapsed())
        gate.reset()
        self.assertFalse(gate.released)
        self.assertFalse(gate.mark_minimum_elapsed())
        self.assertTrue(gate.mark_page_ready())


if __name__ == "__main__":
    unittest.main()
