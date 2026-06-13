import json
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import start_game_command


class StartGameCommandTest(unittest.TestCase):
    def test_gradlew_command_is_dev_launch(self):
        mode = start_game_command.classify_launch_mode("cmd /c .\\gradlew.bat --no-daemon run", command_source="dev")

        self.assertEqual(mode["launchMode"], "dev_gradle_run")
        self.assertFalse(mode["authenticatedLaunchLikely"])
        self.assertTrue(mode["warnings"])

    def test_jagex_quick_launch_is_authenticated_live_mode(self):
        command = r'"C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite'
        mode = start_game_command.classify_launch_mode(command, command_source="live")

        self.assertEqual(mode["launchMode"], "jagex_launcher_runelite_quick_launch")
        self.assertTrue(mode["authenticatedLaunchLikely"])

    def test_live_resolution_prefers_live_command_over_dev_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
            config_path = Path(tmp) / "telemetry_ui_config.json"
            live_command = r'"C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite'
            config_path.write_text(
                json.dumps(
                    {
                        "dev_start_command": "cmd /c .\\gradlew.bat --no-daemon run",
                        "game_launch_command": "cmd /c .\\gradlew.bat --no-daemon run",
                        "live_start_command": live_command,
                    }
                ),
                encoding="utf-8",
            )

            resolved = start_game_command.resolve_start_game_command(
                root=root,
                config_path=config_path,
                prefer_authenticated=True,
            )

        self.assertEqual(resolved["status"], "PASS")
        self.assertEqual(resolved["command"], live_command)
        self.assertEqual(resolved["launchMode"], "jagex_launcher_runelite_quick_launch")
        self.assertEqual(resolved["devLaunchMode"], "dev_gradle_run")
        self.assertTrue(resolved["authenticatedLaunchLikely"])

    def test_dev_gradle_is_rejected_when_used_as_live_command(self):
        resolved = start_game_command.resolve_start_game_command(
            configured_command="cmd /c .\\gradlew.bat --no-daemon run",
            prefer_authenticated=True,
        )

        self.assertEqual(resolved["status"], "FAIL")
        self.assertEqual(resolved["reason"], "authenticated_live_start_missing")
        self.assertEqual(resolved["launchMode"], "dev_gradle_run")
        self.assertFalse(resolved["authenticatedLaunchLikely"])

    def test_set_live_command_writes_compatibility_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "telemetry_ui_config.json"
            command = r'"C:\Program Files (x86)\Jagex Launcher\JagexLauncher.exe" --launch=osrs_runelite'
            result = start_game_command.set_live_start_command(command, config_path=path)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(saved["live_start_command"], command)
        self.assertEqual(saved["authenticated_game_start_command"], command)


if __name__ == "__main__":
    unittest.main()
