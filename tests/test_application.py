from __future__ import annotations

import ast
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from osrs_bot.application import (
    ApplicationError,
    EngineApplication,
    LifecycleState,
    SUPPORTED_TASK_ID,
)
from osrs_bot.definition import LUMBRIDGE_WEST_TREES_V1
from osrs_bot.demonstration import InspectionResult
from osrs_bot.model import (
    Action,
    ActionKind,
    InventoryObservation,
    NearbyObject,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
)
from osrs_bot.observation import parse_observation
from osrs_bot.profile import DEFAULT_PROFILE
from osrs_bot.runtime import RuntimeControl, TaskRuntime
from osrs_bot.task_contract import Decision, ObservationRequest, TaskSnapshot, TaskStatus


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "snapshot_loaded.json"


def _observation(tick: int):
    base = parse_observation(json.loads(FIXTURE.read_text(encoding="utf-8")))
    now = datetime.now(timezone.utc)
    return replace(
        base,
        tick=tick,
        timestamp=now,
        assembled_at=now,
        frame_id=f"app-frame-{tick}",
        geometry_frame_id=f"app-frame-{tick}",
        menu_source_tick=tick,
        menu_timestamp=now,
    )


class _LoopClient:
    def __init__(self) -> None:
        self.tick = 0

    def fetch(self, _tiles):
        self.tick += 1
        return _observation(self.tick)


class _TreeReadyClient:
    def __init__(self) -> None:
        self.tick = 0

    def fetch(self, _tiles):
        self.tick += 1
        observation = _observation(self.tick)
        anchor = LUMBRIDGE_WEST_TREES_V1.resource.work_area.anchor
        tree_id = next(iter(LUMBRIDGE_WEST_TREES_V1.resource.selector.object_ids))
        geometry = TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            canvas_point=ScreenPoint(300, 200),
            screen_point=ScreenPoint(400, 300),
            screen_bounds=ScreenBounds(380, 280, 40, 40),
            visible_area_ratio=1.0,
        )
        tree = NearbyObject(
            key=f"application:tree:{tree_id}",
            object_id=tree_id,
            name=LUMBRIDGE_WEST_TREES_V1.resource.selector.name,
            kind="GAME_OBJECT",
            actions=(LUMBRIDGE_WEST_TREES_V1.resource.selector.action,),
            location=anchor,
            distance=0,
            geometry=geometry,
            scene_x=50,
            scene_y=50,
        )
        return replace(
            observation,
            location=anchor,
            plane=anchor.plane,
            inventory=InventoryObservation((), 28, 0, 28, True),
            nearby_objects=(tree,),
        )


class _JoinGate:
    def __init__(self, worker: threading.Thread) -> None:
        self.worker = worker
        self.joined = threading.Event()
        self.release = threading.Event()

    def is_alive(self) -> bool:
        return self.worker.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self.worker.join(timeout)
        if self.worker.is_alive():
            return
        self.joined.set()
        if not self.release.wait(2.0):
            raise AssertionError("join gate was not released")


class _WaitTask:
    def observation_request(self) -> ObservationRequest:
        return ObservationRequest()

    def decide(self, observation) -> Decision:
        return Decision(
            "waiting",
            "test wait",
            Action(ActionKind.WAIT, "Wait", observation.tick),
        )

    def apply_verification(self, _result) -> None:
        raise AssertionError("wait task must not receive verification")

    def snapshot(self) -> TaskSnapshot:
        return TaskSnapshot("facade-test", TaskStatus.RUNNING, "waiting")


class _UnusedVerifier:
    def evaluate(self, *_args):
        raise AssertionError("wait task must not invoke verifier")


class _Factory:
    def __init__(self) -> None:
        self.runtimes: list[TaskRuntime] = []
        self.controls: list[RuntimeControl] = []
        self.bindings = []

    def __call__(self, _client, binding, configuration, execute, control):
        if execute:
            raise AssertionError("test factory is read-only")
        runtime = TaskRuntime(
            _LoopClient(),
            _WaitTask(),
            _UnusedVerifier(),
            configuration=configuration,
            control=control,
            sleep=lambda _seconds: time.sleep(0.002),
        )
        self.runtimes.append(runtime)
        self.controls.append(control)
        self.bindings.append(binding)
        return runtime


def _profile_values() -> dict[str, object]:
    return {
        "profileId": DEFAULT_PROFILE.profile_id,
        "definitionId": DEFAULT_PROFILE.definition_id,
        "cycleGoal": DEFAULT_PROFILE.cycle_goal,
    }


def _wait_for_lifecycle(
    application: EngineApplication,
    state: LifecycleState,
    timeout: float = 2.0,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = application.snapshot()
        if snapshot.lifecycle is state:
            return snapshot
        time.sleep(0.005)
    raise AssertionError(f"application did not reach {state.value}")


class EngineApplicationTests(unittest.TestCase):
    def test_catalog_and_profile_contract_are_exact_and_fresh(self) -> None:
        tasks = EngineApplication.list_tasks()
        definitions = EngineApplication.list_definitions()
        first = EngineApplication.profile_contract()
        second = EngineApplication.profile_contract()

        self.assertEqual(1, len(tasks))
        self.assertEqual(SUPPORTED_TASK_ID, tasks[0].task_id)
        self.assertEqual((DEFAULT_PROFILE.definition_id,), tasks[0].definition_ids)
        self.assertEqual(1, len(definitions))
        self.assertEqual(DEFAULT_PROFILE.definition_id, definitions[0].definition_id)
        self.assertFalse(definitions[0].profile_selectable_resource)
        self.assertFalse(definitions[0].profile_selectable_bank)
        self.assertIsNot(first, second)
        first["fields"].append({"name": "unsafe"})
        self.assertEqual(3, len(second["fields"]))
        self.assertFalse(second["profileMayOverrideEngineInvariants"])
        with self.assertRaises(ApplicationError):
            EngineApplication.list_definitions("unknown")

    def test_authoritative_profile_validation_rejects_missing_unknown_and_unsafe(self) -> None:
        binding = EngineApplication.validate_profile(_profile_values())
        self.assertEqual(DEFAULT_PROFILE, binding.profile)
        for values in (
            {},
            {**_profile_values(), "freshness": False},
            {**_profile_values(), "cycleGoal": 2},
            {**_profile_values(), "cycleGoal": True},
            {**_profile_values(), "definitionId": "unknown"},
        ):
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                EngineApplication.validate_profile(values)

    def test_default_facade_composes_the_real_task_runtime_and_frame(self) -> None:
        application = EngineApplication(client=_TreeReadyClient())

        started = application.start(profile_values=_profile_values())
        finished = application.wait(started.run_id, 2.0)

        self.assertIs(finished.lifecycle, LifecycleState.COMPLETE)
        self.assertEqual("DRY_RUN", finished.runtime_statistics.status)
        self.assertEqual(2, finished.runtime_statistics.observations)
        self.assertIsNotNone(finished.engine_frame)
        self.assertEqual(SUPPORTED_TASK_ID, finished.engine_frame.task.task_id)
        self.assertEqual(
            ActionKind.INTERACT_OBJECT,
            finished.engine_frame.decision.action.kind,
        )
        self.assertEqual(
            DEFAULT_PROFILE.definition_id,
            finished.engine_frame.task.definition_id,
        )

    def test_start_pause_resume_stop_and_exact_read_surfaces(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        started = application.start(profile_values=_profile_values())
        run_id = started.active_run_id
        self.assertIsNotNone(run_id)

        application.request_pause(run_id)
        paused = _wait_for_lifecycle(application, LifecycleState.PAUSED)
        before = application.read_statistics()
        time.sleep(0.02)
        after = application.read_statistics()

        self.assertEqual(before, after)
        self.assertIs(application.read_engine_frame(), factory.runtimes[0].frame_publisher.latest())
        self.assertIs(application.read_statistics(), factory.runtimes[0].statistics())
        self.assertIs(paused.engine_frame, application.read_engine_frame())
        application.resume(run_id)
        application.request_safe_stop(run_id)
        stopped = application.wait(run_id, 2.0)

        self.assertIs(stopped.lifecycle, LifecycleState.STOPPED)
        self.assertEqual("SAFE_STOPPED", stopped.runtime_statistics.status)
        self.assertEqual((), stopped.blockers)
        self.assertEqual(1, len(factory.bindings))
        self.assertIs(
            application.request_safe_stop(run_id).lifecycle,
            LifecycleState.STOPPED,
        )

    def test_snapshot_uses_one_engine_frame_sample(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        run_id = application.start().active_run_id
        application.request_pause(run_id)
        _wait_for_lifecycle(application, LifecycleState.PAUSED)
        runtime = factory.runtimes[0]
        original = runtime._frame_publisher
        first = original.latest()
        self.assertIsNotNone(first)
        second = replace(first, sequence=first.sequence + 1, blocker="later blocker")

        class AlternatingPublisher:
            def __init__(self) -> None:
                self.calls = 0

            def latest(self):
                self.calls += 1
                return first if self.calls == 1 else second

        publisher = AlternatingPublisher()
        runtime._frame_publisher = publisher
        try:
            snapshot = application.snapshot()
        finally:
            runtime._frame_publisher = original

        self.assertIs(first, snapshot.engine_frame)
        self.assertEqual((), snapshot.blockers)
        self.assertEqual(1, publisher.calls)
        application.request_safe_stop(run_id)
        application.wait(run_id, 2.0)

    def test_stale_run_id_cannot_control_a_new_run(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        first_id = application.start().active_run_id
        application.request_safe_stop(first_id)
        application.wait(first_id, 2.0)
        second_id = application.start().active_run_id

        self.assertNotEqual(first_id, second_id)
        with self.assertRaises(ApplicationError):
            application.request_pause(first_id)
        application.request_safe_stop(second_id)
        application.wait(second_id, 2.0)
        self.assertEqual(2, len(factory.runtimes))
        self.assertIsNot(factory.runtimes[0], factory.runtimes[1])
        self.assertIsNot(factory.controls[0], factory.controls[1])

    def test_two_racing_starts_create_only_one_worker(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        barrier = threading.Barrier(3)
        successes = []
        errors = []

        def start() -> None:
            barrier.wait()
            try:
                successes.append(application.start())
            except ApplicationError as error:
                errors.append(error)

        workers = [threading.Thread(target=start) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(2.0)

        self.assertEqual(1, len(successes))
        self.assertEqual(1, len(errors))
        run_id = successes[0].active_run_id
        application.request_safe_stop(run_id)
        application.wait(run_id, 2.0)

    def test_demonstration_is_mutually_exclusive_and_stale_ids_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            demo_started = threading.Event()

            def runner(_name, _client, **values):
                demo_started.set()
                while not values["stop_requested"]():
                    time.sleep(0.002)
                return Path(temporary) / "artifact"

            application = EngineApplication(
                client=_LoopClient(),
                runtime_factory=_Factory(),
                demonstration_runner=runner,
                demonstration_inspector=lambda _path: InspectionResult(
                    True, "VERIFIED"
                ),
            )
            first = application.begin_demonstration("route")
            first_id = first.active_capture_id
            self.assertTrue(demo_started.wait(1.0))
            with self.assertRaises(ApplicationError):
                application.start()
            ended = application.end_demonstration(first_id, timeout=2.0)

            self.assertFalse(ended.recent_demonstration is None)
            self.assertTrue(ended.recent_demonstration.valid)
            second_id = application.begin_demonstration("route-two").active_capture_id
            with self.assertRaises(ApplicationError):
                application.end_demonstration(first_id)
            application.end_demonstration(second_id, timeout=2.0)

    def test_active_run_blocks_demonstration(self) -> None:
        application = EngineApplication(
            client=_LoopClient(), runtime_factory=_Factory()
        )
        run_id = application.start().active_run_id
        with self.assertRaises(ApplicationError):
            application.begin_demonstration("not-while-running")
        application.request_safe_stop(run_id)
        application.wait(run_id, 2.0)

    def test_completed_tokens_are_not_commands_after_switching_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def runner(_name, _client, **values):
                while not values["stop_requested"]():
                    time.sleep(0.002)
                return Path(temporary) / "artifact"

            application = EngineApplication(
                client=_LoopClient(),
                runtime_factory=_Factory(),
                demonstration_runner=runner,
                demonstration_inspector=lambda _path: InspectionResult(
                    True, "VERIFIED"
                ),
            )
            run_id = application.start().active_run_id
            application.request_safe_stop(run_id)
            application.wait(run_id, 2.0)
            capture_id = application.begin_demonstration("cross-mode").active_capture_id

            with self.assertRaises(ApplicationError):
                application.request_safe_stop(run_id)
            with self.assertRaises(ApplicationError):
                application.wait(run_id, 0.0)
            application.end_demonstration(capture_id, timeout=2.0)

            next_run_id = application.start().active_run_id
            with self.assertRaises(ApplicationError):
                application.end_demonstration(capture_id, timeout=0.0)
            application.request_safe_stop(next_run_id)
            application.wait(next_run_id, 2.0)

    def test_wait_rejects_if_a_new_run_starts_after_join(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        first_id = application.start().active_run_id
        original_worker = application._run_thread
        gate = _JoinGate(original_worker)
        application._run_thread = gate
        application.request_safe_stop(first_id)
        errors = []

        def wait_first() -> None:
            try:
                application.wait(first_id, 2.0)
            except ApplicationError as error:
                errors.append(error)

        waiter = threading.Thread(target=wait_first)
        waiter.start()
        self.assertTrue(gate.joined.wait(2.0))
        second_id = application.start().active_run_id
        gate.release.set()
        waiter.join(2.0)

        self.assertEqual(1, len(errors))
        application.request_safe_stop(second_id)
        application.wait(second_id, 2.0)

    def test_demo_end_rejects_if_a_run_starts_after_join(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def runner(_name, _client, **values):
                while not values["stop_requested"]():
                    time.sleep(0.002)
                return Path(temporary) / "artifact"

            application = EngineApplication(
                client=_LoopClient(),
                runtime_factory=_Factory(),
                demonstration_runner=runner,
                demonstration_inspector=lambda _path: InspectionResult(
                    True, "VERIFIED"
                ),
            )
            capture_id = application.begin_demonstration("join-race").active_capture_id
            original_worker = application._demo_thread
            gate = _JoinGate(original_worker)
            application._demo_thread = gate
            errors = []

            def end_capture() -> None:
                try:
                    application.end_demonstration(capture_id, timeout=2.0)
                except ApplicationError as error:
                    errors.append(error)

            ender = threading.Thread(target=end_capture)
            ender.start()
            self.assertTrue(gate.joined.wait(2.0))
            run_id = application.start().active_run_id
            gate.release.set()
            ender.join(2.0)

            self.assertEqual(1, len(errors))
            application.request_safe_stop(run_id)
            application.wait(run_id, 2.0)

    def test_racing_run_and_demo_allow_exactly_one_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            def runner(_name, _client, **values):
                while not values["stop_requested"]():
                    time.sleep(0.002)
                return Path(temporary) / "artifact"

            application = EngineApplication(
                client=_LoopClient(),
                runtime_factory=_Factory(),
                demonstration_runner=runner,
                demonstration_inspector=lambda _path: InspectionResult(
                    True, "VERIFIED"
                ),
            )
            barrier = threading.Barrier(3)
            successes = []
            errors = []

            def start_run() -> None:
                barrier.wait()
                try:
                    successes.append(("run", application.start()))
                except ApplicationError as error:
                    errors.append(error)

            def start_demo() -> None:
                barrier.wait()
                try:
                    successes.append(
                        ("demo", application.begin_demonstration("race"))
                    )
                except ApplicationError as error:
                    errors.append(error)

            workers = [
                threading.Thread(target=start_run),
                threading.Thread(target=start_demo),
            ]
            for worker in workers:
                worker.start()
            barrier.wait()
            for worker in workers:
                worker.join(2.0)

            self.assertEqual(1, len(successes))
            self.assertEqual(1, len(errors))
            kind, snapshot = successes[0]
            if kind == "run":
                application.request_safe_stop(snapshot.active_run_id)
                application.wait(snapshot.active_run_id, 2.0)
            else:
                application.end_demonstration(snapshot.active_capture_id, timeout=2.0)

    def test_profile_is_revalidated_before_runtime_construction(self) -> None:
        factory = _Factory()
        application = EngineApplication(client=_LoopClient(), runtime_factory=factory)
        with self.assertRaises(ValueError):
            application.start(profile_values={})
        self.assertEqual([], factory.runtimes)

    def test_worker_exception_becomes_status_and_blocker(self) -> None:
        class ExplodingRuntime(TaskRuntime):
            def run(self, *, execute: bool = False):
                raise RuntimeError("worker exploded")

        def factory(_client, _binding, configuration, _execute, control):
            return ExplodingRuntime(
                _LoopClient(),
                _WaitTask(),
                _UnusedVerifier(),
                configuration=configuration,
                control=control,
                sleep=lambda _seconds: None,
            )

        application = EngineApplication(
            client=_LoopClient(), runtime_factory=factory
        )
        run_id = application.start().active_run_id
        snapshot = application.wait(run_id, 2.0)

        self.assertIs(snapshot.lifecycle, LifecycleState.ERROR)
        self.assertIn("worker exploded", " ".join(snapshot.blockers))
        self.assertFalse(snapshot.runtime_statistics.active)
        self.assertEqual("ERROR", snapshot.runtime_statistics.status)

    def test_facade_has_no_engine_or_input_authority(self) -> None:
        path = ROOT / "osrs_bot" / "application.py"
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
            "InputCoordinator",
            "Verifier",
            "Arduino",
            "Action",
        ):
            self.assertNotIn(forbidden, imported_names)
        self.assertNotIn(".decide(", source)
        self.assertNotIn(".apply_verification(", source)
        self.assertNotIn("pyautogui", source.lower())
        self.assertNotIn("pydirectinput", source.lower())


if __name__ == "__main__":
    unittest.main()
