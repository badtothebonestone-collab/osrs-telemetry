import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from input_control.input_geometry import (
    input_geometry_from_status,
    normalize_input_geometry,
    resolve_screen_click_point,
)


def geometry_payload(**overrides):
    payload = {
        "geometryAvailable": True,
        "canvasScreenX": 1000,
        "canvasScreenY": 2000,
        "canvasWidth": 800,
        "canvasHeight": 600,
        "displayScaleX": 2.0,
        "displayScaleY": 2.0,
        "sourceTick": 42,
    }
    payload.update(overrides)
    return payload


class InputGeometryTest(unittest.TestCase):
    def test_normalize_reports_available_canvas_geometry(self):
        geometry = normalize_input_geometry(geometry_payload())

        self.assertTrue(geometry["inputGeometryAvailable"])
        self.assertEqual(geometry["canvasScreenOrigin"], {"x": 1000, "y": 2000})
        self.assertEqual(geometry["canvasSize"], {"width": 800, "height": 600})
        self.assertEqual(geometry["displayScale"], {"x": 2.0, "y": 2.0})
        self.assertEqual(geometry["sourceTick"], 42)

    def test_status_geometry_can_be_extracted_from_daemon_status(self):
        status = {"inputGeometry": geometry_payload(canvasScreenX=11, canvasScreenY=22)}

        geometry = input_geometry_from_status(status)

        self.assertTrue(geometry["inputGeometryAvailable"])
        self.assertEqual(geometry["canvasScreenOrigin"], {"x": 11, "y": 22})

    def test_dynamic_geometry_scales_canvas_point_to_screen(self):
        geometry = normalize_input_geometry(geometry_payload())

        resolution = resolve_screen_click_point(
            {"x": 200, "y": 150},
            click_point_space="canvas",
            input_geometry=geometry,
            source_canvas_size={"width": 800, "height": 600},
        )

        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["method"], "dynamic_input_geometry")
        self.assertEqual(resolution["screenClickPoint"], {"x": 1400, "y": 2300})
        self.assertEqual(resolution["coordinateSpace"], "physical_pyautogui")
        self.assertEqual(resolution["screenPointBeforeScaling"], {"x": 1400, "y": 2300})
        self.assertEqual(resolution["screenPointAfterScaling"], {"x": 1400, "y": 2300})

    def test_dynamic_geometry_scales_when_source_canvas_differs(self):
        geometry = normalize_input_geometry(geometry_payload(displayScaleX=1.0, displayScaleY=1.0))

        resolution = resolve_screen_click_point(
            {"x": 200, "y": 150},
            click_point_space="canvas",
            input_geometry=geometry,
            source_canvas_size={"width": 400, "height": 300},
        )

        self.assertEqual(resolution["screenClickPoint"], {"x": 1400, "y": 2300})
        self.assertEqual(resolution["scale"], {"x": 2.0, "y": 2.0})

    def test_dynamic_geometry_uses_embedded_source_canvas_size(self):
        geometry = normalize_input_geometry(
            geometry_payload(
                canvasScreenX=5776,
                canvasScreenY=39,
                canvasWidth=1834,
                canvasHeight=1205,
                displayScaleX=1.0,
                displayScaleY=1.0,
                sourceCanvasWidth=765,
                sourceCanvasHeight=503,
            )
        )

        resolution = resolve_screen_click_point(
            {"x": 278, "y": 68},
            click_point_space="canvas",
            input_geometry=geometry,
        )

        self.assertEqual(resolution["screenClickPoint"], {"x": 6442, "y": 202})
        self.assertEqual(resolution["sourceCanvasSize"], {"width": 765, "height": 503})

    def test_dynamic_geometry_does_not_double_apply_vm_display_scale(self):
        geometry = normalize_input_geometry(
            geometry_payload(
                canvasScreenX=848,
                canvasScreenY=73,
                canvasWidth=1229,
                canvasHeight=868,
                displayScaleX=1.75,
                displayScaleY=1.75,
                sourceCanvasWidth=765,
                sourceCanvasHeight=503,
            )
        )

        resolution = resolve_screen_click_point(
            {"x": 455, "y": 302},
            click_point_space="canvas",
            input_geometry=geometry,
        )

        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["screenClickPoint"], {"x": 1579, "y": 594})
        self.assertFalse(resolution["displayScaleApplied"])
        self.assertEqual(resolution["coordinateSpace"], "physical_pyautogui")
        self.assertEqual(resolution["windowBoundsSource"], "canvasScreenOrigin")

    def test_dynamic_geometry_scales_vm_logical_window_coordinates_to_physical_screen(self):
        geometry = normalize_input_geometry(
            geometry_payload(
                canvasScreenX=51,
                canvasScreenY=166,
                canvasWidth=765,
                canvasHeight=503,
                displayScaleX=1.75,
                displayScaleY=1.75,
                sourceCanvasWidth=765,
                sourceCanvasHeight=503,
                clientWindowX=40,
                clientWindowY=139,
                clientWindowWidth=1282,
                clientWindowHeight=906,
            )
        )

        resolution = resolve_screen_click_point(
            {"x": 455, "y": 302},
            click_point_space="canvas",
            input_geometry=geometry,
        )

        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["screenClickPoint"], {"x": 886, "y": 819})
        self.assertTrue(resolution["displayScaleApplied"])
        self.assertEqual(resolution["coordinateSpace"], "scaled_logical_to_physical")
        self.assertEqual(resolution["screenPointBeforeScaling"], {"x": 506, "y": 468})
        self.assertEqual(resolution["screenPointAfterScaling"], {"x": 886, "y": 819})
        self.assertEqual(resolution["windowBoundsSource"], "clientWindowBounds")
        self.assertEqual(resolution["canvasBoundsSource"], "canvasSize/sourceCanvasSize")

    def test_dynamic_geometry_scales_awt_window_coordinates_even_when_canvas_is_expanded(self):
        geometry = normalize_input_geometry(
            geometry_payload(
                canvasScreenX=51,
                canvasScreenY=166,
                canvasWidth=1229,
                canvasHeight=868,
                displayScaleX=1.75,
                displayScaleY=1.75,
                sourceCanvasWidth=765,
                sourceCanvasHeight=503,
                clientWindowX=40,
                clientWindowY=139,
                clientWindowWidth=1282,
                clientWindowHeight=906,
            )
        )

        resolution = resolve_screen_click_point(
            {"x": 244, "y": 132},
            click_point_space="canvas",
            input_geometry=geometry,
        )

        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["screenClickPoint"], {"x": 775, "y": 689})
        self.assertTrue(resolution["displayScaleApplied"])
        self.assertEqual(resolution["coordinateSpace"], "scaled_logical_to_physical")
        self.assertEqual(resolution["screenPointBeforeScaling"], {"x": 443, "y": 394})
        self.assertEqual(resolution["screenPointAfterScaling"], {"x": 775, "y": 689})

    def test_dynamic_geometry_scales_current_vm_runelite_resource_target(self):
        geometry = normalize_input_geometry(
            geometry_payload(
                canvasScreenX=20,
                canvasScreenY=68,
                canvasWidth=1229,
                canvasHeight=868,
                displayScaleX=1.75,
                displayScaleY=1.75,
                sourceCanvasWidth=765,
                sourceCanvasHeight=503,
                clientWindowX=9,
                clientWindowY=41,
                clientWindowWidth=1282,
                clientWindowHeight=906,
            )
        )

        resolution = resolve_screen_click_point(
            {"x": 436, "y": 214},
            click_point_space="canvas",
            input_geometry=geometry,
        )

        self.assertEqual(resolution["status"], "PASS")
        self.assertEqual(resolution["screenPointBeforeScaling"], {"x": 720, "y": 437})
        self.assertEqual(resolution["screenClickPoint"], {"x": 1261, "y": 765})
        self.assertTrue(resolution["displayScaleApplied"])
        self.assertEqual(resolution["coordinateSpace"], "scaled_logical_to_physical")

    def test_canvas_point_outside_dynamic_canvas_fails(self):
        geometry = normalize_input_geometry(geometry_payload(displayScaleX=1.0, displayScaleY=1.0))

        resolution = resolve_screen_click_point(
            {"x": 900, "y": 150},
            click_point_space="canvas",
            input_geometry=geometry,
            source_canvas_size={"width": 800, "height": 600},
        )

        self.assertEqual(resolution["status"], "FAIL")
        self.assertIn("screen_click_point", resolution["missingCapabilities"])

    def test_missing_geometry_requests_backend_fallback(self):
        resolution = resolve_screen_click_point(
            {"x": 200, "y": 150},
            click_point_space="canvas",
            input_geometry={},
            source_canvas_size={"width": 800, "height": 600},
        )

        self.assertEqual(resolution["status"], "WARN")
        self.assertEqual(resolution["method"], "backend_fallback_required")
        self.assertIsNone(resolution["screenClickPoint"])


if __name__ == "__main__":
    unittest.main()
