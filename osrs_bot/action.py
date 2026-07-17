from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .input_coordinator import (
    ApprovedCameraHoldIntent,
    ApprovedCameraZoomIntent,
    ApprovedCursorRecoveryIntent,
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    CursorInvalidationCause,
    InputCoordinator,
    InputFailureKind,
    InputPurpose,
    InputReceipt,
    InputValidation,
    MouseButton,
    PointerActivation,
    PointerActivationDecision,
    RuneLiteGeometryEvidence,
)
from .model import (
    Action,
    ActionKind,
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    MenuEntry,
    Observation,
    ScreenBounds,
    ScreenPoint,
    WidgetTarget,
)
from .observability import (
    ObservabilityEvidence,
    TimingEvidence,
    TimingPhase,
    WaitState,
    safe_elapsed_millis,
)
from .pointer import gameplay_pointer_safe_bounds
from .safety import (
    POINTER_MATCH_TOLERANCE_PX,
    SafetyCheck,
    SafetyEvaluation,
    SafetyGate,
    SafetyResult,
)


TRANSIENT_POST_MOVE_RETRY_REASONS = frozenset({"observation_not_pass"})
TARGET_EVIDENCE_INVALIDATION_REASONS = frozenset(
    {
        "geometry_unavailable",
        "hover_menu_mismatch",
        "screen_point_missing",
        "screen_point_not_verified",
        "screen_point_outside_target",
        "settled_pointer_outside_fresh_region",
        "target_missing",
        "target_not_actionable",
        "target_not_visible",
        "target_offscreen",
    }
)


class _ActionBlocked(RuntimeError):
    pass


class UnsentActionDisposition(str, Enum):
    NONE = "none"
    TARGET_EVIDENCE_INVALIDATED = "target_evidence_invalidated"
    CURSOR_STATE_INVALIDATED = "cursor_state_invalidated"
    CAMERA_FRAMING_SATISFIED = "camera_framing_satisfied"


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Gameplay disposition plus the coordinator's immutable wire receipt."""

    action: Action
    pre_move_tick: int
    local_status: str
    local_reason: str
    post_move_tick: int | None = None
    receipt: InputReceipt | None = None
    safety_checks: tuple[SafetyCheck, ...] = ()
    unsent_disposition: UnsentActionDisposition = UnsentActionDisposition.NONE
    activation_attempted: bool = False
    observability: ObservabilityEvidence = ObservabilityEvidence()

    def __post_init__(self) -> None:
        if self.local_status not in {"BLOCKED", "ERROR", "NO_ACTION"}:
            raise ValueError("local_status must describe a non-coordinator result")
        if not isinstance(self.local_reason, str) or not self.local_reason.strip():
            raise ValueError("local_reason must be non-empty")
        if self.receipt is not None and not isinstance(self.receipt, InputReceipt):
            raise TypeError("receipt must be InputReceipt or None")
        if not isinstance(self.safety_checks, tuple) or not all(
            isinstance(check, SafetyCheck) for check in self.safety_checks
        ):
            raise TypeError("safety_checks must be a tuple of SafetyCheck values")
        if not isinstance(self.unsent_disposition, UnsentActionDisposition):
            raise TypeError(
                "unsent_disposition must be an UnsentActionDisposition"
            )
        if not isinstance(self.activation_attempted, bool):
            raise TypeError("activation_attempted must be bool")
        if not isinstance(self.observability, ObservabilityEvidence):
            raise TypeError("observability must be ObservabilityEvidence")
        if (
            self.activation_attempted
            and self.unsent_disposition is not UnsentActionDisposition.NONE
        ):
            raise ValueError(
                "an attempted activation cannot have an unsent disposition"
            )

    @property
    def status(self) -> str:
        receipt = self.receipt
        if receipt is None:
            return self.local_status
        if receipt.status == "PASS" and receipt.successful:
            return "SENT"
        if receipt.status == "BLOCKED":
            return "BLOCKED"
        # An ERROR receipt, or an internally inconsistent PASS receipt, always
        # overrides any successful action-layer validation.
        return "ERROR"

    @property
    def reason(self) -> str:
        receipt = self.receipt
        if receipt is None:
            return self.local_reason
        if receipt.status == "PASS" and receipt.successful:
            return "action_sent"
        if receipt.status == "PASS":
            return f"receipt_not_successful: {receipt.reason}"
        return receipt.reason

    @property
    def sent(self) -> bool:
        return self.status == "SENT"

    @property
    def stop_all_confirmed(self) -> bool:
        return bool(self.receipt and self.receipt.stop_all_acknowledged)

    @property
    def disarm_confirmed(self) -> bool:
        return bool(self.receipt and self.receipt.disarm_acknowledged)

    @property
    def cleanup_confirmed(self) -> bool:
        receipt = self.receipt
        return bool(
            receipt
            and receipt.stop_all_acknowledged
            and receipt.disarm_acknowledged
            and receipt.firmware_status_acknowledged
            and receipt.firmware_status is not None
            and receipt.firmware_status.safe
            and receipt.unresolved_command_count == 0
            and receipt.failed_command_count == 0
            and receipt.ack_missing_count == 0
            and receipt.ledger_complete
            and receipt.ledger_closed
            and receipt.backend_closed
            and all(command.successful for command in receipt.commands)
        )


@dataclass(frozen=True, slots=True)
class _PinnedPointerRecovery:
    geometry: RuneLiteGeometryEvidence
    observed_outer_bounds: ScreenBounds
    viewport_bounds: ScreenBounds


class CoordinatedActionInterface:
    """Submit gameplay intents through the sole automated-input owner.

    SafetyGate approves immutable task evidence before submission. Pointer and
    key callbacks then reobserve immediately before activation; this layer has
    no transport, session, or raw input access of its own.
    """

    def __init__(
        self,
        coordinator: InputCoordinator,
        safety: SafetyGate,
        observe: Callable[[], Observation],
        *,
        sleep: Callable[[float], None] = time.sleep,
        evidence_attempts: int = 12,
        evidence_delay_seconds: float = 0.1,
        evidence_clock: Callable[[], float] = time.monotonic,
        wait_state_observer: Callable[[WaitState | None], None] | None = None,
    ) -> None:
        if not callable(observe):
            raise TypeError("observe must be callable")
        self._coordinator = coordinator
        self._safety = safety
        self._observe = observe
        self._sleep = sleep
        self._evidence_attempts = max(1, evidence_attempts)
        self._evidence_delay_seconds = max(0.0, evidence_delay_seconds)
        if not callable(evidence_clock):
            raise TypeError("evidence_clock must be callable")
        if wait_state_observer is not None and not callable(wait_state_observer):
            raise TypeError("wait_state_observer must be callable or None")
        self._evidence_clock = evidence_clock
        self._wait_state_observer = wait_state_observer
        self._pinned_pointer_recovery: _PinnedPointerRecovery | None = None

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        safety_checks: list[SafetyCheck] = []
        timing = [TimingEvidence()]
        preflight_evaluation = self._timed_safety_evaluation(
            timing,
            self._safety.evaluate_pre_move,
            action,
            observation,
        )
        self._extend_safety_checks(safety_checks, preflight_evaluation)
        preflight = preflight_evaluation.result
        if not preflight.allowed:
            return self._local_result(
                action,
                observation,
                "BLOCKED",
                preflight.reason,
                safety_checks,
                observability=ObservabilityEvidence(timing=timing[0]),
            )

        if action.kind is ActionKind.WAIT:
            return self._local_result(
                action,
                observation,
                "NO_ACTION",
                "wait_action",
                safety_checks,
                observability=ObservabilityEvidence(timing=timing[0]),
            )

        if (
            self._pinned_pointer_recovery is not None
            and action.kind in {
                ActionKind.PRESS_KEY,
                ActionKind.CAMERA_HOLD,
                ActionKind.CAMERA_ZOOM,
            }
        ):
            # The one retry after cursor recovery must re-recognize a pointer
            # target and pass the exact HWND/native-geometry lane.  A key has
            # no point-owner proof and cannot safely stand in for that retry.
            try:
                self._required_pinned_pointer_binding(observation)
            except (TypeError, ValueError) as error:
                return self._local_result(
                    action,
                    observation,
                    "BLOCKED",
                    str(error),
                    safety_checks,
                    observability=ObservabilityEvidence(timing=timing[0]),
                )
            return self._local_result(
                action,
                observation,
                "BLOCKED",
                "cursor_recovery_retry_requires_pointer_target",
                safety_checks,
                observability=ObservabilityEvidence(timing=timing[0]),
            )

        last_observation: list[Observation | None] = [None]
        unsent_disposition = UnsentActionDisposition.NONE
        activation_attempted = False
        try:
            if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
                (
                    receipt,
                    unsent_disposition,
                    activation_attempted,
                ) = self._execute_adaptive_target(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
                    timing,
                )
            elif action.kind is ActionKind.CLICK_WIDGET:
                receipt = self._execute_direct_pointer(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
                    timing,
                )
                activation_attempted = self._command_may_have_taken_effect(
                    receipt,
                    "MOUSE_DOWN",
                )
            elif action.kind is ActionKind.PRESS_KEY:
                receipt = self._execute_key(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
                    timing,
                )
                activation_attempted = self._command_may_have_taken_effect(
                    receipt,
                    "KEY_PRESS",
                )
            elif action.kind is ActionKind.CAMERA_HOLD:
                receipt = self._execute_camera_hold(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
                    timing,
                )
                activation_attempted = self._command_may_have_taken_effect(
                    receipt,
                    "CAMERA_HOLD",
                )
            elif action.kind is ActionKind.CAMERA_ZOOM:
                receipt = self._execute_camera_zoom(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
                    timing,
                )
                activation_attempted = self._command_may_have_taken_effect(
                    receipt,
                    "WHEEL",
                )
            else:
                raise ValueError(f"unsupported live action: {action.kind.value}")
            if not isinstance(receipt, InputReceipt):
                raise TypeError("InputCoordinator returned no immutable InputReceipt")
            self._remember_completed_reacquisition(observation, receipt)
            if activation_attempted and action.post_action_delay_seconds > 0.0:
                self._sleep(action.post_action_delay_seconds)
        except Exception as error:  # fail closed before or at the coordinator API
            return ExecutionResult(
                action=action,
                pre_move_tick=observation.tick,
                local_status="ERROR",
                local_reason=f"{type(error).__name__}: {error}",
                post_move_tick=(
                    last_observation[0].tick
                    if last_observation[0] is not None
                    else None
                ),
                safety_checks=tuple(safety_checks),
                observability=ObservabilityEvidence(timing=timing[0]),
            )

        if (
            not activation_attempted
            and receipt.failure_kind
            is InputFailureKind.CURSOR_STATE_INVALIDATED
        ):
            unsent_disposition = (
                UnsentActionDisposition.CURSOR_STATE_INVALIDATED
            )
        elif (
            not activation_attempted
            and action.kind is ActionKind.CAMERA_HOLD
            and action.task_constraints.camera is not None
            and receipt.status == "BLOCKED"
            and receipt.reason.endswith("camera_projection_already_actionable")
        ):
            # Fresh pre-key geometry can enter the desired framing region
            # after the proposal was made.  No key was sent, so reobserve and
            # replan instead of converting a satisfied camera objective into
            # a terminal task failure.
            unsent_disposition = (
                UnsentActionDisposition.CAMERA_FRAMING_SATISFIED
            )

        receipt_observability = getattr(
            receipt,
            "observability",
            ObservabilityEvidence(),
        )
        combined_timing = timing[0]
        try:
            combined_timing = combined_timing.merge(
                receipt_observability.timing
            )
        except Exception:
            # Receipt diagnostics cannot alter an already completed input result.
            pass
        combined_observability = ObservabilityEvidence(
            timing=combined_timing,
            observed_wait_states=receipt_observability.observed_wait_states,
        )
        return ExecutionResult(
            action=action,
            pre_move_tick=observation.tick,
            local_status="ERROR",
            local_reason="coordinator_receipt_unavailable",
            post_move_tick=(
                last_observation[0].tick
                if last_observation[0] is not None
                else None
            ),
            receipt=receipt,
            safety_checks=tuple(safety_checks),
            unsent_disposition=unsent_disposition,
            activation_attempted=activation_attempted,
            observability=combined_observability,
        )

    def recover_cursor(
        self,
        observation: Observation,
        invalidated_receipt: InputReceipt,
    ) -> InputReceipt:
        """Run one geometry-only Arduino MOVE recovery for a discarded intent."""

        if not isinstance(observation, Observation):
            raise TypeError("observation must be an Observation")
        if not isinstance(invalidated_receipt, InputReceipt):
            raise TypeError("invalidated_receipt must be an InputReceipt")
        if (
            invalidated_receipt.failure_kind
            is not InputFailureKind.CURSOR_STATE_INVALIDATED
        ):
            raise ValueError("cursor recovery requires a typed invalidation receipt")
        cause = invalidated_receipt.cursor_invalidation_cause
        if cause is None or not cause.recovery_eligible:
            raise ValueError("cursor invalidation cause is not recovery eligible")
        if (
            invalidated_receipt.status != "BLOCKED"
            or not self._receipt_cleanup_confirmed(invalidated_receipt)
            or any(
                command.command in {"MOUSE_DOWN", "MOUSE_UP", "KEY_PRESS"}
                for command in invalidated_receipt.commands
            )
        ):
            raise ValueError(
                "cursor recovery requires complete pre-activation cleanup"
            )
        geometry = invalidated_receipt.pointer_geometry
        if geometry is None:
            raise ValueError("cursor recovery requires pinned native geometry")
        process_id = self._required_pid(observation)
        canvas = self._required_canvas(observation)
        viewport = self._required_viewport(observation)
        observed_outer = self._required_outer_window(observation)
        if process_id != geometry.expected_pid:
            raise ValueError("RuneLite PID changed before cursor recovery")
        if canvas != geometry.canvas_bounds:
            raise ValueError("RuneLite canvas geometry changed before cursor recovery")
        if not self._outer_quantization_compatible(
            observed_outer,
            geometry.outer_bounds,
        ):
            raise ValueError("RuneLite outer geometry changed before cursor recovery")
        safe_bounds = gameplay_pointer_safe_bounds(viewport)
        receipt = self._coordinator.execute_cursor_reacquisition(
            ApprovedCursorRecoveryIntent(
                recovery_id=(
                    f"cursor-recovery-{invalidated_receipt.transaction_id}"
                ),
                expected_pid=process_id,
                expected_hwnd=geometry.expected_hwnd,
                expected_outer_bounds=observed_outer,
                expected_native_outer_bounds=geometry.outer_bounds,
                expected_native_client_bounds=geometry.client_bounds,
                canvas_bounds=canvas,
                viewport_bounds=viewport,
                pointer_safe_bounds=safe_bounds,
            )
        )
        self._remember_completed_reacquisition(observation, receipt)
        return receipt

    def _execute_direct_pointer(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> InputReceipt:
        intent = self._pointer_intent(action, observation)
        if action.pre_move_delay_seconds > 0.0:
            self._sleep(action.pre_move_delay_seconds)

        def validate(
            _intent: ApprovedPointerIntent,
            actual_point: ScreenPoint,
        ) -> InputValidation:
            if action.settle_delay_seconds > 0.0:
                self._sleep(action.settle_delay_seconds)
            post, result, _ = self._await_post_move(
                action,
                {
                    "menu_sample_not_newer",
                    "hover_pointer_mismatch",
                    "hover_menu_mismatch",
                },
                safety_checks,
                timing,
                settled_pointer=actual_point,
            )
            if result.allowed and action.pre_click_delay_seconds > 0.0:
                self._sleep(action.pre_click_delay_seconds)
                post, result, _ = self._await_post_move(
                    action,
                    {
                        "menu_sample_not_newer",
                        "hover_pointer_mismatch",
                        "hover_menu_mismatch",
                    },
                    safety_checks,
                    timing,
                    settled_pointer=actual_point,
                )
            last_observation[0] = post
            return self._input_validation(result)

        return self._coordinator.execute_pointer(intent, validate=validate)

    def _execute_adaptive_target(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> tuple[InputReceipt, UnsentActionDisposition, bool]:
        intent = self._pointer_intent(action, observation)
        if action.pre_move_delay_seconds > 0.0:
            self._sleep(action.pre_move_delay_seconds)
        canvas = self._required_canvas(observation)
        context_minimum_tick: list[int | None] = [None]
        row_minimum_tick: list[int | None] = [None]
        validated_action: list[Action | None] = [None]
        selected_activation: list[PointerActivation | None] = [None]
        target_evidence_invalidated = [False]

        def decide_activation(
            _intent: ApprovedPointerIntent,
            actual_point: ScreenPoint,
        ) -> PointerActivationDecision:
            validated_action[0] = action
            if action.settle_delay_seconds > 0.0:
                self._sleep(action.settle_delay_seconds)
            post, hover, context = self._await_post_move(
                action,
                {
                    "menu_sample_not_newer",
                    "hover_pointer_mismatch",
                    "hover_menu_mismatch",
                },
                safety_checks,
                timing,
                settled_pointer=actual_point,
            )
            if (
                (hover.allowed or context.allowed)
                and action.pre_click_delay_seconds > 0.0
            ):
                self._sleep(action.pre_click_delay_seconds)
                post, hover, context = self._await_post_move(
                    action,
                    {
                        "menu_sample_not_newer",
                        "hover_pointer_mismatch",
                        "hover_menu_mismatch",
                    },
                    safety_checks,
                    timing,
                    settled_pointer=actual_point,
                )
            last_observation[0] = post
            if hover.allowed:
                selected_activation[0] = PointerActivation.DIRECT_LEFT
                return PointerActivationDecision.direct(hover.reason)
            if context.allowed:
                if post.menu_client_tick is None:
                    return PointerActivationDecision.deny("menu_sample_missing")
                context_minimum_tick[0] = post.menu_client_tick
                selected_activation[0] = PointerActivation.CONTEXT_MENU
                return PointerActivationDecision.context(context.reason)
            if hover.reason in TARGET_EVIDENCE_INVALIDATION_REASONS:
                target_evidence_invalidated[0] = True
            return PointerActivationDecision.deny(hover.reason)

        def resolve_row() -> ApprovedPointerIntent:
            minimum_tick = context_minimum_tick[0]
            if minimum_tick is None:
                raise _ActionBlocked("menu_sample_missing")
            canonical_action = validated_action[0]
            if canonical_action is None:
                raise _ActionBlocked("actual_pointer_sample_missing")
            opened, result = self._await_context_menu(
                canonical_action,
                minimum_tick=minimum_tick,
                safety_checks=safety_checks,
                timing=timing,
            )
            last_observation[0] = opened
            if not result.allowed:
                raise _ActionBlocked(result.reason)
            entry = self._exact_context_entry(canonical_action, opened)
            if entry is None or entry.row_bounds is None:
                raise _ActionBlocked("context_row_bounds_missing")
            if opened.menu_client_tick is None:
                raise _ActionBlocked("menu_sample_missing")
            point = entry.row_bounds.center
            safe_bounds = self._required_pointer_safe_bounds(opened)
            if not safe_bounds.contains(point):
                raise _ActionBlocked("context_row_outside_pointer_safe_bounds")
            row_minimum_tick[0] = opened.menu_client_tick
            return ApprovedPointerIntent(
                intent_id=self._intent_id(action, "context-row"),
                purpose=InputPurpose.CONTEXT_ROW,
                target=point,
                movement_bounds=safe_bounds,
                target_bounds=self._bounded_target_region(
                    entry.row_bounds,
                    point,
                    safe_bounds,
                ),
                motion_target_bounds=self._canvas_intersection(
                    entry.row_bounds, safe_bounds, point
                ),
                expected_pid=self._required_pid(opened),
                button=MouseButton.LEFT,
                canvas_bounds=canvas,
                viewport_bounds=self._required_viewport(opened),
                motion_seed=canonical_action.behavior_seed,
                motion_decision_id=(
                    f"{canonical_action.decision_id}:context-row"
                    if canonical_action.decision_id is not None
                    else None
                ),
                motion_context="context_row",
            )

        def validate_row(
            row_intent: ApprovedPointerIntent,
            actual_point: ScreenPoint,
        ) -> InputValidation:
            minimum_tick = row_minimum_tick[0]
            if minimum_tick is None:
                return InputValidation.deny("menu_sample_missing")
            canonical_action = validated_action[0]
            if canonical_action is None:
                return InputValidation.deny("actual_pointer_sample_missing")
            row_observation, result = self._await_context_menu(
                canonical_action,
                minimum_tick=minimum_tick,
                row_point=actual_point,
                safety_checks=safety_checks,
                timing=timing,
            )
            last_observation[0] = row_observation
            return self._input_validation(result)

        receipt = self._coordinator.execute_adaptive_pointer(
            intent,
            decide_activation=decide_activation,
            resolve_row=resolve_row,
            validate_row=validate_row,
        )
        disposition = (
            UnsentActionDisposition.TARGET_EVIDENCE_INVALIDATED
            if target_evidence_invalidated[0] and receipt.status == "BLOCKED"
            else UnsentActionDisposition.NONE
        )
        required_mouse_downs = (
            1
            if selected_activation[0] is PointerActivation.DIRECT_LEFT
            else (
                2
                if selected_activation[0] is PointerActivation.CONTEXT_MENU
                else 0
            )
        )
        activation_attempted = bool(
            required_mouse_downs
            and self._command_write_count(receipt, "MOUSE_DOWN")
            >= required_mouse_downs
        )
        return receipt, disposition, activation_attempted

    @staticmethod
    def _command_write_count(receipt: InputReceipt, command: str) -> int:
        return sum(
            evidence.command == command
            and evidence.write_ok
            and evidence.status != "REJECTED"
            for evidence in receipt.commands
        )

    @classmethod
    def _command_may_have_taken_effect(
        cls,
        receipt: InputReceipt,
        command: str,
    ) -> bool:
        return cls._command_write_count(receipt, command) > 0

    def _execute_key(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> InputReceipt:
        if not action.key:
            raise ValueError("press_key action has no key")
        if action.pre_move_delay_seconds > 0.0:
            self._sleep(action.pre_move_delay_seconds)
        intent = ApprovedKeyIntent(
            intent_id=self._intent_id(action, "key"),
            purpose=InputPurpose.GAMEPLAY_KEY,
            key=action.key,
            expected_pid=self._required_pid(observation),
            hold_millis=action.key_hold_millis,
        )
        interface = action.task_constraints.interface
        if interface is not None and interface.require_keyboard_close:
            retry_reason = "interface_sample_not_newer"
        elif action.task_constraints.camera is not None:
            retry_reason = "camera_sample_not_newer"
        else:
            retry_reason = "dialogue_sample_not_newer"

        def validate(_intent: ApprovedKeyIntent) -> InputValidation:
            post, result, _ = self._await_post_move(
                action, {retry_reason}, safety_checks, timing
            )
            last_observation[0] = post
            return self._input_validation(result)

        return self._coordinator.execute_key(intent, validate=validate)

    def _execute_camera_hold(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> InputReceipt:
        constraint = action.task_constraints.camera
        if constraint is None:
            raise ValueError("camera_hold action has no camera constraint")
        if action.pre_move_delay_seconds > 0.0:
            self._sleep(action.pre_move_delay_seconds)
        intent = ApprovedCameraHoldIntent(
            intent_id=self._intent_id(action, "camera-hold"),
            purpose=InputPurpose.CAMERA_HOLD,
            direction=constraint.direction,
            expected_pid=self._required_pid(observation),
            hold_millis=constraint.hold_millis,
            before_yaw=constraint.before_yaw,
            before_pitch=constraint.before_pitch,
            before_zoom=observation.camera_zoom,
            source_geometry_frame_id=constraint.source_geometry_frame_id,
        )

        def validate(_intent: ApprovedCameraHoldIntent) -> InputValidation:
            post, result, _ = self._await_post_move(
                action,
                {"camera_sample_not_newer"},
                safety_checks,
                timing,
            )
            last_observation[0] = post
            return self._input_validation(result)

        return self._coordinator.execute_camera_hold(intent, validate=validate)

    def _execute_camera_zoom(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> InputReceipt:
        constraint = action.task_constraints.camera_zoom
        if constraint is None:
            raise ValueError("camera_zoom action has no camera zoom constraint")
        if action.pre_move_delay_seconds > 0.0:
            self._sleep(action.pre_move_delay_seconds)
        canvas = self._required_canvas(observation)
        viewport = self._required_viewport(observation)
        outer = self._required_outer_window(observation)
        expected_hwnd, native_outer, native_client = (
            self._required_pinned_pointer_binding(observation)
        )
        intent = ApprovedCameraZoomIntent(
            intent_id=self._intent_id(action, "camera-zoom"),
            purpose=InputPurpose.CAMERA_ZOOM,
            amount=constraint.amount,
            expected_pid=self._required_pid(observation),
            expected_hwnd=expected_hwnd,
            expected_outer_bounds=outer,
            expected_native_outer_bounds=native_outer,
            expected_native_client_bounds=native_client,
            canvas_bounds=canvas,
            viewport_bounds=viewport,
            pointer_safe_bounds=gameplay_pointer_safe_bounds(viewport),
            before_yaw=constraint.before_yaw,
            before_pitch=constraint.before_pitch,
            before_zoom=constraint.before_zoom,
            source_geometry_frame_id=constraint.source_geometry_frame_id,
        )

        def validate(_intent: ApprovedCameraZoomIntent) -> InputValidation:
            post, result, _ = self._await_post_move(
                action,
                {"camera_sample_not_newer"},
                safety_checks,
                timing,
            )
            last_observation[0] = post
            return self._input_validation(result)

        return self._coordinator.execute_camera_zoom(intent, validate=validate)

    def _pointer_intent(
        self,
        action: Action,
        observation: Observation,
    ) -> ApprovedPointerIntent:
        if action.screen_point is None:
            raise ValueError("pointer action has no verified screen point")
        canvas = self._required_canvas(observation)
        viewport = self._required_viewport(observation)
        safe_bounds = gameplay_pointer_safe_bounds(viewport)
        outer_window = self._required_outer_window(observation)
        (
            expected_hwnd,
            expected_native_outer,
            expected_native_client,
        ) = self._required_pinned_pointer_binding(observation)
        purpose = (
            InputPurpose.GAMEPLAY_WIDGET
            if action.kind is ActionKind.CLICK_WIDGET
            else InputPurpose.GAMEPLAY_OBJECT
        )
        target_geometry_bounds = self._target_bounds(action, observation)
        return ApprovedPointerIntent(
            intent_id=self._intent_id(action, "target"),
            purpose=purpose,
            target=action.screen_point,
            movement_bounds=safe_bounds,
            target_bounds=self._bounded_target_region(
                target_geometry_bounds,
                action.screen_point,
                safe_bounds,
            ),
            expected_pid=self._required_pid(observation),
            expected_hwnd=expected_hwnd,
            button=MouseButton.LEFT,
            reacquisition_bounds=outer_window,
            canvas_bounds=canvas,
            viewport_bounds=viewport,
            expected_native_outer_bounds=expected_native_outer,
            expected_native_client_bounds=expected_native_client,
            motion_target_bounds=(
                self._canvas_intersection(
                    target_geometry_bounds,
                    safe_bounds,
                    action.screen_point,
                )
                if target_geometry_bounds is not None
                else None
            ),
            motion_seed=action.behavior_seed,
            motion_decision_id=action.decision_id,
            motion_context=(
                "walk"
                if action.kind is ActionKind.WALK
                else (
                    "widget"
                    if action.kind is ActionKind.CLICK_WIDGET
                    else "object"
                )
            ),
        )

    @staticmethod
    def _intent_id(action: Action, suffix: str) -> str:
        return f"gameplay-{action.source_tick}-{action.kind.value}-{suffix}"

    @staticmethod
    def _required_canvas(observation: Observation) -> ScreenBounds:
        if observation.canvas_bounds is None:
            raise ValueError("canvas bounds unavailable")
        return observation.canvas_bounds

    @staticmethod
    def _required_viewport(observation: Observation) -> ScreenBounds:
        if observation.viewport_bounds is None:
            raise ValueError("authoritative viewport bounds unavailable")
        return observation.viewport_bounds

    @staticmethod
    def _required_outer_window(observation: Observation) -> ScreenBounds:
        if observation.client_window_bounds is None:
            raise ValueError(
                "RuneLite outer window geometry unavailable for pointer recovery"
            )
        return observation.client_window_bounds

    @staticmethod
    def _outer_quantization_compatible(
        observed: ScreenBounds,
        native: ScreenBounds,
    ) -> bool:
        return bool(
            observed.width == native.width
            and observed.height == native.height
            and abs(observed.x - native.x) <= 1
            and abs(observed.y - native.y) <= 1
        )

    def _required_pointer_safe_bounds(
        self,
        observation: Observation,
    ) -> ScreenBounds:
        self._required_pinned_pointer_binding(observation)
        return gameplay_pointer_safe_bounds(self._required_viewport(observation))

    def _required_pinned_pointer_binding(
        self,
        observation: Observation,
    ) -> tuple[int | None, ScreenBounds | None, ScreenBounds | None]:
        pinned = self._pinned_pointer_recovery
        if pinned is None:
            return None, None, None
        geometry = pinned.geometry
        if self._required_pid(observation) != geometry.expected_pid:
            raise ValueError("RuneLite PID changed after cursor recovery")
        if self._required_canvas(observation) != geometry.canvas_bounds:
            raise ValueError("RuneLite canvas geometry changed after cursor recovery")
        if self._required_viewport(observation) != pinned.viewport_bounds:
            raise ValueError("RuneLite viewport geometry changed after cursor recovery")
        if self._required_outer_window(observation) != pinned.observed_outer_bounds:
            raise ValueError("RuneLite outer geometry changed after cursor recovery")
        return (
            geometry.expected_hwnd,
            geometry.outer_bounds,
            geometry.client_bounds,
        )

    def _remember_completed_reacquisition(
        self,
        observation: Observation,
        receipt: InputReceipt,
    ) -> None:
        evidence = receipt.cursor_reacquisition
        if evidence is None or not evidence.completed:
            return
        geometry = receipt.pointer_geometry
        if (
            receipt.status != "BLOCKED"
            or receipt.failure_kind
            is not InputFailureKind.CURSOR_STATE_INVALIDATED
            or receipt.cursor_invalidation_cause
            is not CursorInvalidationCause.CURSOR_REACQUIRED
            or not self._receipt_cleanup_confirmed(receipt)
            or geometry is None
            or evidence.before_geometry != geometry
            or evidence.after_geometry != geometry
            or not evidence.geometry_unchanged
            or not evidence.no_activation_sent
        ):
            return
        try:
            process_id = self._required_pid(observation)
            canvas = self._required_canvas(observation)
            viewport = self._required_viewport(observation)
            observed_outer = self._required_outer_window(observation)
        except (TypeError, ValueError):
            return
        if (
            process_id != geometry.expected_pid
            or canvas != geometry.canvas_bounds
            or not self._outer_quantization_compatible(
                observed_outer,
                geometry.outer_bounds,
            )
        ):
            return
        self._pinned_pointer_recovery = _PinnedPointerRecovery(
            geometry=geometry,
            observed_outer_bounds=observed_outer,
            viewport_bounds=viewport,
        )

    @staticmethod
    def _receipt_cleanup_confirmed(receipt: InputReceipt) -> bool:
        return bool(
            receipt.stop_all_acknowledged
            and receipt.disarm_acknowledged
            and receipt.firmware_status_acknowledged
            and receipt.firmware_status is not None
            and receipt.firmware_status.safe
            and receipt.unresolved_command_count == 0
            and receipt.failed_command_count == 0
            and receipt.ack_missing_count == 0
            and receipt.ledger_complete
            and receipt.ledger_closed
            and receipt.backend_closed
            and all(command.successful for command in receipt.commands)
        )

    @staticmethod
    def _required_pid(observation: Observation) -> int:
        process_id = observation.client_process_id
        if process_id is None or process_id <= 0:
            raise ValueError("client process unavailable")
        return process_id

    @classmethod
    def _target_bounds(
        cls,
        action: Action,
        observation: Observation,
    ) -> ScreenBounds | None:
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            target = observation.object_by_key(action.target_key)
            return target.geometry.screen_bounds if target is not None else None
        if action.kind is ActionKind.CLICK_WIDGET:
            widget = cls._selected_widget(action, observation)
            return widget.screen_bounds if widget is not None else None
        return None

    @staticmethod
    def _selected_widget(
        action: Action,
        observation: Observation,
    ) -> WidgetTarget | None:
        widgets = {
            DEPOSIT_INVENTORY_WIDGET_KEY: observation.widgets.deposit_inventory,
            CLOSE_BANK_WIDGET_KEY: observation.widgets.close_bank,
        }
        if action.target_key in widgets:
            return widgets[action.target_key]
        if action.target_key is not None:
            return None
        matches = tuple(
            widget
            for widget in widgets.values()
            if widget is not None and widget.name == action.target_name
        )
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _bounded_target_region(
        target_bounds: ScreenBounds | None,
        point: ScreenPoint,
        canvas: ScreenBounds,
    ) -> ScreenBounds:
        if target_bounds is None:
            return ScreenBounds(point.x, point.y, 1, 1)
        left = max(
            target_bounds.x,
            canvas.x,
            point.x - POINTER_MATCH_TOLERANCE_PX,
        )
        top = max(
            target_bounds.y,
            canvas.y,
            point.y - POINTER_MATCH_TOLERANCE_PX,
        )
        right = min(
            target_bounds.x + target_bounds.width,
            canvas.x + canvas.width,
            point.x + POINTER_MATCH_TOLERANCE_PX + 1,
        )
        bottom = min(
            target_bounds.y + target_bounds.height,
            canvas.y + canvas.height,
            point.y + POINTER_MATCH_TOLERANCE_PX + 1,
        )
        if right <= left or bottom <= top:
            raise ValueError("verified target bounds do not intersect canvas")
        bounded = ScreenBounds(left, top, right - left, bottom - top)
        if not bounded.contains(point):
            raise ValueError("verified target point is outside bounded target region")
        return bounded

    @staticmethod
    def _canvas_intersection(
        target_bounds: ScreenBounds,
        canvas: ScreenBounds,
        point: ScreenPoint,
    ) -> ScreenBounds:
        left = max(target_bounds.x, canvas.x)
        top = max(target_bounds.y, canvas.y)
        right = min(
            target_bounds.x + target_bounds.width,
            canvas.x + canvas.width,
        )
        bottom = min(
            target_bounds.y + target_bounds.height,
            canvas.y + canvas.height,
        )
        if right <= left or bottom <= top:
            raise ValueError("target bounds do not intersect canvas")
        clipped = ScreenBounds(left, top, right - left, bottom - top)
        if not clipped.contains(point):
            raise ValueError("target point is outside clipped target bounds")
        return clipped

    @staticmethod
    def _input_validation(result: SafetyResult) -> InputValidation:
        if result.allowed:
            return InputValidation.allow(result.reason)
        return InputValidation.deny(result.reason)

    def _await_post_move(
        self,
        action: Action,
        retry_reasons: set[str],
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> tuple[Observation, SafetyResult, SafetyResult]:
        bounded_retry_reasons = (
            set(retry_reasons) | TRANSIENT_POST_MOVE_RETRY_REASONS
        )
        observation = self._timed_observe(timing)
        result_evaluation = self._timed_safety_evaluation(
            timing,
            self._safety.evaluate_post_move,
            action,
            observation,
            settled_pointer=settled_pointer,
        )
        context_evaluation = self._timed_safety_evaluation(
            timing,
            self._safety.evaluate_context_candidate,
            action,
            observation,
            settled_pointer=settled_pointer,
        )
        self._extend_safety_checks(safety_checks, result_evaluation)
        self._extend_safety_checks(safety_checks, context_evaluation)
        result = result_evaluation.result
        context = context_evaluation.result
        for _ in range(1, self._evidence_attempts):
            if (
                result.allowed
                or context.allowed
                or result.reason not in bounded_retry_reasons
            ):
                break
            wait_started = self._evidence_now()
            self._notify_wait_state(self._observation_wait_state(observation))
            try:
                self._sleep(self._evidence_delay_seconds)
                observation = self._timed_observe(timing)
            finally:
                self._record_timing(
                    timing,
                    TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT,
                    safe_elapsed_millis(
                        wait_started,
                        self._evidence_now(),
                    ),
                )
            result_evaluation = self._timed_safety_evaluation(
                timing,
                self._safety.evaluate_post_move,
                action,
                observation,
                settled_pointer=settled_pointer,
            )
            context_evaluation = self._timed_safety_evaluation(
                timing,
                self._safety.evaluate_context_candidate,
                action,
                observation,
                settled_pointer=settled_pointer,
            )
            self._extend_safety_checks(safety_checks, result_evaluation)
            self._extend_safety_checks(safety_checks, context_evaluation)
            result = result_evaluation.result
            context = context_evaluation.result
        self._notify_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
        return observation, result, context

    def _await_context_menu(
        self,
        action: Action,
        *,
        minimum_tick: int,
        row_point: ScreenPoint | None = None,
        safety_checks: list[SafetyCheck],
        timing: list[TimingEvidence],
    ) -> tuple[Observation, SafetyResult]:
        retry_reasons = {
            "menu_sample_not_newer",
            "context_menu_not_open",
            "context_row_pointer_mismatch",
        } | TRANSIENT_POST_MOVE_RETRY_REASONS
        observation = self._timed_observe(timing)
        evaluation = self._timed_safety_evaluation(
            timing,
            self._safety.evaluate_context_menu,
            action,
            observation,
            minimum_menu_client_tick=minimum_tick,
            row_point=row_point,
        )
        self._extend_safety_checks(safety_checks, evaluation)
        result = evaluation.result
        for _ in range(1, self._evidence_attempts):
            if result.allowed or result.reason not in retry_reasons:
                break
            wait_started = self._evidence_now()
            self._notify_wait_state(self._observation_wait_state(observation))
            try:
                self._sleep(self._evidence_delay_seconds)
                observation = self._timed_observe(timing)
            finally:
                self._record_timing(
                    timing,
                    TimingPhase.SOURCE_COHERENCE_FRESHNESS_WAIT,
                    safe_elapsed_millis(
                        wait_started,
                        self._evidence_now(),
                    ),
                )
            evaluation = self._timed_safety_evaluation(
                timing,
                self._safety.evaluate_context_menu,
                action,
                observation,
                minimum_menu_client_tick=minimum_tick,
                row_point=row_point,
            )
            self._extend_safety_checks(safety_checks, evaluation)
            result = evaluation.result
        self._notify_wait_state(WaitState.INPUT_TRANSACTION_BUSY)
        return observation, result

    def _timed_safety_evaluation(
        self,
        timing: list[TimingEvidence],
        evaluate: Callable[..., SafetyEvaluation],
        *args: object,
        **kwargs: object,
    ) -> SafetyEvaluation:
        started = self._evidence_now()
        try:
            return evaluate(*args, **kwargs)
        finally:
            finished = self._evidence_now()
            self._record_timing(
                timing,
                TimingPhase.SAFETY_GATE_EVALUATION,
                safe_elapsed_millis(started, finished),
            )

    def _timed_observe(
        self,
        timing: list[TimingEvidence],
    ) -> Observation:
        started = self._evidence_now()
        try:
            return self._observe()
        finally:
            self._record_timing(
                timing,
                TimingPhase.OBSERVATION_REQUEST_FETCH,
                safe_elapsed_millis(started, self._evidence_now()),
            )

    @staticmethod
    def _record_timing(
        timing: list[TimingEvidence],
        phase: TimingPhase,
        duration_millis: int,
    ) -> None:
        try:
            timing[0] = timing[0].record(phase, duration_millis)
        except Exception:
            return

    def _evidence_now(self) -> object:
        try:
            return self._evidence_clock()
        except Exception:
            return float("nan")

    def _notify_wait_state(self, state: WaitState | None) -> None:
        observer = self._wait_state_observer
        if observer is None:
            return
        try:
            observer(state)
        except Exception:
            # Presentation evidence is diagnostic and cannot influence input.
            return

    @staticmethod
    def _observation_wait_state(observation: Observation) -> WaitState:
        if not observation.source_coherent:
            return WaitState.WAITING_FOR_SOURCE_COHERENCE
        return WaitState.WAITING_FOR_NEXT_SCENE_UPDATE

    @staticmethod
    def _exact_context_entry(
        action: Action,
        observation: Observation,
    ) -> MenuEntry | None:
        if action.kind is ActionKind.WALK:
            matches = [
                entry
                for entry in observation.menus
                if entry.option == action.option and entry.entry_type == "WALK"
            ]
        else:
            matches = [
                entry
                for entry in observation.menus
                if entry.option == action.option
                and entry.target == action.target_name
                and entry.identifier == action.target_id
                and entry.param0 == action.target_param0
                and entry.param1 == action.target_param1
            ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _extend_safety_checks(
        destination: list[SafetyCheck], evaluation: SafetyEvaluation
    ) -> None:
        destination.extend(evaluation.checks)

    @staticmethod
    def _local_result(
        action: Action,
        observation: Observation,
        status: str,
        reason: str,
        safety_checks: list[SafetyCheck],
        *,
        observability: ObservabilityEvidence = ObservabilityEvidence(),
    ) -> ExecutionResult:
        return ExecutionResult(
            action=action,
            pre_move_tick=observation.tick,
            local_status=status,
            local_reason=reason,
            safety_checks=tuple(safety_checks),
            observability=observability,
        )
