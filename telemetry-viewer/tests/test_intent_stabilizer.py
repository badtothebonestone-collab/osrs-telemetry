import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import intent_stabilizer as stabilizer


def target(key, *, score=100, distance=1, tick=1, reachability="reachable", liveness="live_assumed", target_type="sceneObject", class_id="tree", aim=True):
    value = {
        "objectKey": key,
        "targetName": key,
        "targetType": target_type,
        "classId": class_id,
        "id": 1000,
        "hash": hash(key) & 0xFFFF,
        "worldX": 3200 + len(key),
        "worldY": 3200,
        "plane": 0,
        "qualityScore": score,
        "distanceTiles": distance,
        "navigation": {"directReachability": reachability},
        "targetLiveState": liveness,
        "lastSeenTick": tick,
        "present": True,
    }
    if aim:
        value["aimPoint"] = {"canvasX": 100, "canvasY": 110}
    return value


def context(candidates, *, task="woodcutting", intent="target_available", tick=1, **extra):
    payload = {
        "activeTask": task,
        "activeIntent": intent,
        "latestTick": tick,
        "rawBestTarget": candidates[0] if candidates else None,
        "profile": task,
    }
    payload.update(extra)
    return payload


class IntentStabilizerTest(unittest.TestCase):
    def test_keeps_same_target_when_raw_best_alternates_for_one_tick(self):
        state = stabilizer.IntentState()
        first = stabilizer.choose_stable_intent(state, [target("oak-a", score=100), target("oak-b", score=101)], context([target("oak-a", score=100), target("oak-b", score=101)]))
        second = stabilizer.choose_stable_intent(state, [target("oak-b", score=101), target("oak-a", score=100)], context([target("oak-b", score=101), target("oak-a", score=100)], tick=2))

        self.assertEqual(first.selectedTargetKey, "oak-a")
        self.assertEqual(second.selectedTargetKey, "oak-a")
        self.assertTrue(second.candidateWasRetained)
        self.assertEqual(second.switchReason, "retained_current_target")

    def test_compact_brain_key_matches_full_candidate_object_key(self):
        state = stabilizer.IntentState()
        compact_a = dict(target("oak-a", score=100))
        compact_a.pop("objectKey")
        compact_a["key"] = "objectKey:oak-a"
        compact_b = dict(target("oak-b", score=101))
        compact_b.pop("objectKey")
        compact_b["key"] = "objectKey:oak-b"

        first = stabilizer.choose_stable_intent(state, [target("oak-a", score=100), target("oak-b", score=101)], context([compact_a], tick=1, rawBestTarget=compact_a))
        second = stabilizer.choose_stable_intent(
            state,
            [target("oak-b", score=101), target("oak-a", score=100)],
            context([compact_b], tick=2, rawBestTarget=compact_b),
        )

        self.assertEqual(first.selectedTargetKey, "oak-a")
        self.assertEqual(second.selectedTargetKey, "oak-a")
        self.assertEqual(second.rawBestTargetKey, "oak-b")
        self.assertEqual(second.switchReason, "retained_current_target")

    def test_compact_raw_best_is_enriched_with_full_candidate_identity(self):
        state = stabilizer.IntentState()
        full = target("oak-a", score=100)
        full["sceneX"] = 12
        full["sceneY"] = 18
        full["localX"] = 6200
        full["localY"] = 6220
        full["clickableHull"] = [[1, 1], [2, 1], [2, 2]]
        compact = {
            "key": "objectKey:oak-a",
            "targetName": "Oak tree",
            "targetType": "sceneObject",
            "classId": "tree",
            "qualityScore": 100,
            "distanceTiles": 1,
        }

        result = stabilizer.choose_stable_intent(state, [full], context([compact], rawBestTarget=compact))

        self.assertEqual(result.selectedTargetKey, "oak-a")
        self.assertEqual(result.selectedTarget.raw["objectKey"], "oak-a")
        self.assertEqual(result.selectedTarget.raw["sceneX"], 12)
        self.assertEqual(result.selectedTarget.raw["localX"], 6200)
        self.assertEqual(result.selectedTarget.raw["clickableHull"], [[1, 1], [2, 1], [2, 2]])

    def test_retains_previous_target_when_still_present_beyond_top_candidate_cap(self):
        stabilizer_instance = stabilizer.IntentStabilizer(max_candidates_considered=2)
        stabilizer_instance.choose([target("oak-a"), target("oak-b"), target("oak-c")], context([target("oak-a")], tick=1))
        result = stabilizer_instance.choose(
            [target("oak-b", score=101), target("oak-c", score=100), target("oak-a", score=99)],
            context([target("oak-b", score=101)], tick=2),
        )

        self.assertEqual(result.selectedTargetKey, "oak-a")
        self.assertEqual(result.switchReason, "retained_current_target")

    def test_switches_after_better_candidate_persists(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a", score=100), target("oak-b", score=101)], context([target("oak-a", score=100), target("oak-b", score=101)], tick=1))
        stabilizer.choose_stable_intent(state, [target("oak-b", score=101), target("oak-a", score=100)], context([target("oak-b", score=101), target("oak-a", score=100)], tick=2))
        result = stabilizer.choose_stable_intent(state, [target("oak-b", score=101), target("oak-a", score=100)], context([target("oak-b", score=101), target("oak-a", score=100)], tick=3))

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertTrue(result.candidateWasSwitched)
        self.assertTrue(result.softSwitch)
        self.assertEqual(result.switchReason, "better_candidate_persisted")

    def test_switches_on_score_margin_without_waiting_for_persistence(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a", score=100), target("oak-b", score=111)], context([target("oak-a", score=100), target("oak-b", score=111)], tick=1))
        result = stabilizer.choose_stable_intent(state, [target("oak-b", score=111), target("oak-a", score=100)], context([target("oak-b", score=111), target("oak-a", score=100)], tick=2))

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertTrue(result.softSwitch)
        self.assertEqual(result.switchReason, "better_candidate_score_margin")

    def test_retains_current_target_when_missing_for_one_tick_without_hard_invalid(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        result = stabilizer.choose_stable_intent(state, [target("oak-b")], context([target("oak-b")], tick=2))

        self.assertEqual(result.selectedTargetKey, "oak-a")
        self.assertTrue(result.candidateWasRetained)
        self.assertTrue(result.retainedDueToGrace)
        self.assertEqual(result.currentMissingTicks, 1)
        self.assertEqual(result.switchReason, "retained_current_target_transient_missing")

    def test_retains_current_target_for_two_missing_ticks_then_switches_beyond_grace(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        second = stabilizer.choose_stable_intent(state, [target("oak-b")], context([target("oak-b")], tick=2))
        third = stabilizer.choose_stable_intent(state, [target("oak-b")], context([target("oak-b")], tick=3))
        fourth = stabilizer.choose_stable_intent(state, [target("oak-b")], context([target("oak-b")], tick=4))

        self.assertEqual(second.selectedTargetKey, "oak-a")
        self.assertEqual(third.selectedTargetKey, "oak-a")
        self.assertEqual(third.currentMissingTicks, 2)
        self.assertEqual(fourth.selectedTargetKey, "oak-b")
        self.assertTrue(fourth.hardSwitch)
        self.assertEqual(fourth.switchReason, "current_target_missing")

    def test_switch_audit_records_transient_missing_grace(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        result = stabilizer.choose_stable_intent(state, [target("oak-b")], context([target("oak-b")], tick=2))

        self.assertTrue(result.switchAuditTail)
        last = result.switchAuditTail[-1]
        self.assertEqual(last["selectedTargetKey"], "oak-a")
        self.assertEqual(last["rawBestTargetKey"], "oak-b")
        self.assertTrue(last["retainedDueToGrace"])
        self.assertTrue(last["currentTargetMissingThisTick"])

    def test_switches_immediately_when_current_target_depleted(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        result = stabilizer.choose_stable_intent(
            state,
            [target("oak-b"), target("oak-a", liveness="target_depleted")],
            context([target("oak-b"), target("oak-a", liveness="target_depleted")], tick=2),
        )

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertEqual(result.switchReason, "current_target_depleted")

    def test_switches_immediately_when_current_target_is_stale(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a", tick=1)], context([target("oak-a", tick=1)], tick=1))
        result = stabilizer.choose_stable_intent(
            state,
            [target("oak-b", tick=4), target("oak-a", liveness="stale", tick=1)],
            context([target("oak-b", tick=4), target("oak-a", liveness="stale", tick=1)], tick=4),
        )

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertEqual(result.switchReason, "current_target_stale")

    def test_switches_immediately_when_current_target_unreachable(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        result = stabilizer.choose_stable_intent(
            state,
            [target("oak-b"), target("oak-a", reachability="blocked")],
            context([target("oak-b"), target("oak-a", reachability="blocked")], tick=2),
        )

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertEqual(result.switchReason, "current_target_unreachable")

    def test_same_object_with_changed_aimpoint_and_geometry_is_same_identity(self):
        state = stabilizer.IntentState()
        first = target("oak-a", score=100, distance=3)
        first.pop("objectKey")
        first["hash"] = 12345
        first["worldX"] = 3210
        first["worldY"] = 3220
        first["aimPoint"] = {"canvasX": 100, "canvasY": 110}
        refreshed = target("oak-a-refresh", score=100, distance=3)
        refreshed.pop("objectKey")
        refreshed["hash"] = 12345
        refreshed["worldX"] = 3210
        refreshed["worldY"] = 3220
        refreshed["aimPoint"] = {"canvasX": 160, "canvasY": 170}
        refreshed["clickableHull"] = [[1, 1], [2, 1], [2, 2]]

        stabilizer.choose_stable_intent(state, [first], context([first], tick=1))
        result = stabilizer.choose_stable_intent(state, [refreshed], context([refreshed], tick=2))

        self.assertEqual(result.switchReason, "retained_current_target")
        self.assertEqual(result.selectedTarget.raw["aimPoint"], {"canvasX": 160, "canvasY": 170})
        self.assertEqual(result.selectedTarget.raw["clickableHull"], [[1, 1], [2, 1], [2, 2]])

    def test_switches_immediately_when_task_changes(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], task="woodcutting", tick=1))
        result = stabilizer.choose_stable_intent(state, [target("banker", class_id="banker", target_type="npc")], context([target("banker", class_id="banker", target_type="npc")], task="banking", tick=2))

        self.assertEqual(result.selectedTargetKey, "banker")
        self.assertEqual(result.switchReason, "task_changed")

    def test_switches_immediately_on_force_switch(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        result = stabilizer.choose_stable_intent(state, [target("oak-b"), target("oak-a")], context([target("oak-b"), target("oak-a")], tick=2, forceSwitch=True))

        self.assertEqual(result.selectedTargetKey, "oak-b")
        self.assertEqual(result.switchReason, "force_switch")

    def test_higher_priority_interrupt_overrides_selected_target(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], tick=1))
        threat = target("escape-tile", target_type="tile", class_id="waypoint", score=10)
        result = stabilizer.choose_stable_intent(
            state,
            [target("oak-a"), threat],
            context([target("oak-a"), threat], tick=2, interrupt=True, interruptReason="threat", interruptPriority=100, interruptTarget=threat),
        )

        self.assertEqual(result.selectedTargetKey, "escape-tile")
        self.assertEqual(result.switchReason, "interrupt")
        self.assertEqual(result.interruptReason, "threat")

    def test_task_transition_intent_overrides_selected_target(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], intent="target_available", tick=1))
        banker = target("banker", target_type="npc", class_id="banker", score=10)
        result = stabilizer.choose_stable_intent(
            state,
            [banker, target("oak-a")],
            context([banker, target("oak-a")], intent="banking_needed", tick=2, intentPriority=70),
        )

        self.assertEqual(result.selectedTargetKey, "banker")
        self.assertEqual(result.switchReason, "intent_changed")

    def test_phase_transition_to_inventory_full_clears_selected_target(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], intent="target_selected", tick=1))

        result = stabilizer.choose_stable_intent(
            state,
            [],
            context([], intent="needs_service", tick=2, intentPriority=70),
        )

        self.assertIsNone(result.selectedTargetKey)
        self.assertIsNone(result.selectedTarget)
        self.assertEqual(result.switchReason, "task_phase_changed")
        self.assertTrue(result.hardSwitch)

    def test_phase_transition_to_process_inventory_clears_selected_target(self):
        state = stabilizer.IntentState()
        stabilizer.choose_stable_intent(state, [target("oak-a")], context([target("oak-a")], intent="target_selected", tick=1))

        result = stabilizer.choose_stable_intent(
            state,
            [],
            context([], intent="process_inventory", tick=2, intentPriority=70),
        )

        self.assertIsNone(result.selectedTargetKey)
        self.assertIsNone(result.selectedTarget)
        self.assertEqual(result.switchReason, "task_phase_changed")
        self.assertTrue(result.hardSwitch)

    def test_generic_target_types_are_supported(self):
        for target_type, class_id in (
            ("sceneObject", "tree"),
            ("npc", "banker"),
            ("tile", "waypoint"),
            ("ui", "bank_tab"),
            ("inventorySlot", "inventory_item"),
            ("groundItem", "ground_item"),
        ):
            state = stabilizer.IntentState()
            result = stabilizer.choose_stable_intent(
                state,
                [target(f"{target_type}-1", target_type=target_type, class_id=class_id)],
                context([target(f"{target_type}-1", target_type=target_type, class_id=class_id)]),
            )
            self.assertEqual(result.selectedTarget.targetType, target_type)
            self.assertEqual(result.selectedTarget.classId, class_id)

    def test_stabilizer_exposes_no_action_shaped_fields(self):
        result = stabilizer.choose_stable_intent(stabilizer.IntentState(), [target("oak-a")], context([target("oak-a")]))
        payload = result.to_status_fields()
        text = " ".join(payload.keys()).lower()
        for forbidden in ("click", "mouse", "keyboard", "menu", "invoke", "execute", "input"):
            self.assertNotIn(forbidden, text)

    def test_stabilizer_module_does_not_import_input_readers_or_write_files(self):
        source = Path(stabilizer.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "live_target_processor",
            "PluginSnapshot",
            "compact_packet",
            "context_service",
            "open(",
            "Path(",
            "atomic_write",
            "jsonl",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
