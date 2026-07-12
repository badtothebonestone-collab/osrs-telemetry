from __future__ import annotations

import inspect
import ast
import unittest
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

from osrs_bot.application import LifecycleState
from osrs_bot.engine_frame import (
    CleanupEvidence,
    EngineFrame,
    EngineStage,
    ObservationReference,
)
from osrs_bot.frontend_presentation import (
    PresentationState,
    classify_frontend_presentation,
)
from osrs_bot.model import ScreenBounds
from osrs_bot.operator_services import (
    ConnectionSnapshot,
    ConnectionState,
)
from osrs_bot.task_contract import TaskSnapshot, TaskStatus
from osrs_bot.verification import (
    Outcome,
    OutcomeKind,
    VerificationResult,
    VerificationStatus,
)


NOW = datetime(2026, 7, 12, 18, 0, 0, tzinfo=timezone.utc)
MAX_SOURCE_AGE_MILLIS = 2_000


def _observation(
    *,
    captured_at: datetime = NOW,
    process_id: int = 1234,
    session_id: str = "session-a",
    source_tick: int = 50,
) -> ObservationReference:
    values = {
        "source_tick": source_tick,
        "captured_at": captured_at,
        "frame_id": f"{session_id}:{source_tick}",
        "geometry_frame_id": f"geometry-{source_tick}",
        "session_id": session_id,
        "process_id": process_id,
        "canvas_bounds": ScreenBounds(0, 0, 765, 503),
        "game_state": "LOGGED_IN",
        "loaded_scene": True,
        "fresh": True,
        "cache_wall_clock_fresh": True,
        "source_coherent": True,
    }
    if "max_source_age_millis" in inspect.signature(ObservationReference).parameters:
        values["max_source_age_millis"] = MAX_SOURCE_AGE_MILLIS
    return ObservationReference(**values)


def _connection(
    *,
    captured_at: datetime = NOW,
    endpoint_healthy: bool = True,
    runelite_found: bool = True,
    process_id: int | None = 1234,
    session_id: str | None = "session-a",
    exact_process_binding: bool = True,
    loaded_scene: bool = True,
    coherent_fresh_observation: bool = True,
    blocker: str | None = None,
    diagnostic: str | None = None,
    source_tick: int = 50,
):
    values = {
        "state": (
            ConnectionState.CONNECTED
            if endpoint_healthy and exact_process_binding
            else ConnectionState.NOT_FOUND
        ),
        "captured_at": captured_at,
        "endpoint_healthy": endpoint_healthy,
        "runelite_found": runelite_found,
        "process_id": process_id,
        "session_id": session_id,
        "exact_process_binding": exact_process_binding,
        "loaded_scene": loaded_scene,
        "game_state": "LOGGED_IN" if loaded_scene else "LOGIN_SCREEN",
        "foreground": True,
        "coherent_fresh_observation": coherent_fresh_observation,
        "cursor_inside_client": True,
        "layout_supported": True,
        "canvas_bounds": ScreenBounds(0, 0, 765, 503),
        "client_bounds": ScreenBounds(0, 0, 765, 503),
        "blocker": blocker,
        "diagnostic": diagnostic,
    }
    parameters = inspect.signature(ConnectionSnapshot).parameters
    if "max_source_age_millis" in parameters:
        values["max_source_age_millis"] = MAX_SOURCE_AGE_MILLIS
    if "source_captured_at" in parameters:
        values["source_captured_at"] = captured_at
    if "source_tick" in parameters:
        values["source_tick"] = source_tick
    snapshot = ConnectionSnapshot(**values)
    if hasattr(snapshot, "max_source_age_millis"):
        return snapshot
    # Compatibility until the authoritative age contract lands on the shared
    # ConnectionSnapshot in the parent change.
    copied = {field.name: getattr(snapshot, field.name) for field in fields(snapshot)}
    copied["max_source_age_millis"] = MAX_SOURCE_AGE_MILLIS
    copied["source_captured_at"] = captured_at
    return SimpleNamespace(**copied)


def _frame(
    *,
    captured_at: datetime = NOW,
    process_id: int = 1234,
    session_id: str = "session-a",
    source_tick: int = 50,
    status: TaskStatus = TaskStatus.RUNNING,
    blocker: str | None = None,
    terminal_outcome: bool = False,
    safe_cleanup: bool = False,
) -> EngineFrame:
    verification = (
        VerificationResult(
            VerificationStatus.PASS,
            "arrived",
            Outcome(OutcomeKind.ARRIVED, source_tick),
        )
        if terminal_outcome
        else None
    )
    cleanup = CleanupEvidence(
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
        safe_cleanup,
    )
    return EngineFrame(
        sequence=7,
        published_at=captured_at,
        stage=(
            EngineStage.TERMINAL
            if status in {TaskStatus.COMPLETE, TaskStatus.BLOCKED}
            else EngineStage.DECIDED
        ),
        task=TaskSnapshot(
            "woodcut_bank",
            status,
            "complete" if status is TaskStatus.COMPLETE else "find_tree",
            blocker=blocker,
        ),
        observation=_observation(
            captured_at=captured_at,
            process_id=process_id,
            session_id=session_id,
            source_tick=source_tick,
        ),
        last_verification=verification,
        cleanup=cleanup,
        blocker=blocker,
    )


def _classify(**changes):
    values = {
        "lifecycle": LifecycleState.RUNNING,
        "run_id": "run-000001",
        "frame_run_id": "run-000001",
        "expected_process_id": 1234,
        "expected_session_id": "session-a",
        "execute_requested": False,
        "frame": _frame(),
        "connection": _connection(),
        "runtime_status": None,
        "runtime_reason": None,
        "blockers": (),
        "now": NOW + timedelta(milliseconds=500),
    }
    values.update(changes)
    return classify_frontend_presentation(**values)


class FrontendPresentationTests(unittest.TestCase):
    def test_presentation_adds_no_endpoint_worker_or_control_owner(self) -> None:
        path = Path(__file__).resolve().parents[1] / "osrs_bot" / "frontend_presentation.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        self.assertNotIn("observation", imports)
        self.assertNotIn("input_coordinator", imports)
        for forbidden in (
            "ObservationClient",
            "http://",
            "https://",
            "threading",
            "subprocess",
            "SafetyGate",
            "Arduino",
        ):
            self.assertNotIn(forbidden, source)

    def test_state_enum_is_the_bounded_frontend_vocabulary(self) -> None:
        self.assertEqual(
            {
                "CONNECTING",
                "READY",
                "OBSERVING",
                "RUNNING",
                "PAUSED",
                "COMPLETE",
                "BLOCKED",
                "SAFE_STOPPED",
                "DISCONNECTED",
                "STALE",
                "ERROR",
            },
            {state.value for state in PresentationState},
        )

    def test_fresh_current_frame_retains_source_truth_and_geometry(self) -> None:
        presentation = _classify()

        self.assertIs(presentation.state, PresentationState.OBSERVING)
        self.assertIs(presentation.lifecycle, LifecycleState.RUNNING)
        self.assertEqual("run-000001", presentation.run_id)
        self.assertEqual(1234, presentation.expected_process_id)
        self.assertEqual("session-a", presentation.expected_session_id)
        self.assertEqual("decided", presentation.source_frame_stage)
        self.assertEqual("running", presentation.source_frame_status)
        self.assertEqual(50, presentation.source_tick)
        self.assertEqual(1234, presentation.process_id)
        self.assertEqual("session-a", presentation.session_id)
        self.assertEqual(0.5, presentation.age_seconds)
        self.assertEqual(MAX_SOURCE_AGE_MILLIS, presentation.max_source_age_millis)
        self.assertTrue(presentation.current)
        self.assertFalse(presentation.historical)
        self.assertTrue(presentation.geometry_allowed)
        self.assertFalse(presentation.start_live_allowed)

    def test_active_live_paused_and_idle_ready_are_distinct(self) -> None:
        running = _classify(execute_requested=True)
        paused = _classify(
            lifecycle=LifecycleState.PAUSED, execute_requested=True
        )
        ready = _classify(
            lifecycle=LifecycleState.IDLE,
            run_id=None,
            frame_run_id=None,
            frame=None,
        )

        self.assertIs(running.state, PresentationState.RUNNING)
        self.assertTrue(running.geometry_allowed)
        self.assertIs(paused.state, PresentationState.PAUSED)
        self.assertTrue(paused.current)
        self.assertFalse(paused.geometry_allowed)
        self.assertIs(ready.state, PresentationState.READY)
        self.assertEqual(50, ready.source_tick)
        self.assertTrue(ready.start_live_allowed)

    def test_frame_becomes_stale_without_a_new_response(self) -> None:
        presentation = _classify(now=NOW + timedelta(milliseconds=2001))

        self.assertIs(presentation.state, PresentationState.STALE)
        self.assertAlmostEqual(2.001, presentation.age_seconds)
        self.assertEqual(50, presentation.source_tick)
        self.assertFalse(presentation.current)
        self.assertTrue(presentation.historical)
        self.assertFalse(presentation.geometry_allowed)
        self.assertFalse(presentation.start_live_allowed)
        self.assertIn("fresh coherent", presentation.reconnect_guidance)

    def test_endpoint_or_pid_disappearance_is_disconnected(self) -> None:
        cases = (
            _connection(
                endpoint_healthy=False,
                blocker="snapshot_endpoint_unavailable",
                diagnostic="connection refused",
            ),
            _connection(
                runelite_found=False,
                process_id=None,
                session_id=None,
                exact_process_binding=False,
                blocker="runelite_process_not_found",
            ),
        )
        for connection in cases:
            with self.subTest(blocker=connection.blocker):
                presentation = _classify(connection=connection)
                self.assertIs(presentation.state, PresentationState.DISCONNECTED)
                self.assertTrue(presentation.historical)
                self.assertFalse(presentation.geometry_allowed)
                self.assertFalse(presentation.start_live_allowed)
                self.assertIn(connection.blocker, presentation.blockers)
                self.assertIn("PID/session", presentation.reconnect_guidance)

    def test_active_run_world_disconnect_is_disconnected_not_ready(self) -> None:
        presentation = _classify(
            connection=_connection(
                loaded_scene=False,
                coherent_fresh_observation=False,
                blocker="loaded_scene_not_ready",
            )
        )

        self.assertIs(presentation.state, PresentationState.DISCONNECTED)
        self.assertTrue(presentation.historical)
        self.assertFalse(presentation.geometry_allowed)
        self.assertFalse(presentation.start_live_allowed)

    def test_world_disconnect_overrides_historical_terminal_badge(self) -> None:
        presentation = _classify(
            lifecycle=LifecycleState.COMPLETE,
            runtime_status="COMPLETE",
            runtime_reason="cycle complete",
            frame=_frame(status=TaskStatus.COMPLETE, safe_cleanup=True),
            connection=_connection(
                loaded_scene=False,
                coherent_fresh_observation=False,
                blocker="loaded_scene_not_ready",
            ),
        )

        self.assertIs(presentation.state, PresentationState.DISCONNECTED)
        self.assertTrue(presentation.terminal_summary)
        self.assertTrue(presentation.cleanup.safe)
        self.assertFalse(presentation.geometry_allowed)

    def test_pid_or_session_change_invalidates_the_old_frame(self) -> None:
        for process_id, session_id in ((4321, "session-a"), (1234, "session-b")):
            with self.subTest(process_id=process_id, session_id=session_id):
                presentation = _classify(
                    connection=_connection(
                        process_id=process_id,
                        session_id=session_id,
                    )
                )
                self.assertIs(presentation.state, PresentationState.DISCONNECTED)
                self.assertEqual(1234, presentation.source_process_id)
                self.assertEqual("session-a", presentation.source_session_id)
                self.assertEqual(process_id, presentation.process_id)
                self.assertEqual(session_id, presentation.session_id)
                self.assertTrue(presentation.historical)
                self.assertFalse(presentation.geometry_allowed)

    def test_application_run_change_makes_delayed_old_frame_historical(self) -> None:
        presentation = _classify(
            run_id="run-000002",
            frame_run_id="run-000001",
        )

        self.assertIs(presentation.state, PresentationState.STALE)
        self.assertEqual("run-000002", presentation.run_id)
        self.assertEqual("run-000001", presentation.frame_run_id)
        self.assertTrue(presentation.historical)
        self.assertFalse(presentation.geometry_allowed)

    def test_active_run_never_automatically_resumes_under_a_new_identity(self) -> None:
        presentation = _classify(
            frame=_frame(process_id=4321, session_id="session-b", source_tick=51),
            connection=_connection(
                process_id=4321,
                session_id="session-b",
                source_tick=88,
            ),
            expected_process_id=1234,
            expected_session_id="session-a",
        )

        self.assertIs(presentation.state, PresentationState.DISCONNECTED)
        self.assertEqual(4321, presentation.process_id)
        self.assertEqual("session-b", presentation.session_id)
        self.assertEqual(1234, presentation.expected_process_id)
        self.assertEqual("session-a", presentation.expected_session_id)
        self.assertTrue(presentation.historical)
        self.assertFalse(presentation.geometry_allowed)
        self.assertFalse(presentation.start_live_allowed)

    def test_terminal_states_retain_reason_outcome_and_cleanup_without_geometry(self) -> None:
        complete_frame = _frame(
            status=TaskStatus.COMPLETE,
            terminal_outcome=True,
            safe_cleanup=True,
        )
        complete = _classify(
            lifecycle=LifecycleState.COMPLETE,
            frame=complete_frame,
            runtime_status="COMPLETE",
            runtime_reason="cycle goal reached",
        )
        blocked = _classify(
            lifecycle=LifecycleState.BLOCKED,
            frame=_frame(
                status=TaskStatus.BLOCKED,
                blocker="verification_session_changed",
                safe_cleanup=True,
            ),
            runtime_status="BLOCKED",
            runtime_reason="verification_session_changed",
            blockers=("verification_session_changed",),
        )
        stopped = _classify(
            lifecycle=LifecycleState.STOPPED,
            frame=_frame(safe_cleanup=True),
            runtime_status="SAFE_STOPPED",
            runtime_reason="operator requested safe stop",
        )

        self.assertIs(complete.state, PresentationState.COMPLETE)
        self.assertEqual("cycle goal reached", complete.terminal_reason)
        self.assertIsInstance(complete.terminal_outcome, Outcome)
        self.assertTrue(complete.cleanup.safe)
        self.assertTrue(complete.terminal_summary)
        self.assertFalse(complete.geometry_allowed)

        self.assertIs(blocked.state, PresentationState.BLOCKED)
        self.assertEqual(
            "verification_session_changed", blocked.terminal_reason
        )
        self.assertIn("verification_session_changed", blocked.blockers)
        self.assertTrue(blocked.cleanup.safe)
        self.assertFalse(blocked.geometry_allowed)

        self.assertIs(stopped.state, PresentationState.SAFE_STOPPED)
        self.assertEqual(
            "operator requested safe stop", stopped.terminal_reason
        )
        self.assertTrue(stopped.cleanup.safe)
        self.assertFalse(stopped.geometry_allowed)

    def test_reconnect_requires_new_identity_and_fresh_coherent_observation(self) -> None:
        new_connection = _connection(process_id=4321, session_id="session-b")
        old_frame = _classify(connection=new_connection)
        incoherent = _classify(
            run_id="run-000002",
            frame_run_id="run-000002",
            expected_process_id=4321,
            expected_session_id="session-b",
            frame=_frame(process_id=4321, session_id="session-b", source_tick=51),
            connection=_connection(
                process_id=4321,
                session_id="session-b",
                coherent_fresh_observation=False,
                blocker="source_not_coherent",
            ),
        )
        ready = _classify(
            lifecycle=LifecycleState.IDLE,
            run_id="run-000002",
            frame_run_id="run-000002",
            expected_process_id=4321,
            expected_session_id="session-b",
            frame=_frame(process_id=4321, session_id="session-b", source_tick=52),
            connection=new_connection,
        )

        self.assertIs(old_frame.state, PresentationState.DISCONNECTED)
        self.assertFalse(old_frame.start_live_allowed)
        self.assertIs(incoherent.state, PresentationState.STALE)
        self.assertFalse(incoherent.start_live_allowed)
        self.assertIs(ready.state, PresentationState.READY)
        self.assertEqual(4321, ready.source_process_id)
        self.assertEqual("session-b", ready.source_session_id)
        self.assertTrue(ready.start_live_allowed)
        self.assertFalse(ready.geometry_allowed)

    def test_new_connection_state_overrides_an_old_terminal_frame(self) -> None:
        old_terminal = _frame(
            captured_at=NOW - timedelta(seconds=10),
            status=TaskStatus.COMPLETE,
            terminal_outcome=True,
            safe_cleanup=True,
        )
        presentation = _classify(
            lifecycle=LifecycleState.COMPLETE,
            frame=old_terminal,
            connection=_connection(
                process_id=4321,
                session_id="session-b",
                source_tick=88,
            ),
            runtime_status="COMPLETE",
            runtime_reason="old terminal result",
        )

        self.assertIs(presentation.state, PresentationState.READY)
        self.assertTrue(presentation.historical)
        self.assertTrue(presentation.terminal_summary)
        self.assertTrue(presentation.cleanup.safe)
        self.assertEqual(50, presentation.source_tick)
        self.assertEqual(88, presentation.connection_source_tick)
        self.assertEqual(0.5, presentation.connection_age_seconds)
        self.assertFalse(presentation.geometry_allowed)
        self.assertTrue(presentation.start_live_allowed)

    def test_error_retains_original_diagnostic_and_recovery_guidance(self) -> None:
        presentation = _classify(
            frame=None,
            frame_run_id=None,
            runtime_status="ERROR",
            runtime_reason="frontend refresh failed",
            blockers=("frontend_refresh_failed",),
            connection=_connection(diagnostic="exact diagnostic text"),
        )

        self.assertIs(presentation.state, PresentationState.ERROR)
        self.assertEqual("exact diagnostic text", presentation.diagnostic)
        self.assertIn("frontend_refresh_failed", presentation.blockers)
        self.assertIn("diagnostic", presentation.reconnect_guidance)

    def test_terminal_error_becomes_ready_after_fresh_reconnect(self) -> None:
        presentation = _classify(
            lifecycle=LifecycleState.ERROR,
            runtime_status="ERROR",
            runtime_reason="old run failed",
            connection=_connection(process_id=4321, session_id="session-b"),
        )

        self.assertIs(presentation.state, PresentationState.READY)
        self.assertTrue(presentation.terminal_summary)
        self.assertEqual("old run failed", presentation.terminal_reason)
        self.assertTrue(presentation.start_live_allowed)
        self.assertFalse(presentation.geometry_allowed)

    def test_failed_probe_has_no_fresh_connection_source_timestamp(self) -> None:
        failed = replace(
            _connection(
                endpoint_healthy=False,
                runelite_found=False,
                process_id=None,
                session_id=None,
                exact_process_binding=False,
                loaded_scene=False,
                coherent_fresh_observation=False,
                blocker="snapshot_endpoint_unavailable",
            ),
            source_tick=None,
            source_captured_at=None,
        )
        presentation = _classify(connection=failed)

        self.assertIs(presentation.state, PresentationState.DISCONNECTED)
        self.assertIsNone(presentation.connection_source_tick)
        self.assertIsNone(presentation.connection_last_updated_at)
        self.assertIsNone(presentation.connection_age_seconds)


if __name__ == "__main__":
    unittest.main()
