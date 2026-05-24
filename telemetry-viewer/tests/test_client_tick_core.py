import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

from client_tick_core import (  # noqa: E402
    ActionIntent,
    action_intent_from_proposal,
    classify_clicked_menu,
    classify_menu_action,
    get_actionable_entries,
    get_left_click_entry,
    hover_sample_matches_intent,
    is_cancel_entry,
    is_walk_here_entry,
    latest_hover_menu_sample,
    latest_menu_option_clicked_sample,
    menu_tail_volatility,
    post_menu_sort_tail_samples,
)
from input_control.action_proposal import ActionProposal  # noqa: E402


class ClientTickCoreTest(unittest.TestCase):
    def test_parses_hot_state_and_legacy_hover_fields(self):
        response = {
            "clientTickHot": {
                "schema": "client_tick_hot.v1",
                "postMenuSort": {"topOption": "Chop down", "topTarget": "Tree"},
                "lastMenuOptionClicked": {"option": "Walk here", "target": ""},
            }
        }

        self.assertEqual(latest_hover_menu_sample(response)["topOption"], "Chop down")
        self.assertEqual(latest_menu_option_clicked_sample(response)["option"], "Walk here")

        legacy = {"hoverMenu": {"topOption": "Chop"}, "lastMenuOptionClicked": {"option": "Chop"}}
        self.assertEqual(latest_hover_menu_sample(legacy)["topOption"], "Chop")
        self.assertEqual(latest_menu_option_clicked_sample(legacy)["option"], "Chop")

    def test_classifies_walk_and_object_action(self):
        self.assertEqual(classify_menu_action({"option": "Walk here", "type": "WALK"}), "walk_here")
        self.assertEqual(classify_menu_action({"option": "Cancel", "type": "CANCEL"}), "cancel_hover")
        self.assertEqual(
            classify_menu_action({"option": "Chop down", "target": "Tree", "type": "GAME_OBJECT_FIRST_OPTION"}),
            "object_action",
        )
        self.assertEqual(
            classify_menu_action({"option": "Talk-to", "target": "Banker", "type": "NPC_FIRST_OPTION"}),
            "npc_action",
        )

    def test_hover_sample_accepts_fresh_matching_tree(self):
        intent = ActionIntent.for_target(
            activity="woodcutting",
            target_name="Tree",
            object_id=1276,
            expected_options=["Chop down", "Chop"],
            expected_targets=["Tree", "Oak tree"],
            reject_options=["Walk here"],
        )
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "<col=00ff00>Chop down",
            "topTarget": "<col=ffff>Tree",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 1276,
        }

        result = hover_sample_matches_intent(
            sample,
            intent,
            {"x": 200, "y": 146},
            tolerance_px=3,
            min_wall_time_millis=2000,
        )

        self.assertTrue(result.confirmed)
        self.assertEqual(result.reason, "hover_menu_confirmed")

    def test_hover_sample_rejects_stale_position_and_walk_here(self):
        intent = ActionIntent.for_target(
            activity="woodcutting",
            target_name="Tree",
            object_id=1276,
            expected_options=["Chop down", "Chop"],
            reject_options=["Walk here"],
        )

        stale = {
            "wallTimeMillis": 1000,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "Chop down",
            "topTarget": "Tree",
            "topIdentifier": 1276,
        }
        self.assertEqual(
            hover_sample_matches_intent(stale, intent, {"x": 200, "y": 146}, min_wall_time_millis=2000).reason,
            "hover_menu_stale",
        )

        far = dict(stale, wallTimeMillis=2200, mouseCanvasX=210)
        self.assertEqual(
            hover_sample_matches_intent(far, intent, {"x": 200, "y": 146}, tolerance_px=3).reason,
            "mouse_position_outside_tolerance",
        )

        walk = dict(stale, wallTimeMillis=2200, topOption="Walk here", topTarget="")
        self.assertEqual(
            hover_sample_matches_intent(walk, intent, {"x": 200, "y": 146}, tolerance_px=3).reason,
            "top_option_rejected",
        )

    def test_clicked_menu_compares_before_after_and_expected_action(self):
        intent = ActionIntent.for_target(
            activity="woodcutting",
            target_name="Oak tree",
            object_id=10820,
            expected_options=["Chop down", "Chop"],
            expected_targets=["Tree", "Oak tree"],
            reject_options=["Walk here"],
        )
        before = {"clientTick": 10, "wallTimeMillis": 1000, "option": "Walk here", "identifier": 0}

        self.assertEqual(classify_clicked_menu(before, dict(before), intent), "unknown_click_result")
        self.assertEqual(
            classify_clicked_menu(before, {"clientTick": 11, "wallTimeMillis": 1100, "option": "Walk here"}, intent),
            "clicked_walk_here",
        )
        self.assertEqual(
            classify_clicked_menu(
                before,
                {"clientTick": 11, "wallTimeMillis": 1100, "option": "Chop down", "target": "Oak tree", "identifier": 10820},
                intent,
            ),
            "clicked_expected_action",
        )

    def test_menu_entry_helpers_select_stable_left_click_entry(self):
        sample = {
            "topOption": "Chop down",
            "topTarget": "<col=ffff>Tree",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 1276,
            "entries": [
                {"option": "Chop down", "target": "<col=ffff>Tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1276},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
                {"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0},
            ],
        }

        left_click = get_left_click_entry(sample)
        actionable = get_actionable_entries(sample)

        self.assertEqual(left_click["option"], "Chop down")
        self.assertEqual(left_click["selectionReason"], "raw_top_entry")
        self.assertTrue(is_cancel_entry({"option": "Cancel", "type": "CANCEL"}))
        self.assertTrue(is_walk_here_entry({"option": "Walk here", "type": "WALK"}))
        self.assertEqual([entry["option"] for entry in actionable], ["Chop down", "Walk here"])

    def test_cancel_sentinel_does_not_hide_valid_actionable_entry(self):
        sample = {
            "menuOpen": False,
            "topOption": "Cancel",
            "topTarget": "",
            "topType": "CANCEL",
            "topIdentifier": 0,
            "entries": [
                {"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0},
                {"option": "Chop down", "target": "<col=ffff>Tree", "type": "GAME_OBJECT_FIRST_OPTION", "identifier": 1276},
                {"option": "Walk here", "target": "", "type": "WALK", "identifier": 0},
            ],
        }

        left_click = get_left_click_entry(sample)

        self.assertEqual(left_click["option"], "Chop down")
        self.assertEqual(left_click["selectionReason"], "cancel_sentinel_ignored")
        self.assertEqual(left_click["rawTopEntry"]["option"], "Cancel")

    def test_hover_sample_rejects_true_cancel_separately_from_walk_here(self):
        intent = ActionIntent.for_target(
            activity="woodcutting",
            target_name="Tree",
            object_id=1276,
            expected_options=["Chop down", "Chop"],
            reject_options=["Walk here"],
        )
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "menuOpen": False,
            "topOption": "Cancel",
            "topTarget": "",
            "topType": "CANCEL",
            "topIdentifier": 0,
            "entries": [{"option": "Cancel", "target": "", "type": "CANCEL", "identifier": 0}],
        }

        result = hover_sample_matches_intent(sample, intent, {"x": 200, "y": 146}, tolerance_px=3)

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "cancel_hover")
        self.assertEqual(result.details["selectedMenuEntry"]["option"], "Cancel")

    def test_route_transition_proposal_builds_generic_climb_intent(self):
        proposal = ActionProposal(
            proposed_action="interact_service_route_object",
            target_kind="service_route_object",
            target_name="Staircase",
            target_explanation={
                "name": "Staircase",
                "objectId": 56230,
                "actions": ["Climb-up", "Top-floor"],
                "expectedOptions": ["Climb-up"],
                "expectedTargets": ["Staircase", "Stairs"],
            },
        )

        intent = action_intent_from_proposal(proposal)
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 210,
            "mouseCanvasY": 146,
            "topOption": "Climb-up",
            "topTarget": "Staircase",
            "topType": "GAME_OBJECT_FIRST_OPTION",
            "topIdentifier": 56230,
        }

        self.assertIn("Climb-up", intent.expected_options)
        self.assertTrue(hover_sample_matches_intent(sample, intent, {"x": 210, "y": 146}).confirmed)

    def test_deposit_resource_proposal_accepts_log_widget_hover(self):
        proposal = ActionProposal(
            proposed_action="deposit_resources",
            target_kind="bank_ui",
            target_name="logs",
            suggested_click_point={"x": 562, "y": 266},
            click_point_space="canvas",
            target_explanation={
                "targetName": "logs",
                "actions": ["Deposit-1", "Deposit-All"],
                "expectedOptions": ["Deposit"],
                "expectedTargets": ["Logs", "Log"],
            },
        )
        intent = action_intent_from_proposal(proposal)
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 562,
            "mouseCanvasY": 266,
            "topOption": "Deposit-1",
            "topTarget": "<col=ff9040>Logs",
            "topType": "CC_OP",
            "topIdentifier": 1,
        }

        result = hover_sample_matches_intent(sample, intent, {"x": 562, "y": 266}, tolerance_px=3)

        self.assertIn("Deposit-1", intent.expected_options)
        self.assertIn("Logs", intent.expected_targets)
        self.assertTrue(result.confirmed)

    def test_navigation_proposal_accepts_walk_here_hover(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3205, "worldY": 3229, "plane": 0},
            suggested_click_point={"x": 300, "y": 240},
            click_point_space="canvas",
            target_explanation={"name": "Service waypoint", "targetType": "path_tile"},
        )
        intent = action_intent_from_proposal(proposal)
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 300,
            "mouseCanvasY": 240,
            "topOption": "Walk here",
            "topTarget": "",
            "topType": "WALK",
            "topIdentifier": 0,
        }

        result = hover_sample_matches_intent(sample, intent, {"x": 300, "y": 240}, tolerance_px=3)

        self.assertEqual(intent.activity, "service_navigation")
        self.assertIn("Walk here", intent.expected_options)
        self.assertNotIn("Walk here", intent.reject_options)
        self.assertTrue(result.confirmed)
        self.assertEqual(classify_clicked_menu(None, {"clientTick": 12, "option": "Walk here", "type": "WALK"}, intent), "clicked_expected_action")

    def test_woodcutting_still_rejects_walk_here_hover(self):
        proposal = ActionProposal(
            proposed_action="select_resource_target",
            target_kind="resource",
            target_name="Tree",
            target_explanation={"name": "Tree", "objectId": 1276, "classId": "tree"},
        )
        intent = action_intent_from_proposal(proposal)
        sample = {
            "wallTimeMillis": 2200,
            "mouseCanvasX": 200,
            "mouseCanvasY": 146,
            "topOption": "Walk here",
            "topTarget": "",
            "topType": "WALK",
            "topIdentifier": 0,
        }

        result = hover_sample_matches_intent(sample, intent, {"x": 200, "y": 146}, tolerance_px=3)

        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "top_option_rejected")

    def test_menu_tail_volatility_marks_recent_npc_action_near_navigation_waypoint(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            target_tile={"worldX": 3203, "worldY": 3238, "plane": 0},
            suggested_click_point={"x": 300, "y": 240},
            click_point_space="canvas",
            target_explanation={"name": "Service waypoint", "targetType": "path_tile"},
        )
        intent = action_intent_from_proposal(proposal)
        snapshot = {
            "payloads": {
                "client_tick_tail": {
                    "postMenuSortTail": [
                        {
                            "clientTick": 10,
                            "wallTimeMillis": 1000,
                            "mouseCanvasX": 301,
                            "mouseCanvasY": 241,
                            "topOption": "Walk here",
                            "topTarget": "",
                            "topType": "WALK",
                        },
                        {
                            "clientTick": 11,
                            "wallTimeMillis": 1015,
                            "mouseCanvasX": 300,
                            "mouseCanvasY": 240,
                            "topOption": "Attack",
                            "topTarget": "Moving NPC",
                            "topType": "NPC_FIRST_OPTION",
                        },
                    ]
                }
            }
        }

        samples = post_menu_sort_tail_samples(snapshot)
        volatility = menu_tail_volatility(snapshot, {"x": 300, "y": 240}, intent, tolerance_px=3)

        self.assertEqual(len(samples), 2)
        self.assertTrue(volatility["volatileHoverZone"])
        self.assertIn("recent_npc_action", volatility["volatileReasons"])
        self.assertEqual(volatility["recentMenuTail"][-1]["option"], "Attack")

    def test_menu_tail_volatility_keeps_clean_walk_here_waypoint_nonvolatile(self):
        proposal = ActionProposal(
            proposed_action="navigate_to_service",
            target_kind="path_tile",
            target_name="Service waypoint",
            suggested_click_point={"x": 300, "y": 240},
            click_point_space="canvas",
            target_explanation={"name": "Service waypoint", "targetType": "path_tile"},
        )
        intent = action_intent_from_proposal(proposal)
        snapshot = {
            "clientTickHot": {
                "postMenuSortTail": [
                    {
                        "clientTick": 10,
                        "wallTimeMillis": 1000,
                        "mouseCanvasX": 299,
                        "mouseCanvasY": 240,
                        "topOption": "Walk here",
                        "topTarget": "",
                        "topType": "WALK",
                    },
                    {
                        "clientTick": 11,
                        "wallTimeMillis": 1015,
                        "mouseCanvasX": 300,
                        "mouseCanvasY": 239,
                        "topOption": "Walk here",
                        "topTarget": "",
                        "topType": "WALK",
                    },
                ]
            }
        }

        volatility = menu_tail_volatility(snapshot, {"x": 300, "y": 240}, intent, tolerance_px=3)

        self.assertFalse(volatility["volatileHoverZone"])
        self.assertEqual(volatility["matchingWalkHereSamples"], 2)
        self.assertEqual(volatility["volatileReasons"], [])


if __name__ == "__main__":
    unittest.main()
