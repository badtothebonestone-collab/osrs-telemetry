from __future__ import annotations

from dataclasses import dataclass

from .model import (
    MAX_FUTURE_CLOCK_SKEW_SECONDS,
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    CameraConstraint,
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryConstraint,
    MenuEntry,
    NearbyObject,
    Observation,
    ScreenBounds,
    ScreenPoint,
    VerificationKind,
    WidgetTarget,
)


POINTER_MATCH_TOLERANCE_PX = 3


@dataclass(frozen=True, slots=True)
class SafetyResult:
    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be bool")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SafetyCheck:
    stage: str
    code: str
    allowed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("stage must be a non-empty string")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-empty string")
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be bool")


@dataclass(frozen=True, slots=True)
class SafetyEvaluation:
    result: SafetyResult
    checks: tuple[SafetyCheck, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.result, SafetyResult):
            raise TypeError("result must be SafetyResult")
        if not isinstance(self.checks, tuple) or not self.checks:
            raise ValueError("checks must be a non-empty tuple")
        if not all(isinstance(check, SafetyCheck) for check in self.checks):
            raise TypeError("checks must contain only SafetyCheck values")
        final = self.checks[-1]
        if (
            final.allowed is not self.result.allowed
            or final.code != self.result.reason
        ):
            raise ValueError("the final safety check must match the evaluation result")


@dataclass(frozen=True, slots=True)
class SafetyGate:
    max_observation_age_seconds: float = 2.0
    max_menu_age_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_observation_age_seconds < 0:
            raise ValueError("max_observation_age_seconds must be non-negative")
        if self.max_menu_age_seconds < 0:
            raise ValueError("max_menu_age_seconds must be non-negative")

    def validate_pre_move(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        return self.evaluate_pre_move(action, observation).result

    def evaluate_pre_move(
        self, action: Action, observation: Observation
    ) -> SafetyEvaluation:
        checks: list[SafetyCheck] = []
        common = _record(
            checks,
            "pre_move.observation",
            self._validate_observation(observation),
        )
        if not common.allowed:
            return _evaluation(common, checks)
        session = _record(
            checks,
            "pre_move.session",
            self._validate_session(action, observation),
        )
        if not session.allowed:
            return _evaluation(session, checks)
        tick = _record(
            checks,
            "pre_move.source_tick",
            _allow("tick_bound")
            if action.source_tick == observation.tick
            else _reject("tick_mismatch"),
        )
        if not tick.allowed:
            return _evaluation(tick, checks)
        if action.kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }:
            sample = _record(
                checks,
                "pre_move.source_menu_sample",
                self._validate_source_menu_sample(action, observation),
            )
            if not sample.allowed:
                return _evaluation(sample, checks)
        if (
            action.kind is ActionKind.PRESS_KEY
            and action.task_constraints.dialogue is not None
        ):
            if (
                action.source_dialogue_client_tick is None
                or observation.widgets.dialogue_client_tick is None
            ):
                dialogue_sample = _reject("dialogue_sample_missing")
            elif (
                action.source_dialogue_client_tick
                != observation.widgets.dialogue_client_tick
            ):
                dialogue_sample = _reject("dialogue_sample_mismatch")
            else:
                dialogue_sample = _allow("dialogue_sample_bound")
            dialogue_sample = _record(
                checks,
                "pre_move.source_dialogue_sample",
                dialogue_sample,
            )
            if not dialogue_sample.allowed:
                return _evaluation(dialogue_sample, checks)
        action_result = _record(
            checks,
            "pre_move.action_invariants",
            self._validate_engine_action_invariants(action, observation),
        )
        if not action_result.allowed:
            return _evaluation(action_result, checks)
        constraint_result = _record(
            checks,
            "pre_move.task_constraints",
            self._validate_task_constraints(action, observation),
        )
        if not constraint_result.allowed:
            return _evaluation(constraint_result, checks)
        complete = _record(
            checks, "pre_move.complete", _allow("pre_move_safe")
        )
        return _evaluation(complete, checks)

    def validate_post_move(
        self,
        action: Action,
        observation: Observation,
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> SafetyResult:
        return self.evaluate_post_move(
            action, observation, settled_pointer=settled_pointer
        ).result

    def evaluate_post_move(
        self,
        action: Action,
        observation: Observation,
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> SafetyEvaluation:
        base_evaluation = self._evaluate_post_move_base(
            action,
            observation,
            stage="post_move",
            settled_pointer=settled_pointer,
        )
        checks = list(base_evaluation.checks)
        base = base_evaluation.result
        if not base.allowed:
            return base_evaluation
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            hover_result = _record(
                checks,
                "post_move.hover_menu",
                self._validate_hover_menu(action, observation),
            )
            if not hover_result.allowed:
                return _evaluation(hover_result, checks)
        complete = _record(
            checks, "post_move.complete", _allow("post_move_safe")
        )
        return _evaluation(complete, checks)

    def validate_context_candidate(
        self,
        action: Action,
        observation: Observation,
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> SafetyResult:
        return self.evaluate_context_candidate(
            action, observation, settled_pointer=settled_pointer
        ).result

    def evaluate_context_candidate(
        self,
        action: Action,
        observation: Observation,
        *,
        settled_pointer: ScreenPoint | None = None,
    ) -> SafetyEvaluation:
        base_evaluation = self._evaluate_post_move_base(
            action,
            observation,
            stage="context_candidate",
            settled_pointer=settled_pointer,
        )
        checks = list(base_evaluation.checks)
        base = base_evaluation.result
        if not base.allowed:
            return base_evaluation
        supported = _record(
            checks,
            "context_candidate.action_kind",
            _allow("context_selection_supported")
            if action.kind is ActionKind.INTERACT_OBJECT
            else _reject("context_selection_unsupported"),
        )
        if not supported.allowed:
            return _evaluation(supported, checks)
        matches = _matching_menu_entries(action, observation)
        candidate = _record(
            checks,
            "context_candidate.exact_lower_entry",
            _allow("context_option_unique_lower_entry")
            if len(matches) == 1 and matches[0] is not observation.menus[0]
            else _reject("context_option_not_unique_lower_entry"),
        )
        if not candidate.allowed:
            return _evaluation(candidate, checks)
        complete = _record(
            checks,
            "context_candidate.complete",
            _allow("context_candidate_safe"),
        )
        return _evaluation(complete, checks)

    def validate_context_menu(
        self,
        action: Action,
        observation: Observation,
        *,
        minimum_menu_client_tick: int,
        row_point: ScreenPoint | None = None,
    ) -> SafetyResult:
        return self.evaluate_context_menu(
            action,
            observation,
            minimum_menu_client_tick=minimum_menu_client_tick,
            row_point=row_point,
        ).result

    def evaluate_context_menu(
        self,
        action: Action,
        observation: Observation,
        *,
        minimum_menu_client_tick: int,
        row_point: ScreenPoint | None = None,
    ) -> SafetyEvaluation:
        checks: list[SafetyCheck] = []
        common = _record(
            checks,
            "context_menu.observation",
            self._validate_observation(observation),
        )
        if not common.allowed:
            return _evaluation(common, checks)
        session = _record(
            checks,
            "context_menu.session",
            self._validate_session(action, observation),
        )
        if not session.allowed:
            return _evaluation(session, checks)
        tick = _record(
            checks,
            "context_menu.source_tick",
            _allow("tick_not_regressed")
            if observation.tick >= action.source_tick
            else _reject("tick_mismatch"),
        )
        if not tick.allowed:
            return _evaluation(tick, checks)
        menu_source = _record(
            checks,
            "context_menu.menu_source",
            self._validate_menu_source(action, observation),
        )
        if not menu_source.allowed:
            return _evaluation(menu_source, checks)
        supported = _record(
            checks,
            "context_menu.action_kind",
            _allow("context_selection_supported")
            if action.kind is ActionKind.INTERACT_OBJECT
            else _reject("context_selection_unsupported"),
        )
        if not supported.allowed:
            return _evaluation(supported, checks)
        if observation.menu_client_tick is None:
            menu_sample = _reject("menu_sample_missing")
        elif observation.menu_client_tick <= minimum_menu_client_tick:
            menu_sample = _reject("menu_sample_not_newer")
        else:
            menu_sample = _allow("menu_sample_newer")
        menu_sample = _record(
            checks, "context_menu.menu_sample", menu_sample
        )
        if not menu_sample.allowed:
            return _evaluation(menu_sample, checks)
        menu_open = _record(
            checks,
            "context_menu.open_state",
            _allow("context_menu_open")
            if observation.menu_open
            else _reject("context_menu_not_open"),
        )
        if not menu_open.allowed:
            return _evaluation(menu_open, checks)
        action_result = _record(
            checks,
            "context_menu.action_invariants",
            self._validate_engine_action_invariants(
                action,
                observation,
                allow_screen_point_drift=True,
            ),
        )
        if not action_result.allowed:
            return _evaluation(action_result, checks)
        constraint_result = _record(
            checks,
            "context_menu.task_constraints",
            self._validate_task_constraints(action, observation),
        )
        if not constraint_result.allowed:
            return _evaluation(constraint_result, checks)
        matches = _matching_menu_entries(action, observation)
        unique = _record(
            checks,
            "context_menu.exact_option",
            _allow("context_option_unique")
            if len(matches) == 1
            else _reject("context_option_not_unique"),
        )
        if not unique.allowed:
            return _evaluation(unique, checks)
        entry = matches[0]
        bounds = entry.row_bounds
        menu_bounds = observation.menu_bounds
        row_bounds = _record(
            checks,
            "context_menu.row_bounds",
            _allow("context_row_bounds_valid")
            if bounds is not None and _valid_bounds(bounds)
            else _reject("context_row_bounds_missing"),
        )
        if not row_bounds.allowed:
            return _evaluation(row_bounds, checks)
        menu_geometry = _record(
            checks,
            "context_menu.menu_bounds",
            _allow("context_menu_bounds_valid")
            if menu_bounds is not None and _valid_bounds(menu_bounds)
            else _reject("context_menu_bounds_missing"),
        )
        if not menu_geometry.allowed:
            return _evaluation(menu_geometry, checks)
        assert bounds is not None and menu_bounds is not None
        row_inside = _record(
            checks,
            "context_menu.row_inside_menu",
            _allow("context_row_inside_menu")
            if menu_bounds.contains(bounds.center)
            else _reject("context_row_outside_menu"),
        )
        if not row_inside.allowed:
            return _evaluation(row_inside, checks)
        if row_point is None:
            complete = _record(
                checks,
                "context_menu.complete",
                _allow("context_menu_open_safe"),
            )
            return _evaluation(complete, checks)
        pointer_inside = _record(
            checks,
            "context_menu.row_pointer_bounds",
            _allow("context_row_pointer_inside")
            if bounds.contains(row_point) and menu_bounds.contains(row_point)
            else _reject("context_row_pointer_outside"),
        )
        if not pointer_inside.allowed:
            return _evaluation(pointer_inside, checks)
        pointer_match = _record(
            checks,
            "context_menu.row_pointer_match",
            _allow("context_row_pointer_exact")
            if _points_close(observation.menu_mouse_screen_point, row_point)
            else _reject("context_row_pointer_mismatch"),
        )
        if not pointer_match.allowed:
            return _evaluation(pointer_match, checks)
        complete = _record(
            checks, "context_menu.complete", _allow("context_row_safe")
        )
        return _evaluation(complete, checks)

    def _validate_post_move_base(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        return self._evaluate_post_move_base(
            action, observation, stage="post_move_base"
        ).result

    def _evaluate_post_move_base(
        self,
        action: Action,
        observation: Observation,
        *,
        stage: str,
        settled_pointer: ScreenPoint | None = None,
    ) -> SafetyEvaluation:
        checks: list[SafetyCheck] = []
        common = _record(
            checks,
            f"{stage}.observation",
            self._validate_observation(observation),
        )
        if not common.allowed:
            return _evaluation(common, checks)
        session = _record(
            checks,
            f"{stage}.session",
            self._validate_session(action, observation),
        )
        if not session.allowed:
            return _evaluation(session, checks)
        tick = _record(
            checks,
            f"{stage}.source_tick",
            _allow("tick_not_regressed")
            if observation.tick >= action.source_tick
            else _reject("tick_mismatch"),
        )
        if not tick.allowed:
            return _evaluation(tick, checks)
        if action.kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }:
            actual_pointer = (
                action.screen_point
                if settled_pointer is None
                else settled_pointer
            )
            menu_source = _record(
                checks,
                f"{stage}.menu_source",
                self._validate_menu_source(action, observation),
            )
            if not menu_source.allowed:
                return _evaluation(menu_source, checks)
            if action.source_menu_client_tick is None or observation.menu_client_tick is None:
                menu_sample = _reject("menu_sample_missing")
            elif observation.menu_client_tick <= action.source_menu_client_tick:
                menu_sample = _reject("menu_sample_not_newer")
            else:
                menu_sample = _allow("menu_sample_newer")
            menu_sample = _record(
                checks, f"{stage}.menu_sample", menu_sample
            )
            if not menu_sample.allowed:
                return _evaluation(menu_sample, checks)
            closed = _record(
                checks,
                f"{stage}.menu_open_state",
                _reject("context_menu_open")
                if observation.menu_open
                else _allow("context_menu_closed"),
            )
            if not closed.allowed:
                return _evaluation(closed, checks)
            pointer = _record(
                checks,
                f"{stage}.hover_pointer",
                _allow("hover_pointer_exact")
                if _points_close(
                    observation.menu_mouse_screen_point, actual_pointer
                )
                else _reject("hover_pointer_mismatch"),
            )
            if not pointer.allowed:
                return _evaluation(pointer, checks)
            settled = _record(
                checks,
                f"{stage}.settled_pointer",
                self._validate_settled_pointer(
                    action, observation, actual_pointer
                ),
            )
            if not settled.allowed:
                return _evaluation(settled, checks)
        if action.kind is ActionKind.PRESS_KEY:
            if _interface_close_constraint(action) is not None:
                key_sample = (
                    _allow("interface_sample_newer")
                    if observation.tick > action.source_tick
                    else _reject("interface_sample_not_newer")
                )
                key_sample = _record(
                    checks, f"{stage}.interface_sample", key_sample
                )
                if not key_sample.allowed:
                    return _evaluation(key_sample, checks)
            elif action.task_constraints.dialogue is not None:
                if (
                    action.source_dialogue_client_tick is None
                    or observation.widgets.dialogue_client_tick is None
                ):
                    dialogue_sample = _reject("dialogue_sample_missing")
                elif (
                    observation.widgets.dialogue_client_tick
                    <= action.source_dialogue_client_tick
                ):
                    dialogue_sample = _reject("dialogue_sample_not_newer")
                else:
                    dialogue_sample = _allow("dialogue_sample_newer")
                dialogue_sample = _record(
                    checks, f"{stage}.dialogue_sample", dialogue_sample
                )
                if not dialogue_sample.allowed:
                    return _evaluation(dialogue_sample, checks)
            elif action.task_constraints.camera is not None:
                camera_sample = _record(
                    checks,
                    f"{stage}.camera_sample",
                    _allow("camera_sample_newer")
                    if observation.tick > action.source_tick
                    else _reject("camera_sample_not_newer"),
                )
                if not camera_sample.allowed:
                    return _evaluation(camera_sample, checks)
        action_result = _record(
            checks,
            f"{stage}.action_invariants",
            self._validate_engine_action_invariants(
                action,
                observation,
                allow_screen_point_drift=True,
            ),
        )
        if not action_result.allowed:
            return _evaluation(action_result, checks)
        constraint_result = _record(
            checks,
            f"{stage}.task_constraints",
            self._validate_task_constraints(action, observation),
        )
        if not constraint_result.allowed:
            return _evaluation(constraint_result, checks)
        complete = _record(
            checks, f"{stage}.base", _allow("post_move_base_safe")
        )
        return _evaluation(complete, checks)

    @staticmethod
    def _validate_settled_pointer(
        action: Action,
        observation: Observation,
        point: ScreenPoint | None,
    ) -> SafetyResult:
        if not _points_close(point, action.screen_point):
            return _reject("settled_pointer_outside_verified_region")
        target_bounds: ScreenBounds | None
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            if not action.target_key:
                return _reject("target_missing")
            target = observation.object_by_key(action.target_key)
            if target is None:
                return _reject("target_missing")
            target_bounds = target.geometry.screen_bounds
            fresh_point = target.geometry.screen_point
        elif action.kind is ActionKind.CLICK_WIDGET:
            selected = _select_widget(action, observation)
            if selected is None:
                return _reject("target_missing")
            target_bounds = selected[1].screen_bounds
            fresh_point = selected[1].screen_point
        else:
            return _reject("settled_pointer_unsupported")
        if not _points_close(point, fresh_point):
            return _reject("settled_pointer_outside_fresh_region")
        if target_bounds is None and (
            point != action.screen_point or point != fresh_point
        ):
            # A fresh canonical point without a rectangle is usable only as
            # an exact 1x1 region, matching the coordinator's point-only
            # target contract. It cannot inherit either +/- tolerance.
            return _reject("target_bounds_missing")
        result = _validate_point(
            point, observation.canvas_bounds, target_bounds
        )
        return (
            _allow("settled_pointer_safe")
            if result.allowed
            else result
        )

    def _validate_observation(self, observation: Observation) -> SafetyResult:
        if not observation.source_coherent:
            return _reject("source_incoherent")
        if observation.status != "PASS":
            return _reject("observation_not_pass")
        if not observation.fresh or not observation.cache_wall_clock_fresh:
            return _reject("observation_stale")
        try:
            age = observation.age_seconds
        except (AttributeError, TypeError, ValueError, OverflowError):
            return _reject("observation_timestamp_invalid")
        if not observation.timestamp_not_future:
            return _reject("observation_timestamp_future")
        if age > self.max_observation_age_seconds:
            return _reject("observation_too_old")
        if not observation.loaded_scene:
            return _reject("scene_not_loaded")
        if observation.widgets.bank_pin_open:
            return _reject("bank_pin_open")
        if not observation.client_focused:
            return _reject("client_not_focused")
        if observation.client_process_id is None or observation.client_process_id <= 0:
            return _reject("client_process_missing")
        return _allow("observation_safe")

    @staticmethod
    def _validate_session(action: Action, observation: Observation) -> SafetyResult:
        if not action.source_session_id or not observation.session_id:
            return _reject("session_missing")
        if action.source_session_id != observation.session_id:
            return _reject("session_changed")
        return _allow("session_bound")

    def _validate_engine_action_invariants(
        self,
        action: Action,
        observation: Observation,
        *,
        allow_screen_point_drift: bool = False,
    ) -> SafetyResult:
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            return self._validate_object_action(
                action,
                observation,
                allow_screen_point_drift=allow_screen_point_drift,
            )
        if action.kind is ActionKind.CLICK_WIDGET:
            return self._validate_widget_action(
                action,
                observation,
                allow_screen_point_drift=allow_screen_point_drift,
            )
        if action.kind is ActionKind.PRESS_KEY:
            return self._validate_key_action(action)
        if action.kind is ActionKind.WAIT:
            return _allow("wait_safe")
        return _reject("unsupported_action")

    @staticmethod
    def _validate_key_action(action: Action) -> SafetyResult:
        dialogue = action.task_constraints.dialogue
        interface = _interface_close_constraint(action)
        camera = action.task_constraints.camera
        if dialogue is not None:
            if action.key not in {str(value) for value in range(1, 10)}:
                return _reject("unsafe_key")
            if action.key_hold_millis != 50:
                return _reject("unsafe_key_hold")
            if (
                action.key != dialogue.option_key
                or action.option != dialogue.option_text
                or action.target_name != dialogue.option_text
                or action.target_id != dialogue.option_index
                or action.target_key != f"dialogue:{dialogue.option_index}"
            ):
                return _reject("dialogue_option_mismatch")
            return _allow("dialogue_key_shape_safe")
        if interface is not None:
            if action.key not in {"esc", "escape"}:
                return _reject("unsafe_key")
            if action.key_hold_millis != 50:
                return _reject("unsafe_key_hold")
            if (
                not action.option
                or action.option != action.target_name
                or action.target_id != 0
                or not action.target_key
            ):
                return _reject("interface_close_identity_mismatch")
            return _allow("interface_close_key_shape_safe")
        if camera is not None:
            if action.key not in {"left", "right"}:
                return _reject("unsafe_key")
            if (
                action.key != camera.direction
                or action.key_hold_millis != camera.hold_millis
            ):
                return _reject("camera_key_shape_mismatch")
            if (
                not action.option
                or action.target_key != camera.target_key
                or action.target_name != camera.target_key
                or action.target_id != 0
            ):
                return _reject("camera_target_identity_mismatch")
            verification = action.verification
            if (
                verification is None
                or verification.kind is not VerificationKind.CAMERA_POSE_CHANGED
                or verification.before_location != camera.source_location
                or verification.before_camera_yaw != camera.before_yaw
                or verification.before_geometry_frame_id
                != camera.source_geometry_frame_id
                or verification.camera_key != camera.direction
            ):
                return _reject("camera_verification_mismatch")
            return _allow("camera_key_shape_safe")
        return _reject("key_constraint_missing")

    def _validate_source_menu_sample(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        menu_source = self._validate_menu_source(action, observation)
        if not menu_source.allowed:
            return menu_source
        if action.source_menu_client_tick is None or observation.menu_client_tick is None:
            return _reject("menu_sample_missing")
        if action.source_menu_client_tick != observation.menu_client_tick:
            return _reject("menu_sample_mismatch")
        return _allow("menu_sample_bound")

    def _validate_menu_source(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        if not observation.menu_fresh:
            return _reject("menu_evidence_stale")
        if observation.menu_source_tick is None:
            return _reject("menu_source_tick_missing")
        if observation.menu_source_tick != observation.tick:
            return _reject("menu_source_tick_mismatch")
        if not observation.menu_session_id:
            return _reject("menu_session_missing")
        if observation.menu_session_id != observation.session_id:
            return _reject("menu_session_mismatch")
        if observation.menu_session_id != action.source_session_id:
            return _reject("menu_session_changed")
        if observation.menu_process_id is None or observation.menu_process_id <= 0:
            return _reject("menu_process_missing")
        if observation.menu_process_id != observation.client_process_id:
            return _reject("menu_process_mismatch")
        try:
            age = observation.menu_age_seconds
        except (AttributeError, TypeError, ValueError, OverflowError):
            return _reject("menu_timestamp_invalid")
        if age is None:
            return _reject("menu_timestamp_missing")
        if age < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
            return _reject("menu_timestamp_future")
        if age > self.max_menu_age_seconds:
            return _reject("menu_evidence_too_old")
        return _allow("menu_source_bound")

    def _validate_object_action(
        self,
        action: Action,
        observation: Observation,
        *,
        allow_screen_point_drift: bool = False,
    ) -> SafetyResult:
        if not action.target_key:
            return _reject("target_missing")
        target = observation.object_by_key(action.target_key)
        if target is None:
            return _reject("target_missing")
        identity = _validate_exact_target(action, target)
        if not identity.allowed:
            return identity
        geometry = target.geometry
        if not geometry.available:
            return _reject("geometry_unavailable")
        if not geometry.on_screen:
            return _reject("target_offscreen")
        if not geometry.visible:
            return _reject("target_not_visible")
        if not geometry.actionable:
            return _reject("target_not_actionable")
        if geometry.screen_point is None:
            return _reject("screen_point_missing")
        point_result = _validate_reprojected_point(
            action.screen_point,
            geometry.screen_point,
            observation.canvas_bounds,
            geometry.screen_bounds,
            allow_drift=allow_screen_point_drift,
        )
        return point_result

    def _validate_widget_action(
        self,
        action: Action,
        observation: Observation,
        *,
        allow_screen_point_drift: bool = False,
    ) -> SafetyResult:
        selected = _select_widget(action, observation)
        if selected is None:
            return _reject("target_missing")
        _, widget = selected
        if not widget.visible:
            return _reject("widget_not_visible")
        if action.option != widget.name:
            return _reject("target_option_mismatch")
        if action.target_name != widget.name:
            return _reject("target_name_mismatch")
        if widget.screen_point is None:
            return _reject("screen_point_missing")
        point_result = _validate_reprojected_point(
            action.screen_point,
            widget.screen_point,
            observation.canvas_bounds,
            widget.screen_bounds,
            allow_drift=allow_screen_point_drift,
        )
        if not point_result.allowed:
            return point_result
        return (
            point_result
            if point_result.reason == "screen_point_drift_bounded"
            else _allow("widget_safe")
        )

    def _validate_task_constraints(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        constraints = action.task_constraints
        if action.kind is ActionKind.CLICK_WIDGET and constraints.interface is None:
            return _reject("interface_constraint_missing")
        if (
            action.target_key == DEPOSIT_INVENTORY_WIDGET_KEY
            and constraints.inventory is None
        ):
            return _reject("inventory_constraint_missing")

        if constraints.interface is not None:
            interface = self._validate_interface_constraint(
                constraints.interface, observation
            )
            if not interface.allowed:
                return interface
        if constraints.inventory is not None:
            inventory = self._validate_inventory_constraint(
                constraints.inventory, observation
            )
            if not inventory.allowed:
                return inventory
        if constraints.dialogue is not None:
            dialogue = self._validate_dialogue_constraint(
                constraints.dialogue, observation
            )
            if not dialogue.allowed:
                return dialogue
        if constraints.camera is not None:
            camera = self._validate_camera_constraint(
                action, constraints.camera, observation
            )
            if not camera.allowed:
                return camera
        return _allow("task_constraints_satisfied")

    @staticmethod
    def _validate_camera_constraint(
        action: Action,
        constraint: CameraConstraint,
        observation: Observation,
    ) -> SafetyResult:
        if observation.location != constraint.source_location:
            return _reject("camera_source_location_changed")
        if observation.plane != constraint.target_location.plane:
            return _reject("camera_target_plane_mismatch")
        if observation.camera_yaw != constraint.before_yaw:
            return _reject("camera_pose_changed")
        if observation.geometry_frame_id != constraint.source_geometry_frame_id:
            return _reject("camera_geometry_frame_changed")
        target = observation.object_by_key(constraint.target_key)
        if target is None:
            return _reject("camera_target_missing")
        if (
            target.key != constraint.target_key
            or target.object_id != 0
            or target.name != constraint.target_key
            or target.kind != "NAVIGATION_TILE"
            or target.actions != ("Walk here",)
            or target.location != constraint.target_location
            or target.scene_x is None
            or target.scene_y is None
            or action.target_param0 != target.scene_x
            or action.target_param1 != target.scene_y
        ):
            return _reject("camera_target_identity_mismatch")
        if target.geometry.actionable:
            return _reject("camera_projection_already_actionable")
        return _allow("camera_constraint_satisfied")

    @staticmethod
    def _validate_interface_constraint(
        constraint: InterfaceConstraint, observation: Observation
    ) -> SafetyResult:
        if constraint.interface_name != BANK_INTERFACE_NAME:
            return _reject("unsupported_interface_constraint")
        widgets = observation.widgets
        if not widgets.bank_known:
            return _reject("interface_state_unknown")
        if observation.plane != constraint.expected_plane:
            return _reject("interface_plane_mismatch")
        if widgets.bank_open is not constraint.expected_open:
            return _reject("interface_state_mismatch")
        if constraint.require_readable and not widgets.bank_readable:
            return _reject("interface_not_readable")
        if constraint.require_keyboard_close and not widgets.keyboard_close_possible:
            return _reject("interface_keyboard_close_unavailable")
        return _allow("interface_constraint_satisfied")

    @staticmethod
    def _validate_inventory_constraint(
        constraint: InventoryConstraint, observation: Observation
    ) -> SafetyResult:
        inventory = observation.inventory
        if not inventory.known:
            return _reject("inventory_unknown")
        held = [item for item in inventory.items if item.quantity > 0]
        if constraint.require_nonempty and not held:
            return _reject("constrained_inventory_empty")
        if any(item.item_id not in constraint.allowed_item_ids for item in held):
            return _reject("unsafe_deposit_inventory")
        return _allow("inventory_constraint_satisfied")

    @staticmethod
    def _validate_dialogue_constraint(
        constraint: DialogueOptionConstraint, observation: Observation
    ) -> SafetyResult:
        widgets = observation.widgets
        if (
            not widgets.dialogue_active
            or widgets.dialogue_type != "options"
            or not widgets.dialogue_number_keys
            or constraint.prompt_contains.lower()
            not in widgets.dialogue_prompt.lower()
        ):
            return _reject("dialogue_not_ready")
        matches = [
            option
            for option in widgets.dialogue_options
            if option.visible
            and option.key == constraint.option_key
            and option.index == constraint.option_index
            and option.text == constraint.option_text
        ]
        if len(matches) != 1:
            return _reject("dialogue_option_mismatch")
        return _allow("dialogue_constraint_satisfied")

    @staticmethod
    def _validate_hover_menu(
        action: Action, observation: Observation
    ) -> SafetyResult:
        if action.option is None or action.target_name is None or action.target_id is None:
            return _reject("target_identity_incomplete")
        top = observation.menus[0] if observation.menus else None
        if action.kind is ActionKind.WALK:
            exact_hover = bool(
                top is not None
                and top.option == action.option
                and top.entry_type == "WALK"
            )
        else:
            exact_hover = bool(top is not None and _menu_entry_matches(action, top))
        if not exact_hover:
            return _reject("hover_menu_mismatch")
        return _allow("hover_menu_exact")


def _menu_entry_matches(action: Action, entry: MenuEntry) -> bool:
    return bool(
        action.option is not None
        and action.target_name is not None
        and action.target_id is not None
        and action.target_param0 is not None
        and action.target_param1 is not None
        and entry.option == action.option
        and entry.target == action.target_name
        and entry.identifier == action.target_id
        and entry.param0 == action.target_param0
        and entry.param1 == action.target_param1
    )


def _interface_close_constraint(action: Action) -> InterfaceConstraint | None:
    constraint = action.task_constraints.interface
    if (
        action.kind is ActionKind.PRESS_KEY
        and constraint is not None
        and constraint.require_keyboard_close
    ):
        return constraint
    return None


def _matching_menu_entries(
    action: Action, observation: Observation
) -> list[MenuEntry]:
    return [entry for entry in observation.menus if _menu_entry_matches(action, entry)]


def _validate_exact_target(action: Action, target: NearbyObject) -> SafetyResult:
    if action.option is None or action.target_name is None or action.target_id is None:
        return _reject("target_identity_incomplete")
    if action.option not in target.actions:
        return _reject("target_option_mismatch")
    if action.target_name != target.name:
        return _reject("target_name_mismatch")
    if action.target_id != target.object_id:
        return _reject("target_id_mismatch")
    if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
        if target.scene_x is None or target.scene_y is None:
            return _reject("target_scene_missing")
        if action.target_param0 != target.scene_x or action.target_param1 != target.scene_y:
            return _reject("target_scene_mismatch")
    return _allow("target_exact")


def _select_widget(action: Action, observation: Observation) -> tuple[str, WidgetTarget] | None:
    widgets = {
        DEPOSIT_INVENTORY_WIDGET_KEY: observation.widgets.deposit_inventory,
        CLOSE_BANK_WIDGET_KEY: observation.widgets.close_bank,
    }
    if action.target_key in widgets:
        widget = widgets[action.target_key]
        return (action.target_key, widget) if widget is not None else None
    if action.target_key is not None:
        return None
    matches = [(key, widget) for key, widget in widgets.items()
               if widget is not None and action.target_name == widget.name]
    return matches[0] if len(matches) == 1 else None


def _validate_point(
    point: ScreenPoint | None,
    canvas_bounds: ScreenBounds | None,
    target_bounds: ScreenBounds | None,
) -> SafetyResult:
    if point is None:
        return _reject("screen_point_missing")
    if not _valid_coordinate(point.x) or not _valid_coordinate(point.y):
        return _reject("screen_point_invalid")
    if canvas_bounds is None or not _valid_bounds(canvas_bounds):
        return _reject("canvas_bounds_missing")
    if not canvas_bounds.contains(point):
        return _reject("screen_point_out_of_bounds")
    if target_bounds is not None:
        if not _valid_bounds(target_bounds):
            return _reject("target_bounds_invalid")
        if not target_bounds.contains(point):
            return _reject("screen_point_outside_target")
    return _allow("screen_point_safe")


def _validate_reprojected_point(
    source_point: ScreenPoint | None,
    fresh_point: ScreenPoint,
    canvas_bounds: ScreenBounds | None,
    target_bounds: ScreenBounds | None,
    *,
    allow_drift: bool,
) -> SafetyResult:
    drifted = source_point != fresh_point
    if drifted and (
        not allow_drift or not _points_close(source_point, fresh_point)
    ):
        return _reject("screen_point_not_verified")
    result = _validate_point(fresh_point, canvas_bounds, target_bounds)
    if not result.allowed:
        return result
    return (
        _allow("screen_point_drift_bounded")
        if drifted
        else result
    )


def _valid_coordinate(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_bounds(bounds: ScreenBounds) -> bool:
    return (_valid_coordinate(bounds.x) and _valid_coordinate(bounds.y)
            and isinstance(bounds.width, int) and not isinstance(bounds.width, bool)
            and bounds.width > 0 and isinstance(bounds.height, int)
            and not isinstance(bounds.height, bool) and bounds.height > 0)


def _points_close(
    actual: ScreenPoint | None,
    expected: ScreenPoint | None,
    tolerance: int = POINTER_MATCH_TOLERANCE_PX,
) -> bool:
    return bool(
        actual is not None
        and expected is not None
        and abs(actual.x - expected.x) <= tolerance
        and abs(actual.y - expected.y) <= tolerance
    )


def _record(
    checks: list[SafetyCheck], stage: str, result: SafetyResult
) -> SafetyResult:
    checks.append(SafetyCheck(stage, result.reason, result.allowed))
    return result


def _evaluation(
    result: SafetyResult, checks: list[SafetyCheck]
) -> SafetyEvaluation:
    return SafetyEvaluation(result, tuple(checks))


def _allow(reason: str) -> SafetyResult:
    return SafetyResult(True, reason)


def _reject(reason: str) -> SafetyResult:
    return SafetyResult(False, reason)
