from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from osrs_bot.engine_frame import EngineFramePublisher
from osrs_bot.input_coordinator import read_arduino_lease_status
from osrs_bot.login import RuneLiteWindow
from osrs_bot.model import ScreenBounds, ScreenPoint
from osrs_bot.observation import parse_observation
from osrs_bot.operator_services import (
    ArduinoReadiness,
    ConnectionState,
    OperatorServices,
    OverlayState,
    PassiveOverlayOwner,
    ProcessResult,
    ProcessStatus,
    RETAINED_LAYOUT_CANVAS_SIZE,
    RETAINED_LAYOUT_CLIENT_SIZE,
    RuneLiteLaunchState,
    retained_layout_supported,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = parse_observation(
    json.loads(
        (ROOT / "tests" / "fixtures" / "snapshot_loaded.json").read_text(
            encoding="utf-8"
        )
    )
)
WINDOW = RuneLiteWindow(
    77,
    1234,
    "RuneLite - operator test",
    ScreenBounds(900, 1900, 1_000, 800),
    ScreenBounds(890, 1860, 1_020, 850),
)


class _SequenceClient:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.calls = 0

    def fetch(self):
        self.calls += 1
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        if isinstance(value, BaseException):
            raise value
        return value


class _LaunchHandle:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code

    def poll(self) -> int | None:
        return self.return_code


class _FakeOverlay:
    def __init__(
        self,
        *,
        start_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.start_error = start_error
        self.stop_error = stop_error
        self.started = 0
        self.stopped = 0

    def start(self, *, timeout_seconds: float = 3.0) -> None:
        self.started += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self, *, timeout_seconds: float = 3.0) -> None:
        self.stopped += 1
        if self.stop_error is not None:
            raise self.stop_error


class _FakeProcessRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], float, Path]] = []

    def __call__(
        self,
        command,
        *,
        cwd: Path,
        timeout_seconds: float,
        log_path: Path,
    ) -> ProcessResult:
        values = tuple(str(value) for value in command)
        self.calls.append((values, timeout_seconds, log_path))
        if "rev-parse" in values:
            output = "0123456789abcdef"
        elif "status" in values:
            output = ""
        elif values and values[0] == "java":
            output = 'openjdk version "17.0.12"'
        elif "--version" in values:
            output = "Gradle 8.8"
        else:
            output = "OK"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"command: {' '.join(values)}\n{output}\n", encoding="utf-8")
        return ProcessResult(
            ProcessStatus.PASS,
            values,
            0,
            0.01,
            f"command: {' '.join(values)}\n{output}".strip(),
            log_path,
        )


class OperatorServiceConnectionTests(unittest.TestCase):
    def test_live_handoff_focuses_only_exact_window_and_waits_for_proof(self) -> None:
        unfocused = replace(FIXTURE, client_focused=False)
        focused = replace(FIXTURE, client_focused=True)
        client = _SequenceClient(unfocused, focused)
        focused_windows: list[RuneLiteWindow] = []
        services = OperatorServices(
            client,
            window_finder=lambda pid: WINDOW if pid == 1234 else None,
            window_focuser=lambda window: focused_windows.append(window) or True,
            process_checker=lambda pid: pid == 1234,
            cursor_reader=lambda: None,
            layout_checker=lambda _observation: True,
            serial_port_lister=lambda: (),
        )

        status = services.focus_runelite_for_live_handoff()

        self.assertTrue(status.foreground)
        self.assertTrue(status.exact_process_binding)
        self.assertEqual([WINDOW], focused_windows)

    def test_connection_uses_the_shared_client_and_exact_window_binding(self) -> None:
        client = _SequenceClient(FIXTURE)
        services = OperatorServices(
            client,
            window_finder=lambda pid: WINDOW if pid == 1234 else None,
            window_scanner=lambda: (),
            process_checker=lambda pid: pid == 1234,
            cursor_reader=lambda: ScreenPoint(1_000, 2_000),
            layout_checker=lambda _observation: True,
            serial_port_lister=lambda: (),
        )

        status = services.connection_status()

        self.assertIs(services.client, client)
        self.assertIs(status.state, ConnectionState.CONNECTED)
        self.assertTrue(status.endpoint_healthy)
        self.assertTrue(status.runelite_found)
        self.assertEqual(1234, status.process_id)
        self.assertEqual("fixture-session", status.session_id)
        self.assertTrue(status.exact_process_binding)
        self.assertTrue(status.loaded_scene)
        self.assertTrue(status.coherent_fresh_observation)
        self.assertTrue(status.cursor_inside_client)
        self.assertTrue(status.layout_supported)
        self.assertIsNone(status.blocker)
        self.assertEqual("operator_preflight.v1", status.to_dict()["schema"])

    def test_connected_login_scene_is_not_mislabeled_as_loaded(self) -> None:
        login_scene = replace(FIXTURE, game_state="LOGIN_SCREEN")
        services = OperatorServices(
            _SequenceClient(login_scene),
            window_finder=lambda _pid: WINDOW,
            process_checker=lambda _pid: True,
            cursor_reader=lambda: None,
            layout_checker=lambda _observation: None,
            serial_port_lister=lambda: (),
        )

        status = services.connection_status()

        self.assertIs(status.state, ConnectionState.CONNECTED)
        self.assertFalse(status.loaded_scene)
        self.assertEqual("loaded_scene_not_ready", status.blocker)
        self.assertIn("gameState=LOGIN_SCREEN", status.diagnostic)

    def test_default_layout_check_accepts_only_retained_exact_dimensions(self) -> None:
        exact = replace(
            FIXTURE,
            canvas_bounds=ScreenBounds(
                100,
                200,
                RETAINED_LAYOUT_CANVAS_SIZE[0],
                RETAINED_LAYOUT_CANVAS_SIZE[1],
            ),
            client_window_bounds=ScreenBounds(
                50,
                150,
                RETAINED_LAYOUT_CLIENT_SIZE[0],
                RETAINED_LAYOUT_CLIENT_SIZE[1],
            ),
        )
        mismatched = replace(
            exact,
            canvas_bounds=ScreenBounds(
                100,
                200,
                RETAINED_LAYOUT_CANVAS_SIZE[0] - 1,
                RETAINED_LAYOUT_CANVAS_SIZE[1],
            ),
        )
        missing = replace(exact, client_window_bounds=None)

        self.assertTrue(retained_layout_supported(exact))
        self.assertFalse(retained_layout_supported(mismatched))
        self.assertIsNone(retained_layout_supported(missing))

        for observation, expected in (
            (exact, True),
            (mismatched, False),
            (missing, None),
        ):
            status = OperatorServices(
                _SequenceClient(observation),
                window_finder=lambda _pid: WINDOW,
                process_checker=lambda _pid: True,
                cursor_reader=lambda: None,
                serial_port_lister=lambda: (),
            ).connection_status()
            self.assertIs(expected, status.layout_supported)

    def test_existing_window_blocks_duplicate_launch_when_endpoint_is_down(self) -> None:
        launched: list[Path] = []
        services = OperatorServices(
            _SequenceClient(RuntimeError("endpoint down")),
            window_scanner=lambda: (WINDOW,),
            plugin_launcher=lambda _root, log: launched.append(log),
            serial_port_lister=lambda: (),
        )

        result = services.launch_or_connect_runelite(timeout_seconds=1.0)

        self.assertIs(result.state, RuneLiteLaunchState.BLOCKED)
        self.assertFalse(result.launched)
        self.assertEqual([], launched)
        self.assertTrue(result.connection.runelite_found)

    def test_launch_waits_boundedly_for_exact_connection(self) -> None:
        client = _SequenceClient(
            RuntimeError("not started"),
            RuntimeError("starting"),
            FIXTURE,
        )
        launch_calls: list[tuple[Path, Path]] = []

        def launch(root: Path, log: Path) -> _LaunchHandle:
            launch_calls.append((root, log))
            return _LaunchHandle()

        services = OperatorServices(
            client,
            window_finder=lambda _pid: WINDOW,
            window_scanner=lambda: (),
            process_checker=lambda _pid: True,
            plugin_launcher=launch,
            sleep=lambda _seconds: None,
            serial_port_lister=lambda: (),
        )

        result = services.launch_or_connect_runelite(
            timeout_seconds=1.0,
            poll_seconds=0.05,
        )

        self.assertIs(result.state, RuneLiteLaunchState.LAUNCHED)
        self.assertTrue(result.launched)
        self.assertTrue(result.successful)
        self.assertEqual(1, len(launch_calls))
        self.assertEqual(3, client.calls)

    def test_live_launch_handle_prevents_a_second_launch_attempt(self) -> None:
        launches: list[Path] = []
        clock_value = 0.0

        def clock() -> float:
            nonlocal clock_value
            clock_value += 0.1
            return clock_value

        def launch(_root: Path, log: Path) -> _LaunchHandle:
            launches.append(log)
            return _LaunchHandle()

        services = OperatorServices(
            _SequenceClient(RuntimeError("endpoint down")),
            window_scanner=lambda: (),
            plugin_launcher=launch,
            monotonic=clock,
            sleep=lambda _seconds: None,
            serial_port_lister=lambda: (),
        )

        first = services.launch_or_connect_runelite(
            timeout_seconds=0.2,
            poll_seconds=0.05,
        )
        second = services.launch_or_connect_runelite(
            timeout_seconds=0.2,
            poll_seconds=0.05,
        )

        self.assertIs(first.state, RuneLiteLaunchState.STARTING)
        self.assertTrue(first.launched)
        self.assertIs(second.state, RuneLiteLaunchState.STARTING)
        self.assertFalse(second.launched)
        self.assertEqual(1, len(launches))


class OperatorServiceInputAndOverlayTests(unittest.TestCase):
    def test_login_delegates_to_existing_helper_and_coordinator(self) -> None:
        client = _SequenceClient(FIXTURE)
        coordinator = object()
        calls: list[object] = []

        class Helper:
            def run(self, *, max_clicks: int, timeout_seconds: float):
                calls.append((max_clicks, timeout_seconds))
                return SimpleNamespace(
                    status="PASS",
                    reason="loaded_scene_verified",
                    loaded_scene=True,
                    elapsed_seconds=0.25,
                    clicks=(),
                )

        def coordinator_factory(port: str):
            calls.append(port)
            return coordinator

        def helper_factory(shared_client, shared_coordinator):
            self.assertIs(shared_client, client)
            self.assertIs(shared_coordinator, coordinator)
            return Helper()

        services = OperatorServices(
            client,
            coordinator_factory=coordinator_factory,
            login_helper_factory=helper_factory,
            serial_port_lister=lambda: (),
        )

        result = services.recover_session("COM6", timeout_seconds=2.0)

        self.assertTrue(result.successful)
        self.assertEqual("NOT_REQUIRED", result.cleanup_status)
        self.assertEqual(["COM6", (3, 2.0)], calls)
        with self.assertRaises(FrozenInstanceError):
            result.status = "FAIL"  # type: ignore[misc]

    def test_arduino_readiness_lists_ports_without_opening_them(self) -> None:
        captured_at = datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc)
        services = OperatorServices(
            _SequenceClient(FIXTURE),
            serial_port_lister=lambda: ("COM7", "COM6", "COM6"),
            lease_reader=lambda port: {
                "status": "AVAILABLE",
                "available": True,
                "owner": None,
                "ownerPid": None,
                "reason": f"{port} is free",
            },
            now=lambda: captured_at,
        )

        status = services.arduino_readiness("com6")

        self.assertIsInstance(status, ArduinoReadiness)
        self.assertEqual(("COM6", "COM7"), status.available_ports)
        self.assertTrue(status.port_available)
        self.assertTrue(status.lease_available)
        self.assertTrue(status.ready)
        self.assertEqual(captured_at, status.captured_at)
        self.assertEqual(2_000, status.max_age_millis)
        self.assertEqual(captured_at.isoformat(), status.to_dict()["capturedAtUtc"])

        legacy = ArduinoReadiness(
            "COM6",
            ("COM6",),
            True,
            "AVAILABLE",
            True,
            None,
            None,
            "COM6 is free",
        )
        self.assertIsNone(legacy.captured_at)

        clock_failure = OperatorServices(
            _SequenceClient(FIXTURE),
            serial_port_lister=lambda: ("COM6",),
            lease_reader=lambda _port: {
                "status": "AVAILABLE",
                "available": True,
                "reason": "free",
            },
            now=lambda: (_ for _ in ()).throw(RuntimeError("clock failed")),
        ).arduino_readiness("COM6")
        self.assertTrue(clock_failure.ready)
        self.assertIsNone(clock_failure.captured_at)

    def test_read_only_lease_probe_never_acquires_or_removes_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            lock_dir = Path(folder)
            available = read_arduino_lease_status("COM6", lock_dir=lock_dir)
            self.assertTrue(available["available"])
            path = Path(available["lockPath"])
            self.assertFalse(path.exists())

            now_millis = 2_000_000
            path.write_text(
                json.dumps(
                    {
                        "schema": "arduino_serial_lock.v1",
                        "port": "COM6",
                        "pid": os.getpid(),
                        "owner": "test-owner",
                        "createdAtMillis": now_millis,
                    }
                ),
                encoding="utf-8",
            )
            owned = read_arduino_lease_status(
                "COM6",
                lock_dir=lock_dir,
                now_millis=now_millis,
            )

            self.assertEqual("OWNED", owned["status"])
            self.assertFalse(owned["available"])
            self.assertTrue(path.exists())

    def test_overlay_owner_reuses_and_stops_the_existing_overlay(self) -> None:
        created: list[_FakeOverlay] = []

        def factory(
            _publisher,
            _show_rejected: bool,
            _presentation_provider,
            _bound_run_id,
        ):
            overlay = _FakeOverlay()
            created.append(overlay)
            return overlay

        owner = PassiveOverlayOwner(factory)
        publisher = EngineFramePublisher()

        self.assertIs(owner.enable(publisher).state, OverlayState.ACTIVE)
        self.assertIs(owner.enable(publisher).state, OverlayState.ACTIVE)
        self.assertEqual(1, len(created))
        self.assertEqual(1, created[0].started)
        self.assertIs(owner.disable().state, OverlayState.DISABLED)
        self.assertEqual(1, created[0].stopped)

    def test_overlay_failure_is_visible_and_nonthrowing(self) -> None:
        owner = PassiveOverlayOwner(
            lambda _publisher, _show_rejected, _presentation, _run_id: _FakeOverlay(
                start_error=RuntimeError("overlay exploded")
            )
        )

        status = owner.enable(EngineFramePublisher())

        self.assertIs(status.state, OverlayState.FAILED)
        self.assertIn("overlay exploded", status.error)

    def test_failed_overlay_stop_prevents_an_orphaning_rebind(self) -> None:
        first = _FakeOverlay(stop_error=RuntimeError("still running"))
        second = _FakeOverlay()
        created = [first, second]
        owner = PassiveOverlayOwner(
            lambda _publisher, _show_rejected, _presentation, _run_id: created.pop(0)
        )
        first_publisher = EngineFramePublisher()
        second_publisher = EngineFramePublisher()
        self.assertIs(owner.enable(first_publisher).state, OverlayState.ACTIVE)

        rebound = owner.enable(second_publisher)

        self.assertIs(rebound.state, OverlayState.FAILED)
        self.assertIn("still running", rebound.error)
        self.assertEqual(0, second.started)
        self.assertEqual([second], created)


class OperatorServiceDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_and_checks_are_bounded_and_concise(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
            demo_old = root / "demo_runs" / "old"
            demo_new = root / "demo_runs" / "new"
            demo_old.mkdir(parents=True)
            demo_new.mkdir(parents=True)
            (demo_old / "manifest.json").write_text("{}", encoding="utf-8")
            (demo_new / "manifest.json").write_text("{}", encoding="utf-8")
            os.utime(demo_old / "manifest.json", (1, 1))
            os.utime(demo_new / "manifest.json", (2, 2))
            runner = _FakeProcessRunner()
            stamp = datetime(2026, 7, 12, 12, 30, tzinfo=timezone.utc)
            services = OperatorServices(
                _SequenceClient(FIXTURE),
                repo_root=root,
                process_runner=runner,
                serial_port_lister=lambda: ("COM6",),
                now=lambda: stamp,
            )

            diagnostics = services.collect_diagnostics()
            quick = services.run_quick_self_test(timeout_seconds=10.0)
            replay = services.run_golden_replay(timeout_seconds=10.0)

            self.assertEqual("0123456789abcdef", diagnostics.commit)
            self.assertFalse(diagnostics.dirty)
            self.assertTrue(diagnostics.java_available)
            self.assertEqual('openjdk version "17.0.12"', diagnostics.java_version)
            self.assertTrue(diagnostics.gradle_available)
            self.assertEqual("Gradle 8.8", diagnostics.gradle_version)
            self.assertEqual(demo_new, diagnostics.latest_demonstration_path)
            self.assertEqual(("COM6",), diagnostics.available_arduino_ports)
            self.assertEqual("operator_diagnostics.v1", diagnostics.to_dict()["schema"])
            self.assertIs(quick.status, ProcessStatus.PASS)
            self.assertIs(replay.status, ProcessStatus.PASS)
            self.assertTrue(any("tests.test_operator_services" in call[0] for call in runner.calls))
            self.assertTrue(any("tests.test_gui_controller" in call[0] for call in runner.calls))
            self.assertTrue(any("replay" in call[0] for call in runner.calls))
            self.assertTrue(all(call[1] <= 30.0 for call in runner.calls[:4]))

    def test_operator_module_has_no_task_safety_or_software_input_authority(self) -> None:
        path = ROOT / "osrs_bot" / "operator_services.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in (
            "SafetyGate",
            "Verifier",
            "WoodcutBankTask",
            "Action",
            "_ArduinoHIDTransport",
        ):
            self.assertNotIn(forbidden, imported_names)
        lowered = source.casefold()
        for forbidden_text in (
            "pyautogui",
            "pydirectinput",
            "setcursorpos",
            "mouse_event",
            ".decide(",
            ".apply_verification(",
        ):
            self.assertNotIn(forbidden_text, lowered)


if __name__ == "__main__":
    unittest.main()
