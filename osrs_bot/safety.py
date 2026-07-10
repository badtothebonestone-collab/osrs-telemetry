from __future__ import annotations

from dataclasses import dataclass

from .model import (
    LOG_ITEM_ID,
    Action,
    ActionKind,
    MenuEntry,
    NearbyObject,
    Observation,
    ScreenBounds,
    ScreenPoint,
    WidgetTarget,
)


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class SafetyGate:
    max_observation_age_seconds: float = 2.0

    def __post_init__(self) -> None:
        if self.max_observation_age_seconds < 0:
            raise ValueError("max_observation_age_seconds must be non-negative")

    def validate_pre_move(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        common = self._validate_observation(observation)
        if not common.allowed:
            return common
        session = self._validate_session(action, observation)
        if not session.allowed:
            return session
        if action.source_tick != observation.tick:
            return _reject("tick_mismatch")
        if action.kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }:
            sample = self._validate_source_menu_sample(action, observation)
            if not sample.allowed:
                return sample
        if action.kind is ActionKind.PRESS_KEY and not _is_bank_close_key(action):
            if (
                action.source_dialogue_client_tick is None
                or observation.widgets.dialogue_client_tick is None
            ):
                return _reject("dialogue_sample_missing")
            if action.source_dialogue_client_tick != observation.widgets.dialogue_client_tick:
                return _reject("dialogue_sample_mismatch")
        action_result = self._validate_action(action, observation)
        if not action_result.allowed:
            return action_result
        return _allow("pre_move_safe")

    def validate_post_move(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        base = self._validate_post_move_base(action, observation)
        if not base.allowed:
            return base
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            hover_result = self._validate_hover_menu(action, observation)
            if not hover_result.allowed:
                return hover_result
        return _allow("post_move_safe")

    def validate_context_candidate(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        base = self._validate_post_move_base(action, observation)
        if not base.allowed:
            return base
        if action.kind is not ActionKind.INTERACT_OBJECT:
            return _reject("context_selection_unsupported")
        matches = _matching_menu_entries(action, observation)
        if len(matches) != 1 or matches[0] is observation.menus[0]:
            return _reject("context_option_not_unique_lower_entry")
        return _allow("context_candidate_safe")

    def validate_context_menu(
        self,
        action: Action,
        observation: Observation,
        *,
        minimum_menu_client_tick: int,
        row_point: ScreenPoint | None = None,
    ) -> SafetyResult:
        common = self._validate_observation(observation)
        if not common.allowed:
            return common
        session = self._validate_session(action, observation)
        if not session.allowed:
            return session
        if observation.tick < action.source_tick:
            return _reject("tick_mismatch")
        if action.kind is not ActionKind.INTERACT_OBJECT:
            return _reject("context_selection_unsupported")
        if observation.menu_client_tick is None:
            return _reject("menu_sample_missing")
        if observation.menu_client_tick <= minimum_menu_client_tick:
            return _reject("menu_sample_not_newer")
        if not observation.menu_open:
            return _reject("context_menu_not_open")
        action_result = self._validate_action(action, observation)
        if not action_result.allowed:
            return action_result
        matches = _matching_menu_entries(action, observation)
        if len(matches) != 1:
            return _reject("context_option_not_unique")
        entry = matches[0]
        bounds = entry.row_bounds
        menu_bounds = observation.menu_bounds
        if bounds is None or not _valid_bounds(bounds):
            return _reject("context_row_bounds_missing")
        if menu_bounds is None or not _valid_bounds(menu_bounds):
            return _reject("context_menu_bounds_missing")
        if not menu_bounds.contains(bounds.center):
            return _reject("context_row_outside_menu")
        if row_point is None:
            return _allow("context_menu_open_safe")
        if not bounds.contains(row_point) or not menu_bounds.contains(row_point):
            return _reject("context_row_pointer_outside")
        if not _points_close(observation.menu_mouse_screen_point, row_point):
            return _reject("context_row_pointer_mismatch")
        return _allow("context_row_safe")

    def _validate_post_move_base(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        common = self._validate_observation(observation)
        if not common.allowed:
            return common
        session = self._validate_session(action, observation)
        if not session.allowed:
            return session
        if observation.tick < action.source_tick:
            return _reject("tick_mismatch")
        if action.kind in {
            ActionKind.INTERACT_OBJECT,
            ActionKind.WALK,
            ActionKind.CLICK_WIDGET,
        }:
            if action.source_menu_client_tick is None or observation.menu_client_tick is None:
                return _reject("menu_sample_missing")
            if observation.menu_client_tick <= action.source_menu_client_tick:
                return _reject("menu_sample_not_newer")
            if observation.menu_open:
                return _reject("context_menu_open")
            if not _points_close(observation.menu_mouse_screen_point, action.screen_point):
                return _reject("hover_pointer_mismatch")
        if action.kind is ActionKind.PRESS_KEY:
            if _is_bank_close_key(action):
                if observation.tick <= action.source_tick:
                    return _reject("bank_sample_not_newer")
            else:
                if (
                    action.source_dialogue_client_tick is None
                    or observation.widgets.dialogue_client_tick is None
                ):
                    return _reject("dialogue_sample_missing")
                if observation.widgets.dialogue_client_tick <= action.source_dialogue_client_tick:
                    return _reject("dialogue_sample_not_newer")
        action_result = self._validate_action(action, observation)
        if not action_result.allowed:
            return action_result
        return _allow("post_move_base_safe")

    def _validate_observation(self, observation: Observation) -> SafetyResult:
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
        if not observation.widgets.bank_known:
            return _reject("bank_state_unknown")
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

    def _validate_action(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        if action.kind in {ActionKind.INTERACT_OBJECT, ActionKind.WALK}:
            return self._validate_object_action(action, observation)
        if action.kind is ActionKind.CLICK_WIDGET:
            return self._validate_widget_action(action, observation)
        if action.kind is ActionKind.PRESS_KEY:
            if _is_bank_close_key(action):
                return self._validate_bank_close_key(action, observation)
            return self._validate_dialogue_key(action, observation)
        if action.kind is ActionKind.WAIT:
            return _allow("wait_safe")
        return _reject("unsupported_action")

    @staticmethod
    def _validate_dialogue_key(
        action: Action, observation: Observation
    ) -> SafetyResult:
        if action.key not in {str(value) for value in range(1, 10)}:
            return _reject("unsafe_key")
        widgets = observation.widgets
        if (
            not widgets.dialogue_active
            or widgets.dialogue_type != "options"
            or not widgets.dialogue_number_keys
            or "climb" not in widgets.dialogue_prompt.lower()
        ):
            return _reject("dialogue_not_ready")
        matches = [
            option for option in widgets.dialogue_options
            if option.visible
            and option.key == action.key
            and option.index == action.target_id
            and option.text == action.option
            and option.text == action.target_name
            and action.target_key == f"dialogue:{option.index}"
        ]
        if len(matches) != 1:
            return _reject("dialogue_option_mismatch")
        return _allow("dialogue_key_safe")

    @staticmethod
    def _validate_bank_close_key(
        action: Action, observation: Observation
    ) -> SafetyResult:
        if action.key not in {"esc", "escape"}:
            return _reject("unsafe_key")
        if (
            action.option != "Close bank"
            or action.target_name != "Close bank"
            or action.target_key != "close_bank_keyboard"
            or action.target_id != 0
        ):
            return _reject("bank_close_identity_mismatch")
        widgets = observation.widgets
        if observation.plane != 2:
            return _reject("bank_plane_mismatch")
        if not widgets.bank_open:
            return _reject("bank_not_open")
        if not widgets.keyboard_close_possible:
            return _reject("bank_keyboard_close_unavailable")
        return _allow("bank_close_key_safe")

    @staticmethod
    def _validate_source_menu_sample(
        action: Action, observation: Observation
    ) -> SafetyResult:
        if action.source_menu_client_tick is None or observation.menu_client_tick is None:
            return _reject("menu_sample_missing")
        if action.source_menu_client_tick != observation.menu_client_tick:
            return _reject("menu_sample_mismatch")
        return _allow("menu_sample_bound")

    def _validate_object_action(
        self, action: Action, observation: Observation
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
        if action.screen_point != geometry.screen_point:
            return _reject("screen_point_not_verified")
        return _validate_point(
            action.screen_point,
            observation.canvas_bounds,
            geometry.screen_bounds,
        )

    def _validate_widget_action(
        self, action: Action, observation: Observation
    ) -> SafetyResult:
        selected = _select_widget(action, observation)
        if selected is None:
            return _reject("target_missing")
        widget_key, widget = selected
        if not observation.widgets.bank_known:
            return _reject("bank_state_unknown")
        if observation.plane != 2:
            return _reject("bank_plane_mismatch")
        if not observation.widgets.bank_open:
            return _reject("bank_not_open")
        if widget_key == "deposit_inventory" and not observation.widgets.bank_readable:
            return _reject("bank_not_readable")
        if not widget.visible:
            return _reject("widget_not_visible")
        if action.option != widget.name:
            return _reject("target_option_mismatch")
        if action.target_name != widget.name:
            return _reject("target_name_mismatch")
        if widget.screen_point is None:
            return _reject("screen_point_missing")
        if action.screen_point != widget.screen_point:
            return _reject("screen_point_not_verified")
        point_result = _validate_point(
            action.screen_point,
            observation.canvas_bounds,
            widget.screen_bounds,
        )
        if not point_result.allowed:
            return point_result
        if widget_key == "deposit_inventory":
            inventory = observation.inventory
            if not inventory.known:
                return _reject("inventory_unknown")
            if inventory.log_count <= 0:
                return _reject("no_logs_to_deposit")
            if any(item.item_id != LOG_ITEM_ID for item in inventory.items):
                return _reject("unsafe_deposit_inventory")
        return _allow("widget_safe")

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


def _is_bank_close_key(action: Action) -> bool:
    return (
        action.kind is ActionKind.PRESS_KEY
        and action.target_key == "close_bank_keyboard"
    )


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
        "deposit_inventory": observation.widgets.deposit_inventory,
        "close_bank": observation.widgets.close_bank,
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


def _valid_coordinate(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_bounds(bounds: ScreenBounds) -> bool:
    return (_valid_coordinate(bounds.x) and _valid_coordinate(bounds.y)
            and isinstance(bounds.width, int) and not isinstance(bounds.width, bool)
            and bounds.width > 0 and isinstance(bounds.height, int)
            and not isinstance(bounds.height, bool) and bounds.height > 0)


def _points_close(
    actual: ScreenPoint | None, expected: ScreenPoint | None, tolerance: int = 3
) -> bool:
    return bool(
        actual is not None
        and expected is not None
        and abs(actual.x - expected.x) <= tolerance
        and abs(actual.y - expected.y) <= tolerance
    )


def _allow(reason: str) -> SafetyResult:
    return SafetyResult(True, reason)


def _reject(reason: str) -> SafetyResult:
    return SafetyResult(False, reason)
