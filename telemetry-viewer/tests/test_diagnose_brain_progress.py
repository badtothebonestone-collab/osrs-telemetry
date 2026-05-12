import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import brain_core as brain
import diagnose_brain_progress as diagnose


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class DiagnoseBrainProgressTest(unittest.TestCase):
    def test_diagnose_reports_slot_18_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            write_json(
                live_dir / "live_activity_state.json",
                {
                    "latestTick": 10,
                    "inventoryState": {
                        "known": True,
                        "inventorySlotCount": 28,
                        "slotCount": 28,
                        "filledSlots": 1,
                        "freeSlots": 27,
                        "inventoryFull": False,
                        "inventorySignature": "18:1511:1",
                        "items": [{"slot": 18, "itemId": 1511, "quantity": 1}],
                    },
                },
            )
            state = brain.default_state("woodcutting", 5)
            state["resourceBaselineCounts"] = {"woodcutting_logs": 0}
            state["baselineEstablished"] = True
            state["baselineHeldCount"] = 0
            state["previousResourceCount"] = 0
            state["lastProcessedInventorySignature"] = "empty"
            state["resourceProgress"] = {
                "schema": "resource_progress_state.v1",
                "resourceGroup": "woodcutting_logs",
                "baselineEstablished": True,
                "baselineHeldCount": 0,
                "displayedGoalProgress": 0,
                "lastInventorySignature": "empty",
            }
            state["previousInventoryItems"] = []
            state_path = Path(tmp) / "brain_state.json"
            brain.write_state(str(state_path), state)

            payload = diagnose.diagnose(session, str(state_path), "woodcutting", 5, "woodcutting_logs")

        self.assertEqual(payload["currentCount"]["count"], 1)
        self.assertEqual(payload["progressEstimate"]["matchedSlots"], [18])
        self.assertEqual(payload["progressEstimate"]["gainedSinceStart"], 1)
        self.assertIn("daily gained since start is monotonic held-vs-baseline progress until reset", " ".join(payload["explanation"]))

    def test_writes_off_session_points_to_daemon_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            session.mkdir()

            payload = diagnose.diagnose(session, None, "woodcutting", 5, "woodcutting_logs")

        self.assertEqual(payload["inventorySource"], "none")
        self.assertTrue(any("--from-daemon" in warning for warning in payload["warnings"]))
        self.assertIn("inventory item list missing", " ".join(payload["explanation"]))

    def test_diagnose_reports_poisoned_unchanged_baseline_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            items = [{"slot": slot, "itemId": 1511, "quantity": 1} for slot in range(5)]
            signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in items)
            write_json(
                live_dir / "live_activity_state.json",
                {
                    "latestTick": 10,
                    "inventoryState": {
                        "known": True,
                        "inventorySlotCount": 28,
                        "slotCount": 28,
                        "filledSlots": 5,
                        "freeSlots": 23,
                        "inventoryFull": False,
                        "inventorySignature": signature,
                        "items": items,
                    },
                },
            )
            state = brain.default_state("woodcutting", 5)
            state.update(
                {
                    "baselineEstablished": True,
                    "baselineHeldCount": 5,
                    "previousResourceCount": 5,
                    "observedGained": 20,
                    "observedRemoved": 20,
                    "displayedGoalProgress": 20,
                    "hasValidPostBaselineProgressHistory": False,
                    "lastProcessedInventorySignature": signature,
                    "resourceBaselineCounts": {"woodcutting_logs": 5},
                    "resourceGainedCounts": {"woodcutting_logs": 20},
                    "resourceLostCounts": {"woodcutting_logs": 20},
                    "resourceProgress": {
                        "resourceGroup": "woodcutting_logs",
                        "baselineEstablished": True,
                        "baselineHeldCount": 5,
                        "currentHeldCount": 5,
                        "previousResourceCount": 5,
                        "observedGained": 20,
                        "observedRemoved": 20,
                        "displayedGoalProgress": 20,
                        "hasValidPostBaselineProgressHistory": False,
                        "lastProcessedInventorySignature": signature,
                    },
                }
            )
            state_path = Path(tmp) / "brain_state.json"
            brain.write_state(str(state_path), state)

            payload = diagnose.diagnose(session, str(state_path), "woodcutting", 5, "woodcutting_logs")

        self.assertEqual(payload["progressEstimate"]["gainedSinceStart"], 0)
        self.assertIsNone(payload["progressEstimate"]["observedRemoved"])
        self.assertTrue(payload["progressEstimate"]["progressStateRepaired"])
        self.assertEqual(payload["progressEstimate"]["repairReason"], brain.OLD_PROGRESS_HISTORY_WARNING)
        self.assertIn("old cumulative progress history ignored", " ".join(payload["explanation"]))

    def test_strict_flags_invalid_counted_slot(self):
        payload = {
            "invalidMatchedSlots": [{"slot": 9, "itemId": None, "counted": True}],
            "brainState": {"baselineEstablished": True, "baselineHeldCount": 5},
            "progressEstimate": {
                "source": "inventory_snapshot_held_vs_baseline",
                "currentSnapshotValid": True,
                "progressUpdateApplied": False,
            },
            "currentCount": {"known": True, "count": 5, "matchedSlotDetails": []},
        }

        strict = diagnose.strict_check(payload)

        self.assertEqual(strict["status"], "FAIL")
        self.assertTrue(any("itemId" in failure for failure in strict["failures"]))

    def test_strict_passes_duplicate_snapshot_unchanged(self):
        payload = {
            "invalidMatchedSlots": [],
            "brainState": {"baselineEstablished": True, "baselineHeldCount": 5},
            "progressEstimate": {
                "source": "inventory_snapshot_held_vs_baseline",
                "currentSnapshotValid": True,
                "progressUpdateApplied": False,
                "duplicateSnapshot": True,
                "currentHeldCount": 9,
            },
            "currentCount": {
                "known": True,
                "count": 9,
                "source": "inventory_snapshot_items",
                "matchedSlotDetails": [{"slot": 9, "itemId": 1511, "counted": True}],
            },
        }

        strict = diagnose.strict_check(payload)

        self.assertEqual(strict["status"], "PASS")

    def test_strict_catches_invalid_snapshot_without_retention(self):
        payload = {
            "invalidMatchedSlots": [],
            "brainState": {"baselineEstablished": True, "baselineHeldCount": 5},
            "progressEstimate": {
                "source": "inventory_snapshot_invalid",
                "currentSnapshotValid": False,
                "progressRetainedFromPrevious": False,
                "lastValidProgressTick": 90,
                "displayedGoalProgress": 0,
            },
            "currentCount": {"known": False, "count": None, "matchedSlotDetails": []},
        }

        strict = diagnose.strict_check(payload)

        self.assertEqual(strict["status"], "FAIL")
        self.assertTrue(any("retain" in failure for failure in strict["failures"]))


if __name__ == "__main__":
    unittest.main()
