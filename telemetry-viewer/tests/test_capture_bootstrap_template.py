import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "capture_bootstrap_template.py"
sys.path.insert(0, str(VIEWER_DIR))

import capture_bootstrap_template as capture


class FakeImage:
    def __init__(self):
        self.crops = []
        self.saved = []

    def crop(self, box):
        self.crops.append(box)
        return self

    def save(self, path):
        self.saved.append(str(path))
        Path(path).write_bytes(b"fake-png")


class CaptureBootstrapTemplateTest(unittest.TestCase):
    def test_parse_region(self):
        self.assertEqual(capture.parse_region("1,2,30,40"), (1, 2, 30, 40))

    def test_capture_with_explicit_region_writes_one_template_only(self):
        image = FakeImage()
        with tempfile.TemporaryDirectory() as temp:
            payload = capture.capture_template(
                name="play_now",
                output_dir=Path(temp),
                region=(10, 20, 30, 40),
                overwrite=False,
                screenshot_func=lambda: image,
                window_finder=lambda _filters: {"matchedWindowTitle": "RuneLite", "warnings": []},
                focus_func=lambda: {"focused": True, "warnings": []},
            )
            files = sorted(os.listdir(temp))

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(files, ["play_now.png"])
        self.assertEqual(image.crops, [(10, 20, 40, 60)])

    def test_capture_requires_overwrite_if_file_exists(self):
        image = FakeImage()
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "continue.png").write_bytes(b"existing")
            payload = capture.capture_template(
                name="continue",
                output_dir=Path(temp),
                region=(1, 2, 3, 4),
                overwrite=False,
                screenshot_func=lambda: image,
                window_finder=lambda _filters: {"matchedWindowTitle": "RuneLite", "warnings": []},
                focus_func=lambda: {"focused": True, "warnings": []},
            )
            content = Path(temp, "continue.png").read_bytes()

        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(content, b"existing")
        self.assertEqual(image.saved, [])

    def test_json_cli_does_not_write_without_region(self):
        with tempfile.TemporaryDirectory() as temp:
            before = set(os.listdir(temp))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--name",
                    "play_now",
                    "--output-dir",
                    temp,
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            after = set(os.listdir(temp))

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "bootstrap_template_capture.v1")
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
