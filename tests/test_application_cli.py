from __future__ import annotations

import ast
import json
import subprocess
import tempfile
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
            result = main(["validate-profile", "--cycle-goal", "101"])
        self.assertEqual(2, result)
        self.assertIn("cycle_goal must be no greater than 100", errors.getvalue())

    def test_external_definition_file_drives_schema_validation_and_run_binding(self) -> None:
        source = (
            ROOT
            / "examples"
            / "task_definitions"
            / "lumbridge_swamp_copper_v1.json"
        )
        output = StringIO()
        with redirect_stdout(output):
            result = main(
                ["profile-schema", "--definition-file", str(source)]
            )
        self.assertEqual(0, result)
        source_contract = json.loads(output.getvalue())
        source_definition_field = next(
            field
            for field in source_contract["fields"]
            if field["name"] == "definitionId"
        )
        self.assertEqual(
            ["lumbridge_swamp_copper_v1"],
            source_definition_field["allowedValues"],
        )
        document = json.loads(source.read_text(encoding="utf-8"))
        definition_id = "operator_copper_route_v1"
        document["definition"]["definition_id"] = definition_id
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-task.json"
            path.write_text(json.dumps(document), encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                result = main(["profile-schema", "--definition-file", str(path)])
            self.assertEqual(0, result)
            contract = json.loads(output.getvalue())
            definition_field = next(
                field
                for field in contract["fields"]
                if field["name"] == "definitionId"
            )
            self.assertEqual(definition_id, definition_field["default"])
            self.assertEqual([definition_id], definition_field["allowedValues"])

            output = StringIO()
            with redirect_stdout(output):
                result = main(["validate-profile", "--definition-file", str(path)])
            self.assertEqual(0, result)
            self.assertEqual(definition_id, json.loads(output.getvalue())["definitionId"])

            final = SimpleNamespace(
                lifecycle=LifecycleState.COMPLETE,
                to_dict=lambda: {
                    "schema": "engine_application.v2",
                    "lifecycle": "complete",
                },
            )
            application = SimpleNamespace(
                start=Mock(return_value=SimpleNamespace(run_id="run-000001")),
                wait=Mock(return_value=final),
            )
            output = StringIO()
            with (
                patch(
                    "osrs_bot.application_cli.EngineApplication",
                    return_value=application,
                ),
                redirect_stdout(output),
            ):
                result = main(["run", "--definition-file", str(path)])
            self.assertEqual(0, result)
            start_values = application.start.call_args.kwargs
            self.assertEqual(
                definition_id,
                start_values["profile_values"]["definitionId"],
            )
            self.assertEqual(
                definition_id,
                start_values["definition"].definition_id,
            )

    def test_external_definition_id_mismatch_and_unsupported_shape_fail_closed(self) -> None:
        mining = (
            ROOT
            / "examples"
            / "task_definitions"
            / "lumbridge_swamp_copper_v1.json"
        )
        unsupported = (
            ROOT
            / "examples"
            / "task_definitions"
            / "unsupported_npc_fishing_v1.json"
        )
        errors = StringIO()
        with redirect_stderr(errors):
            result = main(
                [
                    "validate-profile",
                    "--definition-file",
                    str(mining),
                    "--definition-id",
                    "lumbridge_west_trees_v1",
                ]
            )
        self.assertEqual(2, result)
        self.assertIn("does not match --definition-file", errors.getvalue())

        errors = StringIO()
        with redirect_stderr(errors):
            result = main(
                ["validate-profile", "--definition-file", str(unsupported)]
            )
        self.assertEqual(2, result)
        self.assertIn("runtime does not support", errors.getvalue())

        document = json.loads(mining.read_text(encoding="utf-8"))
        document["definition"]["definition_id"] = "operator_combat_v1"
        document["definition"]["task_type"] = "combat"
        with tempfile.TemporaryDirectory() as directory:
            combat = Path(directory) / "combat.json"
            combat.write_text(json.dumps(document), encoding="utf-8")
            errors = StringIO()
            with redirect_stderr(errors):
                result = main(
                    ["profile-schema", "--definition-file", str(combat)]
                )
        self.assertEqual(2, result)
        self.assertIn("only gathering task definitions", errors.getvalue())

    def test_nonfinite_duration_fails_closed(self) -> None:
        errors = StringIO()
        with redirect_stderr(errors):
            result = main(["validate-profile", "--duration-seconds", "nan"])
        self.assertEqual(2, result)
        self.assertIn("positive number", errors.getvalue())

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
        self.assertIn("from osrs_bot.application_cli import main", source)
        self.assertIn("from osrs_bot.task_authoring import main", source)
        self.assertIn("sys.argv[2:]", source)
        self.assertNotIn("%2 %3 %4 %5 %6 %7 %8 %9", source)

    def test_batch_entrypoint_does_not_silently_drop_late_profile_options(self) -> None:
        result = subprocess.run(
            [
                "cmd",
                "/d",
                "/c",
                "run.cmd",
                "app",
                "validate-profile",
                "--definition-id",
                "lumbridge_swamp_copper_v1",
                "--no-cycle-goal",
                "--duration-seconds",
                "1",
                "--profile-max-actions",
                "1",
                "--definitely-invalid",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(2, result.returncode)
        self.assertIn("unrecognized arguments: --definitely-invalid", result.stderr)

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
