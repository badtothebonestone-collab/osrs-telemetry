import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
SCRIPT = VIEWER_DIR / "diagnose_woodcutting_candidates.py"
sys.path.insert(0, str(VIEWER_DIR))

import woodcutting_candidate_diagnostics as diag


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def target(name="Tree", key="tree-a", tick=10):
    return {
        "name": name,
        "classId": "tree",
        "targetType": "sceneObject",
        "objectKey": key,
        "targetKey": key,
        "id": 1276,
        "worldX": 3200,
        "worldY": 3201,
        "plane": 0,
        "distanceTiles": 1,
        "onScreen": True,
        "geometryAvailable": True,
        "aimPoint": {"x": 100, "y": 120},
        "tick": tick,
    }


class WoodcuttingCandidateDiagnosticTest(unittest.TestCase):
    def test_detects_latest_session_mismatch_but_highlighter_matches_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            newer_empty = root / "newer"
            write_json(newer_empty / "manifest.json", {"sessionId": "newer"})
            live_dir = daemon_session / "interaction_geometry" / "live"
            marker = target()
            write_json(live_dir / "overlay_debug_state.json", {"latestTick": 10, "markers": [marker], "targets": [marker]})
            status = {
                "sessionPath": str(daemon_session),
                "latestTick": 10,
                "candidateCount": 1,
                "profileCandidateCount": 1,
                "broadCandidateCount": 1,
                "sourceCapHit": False,
                "budgetExceeded": False,
                "sourceSceneKnowledgeComplete": True,
                "brain": {
                    "latestTick": 10,
                    "freshnessDomains": {"targetCandidateFreshness": "fresh"},
                    "genericTaskState": {"activeIntentTarget": marker},
                },
            }

            report = diag.build_report(latest_session=True, sessions_dir=root, daemon_status=status)

            self.assertEqual(report["status"], "WARN")
            self.assertTrue(report["sessions"]["sourceMismatch"])
            self.assertEqual(Path(report["sessions"]["highlighterSessionPath"]), daemon_session.resolve())
            self.assertTrue(report["selectedTargetChecks"]["inHighlighterSource"])
            self.assertIn("latest session differs", " ".join(report["warnings"]))

    def test_stale_selected_target_fails(self):
        status = {
            "sessionPath": "daemon",
            "latestTick": 20,
            "brain": {
                "latestTick": 20,
                "freshnessDomains": {"targetCandidateFreshness": "fresh"},
                "genericTaskState": {"activeIntentTarget": target(tick=10)},
            },
        }

        report = diag.build_report(daemon_status=status)

        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["freshness"]["stale"])
        self.assertIn("candidate data stale", " ".join(report["failures"]))

    def test_selected_projection_sentinel_is_warned_as_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            live_dir = session / "interaction_geometry" / "live"
            marker = target()
            marker["aimPoint"] = {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"}
            write_json(live_dir / "overlay_debug_state.json", {"latestTick": 10, "markers": [marker], "targets": [marker]})
            status = {
                "sessionPath": str(session),
                "latestTick": 10,
                "candidateCount": 1,
                "profileCandidateCount": 1,
                "broadCandidateCount": 1,
                "sourceCapHit": False,
                "budgetExceeded": False,
                "sourceSceneKnowledgeComplete": True,
                "brain": {
                    "latestTick": 10,
                    "freshnessDomains": {"targetCandidateFreshness": "fresh"},
                    "genericTaskState": {"activeIntentTarget": marker},
                },
            }

            report = diag.build_report(latest_session=True, sessions_dir=root, daemon_status=status)

            self.assertEqual(report["status"], "WARN")
            self.assertFalse(report["selectedTargetChecks"]["hasAimPoint"])
            self.assertFalse(report["selectedTargetChecks"]["actionable"])
            self.assertIn("selected target is visible but not actionable", " ".join(report["warnings"]))

    def test_json_cli_contains_schema_without_writing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            write_json(session / "manifest.json", {"sessionId": "session"})
            before = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--sessions-dir", str(root), "--latest-session", "--json", "--timeout", "0.01"],
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(result.stdout)
            after = sorted(path.relative_to(tmp) for path in Path(tmp).rglob("*"))
            self.assertEqual(payload["schema"], "woodcutting_candidate_diagnostic.v1")
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
