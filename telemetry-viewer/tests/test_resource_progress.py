import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import resource_progress as progress


WOODCUTTING = progress.ResourceDefinition(
    "woodcutting_logs",
    (1511, 1521, 1519, 1517, 1515, 1513),
    "logs",
)


def snapshot(tick, signature, items=None, resource_counts=None):
    return progress.InventorySnapshot(
        session_path="session-a",
        latest_tick=tick,
        inventory_signature=signature,
        inventory_slot_count=28,
        items=tuple(items) if items is not None else None,
        resource_counts=resource_counts or {},
    )


def log(slot, item_id=1511, quantity=1):
    return {"slot": slot, "itemId": item_id, "quantity": quantity}


class ResourceProgressTest(unittest.TestCase):
    def test_item_id_none_never_counts(self):
        count = progress.count_resource_items(
            snapshot(1, "sig-a", [{"slot": 9, "itemId": None, "quantity": None, "counted": True}, log(10)]),
            WOODCUTTING,
        )

        self.assertEqual(count["count"], 1)
        self.assertEqual(count["matchedSlots"], [10])
        self.assertTrue(all(row["itemId"] is not None for row in count["matchedSlotDetails"]))
        self.assertIn(progress.INVALID_MATCHED_SLOT_WARNING, count["warnings"])

    def test_valid_logs_in_all_relevant_slots_count(self):
        count = progress.count_resource_items(snapshot(1, "sig-a", [log(0), log(9, 1521), log(18, 1519), log(27, 1513)]), WOODCUTTING)

        self.assertEqual(count["count"], 4)
        self.assertEqual(count["matchedSlots"], [0, 9, 18, 27])

    def test_quantity_none_counts_as_one_only_for_valid_item_id(self):
        count = progress.count_resource_items(snapshot(1, "sig-a", [log(3, 1511, None), {"slot": 4, "itemId": None, "quantity": None}]), WOODCUTTING)

        self.assertEqual(count["count"], 1)
        self.assertEqual(count["matchedSlotDetails"][0]["quantity"], 1)

    def test_quantity_zero_or_negative_does_not_count(self):
        count = progress.count_resource_items(snapshot(1, "sig-a", [log(3, 1511, 0), log(4, 1521, -1), log(5)]), WOODCUTTING)

        self.assertEqual(count["count"], 1)
        self.assertEqual(count["matchedSlots"], [5])

    def test_shuffled_sparse_items_count_by_slot_field(self):
        count = progress.count_resource_items(snapshot(1, "sig-a", [log(27), log(0), log(18)]), WOODCUTTING)

        self.assertEqual(count["count"], 3)
        self.assertEqual(count["matchedSlots"], [0, 18, 27])

    def test_resource_counts_fallback_does_not_fabricate_slots(self):
        count = progress.count_resource_items(
            snapshot(
                1,
                "sig-a",
                None,
                {"woodcutting_logs": {"count": 2, "matchedItemIds": [1511], "matchedSlots": [9, 18]}},
            ),
            WOODCUTTING,
        )

        self.assertEqual(count["count"], 2)
        self.assertTrue(count["summaryDerived"])
        self.assertEqual(count["matchedSlots"], [])
        self.assertEqual(count["matchedSlotDetails"], [])

    def test_item_list_wins_over_resource_counts(self):
        count = progress.count_resource_items(
            snapshot(1, "sig-a", [log(3)], {"woodcutting_logs": {"count": 99, "matchedItemIds": [1511], "matchedSlots": list(range(28))}}),
            WOODCUTTING,
        )

        self.assertEqual(count["count"], 1)
        self.assertFalse(count["summaryDerived"])
        self.assertIn("resourceCounts disagreed", count["warnings"][0])

    def test_first_valid_snapshot_establishes_baseline_with_zero_progress(self):
        state = progress.ResourceProgressState()

        result = progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)

        self.assertEqual(result.source, "baseline_initialized")
        self.assertEqual(result.baseline_held_count, 5)
        self.assertEqual(result.displayed_goal_progress, 0)
        self.assertFalse(result.goal_complete)

    def test_same_snapshot_repeated_stays_zero(self):
        state = progress.ResourceProgressState()
        snap = snapshot(1, "sig-a", [log(i) for i in range(5)])
        progress.initialize_or_update_progress(state, snap, WOODCUTTING, 5)

        for _ in range(10):
            result = progress.initialize_or_update_progress(state, snap, WOODCUTTING, 5)

        self.assertTrue(result.duplicate_snapshot)
        self.assertEqual(result.displayed_goal_progress, 0)
        self.assertFalse(result.goal_complete)

    def test_current_six_after_baseline_five_is_progress_one(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(6)]), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 1)
        self.assertFalse(result.goal_complete)

    def test_current_returns_to_baseline_keeps_monotonic_gained_since_start(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(6)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(3, "sig-c", [log(i) for i in range(5)]), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 1)
        self.assertEqual(result.current_held_count, 5)
        self.assertFalse(result.goal_complete)
        self.assertEqual(result.progress_held_reason, "valid_inventory_count_decreased_retained_monotonic_progress")

    def test_current_ten_after_baseline_five_completes_goal(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(10)]), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 5)
        self.assertTrue(result.goal_complete)

    def test_old_cumulative_state_is_ignored(self):
        state = progress.state_from_dict(
            {
                "schema": progress.SCHEMA,
                "baselineEstablished": True,
                "baselineHeldCount": 5,
                "observedGained": 25,
                "observedRemoved": 25,
                "displayedGoalProgress": 25,
                "goalComplete": True,
            }
        )

        result = progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)

        self.assertEqual(result.baseline_held_count, 5)
        self.assertEqual(result.displayed_goal_progress, 0)
        self.assertFalse(result.goal_complete)
        self.assertIn(progress.OLD_CUMULATIVE_HISTORY_WARNING, result.warnings)

    def test_invalid_snapshot_does_not_establish_baseline(self):
        state = progress.ResourceProgressState()

        result = progress.initialize_or_update_progress(state, snapshot(1, None, [log(3)]), WOODCUTTING, 5)

        self.assertFalse(result.current_snapshot_valid)
        self.assertFalse(result.state.baseline_established)
        self.assertEqual(result.source, "baseline_pending")

    def test_valid_progress_retained_when_next_snapshot_missing(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(9)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(3, None, None), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 4)
        self.assertEqual(result.current_held_count, 9)
        self.assertEqual(result.source, "retained_previous_progress")
        self.assertTrue(result.progress_retained_from_previous)
        self.assertEqual(result.progress_held_reason, "invalid_snapshot_retained_previous")
        self.assertEqual(result.progress_flicker_prevented_count, 1)

    def test_valid_progress_retained_when_current_signature_missing(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(9)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(3, None, [log(i) for i in range(5)]), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 4)
        self.assertEqual(result.current_held_count, 9)
        self.assertIn("inventorySignature", result.snapshot_validity_missing)
        self.assertTrue(result.progress_retained_from_previous)

    def test_valid_current_held_equal_baseline_does_not_drop_visible_progress(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(9)]), WOODCUTTING, 5)

        result = progress.initialize_or_update_progress(state, snapshot(3, "sig-c", [log(i) for i in range(5)]), WOODCUTTING, 5)

        self.assertEqual(result.displayed_goal_progress, 4)
        self.assertEqual(result.current_held_count, 5)
        self.assertFalse(result.progress_retained_from_previous)
        self.assertIsNone(result.progress_drop_reason)
        self.assertEqual(result.progress_held_reason, "valid_inventory_count_decreased_retained_monotonic_progress")

    def test_normal_skipped_normal_sequence_never_drops_visible_progress(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        first = progress.initialize_or_update_progress(state, snapshot(2, "sig-b", [log(i) for i in range(9)]), WOODCUTTING, 5)
        skipped = progress.initialize_or_update_progress(state, snapshot(3, None, None), WOODCUTTING, 5)
        next_good = progress.initialize_or_update_progress(state, snapshot(4, "sig-c", [log(i) for i in range(9)]), WOODCUTTING, 5)

        self.assertEqual(first.displayed_goal_progress, 4)
        self.assertEqual(skipped.displayed_goal_progress, 4)
        self.assertEqual(next_good.displayed_goal_progress, 4)
        self.assertTrue(skipped.progress_retained_from_previous)

    def test_gained_since_start_monotonic_across_stale_or_lower_counts(self):
        state = progress.ResourceProgressState()
        progress.initialize_or_update_progress(state, snapshot(1, "sig-a", [log(i) for i in range(5)]), WOODCUTTING, 5)
        values = []
        for snap in (
            snapshot(2, "sig-b", [log(i) for i in range(9)]),
            snapshot(3, None, None),
            snapshot(4, "sig-c", [log(i) for i in range(5)]),
            snapshot(5, "sig-d", [log(i) for i in range(10)]),
        ):
            values.append(progress.initialize_or_update_progress(state, snap, WOODCUTTING, 5).displayed_goal_progress)

        self.assertEqual(values, sorted(values))
        self.assertEqual(values, [4, 4, 4, 5])


if __name__ == "__main__":
    unittest.main()
