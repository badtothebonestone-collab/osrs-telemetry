from __future__ import annotations

import ast
import math
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from osrs_bot.model import ScreenBounds, ScreenPoint
from osrs_bot.vision import VisionCropTransform, VisionEvidence


ROOT = Path(__file__).parents[1]


def _transform() -> VisionCropTransform:
    return VisionCropTransform(
        window_screen_bounds=ScreenBounds(100, 100, 1000, 700),
        canvas_screen_bounds=ScreenBounds(150, 150, 800, 500),
        crop_screen_bounds=ScreenBounds(250, 200, 320, 240),
        model_input_width=640,
        model_input_height=480,
    )


class VisionEvidenceTests(unittest.TestCase):
    def test_frozen_advisory_evidence_has_exact_crop_transform(self) -> None:
        evidence = VisionEvidence(
            captured_at=datetime.now(timezone.utc),
            transform=_transform(),
            model_name="future-detector",
            model_version="not-installed-v1",
            class_name="visual-occlusion",
            confidence=0.75,
            occlusion_status="possible",
            image_quality_status="clear",
            model_bounds=ScreenBounds(10, 20, 100, 80),
        )

        self.assertFalse(evidence.authoritative)
        self.assertFalse(evidence.may_authorize_input)
        self.assertEqual(2.0, evidence.transform.screen_to_model_scale_x)
        self.assertEqual(2.0, evidence.transform.screen_to_model_scale_y)
        payload = evidence.to_dict()
        self.assertFalse(payload["authoritative"])
        self.assertFalse(payload["mayAuthorizeInput"])
        self.assertEqual(-250, payload["transform"]["screenToModel"]["offsetX"])
        with self.assertRaises(FrozenInstanceError):
            evidence.class_name = "changed"  # type: ignore[misc]

    def test_mask_evidence_is_deeply_immutable_and_bounded(self) -> None:
        mask = (ScreenPoint(1, 1), ScreenPoint(20, 1), ScreenPoint(10, 20))
        evidence = VisionEvidence(
            datetime.now(timezone.utc),
            _transform(),
            "future-segmenter",
            "v1",
            "prompt",
            0.5,
            "none",
            "clear",
            model_mask=mask,
        )
        self.assertIs(mask, evidence.model_mask)
        self.assertEqual(3, len(evidence.to_dict()["modelMask"]))

    def test_serialized_capture_timestamp_is_normalized_to_utc(self) -> None:
        captured = datetime(2026, 7, 10, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
        evidence = VisionEvidence(
            captured,
            _transform(),
            "future-detector",
            "v1",
            "occlusion",
            0.5,
            "none",
            "clear",
            model_bounds=ScreenBounds(1, 1, 2, 2),
        )

        self.assertEqual(
            "2026-07-10T17:00:00+00:00",
            evidence.to_dict()["capturedAtUtc"],
        )

    def test_invalid_geometry_confidence_and_shape_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            VisionCropTransform(
                ScreenBounds(0, 0, 100, 100),
                ScreenBounds(0, 0, 100, 100),
                ScreenBounds(90, 90, 20, 20),
                100,
                100,
            )
        for confidence in (-0.1, 1.1, math.nan, math.inf, True):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                VisionEvidence(
                    datetime.now(timezone.utc),
                    _transform(),
                    "model",
                    "v1",
                    "class",
                    confidence,
                    "none",
                    "clear",
                    model_bounds=ScreenBounds(1, 1, 2, 2),
                )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            VisionEvidence(
                datetime.now(timezone.utc),
                _transform(),
                "model",
                "v1",
                "class",
                0.5,
                "none",
                "clear",
            )

    def test_module_has_no_model_runtime_or_input_dependency(self) -> None:
        source = (ROOT / "osrs_bot" / "vision.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        for forbidden in (
            "runtime",
            "task",
            "safety",
            "input_coordinator",
            "arduino",
            "torch",
            "yolo",
        ):
            self.assertNotIn(forbidden, imports)
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
