import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "r4_live_readiness"
sys.path.insert(0, str(VIEWER_DIR))

import context_boundary
import recovery_diagnostics as diagnostics
import state_baseline


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
FORBIDDEN_RESPONSE_TEXT = (
    "action",
    "anti-detect",
    "antidetect",
    "click",
    "command",
    "execute",
    "gameplay command",
    "input",
    "interact",
    "interaction",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
)
FORBIDDEN_FIXTURE_NAMES = {"ready_to_act", "execute_ready", "target_ready", "route_ready"}
ALLOWED_DIAGNOSTIC_FIELDS = {"schema", "ok", "status", "reasons", "required_context", "observed_context", "warnings"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stale_time() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")


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


def replace_time_markers(value: Any) -> Any:
    if value == "__NOW__":
        return utc_now()
    if value == "__STALE__":
        return stale_time()
    if isinstance(value, dict):
        return {key: replace_time_markers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_time_markers(child) for child in value]
    return value


def load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURE_DIR / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    return replace_time_markers(payload)


def context_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    source_files: list[dict[str, Any]] = []
    for name in ("baseline", "status", "context"):
        exists = isinstance(fixture.get(name), dict)
        source_files.append({"name": name, "path": f"fixture:{name}", "exists": exists, "modifiedUtc": None, "sizeBytes": None})
        if not exists:
            missing.append(name)
            warnings.append(f"{name} missing:")

    candidates = fixture.get("candidates")
    if not isinstance(candidates, list):
        candidates = []
        missing.append("candidates")
        warnings.append("candidates missing:")

    return {
        "session": "fixture:r4_live_readiness",
        "baseline": copy.deepcopy(fixture.get("baseline")) if isinstance(fixture.get("baseline"), dict) else {},
        "status": copy.deepcopy(fixture.get("status")) if isinstance(fixture.get("status"), dict) else {},
        "context": copy.deepcopy(fixture.get("context")) if isinstance(fixture.get("context"), dict) else {},
        "activity": copy.deepcopy(fixture.get("activity")) if isinstance(fixture.get("activity"), dict) else {},
        "candidates": copy.deepcopy(candidates),
        "warnings": warnings,
        "missingFields": sorted(set(missing)),
        "sourceFiles": source_files,
    }


def context_response_for_fixture(fixture: dict[str, Any], request: dict[str, Any] | None = None) -> dict[str, Any]:
    args = SimpleNamespace(state_stale_ms=5000)
    baseline = state_baseline.state_baseline_payload(context_from_fixture(fixture), args)
    return context_boundary.compact_context_response(baseline, request)


def assert_no_forbidden_response(testcase: unittest.TestCase, payload: dict[str, Any]) -> None:
    for text, key in walk_keys_and_values(payload):
        lower = text.lower()
        if key is not None:
            testcase.assertNotIn(lower, FORBIDDEN_KEYS)
        for forbidden in FORBIDDEN_RESPONSE_TEXT:
            testcase.assertNotIn(forbidden, lower)


def assert_diagnostic_shape(testcase: unittest.TestCase, payload: dict[str, Any]) -> None:
    testcase.assertEqual(set(payload), ALLOWED_DIAGNOSTIC_FIELDS)


class R4LiveReadinessFixturesTest(unittest.TestCase):
    def test_fixture_names_do_not_imply_execution_permission(self):
        fixture_names = {path.name for path in FIXTURE_DIR.glob("*.json")}

        self.assertEqual(
            fixture_names,
            {
                "stale_logged_in.json",
                "login_screen.json",
                "logged_in_no_scene_evidence.json",
                "loaded_scene_evidence_present.json",
                "incomplete_telemetry.json",
            },
        )
        for name in fixture_names:
            lowered = name.lower()
            for forbidden in FORBIDDEN_FIXTURE_NAMES:
                self.assertNotIn(forbidden, lowered)

    def test_fixture_payloads_have_no_direct_control_fields(self):
        for path in FIXTURE_DIR.glob("*.json"):
            fixture = load_fixture(path.name)
            for text, key in walk_keys_and_values(fixture):
                if key is not None:
                    self.assertNotIn(text.lower(), FORBIDDEN_KEYS)

    def test_missing_live_state_returns_clean_failure_without_exception(self):
        payload = context_response_for_fixture({})
        diagnostic = diagnostics.evaluate_observation_readiness(payload, {})

        self.assertFalse(payload["ok"])
        self.assertFalse(diagnostic["ok"])
        self.assertEqual(diagnostic["status"], "FAIL")
        self.assertIn("upstream_not_ok", diagnostic["reasons"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_malformed_fixture_returns_clean_failure_without_exception(self):
        try:
            json.loads("{not-json")
        except json.JSONDecodeError:
            fixture = {
                "status": {
                    "schema": "live_status.v1",
                    "generatedAtUtc": utc_now(),
                    "latestTickProcessed": 7,
                    "loggedIn": True,
                },
                "context": {"schema": "live_context_index.v1"},
                "candidates": [{"schema": "live_candidate_packet.v1", "tick": 7}],
            }

        payload = context_response_for_fixture(fixture)
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertFalse(payload["ok"])
        self.assertFalse(diagnostic["ok"])
        self.assertIn("upstream_not_ok", diagnostic["reasons"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_stale_logged_in_fixture_is_not_observation_ready(self):
        fixture = load_fixture("stale_logged_in.json")
        payload = context_response_for_fixture(fixture)
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertTrue(payload["state"]["loggedIn"])
        self.assertFalse(diagnostic["ok"])
        self.assertIn("state_stale", diagnostic["reasons"])
        self.assertTrue(diagnostic["observed_context"]["loaded_scene_evidence_present"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_login_screen_fixture_stays_blocked_read_only(self):
        fixture = load_fixture("login_screen.json")
        payload = context_response_for_fixture(fixture)
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertEqual(payload["state"]["gameState"], "LOGIN_SCREEN")
        self.assertFalse(payload["state"]["loggedIn"])
        self.assertFalse(diagnostic["ok"])
        self.assertIn("not_logged_in", diagnostic["reasons"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_logged_in_fresh_tick_without_scene_world_player_evidence_is_not_ready(self):
        fixture = load_fixture("logged_in_no_scene_evidence.json")
        payload = context_response_for_fixture(fixture)
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertTrue(payload["state"]["loggedIn"])
        self.assertEqual(payload["state"]["latestTick"], 42)
        self.assertFalse(diagnostic["ok"])
        self.assertIn("missing_player_world_x", diagnostic["reasons"])
        self.assertIn("missing_scene_evidence", diagnostic["reasons"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_loaded_scene_evidence_reports_observation_readiness(self):
        fixture = load_fixture("loaded_scene_evidence_present.json")
        payload = context_response_for_fixture(fixture)
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["gameState"], "LOGGED_IN")
        self.assertEqual(payload["player"]["worldX"], 3205)
        self.assertTrue(diagnostic["ok"])
        self.assertEqual(diagnostic["status"], "PASS")
        self.assertTrue(diagnostic["observed_context"]["loaded_scene_evidence_present"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_incomplete_telemetry_identifies_missing_facts_without_raw_request_echo(self):
        fixture = load_fixture("incomplete_telemetry.json")
        payload = context_response_for_fixture(
            fixture,
            {
                "schema": "context_request.v1",
                "needs": ["state", "click tree", "keyboard"],
                "responseMode": "execute",
                "task": "woodcutting",
            },
        )
        diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)

        self.assertFalse(payload["ok"])
        self.assertIn("status", payload["errors"])
        self.assertFalse(diagnostic["ok"])
        self.assertIn("upstream_not_ok", diagnostic["reasons"])
        self.assertIn("missing_player", diagnostic["reasons"])
        assert_no_forbidden_response(self, payload)
        assert_no_forbidden_response(self, diagnostic)

    def test_context_response_v1_has_no_forbidden_fields_recursively(self):
        for path in FIXTURE_DIR.glob("*.json"):
            payload = context_response_for_fixture(load_fixture(path.name))
            self.assertEqual(payload["schema"], "context_response.v1")
            assert_no_forbidden_response(self, payload)

    def test_recovery_diagnostic_v1_has_no_forbidden_fields_recursively(self):
        for path in FIXTURE_DIR.glob("*.json"):
            fixture = load_fixture(path.name)
            payload = context_response_for_fixture(fixture)
            diagnostic = diagnostics.evaluate_observation_readiness(payload, fixture)
            self.assertEqual(diagnostic["schema"], "recovery_diagnostic.v1")
            assert_diagnostic_shape(self, diagnostic)
            assert_no_forbidden_response(self, diagnostic)


if __name__ == "__main__":
    unittest.main()
