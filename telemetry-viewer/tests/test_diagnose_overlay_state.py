import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_overlay_state as diagnose


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def args(**overrides):
    values = {
        "class_id": "tree",
        "name_contains": None,
        "id": None,
        "show_blocked": False,
        "show_reachable": False,
        "show_unknown": False,
        "top": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def candidate(*, direct="reachable", name="Oak tree", object_key="oak-a", tick=10):
    return {
        "tickId": tick,
        "rank": 1,
        "name": name,
        "id": 10820,
        "objectKey": object_key,
        "classId": "oak_tree",
        "category": "tree",
        "worldX": 3200,
        "worldY": 3201,
        "plane": 0,
        "sceneX": 10,
        "sceneY": 11,
        "distanceTiles": 1,
        "targetLiveState": "live_assumed",
        "navigation": {
            "directReachability": direct,
            "pathLengthTiles": 1,
            "targetInCollisionWindow": True,
            "reachabilityEvidence": ["reachable adjacent interaction tile found"],
            "missingNavigationFields": [],
        },
    }


class DiagnoseOverlayStateTest(unittest.TestCase):
    def test_matching_overlay_and_candidate_reports_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            cand = candidate()
            write_jsonl(live_dir / "live_candidates.jsonl", [cand])
            overlay_target = dict(
                cand,
                directReachability="reachable",
                overlayLabel="Oak tree d1 R assumed",
                overlayColor="green",
                clickableHullAvailable=True,
                clickableHull=[[10, 10], [20, 10], [20, 20], [10, 20]],
                clickboxPolygon=[[10, 10], [20, 10], [20, 20], [10, 20]],
            )
            write_json(live_dir / "overlay_debug_state.json", {"schema": "telemetry_overlay_debug_state.v1", "latestTick": 10, "targets": [overlay_target]})
            write_json(live_dir / "live_status.json", {"lastProcessedTick": 10})
            write_json(live_dir / "live_context_index.json", {"latestTick": 10})
            write_json(live_dir / "live_navigation_summary.json", {"status": "local", "collisionKnown": True, "collisionWindowAvailable": True, "collisionWindowRadius": 24, "reachabilityComputed": True})

            report = diagnose.build_report(session, args())

            self.assertEqual(report["rows"][0]["candidateDirectReachability"], "reachable")
            self.assertEqual(report["rows"][0]["overlayDirectReachability"], "reachable")
            self.assertEqual(report["overlayGeometrySummary"]["clickableHullAvailableCount"], 1)
            self.assertEqual(report["overlayGeometrySummary"]["clickableHullFieldCount"], 1)
            self.assertEqual(report["overlayGeometrySummary"]["geometrySourceCounts"]["clickableHull"], 1)
            self.assertEqual(report["rows"][0]["overlayGeometrySource"], "clickableHull")
            self.assertTrue(report["rows"][0]["clickableHullAvailable"])
            self.assertIn("consistent", report["conclusions"][0])

    def test_detects_overlay_candidate_mismatch_and_stale_tick(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            cand = candidate(direct="reachable", tick=12)
            write_jsonl(live_dir / "live_candidates.jsonl", [cand])
            write_json(live_dir / "overlay_debug_state.json", {"schema": "telemetry_overlay_debug_state.v1", "latestTick": 10, "targets": [dict(cand, directReachability="blocked", overlayLabel="Oak tree d1 BLOCK assumed", overlayColor="red")]})
            write_json(live_dir / "live_status.json", {"lastProcessedTick": 12})
            write_json(live_dir / "live_context_index.json", {"latestTick": 12})
            write_json(live_dir / "live_navigation_summary.json", {"status": "local", "collisionKnown": True, "collisionWindowAvailable": True})

            report = diagnose.build_report(session, args(name_contains="Oak"))

            self.assertTrue(report["overlayStateStale"])
            self.assertIn("overlay stale", " ".join(report["conclusions"]))
            self.assertIn("overlay label mismatch", " ".join(report["conclusions"]))

    def test_reports_missing_hull_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            cand = candidate()
            write_jsonl(live_dir / "live_candidates.jsonl", [cand])
            write_json(
                live_dir / "overlay_debug_state.json",
                {
                    "schema": "telemetry_overlay_debug_state.v1",
                    "latestTick": 10,
                    "targets": [
                        dict(
                            cand,
                            directReachability="reachable",
                            bounds={"x": 10, "y": 10, "width": 20, "height": 20},
                            clickableHullAvailable=False,
                            clickableHullMissingReason="clickbox polygon not present",
                        )
                    ],
                },
            )
            write_json(live_dir / "live_status.json", {"lastProcessedTick": 10})
            write_json(live_dir / "live_context_index.json", {"latestTick": 10})
            write_json(live_dir / "live_navigation_summary.json", {"status": "local", "collisionKnown": True, "collisionWindowAvailable": True})

            report = diagnose.build_report(session, args())

            self.assertEqual(report["overlayGeometrySummary"]["boundsOnlyCount"], 1)
            self.assertEqual(report["overlayGeometrySummary"]["missingHullCount"], 1)
            self.assertEqual(report["overlayGeometrySummary"]["firstMissingHullReason"], "clickbox polygon not present")
            self.assertEqual(report["rows"][0]["overlayGeometrySource"], "bounds")
            self.assertEqual(report["rows"][0]["clickableHullMissingReason"], "clickbox polygon not present")


if __name__ == "__main__":
    unittest.main()
