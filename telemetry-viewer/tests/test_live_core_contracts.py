import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def target(name: str = "Tree", *, key: str = "tree-a", tick: int = 10) -> dict:
    return {
        "name": name,
        "targetName": name,
        "classId": "tree",
        "profileId": "woodcutting",
        "targetType": "sceneObject",
        "objectKey": key,
        "targetKey": key,
        "id": 1276,
        "worldX": 3200,
        "worldY": 3201,
        "plane": 0,
        "rank": 1,
        "score": 96,
        "onScreen": True,
        "geometryAvailable": True,
        "aimPoint": {"canvasX": 100, "canvasY": 120, "source": "clickboxBounds"},
        "tick": tick,
        "positiveSignals": ["profileMatch", "onScreen"],
        "negativeSignals": [],
    }


class LiveCoreContractsTest(unittest.TestCase):
    def test_candidate_explanation_has_canonical_shape(self):
        import candidate_core

        explanation = candidate_core.explain_candidate(
            target(),
            rank=1,
            profile="woodcutting",
            source_session=Path("session-a"),
            source_file=Path("session-a") / "interaction_geometry" / "live" / "live_candidates.jsonl",
            source_tick=10,
            status={"latestTick": 10, "brain": {"freshnessDomains": {"targetCandidateFreshness": "fresh"}}},
        )

        self.assertEqual(explanation["schema"], "candidate_explanation.v1")
        self.assertEqual(explanation["name"], "Tree")
        self.assertEqual(explanation["objectId"], 1276)
        self.assertEqual(explanation["profileMatch"], True)
        self.assertEqual(explanation["rank"], 1)
        self.assertEqual(explanation["worldLocation"], {"worldX": 3200, "worldY": 3201, "plane": 0})
        self.assertEqual(explanation["canvasAimPoint"], {"x": 100, "y": 120})
        self.assertEqual(explanation["geometryStatus"], "available")
        self.assertEqual(explanation["onScreenStatus"], "on_screen")
        self.assertEqual(explanation["freshness"]["status"], "fresh")
        self.assertIn("profileMatch", explanation["acceptedReasons"])
        self.assertEqual(explanation["source"]["sessionPath"], str(Path("session-a")))

    def test_candidate_explanation_rejects_projection_sentinel_aimpoint(self):
        import candidate_core

        candidate = target()
        candidate["aimPoint"] = {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"}

        explanation = candidate_core.explain_candidate(candidate)

        self.assertIsNone(candidate_core.aim_point(candidate))
        self.assertIsNone(explanation["canvasAimPoint"])
        self.assertIsNone(explanation["rawAimPoint"])
        self.assertEqual(explanation["aimPointStatus"], "invalid")
        self.assertIn("invalidAimPoint", explanation["rejectedReasons"])

    def test_live_file_core_reads_overlay_and_candidates(self):
        import live_file_core

        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live = session / "interaction_geometry" / "live"
            marker = target()
            write_json(live / "overlay_debug_state.json", {"markers": [marker]})
            write_jsonl(live / "live_candidates.jsonl", [marker])

            sources = live_file_core.load_live_files(session)

        self.assertEqual(sources["paths"]["liveDir"].name, "live")
        self.assertEqual(len(sources["overlayTargets"]), 1)
        self.assertEqual(len(sources["liveCandidates"]), 1)
        self.assertFalse(sources["missing"])

    def test_live_session_core_prefers_daemon_session_for_highlighter_when_overlay_exists(self):
        import live_session_core

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            daemon_session = root / "daemon"
            latest_live = root / "latest"
            write_json(daemon_session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [target()]})
            write_json(latest_live / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [target(key="newer")]})

            chosen = live_session_core.choose_highlighter_session(daemon_session, latest_live)

        self.assertEqual(chosen, daemon_session)

    def test_readiness_core_exposes_canonical_contract_keys(self):
        import live_readiness_core

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            session = root / "session"
            marker = target()
            write_json(session / "manifest.json", {"sessionId": "session"})
            write_json(session / "interaction_geometry" / "live" / "overlay_debug_state.json", {"markers": [marker]})
            status = {
                "sessionPath": str(session),
                "latestTick": 10,
                "candidateCount": 1,
                "profileCandidateCount": 1,
                "broadCandidateCount": 1,
                "writeDebugLiveFiles": False,
                "noFileDaily": True,
                "overlayStateWritten": True,
                "inputGeometry": {
                    "inputGeometryAvailable": True,
                    "canvasScreenOrigin": {"x": 1000, "y": 2000},
                    "canvasSize": {"width": 800, "height": 600},
                },
                "brain": {
                    "latestTick": 10,
                    "freshnessDomains": {"targetCandidateFreshness": "fresh"},
                    "genericTaskState": {"activeIntentTarget": marker},
                    "inventoryContext": {"inventoryFull": False, "freeSlots": 15},
                    "bankUiContext": {"bankOpen": False},
                    "intentOverlayContext": {"selectedMarker": marker},
                },
            }

            report = live_readiness_core.build_readiness_report(daemon_status=status, sessions_dir=root)

        self.assertEqual(report["schema"], "live_readiness.v2")
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["ready"])
        self.assertEqual(report["currentIntent"], "resource_object_action")
        self.assertEqual(report["actionReadiness"]["status"], "PASS")
        self.assertTrue(report["actionReadiness"]["executionAllowed"])
        self.assertIn("session", report)
        self.assertIn("liveFiles", report)
        self.assertIn("candidates", report)
        self.assertIn("highlighter", report)
        self.assertIn("freshness", report)
        self.assertTrue(report["actionExecution"]["allowed"])

    def test_execute_next_action_accepts_canonical_dry_run_flags(self):
        import execute_next_action

        args = execute_next_action.parse_args(
            [
                "--daemon-url",
                "http://127.0.0.1:8890",
                "--dry-run",
                "--explain-target",
                "--verify-coordinates",
            ]
        )

        self.assertTrue(args.dry_run)
        self.assertTrue(args.explain_target)
        self.assertTrue(args.verify_coordinates)

    def test_stabilization_suite_declares_behavior_categories(self):
        import run_stabilization_suite

        names = [group["name"] for group in run_stabilization_suite.COMMAND_GROUPS]

        self.assertIn("path/session resolution", names)
        self.assertIn("candidate classification/scoring", names)
        self.assertIn("readiness gate", names)
        self.assertIn("diagnostics smoke", names)
        self.assertEqual(
            sum(len(group["commands"]) for group in run_stabilization_suite.COMMAND_GROUPS),
            len(run_stabilization_suite.COMMANDS),
        )


if __name__ == "__main__":
    unittest.main()
