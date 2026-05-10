import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import diagnose_overlay_geometry as diagnose


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def candidate(key: str, rank: int = 1) -> dict:
    return {
        "tickId": 10,
        "rank": rank,
        "objectKey": key,
        "id": 1276,
        "name": "Tree",
        "classId": "tree",
        "category": "tree",
        "worldX": 3200 + rank,
        "worldY": 3201,
        "plane": 0,
        "sceneX": rank,
        "sceneY": 1,
        "aimPoint": {"canvasX": 100 + rank, "canvasY": 120},
        "bounds": {"x": 95 + rank, "y": 115, "width": 10, "height": 10},
        "navigation": {"directReachability": "reachable"},
        "targetLiveState": "live_assumed",
    }


def compact_ref(key: str, rank: int = 1, *, with_hull: bool = True) -> dict:
    ref = {
        "objectKey": key,
        "id": 1276,
        "name": "Tree",
        "worldX": 3200 + rank,
        "worldY": 3201,
        "plane": 0,
        "sceneX": rank,
        "sceneY": 1,
        "aimPoint": {"canvasX": 100 + rank, "canvasY": 120},
        "bounds": {"x": 95 + rank, "y": 115, "width": 10, "height": 10},
    }
    if with_hull:
        ref["clickableHull"] = {"points": [{"x": 90, "y": 110}, {"x": 110, "y": 110}, {"x": 110, "y": 130}, {"x": 90, "y": 130}]}
    return ref


class DiagnoseOverlayGeometryTest(unittest.TestCase):
    def test_detects_cap_order_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            packet_dir = session / "live_packets"
            top = candidate("top-tree", 1)
            write_jsonl(live_dir / "live_candidates.jsonl", [top])
            write_json(live_dir / "overlay_debug_state.json", {"targets": [dict(top, clickableHullAvailable=False, geometrySource="bounds")]})
            write_json(live_dir / "live_status.json", {"candidateHullMissing": 1, "compactHullRefsAvailable": 1, "compactHullRefsUnused": 1})
            write_jsonl(
                packet_dir / "live-000001.ndjson",
                [
                    {
                        "packetType": "live_projection_packet.v1",
                        "tick": 10,
                        "sequence": 1,
                        "payload": {"visibleObjectRefs": [compact_ref("corner-tree", 20, with_hull=True), compact_ref("top-tree", 1, with_hull=False)]},
                    }
                ],
            )
            (packet_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")

            report = diagnose.build_report(session, "tree", 5)

            self.assertEqual(report["summary"]["compactRefsWithHull"], 1)
            self.assertEqual(report["summary"]["topCandidatesWithHull"], 0)
            self.assertFalse(report["summary"]["bestTargetHasHull"])
            self.assertEqual(report["summary"]["hullsByRankBucket"]["rank1"], 0)
            self.assertIn("geometry cap/order mismatch", " ".join(report["conclusions"]))

    def test_reports_top_candidate_with_matching_hull(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            packet_dir = session / "live_packets"
            top = candidate("top-tree", 1)
            overlay_target = dict(top, clickableHullAvailable=True, clickableHull={"points": [{"x": 90, "y": 110}, {"x": 110, "y": 110}, {"x": 110, "y": 130}, {"x": 90, "y": 130}]})
            write_jsonl(live_dir / "live_candidates.jsonl", [top])
            write_json(live_dir / "overlay_debug_state.json", {"targets": [overlay_target]})
            write_json(live_dir / "live_status.json", {"candidateHullDirectMatches": 1})
            write_jsonl(
                packet_dir / "live-000001.ndjson",
                [
                    {
                        "packetType": "live_projection_packet.v1",
                        "tick": 10,
                        "sequence": 1,
                        "payload": {"visibleObjectRefs": [compact_ref("top-tree", 1, with_hull=True)]},
                    }
                ],
            )
            (packet_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")

            report = diagnose.build_report(session, "tree", 5)

            self.assertEqual(report["summary"]["topCandidatesWithHull"], 1)
            self.assertEqual(report["summary"]["overlayTargetsWithHull"], 1)
            self.assertTrue(report["summary"]["bestTargetHasHull"])
            self.assertEqual(report["summary"]["hullsByRankBucket"]["rank1"], 1)
            self.assertTrue(report["rows"][0]["matchedCompactPacketGeometryByObjectKey"])

    def test_reports_hull_rank_buckets_and_priority_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            live_dir = session / "interaction_geometry" / "live"
            packet_dir = session / "live_packets"
            candidates = [candidate("tree-1", 1), candidate("tree-2", 2), candidate("tree-6", 6), candidate("tree-11", 11)]
            overlay_targets = [
                dict(candidates[0], isBest=True, clickableHullAvailable=True, clickableHull={"points": [{"x": 90, "y": 110}, {"x": 110, "y": 110}, {"x": 110, "y": 130}, {"x": 90, "y": 130}]}),
                dict(candidates[1], isNearest=True, clickableHullAvailable=True, clickableHull={"points": [{"x": 91, "y": 110}, {"x": 111, "y": 110}, {"x": 111, "y": 130}, {"x": 91, "y": 130}]}),
                dict(candidates[2], clickableHullAvailable=False, geometrySource="bounds"),
                dict(candidates[3], clickableHullAvailable=True, clickableHull={"points": [{"x": 92, "y": 110}, {"x": 112, "y": 110}, {"x": 112, "y": 130}, {"x": 92, "y": 130}]}),
            ]
            write_jsonl(live_dir / "live_candidates.jsonl", candidates)
            write_json(live_dir / "overlay_debug_state.json", {"targets": overlay_targets})
            write_json(live_dir / "live_status.json", {"compactLiveGeometryMaxRefs": 50, "compactLiveHullsEmitted": 3})
            write_jsonl(
                packet_dir / "live-000001.ndjson",
                [
                    {
                        "packetType": "live_projection_packet.v1",
                        "tick": 10,
                        "sequence": 1,
                        "payload": {"visibleObjectRefs": [compact_ref("tree-1", 1), compact_ref("tree-2", 2), compact_ref("tree-11", 11)]},
                    }
                ],
            )
            (packet_dir / "latest_segment.txt").write_text("live-000001.ndjson\n", encoding="utf-8")

            report = diagnose.build_report(session, "tree", 20)

            self.assertTrue(report["summary"]["bestTargetHasHull"])
            self.assertTrue(report["summary"]["nearestTargetHasHull"])
            self.assertEqual(report["summary"]["hullsByRankBucket"]["rank1"], 1)
            self.assertEqual(report["summary"]["hullsByRankBucket"]["ranks2to5"], 1)
            self.assertEqual(report["summary"]["hullsByRankBucket"]["ranks11plus"], 1)
            self.assertEqual(len(report["summary"]["topTargetsWithHull"]), 3)


if __name__ == "__main__":
    unittest.main()
