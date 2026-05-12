import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_inventory_slots as diagnose


class DiagnoseInventorySlotsTest(unittest.TestCase):
    def test_diagnose_reports_high_slot_resource_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            live_dir.mkdir(parents=True)
            (live_dir / "live_activity_state.json").write_text(
                json.dumps(
                    {
                        "latestTick": 10,
                        "inventoryState": {
                            "known": True,
                            "inventorySlotCount": 28,
                            "slotCount": 28,
                            "filledSlots": 2,
                            "freeSlots": 26,
                            "inventoryFull": False,
                            "items": [
                                {"slot": 0, "itemId": 1521, "quantity": 1},
                                {"slot": 27, "itemId": 1511, "quantity": 1},
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = diagnose.diagnose(session, "woodcutting_logs")

        self.assertEqual(payload["inventorySlotCount"], 28)
        self.assertEqual(payload["resourceCounts"]["woodcutting_logs"]["count"], 2)
        self.assertEqual(payload["resourceCounts"]["woodcutting_logs"]["matchedSlots"], [0, 27])
        self.assertEqual(payload["slotTable"][27]["itemId"], 1511)
        self.assertIn("all 28 inventory slots", payload["conclusion"])


if __name__ == "__main__":
    unittest.main()
