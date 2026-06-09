import importlib
import sys
import tempfile
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = VIEWER_DIR.parent
sys.path.insert(0, str(VIEWER_DIR))


CORE_MODULES = [
    "manual_recorder",
    "analyze_manual_recording",
    "telemetry_ui",
    "context_service",
    "mcp_server",
    "task_script_api",
    "knowledge_fabric",
    "bot_eval_runner",
    "execute_next_action",
    "liveness_recovery_core",
    "live_readiness_core",
    "start_game_command",
    "candidate_core",
    "route_demonstration",
    "route_monitor",
    "route_template",
    "woodcutting_lifecycle",
    "woodcutting_loop_lifecycle",
    "banking_lifecycle",
    "interruption_lifecycle",
    "combat_damage_summary",
    "human_click_profile",
    "update_project_knowledge",
    "command_registry",
]


class ProjectBootstrapSmokeTest(unittest.TestCase):
    def test_core_modules_import(self):
        for module_name in CORE_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertIsNotNone(module)

    def test_canonical_helpers_exist(self):
        import bot_eval_runner
        import liveness_recovery_core
        import start_game_command
        import update_project_knowledge

        self.assertTrue(callable(start_game_command.resolve_start_game_command))
        self.assertTrue(callable(start_game_command.launch_start_game))
        self.assertTrue(callable(liveness_recovery_core.ensure_loaded_scene))
        self.assertTrue(callable(update_project_knowledge.check_knowledge))
        self.assertTrue(callable(bot_eval_runner.run_preflight))

    def test_simple_ui_check_can_run(self):
        import telemetry_ui

        with tempfile.TemporaryDirectory() as tmp:
            payload = telemetry_ui.check_payload(config_path=Path(tmp) / "telemetry_ui_config.json")
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["simple_screen"]["mode"], telemetry_ui.UI_MODE_SIMPLE)

    def test_route_assets_and_knowledge_indexes_exist(self):
        self.assertTrue((REPO_ROOT / "route_templates").is_dir())
        self.assertTrue(any((REPO_ROOT / "route_templates").glob("*.route_template.json")))
        self.assertTrue((REPO_ROOT / "docs" / "knowledge").is_dir())
        for name in (
            "PROJECT_STATE.md",
            "ENTRYPOINTS.md",
            "CAPABILITY_REGISTRY.md",
            "API_DATA_PATHS.md",
            "SCRIPT_API_MAP.md",
            "OPEN_GAPS.md",
        ):
            self.assertTrue((REPO_ROOT / "docs" / "knowledge" / name).exists(), name)
        for name in (
            "project_knowledge.json",
            "recordings_index.json",
            "capability_registry.json",
            "script_api_map.json",
            "open_gaps.json",
        ):
            self.assertTrue((VIEWER_DIR / "knowledge_base" / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
