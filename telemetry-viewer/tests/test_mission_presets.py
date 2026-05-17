import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import mission_presets


class MissionPresetModelTest(unittest.TestCase):
    def test_named_presets_resolve_expected_runtime_fields(self):
        expected = {
            "woodcut_bank": ("woodcutting", "woodcutting_bank", 5, False),
            "woodcut_firemake": ("woodcutting", "woodcutting_firemake", 5, False),
            "woodcut_drop": ("woodcutting", "woodcutting_drop", 5, False),
            "observe_only": ("observe", "observe_only", None, True),
            "combat_default": ("combat", "combat_default", None, False),
        }

        for name, (active_task, policy, goal_count, observe_only) in expected.items():
            with self.subTest(name=name):
                preset = mission_presets.resolve_mission_preset(name)
                fields = mission_presets.runtime_control_fields_for_preset(name)

                self.assertEqual(preset.name, name)
                self.assertEqual(fields["activeTask"], active_task)
                self.assertEqual(fields["taskPolicy"], policy)
                self.assertEqual(fields["goalCount"], goal_count)
                self.assertEqual(fields["observeOnly"], observe_only)
                self.assertTrue(fields["brainEnabled"])
                self.assertEqual(fields["overlayMode"], "intent")
                self.assertEqual(fields["overlayBackupCandidates"], 2)
                self.assertTrue(preset.noActionEmitted)
                self.assertTrue(preset.description)

    def test_goal_override_does_not_mutate_static_preset(self):
        fields = mission_presets.runtime_control_fields_for_preset("woodcut_firemake", goal_count=9)
        original = mission_presets.runtime_control_fields_for_preset("woodcut_firemake")

        self.assertEqual(fields["goalCount"], 9)
        self.assertEqual(original["goalCount"], 5)

    def test_unknown_preset_is_rejected(self):
        with self.assertRaises(KeyError):
            mission_presets.resolve_mission_preset("not_a_preset")

    def test_preset_resolution_writes_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = set(Path(tmp).iterdir())
            mission_presets.runtime_control_fields_for_preset("woodcut_bank")
            after = set(Path(tmp).iterdir())

        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
