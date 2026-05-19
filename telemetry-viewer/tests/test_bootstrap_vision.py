import os
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import bootstrap_vision


class BootstrapVisionTest(unittest.TestCase):
    def test_templates_absent_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as temp:
            candidates, warnings = bootstrap_vision.template_candidates(
                Path(temp),
                screenshot_func=lambda: object(),
                locate_func=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(candidates, [])
        self.assertTrue(any("templates not found" in warning for warning in warnings))

    def test_template_candidates_use_supported_names_only(self):
        with tempfile.TemporaryDirectory() as temp:
            for name in ("play_now.png", "continue.png", "click_here_to_play.png", "world_380.png"):
                Path(temp, name).write_bytes(b"not-a-real-png")

            points = {
                "play_now.png": (100, 200),
                "continue.png": (110, 210),
                "click_here_to_play.png": (120, 220),
                "world_380.png": (999, 999),
            }

            candidates, warnings = bootstrap_vision.template_candidates(
                Path(temp),
                screenshot_func=lambda: object(),
                locate_func=lambda template, _screenshot, **_kwargs: points[Path(template).name],
            )

        self.assertEqual([candidate.name for candidate in candidates], ["play_now", "continue", "click_here_to_play"])
        self.assertEqual([candidate.screen_point for candidate in candidates], [{"x": 100, "y": 200}, {"x": 110, "y": 210}, {"x": 120, "y": 220}])
        self.assertEqual(warnings, [])

    def test_no_files_written_without_debug_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            bootstrap_vision.template_candidates(
                Path(temp),
                screenshot_func=lambda: object(),
                locate_func=lambda *_args, **_kwargs: None,
                save_debug_screenshot=False,
            )
            after = set(os.listdir(temp))

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
