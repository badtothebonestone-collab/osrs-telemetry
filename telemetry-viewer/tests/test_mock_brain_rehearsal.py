import json
import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import mock_brain_rehearsal as brain


def candidate(
    *,
    reachability: str = "reachable",
    live_state: str = "live_assumed",
    distance: int = 1,
    in_window: bool | None = True,
    aim: bool = True,
) -> dict:
    value = {
        "classId": "tree",
        "targetName": "Tree",
        "id": 1278,
        "worldX": 3221,
        "worldY": 3242,
        "plane": 0,
        "sceneX": 48,
        "sceneY": 47,
        "distanceTiles": distance,
        "onScreen": True,
        "geometryAvailable": True,
        "uiBlocked": False,
        "qualityTier": "excellent",
        "qualityScore": 98,
        "targetLiveState": live_state,
        "livenessInterpretation": "assumed" if live_state == "live_assumed" else "direct",
        "navigation": {
            "directReachability": reachability,
            "pathLengthTiles": distance if reachability == "reachable" else None,
            "targetInCollisionWindow": in_window,
            "reachabilityConfidence": 0.9 if reachability == "reachable" else 0.75,
        },
    }
    if aim:
        value["aimPoint"] = {"canvasX": 327.5, "canvasY": 113.0, "source": "clickboxCenter"}
    return value


MISSING = object()


def context_response(
    *,
    best: dict | None | object = MISSING,
    nearest: dict | None | object = MISSING,
    inventory: dict | None = None,
    activity: dict | None = None,
    woodcutting: dict | None = None,
    freshness: dict | None = None,
    events: list[dict] | None = None,
    reachability_summary: dict | None = None,
    liveness: dict | None = None,
) -> dict:
    best = candidate() if best is MISSING else best
    nearest = best if nearest is MISSING else nearest
    response = {
        "schema": "context_response.v1",
        "status": "PASS",
        "latestTick": 20,
        "freshness": freshness if freshness is not None else {"freshByTicks": True, "freshByMillis": True},
        "baseline": {"player": {"worldX": 3220, "worldY": 3241, "plane": 0, "sceneX": 48, "sceneY": 47}},
        "inventory": inventory
        if inventory is not None
        else {"known": True, "freeSlots": 12, "filledSlots": 16, "inventoryFull": False, "changedRecently": False},
        "activity": activity if activity is not None else {"apparentState": "unknown", "animation": -1},
        "woodcuttingState": woodcutting if woodcutting is not None else {"woodcuttingState": "unknown"},
        "navigationReadiness": {
            "status": "local",
            "collisionKnown": True,
            "collisionWindowAvailable": True,
            "reachabilityComputed": True,
            "fullCollisionGridAvailable": False,
        },
        "reachabilitySummary": reachability_summary
        if reachability_summary is not None
        else {"tree": {"candidateCount": 3, "reachableCount": 3, "blockedCount": 0, "unknownCount": 0}},
        "liveness": liveness
        if liveness is not None
        else {"livenessMode": "delta", "suppressedCandidateCount": 0, "livenessDegraded": False},
        "warnings": [],
        "missingCapabilities": ["fullPathfinding"],
        "recentEvents": events if events is not None else [],
    }
    if best is not None:
        response["bestCandidates"] = {"tree": best}
    if nearest is not None:
        response["nearestCandidates"] = {"tree": nearest}
    return response


def event(event_type: str, summary: str, tick: int = 20, severity: str = "info") -> dict:
    return {"tick": tick, "eventType": event_type, "summary": summary, "severity": severity}


class MockBrainRehearsalTest(unittest.TestCase):
    def test_context_request_body_construction(self):
        request = brain.build_context_request("woodcutting", max_candidates=4, max_events=6)
        self.assertEqual(request["schema"], "context_request.v1")
        self.assertEqual(request["task"], "woodcutting")
        self.assertEqual(request["maxCandidates"], 4)
        self.assertEqual(request["maxEvents"], 6)
        self.assertIn("best:tree", request["needs"])
        self.assertIn("events", request["needs"])

    def test_target_available_phase(self):
        result = brain.evaluate_response(context_response(), goal_count=5)
        self.assertEqual(result["schema"], "mock_brain_rehearsal.v1")
        self.assertEqual(result["phase"], "target_available")
        self.assertEqual(result["substate"], "liveness_assumed")
        self.assertEqual(result["blockingConditions"], [])
        self.assertTrue(result["noActionEmitted"])

    def test_inventory_full_phase(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        self.assertEqual(brain.evaluate_response(response)["phase"], "inventory_full")

    def test_no_target_phase(self):
        response = context_response(best=None, nearest=None, reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0}})
        self.assertEqual(brain.evaluate_response(response)["phase"], "no_target_observed")

    def test_target_unreachable_phase(self):
        response = context_response(
            best=candidate(reachability="blocked"),
            nearest=candidate(reachability="blocked", distance=2),
            reachability_summary={"tree": {"candidateCount": 2, "reachableCount": 0, "blockedCount": 2}},
        )
        self.assertEqual(brain.evaluate_response(response)["phase"], "target_unreachable")

    def test_target_depleted_phase(self):
        response = context_response(
            best=candidate(live_state="depleted_or_stump"),
            nearest=candidate(live_state="depleted_or_stump", distance=2),
            reachability_summary={"tree": {"candidateCount": 2, "reachableCount": 0}},
        )
        self.assertEqual(brain.evaluate_response(response)["phase"], "target_depleted")

    def test_likely_busy_phase(self):
        response = context_response(activity={"apparentState": "animating", "animation": 867})
        self.assertEqual(brain.evaluate_response(response)["phase"], "likely_busy")

    def test_likely_idle_phase(self):
        response = context_response(activity={"apparentState": "idle", "animation": -1})
        self.assertEqual(brain.evaluate_response(response)["phase"], "target_available")

    def test_inventory_changed_phase(self):
        response = context_response(
            inventory={"known": True, "freeSlots": 11, "filledSlots": 17, "inventoryFull": False, "changedRecently": True}
        )
        result = brain.evaluate_response(response)
        self.assertEqual(result["phase"], "target_available")
        self.assertEqual(result["substate"], "recent_inventory_change")

    def test_recent_target_depletion_with_replacement_is_substate(self):
        response = context_response(
            best=candidate(reachability="reachable", live_state="live_assumed"),
            activity={"apparentState": "interacting", "animation": -1},
            events=[event("target_depleted", "Target depleted: Tree became stump", tick=19)],
        )
        result = brain.evaluate_response(response)
        self.assertEqual(result["phase"], "target_available")
        self.assertEqual(result["substate"], "recent_target_depletion_observed")
        self.assertEqual(result["currentTargetState"]["liveState"], "live_assumed")

    def test_interacting_unknown_does_not_make_busy(self):
        response = context_response(
            activity={
                "apparentState": "interacting",
                "animation": None,
                "interacting": None,
                "evidence": ["interacting=UNKNOWN"],
            }
        )
        result = brain.evaluate_response(response)
        self.assertEqual(result["phase"], "target_available")
        self.assertIn("interacting_unknown_not_busy", result["substates"])
        self.assertEqual(result["activitySummary"]["apparentState"], "unknown")
        self.assertFalse(result["activitySummary"]["trueBusyEvidence"])

    def test_interacting_null_and_animation_null_do_not_make_busy(self):
        response = context_response(activity={"apparentState": "unknown", "animation": None, "interacting": None})
        result = brain.evaluate_response(response)
        self.assertEqual(result["phase"], "target_available")
        self.assertIn("activity_unknown", result["substates"])

    def test_animation_minus_one_does_not_make_busy(self):
        response = context_response(activity={"apparentState": "unknown", "animation": -1, "interacting": None})
        result = brain.evaluate_response(response)
        self.assertEqual(result["phase"], "target_available")
        self.assertIn("no_explicit_busy_evidence", result["substates"])

    def test_explicit_interacting_target_makes_busy(self):
        response = context_response(activity={"apparentState": "interacting", "animation": -1, "interacting": {"name": "Tree", "id": 1276}})
        self.assertEqual(brain.evaluate_response(response)["phase"], "likely_busy")

    def test_stale_context_phase(self):
        response = context_response(freshness={"freshByTicks": False, "freshByMillis": True})
        self.assertEqual(brain.evaluate_response(response)["phase"], "stale_context")

    def test_system_events_hidden_by_default(self):
        response = context_response(
            events=[
                event("budget_exceeded_changed", "Realtime budget warning toggled", tick=18, severity="warn"),
                event("best_candidate_changed", "Best tree changed", tick=19),
            ]
        )
        result = brain.evaluate_response(response)
        self.assertEqual(result["eventPriority"], "task")
        self.assertEqual(result["systemEventCount"], 1)
        self.assertEqual([item["eventType"] for item in result["recentTaskSignals"]], ["best_candidate_changed"])
        self.assertEqual(result["recentSystemSignals"], [])

    def test_event_priority_all_shows_system_events(self):
        response = context_response(events=[event("budget_exceeded_changed", "Realtime budget warning toggled", tick=18, severity="warn")])
        result = brain.evaluate_response(response, event_priority="all")
        self.assertEqual(result["eventPriority"], "all")
        self.assertEqual(result["recentSystemSignals"][0]["eventType"], "budget_exceeded_changed")

    def test_missing_capabilities_not_duplicated_into_warnings(self):
        response = context_response()
        response["warnings"] = ["fullPathfinding", "runtime warning"]
        result = brain.evaluate_response(response)
        self.assertEqual(result["missingCapabilities"], ["fullPathfinding"])
        self.assertEqual(result["warnings"], ["runtime warning"])

    def test_json_result_has_no_action_command_fields(self):
        result = brain.evaluate_response(context_response())
        raw = json.dumps(result)
        self.assertNotIn("clickCommand", raw)
        self.assertNotIn("mouseCommand", raw)
        self.assertNotIn("keyboardCommand", raw)
        self.assertNotIn("menuCommand", raw)
        self.assertNotIn("moveCommand", raw)
        self.assertNotIn("executeCommand", raw)
        self.assertTrue(result["noActionEmitted"])

    def test_human_output_says_no_action_emitted(self):
        output = brain.format_human(brain.evaluate_response(context_response()))
        self.assertIn("No action emitted.", output)
        self.assertIn("WOODCUTTING REHEARSAL", output)
        self.assertIn("Current target:", output)
        self.assertIn("Recent task signals:", output)
        self.assertIn("no explicit busy evidence", output)

    def test_watch_status_line_is_compact(self):
        line = brain.format_watch_line(brain.evaluate_response(context_response()))
        self.assertIn("phase=target_available", line)
        self.assertIn("substate=liveness_assumed", line)
        self.assertIn("freeSlots=12", line)


if __name__ == "__main__":
    unittest.main()
