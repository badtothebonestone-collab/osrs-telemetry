from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import inspect
import unittest

import osrs_bot.action as action_module
from osrs_bot.action import CoordinatedActionInterface, ExecutionResult
from osrs_bot.input_coordinator import (
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    CommandEvidence,
    FirmwareSafetyStatus,
    InputPurpose,
    InputReceipt,
    InputCoordinator,
    InputValidation,
    PointerActivation,
    PointerActivationDecision,
)
from osrs_bot.model import (
    Action,
    ActionKind,
    CameraConstraint,
    CLOSE_BANK_WIDGET_KEY,
    DialogueOption,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryObservation,
    MenuEntry,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    TaskConstraints,
    VerificationKind,
    VerificationSpec,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.safety import SafetyCheck, SafetyGate


POINT = ScreenPoint(110, 110)
CANVAS = ScreenBounds(50, 50, 500, 400)


def observation(
    *,
    menus: tuple[MenuEntry, ...],
    tick: int = 10,
    menu_open: bool = False,
    menu_bounds: ScreenBounds | None = None,
    menu_point: ScreenPoint = POINT,
    widgets: WidgetObservation | None = None,
    location: WorldPoint = WorldPoint(3192, 3244, 0),
    nearby_objects: tuple[NearbyObject, ...] | None = None,
    camera_yaw: int | None = None,
    geometry_frame_id: str | None = None,
) -> Observation:
    timestamp = datetime.now(timezone.utc)
    session_id = "session-1"
    process_id = 1234
    frame_id = f"test-frame-{tick}"
    tree = NearbyObject(
        key="tree-1",
        object_id=1276,
        name="Tree",
        kind="GAME_OBJECT",
        actions=("Chop down",),
        location=WorldPoint(3193, 3244, 0),
        distance=1,
        geometry=TargetGeometry(
            available=True,
            on_screen=True,
            visible=True,
            actionable=True,
            canvas_point=ScreenPoint(60, 60),
            screen_point=POINT,
            screen_bounds=ScreenBounds(100, 100, 30, 30),
        ),
        scene_x=49,
        scene_y=52,
        resource_candidate=False,
    )
    return Observation(
        player=PlayerObservation(),
        location=location,
        plane=location.plane,
        inventory=InventoryObservation(known=True),
        nearby_objects=(tree,) if nearby_objects is None else nearby_objects,
        menus=menus,
        widgets=widgets or WidgetObservation(bank_known=True),
        canvas_bounds=CANVAS,
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id=session_id,
        menu_client_tick=1000 + tick,
        menu_mouse_screen_point=menu_point,
        menu_open=menu_open,
        menu_bounds=menu_bounds,
        client_focused=True,
        client_process_id=process_id,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=geometry_frame_id or frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=session_id,
        menu_process_id=process_id,
        camera_yaw=camera_yaw,
    )


def tree_action() -> Action:
    return Action(
        kind=ActionKind.INTERACT_OBJECT,
        label="chop ordinary tree",
        source_tick=10,
        option="Chop down",
        target_key="tree-1",
        target_name="Tree",
        target_id=1276,
        screen_point=POINT,
        source_menu_client_tick=1010,
        target_param0=49,
        target_param1=52,
        source_session_id="session-1",
    )


def command_evidence(sequence: int, command: str) -> CommandEvidence:
    return CommandEvidence(
        command_id=f"cmd-{sequence:08d}",
        sequence=sequence,
        command=command,
        status="PASS",
        write_ok=True,
        ack_received=True,
        accepted=True,
        response_token="OK",
        payload_token=command,
    )


def input_receipt(
    *,
    mode: str = "pointer",
    status: str = "PASS",
    reason: str = "input_transaction_succeeded",
    intent_ids: tuple[str, ...] = ("gameplay",),
    commands: tuple[str, ...] = (
        "ARM",
        "MOVE",
        "MOUSE_DOWN",
        "MOUSE_UP",
        "STOP_ALL",
        "DISARM",
        "STATUS",
    ),
    cleanup: bool = True,
    context_cancel_attempted: bool = False,
    context_cancel_acknowledged: bool = False,
) -> InputReceipt:
    evidence = tuple(
        command_evidence(index, command)
        for index, command in enumerate(commands, start=1)
    )
    return InputReceipt(
        transaction_id="input-00000001",
        mode=mode,
        intent_ids=intent_ids,
        status=status,
        reason=reason,
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=True,
        disarm_acknowledged=cleanup,
        firmware_status_acknowledged=cleanup,
        firmware_status=FirmwareSafetyStatus(False, 0, 0) if cleanup else None,
        commands=evidence,
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        context_cancel_attempted=context_cancel_attempted,
        context_cancel_acknowledged=context_cancel_acknowledged,
        errors=() if status == "PASS" else (reason,),
    )


class FakeCoordinator:
    def __init__(
        self,
        *,
        forced_receipt: InputReceipt | None = None,
        actual_pointer: ScreenPoint | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.pointer_intents: list[ApprovedPointerIntent] = []
        self.key_intents: list[ApprovedKeyIntent] = []
        self.decisions: list[PointerActivationDecision] = []
        self.row_intents: list[ApprovedPointerIntent] = []
        self.forced_receipt = forced_receipt
        self.actual_pointer = actual_pointer

    @staticmethod
    def _denied(
        reason: str,
        *,
        intent_ids: tuple[str, ...],
        mode: str = "pointer",
    ) -> InputReceipt:
        return input_receipt(
            mode=mode,
            status="BLOCKED",
            reason=f"fresh_input_validation_denied: {reason}",
            intent_ids=intent_ids,
            commands=("ARM", "MOVE", "STOP_ALL", "DISARM", "STATUS"),
        )

    def execute_pointer(self, intent, *, validate):  # type: ignore[no-untyped-def]
        self.calls.append("pointer")
        self.pointer_intents.append(intent)
        decision = validate(intent, self.actual_pointer or intent.target)
        if self.forced_receipt is not None:
            return self.forced_receipt
        if not isinstance(decision, InputValidation) or not decision.allowed:
            reason = decision.reason if isinstance(decision, InputValidation) else "invalid"
            return self._denied(reason, intent_ids=(intent.intent_id,))
        return input_receipt(intent_ids=(intent.intent_id,))

    def execute_key(self, intent, *, validate):  # type: ignore[no-untyped-def]
        self.calls.append("key")
        self.key_intents.append(intent)
        decision = validate(intent)
        if self.forced_receipt is not None:
            return self.forced_receipt
        if not isinstance(decision, InputValidation) or not decision.allowed:
            reason = decision.reason if isinstance(decision, InputValidation) else "invalid"
            return self._denied(
                reason,
                intent_ids=(intent.intent_id,),
                mode="key",
            )
        return input_receipt(
            mode="key",
            intent_ids=(intent.intent_id,),
            commands=("ARM", "KEY_PRESS", "STOP_ALL", "DISARM", "STATUS"),
        )

    def execute_adaptive_pointer(
        self,
        intent,
        *,
        decide_activation,
        resolve_row,
        validate_row,
    ):  # type: ignore[no-untyped-def]
        self.calls.append("adaptive")
        self.pointer_intents.append(intent)
        decision = decide_activation(intent, self.actual_pointer or intent.target)
        self.decisions.append(decision)
        if self.forced_receipt is not None:
            return self.forced_receipt
        if not isinstance(decision, PointerActivationDecision) or not decision.validation.allowed:
            reason = (
                decision.validation.reason
                if isinstance(decision, PointerActivationDecision)
                else "invalid"
            )
            return self._denied(
                reason,
                intent_ids=(intent.intent_id,),
                mode="adaptive_pointer",
            )
        if decision.activation is PointerActivation.DIRECT_LEFT:
            return input_receipt(
                mode="adaptive_pointer",
                intent_ids=(intent.intent_id,),
            )

        try:
            row_intent = resolve_row()
            self.row_intents.append(row_intent)
            row_validation = validate_row(row_intent, row_intent.target)
        except Exception as error:  # mirror coordinator fail-closed cancellation
            reason = f"context_row_resolution_blocked: {error}"
            return input_receipt(
                mode="adaptive_pointer",
                status="BLOCKED",
                reason=reason,
                intent_ids=(intent.intent_id,),
                commands=(
                    "ARM",
                    "MOVE",
                    "MOUSE_DOWN",
                    "MOUSE_UP",
                    "KEY_PRESS",
                    "STOP_ALL",
                    "DISARM",
                    "STATUS",
                ),
                context_cancel_attempted=True,
                context_cancel_acknowledged=True,
            )
        intent_ids = (intent.intent_id, row_intent.intent_id)
        if not row_validation.allowed:
            return input_receipt(
                mode="adaptive_pointer",
                status="BLOCKED",
                reason=f"fresh_input_validation_denied: {row_validation.reason}",
                intent_ids=intent_ids,
                commands=(
                    "ARM",
                    "MOVE",
                    "MOUSE_DOWN",
                    "MOUSE_UP",
                    "MOVE",
                    "KEY_PRESS",
                    "STOP_ALL",
                    "DISARM",
                    "STATUS",
                ),
                context_cancel_attempted=True,
                context_cancel_acknowledged=True,
            )
        return input_receipt(
            mode="adaptive_pointer",
            intent_ids=intent_ids,
            commands=(
                "ARM",
                "MOVE",
                "MOUSE_DOWN",
                "MOUSE_UP",
                "MOVE",
                "MOUSE_DOWN",
                "MOUSE_UP",
                "STOP_ALL",
                "DISARM",
                "STATUS",
            ),
        )


class CoordinatedActionInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pre = observation(menus=())
        self.hover = observation(
            menus=(
                MenuEntry(
                    "Chop down",
                    "Tree",
                    "GAME_OBJECT_FIRST_OPTION",
                    1276,
                    49,
                    52,
                ),
            ),
            tick=11,
        )

    def interface(
        self,
        coordinator: FakeCoordinator,
        post: Observation,
    ) -> CoordinatedActionInterface:
        return CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: post,
            sleep=lambda _: None,
        )

    def test_object_uses_fresh_adaptive_decision_and_carries_cleanup_receipt(self) -> None:
        coordinator = FakeCoordinator()
        result = self.interface(coordinator, self.hover).execute(
            tree_action(), self.pre
        )

        self.assertEqual("SENT", result.status)
        self.assertEqual("action_sent", result.reason)
        self.assertEqual(11, result.post_move_tick)
        self.assertTrue(result.stop_all_confirmed)
        self.assertTrue(result.disarm_confirmed)
        self.assertTrue(result.cleanup_confirmed)
        self.assertIsInstance(result.receipt, InputReceipt)
        self.assertEqual(["adaptive"], coordinator.calls)
        self.assertEqual(PointerActivation.DIRECT_LEFT, coordinator.decisions[0].activation)
        intent = coordinator.pointer_intents[0]
        self.assertEqual(InputPurpose.GAMEPLAY_OBJECT, intent.purpose)
        self.assertEqual(POINT, intent.target)
        self.assertEqual(CANVAS, intent.movement_bounds)
        self.assertEqual(ScreenBounds(107, 107, 7, 7), intent.target_bounds)
        self.assertEqual(1234, intent.expected_pid)
        self.assertEqual("pre_move.observation", result.safety_checks[0].stage)
        self.assertIn(
            SafetyCheck("pre_move.complete", "pre_move_safe", True),
            result.safety_checks,
        )
        self.assertIn(
            SafetyCheck("post_move.complete", "post_move_safe", True),
            result.safety_checks,
        )
        self.assertEqual(
            SafetyCheck(
                "context_candidate.exact_lower_entry",
                "context_option_not_unique_lower_entry",
                False,
            ),
            result.safety_checks[-1],
        )

    def test_fresh_hover_mismatch_denies_activation_but_preserves_cleanup_proof(self) -> None:
        coordinator = FakeCoordinator()
        no_hover = observation(menus=(), tick=11)
        result = self.interface(coordinator, no_hover).execute(
            tree_action(), self.pre
        )

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("hover_menu_mismatch", result.reason)
        self.assertFalse(result.sent)
        self.assertTrue(result.cleanup_confirmed)
        self.assertIsNone(coordinator.decisions[0].activation)

    def test_hover_validation_is_bound_to_actual_settled_point(self) -> None:
        actual = ScreenPoint(POINT.x + 3, POINT.y)
        stale_center_match = ScreenPoint(POINT.x - 3, POINT.y)
        coordinator = FakeCoordinator(actual_pointer=actual)
        post = observation(
            menus=self.hover.menus,
            tick=11,
            menu_point=stale_center_match,
        )

        result = self.interface(coordinator, post).execute(
            tree_action(), self.pre
        )

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("hover_pointer_mismatch", result.reason)
        self.assertIsNone(coordinator.decisions[0].activation)

    def test_settled_pointer_inside_verified_region_preserves_canonical_aim(self) -> None:
        actual = ScreenPoint(POINT.x + 3, POINT.y)
        coordinator = FakeCoordinator(actual_pointer=actual)
        post = observation(
            menus=self.hover.menus,
            tick=11,
            menu_point=actual,
        )

        result = self.interface(coordinator, post).execute(
            tree_action(), self.pre
        )

        self.assertEqual("SENT", result.status)
        self.assertEqual(
            PointerActivation.DIRECT_LEFT,
            coordinator.decisions[0].activation,
        )

    def test_fresh_hover_polling_is_bounded_before_direct_activation(self) -> None:
        coordinator = FakeCoordinator()
        samples = iter((observation(menus=(), tick=11), self.hover))
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=2,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(11, result.post_move_tick)
        self.assertEqual(PointerActivation.DIRECT_LEFT, coordinator.decisions[0].activation)
        hover_checks = [
            check
            for check in result.safety_checks
            if check.stage == "post_move.hover_menu"
        ]
        self.assertEqual(
            [
                SafetyCheck(
                    "post_move.hover_menu", "hover_menu_mismatch", False
                ),
                SafetyCheck(
                    "post_move.hover_menu", "hover_menu_exact", True
                ),
            ],
            hover_checks,
        )

    def test_transient_post_move_warn_is_reobserved_before_activation(self) -> None:
        coordinator = FakeCoordinator()
        transient = replace(self.hover, status="WARN")
        samples = iter((transient, self.hover))
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=2,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(
            PointerActivation.DIRECT_LEFT,
            coordinator.decisions[0].activation,
        )
        self.assertIn(
            SafetyCheck(
                "post_move.observation", "observation_not_pass", False
            ),
            result.safety_checks,
        )

    def test_persistent_post_move_warn_stays_bounded_and_blocks(self) -> None:
        coordinator = FakeCoordinator()
        transient = replace(self.hover, status="WARN")
        calls = 0

        def observe() -> Observation:
            nonlocal calls
            calls += 1
            return transient

        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            observe,
            sleep=lambda _: None,
            evidence_attempts=2,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("observation_not_pass", result.reason)
        self.assertEqual(2, calls)
        self.assertIsNone(coordinator.decisions[0].activation)
        self.assertTrue(result.cleanup_confirmed)

    def test_context_row_is_resolved_then_revalidated_from_new_exact_evidence(self) -> None:
        coordinator = FakeCoordinator()
        generic = MenuEntry("Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52)
        exact = MenuEntry("Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52)
        candidate = observation(menus=(generic, exact), tick=11)
        menu_bounds = ScreenBounds(80, 80, 200, 100)
        row_bounds = ScreenBounds(81, 114, 199, 15)
        opened_entries = (
            replace(generic, row_bounds=ScreenBounds(81, 99, 199, 15)),
            replace(exact, row_bounds=row_bounds),
        )
        opened = observation(
            menus=opened_entries,
            tick=12,
            menu_open=True,
            menu_bounds=menu_bounds,
        )
        row = observation(
            menus=opened_entries,
            tick=13,
            menu_open=True,
            menu_bounds=menu_bounds,
            menu_point=row_bounds.center,
        )
        samples = iter((candidate, opened, row))
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(13, result.post_move_tick)
        self.assertEqual(PointerActivation.CONTEXT_MENU, coordinator.decisions[0].activation)
        self.assertEqual(1, len(coordinator.row_intents))
        row_intent = coordinator.row_intents[0]
        self.assertEqual(InputPurpose.CONTEXT_ROW, row_intent.purpose)
        self.assertEqual(row_bounds.center, row_intent.target)
        self.assertEqual(
            ScreenBounds(
                row_bounds.center.x - 3,
                row_bounds.center.y - 3,
                7,
                7,
            ),
            row_intent.target_bounds,
        )
        self.assertEqual(2, len(result.receipt.intent_ids if result.receipt else ()))
        self.assertEqual(
            ["context_menu_open_safe", "context_row_safe"],
            [
                check.code
                for check in result.safety_checks
                if check.stage == "context_menu.complete"
            ],
        )

    def test_context_row_pointer_mismatch_blocks_left_activation_and_records_cancel(self) -> None:
        coordinator = FakeCoordinator()
        generic = MenuEntry("Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52)
        exact = MenuEntry("Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52)
        candidate = observation(menus=(generic, exact), tick=11)
        menu_bounds = ScreenBounds(80, 80, 200, 100)
        row_bounds = ScreenBounds(81, 114, 199, 15)
        entries = (
            replace(generic, row_bounds=ScreenBounds(81, 99, 199, 15)),
            replace(exact, row_bounds=row_bounds),
        )
        opened = observation(menus=entries, tick=12, menu_open=True, menu_bounds=menu_bounds)
        wrong_row = observation(
            menus=entries,
            tick=13,
            menu_open=True,
            menu_bounds=menu_bounds,
            menu_point=ScreenPoint(90, 90),
        )
        samples = iter((candidate, opened, wrong_row))
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=1,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("context_row_pointer_mismatch", result.reason)
        self.assertTrue(result.receipt and result.receipt.context_cancel_attempted)
        self.assertTrue(result.receipt and result.receipt.context_cancel_acknowledged)

    def test_context_resolver_failure_exposes_hidden_escape_wire_receipt(self) -> None:
        coordinator = FakeCoordinator()
        generic = MenuEntry("Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52)
        exact = MenuEntry("Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52)
        candidate = observation(menus=(generic, exact), tick=11)
        still_closed = observation(menus=(generic, exact), tick=12)
        samples = iter((candidate, still_closed))
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=1,
        )

        result = interface.execute(tree_action(), self.pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertIn("context_menu_not_open", result.reason)
        self.assertEqual(12, result.post_move_tick)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertTrue(result.receipt.context_cancel_attempted)
        self.assertTrue(result.receipt.context_cancel_acknowledged)
        commands = [evidence.command for evidence in result.receipt.commands]
        self.assertLess(commands.index("KEY_PRESS"), commands.index("STOP_ALL"))
        self.assertTrue(result.cleanup_confirmed)

    def test_real_coordinator_carries_hidden_escape_and_cleanup_evidence(self) -> None:
        # Reuse the transport-faithful ledger fake exercised by the coordinator
        # suite; this test proves the action callback triggers that real path.
        from tests.test_input_coordinator import FakeBackend as LedgerBackend

        generic = MenuEntry("Chop", "Tree", "GAME_OBJECT_FIRST_OPTION", 1276, 49, 52)
        exact = MenuEntry("Chop down", "Tree", "GAME_OBJECT_SECOND_OPTION", 1276, 49, 52)

        def coordinator_pid(sample: Observation) -> Observation:
            return replace(sample, client_process_id=321, menu_process_id=321)

        pre = coordinator_pid(self.pre)
        candidate = coordinator_pid(observation(menus=(generic, exact), tick=11))
        still_closed = coordinator_pid(observation(menus=(generic, exact), tick=12))
        samples = iter((candidate, still_closed))
        backend = LedgerBackend(start=(POINT.x, POINT.y))
        coordinator = InputCoordinator(
            lambda: backend,
            sleep=lambda _: None,
            pointer_timestep_seconds=0.02,
        )
        interface = CoordinatedActionInterface(
            coordinator,
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
            evidence_attempts=1,
        )

        result = interface.execute(tree_action(), pre)

        self.assertEqual("BLOCKED", result.status)
        self.assertIsNotNone(result.receipt)
        assert result.receipt is not None
        self.assertTrue(result.receipt.context_cancel_attempted)
        self.assertTrue(result.receipt.context_cancel_acknowledged)
        self.assertTrue(result.cleanup_confirmed)
        commands = [evidence.command for evidence in result.receipt.commands]
        self.assertLess(commands.index("KEY_PRESS"), commands.index("STOP_ALL"))
        self.assertLess(backend.events.index("press:ESC"), backend.events.index("stop_all"))

    def test_error_receipt_overrides_successful_fresh_action_validation(self) -> None:
        forced = input_receipt(
            status="ERROR",
            reason="disarm_not_acknowledged",
            cleanup=False,
        )
        coordinator = FakeCoordinator(forced_receipt=forced)

        result = self.interface(coordinator, self.hover).execute(
            tree_action(), self.pre
        )

        self.assertEqual("ERROR", result.status)
        self.assertEqual("disarm_not_acknowledged", result.reason)
        self.assertFalse(result.sent)
        self.assertFalse(result.disarm_confirmed)
        self.assertFalse(result.cleanup_confirmed)
        self.assertIs(forced, result.receipt)

    def test_preflight_block_and_wait_never_submit_input(self) -> None:
        coordinator = FakeCoordinator()
        stale = replace(self.pre, fresh=False)
        blocked = self.interface(coordinator, self.hover).execute(tree_action(), stale)
        wait = Action(
            ActionKind.WAIT,
            "wait",
            10,
            source_session_id="session-1",
        )
        no_action = self.interface(coordinator, self.hover).execute(wait, self.pre)

        self.assertEqual("BLOCKED", blocked.status)
        self.assertIsNone(blocked.receipt)
        self.assertEqual("NO_ACTION", no_action.status)
        self.assertIsNone(no_action.receipt)
        self.assertEqual([], coordinator.calls)
        self.assertEqual(
            (SafetyCheck("pre_move.observation", "observation_stale", False),),
            blocked.safety_checks,
        )
        self.assertEqual("pre_move_safe", no_action.safety_checks[-1].code)

    def test_dialogue_key_rechecks_fresh_option_and_submits_typed_key_intent(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(DialogueOption(1, "1", "Climb up the stairs."),),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        pre = replace(self.pre, widgets=widgets)
        fresh = replace(self.hover, widgets=replace(widgets, dialogue_client_tick=501))
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose climb up",
            10,
            option="Climb up the stairs.",
            target_key="dialogue:1",
            target_name="Climb up the stairs.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
            task_constraints=TaskConstraints(
                dialogue=DialogueOptionConstraint(
                    "climb", "Climb up the stairs.", 1, "1"
                )
            ),
        )
        coordinator = FakeCoordinator()

        result = self.interface(coordinator, fresh).execute(action, pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(["key"], coordinator.calls)
        intent = coordinator.key_intents[0]
        self.assertEqual(InputPurpose.GAMEPLAY_KEY, intent.purpose)
        self.assertEqual("1", intent.key)
        self.assertEqual(501, fresh.widgets.dialogue_client_tick)

    def test_camera_key_rechecks_pose_and_submits_bounded_hold(self) -> None:
        source = WorldPoint(3195, 3248, 0)
        target_location = WorldPoint(3200, 3238, 0)
        target = NearbyObject(
            key="route:west_approach_bridge",
            object_id=0,
            name="route:west_approach_bridge",
            kind="NAVIGATION_TILE",
            actions=("Walk here",),
            location=target_location,
            distance=10,
            geometry=TargetGeometry(),
            scene_x=56,
            scene_y=38,
            route_candidate=True,
        )
        verification = VerificationSpec(
            VerificationKind.CAMERA_POSE_CHANGED,
            before_tick=10,
            deadline_tick=20,
            before_location=source,
            source_session_id="session-1",
            before_camera_yaw=0,
            before_geometry_frame_id="camera-frame-0",
            camera_key="left",
        )
        action = Action(
            ActionKind.PRESS_KEY,
            "Turn camera toward route",
            10,
            option="Turn camera left",
            target_key=target.key,
            target_name=target.name,
            target_id=0,
            key="left",
            key_hold_millis=250,
            verification=verification,
            target_param0=56,
            target_param1=38,
            source_session_id="session-1",
            task_constraints=TaskConstraints(
                camera=CameraConstraint(
                    target.key,
                    target_location,
                    source,
                    "camera-frame-0",
                    0,
                    "left",
                    250,
                )
            ),
        )
        pre = observation(
            menus=(),
            tick=10,
            location=source,
            nearby_objects=(target,),
            camera_yaw=0,
            geometry_frame_id="camera-frame-0",
        )
        fresh = observation(
            menus=(),
            tick=11,
            location=source,
            nearby_objects=(target,),
            camera_yaw=0,
            geometry_frame_id="camera-frame-0",
        )
        coordinator = FakeCoordinator()

        result = self.interface(coordinator, fresh).execute(action, pre)

        self.assertEqual("SENT", result.status)
        intent = coordinator.key_intents[0]
        self.assertEqual("LEFT", intent.key)
        self.assertEqual(250, intent.hold_millis)
        self.assertEqual(11, result.post_move_tick)

    def test_dialogue_key_waits_boundedly_for_new_widget_evidence(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            dialogue_active=True,
            dialogue_type="options",
            dialogue_prompt="Climb up or down the stairs?",
            dialogue_options=(DialogueOption(1, "1", "Climb up the stairs."),),
            dialogue_number_keys=True,
            dialogue_client_tick=500,
        )
        stale = replace(self.hover, widgets=widgets)
        fresh = replace(self.hover, widgets=replace(widgets, dialogue_client_tick=501))
        samples = iter((stale, stale, fresh))
        action = Action(
            ActionKind.PRESS_KEY,
            "Choose climb up",
            10,
            option="Climb up the stairs.",
            target_key="dialogue:1",
            target_name="Climb up the stairs.",
            target_id=1,
            key="1",
            source_session_id="session-1",
            source_dialogue_client_tick=500,
            task_constraints=TaskConstraints(
                dialogue=DialogueOptionConstraint("climb", "Climb up the stairs.", 1, "1")
            ),
        )
        coordinator = FakeCoordinator()
        interface = CoordinatedActionInterface(
            coordinator,  # type: ignore[arg-type]
            SafetyGate(max_observation_age_seconds=10),
            lambda: next(samples),
            sleep=lambda _: None,
        )

        result = interface.execute(action, replace(self.pre, widgets=widgets))

        self.assertEqual("SENT", result.status)
        self.assertEqual(11, result.post_move_tick)

    def test_bank_escape_rechecks_interface_then_submits_coordinator_key(self) -> None:
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            keyboard_close_possible=True,
        )
        location = WorldPoint(3208, 3220, 2)
        pre = observation(menus=(), widgets=widgets, location=location)
        fresh = observation(menus=(), tick=11, widgets=widgets, location=location)
        action = Action(
            ActionKind.PRESS_KEY,
            "Close bank with Escape",
            10,
            option="Close bank",
            target_key="close_bank_keyboard",
            target_name="Close bank",
            target_id=0,
            key="escape",
            source_session_id="session-1",
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(
                    "bank", 2, True, require_keyboard_close=True
                )
            ),
        )
        coordinator = FakeCoordinator()

        result = self.interface(coordinator, fresh).execute(action, pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual("ESCAPE", coordinator.key_intents[0].key)
        self.assertEqual(11, result.post_move_tick)

    def test_widget_pointer_rechecks_exact_widget_and_uses_widget_bounds(self) -> None:
        close = WidgetTarget(
            "Close bank",
            True,
            POINT,
            ScreenBounds(100, 100, 30, 30),
        )
        widgets = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            close_bank=close,
        )
        location = WorldPoint(3208, 3220, 2)
        pre = observation(menus=(), widgets=widgets, location=location)
        fresh = observation(menus=(), tick=11, widgets=widgets, location=location)
        action = Action(
            ActionKind.CLICK_WIDGET,
            "Close bank",
            10,
            option="Close bank",
            target_key=CLOSE_BANK_WIDGET_KEY,
            target_name="Close bank",
            target_id=0,
            screen_point=POINT,
            source_menu_client_tick=1010,
            source_session_id="session-1",
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint("bank", 2, True, require_readable=True)
            ),
        )
        coordinator = FakeCoordinator()

        result = self.interface(coordinator, fresh).execute(action, pre)

        self.assertEqual("SENT", result.status)
        self.assertEqual(["pointer"], coordinator.calls)
        intent = coordinator.pointer_intents[0]
        self.assertEqual(InputPurpose.GAMEPLAY_WIDGET, intent.purpose)
        self.assertEqual(ScreenBounds(107, 107, 7, 7), intent.target_bounds)

    def test_execution_result_is_immutable_and_has_no_mutable_backend_status(self) -> None:
        coordinator = FakeCoordinator()
        result = self.interface(coordinator, self.hover).execute(tree_action(), self.pre)

        self.assertFalse(hasattr(result, "__dict__"))
        self.assertFalse(hasattr(result, "backend_status"))
        self.assertIsInstance(result.receipt, InputReceipt)
        self.assertFalse(hasattr(result.receipt, "__dict__"))
        self.assertIsInstance(result.safety_checks, tuple)
        self.assertFalse(hasattr(result.safety_checks[0], "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            result.receipt = None  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.safety_checks[0].allowed = False  # type: ignore[misc]

    def test_action_module_cannot_import_or_call_raw_input_boundary(self) -> None:
        source = inspect.getsource(action_module)
        tree = ast.parse(source)
        imported_modules = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }

        self.assertTrue(any(module.endswith("input_coordinator") for module in imported_modules))
        self.assertFalse(any(module.endswith("arduino") for module in imported_modules))
        for forbidden in (
            "._backend",
            ".connect(",
            ".arm(",
            ".move_to_absolute(",
            ".mouse_down(",
            ".mouse_up(",
            ".press(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
