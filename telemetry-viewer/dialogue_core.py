from __future__ import annotations

import re
from typing import Any


SCHEMA = "dialogue_choice.v1"
_TAG_RE = re.compile(r"<[^>]*>")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    return _TAG_RE.sub("", text).replace("\xa0", " ").strip()


def _lower(value: Any) -> str:
    return _clean(value).lower()


def _truthy_or_unknown(value: Any) -> bool:
    return value is None or value == "unknown" or value is True


def expected_climb_direction(route_step: dict[str, Any] | None) -> str | None:
    step = _dict(route_step)
    plane_change = _clean(step.get("planeChange") or step.get("expectedPlaneChange"))
    edge_type = _lower(step.get("edgeType") or step.get("type") or step.get("routeEdgeType") or step.get("label"))
    if plane_change.startswith("+") or plane_change == "1" or "climb_up" in edge_type or " up" in edge_type:
        return "up"
    if plane_change.startswith("-") or plane_change == "-1" or "climb_down" in edge_type or " down" in edge_type:
        return "down"
    return None


def option_matches_direction(option: dict[str, Any], direction: str) -> bool:
    text = _lower(option.get("text"))
    if direction == "up":
        return "climb up" in text or text.startswith("up")
    if direction == "down":
        return "climb down" in text or text.startswith("down")
    return False


def _selection_method(option: dict[str, Any], dialogue_state: dict[str, Any]) -> tuple[str | None, str | None]:
    key = _clean(option.get("key"))
    if key and _truthy_or_unknown(dialogue_state.get("canUseNumberKeys")):
        return "number_key", key
    bounds = _dict(option.get("bounds"))
    if bounds and option.get("visible") is not False:
        return "widget_click", None
    return None, None


def route_dialogue_choice(dialogue_state: dict[str, Any] | None, route_step: dict[str, Any] | None) -> dict[str, Any] | None:
    dialogue = _dict(dialogue_state)
    if dialogue.get("active") is not True or _lower(dialogue.get("type")) != "options":
        return None
    direction = expected_climb_direction(route_step)
    if not direction:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": "route_direction_unknown",
            "dialoguePrompt": _clean(dialogue.get("promptText")),
            "dialogueOptions": list(_list(dialogue.get("options"))),
        }
    matching_options = [dict(option) for option in _list(dialogue.get("options")) if isinstance(option, dict) and option_matches_direction(option, direction)]
    if not matching_options:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": "dialogue_option_not_found",
            "expectedDirection": direction,
            "dialoguePrompt": _clean(dialogue.get("promptText")),
            "dialogueOptions": list(_list(dialogue.get("options"))),
        }
    option = matching_options[0]
    method, key = _selection_method(option, dialogue)
    if not method:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "reason": "dialogue_option_not_selectable",
            "expectedDirection": direction,
            "option": option,
            "dialoguePrompt": _clean(dialogue.get("promptText")),
            "dialogueOptions": list(_list(dialogue.get("options"))),
        }
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "reason": "route_transition_dialogue_choice_ready",
        "expectedDirection": direction,
        "expectedDialogueOption": _clean(option.get("text")),
        "selectedDialogueOption": _clean(option.get("text")),
        "selectionMethod": method,
        "key": key,
        "option": option,
        "dialoguePrompt": _clean(dialogue.get("promptText")),
        "dialogueOptions": list(_list(dialogue.get("options"))),
    }

