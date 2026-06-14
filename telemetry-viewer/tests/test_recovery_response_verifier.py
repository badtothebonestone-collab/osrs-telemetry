import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VIEWER_DIR = REPO_ROOT / "telemetry-viewer"
CONTEXT_SERVICE = VIEWER_DIR / "context_service.py"
VERIFIER = REPO_ROOT / "scripts" / "verify_recovery_response.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def write_valid_session(session: Path) -> None:
    generated_at = utc_now()
    live = session / "interaction_geometry" / "live"
    write_json(
        live / "live_baseline_state.json",
        {
            "schema": "live_baseline_state.v1",
            "generatedAtUtc": generated_at,
            "latestTick": 42,
            "gameState": "LOGGED_IN",
            "loggedIn": True,
            "player": {"worldX": 3200, "worldY": 3201, "plane": 0},
            "inventory": {"known": True, "freeSlots": 24, "itemCount": 4},
        },
    )
    write_json(live / "live_status.json", {"schema": "live_status.v1", "generatedAtUtc": generated_at, "latestTickProcessed": 42})
    write_json(live / "live_context_index.json", {"schema": "live_context_index.v1"})
    write_jsonl(live / "live_candidates.jsonl", [{"schema": "live_candidate_packet.v1", "tick": 42}])


def run_python(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=REPO_ROOT,
        check=False,
    )


def verify_payload(payload: dict, schema: str) -> subprocess.CompletedProcess:
    return run_python([str(VERIFIER), "--schema", schema], input_text=json.dumps(payload))


class RecoveryResponseVerifierTest(unittest.TestCase):
    def test_verifier_accepts_required_valid_responses(self):
        state_payload = {
            "schema": "recovery_state_baseline.v1",
            "status": "PASS",
            "warnings": [],
            "missingFields": [],
            "player": {},
            "inventory": {},
            "sourceFiles": [],
        }
        context_payload = {
            "schema": "context_response.v1",
            "ok": True,
            "errors": [],
            "warnings": [],
            "generatedAtUtc": utc_now(),
            "state": {"gameState": "LOGGED_IN"},
        }

        self.assertEqual(verify_payload(state_payload, "recovery_state_baseline.v1").returncode, 0)
        self.assertEqual(verify_payload(context_payload, "context_response.v1").returncode, 0)

    def test_verifier_rejects_status_fail(self):
        payload = {
            "schema": "recovery_state_baseline.v1",
            "status": "FAIL",
            "warnings": [],
            "missingFields": [],
            "player": {},
            "inventory": {},
            "sourceFiles": [],
        }

        result = verify_payload(payload, "recovery_state_baseline.v1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status_fail", result.stdout)

    def test_verifier_rejects_context_ok_false(self):
        payload = {
            "schema": "context_response.v1",
            "ok": False,
            "errors": ["baseline"],
            "warnings": [],
            "generatedAtUtc": utc_now(),
        }

        result = verify_payload(payload, "context_response.v1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("context_response_not_ok", result.stdout)

    def test_verifier_rejects_forbidden_nested_fields(self):
        payload = {
            "schema": "context_response.v1",
            "ok": True,
            "errors": [],
            "warnings": [],
            "generatedAtUtc": utc_now(),
            "state": {"command": "noop"},
        }

        result = verify_payload(payload, "context_response.v1")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forbidden_field", result.stdout)

    def test_verifier_rejects_invalid_json(self):
        result = run_python([str(VERIFIER), "--schema", "context_response.v1"], input_text="{not-json")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid_json", result.stdout)

    def test_deterministic_cli_fixture_outputs_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            write_valid_session(session)

            state_result = run_python([str(CONTEXT_SERVICE), "--session", str(session), "--state-baseline"])
            context_result = run_python([str(CONTEXT_SERVICE), "--session", str(session), "--compact-context"])

        self.assertEqual(state_result.returncode, 0, state_result.stderr)
        self.assertEqual(context_result.returncode, 0, context_result.stderr)

        state_verify = run_python([str(VERIFIER), "--schema", "recovery_state_baseline.v1"], input_text=state_result.stdout)
        context_verify = run_python([str(VERIFIER), "--schema", "context_response.v1"], input_text=context_result.stdout)

        self.assertEqual(state_verify.returncode, 0, state_verify.stdout + state_verify.stderr)
        self.assertEqual(context_verify.returncode, 0, context_verify.stdout + context_verify.stderr)


if __name__ == "__main__":
    unittest.main()
