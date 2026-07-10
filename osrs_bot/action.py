from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .arduino import ArduinoHIDBackend
from .model import Action, ActionKind, MenuEntry, Observation, ScreenBounds, ScreenPoint
from .safety import SafetyGate


class _ActionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str
    action: Action
    pre_move_tick: int
    post_move_tick: int | None = None
    stop_all_confirmed: bool = False
    disarm_confirmed: bool = False
    backend_status: dict[str, Any] | None = None

    @property
    def sent(self) -> bool:
        return self.status == "SENT"


class ArduinoActionInterface:
    """The only live action path.

    Every pointer action is checked, moved through the Arduino, checked again
    against a fresh hover observation, and only then clicked. Cleanup runs for
    every connected attempt, including blocked and failed attempts.
    """

    def __init__(
        self,
        backend: ArduinoHIDBackend,
        safety: SafetyGate,
        observe: Callable[[], Observation],
        *,
        sleep: Callable[[float], None] = time.sleep,
        evidence_attempts: int = 12,
        evidence_delay_seconds: float = 0.1,
    ) -> None:
        self._backend = backend
        self._safety = safety
        self._observe = observe
        self._sleep = sleep
        self._evidence_attempts = max(1, evidence_attempts)
        self._evidence_delay_seconds = max(0.0, evidence_delay_seconds)

    def execute(self, action: Action, observation: Observation) -> ExecutionResult:
        preflight = self._safety.validate_pre_move(action, observation)
        if not preflight.allowed:
            return ExecutionResult(
                status="BLOCKED",
                reason=preflight.reason,
                action=action,
                pre_move_tick=observation.tick,
            )

        if action.kind == ActionKind.WAIT:
            return ExecutionResult(
                status="NO_ACTION",
                reason="wait_action",
                action=action,
                pre_move_tick=observation.tick,
            )

        connected = False
        post_move: Observation | None = None
        status = "ERROR"
        reason = "action_not_sent"
        stop_all_confirmed = False
        disarm_confirmed = False

        try:
            self._backend.connect()
            connected = True
            if self._is_pointer_action(action):
                canvas = self._required_canvas(observation)
                region = self._region(canvas)
                self._backend.configure_movement_safety(
                    allowed_region=region,
                    allowed_foreground_titles=["RuneLite"],
                    enabled=True,
                    margin_px=1,
                )

            self._backend.arm()

            if self._is_pointer_action(action):
                assert action.screen_point is not None
                canvas = self._required_canvas(observation)
                point = {"x": action.screen_point.x, "y": action.screen_point.y}
                self._backend.move_to_absolute(
                    point,
                    allowed_region=self._region(canvas),
                    allowed_foreground_titles=["RuneLite"],
                    tolerance_px=1,
                    margin_px=1,
                )
                post_move, hover_check, context_check = self._await_post_move(
                    action,
                    {"menu_sample_not_newer", "hover_pointer_mismatch", "hover_menu_mismatch"},
                )
                if hover_check.allowed:
                    self._backend.assert_foreground(
                        ["RuneLite"], expected_pid=post_move.client_process_id
                    )
                    self._click("left")
                elif context_check.allowed:
                    post_move = self._select_context_entry(
                        action, post_move, canvas
                    )
                else:
                    raise _ActionBlocked(hover_check.reason)
            elif action.kind == ActionKind.PRESS_KEY:
                if not action.key:
                    raise ValueError("press_key action has no key")
                retry_reason = (
                    "bank_sample_not_newer"
                    if action.target_key == "close_bank_keyboard"
                    else "dialogue_sample_not_newer"
                )
                post_move, key_check, _ = self._await_post_move(
                    action, {retry_reason}
                )
                if not key_check.allowed:
                    raise _ActionBlocked(key_check.reason)
                self._backend.assert_foreground(
                    ["RuneLite"], expected_pid=post_move.client_process_id
                )
                self._backend.press(action.key)
            else:
                raise ValueError(f"unsupported live action: {action.kind.value}")

            status = "SENT"
            reason = "action_sent"
        except _ActionBlocked as error:
            status = "BLOCKED"
            reason = str(error)
        except Exception as error:  # fail closed at the hardware boundary
            status = "ERROR"
            reason = f"{type(error).__name__}: {error}"
        finally:
            if connected:
                try:
                    self._backend.stop_all()
                    stop_all_confirmed = True
                except Exception:
                    stop_all_confirmed = False
                try:
                    self._backend.disarm()
                    disarm_confirmed = True
                except Exception:
                    disarm_confirmed = False
                try:
                    self._backend.close()
                except Exception:
                    pass

        if connected and (not stop_all_confirmed or not disarm_confirmed):
            prior = f"{status.lower()}: {reason}"
            status = "ERROR"
            reason = f"cleanup_not_confirmed ({prior})"

        return ExecutionResult(
            status=status,
            reason=reason,
            action=action,
            pre_move_tick=observation.tick,
            post_move_tick=post_move.tick if post_move is not None else None,
            stop_all_confirmed=stop_all_confirmed,
            disarm_confirmed=disarm_confirmed,
            backend_status=self._safe_status(),
        )

    @staticmethod
    def _is_pointer_action(action: Action) -> bool:
        return action.kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }

    @staticmethod
    def _required_canvas(observation: Observation) -> ScreenBounds:
        if observation.canvas_bounds is None:
            raise ValueError("canvas bounds unavailable")
        return observation.canvas_bounds

    @staticmethod
    def _region(bounds: ScreenBounds) -> dict[str, int]:
        return {
            "x": bounds.x,
            "y": bounds.y,
            "width": bounds.width,
            "height": bounds.height,
        }

    def _safe_status(self) -> dict[str, Any] | None:
        try:
            return self._backend.status()
        except Exception:
            return None

    def _await_post_move(self, action: Action, retry_reasons: set[str]):
        observation = self._observe()
        result = self._safety.validate_post_move(action, observation)
        context = self._safety.validate_context_candidate(action, observation)
        for _ in range(1, self._evidence_attempts):
            if result.allowed or context.allowed or result.reason not in retry_reasons:
                break
            self._sleep(self._evidence_delay_seconds)
            observation = self._observe()
            result = self._safety.validate_post_move(action, observation)
            context = self._safety.validate_context_candidate(action, observation)
        return observation, result, context

    def _select_context_entry(
        self, action: Action, hover: Observation, canvas: ScreenBounds
    ) -> Observation:
        minimum_tick = hover.menu_client_tick
        if minimum_tick is None:
            raise _ActionBlocked("menu_sample_missing")
        self._backend.assert_foreground(
            ["RuneLite"], expected_pid=hover.client_process_id
        )
        self._click("right")
        try:
            opened, result = self._await_context_menu(
                action, minimum_tick=minimum_tick
            )
            if not result.allowed:
                raise _ActionBlocked(result.reason)
            entry = self._exact_context_entry(action, opened)
            if entry is None or entry.row_bounds is None:
                raise _ActionBlocked("context_row_bounds_missing")
            point = entry.row_bounds.center
            self._backend.move_to_absolute(
                {"x": point.x, "y": point.y},
                allowed_region=self._region(canvas),
                allowed_foreground_titles=["RuneLite"],
                tolerance_px=1,
                margin_px=1,
            )
            row_observation, row_result = self._await_context_menu(
                action,
                minimum_tick=opened.menu_client_tick,
                row_point=point,
            )
            if not row_result.allowed:
                raise _ActionBlocked(row_result.reason)
            self._backend.assert_foreground(
                ["RuneLite"], expected_pid=row_observation.client_process_id
            )
            self._click("left")
            return row_observation
        except Exception:
            try:
                self._backend.assert_foreground(
                    ["RuneLite"], expected_pid=hover.client_process_id
                )
                self._backend.press("ESC")
            except Exception:
                pass
            raise

    def _await_context_menu(
        self,
        action: Action,
        *,
        minimum_tick: int | None,
        row_point: ScreenPoint | None = None,
    ):
        if minimum_tick is None:
            raise _ActionBlocked("menu_sample_missing")
        retry_reasons = {
            "menu_sample_not_newer",
            "context_menu_not_open",
            "context_row_pointer_mismatch",
        }
        observation = self._observe()
        result = self._safety.validate_context_menu(
            action,
            observation,
            minimum_menu_client_tick=minimum_tick,
            row_point=row_point,
        )
        for _ in range(1, self._evidence_attempts):
            if result.allowed or result.reason not in retry_reasons:
                break
            self._sleep(self._evidence_delay_seconds)
            observation = self._observe()
            result = self._safety.validate_context_menu(
                action,
                observation,
                minimum_menu_client_tick=minimum_tick,
                row_point=row_point,
            )
        return observation, result

    def _click(self, button: str) -> None:
        self._backend.mouse_down(button=button)
        self._sleep(0.06)
        self._backend.mouse_up(button=button)

    @staticmethod
    def _exact_context_entry(
        action: Action, observation: Observation
    ) -> MenuEntry | None:
        matches = [
            entry for entry in observation.menus
            if entry.option == action.option
            and entry.target == action.target_name
            and entry.identifier == action.target_id
            and entry.param0 == action.target_param0
            and entry.param1 == action.target_param1
        ]
        return matches[0] if len(matches) == 1 else None
