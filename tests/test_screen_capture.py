from __future__ import annotations

import ast
import unittest
from pathlib import Path

from PIL import Image

from osrs_bot.model import ScreenBounds, ScreenPoint
from osrs_bot.screen_capture import bounded_region_around, capture_canvas_region


ROOT = Path(__file__).resolve().parents[1]


class ScreenCaptureTests(unittest.TestCase):
    def test_event_crop_is_clamped_inside_verified_canvas(self) -> None:
        canvas = ScreenBounds(100, 200, 765, 503)

        top_left = bounded_region_around(
            canvas, ScreenPoint(100, 200), width=320, height=240
        )
        bottom_right = bounded_region_around(
            canvas, ScreenPoint(864, 702), width=320, height=240
        )

        self.assertEqual(ScreenBounds(100, 200, 320, 240), top_left)
        self.assertEqual(ScreenBounds(545, 463, 320, 240), bottom_right)

    def test_capture_uses_exact_screen_region_and_reports_transform(self) -> None:
        canvas = ScreenBounds(100, 200, 765, 503)
        region = ScreenBounds(150, 260, 100, 80)
        calls = []

        def grab(**values):
            calls.append(values)
            return Image.new("RGBA", (100, 80), (1, 2, 3, 255))

        image, metadata = capture_canvas_region(canvas, region, grab=grab)

        self.assertEqual("RGB", image.mode)
        self.assertEqual(
            [{"bbox": (150, 260, 250, 340), "all_screens": True}], calls
        )
        self.assertEqual(ScreenBounds(50, 60, 100, 80), metadata.relative_bounds)
        self.assertEqual("windows_imagegrab", metadata.method)

    def test_capture_rejects_any_region_outside_canvas(self) -> None:
        canvas = ScreenBounds(100, 200, 765, 503)
        with self.assertRaisesRegex(ValueError, "inside the verified canvas"):
            capture_canvas_region(canvas, ScreenBounds(99, 200, 10, 10))

    def test_capture_rejects_nonpositive_canvas_or_region_dimensions(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive dimensions"):
            bounded_region_around(
                ScreenBounds(100, 200, 0, 503), ScreenPoint(100, 200)
            )
        with self.assertRaisesRegex(ValueError, "positive dimensions"):
            capture_canvas_region(
                ScreenBounds(100, 200, 765, 503),
                ScreenBounds(100, 200, -1, 10),
            )

    def test_module_has_no_input_or_control_imports(self) -> None:
        path = ROOT / "osrs_bot" / "screen_capture.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        for forbidden in (
            "login",
            "arduino",
            "input_coordinator",
            "runtime",
            "action",
            "safety",
        ):
            self.assertNotIn(forbidden, imports)
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
