import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import context_service as service


FORBIDDEN_KEYS = {
    "action",
    "actions",
    "click",
    "clickcommand",
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
    "anti-detection",
    "click",
    "command",
    "execute",
    "gameplay command",
    "input",
    "interact",
    "keyboard",
    "menu",
    "mouse",
    "movement",
    "target",
)


def args_for(session: Path) -> SimpleNamespace:
    return SimpleNamespace(
        session=str(session),
        latest_session=False,
        sessions_dir=None,
        reload_interval=0,
        max_candidates=3,
        max_response_bytes=1_000_000,
        auth_token=None,
        no_auth_token=True,
        debug=False,
        compact_include_source_files=False,
        compact_include_liveness_examples=0,
        state_stale_ms=5000,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stale_time() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def write_session(session: Path, *, generated_at: str | None = None) -> None:
    generated_at = generated_at or utc_now()
    live = session / "interaction_geometry" / "live"
    write_json(
        live / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated_at,
            "latestTick": 42,
            "gameState": "LOGGED_IN",
            "player": {"worldX": 3200, "worldY": 3201, "plane": 0},
            "inventory": {"freeSlots": 24, "itemCount": 4},
        },
    )
    write_json(live / "live_status.json", {"schema": "live_status.v1", "generatedAtUtc": generated_at, "latestTickProcessed": 42})
    write_json(live / "live_context_index.json", {"schema": "live_context_index.v1"})
    write_jsonl(live / "live_candidates.jsonl", [{"schema": "live_candidate_packet.v1", "tick": 42}])


def compact_payload(session: Path, request: dict | None = None) -> dict:
    args = args_for(session)
    state = service.ContextState(args).load_context(force=True)
    baseline = service.state_baseline_payload(state, args)
    return service.compact_context_response(baseline, request)


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


class CompactContextBoundaryTest(unittest.TestCase):
    def assert_no_forbidden_context_fields(self, payload: dict) -> None:
        for text, key in walk_keys_and_values(payload):
            lower = text.lower()
            if key is not None:
                self.assertNotIn(lower, FORBIDDEN_KEYS)
            for forbidden in FORBIDDEN_TEXT:
                self.assertNotIn(forbidden, lower)

    def test_compact_response_shape_from_valid_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_session(session)

            payload = compact_payload(session)

        self.assertEqual(payload["schema"], "context_response.v1")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["state"]["gameState"], "LOGGED_IN")
        self.assertEqual(payload["state"]["latestTick"], 42)
        self.assertEqual(payload["player"]["worldX"], 3200)
        self.assertEqual(payload["inventory"]["freeSlots"], 24)
        self.assertEqual(payload["source"]["stateSchema"], "recovery_state_baseline.v1")
        self.assert_no_forbidden_context_fields(payload)

    def test_request_filters_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_session(session)

            payload = compact_payload(session, {"schema": "context_request.v1", "needs": ["state"], "responseMode": "compact"})

        self.assertEqual(sorted(payload.keys()), ["errors", "generatedAtUtc", "ok", "schema", "state", "warnings"])
        self.assertTrue(payload["ok"])
        self.assert_no_forbidden_context_fields(payload)

    def test_missing_state_uses_r1_parser_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "missing"

            payload = compact_payload(session)

        self.assertEqual(payload["schema"], "context_response.v1")
        self.assertFalse(payload["ok"])
        self.assertIn("baseline", payload["errors"])
        self.assertIn("status", payload["errors"])
        self.assert_no_forbidden_context_fields(payload)

    def test_malformed_state_uses_r1_parser_and_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live = session / "interaction_geometry" / "live"
            live.mkdir(parents=True)
            (live / "live_baseline_state.json").write_text("{not-json", encoding="utf-8")
            write_json(live / "live_status.json", {"schema": "live_status.v1", "generatedAtUtc": utc_now(), "latestTick": 7})
            write_json(live / "live_context_index.json", {"schema": "live_context_index.v1"})
            write_jsonl(live / "live_candidates.jsonl", [{"tick": 7}])

            payload = compact_payload(session)

        self.assertFalse(payload["ok"])
        self.assertIn("baseline", payload["errors"])
        self.assertEqual(payload["state"]["latestTick"], 7)
        self.assert_no_forbidden_context_fields(payload)

    def test_max_age_marks_stale_context_not_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_session(session, generated_at=stale_time())

            payload = compact_payload(session, {"schema": "context_request.v1", "needs": ["state", "source"], "responseMode": "compact", "maxAgeMs": 1})

        self.assertFalse(payload["ok"])
        self.assertTrue(any("stateAgeMillis exceeds maxAgeMs" in error for error in payload["errors"]))
        self.assert_no_forbidden_context_fields(payload)

    def test_activity_summary_drops_action_like_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_session(session)
            live = session / "interaction_geometry" / "live"
            write_json(
                live / "live_activity_state.json",
                {
                    "schema": "live_activity_state.v1",
                    "generatedAtUtc": utc_now(),
                    "latestTick": 42,
                    "activityState": {
                        "apparentState": "idle",
                        "confidence": 0.8,
                        "evidence": ["test"],
                        "action": "click tree",
                        "target": {"name": "Tree"},
                        "menu": "Chop down",
                        "keyboard": "space",
                        "movement": "walk",
                    },
                },
            )

            payload = compact_payload(session, {"schema": "context_request.v1", "needs": ["activity"], "responseMode": "compact"})

        self.assertEqual(payload["activity"]["apparentState"], "idle")
        self.assertEqual(payload["activity"]["confidence"], 0.8)
        self.assertEqual(payload["activity"]["evidenceCount"], 1)
        self.assertEqual(sorted(payload.keys()), ["activity", "errors", "generatedAtUtc", "ok", "schema", "warnings"])
        self.assert_no_forbidden_context_fields(payload)

    def test_unsupported_request_values_are_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_session(session)

            payload = compact_payload(
                session,
                {
                    "schema": "not-context-request",
                    "needs": ["state", "click tree", "menu option", "action", "keyboard"],
                    "responseMode": "execute",
                    "task": "woodcutting",
                    "profile": "target profile",
                    "mouse": "move",
                },
            )

        self.assertTrue(payload["ok"])
        self.assertIn("invalid_schema", payload["warnings"])
        self.assertIn("unsupported_need", payload["warnings"])
        self.assertIn("unsupported_need_count:4", payload["warnings"])
        self.assertIn("unsupported_response_mode", payload["warnings"])
        self.assertIn("unsupported_task", payload["warnings"])
        self.assertIn("unsupported_profile", payload["warnings"])
        self.assertIn("unsupported_request_field_count:1", payload["warnings"])
        self.assert_no_forbidden_context_fields(payload)


if __name__ == "__main__":
    unittest.main()
