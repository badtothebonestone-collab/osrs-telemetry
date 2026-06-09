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
import banking_lifecycle
import context_service
import telemetry_ui


START = datetime(2026, 6, 7, 15, 47, 44, tzinfo=timezone.utc)


def iso(offset_seconds: float) -> str:
    return (START + timedelta(seconds=offset_seconds)).isoformat().replace("+00:00", "Z")


def inventory(logs: int, free: int) -> dict:
    items = [{"slot": index, "itemId": 1511, "name": "Logs", "quantity": 1} for index in range(logs)]
    return {
        "known": True,
        "itemsKnown": True,
        "freeSlots": free,
        "filledSlots": 28 - free,
        "inventoryFull": free == 0,
        "items": items,
        "resourceCounts": {
            "normal_logs": {
                "displayName": "Logs",
                "itemIds": [1511],
                "count": logs,
                "byItemId": {"1511": logs},
                "matchedItems": items,
            }
        },
    }


def bank_ui(open_: bool = True, logs: int | None = None, *, deposit_box: bool = False) -> dict:
    bank_items = [] if logs is None else [{"slot": 10, "itemId": 1511, "name": "Logs", "quantity": logs}]
    return {
        "schema": "bank_ui_context_payload.v1",
        "bankOpen": open_,
        "depositBoxOpen": deposit_box,
        "bankRootVisible": open_ and not deposit_box,
        "depositBoxRootVisible": open_ and deposit_box,
        "bankContainerVisible": logs is not None,
        "bankInventoryVisible": open_,
        "depositInventoryButtonVisible": open_,
        "bankRootWidget": {"packedId": 1, "hidden": False},
        "bankItems": bank_items,
        "bankSummary": {
            "known": logs is not None,
            "itemCount": logs or 0,
            "filledSlots": 1 if logs else 0,
            "totalQuantityByItemId": {"1511": logs or 0} if logs is not None else {},
        },
    }


def bank_target() -> dict:
    return {
        "objectKey": "bank-booth",
        "kind": "GAME_OBJECT",
        "objectName": "Bank booth",
        "actions": ["Bank", "Collect"],
        "worldX": 3208,
        "worldY": 3221,
        "plane": 2,
        "distanceToPlayer": 1,
    }


def snapshot(tick: int, elapsed: float, logs: int, free: int, *, bank: dict | None = None) -> dict:
    baseline = {"latestTick": tick, "inventory": inventory(logs, free)}
    status = {
        "latestTickProcessed": tick,
        "worldModelRouteObjectCensus": {"objects": [bank_target()]},
        "clientTickHot": {"postMenuSort": {"entries": [{"option": "Bank", "target": "<col=ffff>Bank booth"}]}},
    }
    high = {
        "latest_tick": tick,
        "inventory": inventory(logs, free),
        "route_objects": [bank_target()],
        "bank": bank or {"missing": True},
    }
    sources = [
        {"name": "baseline", "data": baseline, "parse_status": "ok"},
        {"name": "status", "data": status, "parse_status": "ok"},
        {"name": "activity", "data": {"latestTick": tick, "inventoryState": inventory(logs, free)}, "parse_status": "ok"},
    ]
    if bank:
        sources.append({"name": "bank_ui", "data": bank, "parse_status": "ok"})
    return {
        "schema_version": "manual_telemetry_event.v1",
        "event_type": "source_snapshot",
        "session_id": "s1",
        "wall_time_utc": iso(elapsed),
        "elapsed_seconds": elapsed,
        "latest_tick": tick,
        "sources": sources,
        "high_value_fields": high,
    }


def deposit_click(seq: int = 10) -> dict:
    return {
        "schema": "input_action_classification.v1",
        "eventSeq": seq,
        "eventKind": "click",
        "button": "left",
        "classification": "minimap_click",
        "region": "minimap",
        "time": {"elapsedSeconds": 2.0, "wallTimeUtc": iso(2)},
        "menuContext": {
            "menuOpenBefore": True,
            "menuOpenAfter": False,
            "hoverOption": "Deposit-All",
            "hoverTarget": "<col=ff9040>Logs</col>",
        },
    }


def withdraw_click(seq: int = 11) -> dict:
    return {
        "schema": "input_action_classification.v1",
        "eventSeq": seq,
        "eventKind": "click",
        "button": "left",
        "classification": "menu_selection_click",
        "time": {"elapsedSeconds": 2.0, "wallTimeUtc": iso(2)},
        "menuContext": {
            "menuOpenBefore": True,
            "menuOpenAfter": False,
            "hoverOption": "Withdraw-1",
            "hoverTarget": "<col=ff9040>Logs</col>",
        },
    }


def events_without_direct_bank() -> list[dict]:
    return [
        {"event_type": "recording_start", "wall_time_utc": iso(0), "elapsed_seconds": 0, "session_id": "s1"},
        snapshot(100, 1, 6, 10),
        snapshot(105, 4, 0, 16),
        {"event_type": "recording_stop", "wall_time_utc": iso(5), "elapsed_seconds": 5, "duration_seconds": 5},
    ]


def events_with_bank_container() -> list[dict]:
    return [
        {"event_type": "recording_start", "wall_time_utc": iso(0), "elapsed_seconds": 0, "session_id": "s1"},
        snapshot(100, 1, 6, 10, bank=bank_ui(True, 20)),
        snapshot(105, 4, 0, 16, bank=bank_ui(True, 26)),
        {"event_type": "recording_stop", "wall_time_utc": iso(5), "elapsed_seconds": 5, "duration_seconds": 5},
    ]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")


class BankingLifecycleTest(unittest.TestCase):
    def test_deposit_logs_from_inventory_delta_only_warns_with_missing_container(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=events_without_direct_bank(),
            input_action_classifications=[deposit_click()],
        )
        self.assertEqual(lifecycle["status"], "WARN")
        self.assertTrue(lifecycle["deposit"]["detected"])
        self.assertEqual(lifecycle["deposit"]["items"][0]["id"], 1511)
        self.assertEqual(lifecycle["deposit"]["items"][0]["quantity"], 6)
        self.assertFalse(lifecycle["bank"]["containerAvailable"])
        self.assertIn("banking.bankContainer.items", lifecycle["missingCapabilities"])

    def test_deposit_logs_with_bank_open_and_container_increase_passes(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=events_with_bank_container(),
            input_action_classifications=[deposit_click()],
        )
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertTrue(lifecycle["bank"]["openSeen"])
        self.assertTrue(lifecycle["bank"]["containerAvailable"])
        self.assertTrue(lifecycle["bank"]["bankUiPresent"])
        self.assertTrue(lifecycle["bankContainerDeltaAvailable"])
        self.assertEqual(lifecycle["depositConfirmationLevel"], "bank_container_delta_confirmed")
        self.assertEqual(lifecycle["bank"]["changedItems"][0]["delta"], 6)
        self.assertEqual(lifecycle["deposit"]["items"][0]["confirmationLevel"], "bank_container_delta_confirmed")

    def test_bank_delta_recovers_from_open_container_snapshots_before_bank_close(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=[
                {"event_type": "recording_start", "wall_time_utc": iso(0), "elapsed_seconds": 0, "session_id": "s1"},
                snapshot(100, 1, 16, 0, bank=bank_ui(True, 126)),
                snapshot(105, 4, 0, 16, bank=bank_ui(True, 142)),
                snapshot(106, 5, 0, 16, bank=bank_ui(False, None)),
                {"event_type": "recording_stop", "wall_time_utc": iso(6), "elapsed_seconds": 6, "duration_seconds": 6},
            ],
            input_action_classifications=[deposit_click()],
        )
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertTrue(lifecycle["bankContainerDeltaAvailable"])
        self.assertEqual(lifecycle["bank"]["bankContainerDeltaSource"], "recorded_bank_snapshot_diff")
        self.assertEqual(lifecycle["bank"]["changedItems"][0]["before"], 126)
        self.assertEqual(lifecycle["bank"]["changedItems"][0]["after"], 142)
        self.assertNotIn("banking.bankContainer.delta", lifecycle["missingCapabilities"])

    def test_bank_target_click_and_bank_open_event(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=events_with_bank_container(),
            input_action_classifications=[{"eventSeq": 5, "eventKind": "click", "menuContext": {"hoverOption": "Bank", "hoverTarget": "Bank booth"}}],
        )
        self.assertTrue(any(event["eventType"] == "bank_target_click" for event in lifecycle["events"]))
        self.assertTrue(any(event["eventType"] == "bank_opened" for event in lifecycle["events"]))

    def test_deposit_box_open_sets_interface(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=[
                snapshot(100, 1, 1, 15, bank=bank_ui(True, 0, deposit_box=True)),
                snapshot(101, 2, 1, 15, bank=bank_ui(True, 0, deposit_box=True)),
            ],
            input_action_classifications=[],
        )
        self.assertEqual(lifecycle["bankLikeInterface"], "deposit_box")
        self.assertTrue(lifecycle["bank"]["depositBoxOpenSeen"])

    def test_inventory_free_slots_and_normal_logs_delta(self):
        lifecycle = banking_lifecycle.analyze_data(events=events_without_direct_bank(), input_action_classifications=[deposit_click()])
        self.assertEqual(lifecycle["inventory"]["freeSlotsBefore"], 10)
        self.assertEqual(lifecycle["inventory"]["freeSlotsAfter"], 16)
        self.assertEqual(lifecycle["inventory"]["freeSlotDelta"], 6)
        self.assertEqual(lifecycle["inventory"]["normalLogsBefore"], 6)
        self.assertEqual(lifecycle["inventory"]["normalLogsAfter"], 0)

    def test_withdraw_detection_from_inventory_increase_and_bank_decrease(self):
        lifecycle = banking_lifecycle.analyze_data(
            events=[
                snapshot(100, 1, 0, 16, bank=bank_ui(True, 26)),
                snapshot(105, 4, 1, 15, bank=bank_ui(True, 25)),
            ],
            input_action_classifications=[withdraw_click()],
        )
        self.assertEqual(lifecycle["status"], "PASS")
        self.assertTrue(lifecycle["withdraw"]["detected"])
        self.assertEqual(lifecycle["withdraw"]["items"][0]["quantity"], 1)

    def test_analyzer_writes_banking_lifecycle_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording"
            write_jsonl(recording / "events.jsonl", events_without_direct_bank())
            write_jsonl(recording / "input_action_classifications.jsonl", [deposit_click()])
            summary = analyze_manual_recording.update_outputs(recording)
            self.assertIn("banking_lifecycle", summary)
            self.assertTrue((recording / "banking_lifecycle.json").exists())
            self.assertEqual(summary["banking_lifecycle"]["deposit"]["totalDepositedCount"], 6)

    def test_context_compact_banking_response_includes_deposit_summary(self):
        context = {
            "baseline": {"latestTick": 1, "inventory": inventory(0, 16)},
            "status": {"latestTickProcessed": 1},
            "activity": {"inventoryState": inventory(0, 16)},
            "bank_ui": bank_ui(True, 26),
            "candidates": [],
            "events": [],
            "warnings": [],
            "missingFields": [],
            "sourceFiles": [],
        }
        response = context_service.build_context_response(
            context,
            {"schema": "context_request.v1", "needs": ["banking_lifecycle", "bank_state", "deposit_result"], "responseMode": "compact"},
        )
        self.assertIn("bankingLifecycle", response)
        self.assertTrue(response["bankingLifecycle"]["bankOpenSeen"])
        self.assertTrue(response["bankState"]["bankContainerAvailable"])
        self.assertTrue(response["bankState"]["bankUiPresent"])
        self.assertIn("depositResult", response)
        self.assertFalse(response["depositResult"]["depositComplete"])

    def test_ui_activity_detection_prefers_banking_lifecycle(self):
        activity = telemetry_ui.detected_activity_type(
            {
                "label": "manual_recording",
                "banking_lifecycle": {
                    "status": "WARN",
                    "deposit": {"detected": True, "items": [{"id": 1511, "name": "Logs", "quantity": 6}]},
                    "bank": {"openSeen": False, "targetEvidence": [{"name": "Bank booth"}]},
                },
            }
        )
        self.assertEqual(activity, "Banking")

    def test_actual_banking_fixture_when_present(self):
        recording = REPO_ROOT / "recordings" / "20260607_104744_Opening_Bank_and_Deposit_all_logs"
        if not recording.exists():
            self.skipTest("local banking recording fixture not present")
        lifecycle = banking_lifecycle.analyze_recording(recording)
        self.assertEqual(lifecycle["status"], "WARN")
        self.assertEqual(lifecycle["deposit"]["totalDepositedCount"], 6)
        self.assertFalse(lifecycle["bank"]["openSeen"])
        self.assertFalse(lifecycle["bank"]["containerAvailable"])


if __name__ == "__main__":
    unittest.main()
