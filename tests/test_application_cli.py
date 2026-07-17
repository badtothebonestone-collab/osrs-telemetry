from __future__ import annotations

import ast
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from osrs_bot.application import LifecycleState
from osrs_bot.application_cli import main


ROOT = Path(__file__).parents[1]


class ApplicationCliTests(unittest.TestCase):
    def test_catalog_schema_and_validation_commands_emit_json(self) -> None:
        for arguments in (["catalog"], ["profile-schema"], ["validate-profile"]):
            with self.subTest(arguments=arguments):
                output = StringIO()
                with redirect_stdout(output):
                    result = main(arguments)
                self.assertEqual(0, result)
                self.assertIsInstance(json.loads(output.getvalue()), dict)

    def test_invalid_profile_fails_without_starting_runtime(self) -> None:
        errors = StringIO()
        with redirect_stderr(errors):
            result = main(["validate-profile", "--cycle-goal", "2"])
        self.assertEqual(2, result)
        self.assertIn("unsupported cycle_goal", errors.getvalue())

    def test_foreground_interrupt_requests_safe_stop_and_waits(self) -> None:
        final = SimpleNamespace(
            lifecycle=LifecycleState.STOPPED,
            to_dict=lambda: {"schema": "engine_application.v1", "lifecycle": "stopped"},
        )
        application = SimpleNamespace()
        application.start = lambda **_values: SimpleNamespace(run_id="run-000001")
        application.wait = Mock(side_effect=[KeyboardInterrupt(), final])
        application.request_safe_stop = Mock()
        application.snapshot = lambda: SimpleNamespace(
            active_run_id="run-000001",
            run_id="run-000001",
        )
        output = StringIO()
        with (
            patch(
                "osrs_bot.application_cli.EngineApplication",
                return_value=application,
            ),
            redirect_stdout(output),
        ):
            result = main(["run"])

        self.assertEqual(0, result)
        application.request_safe_stop.assert_called_once_with("run-000001")
        self.assertEqual(2, application.wait.call_count)
        self.assertEqual("stopped", json.loads(output.getvalue())["lifecycle"])

    def test_interrupt_during_start_recovers_and_stops_the_active_worker(self) -> None:
        final = SimpleNamespace(
            lifecycle=LifecycleState.STOPPED,
            to_dict=lambda: {
                "schema": "engine_application.v1",
                "lifecycle": "stopped",
            },
        )
        application = SimpleNamespace()
        application.start = Mock(side_effect=KeyboardInterrupt())
        application.snapshot = Mock(
            return_value=SimpleNamespace(
                active_run_id="run-000001",
                run_id="run-000001",
            )
        )
        application.request_safe_stop = Mock()
        application.wait = Mock(return_value=final)
        output = StringIO()
        with (
            patch(
                "osrs_bot.application_cli.EngineApplication",
                return_value=application,
            ),
            redirect_stdout(output),
        ):
            result = main(["run"])

        self.assertEqual(0, result)
        application.request_safe_stop.assert_called_once_with("run-000001")
        application.wait.assert_called_once_with("run-000001")
        self.assertEqual("stopped", json.loads(output.getvalue())["lifecycle"])

    def test_overlay_rejected_detail_requires_overlay(self) -> None:
        errors = StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["run", "--overlay-show-rejected"])

        self.assertEqual(2, raised.exception.code)
        self.assertIn("requires --overlay", errors.getvalue())

    def test_overlay_routes_through_facade_and_always_cleans_up(self) -> None:
        final = SimpleNamespace(
            lifecycle=LifecycleState.COMPLETE,
            to_dict=lambda: {
                "schema": "engine_application.v1",
                "lifecycle": "complete",
            },
        )
        application = SimpleNamespace(
            start=Mock(return_value=SimpleNamespace(run_id="run-000001")),
            wait=Mock(return_value=final),
            set_overlay_enabled=Mock(
                side_effect=[
                    SimpleNamespace(error=None),
                    SimpleNamespace(error=None),
                ]
            ),
            overlay_snapshot=Mock(
                return_value=SimpleNamespace(error="passive styles unavailable")
            ),
        )
        output = StringIO()
        errors = StringIO()
        with (
            patch(
                "osrs_bot.application_cli.EngineApplication",
                return_value=application,
            ),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            result = main(["run", "--overlay", "--overlay-show-rejected"])

        self.assertEqual(0, result)
        self.assertEqual("complete", json.loads(output.getvalue())["lifecycle"])
        self.assertIn("Diagnostic overlay unavailable", errors.getvalue())
        self.assertEqual(
            [call(True, show_rejected=True), call(False)],
            application.set_overlay_enabled.call_args_list,
        )
        application.overlay_snapshot.assert_called_once_with()

    def test_batch_entrypoint_exposes_application_facade(self) -> None:
        source = (ROOT / "run.cmd").read_text(encoding="utf-8").lower()
        self.assertIn('if /i "%mode%"=="app" goto app', source)
        self.assertIn("python -m osrs_bot.application_cli run %~2 %~3", source)
        self.assertIn(
            'python -m osrs_bot.application_cli run --execute --arduino-port "%~2"',
            source,
        )
        self.assertNotIn("python -m osrs_bot task", source)

    def test_cli_has_no_task_safety_or_input_authority(self) -> None:
        source = (ROOT / "osrs_bot" / "application_cli.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in (
            "WoodcutBankTask",
            "SafetyGate",
            "InputCoordinator",
            "Verifier",
            "Arduino",
        ):
            self.assertNotIn(forbidden, imports)
        self.assertNotIn(".decide(", source)
        self.assertNotIn(".apply_verification(", source)


if __name__ == "__main__":
    unittest.main()
