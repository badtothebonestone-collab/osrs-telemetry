import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import brain_core as brain


MISSING = object()


def candidate(
    *,
    reachability: str = "reachable",
    live_state: str = "live_assumed",
    distance: int = 1,
    aim: bool = True,
    on_screen: bool = True,
    geometry: bool = True,
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
        "onScreen": on_screen,
        "geometryAvailable": geometry,
        "qualityTier": "excellent",
        "targetLiveState": live_state,
        "livenessInterpretation": "assumed" if live_state == "live_assumed" else "direct",
        "navigation": {
            "directReachability": reachability,
            "pathLengthTiles": distance if reachability == "reachable" else None,
            "targetInCollisionWindow": reachability != "unknown_outside",
            "reachabilityConfidence": 0.9 if reachability == "reachable" else 0.65,
        },
    }
    if aim:
        value["aimPoint"] = {"canvasX": 327.5, "canvasY": 113.0, "source": "clickboxCenter"}
    return value


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
    missing: list[str] | None = None,
    suggestions: list[dict] | None = None,
    status: str = "PASS",
) -> dict:
    best = candidate() if best is MISSING else best
    nearest = best if nearest is MISSING else nearest
    response = {
        "schema": "context_response.v1",
        "status": status,
        "latestTick": 42,
        "freshness": freshness if freshness is not None else {"freshByTicks": True, "freshByMillis": True},
        "baseline": {"player": {"worldX": 3220, "worldY": 3241, "plane": 0, "sceneX": 48, "sceneY": 47}},
        "inventory": inventory
        if inventory is not None
        else {
            "known": True,
            "freeSlots": 12,
            "filledSlots": 16,
            "inventoryFull": False,
            "changedRecently": False,
            "inventorySignature": "inv-a",
        },
        "activity": activity if activity is not None else {"apparentState": "unknown", "animation": -1, "interacting": None},
        "woodcuttingState": woodcutting if woodcutting is not None else {"woodcuttingState": "unknown"},
        "navigationReadiness": {
            "status": "local",
            "collisionKnown": True,
            "collisionWindowAvailable": True,
            "reachabilityComputed": True,
        },
        "reachabilitySummary": reachability_summary
        if reachability_summary is not None
        else {"tree": {"candidateCount": 3, "reachableCount": 3, "blockedCount": 0, "unknownCount": 0}},
        "liveness": {"livenessMode": "delta", "suppressedCandidateCount": 0, "livenessDegraded": False},
        "warnings": [],
        "missingCapabilities": missing if missing is not None else ["fullPathfinding"],
        "suggestedWatchRequests": suggestions if suggestions is not None else [],
        "recentEvents": events if events is not None else [],
    }
    if best is not None:
        response["bestCandidates"] = {"tree": best}
    if nearest is not None:
        response["nearestCandidates"] = {"tree": nearest}
    return response


def inventory_with_items(items: list[dict], **overrides) -> dict:
    filled = len([item for item in items if item.get("itemId", item.get("id")) not in (None, -1, 0)])
    value = {
        "known": True,
        "freeSlots": max(0, 28 - filled),
        "filledSlots": filled,
        "inventoryFull": filled >= 28,
        "changedRecently": False,
        "inventorySignature": "|".join(f"{item.get('slot')}:{item.get('itemId', item.get('id'))}:{item.get('quantity', 1)}" for item in items),
        "items": items,
    }
    value.update(overrides)
    return value


def log_item(slot: int, item_id: int = 1511, quantity: int = 1) -> dict:
    return {"slot": slot, "itemId": item_id, "quantity": quantity}


def inventory_with_resource_count(count: int, signature=MISSING, **overrides) -> dict:
    value = {
        "known": True,
        "freeSlots": max(0, 28 - count),
        "filledSlots": count,
        "inventoryFull": count >= 28,
        "inventorySlotCount": 28,
        "slotCount": 28,
        "resourceCounts": {
            "woodcutting_logs": {
                "count": count,
                "matchedItemIds": [1511],
                "byItemId": {"1511": count},
                "matchedSlots": list(range(count)),
            }
        },
    }
    if signature is not MISSING:
        value["inventorySignature"] = signature
    value.update(overrides)
    return value


def event(event_type: str, summary: str, tick: int = 42, severity: str = "info") -> dict:
    return {"tick": tick, "eventType": event_type, "summary": summary, "severity": severity}


def evaluate(response: dict, state: dict | None = None, **kwargs) -> tuple[dict, dict]:
    state = state or brain.default_state("woodcutting", 5)
    return brain.evaluate_brain(response, state, task="woodcutting", goal_count=5, **kwargs)


def walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_keys(nested)


class BrainCoreTest(unittest.TestCase):
    def test_context_request_body_construction(self):
        request = brain.build_context_request("woodcutting", max_candidates=4, max_events=6)
        self.assertEqual(request["schema"], "context_request.v1")
        self.assertEqual(request["task"], "woodcutting")
        self.assertEqual(request["maxCandidates"], 4)
        self.assertEqual(request["maxEvents"], 6)
        self.assertIn("best:tree", request["needs"])
        self.assertIn("reachability:tree", request["needs"])
        self.assertIn("events", request["needs"])
        self.assertIn("watches", request["needs"])

    def test_target_available_phase(self):
        decision, state = evaluate(context_response())
        self.assertEqual(decision["schema"], "brain_decision.v1")
        self.assertEqual(decision["phase"], "target_available")
        self.assertEqual(decision["genericTaskState"]["phase"], "target_selected")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "continue_current_target")
        self.assertEqual(decision["substate"], "liveness_assumed")
        self.assertEqual(decision["blockingConditions"], [])
        self.assertTrue(decision["noActionEmitted"])
        self.assertTrue(decision["genericTaskState"]["noActionEmitted"])
        self.assertEqual(state["latestTick"], 42)

    def test_likely_busy_phase_with_explicit_evidence(self):
        response = context_response(activity={"apparentState": "interacting", "animation": -1, "interacting": {"name": "Tree", "id": 1278}})
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "likely_busy")
        self.assertIn("explicit interacting target present", " ".join(decision["observations"] + brain.activity_busy_analysis(brain.activity_summary(response))[1]))

    def test_unknown_interaction_does_not_make_busy(self):
        response = context_response(
            activity={
                "apparentState": "interacting",
                "animation": None,
                "interacting": None,
                "evidence": ["interacting=UNKNOWN"],
            }
        )
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "target_available")
        self.assertIn(decision["substate"], {"activity_unknown", "interacting_unknown_not_busy", "movement_unknown", "no_explicit_busy_evidence"})

    def test_inventory_full_phase(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "needs_service")
        self.assertEqual(decision["genericTaskState"]["serviceTypeNeeded"], "bank")
        self.assertIsNone(decision["genericTaskState"]["selectedTargetKey"])
        self.assertIsNone(decision["genericTaskState"]["activeIntentTarget"])
        self.assertEqual(decision["genericTaskState"]["availableTarget"]["id"], 1278)
        self.assertIn("inventory is full", decision["blockingConditions"])

    def test_inventory_full_human_output_does_not_label_tree_current_target(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response)
        decision["serviceContext"] = {"serviceNeeded": True, "candidateCount": 0}

        output = brain.format_human(decision)

        self.assertIn("Active intent: needs service", output)
        self.assertIn("Task policy: bank resources", output)
        self.assertIn("Service needed: bank", output)
        self.assertIn("Service candidate: not observed", output)
        self.assertIn("Missing/needed context: bank_service candidate", output)
        self.assertIn("Previous target: Tree 1278, reachable", output)
        self.assertIn("Available target: Tree 1278, reachable", output)
        self.assertNotIn("Current target: Tree 1278", output)

    def test_inventory_full_human_output_shows_observed_service_candidate(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response)
        decision["genericTaskState"]["activeIntentTarget"] = {
            "name": "Bank booth",
            "id": 10355,
            "directReachability": "reachable",
        }
        decision["serviceContext"] = {
            "serviceNeeded": True,
            "bestServiceCandidate": {
                "name": "Bank booth",
                "id": 10355,
                "directReachability": "reachable",
            },
        }
        decision["navigationIntentContext"] = {
            "navigationNeeded": True,
            "navigationReason": "service_target_available",
            "targetKind": "service",
            "destinationTarget": {
                "name": "Bank booth",
                "id": 10355,
                "directReachability": "reachable",
            },
            "distanceTiles": 4,
            "directReachability": "reachable",
            "collisionWindowAvailable": True,
            "missingCapabilities": [],
        }

        output = brain.format_human(decision)

        self.assertIn("Best service candidate: Bank booth 10355, reachable", output)
        self.assertIn("Navigation context:", output)
        self.assertIn("Destination: Bank booth 10355, reachable", output)
        self.assertIn("Distance: 4 tiles", output)
        self.assertIn("Reachability: reachable", output)
        self.assertIn("Collision window: available", output)

    def test_inventory_full_firemaking_policy_processes_inventory_context_only(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response, task_policy="woodcutting_firemake")

        self.assertEqual(decision["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(decision["genericTaskState"]["processTypeNeeded"], "firemaking")
        self.assertEqual(decision["genericTaskState"]["resourceDisposition"], "burn")
        self.assertIsNone(decision["genericTaskState"]["activeIntentTarget"])

        output = brain.format_human(decision)
        self.assertIn("Task policy: burn resources", output)
        self.assertIn("Active intent: process inventory", output)
        self.assertIn("Process needed: firemaking", output)
        self.assertIn("No service target required", output)
        self.assertNotIn("Current target: Tree 1278", output)

    def test_inventory_full_drop_policy_processes_inventory_context_only(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response, task_policy="woodcutting_drop")

        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(decision["genericTaskState"]["processTypeNeeded"], "drop")
        self.assertEqual(decision["genericTaskState"]["resourceDisposition"], "drop")
        self.assertIsNone(decision["genericTaskState"]["activeIntentTarget"])

    def test_firemake_full_inventory_with_stale_missing_tree_candidates_processes_inventory(self):
        items = [log_item(slot) for slot in range(27)] + [{"slot": 27, "itemId": 590, "quantity": 1}]
        response = context_response(
            best=None,
            nearest=None,
            inventory=inventory_with_items(items),
            freshness={"freshByTicks": False, "freshByMillis": True},
            reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0, "blockedCount": 0, "unknownCount": 0}},
        )

        decision, _state = evaluate(response, task_policy="woodcutting_firemake")

        self.assertEqual(decision["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(decision["genericTaskState"]["processTypeNeeded"], "firemaking")
        self.assertNotIn("context freshness failed", decision["blockingConditions"])
        self.assertIn("no tree candidates currently observed", decision["warnings"])
        self.assertEqual(decision["freshnessDomains"]["inventoryFreshness"], "fresh")
        self.assertIn(decision["freshnessDomains"]["targetCandidateFreshness"], {"stale", "unknown"})

    def test_drop_full_inventory_with_stale_missing_tree_candidates_processes_inventory(self):
        response = context_response(
            best=None,
            nearest=None,
            inventory=inventory_with_items([log_item(slot) for slot in range(28)]),
            freshness={"freshByTicks": False, "freshByMillis": True},
            reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0, "blockedCount": 0, "unknownCount": 0}},
        )

        decision, _state = evaluate(response, task_policy="woodcutting_drop")

        self.assertEqual(decision["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(decision["genericTaskState"]["processTypeNeeded"], "drop")
        self.assertNotIn("context freshness failed", decision["blockingConditions"])

    def test_bank_full_inventory_with_stale_missing_tree_candidates_still_needs_service(self):
        response = context_response(
            best=None,
            nearest=None,
            inventory=inventory_with_items([log_item(slot) for slot in range(28)]),
            freshness={"freshByTicks": False, "freshByMillis": True},
            reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0, "blockedCount": 0, "unknownCount": 0}},
        )

        decision, _state = evaluate(response, task_policy="woodcutting_bank")

        self.assertEqual(decision["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "needs_service")
        self.assertIsNone(decision["genericTaskState"]["activeIntentTarget"])
        self.assertNotIn("context freshness failed", decision["blockingConditions"])

    def test_process_inventory_policy_needs_inventory_when_inventory_unknown(self):
        response = context_response(
            best=None,
            nearest=None,
            inventory={"known": False},
            freshness={"freshByTicks": False, "freshByMillis": True},
            reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0}},
        )

        decision, _state = evaluate(response, task_policy="woodcutting_firemake")

        self.assertEqual(decision["phase"], "stale_context")
        self.assertNotEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")

    def test_inventory_full_combat_policy_continues_task(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        state = brain.default_state("combat", None)
        decision, _state = brain.evaluate_brain(response, state, task="combat", goal_count=None, task_policy="combat_default")

        self.assertEqual(decision["genericTaskState"]["activeIntent"], "continue_task")
        self.assertEqual(decision["genericTaskState"]["phase"], "target_selected")
        self.assertIsNotNone(decision["genericTaskState"]["activeIntentTarget"])
        self.assertNotEqual(decision["genericTaskState"].get("serviceTypeNeeded"), "bank")

        output = brain.format_human(decision)
        self.assertIn("Task policy: continue task", output)
        self.assertIn("Inventory full: expected/allowed", output)
        self.assertIn("Active intent: continue task", output)
        self.assertIn("Current target: Tree 1278", output)

    def test_observe_only_inventory_full_does_not_request_service(self):
        response = context_response(inventory={"known": True, "freeSlots": 0, "filledSlots": 28, "inventoryFull": True})
        decision, _state = evaluate(response, task_policy="observe_only")

        self.assertEqual(decision["genericTaskState"]["phase"], "observe")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "observe")
        self.assertIsNone(decision["genericTaskState"]["activeIntentTarget"])
        self.assertIsNone(decision["genericTaskState"].get("serviceTypeNeeded"))

    def test_no_target_observed_phase(self):
        response = context_response(best=None, nearest=None, reachability_summary={"tree": {"candidateCount": 0, "reachableCount": 0}})
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "no_target_observed")
        self.assertEqual(decision["genericTaskState"]["phase"], "needs_more_context")

    def test_blocked_or_unreachable_phase(self):
        response = context_response(
            best=candidate(reachability="blocked"),
            nearest=candidate(reachability="blocked", distance=2),
            reachability_summary={"tree": {"candidateCount": 2, "reachableCount": 0, "blockedCount": 2}},
        )
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "blocked_or_unreachable")
        self.assertEqual(decision["genericTaskState"]["phase"], "blocked")

    def test_stale_context_phase(self):
        response = context_response(freshness={"freshByTicks": False, "freshByMillis": True})
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "stale_context")

    def test_target_depleted_without_replacement(self):
        response = context_response(
            best=candidate(live_state="depleted_or_stump"),
            nearest=candidate(live_state="stale", distance=2),
            reachability_summary={"tree": {"candidateCount": 2, "reachableCount": 0}},
        )
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "target_depleted")

    def test_recent_depletion_with_replacement_is_substate(self):
        response = context_response(
            best=candidate(reachability="reachable", live_state="live_assumed"),
            events=[event("target_depleted", "Target depleted: Tree became stump", tick=41)],
        )
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "target_available")
        self.assertEqual(decision["substate"], "recent_target_depletion_observed")

    def test_progress_unknown_when_inventory_deltas_missing(self):
        decision, _state = evaluate(context_response())
        self.assertFalse(decision["progress"]["known"])
        self.assertEqual(decision["progress"]["progressSource"], "baseline_pending")
        self.assertIn("item list", decision["progress"]["reason"])

    def test_count_inventory_items_counts_normal_logs_across_slots(self):
        inventory = inventory_with_items([log_item(0, 1511), log_item(1, 1511), log_item(2, 995, 100)])
        result = brain.count_inventory_items(inventory, [1511])
        self.assertTrue(result["known"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["matchedItems"]), 2)

    def test_count_inventory_items_counts_logs_in_edge_and_middle_slots(self):
        for slot in (0, 1, 13, 14, 15, 16, 18, 27):
            with self.subTest(slot=slot):
                inventory = inventory_with_items([log_item(slot, 1511)])
                result = brain.count_inventory_items(inventory, [1511])
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["matchedItems"][0]["slot"], slot)

    def test_count_inventory_items_counts_oak_logs_in_edge_and_middle_slots(self):
        for slot in (0, 1, 13, 14, 15, 16, 18, 27):
            with self.subTest(slot=slot):
                inventory = inventory_with_items([log_item(slot, 1521)])
                result = brain.count_inventory_items(inventory, [1521])
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["matchedItems"][0]["slot"], slot)

    def test_count_inventory_items_counts_willow_log_in_slot_18(self):
        inventory = inventory_with_items([log_item(18, 1519)])
        result = brain.count_inventory_items(inventory, [1519])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["matchedItems"][0]["slot"], 18)

    def test_count_inventory_items_counts_sparse_and_shuffled_slots(self):
        inventory = inventory_with_items([log_item(27, 1511), log_item(0, 1511), log_item(18, 1511)])
        result = brain.count_inventory_items(inventory, [1511])
        self.assertEqual(result["count"], 3)
        self.assertEqual(sorted(item["slot"] for item in result["matchedItems"]), [0, 18, 27])

    def test_count_inventory_items_quantity_missing_counts_as_one(self):
        inventory = inventory_with_items([{"slot": 27, "itemId": 1511}])
        result = brain.count_inventory_items(inventory, [1511])
        self.assertEqual(result["count"], 1)

    def test_count_inventory_items_uses_items_when_resource_counts_disagree(self):
        inventory = inventory_with_items(
            [log_item(0, 1511)],
            resourceCounts={
                "woodcutting_logs": {
                    "displayName": "Woodcutting logs",
                    "itemIds": [1511, 1521, 1519, 1517, 1515, 1513],
                    "count": 2,
                    "matchedItemIds": [1511],
                    "byItemId": {"1511": 2},
                    "matchedSlots": [0, 27],
                    "matchedItems": [log_item(0, 1511), log_item(27, 1511)],
                }
            },
        )
        result = brain.count_inventory_items(inventory, [1511, 1521, 1519, 1517, 1515, 1513])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["source"], "inventory_snapshot_items")
        self.assertEqual(result["matchedSlots"], [0])
        self.assertIn("resourceCounts disagreed", result["warnings"][0])

    def test_count_inventory_items_uses_items_when_resource_counts_agree(self):
        inventory = inventory_with_items(
            [log_item(0, 1511), log_item(27, 1511)],
            resourceCounts={
                "woodcutting_logs": {
                    "displayName": "Woodcutting logs",
                    "itemIds": [1511, 1521, 1519, 1517, 1515, 1513],
                    "count": 2,
                    "matchedItemIds": [1511],
                    "byItemId": {"1511": 2},
                    "matchedSlots": [0, 27],
                }
            },
        )
        result = brain.count_inventory_items(inventory, [1511, 1521, 1519, 1517, 1515, 1513])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["source"], "inventory_snapshot_items")
        self.assertEqual(result["matchedSlots"], [0, 27])

    def test_count_inventory_items_uses_resource_counts_when_items_missing(self):
        inventory = {
            "known": True,
            "freeSlots": 27,
            "filledSlots": 1,
            "resourceCounts": {
                "woodcutting_logs": {
                    "displayName": "Woodcutting logs",
                    "itemIds": [1511, 1521, 1519, 1517, 1515, 1513],
                    "count": 1,
                    "matchedItemIds": [1511],
                    "byItemId": {"1511": 1},
                    "matchedSlots": [18],
                }
            },
        }
        result = brain.count_inventory_items(inventory, [1511, 1521, 1519, 1517, 1515, 1513])
        self.assertTrue(result["known"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["source"], "inventory_resource_counts")
        self.assertIn("item snapshot missing", result["warnings"][0])

    def test_count_inventory_items_counts_woodcutting_log_ids(self):
        inventory = inventory_with_items([log_item(0, 1521), log_item(1, 1519), log_item(2, 1515), log_item(3, 995, 100)])
        group = brain.task_resource_group("woodcutting")
        result = brain.count_inventory_items(inventory, group["itemIds"])
        self.assertEqual(result["count"], 3)

    def test_progress_baseline_set_once_and_increases_from_snapshots(self):
        state = brain.default_state("woodcutting", 5)
        first = context_response(inventory=inventory_with_items([]))
        decision, state = evaluate(first, state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_initialized")
        self.assertTrue(decision["goalProgress"]["baselineEstablished"])

        second = context_response(inventory=inventory_with_items([log_item(0, 1511)]))
        decision, state = evaluate(second, state)
        self.assertEqual(decision["progress"]["currentHeldResourceCount"], 1)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)

        third = context_response(inventory=inventory_with_items([log_item(0, 1511), log_item(1, 1511)]))
        decision, state = evaluate(third, state)
        self.assertEqual(decision["progress"]["currentHeldResourceCount"], 2)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)

    def test_dropping_logs_keeps_monotonic_gained_since_start(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511)])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511), log_item(1, 1511), log_item(2, 1511)])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        self.assertEqual(decision["progress"]["currentHeldResourceCount"], 0)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)
        self.assertLess(decision["progress"]["netResourceChange"], 0)
        self.assertEqual(decision["progress"]["progressHeldReason"], "valid_inventory_count_decreased_retained_monotonic_progress")

    def test_reset_state_resets_progress_baseline(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        reset_state = brain.default_state("woodcutting", 5)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511)])), reset_state)
        self.assertEqual(decision["progress"]["baselineResourceCount"], 1)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertFalse(decision["goalComplete"])
        self.assertEqual(decision["progress"]["progressSource"], "baseline_initialized")

    def test_first_snapshot_after_reset_with_existing_logs_is_not_goal_complete(self):
        state = brain.default_state("woodcutting", 5)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertEqual(decision["progress"]["currentHeldResourceCount"], 5)
        self.assertEqual(decision["progress"]["baselineResourceCount"], 5)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_initialized")
        self.assertFalse(decision["goalComplete"])
        self.assertTrue(state["baselineEstablished"])

    def test_same_inventory_snapshot_processed_twice_is_idempotent(self):
        state = brain.default_state("woodcutting", 5)
        response = context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)]))
        decision, state = evaluate(response, state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        decision, state = evaluate(response, state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertEqual(decision["progress"]["progressSource"], "inventory_snapshot_held_vs_baseline")
        self.assertTrue(decision["goalProgress"]["duplicateSnapshot"])

    def test_unchanged_snapshot_for_many_polls_stays_zero_after_reset(self):
        state = brain.default_state("woodcutting", 5)
        response = context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)]))
        for _index in range(10):
            decision, state = evaluate(response, state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertFalse(decision["goalComplete"])

    def test_invariant_corrects_stale_gain_when_held_equals_baseline_after_reset(self):
        state = brain.default_state("woodcutting", 5)
        inventory_items = [log_item(index, 1511) for index in range(5)]
        inventory = inventory_with_items(inventory_items)
        signature = inventory["inventorySignature"]
        state.update(
            {
                "goalComplete": True,
                "goalPreviouslyReached": True,
                "baselineEstablished": True,
                "baselineHeldCount": 5,
                "previousResourceCount": 5,
                "observedGained": 20,
                "observedRemoved": 20,
                "displayedGoalProgress": 20,
                "postBaselineGainObserved": True,
                "hasValidPostBaselineProgressHistory": False,
                "lastProcessedInventorySignature": signature,
                "previousInventoryItems": inventory_items,
                "resourceBaselineCounts": {"woodcutting_logs": 5},
                "resourceGainedCounts": {"woodcutting_logs": 20},
                "resourceLostCounts": {"woodcutting_logs": 20},
                "resourceProgress": {
                    "resourceGroup": "woodcutting_logs",
                    "baselineEstablished": True,
                    "baselineHeldCount": 5,
                    "currentHeldCount": 5,
                    "previousResourceCount": 5,
                    "observedGained": 20,
                    "observedRemoved": 20,
                    "displayedGoalProgress": 20,
                    "postBaselineGainObserved": True,
                    "hasValidPostBaselineProgressHistory": False,
                    "lastProcessedInventorySignature": signature,
                    "previousInventoryItems": inventory_items,
                },
            }
        )
        decision, state = evaluate(context_response(inventory=inventory), state)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_initialized")
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertFalse(decision["goalComplete"])
        self.assertNotEqual(decision["phase"], "goal_complete")
        self.assertIn(brain.OLD_PROGRESS_HISTORY_WARNING, decision["warnings"])
        self.assertTrue(decision["goalProgress"]["progressStateRepaired"])
        self.assertIsNone(state["observedGained"])
        self.assertIsNone(state["observedRemoved"])
        self.assertFalse(state["hasValidPostBaselineProgressHistory"])
        self.assertFalse(state["goalPreviouslyReached"])
        output = brain.format_human(decision)
        self.assertIn("invalid prior progress state was corrected", output)
        self.assertNotIn("lost/removed since start: 20", output)

    def test_valid_post_baseline_lower_count_retains_monotonic_progress(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(7)])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)
        self.assertEqual(decision["progress"]["progressHeldReason"], "valid_inventory_count_decreased_retained_monotonic_progress")
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertFalse(decision["progress"]["hasValidPostBaselineProgressHistory"])
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertFalse(decision["progress"]["progressStateRepaired"])

    def test_invalid_current_signature_prevents_baseline_and_progress_update(self):
        state = brain.default_state("woodcutting", 5)
        decision, state = evaluate(context_response(inventory=inventory_with_resource_count(5)), state)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_pending")
        self.assertFalse(decision["progress"]["baselineEstablished"])
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertFalse(decision["goalComplete"])
        self.assertIn("inventorySignature", decision["progress"]["snapshotValidityMissing"])

    def test_previous_snapshot_missing_prevents_positive_delta_and_repairs_partial_state(self):
        state = brain.default_state("woodcutting", 5)
        state.update(
            {
                "baselineEstablished": True,
                "baselineHeldCount": 0,
                "previousResourceCount": 5,
                "observedGained": 15,
                "observedRemoved": 10,
                "displayedGoalProgress": 15,
                "hasValidPostBaselineProgressHistory": True,
                "resourceBaselineCounts": {"woodcutting_logs": 0},
                "resourceGainedCounts": {"woodcutting_logs": 15},
                "resourceLostCounts": {"woodcutting_logs": 10},
                "resourceProgress": {
                    "resourceGroup": "woodcutting_logs",
                    "baselineEstablished": True,
                    "baselineHeldCount": 0,
                    "previousResourceCount": 5,
                    "observedGained": 15,
                    "observedRemoved": 10,
                    "displayedGoalProgress": 15,
                    "hasValidPostBaselineProgressHistory": True,
                },
                "previousInventoryItems": None,
            }
        )
        decision, state = evaluate(context_response(inventory=inventory_with_resource_count(5)), state)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_pending")
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertFalse(decision["goalComplete"])
        self.assertFalse(decision["progress"]["hasValidPostBaselineProgressHistory"])
        self.assertTrue(decision["progress"]["progressStateRepaired"])
        self.assertEqual(decision["progress"]["repairReason"], brain.OLD_PROGRESS_HISTORY_WARNING)

    def test_previous_snapshot_initialized_does_not_count_current_held_as_gained(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        state["previousInventoryItems"] = None
        state["resourceProgress"]["previousInventoryItems"] = None
        state["currentInventory"] = {}
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertEqual(decision["progress"]["progressSource"], "inventory_snapshot_held_vs_baseline")
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 5)
        self.assertTrue(decision["goalComplete"])
        self.assertFalse(decision["progress"]["hasValidPostBaselineProgressHistory"])

    def test_baseline_five_then_incremental_logs_count_only_new_logs(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(6)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        self.assertEqual(decision["progress"]["baselineResourceCount"], 5)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(7)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)
        self.assertFalse(decision["goalComplete"])

    def test_same_count_with_slots_moved_does_not_count_as_gain(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        moved = [log_item(slot, 1511) for slot in [5, 6, 7, 8, 9]]
        decision, _state = evaluate(context_response(inventory=inventory_with_items(moved)), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertEqual(decision["progress"]["progressSource"], "inventory_snapshot_held_vs_baseline")
        self.assertEqual(decision["progress"]["progressUpdateReason"], "daily gained-since-start is monotonic held-vs-baseline progress")

    def test_missing_inventory_items_produces_unknown_progress(self):
        decision, _state = evaluate(context_response(inventory={"known": True, "freeSlots": 20, "filledSlots": 8, "inventoryFull": False}))
        self.assertFalse(decision["progress"]["known"])
        self.assertEqual(decision["progress"]["progressSource"], "baseline_pending")
        self.assertEqual(decision["progress"]["missingReason"], "inventory item list missing")

    def test_missing_inventory_deltas_use_snapshot_wording(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511)])), state)
        self.assertEqual(decision["progress"]["progressSource"], "inventory_snapshot_held_vs_baseline")
        self.assertIn("monotonic held-vs-baseline", decision["progress"]["reason"])
        self.assertNotIn("positive inventory deltas", decision["progress"]["reason"])

    def test_inventory_deltas_available_use_delta_wording(self):
        state = brain.default_state("woodcutting", 5)
        response = context_response(inventory=inventory_with_items([]))
        response["recentInventoryDeltas"] = [
            {"fromTick": 41, "toTick": 42, "changes": [{"itemId": 1511, "beforeQuantity": 0, "afterQuantity": 1, "delta": 1}]}
        ]
        decision, _state = evaluate(response, state)
        self.assertEqual(decision["progress"]["progressSource"], "baseline_initialized")
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 0)
        self.assertIn("baseline initialized", decision["progress"]["reason"])

    def test_progress_increases_when_logs_appear_in_high_slot(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        self.assertEqual(decision["progress"]["currentHeldResourceCount"], 1)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        self.assertEqual(decision["progress"]["matchedSlots"], [18])

    def test_progress_accumulates_sequential_logs_in_slots_18_19_20(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        for expected, slots in enumerate(([18], [18, 19], [18, 19, 20]), start=1):
            decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(slot, 1511) for slot in slots])), state)
            self.assertEqual(decision["progress"]["gainedSinceBaseline"], expected)
            self.assertIsNone(decision["progress"]["cumulativeGained"])
        self.assertEqual(decision["progress"]["matchedSlots"], [18, 19, 20])

    def test_moving_log_between_slots_does_not_count_as_gained(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(5, 1511)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])
        self.assertEqual(decision["progress"]["matchedSlots"], [5])

    def test_no_goal_count_disables_progress_accumulation(self):
        state = brain.default_state("woodcutting", None)
        response = context_response(inventory=inventory_with_items([log_item(18, 1511), log_item(19, 1511)]))
        decision, state = brain.evaluate_brain(response, state, task="woodcutting", goal_count=None)
        self.assertTrue(decision["progress"]["progressDisabled"])
        self.assertIsNone(decision["progress"]["gainedSinceBaseline"])
        self.assertIsNone(decision["goalProgress"]["gainedSinceStart"])
        self.assertEqual(state["resourceGainedCounts"], {})
        output = brain.format_human(decision)
        self.assertIn("observing woodcutting context", output)
        self.assertIn("disabled, no goal count set", output)
        self.assertNotIn("/ unknown", output)

    def test_recent_depletion_is_signal_not_activity(self):
        state = brain.default_state("woodcutting", None)
        response = context_response(
            activity={"apparentState": "idle", "animation": -1, "interacting": None},
            events=[event("target_depleted", "Target depleted: Tree became stump", tick=41)],
            best=candidate(reachability="reachable", live_state="live_assumed"),
        )
        decision, _state = brain.evaluate_brain(response, state, task="woodcutting", goal_count=None)
        output = brain.format_human(decision)
        self.assertIn("Activity: idle", output)
        self.assertIn("Recent task signals:", output)
        self.assertIn("target depleted recently", output)
        self.assertNotIn("Activity: idle / target_depleted", output)

    def test_dropping_log_from_slot_18_keeps_monotonic_gained_since_start(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])

    def test_dropping_then_gaining_again_increments_cumulative_gained(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(19, 1511)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 1)
        self.assertIsNone(decision["progress"]["cumulativeLostOrRemoved"])

    def test_suspicious_snapshot_jump_is_ignored_without_deltas(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511, quantity=100)])), state)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 100)
        self.assertTrue(decision["goalComplete"])

    def test_state_file_persists_cumulative_progress(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain_state.json"
            brain.write_state(str(path), state)
            loaded = brain.load_state(str(path), "woodcutting", 5)
            decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511), log_item(19, 1511)])), loaded)
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 2)
        self.assertEqual(decision["progress"]["matchedSlots"], [18, 19])

    def test_human_output_includes_matched_slot_18(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        output = brain.format_human(decision)
        self.assertIn("matched slots: 18", output)

    def test_json_goal_progress_includes_resource_progress_fields(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(18, 1511)])), state)
        self.assertEqual(decision["goalProgress"]["resourceGroup"], "woodcutting_logs")
        self.assertEqual(decision["goalProgress"]["currentHeldCount"], 1)
        self.assertIsNone(decision["goalProgress"]["cumulativeGained"])
        self.assertEqual(decision["goalProgress"]["matchedSlots"], [18])

    def test_inventory_slot_diagnostics_detects_invalid_and_duplicate_slots(self):
        inventory = inventory_with_items([log_item(0, 1511), log_item(0, 1521), log_item(28, 1511)], inventorySlotCount=28, slotCount=28)
        diagnostics = brain.inventory_slot_diagnostics(inventory)
        self.assertIn(0, diagnostics["duplicateSlots"])
        self.assertEqual(diagnostics["invalidSlots"][0]["slot"], 28)
        self.assertFalse(diagnostics["consistent"])

    def test_goal_complete_phase_at_goal_count(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertEqual(decision["phase"], "goal_complete")
        self.assertEqual(decision["genericTaskState"]["phase"], "goal_complete")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "none")
        self.assertIsNone(decision["genericTaskState"]["selectedTargetKey"])
        self.assertEqual(decision["substate"], "goal_count_reached")
        self.assertEqual(decision["confidence"], 0.95)
        self.assertTrue(decision["goalComplete"])
        self.assertTrue(decision["goalProgress"]["complete"])
        self.assertEqual(decision["goalProgress"]["gainedSinceStart"], 5)
        self.assertEqual(decision["internalNextState"], "hold_goal_complete_state")
        self.assertEqual(decision["blockingConditions"], [])
        self.assertTrue(state["goalComplete"])

    def test_goal_complete_phase_above_goal_count(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(6)])), state)
        self.assertEqual(decision["phase"], "goal_complete")
        self.assertEqual(decision["goalProgress"]["gainedSinceStart"], 6)

    def test_goal_complete_overrides_target_available(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)]), best=candidate()), state)
        self.assertEqual(decision["phase"], "goal_complete")
        self.assertNotEqual(decision["internalNextState"], "hold_target_available_state")

    def test_stale_context_overrides_goal_complete(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        response = context_response(
            inventory=inventory_with_items([log_item(index, 1511) for index in range(5)]),
            freshness={"freshByTicks": False, "freshByMillis": True},
        )
        decision, _state = evaluate(response, state)
        self.assertEqual(decision["phase"], "stale_context")
        self.assertTrue(decision["goalComplete"])

    def test_goal_complete_human_output(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        decision, _state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        output = brain.format_human(decision)
        self.assertIn("goal_complete", output)
        self.assertIn("goal reached: yes", output)
        self.assertIn("hold_goal_complete_state", output)
        self.assertIn("No action emitted.", output)

    def test_goal_previously_reached_survives_current_held_drop_until_reset(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(0, 1511)])), state)
        self.assertEqual(decision["phase"], "goal_complete")
        self.assertTrue(decision["goalComplete"])
        self.assertTrue(state["goalComplete"])
        self.assertEqual(decision["progress"]["gainedSinceBaseline"], 5)

    def test_reset_state_clears_goal_complete(self):
        state = brain.default_state("woodcutting", 5)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([])), state)
        _decision, state = evaluate(context_response(inventory=inventory_with_items([log_item(index, 1511) for index in range(5)])), state)
        self.assertTrue(state["goalComplete"])
        reset_state = brain.default_state("woodcutting", 5)
        self.assertFalse(reset_state["goalComplete"])
        self.assertFalse(reset_state["goalPreviouslyReached"])

    def test_inventory_changed_phase(self):
        response = context_response(inventory={"known": True, "freeSlots": 11, "filledSlots": 17, "inventoryFull": False, "changedRecently": True})
        decision, _state = evaluate(response)
        self.assertEqual(decision["phase"], "inventory_changed")
        self.assertEqual(decision["substate"], "recent_inventory_change")

    def test_watch_suggestions_generated_for_missing_watchable_capabilities(self):
        suggestion = {"alias": "inventory_delta", "type": "builtin", "id": "inventory.deltas", "sampleMode": "on_change", "ttlTicks": 500}
        response = context_response(missing=["watch:inventory_delta"], suggestions=[suggestion])
        decision, _state = evaluate(response)
        self.assertIn("watch:inventory_delta", decision["missingCapabilities"])
        self.assertEqual(decision["suggestedWatchRequests"], [suggestion])
        self.assertTrue(any(item.get("status") == "watch_recommended" for item in decision["observationNeeds"]))

    def test_request_missing_watches_sends_watch_request_when_enabled(self):
        suggestion = {"alias": "inventory_delta", "type": "builtin", "id": "inventory.deltas", "sampleMode": "on_change", "ttlTicks": 500}
        response = context_response(missing=["watch:inventory_delta"], suggestions=[suggestion])
        args = SimpleNamespace(
            task="woodcutting",
            max_candidates=3,
            max_events=5,
            host="127.0.0.1",
            port=8890,
            timeout=1.0,
            goal_count=5,
            request_missing_watches=True,
        )
        with mock.patch.object(brain, "fetch_optional_endpoint", return_value=({}, None)), mock.patch.object(
            brain, "fetch_context", return_value=response
        ), mock.patch.object(
            brain,
            "post_optional_watch_request",
            return_value=({"schema": "context_watch_response.v1", "accepted": [suggestion], "activeWatches": [suggestion], "requestWritten": True}, None),
        ) as post:
            decision, state = brain.brain_once(args, brain.default_state("woodcutting", 5))
        post.assert_called_once()
        self.assertEqual(decision["watchRequest"]["acceptedCount"], 1)
        self.assertEqual(state["activeWatchRequests"], [suggestion])

    def test_json_output_has_no_action_input_fields(self):
        decision, _state = evaluate(context_response())
        brain.validate_no_action_fields(decision)
        forbidden = brain.SAFETY_FORBIDDEN_KEYS - brain.SAFETY_ALLOWED_KEYS
        self.assertFalse({str(key).lower() for key in walk_keys(decision)} & forbidden)
        self.assertTrue(decision["noActionEmitted"])
        self.assertIn('"noActionEmitted": true', json.dumps(decision))

    def test_human_output_includes_no_action_emitted(self):
        decision, _state = evaluate(context_response())
        output = brain.format_human(decision)
        self.assertIn("WOODCUTTING BRAIN CORE", output)
        self.assertIn("No action emitted.", output)
        self.assertIn("Internal next state:", output)

    def test_state_file_read_write_and_reset(self):
        state = brain.default_state("woodcutting", 5)
        state["latestTick"] = 99
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain_state.json"
            brain.write_state(str(path), state)
            loaded = brain.load_state(str(path), "woodcutting", 5)
            self.assertEqual(loaded["latestTick"], 99)
            reset = brain.load_state(str(path), "woodcutting", 5, reset=True)
            self.assertIsNone(reset["latestTick"])

    def test_old_state_file_without_history_validity_drops_poisoned_progress(self):
        state = brain.default_state("woodcutting", 5)
        state["baselineEstablished"] = True
        state["baselineHeldCount"] = 5
        state["observedGained"] = 20
        state["observedRemoved"] = 20
        state["displayedGoalProgress"] = 20
        state["goalComplete"] = True
        state["resourceGainedCounts"] = {"woodcutting_logs": 20}
        state["resourceLostCounts"] = {"woodcutting_logs": 20}
        state["resourceProgress"] = {
            "resourceGroup": "woodcutting_logs",
            "baselineEstablished": True,
            "baselineHeldCount": 5,
            "observedGained": 20,
            "observedRemoved": 20,
            "displayedGoalProgress": 20,
        }
        state.pop("hasValidPostBaselineProgressHistory", None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain_state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            loaded = brain.load_state(str(path), "woodcutting", 5)

        self.assertIsNone(loaded["observedGained"])
        self.assertIsNone(loaded["observedRemoved"])
        self.assertFalse(loaded["goalComplete"])
        self.assertFalse(loaded["hasValidPostBaselineProgressHistory"])
        self.assertIn(brain.OLD_PROGRESS_HISTORY_WARNING, loaded["progressHistoryRepairWarnings"])

    def test_state_file_task_or_goal_mismatch_resets(self):
        state = brain.default_state("woodcutting", 5)
        state["latestTick"] = 99
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brain_state.json"
            brain.write_state(str(path), state)
            loaded = brain.load_state(str(path), "woodcutting", None)
        self.assertIsNone(loaded["latestTick"])
        self.assertIsNone(loaded["goal"]["goalCount"])

    def test_percent_userprofile_state_path_warns(self):
        warning = brain.state_file_path_warning("%USERPROFILE%\\.osrs-telemetry\\brain_state_woodcutting.json")
        self.assertIsNotNone(warning)
        self.assertIn("USERPROFILE", warning)


if __name__ == "__main__":
    unittest.main()
