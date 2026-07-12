from __future__ import annotations

import ast
import unittest
from pathlib import Path

from osrs_bot.application import ApplicationSnapshot, LifecycleState
from osrs_bot.gui import (
    GUI_PRESENTATION_SCHEMA,
    _cleanup_detail,
    _connection_mapping,
    _lifecycle_detail,
    _plain_blocker,
    _runelite_found,
)


ROOT = Path(__file__).resolve().parents[1]


def _application(*, execute_requested: bool = False) -> ApplicationSnapshot:
    return ApplicationSnapshot(
        lifecycle=LifecycleState.IDLE,
        run_id=None,
        capture_id=None,
        active_run_id=None,
        active_capture_id=None,
        execute_requested=execute_requested,
        profile_id=None,
        runtime_control=None,
        engine_frame=None,
        runtime_statistics=None,
        blockers=(),
        recent_demonstration=None,
        started_at=None,
        finished_at=None,
    )


class GuiPresentationTests(unittest.TestCase):
    def test_importing_gui_has_no_window_or_worker_side_effect(self) -> None:
        self.assertEqual("osrs_operator_gui.v1", GUI_PRESENTATION_SCHEMA)

    def test_login_and_launch_results_unwrap_authoritative_connection(self) -> None:
        result = {
            "state": "launched",
            "reason": "ready",
            "launched": True,
            "connection": {
                "endpointHealthy": True,
                "processId": 1234,
                "loadedScene": True,
            },
        }

        connection = _connection_mapping(result)

        self.assertTrue(connection["endpointHealthy"])
        self.assertEqual(1234, connection["processId"])
        self.assertEqual("launched", connection["operationState"])
        self.assertEqual("ready", connection["operationReason"])

    def test_login_result_unwraps_nested_connection_dataclass(self) -> None:
        class Connection:
            @staticmethod
            def to_dict() -> dict[str, object]:
                return {
                    "runeLiteFound": True,
                    "endpointHealthy": True,
                    "exactProcessBinding": True,
                }

        connection = _connection_mapping(
            {"recovery": {"status": "PASS"}, "connection": Connection()}
        )

        self.assertTrue(connection["runeLiteFound"])
        self.assertTrue(connection["endpointHealthy"])
        self.assertTrue(connection["exactProcessBinding"])

    def test_exact_binding_logically_proves_runelite_is_found(self) -> None:
        self.assertTrue(_runelite_found({"exactProcessBinding": True}))
        self.assertFalse(
            _runelite_found(
                {"runeLiteFound": False, "exactProcessBinding": False}
            )
        )

    def test_presentation_retains_exact_blocker_code(self) -> None:
        code = "input_process_lease_unavailable: COM6 owned"
        rendered = _plain_blocker(code)

        self.assertIn("Another process owns the Arduino lease", rendered)
        self.assertIn(code, rendered)

    def test_observe_cleanup_is_not_mislabeled_as_failure(self) -> None:
        self.assertEqual(
            "Not required in Observe Only.",
            _cleanup_detail({"attempted": False, "safe": False}, _application()),
        )
        self.assertIn(
            "could not be authoritatively confirmed",
            _cleanup_detail(
                {"attempted": True, "safe": False},
                _application(execute_requested=True),
            ),
        )

    def test_lifecycle_copy_distinguishes_requested_and_acknowledged_pause(self) -> None:
        self.assertIn(
            "awaiting a no-input boundary",
            _lifecycle_detail(LifecycleState.PAUSE_REQUESTED, None),
        )
        self.assertIn(
            "currently paused",
            _lifecycle_detail(LifecycleState.PAUSED, None),
        )

    def test_gui_source_has_no_domain_or_input_authority(self) -> None:
        path = ROOT / "osrs_bot" / "gui.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        forbidden_modules = {
            "task",
            "safety",
            "action",
            "login",
            "input_coordinator",
            "arduino",
            "demonstration",
            "debug_overlay",
        }
        self.assertTrue(forbidden_modules.isdisjoint(imported_modules))
        lowered = source.casefold()
        for forbidden in (
            "pyautogui",
            "pydirectinput",
            "serial.tools",
            ".decide(",
            ".apply_verification(",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_public_wrapper_advertises_and_routes_gui(self) -> None:
        source = (ROOT / "run.cmd").read_text(encoding="utf-8")
        self.assertIn('if /I "%MODE%"=="gui" goto gui', source)
        self.assertIn("run.cmd gui", source)
        self.assertIn("python -m osrs_bot.gui", source)


if __name__ == "__main__":
    unittest.main()
