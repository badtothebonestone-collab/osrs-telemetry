import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = VIEWER_DIR.parents[0]
sys.path.insert(0, str(VIEWER_DIR))

import analyze_manual_recording
import context_service
import woodcutting_lifecycle


START = datetime(2026, 6, 3, 3, 34, 44, tzinfo=timezone.utc)


def iso(offset_seconds: float) -> str:
    return (START + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


def inventory(logs: int, free: int) -> dict:
    filled = 28 - free
    items = [{"slot": index, "itemId": 1511, "quantity": 1} for index in range(logs)]
    return {
        "known": True,
        "itemsKnown": True,
        "freeSlots": free,
        "filledSlots": filled,
        "inventoryFull": free == 0,
        "items": items,
        "resourceCounts": {
            "normal_logs": {
                "displayName": "Logs",
                "itemIds": [1511],
                "count": logs,
            }
        },
    }


def tree_object(x: int = 3200, y: int = 3200, object_id: int = 1276) -> dict:
    return {
        "objectKey": f"0:{x}:{y}:tree",
        "kind": "GAME_OBJECT",
        "id": object_id,
        "objectName": "Tree",
        "actions": ["Chop down"],
        "worldX": x,
        "worldY": y,
        "plane": 0,
        "distanceToPlayer": 1,
        "resourceType": "basic_tree",
        "projection": {
            "geometryAvailable": True,
            "onScreen": True,
            "actionableByCanvas": True,
            "aimPoint": {"canvasX": 200, "canvasY": 150, "source": "canvasLocation"},
        },
    }


def click(option: str, target: str, tick: int, *, timestamp: str | None = None, identifier: int = 1276) -> dict:
    return {
        "schema": "plugin_menu_option_clicked.v1",
        "option": option,
        "target": target,
        "type": "GAME_OBJECT_FIRST_OPTION" if option == "Chop down" else "CC_OP",
        "identifier": identifier,
        "itemId": -1 if option == "Chop down" else 1511,
        "param0": 42,
        "param1": 41,
        "gameTickAtSample": tick,
        "timestampUtc": timestamp or iso(tick - 100),
        "mouseCanvasX": 200,
        "mouseCanvasY": 150,
    }


def snapshot(tick: int, elapsed: float, logs: int, free: int, *, animation: int = -1, last_click: dict | None = None, wood_state: str = "target_depleted") -> dict:
    status = {
        "latestTickProcessed": tick,
        "compactPacketLastSequence": tick * 10,
        "clientTickHot": {
            "gameTickAtSample": tick,
            "lastMenuOptionClicked": last_click,
            "postMenuSort": {
                "menuOpen": False,
                "entries": [
                    {"option": "Walk here", "target": "", "type": "WALK"},
                    {"option": "Chop down", "target": "<col=ffff>Tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1276},
                ],
            },
        },
        "worldModelResourceObjectCensus": {"objects": [tree_object()]},
    }
    activity = {
        "latestTick": tick,
        "inventoryState": inventory(logs, free),
        "activityState": {
            "apparentState": "animating" if animation == 879 else "idle",
            "apparentTask": "woodcutting_possible" if animation == 879 else "unknown",
        },
        "woodcuttingState": {
            "woodcuttingState": "inventory_full" if free == 0 else wood_state,
            "evidence": ["inventory freeSlots=0"] if free == 0 else ["synthetic target depleted"],
        },
    }
    baseline = {
        "latestTick": tick,
        "gameState": "LOGGED_IN",
        "player": {"worldX": 3190, "worldY": 3240, "plane": 0, "animation": animation, "poseAnimation": 808},
        "inventory": {"freeSlots": free, "filledSlots": 28 - free, "inventoryFull": free == 0},
    }
    return {
        "schema_version": "manual_telemetry_event.v1",
        "event_type": "source_snapshot",
        "session_id": "s1",
        "wall_time_utc": iso(elapsed),
        "elapsed_seconds": elapsed,
        "latest_tick": tick,
        "sources": [
            {"name": "baseline", "data": baseline, "parse_status": "ok"},
            {"name": "status", "data": status, "parse_status": "ok"},
            {"name": "activity", "data": activity, "parse_status": "ok"},
        ],
    }


def synthetic_events() -> list[dict]:
    events = [
        {
            "schema_version": "manual_telemetry_event.v1",
            "event_type": "recording_start",
            "session_id": "s1",
            "wall_time_utc": iso(0),
            "elapsed_seconds": 0,
            "label": "tree cutting",
            "description": "synthetic tree cutting",
        },
        snapshot(100, 1, 0, 16, last_click=click("Drop", "<col=ff9040>Logs</col>", 80, timestamp=iso(-30), identifier=1), wood_state="inventory_changed"),
    ]
    for index in range(1, 17):
        click_tick = 100 + index * 10
        chop = click("Chop down", "<col=ffff>Tree", click_tick, timestamp=iso(index * 5))
        events.append(snapshot(click_tick, index * 5.0, index - 1, 17 - index, animation=879, last_click=chop))
        events.append(snapshot(click_tick + 3, index * 5.0 + 1.0, index, 16 - index, animation=-1, last_click=chop))
    events.append(
        {
            "schema_version": "manual_telemetry_event.v1",
            "event_type": "recording_stop",
            "session_id": "s1",
            "wall_time_utc": iso(90),
            "elapsed_seconds": 90,
            "duration_seconds": 90,
        }
    )
    return events


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def input_hover_chop_click(event_seq: int = 42, *, elapsed: float = 2.0, drag_distance: float = 12.0) -> dict:
    return {
        "schema": "input_action_classification.v1",
        "clickId": f"click_{event_seq}",
        "eventSeq": event_seq,
        "eventKind": "click",
        "time": {"elapsedSeconds": elapsed, "wallTimeUtc": iso(elapsed)},
        "button": "left",
        "classification": "ambiguous_click",
        "region": "viewport",
        "position": {"client": {"x": 340, "y": 220}},
        "dragContext": {"dragDistancePx": drag_distance},
        "menuContext": {
            "hoverOption": "Chop down",
            "hoverTarget": "<col=ffff>Tree",
            "menuOpenBefore": True,
            "menuOpenAfter": False,
        },
        "targetContext": {
            "matchedTarget": {"name": "Gate", "actions": ["Open"]},
            "targetName": "Gate",
            "targetAction": "Open",
        },
    }


def without_tree_candidates(event: dict) -> dict:
    copy = json.loads(json.dumps(event))
    for source in copy.get("sources") or []:
        if source.get("name") == "status":
            source["data"]["worldModelResourceObjectCensus"] = {"objects": []}
    return copy


class WoodcuttingLifecycleTest(unittest.TestCase):
    def test_detects_log_gain_free_slots_and_inventory_full(self):
        lifecycle = woodcutting_lifecycle.analyze_events(synthetic_events())
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertEqual(lifecycle["phase"], "inventory_full")
        self.assertEqual(lifecycle["inventory"]["normalLogsStart"], 0)
        self.assertEqual(lifecycle["inventory"]["normalLogsEnd"], 16)
        self.assertEqual(lifecycle["inventory"]["normalLogsGained"], 16)
        self.assertEqual(lifecycle["inventory"]["freeSlotsStart"], 16)
        self.assertEqual(lifecycle["inventory"]["freeSlotsEnd"], 0)
        self.assertTrue(lifecycle["inventory"]["inventoryFull"])

    def test_detects_animation_fresh_clicks_and_dedupes_repeated_clicks(self):
        lifecycle = woodcutting_lifecycle.analyze_events(synthetic_events())
        self.assertEqual(lifecycle["animation"]["woodcuttingAnimationId"], 879)
        self.assertEqual(lifecycle["animation"]["activeSnapshotCount"], 16)
        self.assertEqual(lifecycle["clicks"]["freshChopClickCount"], 16)
        self.assertGreaterEqual(lifecycle["clicks"]["ignoredRepeatedClickCount"], 16)

    def test_ignores_pre_recording_drop_logs_click(self):
        lifecycle = woodcutting_lifecycle.analyze_events(synthetic_events())
        self.assertEqual(lifecycle["clicks"]["ignoredPreRecordingClickCount"], 1)
        self.assertNotEqual(
            lifecycle["clicks"]["freshChopClicks"][0]["option"],
            "Drop",
        )

    def test_produces_cycles_from_click_animation_and_log_gain(self):
        lifecycle = woodcutting_lifecycle.analyze_events(synthetic_events())
        self.assertGreaterEqual(len(lifecycle["cycles"]), 16)
        first = lifecycle["cycles"][0]
        self.assertEqual(first["logsGained"], 1)
        self.assertEqual(first["click"]["option"], "Chop down")
        self.assertIsNotNone(first["animationStartTick"])

    def test_warns_when_only_partial_signals_exist(self):
        events = [
            {
                "schema_version": "manual_telemetry_event.v1",
                "event_type": "recording_start",
                "session_id": "s1",
                "wall_time_utc": iso(0),
                "elapsed_seconds": 0,
            },
            snapshot(100, 1, 0, 16, last_click=None, animation=-1),
            snapshot(105, 2, 1, 15, last_click=None, animation=-1),
        ]
        lifecycle = woodcutting_lifecycle.analyze_events(events)
        self.assertEqual(lifecycle["status"], "WARN")
        self.assertLess(lifecycle["confidence"], 0.8)
        self.assertIn("No fresh Chop down click was found.", lifecycle["warnings"])

    def test_input_hover_chop_tree_counts_as_fresh_human_click_evidence(self):
        events = [
            {
                "schema_version": "manual_telemetry_event.v1",
                "event_type": "recording_start",
                "session_id": "s1",
                "wall_time_utc": iso(0),
                "elapsed_seconds": 0,
            },
            without_tree_candidates(snapshot(100, 1, 0, 16, last_click=None, animation=879)),
            without_tree_candidates(snapshot(104, 3, 1, 15, last_click=None, animation=-1)),
        ]
        lifecycle = woodcutting_lifecycle.analyze_events(
            events,
            input_action_classifications=[input_hover_chop_click()],
        )
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertEqual(lifecycle["clicks"]["freshChopClickCount"], 1)
        self.assertEqual(lifecycle["clicks"]["freshChopClicks"][0]["source"], "input_action_menu_hover")
        self.assertEqual(lifecycle["targets"]["treeCountSeen"], 0)
        self.assertEqual(lifecycle["targets"]["inputTreeTargetEvidenceCount"], 1)
        self.assertNotIn("No tree target evidence was found.", lifecycle["warnings"])
        self.assertNotIn("No fresh Chop down click was found.", lifecycle["warnings"])

    def test_context_service_includes_woodcutting_lifecycle_when_requested(self):
        context = {
            "baseline": {"latestTick": 120, "player": {"worldX": 3190, "worldY": 3240, "plane": 0, "animation": 879}},
            "status": {
                "latestTickProcessed": 120,
                "clientTickHot": {
                    "lastMenuOptionClicked": click("Chop down", "<col=ffff>Tree", 118, timestamp=iso(3)),
                    "postMenuSort": {"entries": []},
                },
                "worldModelResourceObjectCensus": {"objects": [tree_object()]},
            },
            "activity": {
                "inventoryState": inventory(3, 13),
                "activityState": {"apparentState": "animating"},
                "woodcuttingState": {"woodcuttingState": "target_depleted"},
            },
            "candidates": [],
            "events": [],
            "warnings": [],
            "missingFields": [],
            "sourceFiles": [],
        }
        response = context_service.build_context_response(
            context,
            {"schema": "context_request.v1", "needs": ["woodcutting_lifecycle"], "responseMode": "compact"},
        )
        self.assertIn("woodcuttingLifecycle", response)
        self.assertEqual(response["woodcuttingLifecycle"]["phase"], "target_depleted")
        self.assertEqual(response["woodcuttingLifecycle"]["freshChopClickCount"], 1)

    def test_analyzer_writes_lifecycle_block_into_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            write_jsonl(recording / "events.jsonl", synthetic_events())
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("woodcutting_lifecycle", summary)
            self.assertTrue((recording / "woodcutting_lifecycle.json").exists())
            self.assertEqual(summary["woodcutting_lifecycle"]["inventory"]["normalLogsGained"], 16)

    def test_actual_recording_fixture_when_present(self):
        recording = REPO_ROOT / "recordings" / "20260602_223444_manual_action-Tree_cutting"
        if not recording.exists():
            self.skipTest("local tree-cutting recording fixture not present")
        lifecycle = woodcutting_lifecycle.analyze_recording(recording)
        self.assertEqual(lifecycle["phase"], "inventory_full")
        self.assertEqual(lifecycle["inventory"]["normalLogsGained"], 16)
        self.assertEqual(lifecycle["inventory"]["freeSlotsStart"], 16)
        self.assertEqual(lifecycle["inventory"]["freeSlotsEnd"], 0)
        self.assertGreaterEqual(lifecycle["clicks"]["freshChopClickCount"], 15)


if __name__ == "__main__":
    unittest.main()
