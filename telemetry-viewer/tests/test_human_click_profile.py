import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import human_click_profile
import task_script_api
import update_project_knowledge


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def make_recording(root: Path, name: str = "recording") -> Path:
    recording = root / name
    recording.mkdir(parents=True)
    write_json(
        recording / "summary.json",
        {
            "recording_id": name,
            "status": "PASS",
            "label": name,
        },
    )
    write_json(
        recording / "input_action_summary.json",
        {
            "schema": "input_action_summary.v1",
            "rawOsClickCount": 4,
            "eligibleGameActionClickCount": 2,
            "targetRelativeClickCount": 2,
            "rightClickMenuOpenCount": 1,
            "menuSelectionClickCount": 1,
            "classificationCounts": {"object_action_click": 1, "menu_selection_click": 1, "right_click_menu_open": 1},
        },
    )
    write_jsonl(
        recording / "target_match_quality.jsonl",
        [
            {
                "schema": "target_match_quality.v1",
                "eventSeq": 1,
                "classification": "object_action_click",
                "quality": "strong",
                "score": 0.95,
                "matchedTarget": {"name": "Tree", "action": "Chop down"},
                "geometry": {"insideClickbox": False, "clickboxAvailable": True, "distanceFromAimPointPx": 100, "targetOnScreen": True},
                "postClickResult": {"animationStarted": True, "matchedExpectedOutcome": True},
                "warnings": ["click_outside_clickbox"],
            },
            {
                "schema": "target_match_quality.v1",
                "eventSeq": 2,
                "classification": "menu_selection_click",
                "quality": "medium",
                "score": 0.75,
                "matchedTarget": {"name": "Staircase", "action": "Climb-down"},
                "geometry": {"insideClickbox": True, "clickboxAvailable": True, "distanceFromAimPointPx": 20, "targetOnScreen": True},
                "menuSelectionQuality": {"insideRowBounds": True, "rowBounds": {"x": 1}, "rowCenterDistancePx": 8, "selectedOption": "Climb-down"},
                "postClickResult": {"planeChanged": True},
                "warnings": [],
            },
        ],
    )
    write_json(
        recording / "target_match_summary.json",
        {
            "schema": "target_match_summary.v1",
            "targetRelativeClickCount": 2,
            "strongMatchCount": 1,
            "mediumMatchCount": 1,
            "weakMatchCount": 0,
            "qualityCounts": {"strong": 1, "medium": 1, "weak": 0, "unmatched": 0},
        },
    )
    write_jsonl(
        recording / "input_action_classifications.jsonl",
        [
            {
                "schema": "input_action_classification.v1",
                "eventSeq": 1,
                "eventKind": "click",
                "classification": "object_action_click",
                "menuContext": {"hoverOption": "Chop down", "hoverTarget": "Tree"},
                "targetContext": {"targetName": "Tree", "targetAction": "Chop down"},
            }
        ],
    )
    write_jsonl(
        recording / "joined_input_telemetry.jsonl",
        [
            {
                "inputEvent": {"event_seq": 1, "kind": "click"},
                "clickAnalysis": {"hoverBeforeClick": {"durationMs": 125}},
            }
        ],
    )
    write_jsonl(
        recording / "input_events.jsonl",
        [
            {"kind": "mouse_move", "event_seq": 1, "elapsed_seconds": 0.0, "screen_x": 0, "screen_y": 0},
            {"kind": "mouse_move", "event_seq": 2, "elapsed_seconds": 0.1, "screen_x": 30, "screen_y": 40},
            {"kind": "mouse_move", "event_seq": 3, "elapsed_seconds": 0.5, "screen_x": 60, "screen_y": 40},
        ],
    )
    write_json(
        recording / "camera_behavior_summary.json",
        {
            "schema": "camera_behavior_summary.v1",
            "status": "PASS",
            "totalCameraSegments": 1,
            "middleMouseDragSegments": 1,
            "arrowKeyCameraSegments": 0,
            "cameraBeforeClickCount": 1,
            "cameraBeforeStrongOrMediumClickCount": 1,
            "segments": [
                {
                    "segmentId": "cam_001",
                    "source": "middle_mouse_drag",
                    "durationMs": 500,
                    "endTime": 0.5,
                    "deltaYaw": -82,
                    "deltaPitch": -2,
                    "mouseDrag": {"start": {"x": 100, "y": 100}, "end": {"x": 20, "y": 100}, "dx": -80, "dy": 0},
                    "nextClick": {"event_seq": 1, "elapsed_seconds": 0.7},
                    "nextClickTargetName": "Tree",
                    "nextClickTargetAction": "Chop down",
                    "nextClickTargetQuality": "strong",
                }
            ],
        },
    )
    write_json(
        recording / "woodcutting_lifecycle.json",
        {
            "schema": "woodcutting_lifecycle.v1",
            "status": "PASS",
            "phase": "inventory_full",
            "confidence": 0.95,
            "clicks": {"freshChopClickCount": 1, "inputActionChopClickCount": 1, "inputTreeTargetEvidenceCount": 1},
            "inventory": {"normalLogsGained": 3, "inventoryFull": True},
            "animation": {"activeSnapshotCount": 2},
        },
    )
    write_json(
        recording / "banking_lifecycle.json",
        {
            "schema": "banking_lifecycle.v1",
            "status": "PASS",
            "phase": "complete",
            "confidence": 0.95,
            "bankContainerDeltaAvailable": True,
            "depositConfirmationLevel": "bank_container_delta_confirmed",
            "bank": {"openSeen": True, "bankUiPresent": True, "containerAvailable": True},
            "deposit": {"items": [{"id": 1511, "name": "Logs", "quantity": 3}]},
        },
    )
    write_json(
        recording / "traversal_lifecycle.json",
        {
            "schema": "traversal_lifecycle.v1",
            "status": "PASS",
            "routeName": "Bank_to_Woodcutting_area",
            "phase": "arrived",
            "confidence": 0.8,
            "routeSegmentCount": 5,
            "successfulSegmentCount": 5,
            "partialSegmentCount": 0,
            "reviewEvidenceCount": 1,
            "movement": {"planeChanges": [{"before": 1, "after": 0}], "distanceApprox": 42},
            "routeSegments": [{"segmentIndex": 1, "segmentType": "area_start", "label": "Start"}],
        },
    )
    return recording


class HumanClickProfileTest(unittest.TestCase):
    def test_aggregates_target_quality_counts_and_aim_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = make_recording(Path(tmp))
            profile = human_click_profile.analyze_recordings([recording])
        self.assertEqual(profile["status"], "PASS")
        self.assertEqual(profile["clicks"]["strongTargetClicks"], 1)
        self.assertEqual(profile["clicks"]["mediumTargetClicks"], 1)
        self.assertEqual(profile["landing"]["aimDistanceBucketsPx"]["gt80"], 1)
        self.assertEqual(profile["landing"]["aimDistanceBucketsPx"]["le30"], 1)

    def test_counts_menu_camera_and_mouse_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = make_recording(Path(tmp))
            profile = human_click_profile.analyze_recordings([recording])
        self.assertEqual(profile["clicks"]["menuRowSelectionCount"], 1)
        self.assertEqual(profile["camera"]["cameraSegmentCount"], 1)
        self.assertEqual(profile["camera"]["middleMouseDragCount"], 1)
        self.assertGreater(profile["mousePath"]["movementSegments"], 0)

    def test_collects_imperfect_successful_clicks(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = make_recording(Path(tmp))
            profile = human_click_profile.analyze_recordings([recording])
        self.assertEqual(profile["imperfectSuccessfulClickCount"], 1)
        example = profile["imperfectSuccessfulClicks"][0]
        self.assertEqual(example["target"], "Tree")
        self.assertEqual(example["whyItStillSucceeded"], "matched_expected_postcondition")

    def test_aggregate_imperfect_success_count_is_not_example_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recordings = [make_recording(root, f"recording_{index:02d}") for index in range(14)]
            profile = human_click_profile.analyze_recordings(recordings)
        self.assertEqual(profile["imperfectSuccessfulClickCount"], 14)
        self.assertLessEqual(len(profile["imperfectSuccessfulClicks"]), 12)

    def test_task_buckets_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = make_recording(Path(tmp))
            profile = human_click_profile.analyze_recordings([recording])
        self.assertIn("woodcutting", profile["taskProfiles"])
        self.assertIn("banking", profile["taskProfiles"])
        self.assertIn("route_traversal", profile["taskProfiles"])
        self.assertEqual(profile["taskProfiles"]["woodcutting"]["woodcutting"]["inputActionChopClickCount"], 1)
        self.assertEqual(profile["taskProfiles"]["banking"]["banking"]["bankUiPresentRecordings"], 1)
        self.assertEqual(profile["taskProfiles"]["route_traversal"]["traversal"]["planeChangeCount"], 1)

    def test_missing_optional_artifacts_are_warn_not_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "minimal"
            recording.mkdir()
            write_json(recording / "summary.json", {"status": "PASS"})
            profile = human_click_profile.analyze_recordings([recording])
        self.assertEqual(profile["status"], "WARN")
        self.assertIn("target_match_quality", profile["missingCapabilities"])

    def test_writes_profile_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_recording(root)
            profile = human_click_profile.analyze_recordings([recording])
            json_path = human_click_profile.write_profile(profile, root / "profile.json")
            md_path = human_click_profile.write_markdown(profile, root / "profile.md")
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertIn("Human Click Profile", md_path.read_text(encoding="utf-8"))

    def test_analyzer_flag_writes_profile_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_recording(root)
            exit_code = analyze_manual_recording.main([str(recording), "--human-click-profile", "--out", str(root / "out.json")])
            self.assertEqual(exit_code, 0)
            summary = json.loads((recording / "summary.json").read_text(encoding="utf-8"))
            self.assertIn(summary["humanClickProfileStatus"], {"PASS", "WARN"})
            self.assertTrue((recording / "human_click_profile.json").exists())

    def test_context_service_compact_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_recording(root)
            profile = human_click_profile.analyze_recordings([recording])
            profile_path = human_click_profile.write_profile(profile, root / "human_click_profile.json")
            response = context_service.build_context_response(
                {
                    "schema": "context_status.v1",
                    "status": {"generatedAtUtc": "2026-06-07T00:00:00Z"},
                    "baseline": {"generatedAtUtc": "2026-06-07T00:00:00Z"},
                    "candidates": [],
                    "warnings": [],
                    "missingFields": [],
                },
                {
                    "schema": context_service.REQUEST_SCHEMA,
                    "needs": ["human_click_profile", "click_landing_profile", "camera_action_profile"],
                    "humanClickProfilePath": str(profile_path),
                    "responseMode": "compact",
                },
            )
        self.assertIn(response["status"], {"PASS", "WARN"})
        self.assertEqual(response["humanClickProfile"]["recordingCount"], 1)
        self.assertEqual(response["clickLandingProfile"]["medianAimDistancePx"], 60.0)

    def test_task_script_api_compact_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recording = make_recording(root)
            profile = human_click_profile.analyze_recordings([recording])
            profile_path = human_click_profile.write_profile(profile, root / "human_click_profile.json")
            compact = task_script_api.get_human_click_profile(profile_path)
            bucket = task_script_api.get_task_click_profile("woodcutting", profile_path)
        self.assertEqual(compact["recordingCount"], 1)
        self.assertEqual(bucket["activity"], "woodcutting")

    def test_knowledge_updater_indexes_capability(self):
        model = update_project_knowledge.build_project_knowledge()
        capability_ids = {item["id"] for item in model["capabilities"]}
        api_families = {item["family"] for item in model["apiDataPaths"]}
        self.assertIn("human_click_profile", capability_ids)
        self.assertIn("human_click_profile", api_families)


if __name__ == "__main__":
    unittest.main()
