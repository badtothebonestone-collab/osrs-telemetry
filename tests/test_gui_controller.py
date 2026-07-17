from __future__ import annotations

import ast
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from osrs_bot.application import ApplicationSnapshot, LifecycleState
from osrs_bot.engine_frame import EngineFramePublisher, EngineStage
from osrs_bot.gui_controller import (
    MAX_EVENT_HISTORY,
    GuiController,
    GuiControllerBusyError,
    GuiControllerClosedError,
)
from osrs_bot.frontend_presentation import (
    PresentationHysteresisState,
    PresentationState,
    classify_frontend_presentation,
)
from osrs_bot.gui_settings import (
    SETTINGS_SCHEMA,
    GuiSettings,
    GuiSettingsStore,
)
from osrs_bot.operator_services import ArduinoReadiness
from osrs_bot.task_contract import TaskSnapshot, TaskStatus


ROOT = Path(__file__).resolve().parents[1]
CATALOG = {
    "schema": "engine_catalog.v1",
    "tasks": [
        {
            "taskId": "woodcut_bank",
            "displayName": "Woodcut and bank",
            "definitionIds": ["lumbridge_west_trees_v1"],
        }
    ],
    "definitions": [
        {
            "definitionId": "lumbridge_west_trees_v1",
            "displayName": "Lumbridge West Trees",
            "resource": {"name": "Tree", "profileSelectable": False},
            "bank": {"name": "Bank booth", "profileSelectable": False},
        }
    ],
    "profile": {
        "schema": "osrs_profile_contract.v1",
        "additionalProperties": False,
        "fields": [
            {
                "name": "profileId",
                "type": "identifier",
                "required": True,
                "default": "default_lumbridge_west_trees_v1",
            },
            {
                "name": "definitionId",
                "type": "enum",
                "required": True,
                "default": "lumbridge_west_trees_v1",
                "allowedValues": ["lumbridge_west_trees_v1"],
            },
            {
                "name": "cycleGoal",
                "type": "integer",
                "required": True,
                "default": 1,
                "allowedValues": [1],
            },
        ],
    },
}
NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


def _snapshot(
    *,
    lifecycle: LifecycleState = LifecycleState.IDLE,
    run_id: str | None = None,
    active_run_id: str | None = None,
    capture_id: str | None = None,
    active_capture_id: str | None = None,
    execute_requested: bool = False,
    profile_id: str | None = None,
    frame=None,
    blockers: tuple[str, ...] = (),
) -> ApplicationSnapshot:
    return ApplicationSnapshot(
        lifecycle=lifecycle,
        run_id=run_id,
        capture_id=capture_id,
        active_run_id=active_run_id,
        active_capture_id=active_capture_id,
        execute_requested=execute_requested,
        profile_id=profile_id,
        runtime_control=None,
        engine_frame=frame,
        runtime_statistics=None,
        blockers=blockers,
        recent_demonstration=None,
        started_at=None,
        finished_at=None,
    )


def _frame(sequence: int, *, state: str = "find_tree"):
    publisher = EngineFramePublisher()
    frame = None
    for index in range(sequence):
        frame = publisher.publish(
            stage=EngineStage.DECIDED,
            task=TaskSnapshot(
                "woodcut_bank",
                TaskStatus.RUNNING,
                state if index == sequence - 1 else f"state-{index}",
            ),
        )
    assert frame is not None
    return frame


def _ready_presentation():
    connection = SimpleNamespace(
        endpoint_healthy=True,
        runelite_found=True,
        exact_process_binding=True,
        process_id=1234,
        session_id="session-a",
        loaded_scene=True,
        coherent_fresh_observation=True,
        source_captured_at=NOW,
        max_source_age_millis=2_000,
        source_tick=50,
        blocker=None,
        diagnostic=None,
    )
    return classify_frontend_presentation(
        lifecycle=LifecycleState.IDLE,
        run_id=None,
        frame_run_id=None,
        execute_requested=False,
        frame=None,
        connection=connection,
        now=NOW + timedelta(milliseconds=100),
    )


class _FakeApplication:
    def __init__(self, initial: ApplicationSnapshot | None = None) -> None:
        self.current = initial or _snapshot()
        self.calls: list[tuple] = []
        self.run_number = 0
        self.capture_number = 0
        self.overlay_enabled = False
        self.connection_gate: threading.Event | None = None
        self.connection_entered = threading.Event()
        self.connection_calls = 0
        self.connection_error: Exception | None = None
        self.leave_run_active_on_wait = False
        self.shutdown_calls = 0
        self.presentation_ready = True
        self.presentation_state = "READY"
        self.expected_process_id = None
        self.expected_session_id = None
        self.current_process_id = None
        self.current_session_id = None
        self.presentation_override = None

    @staticmethod
    def catalog():
        return CATALOG

    def validate_profile(self, values):
        self.calls.append(("validate_profile", dict(values)))
        if values.get("definitionId") != "lumbridge_west_trees_v1":
            raise ValueError("unsupported definitionId")
        if values.get("cycleGoal") != 1:
            raise ValueError("unsupported cycleGoal")
        profile_id = values.get("profileId")
        if not isinstance(profile_id, str) or not profile_id or profile_id == "invalid":
            raise ValueError("invalid profileId")
        return object()

    def snapshot(self):
        return self.current

    def runtime_configuration(self):
        self.calls.append(("runtime_configuration",))
        return {"maxActions": 100, "maxRuntimeSeconds": 900}

    def frontend_presentation(self, **_values):
        if self.presentation_override is not None:
            return self.presentation_override
        terminal = self.current.lifecycle in {
            LifecycleState.COMPLETE,
            LifecycleState.BLOCKED,
            LifecycleState.ERROR,
            LifecycleState.STOPPED,
        }
        return SimpleNamespace(
            state=SimpleNamespace(value=self.presentation_state),
            start_live_allowed=self.presentation_ready,
            reconnect_guidance=(
                None
                if self.presentation_ready
                else "wait for a fresh coherent loaded Observation"
            ),
            expected_process_id=self.expected_process_id,
            expected_session_id=self.expected_session_id,
            process_id=self.current_process_id,
            session_id=self.current_session_id,
            terminal_summary=terminal,
            runtime_status=("COMPLETE" if terminal else None),
            terminal_reason=("terminal test result" if terminal else None),
            terminal_outcome=None,
            cleanup=(
                self.current.engine_frame.cleanup
                if terminal and self.current.engine_frame is not None
                else None
            ),
        )

    def set_arduino_port(self, port):
        self.calls.append(("set_arduino_port", port))

    def prepare_live_handoff(self):
        self.calls.append(("prepare_live_handoff",))
        return {"foreground": True}

    def arduino_readiness(self, port):
        self.calls.append(("arduino_readiness", port))
        return {"port": port, "available": True, "leaseAvailable": True}

    def start(self, *, profile_values, execute=False, live_evidence_root=None):
        self.calls.append(
            ("start", execute, dict(profile_values), live_evidence_root)
        )
        if self.current.active_run_id or self.current.active_capture_id:
            raise RuntimeError("another operation is active")
        self.run_number += 1
        run_id = f"run-{self.run_number:06d}"
        self.current = _snapshot(
            lifecycle=LifecycleState.RUNNING,
            run_id=run_id,
            active_run_id=run_id,
            execute_requested=execute,
            profile_id=profile_values["profileId"],
        )
        return self.current

    def request_pause(self, run_id):
        self._require_run(run_id)
        self.calls.append(("request_pause", run_id))
        self.current = replace(
            self.current, lifecycle=LifecycleState.PAUSE_REQUESTED
        )
        return self.current

    def resume(self, run_id):
        self._require_run(run_id)
        self.calls.append(("resume", run_id))
        self.current = replace(self.current, lifecycle=LifecycleState.RUNNING)
        return self.current

    def request_safe_stop(self, run_id):
        self._require_run(run_id)
        self.calls.append(("request_safe_stop", run_id))
        self.current = replace(
            self.current, lifecycle=LifecycleState.SAFE_STOP_REQUESTED
        )
        return self.current

    def wait(self, run_id, timeout=None):
        self._require_run(run_id)
        self.calls.append(("wait", run_id, timeout))
        if not self.leave_run_active_on_wait:
            self.current = replace(
                self.current,
                lifecycle=LifecycleState.STOPPED,
                active_run_id=None,
            )
        return self.current

    def begin_demonstration(self, name, **values):
        self.calls.append(("begin_demonstration", name, dict(values)))
        if self.current.active_run_id or self.current.active_capture_id:
            raise RuntimeError("another operation is active")
        self.capture_number += 1
        capture_id = f"demo-{self.capture_number:06d}"
        self.current = _snapshot(
            lifecycle=LifecycleState.DEMONSTRATING,
            capture_id=capture_id,
            active_capture_id=capture_id,
        )
        return self.current

    def end_demonstration(self, capture_id, *, timeout=10.0):
        if capture_id != self.current.active_capture_id:
            raise RuntimeError("stale capture id")
        self.calls.append(("end_demonstration", capture_id, timeout))
        self.current = replace(
            self.current,
            lifecycle=LifecycleState.COMPLETE,
            active_capture_id=None,
        )
        return self.current

    def inspect_demonstration(self, path):
        self.calls.append(("inspect_demonstration", Path(path)))
        return {"valid": True, "path": str(path)}

    def review_demonstration(self, path):
        self.calls.append(("review_demonstration", Path(path)))
        return {
            "valid": True,
            "path": str(path),
            "routeComparison": {"status": "not_compared", "reviewOnly": True},
        }

    def refresh_connection(self):
        self.connection_calls += 1
        call = self.connection_calls
        self.calls.append(("refresh_connection", call))
        if self.connection_gate is not None and call == 1:
            self.connection_entered.set()
            self.connection_gate.wait(2.0)
        if self.connection_error is not None:
            raise self.connection_error
        return {"source": f"refresh-{call}"}

    def launch_or_connect_runelite(self):
        self.calls.append(("launch_or_connect_runelite",))
        return {"source": "launch"}

    def login_or_recover(self):
        self.calls.append(("login_or_recover",))
        return {"source": "login"}

    def set_overlay_enabled(self, enabled):
        self.calls.append(("set_overlay_enabled", enabled))
        self.overlay_enabled = enabled

    def overlay_snapshot(self):
        self.calls.append(("overlay_snapshot",))
        return {"enabled": self.overlay_enabled, "status": "active"}

    def diagnostics(self):
        self.calls.append(("diagnostics",))
        return {"status": "PASS"}

    def run_quick_self_test(self):
        self.calls.append(("run_quick_self_test",))
        return {"status": "PASS"}

    def run_golden_replay(self):
        self.calls.append(("run_golden_replay",))
        return {"status": "PASS", "tests": 2}

    def shutdown_frontend(self, *, timeout=10.0):
        self.calls.append(("shutdown_frontend", timeout))
        self.shutdown_calls += 1

    def _require_run(self, run_id):
        if run_id != self.current.active_run_id:
            raise RuntimeError("stale run id")


def _controller(application, directory):
    application = application or _FakeApplication()
    path = Path(directory.name) / ".osrs-telemetry" / "gui-settings.json"
    controller = GuiController(application, settings_store=GuiSettingsStore(path))
    return controller, application


class GuiSettingsTests(unittest.TestCase):
    def test_round_trip_persists_only_allowlisted_harmless_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".osrs-telemetry" / "gui-settings.json"
            store = GuiSettingsStore(path)
            app = _FakeApplication()
            saved = store.save(
                GuiSettings(
                    "operator_profile",
                    "com12",
                    True,
                    "1200x800-20+40",
                    str(Path(temporary) / "demo_runs"),
                ),
                CATALOG,
                app.validate_profile,
            )

            self.assertEqual("COM12", saved.arduino_port)
            self.assertEqual(saved, store.load(CATALOG, app.validate_profile))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "schema",
                    "profileId",
                    "arduinoPort",
                    "overlayEnabled",
                    "keepTerminalSummaryVisible",
                    "geometry",
                    "lastDemonstrationDirectory",
                },
                set(payload),
            )
            self.assertEqual(SETTINGS_SCHEMA, payload["schema"])
            for unsafe in ("runId", "sessionId", "pid", "cursor", "armed"):
                self.assertNotIn(unsafe, payload)

    def test_malformed_or_stale_values_fall_back_to_catalog_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": SETTINGS_SCHEMA,
                        "profileId": "invalid",
                        "arduinoPort": "serial:///unsafe",
                        "overlayEnabled": "yes",
                        "geometry": "10x10+0+0",
                        "lastDemonstrationDirectory": "bad\x00path",
                    }
                ),
                encoding="utf-8",
            )
            loaded = GuiSettingsStore(path).load(
                CATALOG, _FakeApplication().validate_profile
            )

            self.assertEqual("default_lumbridge_west_trees_v1", loaded.profile_id)
            self.assertEqual("", loaded.arduino_port)
            self.assertFalse(loaded.overlay_enabled)
            self.assertIsNone(loaded.geometry)
            self.assertIsNone(loaded.last_demo_directory)


class GuiControllerTests(unittest.TestCase):
    def _controller(self, application=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return _controller(application or _FakeApplication(), directory)

    def test_controller_source_has_only_frontend_facing_dependencies(self) -> None:
        path = ROOT / "osrs_bot" / "gui_controller.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_modules = {
            "task",
            "safety",
            "action",
            "login",
            "input_coordinator",
            "arduino",
            "serial",
            "demonstration",
            "debug_overlay",
            "pyautogui",
            "pydirectinput",
            "pynput",
        }
        allowed_modules = {
            "__future__",
            "threading",
            "time",
            "collections",
            "collections.abc",
            "dataclasses",
            "datetime",
            "pathlib",
            "queue",
            "application",
            "engine_frame",
            "frontend_presentation",
            "gui_settings",
        }
        offenders = []
        imported_modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                imported_modules.add(name)
                parts = set(name.split("."))
                if parts.intersection(forbidden_modules):
                    offenders.append((node.lineno, name))
        self.assertEqual([], offenders)
        self.assertEqual(set(), imported_modules.difference(allowed_modules))
        for forbidden in (
            "pyautogui",
            "pydirectinput",
            "pynput",
            "_ArduinoHIDTransport",
            ".decide(",
            ".apply_verification(",
            "._connect(",
            "._move_relative(",
            "._mouse_down(",
            "._mouse_up(",
            "._press(",
            "._stop_all(",
            "._disarm(",
        ):
            self.assertNotIn(forbidden, source)

    def test_observe_and_live_use_validated_profile_and_exact_execute_mode(
        self,
    ) -> None:
        observe, observe_app = self._controller()
        values = observe.profile_values("observe_profile")
        observe.start_observe(values)
        observe.wait_for_idle()
        self.assertIn(("start", False, values, None), observe_app.calls)
        self.assertFalse(observe.snapshot().application.execute_requested)

        live, live_app = self._controller()
        live_values = live.profile_values("live_profile")
        live.start_live("COM7", live_values)
        live.wait_for_idle()
        self.assertIn(("set_arduino_port", "COM7"), live_app.calls)
        self.assertIn(("prepare_live_handoff",), live_app.calls)
        self.assertIn(
            (
                "start",
                True,
                live_values,
                Path("_run_proofs/movement_targeting_quality"),
            ),
            live_app.calls,
        )
        self.assertTrue(live.snapshot().application.execute_requested)
        self.assertEqual("COM7", live.snapshot().settings.arduino_port)

    def test_disconnected_observe_is_labeled_but_start_live_is_rejected(self) -> None:
        observe, observe_app = self._controller()
        observe_app.presentation_ready = False
        observe_app.presentation_state = "DISCONNECTED"

        observe.start_observe()
        observe.wait_for_idle()

        self.assertTrue(any(call[0] == "start" for call in observe_app.calls))
        self.assertFalse(observe.snapshot().application.execute_requested)

        live, live_app = self._controller()
        live_app.presentation_ready = False
        live_app.presentation_state = "DISCONNECTED"
        live.start_live("COM6")
        state = live.wait_for_idle()

        self.assertFalse(any(call[0] == "start" for call in live_app.calls))
        self.assertFalse(any(call[0] == "set_arduino_port" for call in live_app.calls))
        self.assertTrue(any("DISCONNECTED" in blocker for blocker in state.blockers))

    def test_invalid_profile_never_reaches_start(self) -> None:
        controller, app = self._controller()
        values = controller.profile_values("invalid")
        valid, diagnostic = controller.validate_profile(values)
        self.assertFalse(valid)
        self.assertEqual("invalid profileId", diagnostic)
        controller.start_live("COM6", values)
        state = controller.wait_for_idle()

        self.assertFalse(any(call[0] == "start" for call in app.calls))
        self.assertTrue(any("invalid profileId" in value for value in state.blockers))
        self.assertIsNone(state.pending_mode)

    def test_pause_resume_and_stop_always_use_current_run_token(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.RUNNING,
                run_id="run-000002",
                active_run_id="run-000002",
            )
        )
        controller, app = self._controller(app)
        controller.request_pause()
        controller.wait_for_idle()
        controller.resume()
        controller.wait_for_idle()
        controller.request_safe_stop()
        controller.wait_for_idle()

        self.assertIn(("request_pause", "run-000002"), app.calls)
        self.assertIn(("resume", "run-000002"), app.calls)
        self.assertIn(("prepare_live_handoff",), app.calls)
        self.assertIn(("request_safe_stop", "run-000002"), app.calls)
        with self.assertRaisesRegex(Exception, "no active demonstration"):
            controller.stop_demonstration()

    def test_resume_refuses_a_changed_runelite_pid_or_session(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.PAUSED,
                run_id="run-000002",
                active_run_id="run-000002",
                execute_requested=True,
            )
        )
        app.expected_process_id = 1234
        app.expected_session_id = "session-a"
        app.current_process_id = 4321
        app.current_session_id = "session-b"
        controller, app = self._controller(app)

        controller.resume()
        state = controller.wait_for_idle()

        self.assertFalse(any(call[0] == "resume" for call in app.calls))
        self.assertTrue(any("PID/session changed" in value for value in state.blockers))

    def test_starting_run_and_demonstration_are_mutually_exclusive(self) -> None:
        app = _FakeApplication()
        gate = threading.Event()
        entered = threading.Event()
        original = app.start

        def slow_start(**values):
            entered.set()
            gate.wait(2.0)
            return original(**values)

        app.start = slow_start
        controller, _app = self._controller(app)
        try:
            controller.start_observe()
            self.assertTrue(entered.wait(1.0))
            with self.assertRaises(GuiControllerBusyError):
                controller.start_demonstration("blocked")
        finally:
            gate.set()
            controller.wait_for_idle()

    def test_async_worker_does_not_block_caller(self) -> None:
        app = _FakeApplication()
        app.connection_gate = threading.Event()
        controller, _app = self._controller(app)
        started = time.monotonic()
        try:
            controller.refresh_connection()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.25)
            self.assertTrue(app.connection_entered.wait(1.0))
            self.assertIn("connection", controller.snapshot().busy_operations)
        finally:
            app.connection_gate.set()
            controller.wait_for_idle()

    def test_replaced_operation_ignores_stale_result(self) -> None:
        app = _FakeApplication()
        app.connection_gate = threading.Event()
        controller, _app = self._controller(app)
        try:
            controller.refresh_connection()
            self.assertTrue(app.connection_entered.wait(1.0))
            controller.refresh_connection()
            time.sleep(0.02)
            controller.drain_results()
            self.assertEqual(
                {"source": "refresh-2"},
                controller.snapshot().result("connection"),
            )
        finally:
            app.connection_gate.set()
            controller.wait_for_idle()
        self.assertEqual(
            {"source": "refresh-2"}, controller.snapshot().result("connection")
        )

    def test_explicit_launch_supersedes_background_connection_refresh(self) -> None:
        app = _FakeApplication()
        app.connection_gate = threading.Event()
        controller, _app = self._controller(app)
        try:
            controller.refresh_connection()
            self.assertTrue(app.connection_entered.wait(1.0))
            self.assertEqual(
                "refresh-connection",
                dict(controller.snapshot().busy_operation_details)["connection"],
            )

            controller.launch_or_connect_runelite()
            time.sleep(0.02)
            controller.drain_results()
            self.assertEqual(
                {"source": "launch"},
                controller.snapshot().result("connection"),
            )
        finally:
            app.connection_gate.set()
            controller.wait_for_idle()

        self.assertEqual(
            {"source": "launch"}, controller.snapshot().result("connection")
        )

    def test_engine_frame_updates_are_latest_only(self) -> None:
        app = _FakeApplication(_snapshot(frame=_frame(2)))
        controller, _app = self._controller(app)
        self.assertEqual(2, controller.snapshot().engine_frame.sequence)

        app.current = _snapshot(frame=_frame(1))
        controller.request_refresh()
        controller.wait_for_idle()
        self.assertEqual(2, controller.snapshot().engine_frame.sequence)

        app.current = _snapshot(frame=_frame(3, state="navigate_to_bank"))
        controller.request_refresh()
        controller.wait_for_idle()
        self.assertEqual(3, controller.snapshot().engine_frame.sequence)

    def test_delayed_old_run_snapshot_cannot_replace_new_run_frame(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.RUNNING,
                run_id="run-000002",
                active_run_id="run-000002",
                frame=_frame(1, state="new-run"),
            )
        )
        controller, _app = self._controller(app)
        delayed = _snapshot(
            lifecycle=LifecycleState.COMPLETE,
            run_id="run-000001",
            frame=_frame(9, state="old-run"),
        )

        with controller._lock:
            controller._apply_application_snapshot_unlocked(delayed, announce=True)

        state = controller.snapshot()
        self.assertEqual("run-000002", state.application.run_id)
        self.assertEqual("run-000002", state.engine_frame_run_id)
        self.assertEqual(1, state.engine_frame.sequence)
        self.assertEqual("new-run", state.engine_frame.task.state)

    def test_new_run_without_frame_clears_frame_and_presentation_debounce(self) -> None:
        run_one = _snapshot(
            lifecycle=LifecycleState.RUNNING,
            run_id="run-000001",
            active_run_id="run-000001",
            frame=_frame(2, state="old-run"),
        )
        app = _FakeApplication(run_one)
        controller, _app = self._controller(app)
        with controller._lock:
            controller._presentation_hysteresis = PresentationHysteresisState(
                run_id="run-000001",
                displayed_state=PresentationState.READY,
                pending_state=PresentationState.SENSOR_STALE,
                pending_since_monotonic=1.0,
            )

        run_two = _snapshot(
            lifecycle=LifecycleState.RUNNING,
            run_id="run-000002",
            active_run_id="run-000002",
            frame=None,
        )
        app.current = run_two
        with controller._lock:
            controller._apply_application_snapshot_unlocked(run_two, announce=True)

        state = controller.snapshot()
        self.assertIsNone(state.engine_frame)
        self.assertIsNone(state.engine_frame_run_id)
        self.assertEqual("run-000002", controller._presentation_hysteresis.run_id)
        self.assertIsNone(controller._presentation_hysteresis.pending_state)

        delayed = replace(run_one, lifecycle=LifecycleState.COMPLETE)
        with controller._lock:
            controller._apply_application_snapshot_unlocked(delayed, announce=True)
        self.assertIsNone(controller.snapshot().engine_frame)

    def test_timestamped_arduino_health_is_exact_but_display_only_debounced(
        self,
    ) -> None:
        app = _FakeApplication()
        app.presentation_override = _ready_presentation()
        monotonic = [10.0]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = GuiSettingsStore(
            Path(directory.name) / ".osrs-telemetry" / "gui-settings.json"
        )
        controller = GuiController(
            app,
            settings_store=store,
            presentation_clock=lambda: monotonic[0],
            presentation_wall_clock=lambda: NOW + timedelta(seconds=3),
        )
        self.assertIs(controller.snapshot().presentation.state, PresentationState.READY)
        with controller._lock:
            controller._results["arduinoReadiness"] = ArduinoReadiness(
                "COM6",
                ("COM6",),
                True,
                "available",
                True,
                None,
                None,
                "lease available",
                captured_at=NOW,
                max_age_millis=2_000,
            )

        monotonic[0] = 10.1
        held = controller.snapshot().presentation
        self.assertIs(held.state, PresentationState.ARDUINO_HEALTH_STALE)
        self.assertIs(held.display_state, PresentationState.READY)
        self.assertEqual(3_000, held.wait_elapsed_millis)
        self.assertTrue(held.start_live_allowed)

        monotonic[0] = 10.6
        expired = controller.snapshot().presentation
        self.assertIs(
            expired.display_state, PresentationState.ARDUINO_HEALTH_STALE
        )

    def test_delayed_same_run_active_snapshot_cannot_regress_terminal_state(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.COMPLETE,
                run_id="run-000002",
                frame=_frame(4, state="terminal"),
            )
        )
        controller, _app = self._controller(app)
        delayed = _snapshot(
            lifecycle=LifecycleState.RUNNING,
            run_id="run-000002",
            active_run_id="run-000002",
            frame=_frame(3, state="old-active"),
        )

        with controller._lock:
            controller._apply_application_snapshot_unlocked(delayed, announce=True)

        state = controller.snapshot()
        self.assertIs(state.application.lifecycle, LifecycleState.COMPLETE)
        self.assertIsNone(state.application.active_run_id)
        self.assertEqual(4, state.engine_frame.sequence)

    def test_equal_sequence_delayed_snapshot_cannot_regress_pause_state(self) -> None:
        frame = _frame(2)
        running = _snapshot(
            lifecycle=LifecycleState.RUNNING,
            run_id="run-000001",
            active_run_id="run-000001",
            frame=frame,
        )
        paused = replace(running, lifecycle=LifecycleState.PAUSED)
        app = _FakeApplication(running)
        controller, _app = self._controller(app)

        app.current = paused
        with controller._lock:
            controller._apply_application_snapshot_unlocked(running, announce=True)

        self.assertIs(
            controller.snapshot().application.lifecycle,
            LifecycleState.PAUSED,
        )

    def test_clear_historical_display_does_not_restore_same_frame(self) -> None:
        terminal = _snapshot(
            lifecycle=LifecycleState.COMPLETE,
            run_id="run-000001",
            frame=_frame(3, state="complete"),
        )
        app = _FakeApplication(terminal)
        controller, _app = self._controller(app)

        retained = controller.snapshot().terminal_summary_evidence
        self.assertIsNotNone(retained)

        controller.clear_historical_display()
        controller.request_refresh()
        controller.wait_for_idle()

        state = controller.snapshot()
        self.assertIsNone(state.engine_frame)
        self.assertEqual(retained, state.terminal_summary_evidence)

    def test_important_event_history_is_bounded(self) -> None:
        controller, _app = self._controller()
        for index in range(MAX_EVENT_HISTORY + 25):
            controller.record_event("test", f"event {index}")
        events = controller.snapshot().events
        self.assertEqual(MAX_EVENT_HISTORY, len(events))
        self.assertEqual("event 324", events[-1].message)
        self.assertGreater(events[0].sequence, 1)

    def test_worker_exception_becomes_visible_blocker(self) -> None:
        app = _FakeApplication()
        app.connection_error = RuntimeError("endpoint failed exactly")
        controller, _app = self._controller(app)
        controller.refresh_connection()
        state = controller.wait_for_idle()

        self.assertIn(
            "RuntimeError: endpoint failed exactly", state.blockers
        )
        self.assertTrue(any(event.status == "BLOCKED" for event in state.events))

    def test_overlay_readiness_diagnostics_and_replay_delegate_to_facade(self) -> None:
        controller, app = self._controller()
        controller.set_overlay_enabled(True)
        controller.request_arduino_readiness("COM9")
        controller.request_diagnostics()
        controller.run_quick_self_test()
        controller.run_golden_replay()
        state = controller.wait_for_idle()

        self.assertEqual({"enabled": True, "status": "active"}, state.result("overlay"))
        self.assertEqual("COM9", state.result("arduinoReadiness")["port"])
        self.assertEqual("PASS", state.result("diagnostics")["status"])
        self.assertEqual("PASS", state.result("quickSelfTest")["status"])
        self.assertEqual(2, state.result("goldenReplay")["tests"])
        self.assertTrue(state.settings.overlay_enabled)
        self.assertEqual(
            {"maxActions": 100, "maxRuntimeSeconds": 900},
            controller.runtime_configuration(),
        )
        self.assertIn(("arduino_readiness", "COM9"), app.calls)

    def test_startup_preferences_are_reapplied_only_through_facade(self) -> None:
        controller, app = self._controller()
        controller.save_preferences(arduino_port="COM4", overlay_enabled=True)
        controller.apply_startup_preferences()
        state = controller.wait_for_idle()

        self.assertIn(("set_arduino_port", "COM4"), app.calls)
        self.assertIn(("set_overlay_enabled", True), app.calls)
        self.assertEqual(
            "COM4", state.result("startupPreferences")["arduinoPort"]
        )

    def test_login_reapplies_the_selected_arduino_port_before_recovery(self) -> None:
        controller, app = self._controller()
        controller.save_preferences(arduino_port="COM5")
        controller.login_or_recover()
        state = controller.wait_for_idle()

        set_index = app.calls.index(("set_arduino_port", "COM5"))
        login_index = app.calls.index(("login_or_recover",))
        refresh_index = next(
            index
            for index, call in enumerate(app.calls)
            if call[0] == "refresh_connection"
        )
        self.assertLess(set_index, login_index)
        self.assertLess(login_index, refresh_index)
        self.assertEqual(
            {"source": "login"}, state.result("connection")["recovery"]
        )
        self.assertEqual(
            {"source": "refresh-1"}, state.result("connection")["connection"]
        )

    def test_demonstration_uses_current_capture_and_application_owned_review(self) -> None:
        controller, app = self._controller()
        controller.start_demonstration("short-route", duration_seconds=3.0)
        started = controller.wait_for_idle()
        capture_id = started.application.active_capture_id
        self.assertIsNotNone(capture_id)
        controller.stop_demonstration(timeout=2.0)
        controller.wait_for_idle()
        controller.inspect_demonstration(Path("demo_runs") / "artifact")
        state = controller.wait_for_idle()

        self.assertIn(("end_demonstration", capture_id, 2.0), app.calls)
        self.assertEqual(True, state.result("demonstrationInspection")["valid"])
        self.assertTrue(
            state.result("demonstrationInspection")["routeComparison"]["reviewOnly"]
        )
        self.assertIn(
            ("review_demonstration", Path("demo_runs") / "artifact"), app.calls
        )

    def test_starting_demonstration_clears_prior_run_engine_frame(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.STOPPED,
                run_id="run-000001",
                frame=_frame(3),
            )
        )
        controller, _app = self._controller(app)
        self.assertEqual(3, controller.snapshot().engine_frame.sequence)

        controller.start_demonstration("after-run", duration_seconds=3.0)
        state = controller.wait_for_idle()

        self.assertIsNone(state.application.run_id)
        self.assertIsNone(state.application.engine_frame)
        self.assertIsNone(state.engine_frame)

    def test_close_active_run_requests_safe_stop_waits_then_shuts_down(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.RUNNING,
                run_id="run-000007",
                active_run_id="run-000007",
            )
        )
        controller, app = self._controller(app)
        controller.request_close(timeout=0.5)
        state = controller.wait_for_idle()

        self.assertIn(("request_safe_stop", "run-000007"), app.calls)
        wait_call = next(call for call in app.calls if call[0] == "wait")
        self.assertEqual(("wait", "run-000007"), wait_call[:2])
        self.assertGreater(wait_call[2], 0.0)
        self.assertLessEqual(wait_call[2], 0.5)
        self.assertEqual(1, app.shutdown_calls)
        self.assertTrue(state.close_ready)
        self.assertFalse(state.close_terminal_failure)
        self.assertIs(state.application.lifecycle, LifecycleState.STOPPED)
        self.assertTrue(controller.join_workers(0.1))
        with self.assertRaises(GuiControllerClosedError):
            controller.refresh_connection()

    def test_close_timeout_never_kills_or_calls_frontend_shutdown(self) -> None:
        app = _FakeApplication(
            _snapshot(
                lifecycle=LifecycleState.RUNNING,
                run_id="run-000008",
                active_run_id="run-000008",
            )
        )
        app.leave_run_active_on_wait = True
        controller, app = self._controller(app)
        controller.request_close(timeout=0.01)
        state = controller.wait_for_idle()

        self.assertFalse(state.close_ready)
        self.assertEqual(0, app.shutdown_calls)
        self.assertTrue(
            any("Safe Stop is still awaiting" in value for value in state.blockers)
        )
        self.assertEqual("run-000008", state.application.active_run_id)

    def test_close_waits_for_pending_start_then_stops_the_created_run(self) -> None:
        app = _FakeApplication()
        gate = threading.Event()
        entered = threading.Event()
        original = app.start

        def slow_start(**values):
            entered.set()
            gate.wait(2.0)
            return original(**values)

        app.start = slow_start
        controller, app = self._controller(app)
        controller.start_live("COM6")
        self.assertTrue(entered.wait(1.0))
        controller.request_close(timeout=1.0)
        gate.set()
        state = controller.wait_for_idle(2.0)

        self.assertTrue(state.close_ready)
        self.assertIn(("request_safe_stop", "run-000001"), app.calls)
        self.assertEqual(1, app.shutdown_calls)
        self.assertTrue(controller.join_workers(0.1))


if __name__ == "__main__":
    unittest.main()
