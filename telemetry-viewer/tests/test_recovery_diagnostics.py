import sys
import unittest
from pathlib import Path
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import recovery_diagnostics as diagnostics


FORBIDDEN_KEYS = {
    "action",
    "actions",
    "click",
    "command",
    "commands",
    "execute",
    "input",
    "interact",
    "interaction",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
}
FORBIDDEN_TEXT = (
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
)


def minimal_context(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "context_response.v1",
        "ok": True,
        "errors": [],
        "warnings": [],
        "generatedAtUtc": "2026-06-14T00:00:00Z",
        "state": {"gameState": "LOGGED_IN", "loggedIn": True, "latestTick": 42},
        "player": {"worldX": 3200, "worldY": 3201, "plane": 0},
    }
    payload.update(overrides)
    return payload


def walk_keys_and_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), key
            yield from walk_keys_and_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys_and_values(child)
    elif isinstance(value, str):
        yield value, None


class RecoveryDiagnosticsTest(unittest.TestCase):
    def assert_no_forbidden_output(self, payload: dict) -> None:
        for text, key in walk_keys_and_values(payload):
            lower = text.lower()
            if key is not None:
                self.assertNotIn(lower, FORBIDDEN_KEYS)
            for forbidden in FORBIDDEN_TEXT:
                self.assertNotIn(forbidden, lower)

    def test_missing_context_fails_safely(self):
        payload = diagnostics.evaluate_context(None)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["reasons"], ["missing_context"])
        self.assert_no_forbidden_output(payload)

    def test_malformed_context_fails_safely(self):
        payload = diagnostics.evaluate_context(["not", "a", "dict"])

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "FAIL")
        self.assertEqual(payload["reasons"], ["malformed_context"])
        self.assert_no_forbidden_output(payload)

    def test_valid_minimal_context_passes(self):
        payload = diagnostics.evaluate_context(minimal_context())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["reasons"], [])
        self.assertTrue(payload["observed_context"]["game_state_present"])
        self.assertTrue(payload["observed_context"]["player_world_x_present"])
        self.assert_no_forbidden_output(payload)

    def test_context_with_warnings_is_diagnostic_warn(self):
        payload = diagnostics.evaluate_context(minimal_context(warnings=["state timestamp is stale."]))

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "WARN")
        self.assertEqual(payload["warnings"], ["upstream_warning_count:1"])
        self.assert_no_forbidden_output(payload)

    def test_forbidden_field_rejection_does_not_echo_raw_field(self):
        payload = diagnostics.evaluate_context(
            minimal_context(
                state={
                    "gameState": "LOGGED_IN",
                    "loggedIn": True,
                    "latestTick": 42,
                    "command": "noop",
                }
            )
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "FAIL")
        self.assertIn("forbidden_context_field", payload["reasons"])
        self.assert_no_forbidden_output(payload)


if __name__ == "__main__":
    unittest.main()
