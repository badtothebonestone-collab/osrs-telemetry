import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import safe_aimpoint_core


class SafeAimpointCoreTest(unittest.TestCase):
    def test_projection_sentinel_is_classified_separately_from_edge_clipping(self):
        target = {
            "targetName": "Tree",
            "id": 1278,
            "worldX": 3212,
            "worldY": 3232,
            "plane": 0,
            "projectionMode": "live_object_pending",
            "aimPoint": {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"},
            "bounds": {"x": 2147483647, "y": 2147483647, "width": 1, "height": 1},
        }

        safe = safe_aimpoint_core.safe_aimpoint_for_target(target)
        status = safe_aimpoint_core.resource_projection_status(target, safe_aimpoint=safe)

        self.assertEqual(safe["status"], "FAIL")
        self.assertEqual(safe["rejectionReason"], "projection_sentinel")
        self.assertEqual(status["schema"], "resource_projection_status.v1")
        self.assertEqual(status["classification"], "projection_sentinel")
        self.assertTrue(status["projectionSentinel"])
        self.assertFalse(status["edgeClipped"])
        self.assertTrue(status["recoverySuggested"])

    def test_raw_center_outside_viewport_uses_clipped_visible_interior(self):
        target = {
            "aimPoint": {"canvasX": 770, "canvasY": 250, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 748, "y": 220, "width": 40, "height": 60},
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(
            target,
            source_canvas_size={"width": 765, "height": 503},
            viewport={"x": 0, "y": 0, "width": 765, "height": 503},
            edge_margin_px=6,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source"], "clippedClickboxInterior")
        self.assertTrue(result["insideViewport"])
        self.assertLessEqual(result["canvasX"], 759)
        self.assertGreaterEqual(result["distanceToViewportEdgePx"], 6)
        self.assertEqual(result["rawAimPoint"]["x"], 770)

    def test_no_visible_interactable_area_rejects_candidate(self):
        target = {
            "aimPoint": {"canvasX": 830, "canvasY": 250, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 820, "y": 220, "width": 40, "height": 60},
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(
            target,
            source_canvas_size={"width": 765, "height": 503},
            viewport={"x": 0, "y": 0, "width": 765, "height": 503},
            edge_margin_px=6,
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["rejectionReason"], "no_visible_interactable_geometry")
        self.assertTrue(result["validButUnsafe"])
        self.assertIn("centerOffViewport", result["unsafeReasons"])
        self.assertIn("noVisibleInteractableGeometry", result["unsafeReasons"])

    def test_point_on_viewport_edge_is_inset(self):
        target = {
            "aimPoint": {"canvasX": 0, "canvasY": 20, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 0, "y": 10, "width": 18, "height": 20},
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(
            target,
            source_canvas_size={"width": 765, "height": 503},
            edge_margin_px=6,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertGreaterEqual(result["canvasX"], 6)
        self.assertGreaterEqual(result["canvasY"], 6)

    def test_nested_geometry_clickbox_provides_sampled_aimpoints(self):
        target = {
            "aimPoint": {"x": 470, "y": 344},
            "geometry": {
                "clickboxBounds": {"x": 382, "y": 249, "w": 175, "h": 190},
            },
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(target)

        self.assertEqual(result["status"], "PASS")
        self.assertGreaterEqual(len(result["sampledAimpoints"]), 8)
        self.assertIn({"x": 470, "y": 316}, result["sampledAimpoints"])

    def test_geometry_summary_bounds_provide_sampled_aimpoints(self):
        target = {
            "aimPoint": {"x": 252, "y": 134, "source": "clickboxBounds"},
            "geometrySummary": {
                "bounds": {"x": 218, "y": 89, "w": 69, "h": 91},
            },
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(target)

        self.assertEqual(result["status"], "PASS")
        self.assertGreaterEqual(len(result["sampledAimpoints"]), 8)
        self.assertIn({"x": 242, "y": 134}, result["sampledAimpoints"])

    def test_ui_blocked_region_is_rejected(self):
        target = {
            "aimPoint": {"canvasX": 100, "canvasY": 120, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 90, "y": 110, "width": 20, "height": 20},
            "uiBlocked": True,
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(target, source_canvas_size={"width": 765, "height": 503})

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["rejectionReason"], "ui_blocked")
        self.assertTrue(result["validButUnsafe"])
        self.assertIn("uiBlocked", result["unsafeReasons"])

    def test_world_model_viewport_clips_full_canvas_service_point(self):
        target = {
            "aimPoint": {"canvasX": 412, "canvasY": 350, "source": "clickboxCenter"},
            "clickboxBounds": {"x": 388, "y": 330, "width": 48, "height": 40},
        }

        result = safe_aimpoint_core.safe_aimpoint_for_target(
            target,
            source_canvas_size={"width": 765, "height": 503},
            viewport={
                "viewportWidth": 512,
                "viewportHeight": 334,
                "viewportXOffset": 4,
                "viewportYOffset": 4,
                "canvasWidth": 765,
                "canvasHeight": 503,
            },
            edge_margin_px=6,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source"], "clippedClickboxInterior")
        self.assertFalse(result["rawCenterInsideViewport"])
        self.assertLess(result["canvasY"], 338)
        self.assertIn("centerOffViewport", result["unsafeReasons"])


if __name__ == "__main__":
    unittest.main()
