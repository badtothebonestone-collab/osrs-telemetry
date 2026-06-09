import json
import subprocess
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import task_script_api
from input_control import click_planner


def profile(activity: str = "woodcutting") -> dict:
    return {
        "schema": "human_click_profile_compact.v1",
        "status": "PASS",
        "recordingCount": 4,
        "landing": {
            "medianAimDistancePx": 24,
            "p75AimDistancePx": 36,
            "p90AimDistancePx": 48,
            "aimDistanceBucketsPx": {"le12": 1, "le30": 3, "le80": 1, "gt80": 0, "unknown": 0},
        },
        "taskProfile": {
            "activity": activity,
            "cameraBeforeClickFrequency": 0.25,
            "menuRowSelectionCount": 2,
            "rightClickMenuOpenCount": 1,
            "strongOrMediumTargetRate": 0.9,
        },
        "camera": {"middleMouseDragCount": 1},
        "warnings": [],
        "missingCapabilities": [],
    }


def tree_target(**overrides) -> dict:
    value = {
        "name": "Tree",
        "kind": "object",
        "targetQuality": "strong",
        "onScreen": True,
        "geometryAvailable": True,
        "aimPoint": {"x": 100, "y": 120},
        "clickboxBounds": {"x": 80, "y": 100, "width": 45, "height": 45},
        "world": {"worldX": 3195, "worldY": 3244, "plane": 0},
    }
    value.update(overrides)
    return value


class HumanClickPlanningTest(unittest.TestCase):
    def test_woodcutting_target_profile_produces_non_center_planned_point(self):
        plan = click_planner.build_click_plan(
            {"humanClickProfile": profile()},
            target=tree_target(),
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )

        self.assertEqual(plan["status"], "PASS")
        self.assertEqual(plan["target"]["name"], "Tree")
        self.assertNotEqual(plan["aim"]["basePoint"], plan["aim"]["plannedPoint"])
        self.assertIn("profile_informed_non_center_point", plan["reasons"])

    def test_missing_geometry_returns_warn_without_fake_coordinates(self):
        plan = click_planner.build_click_plan(
            {"humanClickProfile": profile()},
            target=tree_target(aimPoint=None, geometryAvailable=False),
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )

        self.assertEqual(plan["status"], "WARN")
        self.assertEqual(plan["aim"]["plannedPoint"], {"x": None, "y": None})
        self.assertIn("geometry_missing", plan["readiness"]["blockedReasons"])

    def test_nested_recovered_tree_geometry_produces_plan_point(self):
        plan = click_planner.build_click_plan(
            {"humanClickProfile": profile(), "hover": {"hoverOption": "Chop down", "hoverTarget": "Tree"}},
            target={
                "name": "Tree",
                "kind": "object",
                "targetQuality": "strong",
                "onScreen": True,
                "geometry": {"aimPoint": {"x": 489, "y": 234}, "clickbox": None},
            },
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )

        self.assertEqual(plan["status"], "PASS")
        self.assertEqual(plan["aim"]["basePoint"], {"x": 489, "y": 234})
        self.assertNotEqual(plan["aim"]["plannedPoint"], {"x": 489, "y": 234})
        self.assertTrue(plan["readiness"]["geometryAvailable"])

    def test_hover_and_menu_evidence_increase_confidence(self):
        base = click_planner.build_click_plan(
            {"humanClickProfile": profile()},
            target=tree_target(),
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )
        with_evidence = click_planner.build_click_plan(
            {
                "humanClickProfile": profile(),
                "hover": {"confirmed": True},
                "menu": {"option": "Chop down", "target": "Tree"},
            },
            target=tree_target(),
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )

        self.assertGreater(with_evidence["confidence"], base["confidence"])
        self.assertIn("hover_evidence_available", with_evidence["reasons"])
        self.assertIn("menu_evidence_available", with_evidence["reasons"])

    def test_conflicting_hover_menu_and_geometry_warns(self):
        plan = click_planner.build_click_plan(
            {
                "humanClickProfile": profile(),
                "hover": {"hoverOption": "Chop down", "hoverTarget": "<col=ffff>Tree"},
                "menu": {"option": "Chop down", "target": "Tree"},
            },
            target={
                "name": "Gate",
                "kind": "route",
                "action": "Close",
                "targetQuality": "weak",
                "onScreen": True,
                "geometryAvailable": True,
                "aimPoint": {"x": 342, "y": 4},
                "clickboxBounds": {"x": 333, "y": -8, "width": 16, "height": 13},
            },
            action="Chop down",
            activity="woodcutting",
            human_profile=profile(),
        )

        self.assertEqual(plan["status"], "WARN")
        self.assertIn("target_evidence_conflict", plan["readiness"]["blockedReasons"])
        self.assertIn("target_action_conflict", plan["readiness"]["blockedReasons"])
        self.assertFalse(plan["execution"]["clickNowAllowedByPlanner"])

    def test_inventory_full_blocks_chop_and_points_to_route_to_bank(self):
        plan = task_script_api.get_human_click_plan(
            target=tree_target(),
            action="Chop down",
            activity="woodcutting",
            source={
                "humanClickProfile": profile(),
                "woodcuttingLoopLifecycle": {
                    "loopState": "inventory_full",
                },
            },
        )

        self.assertEqual(plan["status"], "WARN")
        self.assertEqual(plan["action"], "route_to_bank")
        self.assertIn("inventory_full_route_to_bank_required", plan["readiness"]["blockedReasons"])

    def test_deposit_complete_blocks_another_deposit_click(self):
        plan = task_script_api.get_human_click_plan(
            target={"name": "Deposit inventory", "aimPoint": {"x": 300, "y": 220}, "geometryAvailable": True, "onScreen": True},
            action="Deposit",
            activity="banking",
            source={
                "humanClickProfile": profile("banking"),
                "depositResult": {"depositComplete": True},
            },
        )

        self.assertEqual(plan["status"], "WARN")
        self.assertEqual(plan["action"], "route_to_woodcutting_area")
        self.assertIn("deposit_complete_route_to_trees_required", plan["readiness"]["blockedReasons"])

    def test_off_route_blocks_normal_route_click_plan(self):
        plan = click_planner.build_click_plan(
            {
                "humanClickProfile": profile("route_traversal"),
                "routeMonitor": {"routeState": "off_route", "offRoute": True},
            },
            target=tree_target(name="Staircase", targetQuality="medium"),
            action="Climb-up",
            activity="route_traversal",
            human_profile=profile("route_traversal"),
        )

        self.assertEqual(plan["status"], "FAIL")
        self.assertIn("route_monitor_off_route", plan["readiness"]["blockedReasons"])

    def test_menu_row_geometry_uses_menu_row_plan(self):
        plan = click_planner.build_click_plan(
            {"humanClickProfile": profile("menu_interaction"), "menu": {"option": "Climb-up"}},
            target={
                "name": "Climb-up Staircase",
                "targetQuality": "medium",
                "rowCanvasGeometry": {
                    "point": {"x": 410, "y": 328},
                    "menuBounds": {"x": 360, "y": 280, "width": 130, "height": 90},
                },
            },
            action="Climb-up",
            activity="menu_interaction",
            human_profile=profile("menu_interaction"),
        )

        self.assertEqual(plan["aim"]["basePointSource"], "menu_row_geometry")
        self.assertEqual(plan["status"], "PASS")
        self.assertNotEqual(plan["aim"]["basePoint"], plan["aim"]["plannedPoint"])

    def test_camera_before_click_profile_can_recommend_camera_adjust_first(self):
        camera_profile = profile()
        camera_profile["taskProfile"]["cameraBeforeClickFrequency"] = 0.8
        plan = click_planner.build_click_plan(
            {"humanClickProfile": camera_profile},
            target=tree_target(aimPoint=None, onScreen=False, geometryAvailable=False),
            action="Chop down",
            activity="woodcutting",
            human_profile=camera_profile,
        )

        self.assertEqual(plan["status"], "WARN")
        self.assertEqual(plan["recommendedPreAction"], "camera_adjust_first")
        self.assertIn("camera_adjust_first", plan["readiness"]["blockedReasons"])

    def test_task_script_api_get_next_click_plan_returns_compact_plan(self):
        plan = task_script_api.get_next_click_plan(
            {
                "target": tree_target(),
                "action": "Chop down",
                "humanClickProfile": profile(),
            }
        )

        self.assertEqual(plan["schema"], "human_click_plan.v1")
        self.assertEqual(plan["action"], "Chop down")

    def test_execute_next_action_dry_run_click_plan_prints_plan(self):
        result = subprocess.run(
            [sys.executable, str(VIEWER_DIR / "execute_next_action.py"), "--dry-run-click-plan", "--json", "--timeout", "0.5"],
            cwd=str(VIEWER_DIR.parent),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertIn(result.returncode, {0, 1})
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema"], "execute_next_action_click_plan.v1")
        self.assertIn(payload["clickPlan"]["status"], {"PASS", "WARN", "FAIL"})


if __name__ == "__main__":
    unittest.main()
