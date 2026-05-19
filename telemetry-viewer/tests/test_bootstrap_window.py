import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import bootstrap_window


class FakeWindow:
    def __init__(self, title="RuneLite", left=10, top=20, width=900, height=700, fail_move=False):
        self.title = title
        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.fail_move = fail_move
        self.restored = False
        self.moves = []
        self.resizes = []

    def restore(self):
        self.restored = True

    def moveTo(self, x, y):
        if self.fail_move:
            raise RuntimeError("move failed")
        self.left = x
        self.top = y
        self.moves.append((x, y))

    def resizeTo(self, width, height):
        if self.fail_move:
            raise RuntimeError("resize failed")
        self.width = width
        self.height = height
        self.resizes.append((width, height))


class BootstrapWindowTest(unittest.TestCase):
    def test_move_to_secondary_attempts_window_move(self):
        window = FakeWindow()

        result = bootstrap_window.find_and_prepare_window(
            ["RuneLite"],
            move_to_secondary=True,
            monitor_index=1,
            execute=True,
            window_provider=lambda: [window],
            monitor_provider=lambda: [
                bootstrap_window.MonitorBounds(0, 0, 1920, 1080),
                bootstrap_window.MonitorBounds(1920, 0, 1920, 1080),
            ],
        )

        self.assertEqual(result["matchedWindowTitle"], "RuneLite")
        self.assertEqual(result["placement"]["status"], "PASS")
        self.assertEqual(result["placement"]["monitorTarget"], 1)
        self.assertEqual(result["originalWindowBounds"], {"x": 10, "y": 20, "width": 900, "height": 700})
        self.assertEqual(result["finalWindowBounds"]["x"], 2000)
        self.assertEqual(result["finalWindowBounds"]["y"], 80)
        self.assertTrue(window.restored)
        self.assertEqual(window.moves, [(2000, 80)])
        self.assertEqual(window.resizes, [(900, 700)])

    def test_monitor_index_chooses_mocked_monitor(self):
        window = FakeWindow()

        result = bootstrap_window.find_and_prepare_window(
            ["RuneLite"],
            move_to_secondary=True,
            monitor_index=2,
            execute=True,
            window_provider=lambda: [window],
            monitor_provider=lambda: [
                bootstrap_window.MonitorBounds(0, 0, 1280, 720),
                bootstrap_window.MonitorBounds(1280, 0, 1280, 720),
                bootstrap_window.MonitorBounds(-1600, 0, 1600, 900),
            ],
        )

        self.assertEqual(result["placement"]["monitorTarget"], 2)
        self.assertEqual(result["finalWindowBounds"]["x"], -1520)
        self.assertEqual(result["finalWindowBounds"]["y"], 80)

    def test_window_move_failure_warns_not_hard_fail(self):
        window = FakeWindow(fail_move=True)

        result = bootstrap_window.find_and_prepare_window(
            ["RuneLite"],
            move_to_secondary=True,
            monitor_index=1,
            execute=True,
            window_provider=lambda: [window],
            monitor_provider=lambda: [
                bootstrap_window.MonitorBounds(0, 0, 1920, 1080),
                bootstrap_window.MonitorBounds(1920, 0, 1920, 1080),
            ],
        )

        self.assertEqual(result["matchedWindowTitle"], "RuneLite")
        self.assertEqual(result["placement"]["status"], "WARN")
        self.assertIn("move failed", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
