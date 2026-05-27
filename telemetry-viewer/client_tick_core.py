from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


CLIENT_TICK_HOT_SCHEMA = "client_tick_hot.v1"
_MENU_TAG_RE = re.compile(r"<[^>]*>")


def _clean_menu_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return _MENU_TAG_RE.sub("", text).replace("\xa0", " ").strip()


def _lower_menu_text(value: Any) -> str:
    return _clean_menu_text(value).lower()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _sample_int(sample: dict[str, Any] | None, *keys: str) -> int | None:
    if not isinstance(sample, dict):
        return None
    for key in keys:
        value = _int_or_none(sample.get(key))
        if value is not None:
            return value
    return None


def _string_list(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    return [_clean_menu_text(value) for value in values or [] if _clean_menu_text(value)]


def _menu_entry_from_values(
    *,
    option: Any,
    target: Any = None,
    menu_type: Any = None,
    identifier: Any = None,
    param0: Any = None,
    param1: Any = None,
    source: str = "entry",
) -> dict[str, Any]:
    return {
        "option": _clean_menu_text(option),
        "target": _clean_menu_text(target),
        "type": _clean_menu_text(menu_type),
        "identifier": _int_or_none(identifier),
        "param0": _int_or_none(param0),
        "param1": _int_or_none(param1),
        "source": source,
    }


def _raw_top_entry(sample: dict[str, Any]) -> dict[str, Any] | None:
    option = sample.get("topOption") if sample.get("topOption") is not None else sample.get("option")
    target = sample.get("topTarget") if sample.get("topTarget") is not None else sample.get("target")
    menu_type = sample.get("topType") if sample.get("topType") is not None else sample.get("type")
    identifier = sample.get("topIdentifier") if sample.get("topIdentifier") is not None else sample.get("identifier")
    param0 = sample.get("topParam0") if sample.get("topParam0") is not None else sample.get("param0")
    param1 = sample.get("topParam1") if sample.get("topParam1") is not None else sample.get("param1")
    if option is None and target is None and menu_type is None and identifier is None:
        return None
    return _menu_entry_from_values(
        option=option,
        target=target,
        menu_type=menu_type,
        identifier=identifier,
        param0=param0,
        param1=param1,
        source="raw_top",
    )


def _normalise_menu_entry(entry: Any, *, index: int | None = None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    normalised = _menu_entry_from_values(
        option=entry.get("option", entry.get("topOption")),
        target=entry.get("target", entry.get("topTarget")),
        menu_type=entry.get("type", entry.get("topType")),
        identifier=entry.get("identifier", entry.get("topIdentifier")),
        param0=entry.get("param0", entry.get("topParam0")),
        param1=entry.get("param1", entry.get("topParam1")),
        source="entries",
    )
    if index is not None:
        normalised["entryIndex"] = index
    return normalised


def _sample_entries(sample: dict[str, Any]) -> list[dict[str, Any]]:
    entries = sample.get("entries")
    if not isinstance(entries, list):
        return []
    normalised: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        item = _normalise_menu_entry(entry, index=index)
        if item is not None:
            normalised.append(item)
    return normalised


def is_cancel_entry(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    option = _lower_menu_text(entry.get("option") or entry.get("topOption"))
    menu_type = _lower_menu_text(entry.get("type") or entry.get("topType"))
    return option == "cancel" or menu_type == "cancel"


def is_walk_here_entry(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    option = _lower_menu_text(entry.get("option") or entry.get("topOption"))
    menu_type = _lower_menu_text(entry.get("type") or entry.get("topType"))
    return "walk here" in option or option == "walk" or menu_type == "walk"


def get_actionable_entries(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    entries = _sample_entries(sample)
    if not entries:
        top = _raw_top_entry(sample)
        entries = [top] if top is not None else []
    return [entry for entry in entries if not is_cancel_entry(entry)]


def get_left_click_entry(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(sample, dict):
        return None
    raw_top = _raw_top_entry(sample)
    entries = _sample_entries(sample)
    if raw_top is not None and not is_cancel_entry(raw_top):
        selected = dict(raw_top)
        selected["selectionReason"] = "raw_top_entry"
        return selected
    if raw_top is not None and is_cancel_entry(raw_top):
        if sample.get("menuOpen") is True:
            selected = dict(raw_top)
            selected["selectionReason"] = "menu_open_cancel_top"
            selected["rawTopEntry"] = dict(raw_top)
            return selected
        for entry in entries:
            if not is_cancel_entry(entry):
                selected = dict(entry)
                selected["selectionReason"] = "cancel_sentinel_ignored"
                selected["rawTopEntry"] = dict(raw_top)
                return selected
        selected = dict(raw_top)
        selected["selectionReason"] = "cancel_left_click"
        selected["rawTopEntry"] = dict(raw_top)
        return selected
    if entries:
        selected = dict(entries[0])
        selected["selectionReason"] = "first_menu_entry"
        return selected
    return None


@dataclass(frozen=True)
class ActionIntent:
    activity: str = "generic"
    expected_options: tuple[str, ...] = ()
    expected_targets: tuple[str, ...] = ()
    expected_object_ids: tuple[int, ...] = ()
    allow_menu_types: tuple[str, ...] = ()
    reject_options: tuple[str, ...] = ("Walk here",)
    position_tolerance_px: int = 3
    freshness_millis: int = 120

    @classmethod
    def for_target(
        cls,
        *,
        activity: str = "generic",
        target_name: str | None = None,
        object_id: int | None = None,
        expected_options: list[str] | tuple[str, ...] | None = None,
        expected_targets: list[str] | tuple[str, ...] | None = None,
        expected_object_ids: list[int] | tuple[int, ...] | None = None,
        allow_menu_types: list[str] | tuple[str, ...] | None = None,
        reject_options: list[str] | tuple[str, ...] | None = None,
        position_tolerance_px: int = 3,
        freshness_millis: int = 120,
    ) -> "ActionIntent":
        object_ids = list(expected_object_ids or [])
        if object_id is not None:
            object_ids.append(int(object_id))
        targets = _string_list(list(expected_targets or []))
        if target_name:
            targets.append(_clean_menu_text(target_name))
        deduped_targets = tuple(dict.fromkeys(target for target in targets if target))
        deduped_ids = tuple(dict.fromkeys(object_ids))
        return cls(
            activity=activity or "generic",
            expected_options=tuple(_string_list(list(expected_options or []))),
            expected_targets=deduped_targets,
            expected_object_ids=deduped_ids,
            allow_menu_types=tuple(_string_list(list(allow_menu_types or []))),
            reject_options=tuple(_string_list(list(reject_options or ["Walk here"]))),
            position_tolerance_px=max(0, int(position_tolerance_px)),
            freshness_millis=max(0, int(freshness_millis)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "action_intent.v1",
            "activity": self.activity,
            "expectedOptions": list(self.expected_options),
            "expectedTargets": list(self.expected_targets),
            "expectedObjectIds": list(self.expected_object_ids),
            "allowMenuTypes": list(self.allow_menu_types),
            "rejectOptions": list(self.reject_options),
            "positionTolerancePx": self.position_tolerance_px,
            "freshnessMillis": self.freshness_millis,
        }


@dataclass
class HoverMenuMatchResult:
    confirmed: bool
    reason: str
    sample: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "reason": self.reason,
            "mismatchReason": self.details.get("mismatchReason") if isinstance(self.details, dict) else None,
            "sample": self.sample,
            "details": dict(self.details),
        }


def latest_hot_state(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    hot = snapshot.get("clientTickHot")
    if isinstance(hot, dict):
        return hot
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    for key in ("interaction_hot", "client_tick_tail"):
        value = payloads.get(key)
        if isinstance(value, dict):
            return value
    return None


def latest_hover_menu_sample(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    hot = latest_hot_state(snapshot)
    if isinstance(hot, dict):
        for key in ("postMenuSort", "hoverMenu"):
            value = hot.get(key)
            if isinstance(value, dict):
                return value
    sample = snapshot.get("hoverMenu")
    if isinstance(sample, dict):
        return sample
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    for payload_name in ("baseline", "writer_health"):
        payload = payloads.get(payload_name)
        if isinstance(payload, dict) and isinstance(payload.get("hoverMenu"), dict):
            return payload.get("hoverMenu")
    return None


def latest_menu_option_clicked_sample(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    hot = latest_hot_state(snapshot)
    if isinstance(hot, dict) and isinstance(hot.get("lastMenuOptionClicked"), dict):
        return hot.get("lastMenuOptionClicked")
    sample = snapshot.get("lastMenuOptionClicked")
    if isinstance(sample, dict):
        return sample
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    for payload_name in ("baseline", "writer_health"):
        payload = payloads.get(payload_name)
        if isinstance(payload, dict) and isinstance(payload.get("lastMenuOptionClicked"), dict):
            return payload.get("lastMenuOptionClicked")
    return None


def _tail_hot_payloads(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    payloads: list[dict[str, Any]] = []
    for value in (snapshot.get("clientTickHot"), snapshot):
        if isinstance(value, dict):
            payloads.append(value)
    nested = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    for key in ("client_tick_tail", "interaction_hot"):
        value = nested.get(key)
        if isinstance(value, dict):
            payloads.append(value)
    return payloads


def _sample_key(sample: dict[str, Any]) -> tuple[Any, ...]:
    return (
        sample.get("clientTick"),
        sample.get("wallTimeMillis"),
        sample.get("mouseCanvasX"),
        sample.get("mouseCanvasY"),
        sample.get("topOption") if sample.get("topOption") is not None else sample.get("option"),
        sample.get("topTarget") if sample.get("topTarget") is not None else sample.get("target"),
        sample.get("topType") if sample.get("topType") is not None else sample.get("type"),
        sample.get("topIdentifier") if sample.get("topIdentifier") is not None else sample.get("identifier"),
    )


def post_menu_sort_tail_samples(snapshot: dict[str, Any] | None, *, include_latest: bool = False) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for payload in _tail_hot_payloads(snapshot):
        for key in ("postMenuSortTail", "hoverMenuTail", "menuTail"):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                key_value = _sample_key(item)
                if key_value in seen:
                    continue
                seen.add(key_value)
                samples.append(dict(item))
    if include_latest:
        latest = latest_hover_menu_sample(snapshot)
        if isinstance(latest, dict):
            key_value = _sample_key(latest)
            if key_value not in seen:
                samples.append(dict(latest))
    return samples


def _sample_mouse_delta(sample: dict[str, Any], canvas_point: dict[str, Any]) -> tuple[int | None, int | None]:
    mouse_x = _sample_int(sample, "mouseCanvasX")
    mouse_y = _sample_int(sample, "mouseCanvasY")
    expected_x = _int_or_none(canvas_point.get("x")) if isinstance(canvas_point, dict) else None
    expected_y = _int_or_none(canvas_point.get("y")) if isinstance(canvas_point, dict) else None
    if mouse_x is None or mouse_y is None or expected_x is None or expected_y is None:
        return None, None
    return abs(mouse_x - expected_x), abs(mouse_y - expected_y)


def _compact_tail_sample(sample: dict[str, Any]) -> dict[str, Any]:
    entry = get_left_click_entry(sample) or _raw_top_entry(sample) or {}
    return {
        "clientTick": sample.get("clientTick"),
        "wallTimeMillis": sample.get("wallTimeMillis"),
        "mouseCanvasX": sample.get("mouseCanvasX"),
        "mouseCanvasY": sample.get("mouseCanvasY"),
        "option": entry.get("option") or sample.get("topOption") or sample.get("option"),
        "target": entry.get("target") or sample.get("topTarget") or sample.get("target"),
        "type": entry.get("type") or sample.get("topType") or sample.get("type"),
        "identifier": entry.get("identifier") if entry.get("identifier") is not None else sample.get("topIdentifier") or sample.get("identifier"),
        "classification": classify_menu_action(sample),
    }


def menu_tail_volatility(
    snapshot: dict[str, Any] | None,
    canvas_point: dict[str, Any],
    intent: ActionIntent,
    *,
    tolerance_px: int | None = None,
    tail_limit: int = 8,
) -> dict[str, Any]:
    samples = post_menu_sort_tail_samples(snapshot, include_latest=True)
    tolerance = intent.position_tolerance_px if tolerance_px is None else max(0, int(tolerance_px))
    near_samples: list[dict[str, Any]] = []
    volatile_reasons: list[str] = []
    matching_walk_here_samples = 0
    expected_matches = 0

    for sample in samples[-max(1, int(tail_limit or 1)) :]:
        dx, dy = _sample_mouse_delta(sample, canvas_point)
        if dx is None or dy is None or dx > tolerance or dy > tolerance:
            continue
        compact = _compact_tail_sample(sample)
        compact["dx"] = dx
        compact["dy"] = dy
        near_samples.append(compact)
        match = hover_sample_matches_intent(sample, intent, canvas_point, tolerance_px=tolerance)
        if match.confirmed:
            expected_matches += 1
            if is_walk_here_entry(get_left_click_entry(sample)):
                matching_walk_here_samples += 1
            continue
        classification = compact.get("classification")
        reason = None
        if classification == "npc_action":
            reason = "recent_npc_action"
        elif classification == "object_action":
            reason = "recent_object_action"
        elif classification == "item_action":
            reason = "recent_item_action"
        elif classification == "widget_action":
            reason = "recent_widget_action"
        elif classification == "cancel_hover":
            reason = "recent_cancel_hover"
        if reason:
            volatile_reasons.append(reason)

    deduped_reasons = list(dict.fromkeys(volatile_reasons))
    return {
        "schema": "menu_tail_volatility.v1",
        "volatileHoverZone": bool(deduped_reasons),
        "volatileReasons": deduped_reasons,
        "recentMenuTail": near_samples,
        "matchingWalkHereSamples": matching_walk_here_samples,
        "expectedMatches": expected_matches,
        "sampleCount": len(samples),
        "nearSampleCount": len(near_samples),
        "positionTolerancePx": tolerance,
    }


def classify_menu_action(sample: dict[str, Any] | None) -> str:
    if not isinstance(sample, dict):
        return "unknown"
    entry = get_left_click_entry(sample) or {}
    option = _lower_menu_text(entry.get("option") or sample.get("topOption") or sample.get("option"))
    menu_type = _lower_menu_text(entry.get("type") or sample.get("topType") or sample.get("type"))
    if is_cancel_entry(entry):
        return "cancel_hover"
    if is_walk_here_entry(entry):
        return "walk_here"
    if "npc" in menu_type:
        return "npc_action"
    if "item" in menu_type or "ground_item" in menu_type:
        return "item_action"
    if "widget" in menu_type or "cc_op" in menu_type:
        return "widget_action"
    if "object" in menu_type or "game_object" in menu_type:
        return "object_action"
    if sample.get("topIdentifier") is not None or sample.get("identifier") is not None:
        return "object_action" if option else "unknown"
    return "unknown"


def _option_matches(option: str, expected: tuple[str, ...]) -> bool:
    if not expected:
        return True
    return any(_lower_menu_text(item) in option for item in expected if _lower_menu_text(item))


def _option_rejected(option: str, rejected: tuple[str, ...]) -> bool:
    return any(_lower_menu_text(item) in option for item in rejected if _lower_menu_text(item))


def _type_allowed(sample: dict[str, Any], intent: ActionIntent, *, top_prefix: bool = True) -> bool:
    if not intent.allow_menu_types:
        return True
    menu_type = _lower_menu_text(sample.get("topType" if top_prefix else "type") or sample.get("type"))
    if not menu_type:
        return True
    return any(_lower_menu_text(item) in menu_type for item in intent.allow_menu_types if _lower_menu_text(item))


def _target_text_matches_expected(target: str, expected: str) -> bool:
    target_key = _lower_menu_text(target)
    expected_key = _lower_menu_text(expected)
    if not expected_key:
        return False
    if expected_key == "tree":
        return target_key == "tree" or target_key.startswith("tree ") or target_key.startswith("tree/")
    if expected_key == "dead tree":
        return target_key == "dead tree" or target_key.startswith("dead tree ")
    if expected_key in {"oak", "oak tree"}:
        return target_key in {"oak", "oak tree"} or target_key.startswith("oak tree ")
    return expected_key in target_key


def _target_matches(sample: dict[str, Any], intent: ActionIntent, *, top_prefix: bool = True) -> bool:
    identifier = _sample_int(sample, "topIdentifier" if top_prefix else "identifier", "identifier")
    if identifier is not None and identifier in intent.expected_object_ids:
        return True
    target = _lower_menu_text(sample.get("topTarget" if top_prefix else "target") or sample.get("target"))
    if not intent.expected_targets and not intent.expected_object_ids:
        return True
    return any(_target_text_matches_expected(target, expected) for expected in intent.expected_targets if _lower_menu_text(expected))


def menu_entry_matches_intent(entry: dict[str, Any] | None, intent: ActionIntent) -> bool:
    if not isinstance(entry, dict) or is_cancel_entry(entry):
        return False
    option = _lower_menu_text(entry.get("option") or entry.get("topOption"))
    if _option_rejected(option, intent.reject_options):
        return False
    if is_walk_here_entry(entry) and not _option_matches(option, intent.expected_options):
        return False
    return (
        _option_matches(option, intent.expected_options)
        and _type_allowed(entry, intent, top_prefix=False)
        and _target_matches(entry, intent, top_prefix=False)
    )


def expected_entries_not_top(sample: dict[str, Any] | None, intent: ActionIntent) -> list[dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    selected = get_left_click_entry(sample)
    selected_index = selected.get("entryIndex") if isinstance(selected, dict) else None
    matches: list[dict[str, Any]] = []
    for entry in get_actionable_entries(sample):
        if selected_index is not None and entry.get("entryIndex") == selected_index:
            continue
        if selected_index is None and selected is not None and same_menu_option_sample(entry, selected):
            continue
        if menu_entry_matches_intent(entry, intent):
            matches.append(dict(entry))
    return matches


def hover_sample_matches_intent(
    sample: dict[str, Any] | None,
    intent: ActionIntent,
    canvas_point: dict[str, Any],
    *,
    tolerance_px: int | None = None,
    min_wall_time_millis: int | None = None,
) -> HoverMenuMatchResult:
    if not isinstance(sample, dict):
        return HoverMenuMatchResult(False, "hover_menu_missing", details={"mismatchReason": "stale_hover_sample"})
    wall_time_millis = _sample_int(sample, "wallTimeMillis")
    if min_wall_time_millis is not None and wall_time_millis is not None and wall_time_millis < min_wall_time_millis:
        return HoverMenuMatchResult(False, "hover_menu_stale", sample, {"mismatchReason": "stale_hover_sample"})

    mouse_x = _sample_int(sample, "mouseCanvasX")
    mouse_y = _sample_int(sample, "mouseCanvasY")
    expected_x = _int_or_none(canvas_point.get("x")) if isinstance(canvas_point, dict) else None
    expected_y = _int_or_none(canvas_point.get("y")) if isinstance(canvas_point, dict) else None
    if mouse_x is None or mouse_y is None or expected_x is None or expected_y is None:
        return HoverMenuMatchResult(False, "mouse_position_missing", sample, {"mismatchReason": "stale_hover_sample"})
    dx = abs(mouse_x - expected_x)
    dy = abs(mouse_y - expected_y)
    tolerance = intent.position_tolerance_px if tolerance_px is None else max(0, int(tolerance_px))
    if dx > tolerance or dy > tolerance:
        return HoverMenuMatchResult(
            False,
            "mouse_position_outside_tolerance",
            sample,
            {"dx": dx, "dy": dy, "tolerancePx": tolerance, "mismatchReason": "hover_position_mismatch"},
        )

    selected_entry = get_left_click_entry(sample)
    if not isinstance(selected_entry, dict):
        return HoverMenuMatchResult(False, "menu_entry_missing", sample, {"mismatchReason": "hover_option_mismatch"})
    details = {
        "dx": dx,
        "dy": dy,
        "tolerancePx": tolerance,
        "rawTopEntry": selected_entry.get("rawTopEntry") or _raw_top_entry(sample),
        "selectedMenuEntry": selected_entry,
        "menuSelectionReason": selected_entry.get("selectionReason"),
        "menuOpen": sample.get("menuOpen"),
    }
    lower_expected_entries = expected_entries_not_top(sample, intent)
    if lower_expected_entries:
        details["expectedEntryPresentButNotTop"] = True
        details["lowerHoverEntries"] = lower_expected_entries
        details["lowerMenuWouldWorkPotentially"] = True
        if intent.activity == "woodcutting":
            details["rightClickResourceSelectionDeferred"] = True
    if selected_entry.get("selectionReason") == "menu_open_cancel_top":
        return HoverMenuMatchResult(False, "menu_state_ambiguous", sample, {**details, "mismatchReason": "hover_option_mismatch"})
    if is_cancel_entry(selected_entry):
        return HoverMenuMatchResult(False, "cancel_hover", sample, {**details, "mismatchReason": "hover_option_mismatch"})
    option = _lower_menu_text(selected_entry.get("option"))
    if _option_rejected(option, intent.reject_options):
        return HoverMenuMatchResult(False, "top_option_rejected", sample, {**details, "topOption": selected_entry.get("option"), "mismatchReason": "hover_option_mismatch"})
    if is_walk_here_entry(selected_entry) and not _option_matches(option, intent.expected_options):
        return HoverMenuMatchResult(False, "top_option_rejected", sample, {**details, "topOption": selected_entry.get("option"), "mismatchReason": "hover_option_mismatch"})
    if not _option_matches(option, intent.expected_options):
        return HoverMenuMatchResult(False, "top_option_not_expected", sample, {**details, "topOption": selected_entry.get("option"), "mismatchReason": "hover_option_mismatch"})
    if not _type_allowed(selected_entry, intent, top_prefix=False):
        return HoverMenuMatchResult(False, "top_type_not_allowed", sample, {**details, "topType": selected_entry.get("type"), "mismatchReason": "wrong_intent_matcher"})
    if not _target_matches(selected_entry, intent, top_prefix=False):
        return HoverMenuMatchResult(False, "top_target_not_expected", sample, {**details, "mismatchReason": "hover_target_mismatch"})
    return HoverMenuMatchResult(
        True,
        "hover_menu_confirmed",
        sample,
        {
            **details,
            "menuActionClass": classify_menu_action(sample),
            "matchedIntent": intent.activity,
        },
    )


def same_menu_option_sample(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    keys = ("clientTick", "wallTimeMillis", "option", "target", "type", "identifier", "param0", "param1")
    return all(left.get(key) == right.get(key) for key in keys)


def classify_clicked_menu(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    intent: ActionIntent,
) -> str:
    if not isinstance(after, dict):
        return "unknown_click_result"
    if same_menu_option_sample(before, after):
        return "unknown_click_result"
    entry = get_left_click_entry(after) or {}
    option = _lower_menu_text(entry.get("option") or after.get("option") or after.get("topOption"))
    if is_cancel_entry(entry):
        return "clicked_cancel"
    if _option_rejected(option, intent.reject_options):
        return "clicked_walk_here"
    if _option_matches(option, intent.expected_options) and _type_allowed(entry or after, intent, top_prefix=False) and _target_matches(entry or after, intent, top_prefix=False):
        return "clicked_expected_action"
    if is_walk_here_entry(entry):
        return "clicked_walk_here"
    action_class = classify_menu_action(after)
    if action_class != "unknown":
        return f"clicked_{action_class}"
    return "unknown_click_result"


def _proposal_target_id(proposal: Any) -> int | None:
    explanation = getattr(proposal, "target_explanation", None)
    explanation = explanation if isinstance(explanation, dict) else {}
    for key in ("objectId", "id", "rawId", "identifier"):
        value = _int_or_none(explanation.get(key))
        if value is not None:
            return value
    return None


def action_intent_from_proposal(proposal: Any, *, tolerance_px: int = 3, freshness_millis: int = 120) -> ActionIntent:
    explanation = getattr(proposal, "target_explanation", None)
    explanation = explanation if isinstance(explanation, dict) else {}
    target_name = str(getattr(proposal, "target_name", "") or explanation.get("name") or "")
    class_id = str(explanation.get("classId") or explanation.get("class") or "").lower()
    proposed_action = str(getattr(proposal, "proposed_action", "") or "")
    object_id = _proposal_target_id(proposal)
    actions = _string_list(explanation.get("actions") if isinstance(explanation.get("actions"), list) else [])

    if proposed_action in {"navigate_to_service", "return_to_resource_area"} or str(getattr(proposal, "target_kind", "") or "") == "path_tile":
        return ActionIntent.for_target(
            activity="service_navigation",
            target_name="",
            expected_options=["Walk here"],
            expected_targets=[],
            allow_menu_types=["WALK", "walk"],
            reject_options=["Cancel"],
            position_tolerance_px=tolerance_px,
            freshness_millis=freshness_millis,
        )

    if proposed_action == "select_resource_target" and ("tree" in target_name.lower() or class_id == "tree"):
        lower_name = target_name.lower()
        if "oak" in lower_name:
            expected_targets = ["Oak tree", "Oak"]
        elif "dead" in lower_name:
            expected_targets = ["Dead tree"]
        elif target_name:
            expected_targets = [target_name]
        else:
            expected_targets = ["Tree", "Dead tree"]
        return ActionIntent.for_target(
            activity="woodcutting",
            target_name=target_name,
            object_id=object_id,
            expected_options=["Chop down", "Chop"],
            expected_targets=expected_targets,
            allow_menu_types=["GAME_OBJECT_FIRST_OPTION", "GAME_OBJECT_SECOND_OPTION", "object"],
            reject_options=["Walk here"],
            position_tolerance_px=tolerance_px,
            freshness_millis=freshness_millis,
        )

    expected_options = actions or _string_list(explanation.get("expectedOptions") if isinstance(explanation.get("expectedOptions"), list) else [])
    expected_targets = _string_list(explanation.get("expectedTargets") if isinstance(explanation.get("expectedTargets"), list) else [])
    dialogue_openers = _string_list(explanation.get("dialogueOpenerOptions") if isinstance(explanation.get("dialogueOpenerOptions"), list) else [])
    if dialogue_openers:
        expected_options = list(dict.fromkeys([*expected_options, *dialogue_openers]))
    return ActionIntent.for_target(
        activity=str(explanation.get("profile") or "generic"),
        target_name=target_name,
        object_id=object_id,
        expected_options=expected_options,
        expected_targets=expected_targets or ([target_name] if target_name else []),
        reject_options=["Walk here"],
        position_tolerance_px=tolerance_px,
        freshness_millis=freshness_millis,
    )


def compact_hot_explanation(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    hot = latest_hot_state(snapshot) or {}
    hover = latest_hover_menu_sample(snapshot)
    clicked = latest_menu_option_clicked_sample(snapshot)
    latency = hot.get("latency") if isinstance(hot.get("latency"), dict) else {}
    return {
        "schema": CLIENT_TICK_HOT_SCHEMA,
        "clientTick": hot.get("clientTick"),
        "gameTickAtSample": hot.get("gameTickAtSample"),
        "mouse": hot.get("mouse") if isinstance(hot.get("mouse"), dict) else None,
        "topOption": hover.get("topOption") if isinstance(hover, dict) else None,
        "topTarget": hover.get("topTarget") if isinstance(hover, dict) else None,
        "postMenuSortAgeMillis": latency.get("postMenuSortAgeMillis"),
        "lastClickedOption": clicked.get("option") if isinstance(clicked, dict) else None,
        "lastClickedTarget": clicked.get("target") if isinstance(clicked, dict) else None,
        "lastClickAgeMillis": latency.get("lastClickAgeMillis"),
        "samplesBuffered": latency.get("samplesBuffered"),
    }
