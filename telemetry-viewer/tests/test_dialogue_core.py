import sys
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import dialogue_core


class DialogueCoreTest(unittest.TestCase):
    def test_climb_dialogue_selects_up_option_for_positive_plane_change(self):
        dialogue = {
            "schema": "dialogue_state.v1",
            "active": True,
            "type": "options",
            "promptText": "Climb up or down the stairs?",
            "canUseNumberKeys": True,
            "options": [
                {"index": 1, "key": "1", "text": "Climb up the stairs."},
                {"index": 2, "key": "2", "text": "Climb down the stairs."},
            ],
        }
        route_step = {"planeChange": "+1", "label": "second stairs up"}

        choice = dialogue_core.route_dialogue_choice(dialogue, route_step)

        self.assertIsNotNone(choice)
        self.assertEqual(choice["status"], "PASS")
        self.assertEqual(choice["selectionMethod"], "number_key")
        self.assertEqual(choice["key"], "1")
        self.assertEqual(choice["option"]["text"], "Climb up the stairs.")

    def test_climb_dialogue_selects_down_option_for_negative_plane_change(self):
        dialogue = {
            "schema": "dialogue_state.v1",
            "active": True,
            "type": "options",
            "promptText": "Climb up or down the stairs?",
            "canUseNumberKeys": True,
            "options": [
                {"index": 1, "key": "1", "text": "Climb up the stairs."},
                {"index": 2, "key": "2", "text": "Climb down the stairs."},
            ],
        }
        route_step = {"planeChange": "-1", "label": "stairs down"}

        choice = dialogue_core.route_dialogue_choice(dialogue, route_step)

        self.assertIsNotNone(choice)
        self.assertEqual(choice["status"], "PASS")
        self.assertEqual(choice["key"], "2")
        self.assertEqual(choice["option"]["text"], "Climb down the stairs.")

    def test_missing_route_direction_fails_without_selecting_option(self):
        dialogue = {
            "schema": "dialogue_state.v1",
            "active": True,
            "type": "options",
            "options": [{"index": 1, "key": "1", "text": "Climb up the stairs."}],
        }

        choice = dialogue_core.route_dialogue_choice(dialogue, {"label": "unknown stairs"})

        self.assertIsNotNone(choice)
        self.assertEqual(choice["status"], "FAIL")
        self.assertEqual(choice["reason"], "route_direction_unknown")


if __name__ == "__main__":
    unittest.main()
