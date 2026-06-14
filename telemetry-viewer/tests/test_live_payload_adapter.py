import json
import sys
import unittest
from pathlib import Path
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import live_payload_adapter as adapter


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s3_live_payload" / "snapshot_ready.json"
FORBIDDEN_KEYS = {
    "action",
    "click",
    "command",
    "execute",
    "input",
    "interact",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
}


def walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


class LivePayloadAdapterTest(unittest.TestCase):
    def test_snapshot_flows_through_recovered_read_only_stack(self):
        snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))

        payload = adapter.stack_consumption_payload(snapshot, state_stale_ms=86_400_000)

        self.assertEqual(payload["schema"], "live_payload_stack_consumption.v1")
        self.assertEqual(payload["stateBaseline"]["schema"], "recovery_state_baseline.v1")
        self.assertEqual(payload["stateBaseline"]["status"], "PASS")
        self.assertEqual(payload["stateBaseline"]["inventory"]["freeSlots"], 24)
        self.assertEqual(payload["compactContext"]["schema"], "context_response.v1")
        self.assertTrue(payload["compactContext"]["ok"])
        self.assertEqual(payload["diagnostic"]["status"], "PASS")
        self.assertEqual(payload["observationDiagnostic"]["status"], "PASS")
        self.assertTrue(payload["consumedByRecoveredStack"])
        self.assertTrue(payload["observationReady"])

    def test_adapter_output_does_not_add_control_fields(self):
        snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))

        payload = adapter.stack_consumption_payload(snapshot, state_stale_ms=86_400_000)

        for key in walk_keys(payload):
            self.assertNotIn(key.lower(), FORBIDDEN_KEYS)


if __name__ == "__main__":
    unittest.main()
