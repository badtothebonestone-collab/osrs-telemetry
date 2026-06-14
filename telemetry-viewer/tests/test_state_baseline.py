import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import context_service as service


def args_for(session: Path | None = None, *, sessions_dir: Path | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session=str(session) if session else None,
        latest_session=session is None,
        sessions_dir=str(sessions_dir) if sessions_dir else None,
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


def write_minimal_session(session: Path, *, generated_at: str | None = None) -> None:
    generated_at = generated_at or utc_now()
    live = session / "interaction_geometry" / "live"
    write_json(
        live / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated_at,
            "latestTick": 42,
            "game_state": "LOGGED_IN",
            "player": {"world_x": 3200, "world_y": 3201, "plane": 0},
            "inventory": {"freeSlots": 24, "itemCount": 4},
        },
    )
    write_json(
        live / "live_status.json",
        {
            "schema": "live_status.v1",
            "generatedAtUtc": generated_at,
            "latestTickProcessed": 42,
            "loggedIn": True,
        },
    )
    write_json(live / "live_context_index.json", {"schema": "live_context_index.v1"})
    write_jsonl(live / "live_candidates.jsonl", [{"schema": "live_candidate_packet.v1", "tick": 42}])


def payload_for(session: Path) -> dict:
    args = args_for(session)
    context = service.ContextState(args).load_context(force=True)
    return service.state_baseline_payload(context, args)


class StateBaselineTest(unittest.TestCase):
    def test_missing_state_file_warns_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "missing-session"
            payload = payload_for(session)

        self.assertEqual(payload["schema"], "recovery_state_baseline.v1")
        self.assertEqual(payload["status"], "WARN")
        self.assertIn("baseline", payload["missingFields"])
        self.assertTrue(any("no readable baseline or status" in warning for warning in payload["warnings"]))

    def test_malformed_json_warns_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live = session / "interaction_geometry" / "live"
            live.mkdir(parents=True)
            (live / "live_baseline_state.json").write_text("{not-json", encoding="utf-8")
            write_json(live / "live_status.json", {"schema": "live_status.v1", "generatedAtUtc": utc_now(), "latestTick": 7})
            write_json(live / "live_context_index.json", {"schema": "live_context_index.v1"})
            write_jsonl(live / "live_candidates.jsonl", [{"tick": 7}])

            payload = payload_for(session)

        self.assertEqual(payload["status"], "WARN")
        self.assertIn("baseline", payload["missingFields"])
        self.assertTrue(any("baseline unreadable" in warning for warning in payload["warnings"]))
        self.assertEqual(payload["latestTick"], 7)

    def test_valid_minimal_json_produces_compact_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_minimal_session(session)

            payload = payload_for(session)

        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["gameState"], "LOGGED_IN")
        self.assertTrue(payload["loggedIn"])
        self.assertEqual(payload["latestTick"], 42)
        self.assertEqual(payload["player"]["worldX"], 3200)
        self.assertEqual(payload["player"]["worldY"], 3201)
        self.assertEqual(payload["inventory"]["freeSlots"], 24)
        disallowed = json.dumps(payload).lower()
        for term in ("click", "mouse", "keyboard", "menu"):
            self.assertNotIn(term, disallowed)

    def test_stale_json_warns_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_minimal_session(session, generated_at=stale_time())

            payload = payload_for(session)

        self.assertEqual(payload["status"], "WARN")
        self.assertGreater(payload["stateAgeMillis"], payload["staleThresholdMillis"])
        self.assertTrue(any("stale" in warning for warning in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
