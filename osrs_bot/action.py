from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .input_coordinator import (
    ApprovedKeyIntent,
    ApprovedPointerIntent,
    InputCoordinator,
    InputFailureKind,
    InputPurpose,
    InputReceipt,
    InputValidation,
    MouseButton,
    PointerActivation,
    PointerActivationDecision,
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
    ) -> None:
        if not callable(observe):
            raise TypeError("observe must be callable")
        self._coordinator = coordinator
        self._safety = safety
        self._observe = observe
        self._sleep = sleep
        self._evidence_attempts = max(1, evidence_attempts)
        self._evidence_delay_seconds = max(0.0, evidence_delay_seconds)

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        safety_checks: list[SafetyCheck] = []
        preflight_evaluation = self._safety.evaluate_pre_move(action, observation)
        self._extend_safety_checks(safety_checks, preflight_evaluation)
        preflight = preflight_evaluation.result
        if not preflight.allowed:
            return self._local_result(
                action,
                observation,
                "BLOCKED",
                preflight.reason,
                safety_checks,
            )

        if action.kind is ActionKind.WAIT:
            return self._local_result(
                action,
                observation,
                "NO_ACTION",
                "wait_action",
                safety_checks,
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
                )
            elif action.kind is ActionKind.CLICK_WIDGET:
                receipt = self._execute_direct_pointer(
                    action,
                    observation,
                    last_observation,
                    safety_checks,
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
                )
                activation_attempted = self._command_may_have_taken_effect(
                    receipt,
                    "KEY_PRESS",
                )
            else:
                raise ValueError(f"unsupported live action: {action.kind.value}")
            if not isinstance(receipt, InputReceipt):
                raise TypeError("InputCoordinator returned no immutable InputReceipt")
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
            )

        if (
            not activation_attempted
            and receipt.failure_kind
            is InputFailureKind.CURSOR_STATE_INVALIDATED
        ):
            unsent_disposition = (
                UnsentActionDisposition.CURSOR_STATE_INVALIDATED
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
        )

    def _execute_direct_pointer(
        self,
        action: Action,
        observation: Observation,
        last_observation: list[Observation | None],
        safety_checks: list[SafetyCheck],
    ) -> InputReceipt:
        intent = self._pointer_intent(action, observation)

        def validate(
            _intent: ApprovedPointerIntent,
            actual_point: ScreenPoint,
        ) -> InputValidation:
            post, result, _ = self._await_post_move(
                action,
                {
                    "menu_sample_not_newer",
                    "hover_pointer_mismatch",
                    "hover_menu_mismatch",
                },
                safety_checks,
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
    ) -> tuple[InputReceipt, UnsentActionDisposition, bool]:
        intent = self._pointer_intent(action, observation)
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
            post, hover, context = self._await_post_move(
                action,
                {
                    "menu_sample_not_newer",
                    "hover_pointer_mismatch",
                    "hover_menu_mismatch",
                },
                safety_checks,
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
            if not canvas.contains(point):
                raise _ActionBlocked("context_row_outside_canvas")
            row_minimum_tick[0] = opened.menu_client_tick
            return ApprovedPointerIntent(
                intent_id=self._intent_id(action, "context-row"),
                purpose=InputPurpose.CONTEXT_ROW,
                target=point,
                movement_bounds=canvas,
                target_bounds=self._bounded_target_region(
                    entry.row_bounds,
                    point,
                    canvas,
                ),
                expected_pid=self._required_pid(opened),
                button=MouseButton.LEFT,
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
    ) -> InputReceipt:
        if not action.key:
            raise ValueError("press_key action has no key")
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
                action, {retry_reason}, safety_checks
            )
            last_observation[0] = post
            return self._input_validation(result)

        return self._coordinator.execute_key(intent, validate=validate)

    def _pointer_intent(
        self,
        action: Action,
        observation: Observation,
    ) -> ApprovedPointerIntent:
        if action.screen_point is None:
            raise ValueError("pointer action has no verified screen point")
        canvas = self._required_canvas(observation)
        outer_window = observation.client_window_bounds
        if outer_window is None:
            raise ValueError(
                "RuneLite outer window geometry unavailable for pointer recovery"
            )
        purpose = (
            InputPurpose.GAMEPLAY_WIDGET
            if action.kind is ActionKind.CLICK_WIDGET
            else InputPurpose.GAMEPLAY_OBJECT
        )
        return ApprovedPointerIntent(
            intent_id=self._intent_id(action, "target"),
            purpose=purpose,
            target=action.screen_point,
            movement_bounds=canvas,
            target_bounds=self._bounded_target_region(
                self._target_bounds(action, observation),
                action.screen_point,
                canvas,
            ),
            expected_pid=self._required_pid(observation),
            button=MouseButton.LEFT,
            reacquisition_bounds=outer_window,
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
    def _input_validation(result: SafetyResult) -> InputValidation:
        if result.allowed:
            return InputValidation.allow(result.reason)
        return InputValidation.deny(result.reason)

    def _await_post_move(
        self,
        action: Action,
        retry_reasons: set[str],
        safety_checks: list[SafetyCheck],
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> tuple[Observation, SafetyResult, SafetyResult]:
        bounded_retry_reasons = (
            set(retry_reasons) | TRANSIENT_POST_MOVE_RETRY_REASONS
        )
        observation = self._observe()
        result_evaluation = self._safety.evaluate_post_move(
            action, observation, settled_pointer=settled_pointer
        )
        context_evaluation = self._safety.evaluate_context_candidate(
            action, observation, settled_pointer=settled_pointer
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
            self._sleep(self._evidence_delay_seconds)
            observation = self._observe()
            result_evaluation = self._safety.evaluate_post_move(
                action, observation, settled_pointer=settled_pointer
            )
            context_evaluation = self._safety.evaluate_context_candidate(
                action, observation, settled_pointer=settled_pointer
            )
            self._extend_safety_checks(safety_checks, result_evaluation)
            self._extend_safety_checks(safety_checks, context_evaluation)
            result = result_evaluation.result
            context = context_evaluation.result
        return observation, result, context

    def _await_context_menu(
        self,
        action: Action,
        *,
        minimum_tick: int,
        row_point: ScreenPoint | None = None,
        safety_checks: list[SafetyCheck],
    ) -> tuple[Observation, SafetyResult]:
        retry_reasons = {
            "menu_sample_not_newer",
            "context_menu_not_open",
            "context_row_pointer_mismatch",
        } | TRANSIENT_POST_MOVE_RETRY_REASONS
        observation = self._observe()
        evaluation = self._safety.evaluate_context_menu(
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
            self._sleep(self._evidence_delay_seconds)
            observation = self._observe()
            evaluation = self._safety.evaluate_context_menu(
                action,
                observation,
                minimum_menu_client_tick=minimum_tick,
                row_point=row_point,
            )
            self._extend_safety_checks(safety_checks, evaluation)
            result = evaluation.result
        return observation, result

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
    ) -> ExecutionResult:
        return ExecutionResult(
            action=action,
            pre_move_tick=observation.tick,
            local_status=status,
            local_reason=reason,
            safety_checks=tuple(safety_checks),
        )
