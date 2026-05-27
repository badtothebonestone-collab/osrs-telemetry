from __future__ import annotations

import json
import time
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


SERVICE_ROUTES_SCHEMA = "service_routes.v1"
SERVICE_ROUTE_SCHEMA = "service_route.v1"
SERVICE_ROUTE_CONTEXT_SCHEMA = "service_route_context.v1"
RETURN_ROUTE_CONTEXT_SCHEMA = "return_route_context.v1"
ROUTE_CONTEXT_SCHEMA = "route_context.v1"
SERVICE_ROUTE_OBJECT_CENSUS_SCHEMA = "service_route_object_census.v1"
SERVICE_OBJECT_CENSUS_SCHEMA = "service_object_census.v1"
ROUTE_RELEVANCE_SCHEMA = "route_relevance.v1"

DEFAULT_SERVICE_ROUTES_PATH = Path(__file__).resolve().parent / "profiles" / "service_routes.json"

ROUTE_TRANSITION_OBJECT_WORDS = ("stair", "stairs", "staircase", "ladder")
ROUTE_TRANSITION_ACTION_WORDS = ("climb", "climb up", "climb down", "climb-up", "climb-down", "top floor", "top-floor", "bottom floor", "bottom-floor", "open")
SERVICE_OBJECT_WORDS = ("bank", "bank booth", "banker", "deposit", "deposit box", "bank chest")
SERVICE_ACTION_WORDS = ("bank", "use", "deposit")
ROUTE_OBJECT_SCAN_LIMIT = 64
ROUTE_RELEVANCE_SEARCH_RADIUS_TILES = 12
KNOWN_ROUTE_SOURCE_RADIUS_TILES = 10
NEARBY_ROUTE_SOURCE_RADIUS_TILES = 20
KNOWN_ROUTE_NODE_RADIUS_TILES = 6
GOAL_DIRECTED_APPROACH_NODE_IDS = (
    "lumbridge_bridge_east_approach",
    "lumbridge_bridge_west_approach",
    "lumbridge_castle_south_entrance_approach",
    "lumbridge_castle_entrance_or_courtyard",
    "lumbridge_castle_west_approach",
)


@dataclass
class ServiceRouteState:
    active_route_id: str | None = None
    current_step_index: int | None = None
    observed_anchors: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_steps: list[str] = field(default_factory=list)
    last_updated_tick: int | None = None
    last_reason: str | None = None

    def reset_for_route(self, route_id: str | None) -> None:
        if route_id == self.active_route_id:
            return
        self.active_route_id = route_id
        self.current_step_index = None
        self.observed_anchors.clear()
        self.completed_steps.clear()
        self.last_updated_tick = None
        self.last_reason = "route_changed"


def _dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int | None:
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return " ".join(_text(value).lower().replace("-", " ").replace("_", " ").split())


def _contains_any(haystack: Any, needles: list[Any]) -> bool:
    text = _norm(haystack)
    return bool(text and any(_norm(needle) in text for needle in needles if _norm(needle)))


def _route_profiles(route: dict[str, Any]) -> set[str]:
    values = set()
    if route.get("profile"):
        values.add(str(route.get("profile")))
    values.update(str(item) for item in _list(route.get("profiles")) if item)
    return values


def _service_type_matches(route_service_type: Any, requested_service_type: Any) -> bool:
    route_text = _norm(route_service_type)
    requested_text = _norm(requested_service_type)
    if not route_text or not requested_text:
        return True
    if route_text == requested_text:
        return True
    if route_text == "bank" and requested_text.startswith("bank"):
        return True
    return requested_text == "bank" and route_text.startswith("bank")


def load_service_routes(path: Path | str | None = None) -> list[dict[str, Any]]:
    route_path = Path(path) if path is not None else DEFAULT_SERVICE_ROUTES_PATH
    if not route_path.exists():
        return []
    with route_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    routes = []
    for route in _list(_dict(payload).get("routes")):
        if not isinstance(route, dict):
            continue
        item = deepcopy(route)
        item.setdefault("schema", SERVICE_ROUTE_SCHEMA)
        routes.append(item)
    return routes


def select_service_route(
    routes: list[dict[str, Any]] | None = None,
    *,
    profile: str | None = None,
    service_type: str | None = None,
    route_id: str | None = None,
) -> dict[str, Any] | None:
    for route in routes or []:
        if not isinstance(route, dict):
            continue
        aliases = {str(value) for value in _list(route.get("aliases")) if value}
        destination_route_id = _text(route.get("destinationRouteId"))
        if route_id and route.get("routeId") != route_id and route_id not in aliases and route_id != destination_route_id:
            continue
        profiles = _route_profiles(route)
        if profile and profiles and profile not in profiles:
            continue
        if service_type and route.get("serviceType") and not _service_type_matches(route.get("serviceType"), service_type):
            continue
        return deepcopy(route)
    return None


def _player_plane(player_context: Any) -> int | None:
    player = _dict(player_context)
    return _int(player.get("plane"))


def _player_location_info(player_context: Any) -> dict[str, Any]:
    player = _dict(player_context)
    tile = player.get("worldTile") if isinstance(player.get("worldTile"), dict) else player.get("tile")
    source = _text(player.get("location_source") or player.get("locationSource"))
    confidence_value = player.get("location_confidence") if player.get("location_confidence") is not None else player.get("locationConfidence")
    confidence = float(confidence_value) if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool) else None
    if isinstance(tile, dict):
        world_x = _int(tile.get("worldX", tile.get("x")))
        world_y = _int(tile.get("worldY", tile.get("y")))
        plane = _int(tile.get("plane"))
        return {
            "tile": {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else _player_plane(player)}
            if world_x is not None and world_y is not None
            else None,
            "source": source or "player_context",
            "confidence": confidence if confidence is not None else 1.0,
        }
    world_x = _int(player.get("worldX") if "worldX" in player else player.get("world_x"))
    world_y = _int(player.get("worldY") if "worldY" in player else player.get("world_y"))
    plane = _player_plane(player)
    if world_x is not None and world_y is not None:
        return {
            "tile": {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else 0},
            "source": source or "player_context",
            "confidence": confidence if confidence is not None else 1.0,
        }
    for key in ("collisionWindowCenterWorld", "pathingCollisionWindowCenterWorld"):
        center = player.get(key)
        if isinstance(center, dict):
            center_x = _int(center.get("worldX", center.get("x")))
            center_y = _int(center.get("worldY", center.get("y")))
            center_plane = _int(center.get("plane"))
            if center_x is not None and center_y is not None:
                return {
                    "tile": {"worldX": center_x, "worldY": center_y, "plane": center_plane if center_plane is not None else 0},
                    "source": "collision_window_center_proxy",
                    "confidence": confidence if confidence is not None else 0.35,
                }
    return {"tile": None, "source": source or None, "confidence": confidence}


def _player_tile(player_context: Any) -> dict[str, Any] | None:
    info = _player_location_info(player_context)
    return info.get("tile") if isinstance(info.get("tile"), dict) else None


def _candidate_name(candidate: dict[str, Any]) -> str:
    return _text(candidate.get("targetName") or candidate.get("name") or candidate.get("classId") or candidate.get("targetType"))


def _candidate_actions(candidate: dict[str, Any]) -> list[str]:
    actions = []
    for key in ("actions", "menuActions", "actionNames", "expectedOptions"):
        value = candidate.get(key)
        if isinstance(value, list):
            actions.extend(_text(item) for item in value if _text(item))
    return list(dict.fromkeys(actions))


def _preferred_candidate_action(candidate: dict[str, Any] | None, step: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    selected = _text(candidate.get("selectedAction") or candidate.get("matchedAction"))
    if selected:
        return selected
    actions = _candidate_actions(candidate)
    if not actions:
        return None
    expected = list(_list((step or {}).get("expectedOptions")))
    for expected_action in expected:
        expected_norm = _norm(expected_action)
        if not expected_norm:
            continue
        for action in actions:
            if _norm(action) == expected_norm or _contains_any(action, [expected_action]):
                return action
    return actions[0]


def _candidate_id(candidate: dict[str, Any]) -> int | None:
    for key in ("id", "rawId", "objectId", "identifier"):
        value = _int(candidate.get(key))
        if value is not None:
            return value
    return None


def _candidate_key(candidate: dict[str, Any]) -> str:
    for key in ("objectKey", "targetKey", "candidateKey", "key", "hash"):
        value = candidate.get(key)
        if value is not None:
            return f"{key}:{value}"
    parts = [_candidate_id(candidate), candidate.get("worldX"), candidate.get("worldY"), candidate.get("plane"), _candidate_name(candidate)]
    return ":".join(str(part) for part in parts if part is not None)


def _candidate_world_tile(candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    candidate = candidate if isinstance(candidate, dict) else {}
    world_x = _int(candidate.get("worldX"))
    world_y = _int(candidate.get("worldY"))
    plane = _int(candidate.get("plane"))
    if world_x is None or world_y is None:
        target_world = _dict(candidate.get("targetWorld") or candidate.get("world"))
        world_x = _int(target_world.get("worldX") if target_world.get("worldX") is not None else target_world.get("x"))
        world_y = _int(target_world.get("worldY") if target_world.get("worldY") is not None else target_world.get("y"))
        if plane is None:
            plane = _int(target_world.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else 0}


def _route_node_by_id(route: dict[str, Any], node_id: str | None) -> dict[str, Any]:
    if not node_id:
        return {}
    for node in _route_nodes(route):
        if node.get("nodeId") == node_id:
            return node
    return {}


def _step_expected_location(route: dict[str, Any], step: dict[str, Any], index: int | None) -> dict[str, Any] | None:
    location = _dict(step.get("worldLocation"))
    if location:
        world_x = _int(location.get("worldX") if location.get("worldX") is not None else location.get("x"))
        world_y = _int(location.get("worldY") if location.get("worldY") is not None else location.get("y"))
        if world_x is not None and world_y is not None:
            plane = _int(location.get("plane") if location.get("plane") is not None else step.get("plane"))
            return {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else 0}

    node = _route_node_by_id(route, _step_node_id(step, index))
    node_location = _dict(node.get("worldLocation"))
    if node_location:
        world_x = _int(node_location.get("worldX") if node_location.get("worldX") is not None else node_location.get("x"))
        world_y = _int(node_location.get("worldY") if node_location.get("worldY") is not None else node_location.get("y"))
        if world_x is not None and world_y is not None:
            plane = _int(node_location.get("plane") if node_location.get("plane") is not None else node.get("plane") if node.get("plane") is not None else step.get("plane"))
            return {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else 0}

    if index is not None:
        steps = _list(route.get("steps"))
        step_plane = _int(step.get("plane"))
        for prior_index in range(index - 1, -1, -1):
            prior = steps[prior_index] if prior_index < len(steps) and isinstance(steps[prior_index], dict) else {}
            if prior.get("type") != "navigate_world":
                continue
            prior_plane = _int(prior.get("plane") if prior.get("plane") is not None else _dict(prior.get("worldLocation")).get("plane"))
            if step_plane is not None and prior_plane is not None and prior_plane != step_plane:
                continue
            location = _dict(prior.get("worldLocation"))
            world_x = _int(location.get("worldX") if location.get("worldX") is not None else location.get("x"))
            world_y = _int(location.get("worldY") if location.get("worldY") is not None else location.get("y"))
            if world_x is not None and world_y is not None:
                plane = _int(location.get("plane") if location.get("plane") is not None else prior.get("plane"))
                return {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else (step_plane if step_plane is not None else 0)}
    return None


def _candidate_class_ids(candidate: dict[str, Any]) -> set[str]:
    values = set()
    for key in ("classId", "targetClass"):
        value = candidate.get(key)
        if value:
            values.add(_norm(value))
    for key in ("targetClassIds", "classIds"):
        for value in _list(candidate.get(key)):
            if value:
                values.add(_norm(value))
    target = _dict(candidate.get("target"))
    for value in _list(target.get("targetClassIds")):
        if value:
            values.add(_norm(value))
    return values


def _candidate_has_route_transition_shape(candidate: dict[str, Any]) -> bool:
    classes = _candidate_class_ids(candidate)
    if "route transition" in classes or "door" in classes:
        return True
    name = _candidate_name(candidate)
    actions = " ".join(_candidate_actions(candidate))
    if _contains_any(name, list(ROUTE_TRANSITION_OBJECT_WORDS)):
        return True
    return bool(actions and _contains_any(actions, list(ROUTE_TRANSITION_ACTION_WORDS)))


def _candidate_has_service_shape(candidate: dict[str, Any]) -> bool:
    classes = _candidate_class_ids(candidate)
    if "bank related" in classes or "bank service" in classes:
        return True
    name = _candidate_name(candidate)
    actions = " ".join(_candidate_actions(candidate))
    return _contains_any(name, list(SERVICE_OBJECT_WORDS)) or bool(actions and _contains_any(actions, ["bank", "deposit"]))


def _candidate_projection_status(candidate: dict[str, Any]) -> dict[str, Any]:
    aim = candidate.get("safeAimPoint") if isinstance(candidate.get("safeAimPoint"), dict) else None
    if not aim:
        aim = candidate.get("aimPointContext") if isinstance(candidate.get("aimPointContext"), dict) else None
    if not aim:
        aim = candidate.get("aimPoint") if isinstance(candidate.get("aimPoint"), dict) else None
    canvas_x = None
    canvas_y = None
    if isinstance(aim, dict):
        canvas_x = aim.get("canvasX") if aim.get("canvasX") is not None else aim.get("x")
        canvas_y = aim.get("canvasY") if aim.get("canvasY") is not None else aim.get("y")
    has_point = isinstance(canvas_x, (int, float)) and isinstance(canvas_y, (int, float))
    degenerate = bool(has_point and abs(float(canvas_x)) < 0.001 and abs(float(canvas_y)) < 0.001)
    on_screen = candidate.get("onScreen")
    ui_blocked = bool(candidate.get("uiBlocked") or (isinstance(aim, dict) and aim.get("uiBlocked") is True))
    visible = bool((on_screen is True or has_point) and not degenerate)
    return {
        "schema": "route_projection_status.v1",
        "canvasPoint": {"canvasX": canvas_x, "canvasY": canvas_y} if has_point else None,
        "projectionAvailable": has_point,
        "visible": visible,
        "inCanvas": True if has_point and not degenerate else None,
        "inViewport": True if on_screen is True else (None if on_screen is None else False),
        "offscreen": on_screen is False,
        "degenerateProjection": degenerate,
        "tinyProjection": False,
        "uiBlocked": ui_blocked,
        "objectOccluded": None,
        "hoverOption": candidate.get("hoverTopOption") or candidate.get("topOption"),
        "hoverTarget": candidate.get("hoverTopTarget") or candidate.get("topTarget"),
        "projectionSource": (aim or {}).get("source") if isinstance(aim, dict) else None,
        "actionableByCanvas": bool(visible and not ui_blocked),
        "actionableByMinimap": None,
        "rejectionReason": "uiBlocked" if ui_blocked else ("degenerateProjection" if degenerate else ("offscreen" if on_screen is False else None)),
    }


def _candidate_plane_matches(candidate: dict[str, Any], step: dict[str, Any], player_plane: int | None) -> bool:
    step_plane = _int(step.get("plane"))
    candidate_plane = _int(candidate.get("plane"))
    if step_plane is not None and candidate_plane is not None:
        return step_plane == candidate_plane
    if step_plane is not None and player_plane is not None:
        return step_plane == player_plane
    return True


def _candidate_matches_interaction_step(candidate: dict[str, Any], step: dict[str, Any], *, player_plane: int | None) -> bool:
    if not isinstance(candidate, dict) or not isinstance(step, dict):
        return False
    if not _candidate_plane_matches(candidate, step, player_plane):
        return False
    candidate_id = _candidate_id(candidate)
    expected_ids = [_int(value) for value in _list(step.get("expectedObjectIds"))]
    expected_ids = [value for value in expected_ids if value is not None]
    if expected_ids and candidate_id in expected_ids:
        return True
    target_needles = _list(step.get("expectedTargetContains"))
    name_text = " ".join(
        _text(value)
        for value in (
            candidate.get("targetName"),
            candidate.get("name"),
            candidate.get("classId"),
            candidate.get("targetType"),
        )
        if _text(value)
    )
    if target_needles and not _contains_any(name_text, target_needles):
        return False
    expected_options = _list(step.get("expectedOptions"))
    actions = _candidate_actions(candidate)
    if expected_options and actions:
        return any(_contains_any(action, expected_options) for action in actions)
    return bool(target_needles)


def _interaction_steps(route: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, dict(step))
        for index, step in enumerate(_list(route.get("steps")))
        if isinstance(step, dict) and step.get("type") in {"interact_object", "service_interact"}
    ]


def _candidate_matching_steps(route: dict[str, Any], candidate: dict[str, Any], *, player_plane: int | None) -> list[tuple[int, dict[str, Any]]]:
    return [
        (index, step)
        for index, step in _interaction_steps(route)
        if _candidate_matches_interaction_step(candidate, step, player_plane=player_plane)
    ]


def _first_pending_interaction_index_for_plane(route: dict[str, Any], player_plane: int | None, completed_steps: list[str] | None = None) -> int | None:
    completed = {_norm(value) for value in (completed_steps or []) if _norm(value)}
    for index, step in _interaction_steps(route):
        label = _norm(step.get("label"))
        if label and label in completed:
            continue
        step_plane = _int(step.get("plane"))
        if player_plane is not None and step_plane is not None and step_plane != player_plane:
            continue
        return index
    return None


def _candidate_route_relevance(
    route: dict[str, Any],
    candidate: dict[str, Any],
    step: dict[str, Any],
    index: int,
    *,
    player_plane: int | None,
    player_tile: dict[str, Any] | None,
    completed_steps: list[str] | None = None,
) -> dict[str, Any]:
    expected_location = _step_expected_location(route, step, index)
    candidate_tile = _candidate_world_tile(candidate)
    candidate_plane = _int(candidate.get("plane"))
    expected_plane = _int(step.get("plane") if step.get("plane") is not None else (expected_location or {}).get("plane"))
    distance_to_expected = _tile_distance(candidate_tile, expected_location)
    distance_to_player = _tile_distance(candidate_tile, player_tile)
    first_pending = _first_pending_interaction_index_for_plane(route, player_plane, completed_steps)
    expected_actions = list(_list(step.get("expectedOptions")))
    dialogue_opener_actions = list(_list(step.get("dialogueOpenerOptions")))
    expected_targets = list(_list(step.get("expectedTargetContains")))
    status = "PASS"
    reason = None

    if step.get("type") not in {"interact_object", "service_interact"}:
        status = "FAIL"
        reason = "wrongRouteStep"
    elif expected_plane is not None and player_plane is not None and expected_plane != player_plane:
        status = "FAIL"
        reason = "wrongPlane"
    elif expected_plane is not None and candidate_plane is not None and candidate_plane != expected_plane:
        status = "FAIL"
        reason = "wrongPlane"
    elif not _candidate_matches_interaction_step(candidate, step, player_plane=player_plane):
        if expected_actions and not any(_contains_any(action, expected_actions) for action in _candidate_actions(candidate)):
            reason = "wrongAction"
        else:
            reason = "wrongObjectKind"
        status = "FAIL"
    elif first_pending is not None and index != first_pending:
        status = "FAIL"
        reason = "wrongRouteStep"
    elif distance_to_expected is not None and distance_to_expected > ROUTE_RELEVANCE_SEARCH_RADIUS_TILES:
        status = "FAIL"
        reason = "outsideRouteCorridor"
    elif expected_location is None and distance_to_player is not None and distance_to_player > 30:
        status = "FAIL"
        reason = "outsideSearchArea"

    would_advance = status == "PASS"
    return {
        "schema": ROUTE_RELEVANCE_SCHEMA,
        "routeId": route.get("routeId"),
        "currentNodeId": _step_node_id(step, index),
        "currentEdgeId": (_edge_for_step(route, step, index) or {}).get("edgeId"),
        "expectedStepType": step.get("type"),
        "expectedObjectKinds": expected_targets,
        "expectedActions": expected_actions,
        "dialogueOpenerActions": dialogue_opener_actions,
        "expectedPlane": expected_plane,
        "expectedPlaneChange": step.get("planeChange"),
        "expectedArea": expected_location,
        "candidateName": _candidate_name(candidate),
        "candidateActions": _candidate_actions(candidate),
        "candidatePlane": candidate_plane,
        "candidateWorldLocation": candidate_tile,
        "candidateDistanceToRouteNode": distance_to_expected,
        "candidateDistanceToRouteCorridor": distance_to_expected,
        "candidateDistanceToPlayer": distance_to_player,
        "candidateWouldAdvanceRoute": would_advance,
        "candidateIsBackward": False if would_advance else None,
        "candidateIsWrongSideOfWall": None,
        "candidateIsRandomTransitionObject": status == "FAIL" and reason in {"outsideRouteCorridor", "wrongRouteStep", "outsideSearchArea"},
        "relevanceStatus": status,
        "rejectionReason": reason,
    }


def _route_object_kind(candidate: dict[str, Any]) -> str | None:
    if _candidate_has_service_shape(candidate):
        return "service_object"
    if _candidate_has_route_transition_shape(candidate):
        return "route_transition"
    return None


def _service_object_type(candidate: dict[str, Any]) -> str:
    classes = _candidate_class_ids(candidate)
    name = _norm(_candidate_name(candidate))
    if "deposit box" in name or "deposit chest" in name or "deposit_box" in classes or "deposit chest" in classes:
        return "deposit_box"
    if "banker" in name or "banker" in classes:
        return "banker_npc"
    if "bank booth" in name or "booth" in name or "bank_booth" in classes:
        return "bank_booth"
    if "bank chest" in name or "bank_chest" in classes:
        return "bank_chest"
    return "unknown"


def _service_object_priority(candidate: dict[str, Any]) -> int:
    service_type = _service_object_type(candidate)
    if service_type == "deposit_box":
        return 0
    if service_type in {"bank_booth", "bank_chest"}:
        return 1
    if service_type == "banker_npc":
        return 2
    return 3


def _route_object_census(
    route: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    player_plane: int | None,
    player_tile: dict[str, Any] | None,
    completed_steps: list[str] | None = None,
    scan_limit: int = ROUTE_OBJECT_SCAN_LIMIT,
) -> dict[str, Any]:
    route_objects: list[dict[str, Any]] = []
    rejected = Counter()
    candidate_source_counts = Counter()
    considered = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        kind = _route_object_kind(candidate)
        if not kind:
            continue
        considered += 1
        if len(route_objects) >= max(0, scan_limit):
            rejected["routeObjectScanLimit"] += 1
            continue
        candidate_source_counts[str(candidate.get("_routeObjectScanSource") or candidate.get("source") or "unknown")] += 1
        projection = _candidate_projection_status(candidate)
        matching_steps = _candidate_matching_steps(route, candidate, player_plane=player_plane)
        relevance = None
        matched_index = None
        matched_step = None
        if matching_steps:
            ranked: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
            for step_index, step in matching_steps:
                step_relevance = _candidate_route_relevance(
                    route,
                    candidate,
                    step,
                    step_index,
                    player_plane=player_plane,
                    player_tile=player_tile,
                    completed_steps=completed_steps,
                )
                ranked.append((step_index, step, step_relevance))
            ranked.sort(
                key=lambda item: (
                    0 if item[2].get("relevanceStatus") == "PASS" else 1,
                    item[0],
                )
            )
            matched_index, matched_step, relevance = ranked[0]
        else:
            rejection = "wrongObjectKind"
            if kind == "route_transition" and _candidate_has_route_transition_shape(candidate):
                rejection = "randomTransitionObject"
            relevance = {
                "schema": ROUTE_RELEVANCE_SCHEMA,
                "routeId": route.get("routeId"),
                "candidateName": _candidate_name(candidate),
                "candidateActions": _candidate_actions(candidate),
                "candidatePlane": _int(candidate.get("plane")),
                "candidateWorldLocation": _candidate_world_tile(candidate),
                "relevanceStatus": "FAIL",
                "rejectionReason": rejection,
                "candidateWouldAdvanceRoute": False,
                "candidateIsRandomTransitionObject": rejection == "randomTransitionObject",
            }
        if relevance.get("relevanceStatus") != "PASS":
            rejected[str(relevance.get("rejectionReason") or "unknown")] += 1
        route_objects.append(
            {
                "name": _candidate_name(candidate),
                "objectId": _candidate_id(candidate),
                "hash": candidate.get("hash"),
                "objectKey": candidate.get("objectKey") or candidate.get("targetKey"),
                "actions": _candidate_actions(candidate),
                "worldLocation": _candidate_world_tile(candidate),
                "plane": _int(candidate.get("plane")),
                "distanceToPlayer": _tile_distance(_candidate_world_tile(candidate), player_tile),
                "routeObjectKind": kind,
                "source": candidate.get("_routeObjectScanSource") or candidate.get("source"),
                "matchedRouteStepIndex": matched_index,
                "matchedRouteStepLabel": matched_step.get("label") if isinstance(matched_step, dict) else None,
                "projectionStatus": projection,
                "routeRelevance": relevance,
                "routeRelevanceStatus": relevance.get("relevanceStatus"),
                "routeRelevanceScore": 1.0 if relevance.get("relevanceStatus") == "PASS" else 0.0,
                "routeRelevanceRejectionReason": relevance.get("rejectionReason"),
                "safeAimPointStatus": _dict(candidate.get("safeAimPoint")).get("status") if isinstance(candidate.get("safeAimPoint"), dict) else None,
                "hoverConfirmationStatus": candidate.get("hoverConfirmationStatus"),
                "rejectionReason": relevance.get("rejectionReason") or projection.get("rejectionReason"),
                "candidate": dict(candidate),
            }
        )
    visible = [item for item in route_objects if _dict(item.get("projectionStatus")).get("visible")]
    actionable = [
        item
        for item in visible
        if item.get("routeRelevanceStatus") == "PASS" and _dict(item.get("projectionStatus")).get("actionableByCanvas") is True
    ]
    relevant = [item for item in route_objects if item.get("routeRelevanceStatus") == "PASS"]
    relevant_actionable = [
        item
        for item in actionable
        if item.get("routeRelevanceStatus") == "PASS"
    ]
    visible_irrelevant = [item for item in visible if item.get("routeRelevanceStatus") != "PASS"]
    service_objects = [item for item in route_objects if item.get("routeObjectKind") == "service_object"]
    service_visible = [item for item in service_objects if _dict(item.get("projectionStatus")).get("visible")]
    service_actionable = [
        item
        for item in service_visible
        if item.get("routeRelevanceStatus") == "PASS" and _dict(item.get("projectionStatus")).get("actionableByCanvas") is True
    ]
    service_relevant = [item for item in service_objects if item.get("routeRelevanceStatus") == "PASS"]
    service_relevant_actionable = [
        item
        for item in service_actionable
        if item.get("routeRelevanceStatus") == "PASS"
    ]
    service_visible_irrelevant = [item for item in service_visible if item.get("routeRelevanceStatus") != "PASS"]
    rejected_service = Counter()
    for item in service_objects:
        if item in service_relevant_actionable:
            continue
        reason = item.get("rejectionReason") or _dict(item.get("projectionStatus")).get("rejectionReason")
        if reason:
            rejected_service[str(reason)] += 1
    top = sorted(
        route_objects,
        key=lambda item: (
            0 if item.get("routeRelevanceStatus") == "PASS" else 1,
            0 if _dict(item.get("projectionStatus")).get("visible") else 1,
            item.get("distanceToPlayer") if isinstance(item.get("distanceToPlayer"), int) else 9999,
            str(item.get("name") or ""),
        ),
    )[:10]
    top_service = sorted(
        service_objects,
        key=lambda item: (
            0 if item.get("routeRelevanceStatus") == "PASS" else 1,
            0 if _dict(item.get("projectionStatus")).get("visible") else 1,
            0 if _dict(item.get("projectionStatus")).get("actionableByCanvas") is True else 1,
            _service_object_priority(_dict(item.get("candidate"))),
            item.get("distanceToPlayer") if isinstance(item.get("distanceToPlayer"), int) else 9999,
            str(item.get("name") or ""),
        ),
    )[:10]
    service_census = {
        "schema": SERVICE_OBJECT_CENSUS_SCHEMA,
        "routeId": route.get("routeId"),
        "serviceObjectCandidatesTotal": len(service_objects),
        "bankBoothCandidates": sum(1 for item in service_objects if _service_object_type(_dict(item.get("candidate"))) == "bank_booth"),
        "bankerCandidates": sum(1 for item in service_objects if _service_object_type(_dict(item.get("candidate"))) == "banker_npc"),
        "depositBoxCandidates": sum(1 for item in service_objects if _service_object_type(_dict(item.get("candidate"))) == "deposit_box"),
        "visibleServiceObjects": len(service_visible),
        "actionableServiceObjects": len(service_actionable),
        "routeRelevantServiceObjects": len(service_relevant),
        "routeRelevantActionableServiceObjects": len(service_relevant_actionable),
        "visibleButRouteIrrelevantServiceObjects": len(service_visible_irrelevant),
        "rejectedServiceObjectsByReason": dict(rejected_service.most_common()),
        "serviceObjectScanSource": dict(candidate_source_counts.most_common()),
        "serviceObjectScanLimit": scan_limit,
        "sourceCapHit": None,
        "profileFilterHit": None,
        "topServiceObjects": top_service,
    }
    return {
        "schema": SERVICE_ROUTE_OBJECT_CENSUS_SCHEMA,
        "routeId": route.get("routeId"),
        "routeObjectCandidatesTotal": len(route_objects),
        "routeTransitionCandidates": sum(1 for item in route_objects if item.get("routeObjectKind") == "route_transition"),
        "serviceObjectCandidates": sum(1 for item in route_objects if item.get("routeObjectKind") == "service_object"),
        "visibleRouteObjects": len(visible),
        "actionableRouteObjects": len(actionable),
        "routeRelevantObjects": len(relevant),
        "routeRelevantActionableObjects": len(relevant_actionable),
        "visibleButRouteIrrelevantObjects": len(visible_irrelevant),
        "rejectedRouteObjectsByReason": dict(rejected.most_common()),
        "sourceCapHit": None,
        "profileFilterHit": None,
        "routeObjectScanLimit": scan_limit,
        "routeObjectScanSource": dict(candidate_source_counts.most_common()),
        "routeObjectInputsConsidered": considered,
        "topRouteObjects": top,
        "serviceObjectCensus": service_census,
        **{key: value for key, value in service_census.items() if key != "schema"},
    }


def _best_route_interaction_from_census(
    route: dict[str, Any],
    census: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
    top = _list(census.get("topRouteObjects"))
    for item in top:
        if not isinstance(item, dict):
            continue
        if item.get("routeObjectKind") != "route_transition":
            continue
        if item.get("routeRelevanceStatus") != "PASS":
            continue
        if _dict(item.get("projectionStatus")).get("actionableByCanvas") is not True:
            continue
        index = _int(item.get("matchedRouteStepIndex"))
        if index is None:
            continue
        step = _route_step(route, index)
        candidate = _dict(item.get("candidate"))
        if step and candidate:
            candidate["routeRelevance"] = item.get("routeRelevance")
            candidate["projectionStatus"] = item.get("projectionStatus")
            candidate["routeObjectSource"] = item.get("source")
            candidate["selectedAction"] = _preferred_candidate_action(candidate, step)
            return index, step, candidate
    return None, None, None


def _best_service_interaction_from_census(
    route: dict[str, Any],
    census: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
    service_census = _dict(census.get("serviceObjectCensus")) or census
    top = _list(service_census.get("topServiceObjects"))
    for item in top:
        if not isinstance(item, dict):
            continue
        if item.get("routeObjectKind") != "service_object":
            continue
        if item.get("routeRelevanceStatus") != "PASS":
            continue
        if _dict(item.get("projectionStatus")).get("actionableByCanvas") is not True:
            continue
        index = _int(item.get("matchedRouteStepIndex"))
        if index is None:
            continue
        step = _route_step(route, index)
        if step.get("type") != "service_interact":
            continue
        candidate = _dict(item.get("candidate"))
        if step and candidate:
            candidate["routeRelevance"] = item.get("routeRelevance")
            candidate["projectionStatus"] = item.get("projectionStatus")
            candidate["routeObjectSource"] = item.get("source")
            candidate["serviceObjectType"] = _service_object_type(candidate)
            candidate["selectedAction"] = _preferred_candidate_action(candidate, step)
            return index, step, candidate
    return None, None, None


def _candidate_lists(target_context: Any, service_context: Any) -> list[dict[str, Any]]:
    target = _dict(target_context)
    service = _dict(service_context)
    candidates: list[dict[str, Any]] = []
    for value in (
        service.get("bestServiceCandidate"),
        service.get("nearestServiceCandidate"),
    ):
        if isinstance(value, dict):
            payload = dict(value)
            payload.setdefault("_routeObjectScanSource", "serviceSelection")
            candidates.append(payload)
    for key in (
        "serviceCandidates",
        "service_candidates",
        "serviceCandidateInputs",
        "service_candidate_inputs",
        "loadedServiceScene",
        "loaded_service_scene",
        "broadCandidates",
        "broad_candidates",
        "profileCandidates",
        "profile_candidates",
        "candidates",
    ):
        for item in _list(service.get(key)) + _list(target.get(key)):
            if isinstance(item, dict):
                payload = dict(item)
                payload.setdefault("_routeObjectScanSource", key)
                candidates.append(payload)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _visible_service_target(service_context: Any) -> dict[str, Any] | None:
    service = _dict(service_context)
    best = service.get("bestServiceCandidate")
    return dict(best) if isinstance(best, dict) and best else None


def _route_step(route: dict[str, Any], index: int) -> dict[str, Any]:
    steps = _list(route.get("steps"))
    return dict(steps[index]) if 0 <= index < len(steps) and isinstance(steps[index], dict) else {}


def _service_step(route: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    for index, step in enumerate(_list(route.get("steps"))):
        if isinstance(step, dict) and step.get("type") == "service_interact":
            return index, dict(step)
    return None, None


def _route_nodes(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(node) for node in _list(route.get("nodes")) if isinstance(node, dict)]


def _route_edges(route: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(edge) for edge in _list(route.get("edges")) if isinstance(edge, dict)]


def _step_node_id(step: dict[str, Any], index: int | None = None) -> str | None:
    for key in ("nodeId", "fromNode", "targetNode"):
        value = step.get(key)
        if value:
            return str(value)
    return f"step_{index}" if index is not None else None


def _edge_for_step(route: dict[str, Any], step: dict[str, Any], index: int | None) -> dict[str, Any] | None:
    step_id = step.get("stepId")
    node_id = _step_node_id(step, index)
    for edge in _route_edges(route):
        if index is not None and _int(edge.get("stepIndex")) == index:
            return edge
        if step_id and edge.get("stepId") == step_id:
            return edge
        if node_id and edge.get("fromNode") == node_id:
            return edge
    return None


def _completed_step_labels_for_plane(route: dict[str, Any], player_plane: int | None) -> list[str]:
    if player_plane is None:
        return []
    completed: list[str] = []
    for step in _list(route.get("steps")):
        if not isinstance(step, dict) or step.get("type") != "interact_object":
            continue
        step_plane = _int(step.get("plane"))
        if step_plane is None or player_plane <= step_plane:
            continue
        plane_change = _text(step.get("planeChange"))
        if plane_change.startswith("+") or plane_change == "1":
            label = _text(step.get("label"))
            if label:
                completed.append(label)
    return completed


def _tile_distance(left: dict[str, Any] | None, right: dict[str, Any] | None) -> int | None:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_x = _int(left.get("worldX"))
    left_y = _int(left.get("worldY"))
    right_x = _int(right.get("worldX"))
    right_y = _int(right.get("worldY"))
    if left_x is None or left_y is None or right_x is None or right_y is None:
        return None
    return max(abs(left_x - right_x), abs(left_y - right_y))


def _merge_completed_steps(
    state: ServiceRouteState | None,
    completed: list[str],
    source_tick: int | None,
    *,
    reason: str = "route_progress_observed",
) -> list[str]:
    if state is None:
        return list(dict.fromkeys(completed))
    merged = list(dict.fromkeys([*state.completed_steps, *completed]))
    if merged != state.completed_steps:
        state.completed_steps = merged
        state.last_updated_tick = source_tick
        state.last_reason = reason
    return merged


def _completed_navigation_step_labels_for_location(
    route: dict[str, Any],
    player_plane: int | None,
    player_tile: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(player_tile, dict):
        return []
    highest_arrived_index: int | None = None
    steps = _list(route.get("steps"))
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("type") != "navigate_world":
            continue
        step_plane = _int(step.get("plane") if step.get("plane") is not None else _dict(step.get("worldLocation")).get("plane"))
        if player_plane is not None and step_plane is not None and step_plane != player_plane:
            continue
        distance = _tile_distance(player_tile, _dict(step.get("worldLocation")))
        radius = _int(step.get("arrivalRadiusTiles"))
        if radius is None:
            radius = 2
        if distance is not None and distance <= max(0, radius):
            highest_arrived_index = index

    if highest_arrived_index is None:
        return []

    completed: list[str] = []
    for index, step in enumerate(steps[: highest_arrived_index + 1]):
        if not isinstance(step, dict) or step.get("type") != "navigate_world":
            continue
        step_plane = _int(step.get("plane") if step.get("plane") is not None else _dict(step.get("worldLocation")).get("plane"))
        if player_plane is not None and step_plane is not None and step_plane != player_plane:
            continue
        label = _text(step.get("label") or step.get("nodeId"))
        if label:
            completed.append(label)
    return completed


def _navigation_step_for_plane(
    route: dict[str, Any],
    player_plane: int | None,
    *,
    player_tile: dict[str, Any] | None = None,
    completed_steps: list[str] | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    fallback: tuple[int | None, dict[str, Any] | None] = (None, None)
    last_arrived: tuple[int | None, dict[str, Any] | None] = (None, None)
    completed = {_norm(value) for value in (completed_steps or []) if _norm(value)}
    for index, step in enumerate(_list(route.get("steps"))):
        if not isinstance(step, dict) or step.get("type") != "navigate_world":
            continue
        step_label = _norm(step.get("label") or step.get("nodeId"))
        if step_label and step_label in completed:
            continue
        if fallback[0] is None:
            fallback = (index, dict(step))
        step_plane = _int(step.get("plane") if step.get("plane") is not None else _dict(step.get("worldLocation")).get("plane"))
        if player_plane is not None and step_plane is not None and step_plane != player_plane:
            continue
        step_payload = dict(step)
        distance = _tile_distance(player_tile, _dict(step.get("worldLocation")))
        radius = _int(step.get("arrivalRadiusTiles"))
        if radius is None:
            radius = 2
        if distance is not None and distance <= max(0, radius):
            step_payload["arrivedByDistance"] = True
            step_payload["distanceTiles"] = distance
            last_arrived = (index, step_payload)
            continue
        return index, step_payload
    if last_arrived[0] is not None:
        return last_arrived
    return fallback if player_plane is None else (None, None)


def _visible_interaction_step(
    route: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    player_plane: int | None,
) -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
    for index, step in enumerate(_list(route.get("steps"))):
        if not isinstance(step, dict) or step.get("type") != "interact_object":
            continue
        for candidate in candidates:
            if _candidate_matches_interaction_step(candidate, step, player_plane=player_plane):
                return index, dict(step), dict(candidate)
    return None, None, None


def _route_target_from_step(route: dict[str, Any], step: dict[str, Any], index: int) -> dict[str, Any] | None:
    location = _dict(step.get("worldLocation"))
    world_x = _int(location.get("worldX"))
    world_y = _int(location.get("worldY"))
    plane = _int(location.get("plane") if location.get("plane") is not None else step.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {
        "targetType": "service_route_anchor",
        "classId": "service_route_anchor",
        "targetName": step.get("label") or "Service route anchor",
        "routeId": route.get("routeId"),
        "routeStepIndex": index,
        "routeStepType": step.get("type"),
        "worldX": world_x,
        "worldY": world_y,
        "plane": plane if plane is not None else 0,
        "verifiedLive": bool(step.get("verifiedLive")),
        "confidence": step.get("confidence", route.get("confidence")),
        "source": "static_route_prior",
        "actions": [],
    }


def _interaction_target_from_candidate(route: dict[str, Any], step: dict[str, Any], index: int, candidate: dict[str, Any]) -> dict[str, Any]:
    target = deepcopy(candidate)
    target.setdefault("targetName", _candidate_name(candidate) or step.get("label") or "Route interaction")
    target.setdefault("targetType", "sceneObject")
    target.setdefault("classId", "service_route_transition")
    target["routeId"] = route.get("routeId")
    target["routeStepIndex"] = index
    target["routeStepType"] = step.get("type")
    target["routeStepLabel"] = step.get("label")
    target["expectedOptions"] = list(_list(step.get("expectedOptions")))
    target["dialogueOpenerOptions"] = list(_list(step.get("dialogueOpenerOptions")))
    target["dialogueExpectedPromptContains"] = list(_list(step.get("dialogueExpectedPromptContains")))
    target["expectedTargets"] = list(_list(step.get("expectedTargetContains")))
    target["expectedObjectIds"] = list(_list(step.get("expectedObjectIds")))
    target["expectedPlaneChange"] = step.get("planeChange")
    target["verifiedLive"] = True
    target["source"] = "live_route_object"
    if not _candidate_actions(target) and isinstance(step.get("expectedOptions"), list):
        target["actions"] = list(step["expectedOptions"]) + list(_list(step.get("dialogueOpenerOptions")))
    return target


def _record_anchor(
    state: ServiceRouteState | None,
    *,
    route: dict[str, Any],
    step: dict[str, Any],
    index: int,
    candidate: dict[str, Any],
    source_tick: int | None,
) -> None:
    if state is None:
        return
    state.reset_for_route(str(route.get("routeId") or ""))
    key = f"{index}:{_candidate_key(candidate)}"
    state.observed_anchors[key] = {
        "routeId": route.get("routeId"),
        "routeStepIndex": index,
        "routeStepLabel": step.get("label"),
        "routeStepType": step.get("type"),
        "nodeId": _step_node_id(step, index),
        "objectId": _candidate_id(candidate),
        "name": _candidate_name(candidate),
        "targetName": _candidate_name(candidate),
        "worldX": candidate.get("worldX"),
        "worldY": candidate.get("worldY"),
        "plane": candidate.get("plane"),
        "actions": _candidate_actions(candidate),
        "lastSeenTick": source_tick,
        "verifiedLive": True,
        "confidence": 1.0 if step.get("type") == "service_interact" else 0.85,
        "verificationSource": "successful_or_visible_service" if step.get("type") == "service_interact" else "visible_with_matching_action",
        "source": "observed_route_anchor",
        "serviceType": step.get("serviceType") or route.get("serviceType"),
    }
    state.current_step_index = index
    state.last_updated_tick = source_tick
    state.last_reason = "anchor_seen_live"


def _retained_service_anchor(
    state: ServiceRouteState | None,
    *,
    route: dict[str, Any],
    service_type: str | None,
    player_plane: int | None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    candidates: list[dict[str, Any]] = []
    for anchor in state.observed_anchors.values():
        if not isinstance(anchor, dict):
            continue
        if anchor.get("routeId") != route.get("routeId"):
            continue
        if anchor.get("routeStepType") != "service_interact":
            continue
        if player_plane is not None and _int(anchor.get("plane")) is not None and _int(anchor.get("plane")) != player_plane:
            continue
        if service_type and not _service_type_matches(anchor.get("serviceType"), service_type):
            continue
        candidates.append(anchor)
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (_int(item.get("lastSeenTick")) or -1, float(item.get("confidence") or 0.0)))
    return {
        "targetType": "retained_service_anchor",
        "classId": "bank_service",
        "targetName": best.get("targetName") or best.get("name") or "Observed service anchor",
        "routeId": route.get("routeId"),
        "routeStepIndex": best.get("routeStepIndex"),
        "routeStepType": best.get("routeStepType"),
        "nodeId": best.get("nodeId"),
        "objectId": best.get("objectId"),
        "worldX": best.get("worldX"),
        "worldY": best.get("worldY"),
        "plane": best.get("plane"),
        "actions": list(_list(best.get("actions"))),
        "verifiedLive": True,
        "confidence": best.get("confidence"),
        "lastSeenTick": best.get("lastSeenTick"),
        "source": "observed_route_anchor",
    }


def _node_world_location(node: dict[str, Any] | None) -> dict[str, Any] | None:
    node = node if isinstance(node, dict) else {}
    location = _dict(node.get("worldLocation"))
    world_x = _int(location.get("worldX") if location.get("worldX") is not None else location.get("x"))
    world_y = _int(location.get("worldY") if location.get("worldY") is not None else location.get("y"))
    if world_x is None or world_y is None:
        return None
    plane = _int(location.get("plane") if location.get("plane") is not None else node.get("plane"))
    return {"worldX": world_x, "worldY": world_y, "plane": plane if plane is not None else 0}


def _node_summary(node: dict[str, Any] | None, *, source: str | None = None) -> dict[str, Any] | None:
    node = node if isinstance(node, dict) else {}
    location = _node_world_location(node)
    if not node and location is None:
        return None
    return {
        "nodeId": node.get("nodeId"),
        "label": node.get("label"),
        "type": node.get("type"),
        "worldLocation": location,
        "plane": _int(node.get("plane") if node.get("plane") is not None else (location or {}).get("plane")),
        "confidence": node.get("confidence"),
        "source": source or node.get("source") or "route_profile",
    }


def _source_node(route: dict[str, Any]) -> dict[str, Any]:
    for node in _route_nodes(route):
        if node.get("type") in {"fallback_scouting_point", "resource_area", "source_area"}:
            return node
    first_edge = _route_edges(route)[0] if _route_edges(route) else {}
    return _route_node_by_id(route, first_edge.get("fromNode")) if first_edge else (_route_nodes(route)[0] if _route_nodes(route) else {})


def _service_goal_anchor(route: dict[str, Any], service_type: str | None) -> dict[str, Any] | None:
    service_index, service_step = _service_step(route)
    node = _route_node_by_id(route, _step_node_id(service_step or {}, service_index))
    summary = _node_summary(node, source="route_profile")
    if summary is None and isinstance(service_step, dict):
        summary = _node_summary(service_step, source="route_profile")
    if summary is None:
        return None
    return {
        "serviceType": service_type or route.get("serviceType"),
        "anchorId": summary.get("nodeId") or "service_goal",
        "worldLocation": summary.get("worldLocation"),
        "plane": summary.get("plane"),
        "confidence": summary.get("confidence", route.get("confidence")),
        "source": summary.get("source") or "route_profile",
    }


def _approach_step_candidates(route: dict[str, Any], player_plane: int | None) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    candidates: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    seen_node_ids: set[str] = set()
    for index, step in enumerate(_list(route.get("steps"))):
        if not isinstance(step, dict) or step.get("type") != "navigate_world":
            continue
        node_id = _step_node_id(step, index)
        if node_id not in GOAL_DIRECTED_APPROACH_NODE_IDS:
            continue
        step_plane = _int(step.get("plane") if step.get("plane") is not None else _dict(step.get("worldLocation")).get("plane"))
        if player_plane is not None and step_plane is not None and player_plane != step_plane:
            continue
        node = _route_node_by_id(route, node_id) or step
        candidates.append((index, dict(step), dict(node)))
        if node_id:
            seen_node_ids.add(node_id)
    synthetic_index = len(_list(route.get("steps")))
    for node in _route_nodes(route):
        node_id = _text(node.get("nodeId"))
        if not node_id or node_id in seen_node_ids or node_id not in GOAL_DIRECTED_APPROACH_NODE_IDS:
            continue
        if node.get("type") != "goal_directed_approach" and node.get("goalDirectedOnly") is not True:
            continue
        location = _node_world_location(node)
        if not location:
            continue
        node_plane = _int(node.get("plane") if node.get("plane") is not None else location.get("plane"))
        if player_plane is not None and node_plane is not None and player_plane != node_plane:
            continue
        step = {
            "type": "navigate_world",
            "nodeId": node_id,
            "label": node.get("label") or node_id,
            "worldLocation": dict(location),
            "plane": node_plane,
            "arrivalRadiusTiles": node.get("arrivalRadiusTiles", 3),
            "required": False,
            "confidence": node.get("confidence", 0.3),
            "verifiedLive": bool(node.get("verifiedLive")),
            "goalDirectedOnly": True,
        }
        candidates.append((synthetic_index, step, dict(node)))
        synthetic_index += 1
    return candidates


def _goal_directed_preference(node_id: str | None) -> int:
    return GOAL_DIRECTED_APPROACH_NODE_IDS.index(node_id) if node_id in GOAL_DIRECTED_APPROACH_NODE_IDS else 999


def _goal_directed_lateral_distance_sq(
    *,
    player_tile: dict[str, Any] | None,
    node_location: dict[str, Any] | None,
    goal_location: dict[str, Any] | None,
) -> tuple[int, int] | None:
    if not isinstance(player_tile, dict) or not isinstance(node_location, dict) or not isinstance(goal_location, dict):
        return None
    player_x = _int(player_tile.get("worldX"))
    player_y = _int(player_tile.get("worldY"))
    node_x = _int(node_location.get("worldX"))
    node_y = _int(node_location.get("worldY"))
    goal_x = _int(goal_location.get("worldX"))
    goal_y = _int(goal_location.get("worldY"))
    if None in {player_x, player_y, node_x, node_y, goal_x, goal_y}:
        return None
    route_x = goal_x - node_x
    route_y = goal_y - node_y
    player_x_from_node = player_x - node_x
    player_y_from_node = player_y - node_y
    route_len_sq = route_x * route_x + route_y * route_y
    if route_len_sq <= 0:
        return None
    route_progress_dot = route_x * player_x_from_node + route_y * player_y_from_node
    cross = route_x * player_y_from_node - route_y * player_x_from_node
    if route_progress_dot <= 0:
        return None
    return cross * cross, route_len_sq


def _goal_directed_approach_passed(
    *,
    player_tile: dict[str, Any] | None,
    node_location: dict[str, Any] | None,
    goal_location: dict[str, Any] | None,
    arrival_radius: int,
) -> bool:
    distance = _tile_distance(player_tile, node_location)
    if distance is not None and distance <= max(0, arrival_radius):
        return True
    lateral = _goal_directed_lateral_distance_sq(
        player_tile=player_tile,
        node_location=node_location,
        goal_location=goal_location,
    )
    if lateral is None:
        return False
    lateral_sq, route_len_sq = lateral
    corridor_tolerance = max(8, arrival_radius * 2)
    return lateral_sq <= corridor_tolerance * corridor_tolerance * route_len_sq


def _selected_goal_directed_approach(route: dict[str, Any], player_tile: dict[str, Any] | None, player_plane: int | None) -> tuple[int | None, dict[str, Any] | None, dict[str, Any] | None]:
    candidates = _approach_step_candidates(route, player_plane)
    if not candidates:
        return None, None, None
    service_goal = _service_goal_anchor(route, None)
    goal_location = _dict(service_goal.get("worldLocation")) if isinstance(service_goal, dict) else {}
    not_arrived: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    arrived: list[tuple[int, dict[str, Any], dict[str, Any], int]] = []
    for item in candidates:
        _index, step, node = item
        node_location = _node_world_location(node) or _dict(step.get("worldLocation"))
        radius = _int(step.get("arrivalRadiusTiles") if step.get("arrivalRadiusTiles") is not None else node.get("arrivalRadiusTiles"))
        if radius is None:
            radius = 2
        node_id = _step_node_id(step, _index) or ""
        preference = _goal_directed_preference(node_id)
        if _goal_directed_approach_passed(
            player_tile=player_tile,
            node_location=node_location,
            goal_location=goal_location,
            arrival_radius=radius,
        ):
            arrived.append((_index, step, node, preference))
        else:
            not_arrived.append(item)
    if arrived:
        furthest_arrived = max(item[3] for item in arrived)
        next_candidates = [
            item
            for item in candidates
            if _goal_directed_preference(_step_node_id(item[1], item[0]) or "") > furthest_arrived
        ]
        if next_candidates:
            candidates = next_candidates
            not_arrived = next_candidates
    candidates_to_score = not_arrived or candidates

    def score(item: tuple[int, dict[str, Any], dict[str, Any]]) -> tuple[int, int, int]:
        index, step, node = item
        node_id = _step_node_id(step, index) or ""
        distance = _tile_distance(player_tile, _node_world_location(node) or _dict(step.get("worldLocation")))
        preference = _goal_directed_preference(node_id)
        return (
            distance if distance is not None else 9999,
            preference,
            index,
        )

    index, step, node = min(candidates_to_score, key=score)
    return index, step, node


def _nearest_route_node(route: dict[str, Any], player_tile: dict[str, Any] | None, player_plane: int | None) -> tuple[dict[str, Any] | None, int | None]:
    best_node: dict[str, Any] | None = None
    best_distance: int | None = None
    for node in _route_nodes(route):
        if node.get("type") == "goal_directed_approach" or node.get("goalDirectedOnly") is True:
            continue
        node_location = _node_world_location(node)
        if not node_location:
            continue
        node_plane = _int(node.get("plane") if node.get("plane") is not None else node_location.get("plane"))
        if player_plane is not None and node_plane is not None and node_plane != player_plane:
            continue
        distance = _tile_distance(player_tile, node_location)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best_node = node
            best_distance = distance
    return best_node, best_distance


def _route_source_status(route: dict[str, Any], player_tile: dict[str, Any] | None, player_plane: int | None) -> tuple[str, str, str | None, int | None]:
    source = _source_node(route)
    source_distance = _tile_distance(player_tile, _node_world_location(source))
    nearest_node, nearest_distance = _nearest_route_node(route, player_tile, player_plane)
    if source_distance is not None and source_distance <= KNOWN_ROUTE_SOURCE_RADIUS_TILES:
        return "known_source", "known_route_node", source.get("label") or source.get("nodeId"), source_distance
    if source_distance is not None and source_distance <= NEARBY_ROUTE_SOURCE_RADIUS_TILES:
        return "nearby_known_source", "nearby_known_route_node", source.get("label") or source.get("nodeId"), source_distance
    if nearest_node is not None and nearest_distance is not None and nearest_distance <= KNOWN_ROUTE_NODE_RADIUS_TILES:
        return "known_source", "known_route_node", nearest_node.get("label") or nearest_node.get("nodeId"), nearest_distance
    return "unmapped_source", "unknown_current_position", "unmapped_resource_area", source_distance


def _should_use_goal_directed_fallback(
    route: dict[str, Any],
    *,
    player_tile: dict[str, Any] | None,
    player_plane: int | None,
    route_source_status: str,
    completed_steps: list[str] | None,
) -> bool:
    if not isinstance(player_tile, dict):
        return False
    if player_plane not in {None, 0}:
        return False
    if completed_steps:
        return False
    if route_source_status in {"known_source", "nearby_known_source"}:
        return False
    return bool(_approach_step_candidates(route, player_plane))


def _route_source_mismatch_payload(
    route: dict[str, Any],
    *,
    player_tile: dict[str, Any] | None,
    route_source_status: str,
) -> dict[str, Any] | None:
    if route_source_status in {"known_source", "nearby_known_source"}:
        return None
    source = _source_node(route)
    return {
        "schema": "route_source_mismatch.v1",
        "classification": "route_source_mismatch",
        "routeId": route.get("routeId"),
        "expectedSourceNode": _node_summary(source),
        "currentLocation": dict(player_tile) if isinstance(player_tile, dict) else None,
        "distanceToExpectedSource": _tile_distance(player_tile, _node_world_location(source)),
        "reason": "current location is outside the known route source area",
    }


def _route_context_payload(
    route: dict[str, Any],
    *,
    player_tile: dict[str, Any] | None,
    player_plane: int | None,
    player_location_source: str | None = None,
    player_location_confidence: float | None = None,
    current_node_id: str | None,
    selected_service_anchor: dict[str, Any] | None,
    selected_approach_node: dict[str, Any] | None,
    route_mode: str,
    route_source_status: str,
    current_area_source: str,
    current_area_label: str | None,
    source_distance: int | None,
    route_source_mismatch: dict[str, Any] | None,
    blocker_reason: str | None = None,
) -> dict[str, Any]:
    base_confidence = 0.85 if route_source_status == "known_source" else 0.55 if route_source_status == "nearby_known_source" else 0.35
    if player_location_source == "collision_window_center_proxy":
        base_confidence = min(base_confidence, 0.35)
    resource_area = None
    if route_source_status == "unmapped_source" and isinstance(player_tile, dict):
        resource_area = {
            "id": "unmapped_resource_area",
            "centroid": dict(player_tile),
            "bounds": None,
            "confidence": 0.35,
            "lastSeenTick": None,
            "source": "unknown_current_position",
        }
    return {
        "schema": ROUTE_CONTEXT_SCHEMA,
        "currentLocation": dict(player_tile) if isinstance(player_tile, dict) else None,
        "locationSource": player_location_source,
        "locationConfidence": player_location_confidence,
        "currentPlane": player_plane,
        "currentAreaLabel": current_area_label,
        "currentAreaConfidence": base_confidence,
        "currentAreaSource": current_area_source,
        "resourceArea": resource_area,
        "serviceGoal": selected_service_anchor,
        "routeMode": route_mode,
        "selectedRouteId": route.get("routeId"),
        "selectedEntryNode": _node_summary(_source_node(route)),
        "selectedApproachNode": selected_approach_node,
        "currentNodeId": current_node_id,
        "routeSourceStatus": route_source_status,
        "distanceToExpectedSource": source_distance,
        "routeSourceMismatch": route_source_mismatch,
        "blockerReason": blocker_reason,
    }


def _apply_route_context_fields(
    payload: dict[str, Any],
    *,
    route_context: dict[str, Any] | None,
    selected_service_anchor: dict[str, Any] | None,
    selected_approach_node: dict[str, Any] | None,
    route_source_mismatch: dict[str, Any] | None,
    blocker_reason: str | None,
) -> dict[str, Any]:
    route_context = route_context if isinstance(route_context, dict) else {}
    payload["routeContext"] = route_context or None
    payload["routeMode"] = route_context.get("routeMode") or "unknown"
    payload["routeSourceStatus"] = route_context.get("routeSourceStatus")
    payload["selectedServiceAnchor"] = selected_service_anchor
    payload["selectedApproachNode"] = selected_approach_node
    payload["routeSourceMismatch"] = route_source_mismatch
    payload["blockerReason"] = blocker_reason
    if route_context:
        payload["goalDirectedFallback"] = route_context.get("routeMode") == "goal_directed_fallback"
    return payload


def _context_payload(
    *,
    route: dict[str, Any] | None,
    status: str,
    route_step_status: str,
    current_step_index: int | None = None,
    current_step: dict[str, Any] | None = None,
    current_navigation_target: dict[str, Any] | None = None,
    visible_interaction_target: dict[str, Any] | None = None,
    visible_service_target: dict[str, Any] | None = None,
    action_ready: bool = False,
    current_node_id: str | None = None,
    next_edge: dict[str, Any] | None = None,
    completed_steps: list[str] | None = None,
    warnings: list[str] | None = None,
    missing_capabilities: list[str] | None = None,
    route_state: ServiceRouteState | None = None,
    source_tick: int | None = None,
    started: float | None = None,
    route_object_census: dict[str, Any] | None = None,
    route_context: dict[str, Any] | None = None,
    selected_service_anchor: dict[str, Any] | None = None,
    selected_approach_node: dict[str, Any] | None = None,
    route_source_mismatch: dict[str, Any] | None = None,
    blocker_reason: str | None = None,
) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    step = current_step if isinstance(current_step, dict) else {}
    census = route_object_census if isinstance(route_object_census, dict) else {}
    service_census = _dict(census.get("serviceObjectCensus"))
    route_objects_visible = census.get("visibleRouteObjects") if census else None
    route_objects_actionable = census.get("routeRelevantActionableObjects") if census else None
    service_objects_visible = service_census.get("visibleServiceObjects") if service_census else None
    service_objects_actionable = service_census.get("routeRelevantActionableServiceObjects") if service_census else None
    if route_objects_visible is None:
        route_objects_visible = 1 if visible_interaction_target else 0
    if route_objects_actionable is None:
        route_objects_actionable = 1 if visible_interaction_target and action_ready else 0
    if service_objects_visible is None:
        service_objects_visible = 1 if visible_service_target else 0
    if service_objects_actionable is None:
        service_objects_actionable = 1 if visible_service_target and action_ready else 0
    selected_service_object = visible_service_target if isinstance(visible_service_target, dict) else None
    selected_route_action = _preferred_candidate_action(visible_interaction_target, step)
    selected_service_action = _preferred_candidate_action(selected_service_object, step)
    payload = {
        "schema": SERVICE_ROUTE_CONTEXT_SCHEMA,
        "status": status,
        "warnings": list(warnings or []),
        "missingCapabilities": list(missing_capabilities or []),
        "sourceTick": source_tick,
        "timingMillis": ((time.perf_counter() - started) * 1000.0) if started is not None else None,
        "routeAvailable": bool(route),
        "routeId": route.get("routeId"),
        "routeVerifiedLive": bool(route.get("verifiedLive")),
        "routeConfidence": route.get("confidence"),
        "serviceType": route.get("serviceType"),
        "areaHint": route.get("areaHint"),
        "routeNodes": _route_nodes(route),
        "routeEdges": _route_edges(route),
        "routeSteps": [dict(item) for item in _list(route.get("steps")) if isinstance(item, dict)],
        "currentNodeId": current_node_id,
        "nextEdge": dict(next_edge) if isinstance(next_edge, dict) else None,
        "routeStepStatus": route_step_status,
        "currentStepIndex": current_step_index,
        "currentStep": step or None,
        "currentNavigationTarget": current_navigation_target,
        "visibleInteractionTarget": visible_interaction_target,
        "visibleServiceTarget": visible_service_target,
        "actionReady": bool(action_ready),
        "routeObjectsVisible": route_objects_visible,
        "routeObjectsActionable": route_objects_actionable,
        "routeRelevantObjects": census.get("routeRelevantObjects", route_objects_actionable if route_objects_actionable else 0),
        "routeRelevantActionableObjects": census.get("routeRelevantActionableObjects", route_objects_actionable),
        "visibleButRouteIrrelevantObjects": census.get("visibleButRouteIrrelevantObjects", 0),
        "routeObjectCensus": census or None,
        "serviceObjectCensus": service_census or None,
        "selectedRouteObject": visible_interaction_target or visible_service_target,
        "selectedRouteObjectAction": selected_route_action,
        "selectedRouteObjectRelevance": _dict(visible_interaction_target.get("routeRelevance")) if isinstance(visible_interaction_target, dict) else None,
        "routeObjectRejectedReason": next(iter(census.get("rejectedRouteObjectsByReason", {}) or {}), None) if census else None,
        "routeObjectInterceptReady": bool(action_ready and visible_interaction_target),
        "serviceObjectsVisible": service_objects_visible,
        "serviceObjectsActionable": service_objects_actionable,
        "routeRelevantServiceObjects": service_census.get("routeRelevantServiceObjects", service_objects_actionable if service_objects_actionable else 0) if service_census else service_objects_actionable,
        "routeRelevantActionableServiceObjects": service_census.get("routeRelevantActionableServiceObjects", service_objects_actionable) if service_census else service_objects_actionable,
        "visibleButRouteIrrelevantServiceObjects": service_census.get("visibleButRouteIrrelevantServiceObjects", 0) if service_census else 0,
        "selectedServiceObject": selected_service_object,
        "selectedServiceAction": selected_service_action,
        "selectedServiceObjectRelevance": _dict(selected_service_object.get("routeRelevance")) if selected_service_object else None,
        "serviceObjectRejectedReason": next(iter(service_census.get("rejectedServiceObjectsByReason", {}) or {}), None) if service_census else None,
        "serviceObjectInterceptReady": bool(action_ready and visible_service_target),
        "selectedRouteObjectPresent": bool(visible_interaction_target or visible_service_target),
        "interactionExpectedOptions": list(_list(step.get("expectedOptions"))),
        "interactionDialogueOpenerOptions": list(_list(step.get("dialogueOpenerOptions"))),
        "interactionExpectedTargets": list(_list(step.get("expectedTargetContains"))),
        "expectedPlaneChange": step.get("planeChange"),
        "observedAnchors": deepcopy(route_state.observed_anchors) if route_state else {},
        "completedSteps": list(completed_steps if completed_steps is not None else (route_state.completed_steps if route_state else [])),
    }
    return _apply_route_context_fields(
        payload,
        route_context=route_context,
        selected_service_anchor=selected_service_anchor,
        selected_approach_node=selected_approach_node,
        route_source_mismatch=route_source_mismatch,
        blocker_reason=blocker_reason,
    )


def build_service_route_context(
    *,
    profile: str | None,
    service_type: str | None,
    player_context: Any,
    service_context: Any,
    target_context: Any,
    route_state: ServiceRouteState | None = None,
    routes: list[dict[str, Any]] | None = None,
    route_id: str | None = None,
    source_tick: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    all_routes = routes if routes is not None else load_service_routes()
    route = select_service_route(all_routes, profile=profile, service_type=service_type, route_id=route_id)
    working_state = route_state if route_state is not None else ServiceRouteState()
    working_state.reset_for_route(route.get("routeId") if isinstance(route, dict) else None)
    if not route:
        return _context_payload(
            route=None,
            status="WARN",
            route_step_status="route_missing",
            warnings=["no service route prior available for this profile/service type"],
            missing_capabilities=["service.route"],
            route_state=working_state,
            source_tick=source_tick,
            started=started,
        )

    plane = _player_plane(player_context)
    player_location_info = _player_location_info(player_context)
    player_tile = player_location_info.get("tile") if isinstance(player_location_info.get("tile"), dict) else None
    if plane is None and isinstance(player_tile, dict):
        plane = _int(player_tile.get("plane"))
    plane_completed_steps = _completed_step_labels_for_plane(route, plane)
    location_completed_steps = _completed_navigation_step_labels_for_location(route, plane, player_tile)
    completed_reason = "navigation_progress_observed" if location_completed_steps else "plane_progress_observed"
    completed_steps = _merge_completed_steps(working_state, [*plane_completed_steps, *location_completed_steps], source_tick, reason=completed_reason)
    source_status, current_area_source, current_area_label, source_distance = _route_source_status(route, player_tile, plane)
    service_anchor = _service_goal_anchor(route, service_type)
    route_source_mismatch = _route_source_mismatch_payload(route, player_tile=player_tile, route_source_status=source_status)
    use_goal_directed = _should_use_goal_directed_fallback(
        route,
        player_tile=player_tile,
        player_plane=plane,
        route_source_status=source_status,
        completed_steps=completed_steps,
    )
    approach_index: int | None = None
    approach_step: dict[str, Any] | None = None
    approach_node: dict[str, Any] | None = None
    selected_approach_node: dict[str, Any] | None = None
    if use_goal_directed:
        approach_index, approach_step, approach_node = _selected_goal_directed_approach(route, player_tile, plane)
        selected_approach_node = _node_summary(approach_node or approach_step, source="route_profile")
    route_mode = "goal_directed_fallback" if use_goal_directed else "explicit_route"

    def make_route_context(current_node_id: str | None, *, blocker_reason: str | None = None) -> dict[str, Any]:
        return _route_context_payload(
            route,
            player_tile=player_tile,
            player_plane=plane,
            player_location_source=player_location_info.get("source"),
            player_location_confidence=player_location_info.get("confidence"),
            current_node_id=current_node_id,
            selected_service_anchor=service_anchor,
            selected_approach_node=selected_approach_node,
            route_mode=route_mode,
            route_source_status=source_status,
            current_area_source=current_area_source,
            current_area_label=current_area_label,
            source_distance=source_distance,
            route_source_mismatch=route_source_mismatch,
            blocker_reason=blocker_reason,
        )

    candidates = _candidate_lists(target_context, service_context)
    route_object_census = _route_object_census(
        route,
        candidates,
        player_plane=plane,
        player_tile=player_tile,
        completed_steps=completed_steps,
    )
    service_index, service_step, service_candidate = _best_service_interaction_from_census(route, route_object_census)
    if service_step is not None and service_candidate is not None and service_index is not None:
        target = _interaction_target_from_candidate(route, service_step, service_index, service_candidate)
        _record_anchor(working_state, route=route, step=service_step, index=service_index, candidate=target, source_tick=source_tick)
        return _context_payload(
            route=route,
            status="PASS",
            route_step_status="service_target_actionable",
            current_step_index=service_index,
            current_step=service_step,
            visible_service_target=target,
            action_ready=True,
            current_node_id=_step_node_id(service_step, service_index),
            next_edge=_edge_for_step(route, service_step, service_index),
            completed_steps=completed_steps,
            route_state=working_state,
            source_tick=source_tick,
            started=started,
            route_object_census=route_object_census,
            route_context=make_route_context(_step_node_id(service_step, service_index)),
            selected_service_anchor=service_anchor,
            selected_approach_node=selected_approach_node,
            route_source_mismatch=route_source_mismatch,
        )
    service_target = _visible_service_target(service_context)
    if service_target:
        index, step = _service_step(route)
        if step is None:
            step = {"type": "service_interact", "label": "Visible service target", "serviceType": service_type}
        if index is None:
            index = len(_list(route.get("steps")))
        _record_anchor(working_state, route=route, step=step, index=index, candidate=service_target, source_tick=source_tick)
        return _context_payload(
            route=route,
            status="PASS",
            route_step_status="service_target_visible",
            current_step_index=index,
            current_step=step,
            visible_service_target=service_target,
            action_ready=False,
            current_node_id=_step_node_id(step, index),
            next_edge=_edge_for_step(route, step, index),
            completed_steps=completed_steps,
            route_state=working_state,
            source_tick=source_tick,
            started=started,
            route_object_census=route_object_census,
            route_context=make_route_context(_step_node_id(step, index)),
            selected_service_anchor=service_anchor,
            selected_approach_node=selected_approach_node,
            route_source_mismatch=route_source_mismatch,
        )

    index, step, candidate = _best_route_interaction_from_census(route, route_object_census)
    if step is not None and candidate is not None and index is not None:
        target = _interaction_target_from_candidate(route, step, index, candidate)
        _record_anchor(working_state, route=route, step=step, index=index, candidate=target, source_tick=source_tick)
        return _context_payload(
            route=route,
            status="PASS",
            route_step_status="route_interaction_visible",
            current_step_index=index,
            current_step=step,
            visible_interaction_target=target,
            action_ready=True,
            current_node_id=_step_node_id(step, index),
            next_edge=_edge_for_step(route, step, index),
            completed_steps=completed_steps,
            route_state=working_state,
            source_tick=source_tick,
            started=started,
            route_object_census=route_object_census,
            route_context=make_route_context(_step_node_id(step, index)),
            selected_service_anchor=service_anchor,
            selected_approach_node=selected_approach_node,
            route_source_mismatch=route_source_mismatch,
        )

    retained_anchor = _retained_service_anchor(working_state, route=route, service_type=service_type, player_plane=plane)
    if retained_anchor is not None:
        index, step = _service_step(route)
        step = step or {"type": "service_interact", "label": "Observed service target", "serviceType": service_type}
        return _context_payload(
            route=route,
            status="WARN",
            route_step_status="retained_service_anchor",
            current_step_index=index,
            current_step=step,
            current_navigation_target=retained_anchor,
            warnings=["using previously observed service anchor as a navigation target; live visibility is still required before banking"],
            current_node_id=retained_anchor.get("nodeId") or _step_node_id(step, index),
            next_edge=_edge_for_step(route, step, index),
            completed_steps=completed_steps,
            route_state=working_state,
            source_tick=source_tick,
            started=started,
            route_object_census=route_object_census,
            route_context=make_route_context(retained_anchor.get("nodeId") or _step_node_id(step, index)),
            selected_service_anchor=service_anchor,
            selected_approach_node=selected_approach_node,
            route_source_mismatch=route_source_mismatch,
        )

    if use_goal_directed and approach_step is not None and approach_index is not None:
        target = _route_target_from_step(route, approach_step, approach_index)
        if target is not None:
            target["source"] = "goal_directed_service_anchor"
            target["targetType"] = "service_route_anchor"
            target["classId"] = "service_route_anchor"
            node_id = _step_node_id(approach_step, approach_index)
            blocker = "route_source_mismatch" if route_source_mismatch else None
            target_route_context = make_route_context(node_id, blocker_reason=blocker)
            target["routeMode"] = "goal_directed_fallback"
            target["goalDirectedFallback"] = True
            target["selectedServiceAnchor"] = service_anchor
            target["selectedApproachNode"] = selected_approach_node
            target["routeSourceMismatch"] = route_source_mismatch
            target["routeContext"] = target_route_context
            next_edge = {
                "edgeId": f"goal_directed_to_{node_id}",
                "type": "goal_directed_walk_to",
                "fromNode": "current_resource_area",
                "toNode": node_id,
                "stepIndex": approach_index,
                "verifiedLive": False,
            }
            return _context_payload(
                route=route,
                status="WARN",
                route_step_status="goal_directed_route_prior",
                current_step_index=approach_index,
                current_step=approach_step,
                current_navigation_target=target,
                warnings=[
                    "current resource area is outside the known route source; using destination-centered service approach",
                    "route prior is unverified; use as a scouting/navigation target until live anchors are observed",
                ],
                current_node_id=node_id,
                next_edge=next_edge,
                completed_steps=completed_steps,
                route_state=working_state,
                source_tick=source_tick,
                started=started,
                route_object_census=route_object_census,
                route_context=target_route_context,
                selected_service_anchor=service_anchor,
                selected_approach_node=selected_approach_node,
                route_source_mismatch=route_source_mismatch,
                blocker_reason=blocker,
            )

    nav_index, nav_step = _navigation_step_for_plane(route, plane, player_tile=player_tile, completed_steps=completed_steps)
    if nav_step is not None and nav_index is not None:
        target = _route_target_from_step(route, nav_step, nav_index)
        if target is not None:
            return _context_payload(
                route=route,
                status="WARN",
                route_step_status="static_route_prior",
                current_step_index=nav_index,
                current_step=nav_step,
                current_navigation_target=target,
                warnings=["route prior is unverified; use as a scouting/navigation target until live anchors are observed"],
                current_node_id=_step_node_id(nav_step, nav_index),
                next_edge=_edge_for_step(route, nav_step, nav_index),
                completed_steps=completed_steps,
                route_state=working_state,
                source_tick=source_tick,
                started=started,
                route_object_census=route_object_census,
                route_context=make_route_context(_step_node_id(nav_step, nav_index)),
                selected_service_anchor=service_anchor,
                selected_approach_node=selected_approach_node,
                route_source_mismatch=route_source_mismatch,
            )

    first_step = _route_step(route, 0)
    return _context_payload(
        route=route,
        status="WARN",
        route_step_status="route_anchor_missing",
        current_step_index=0 if first_step else None,
        current_step=first_step or None,
        warnings=["service route exists but no live route object or static world anchor is currently available"],
        missing_capabilities=["service.route.anchor"],
        current_node_id=_step_node_id(first_step, 0) if first_step else None,
        next_edge=_edge_for_step(route, first_step, 0) if first_step else None,
        completed_steps=completed_steps,
        route_state=working_state,
        source_tick=source_tick,
        started=started,
        route_object_census=route_object_census,
        route_context=make_route_context(_step_node_id(first_step, 0) if first_step else None),
        selected_service_anchor=service_anchor,
        selected_approach_node=selected_approach_node,
        route_source_mismatch=route_source_mismatch,
    )


def _return_route_from_route(route: dict[str, Any]) -> dict[str, Any] | None:
    return_steps = [dict(step) for step in _list(route.get("returnSteps")) if isinstance(step, dict)]
    if not return_steps:
        return None
    return_route = dict(route)
    source_route_id = str(route.get("routeId") or "service_route")
    return_route["sourceRouteId"] = source_route_id
    return_route["routeId"] = str(route.get("returnRouteId") or f"{source_route_id}_return")
    return_route["steps"] = return_steps
    return_route["edges"] = [dict(edge) for edge in _list(route.get("returnEdges")) if isinstance(edge, dict)]
    return return_route


def _return_state_from_step_status(step_status: str, *, action_ready: bool, navigation_target: dict[str, Any] | None) -> str:
    if step_status == "route_interaction_visible" and action_ready:
        return "return_transition_actionable"
    if step_status == "static_route_prior" and navigation_target:
        return "return_route_ready"
    if step_status == "route_anchor_missing":
        return "return_blocked"
    return "returning_to_resource" if navigation_target else step_status


def _augment_return_context(
    payload: dict[str, Any],
    *,
    source_route: dict[str, Any],
    return_route: dict[str, Any],
    resource_return_context: Any,
) -> dict[str, Any]:
    resource_return = _dict(resource_return_context)
    target_area = _dict(resource_return.get("returnDestinationTile"))
    navigation_target = _dict(payload.get("currentNavigationTarget"))
    state = _return_state_from_step_status(
        str(payload.get("routeStepStatus") or "return_blocked"),
        action_ready=bool(payload.get("actionReady")),
        navigation_target=navigation_target or None,
    )
    payload["schema"] = RETURN_ROUTE_CONTEXT_SCHEMA
    payload["sourceRouteId"] = source_route.get("routeId")
    payload["returnRouteId"] = return_route.get("routeId")
    payload["state"] = state
    payload["targetResourceArea"] = target_area or None
    payload["resourceAnchor"] = {
        "source": resource_return.get("returnDestinationSource"),
        "worldLocation": target_area or None,
        "plane": target_area.get("plane") if target_area else None,
        "confidence": 0.45 if resource_return.get("returnDestinationSource") == "profile_anchor" else 0.75,
        "lastSeenTick": resource_return.get("resourceMemoryAgeTicks"),
    }
    payload["returnActionReady"] = bool(payload.get("actionReady") or navigation_target)
    payload["returnBlockedReason"] = None if payload["returnActionReady"] else (
        next(iter(payload.get("missingCapabilities") or []), None)
        or payload.get("routeStepStatus")
        or "return_route_unavailable"
    )
    return payload


def build_return_route_context(
    *,
    profile: str | None,
    service_type: str | None,
    player_context: Any,
    target_context: Any,
    service_context: Any,
    resource_return_context: Any,
    route_state: ServiceRouteState | None = None,
    routes: list[dict[str, Any]] | None = None,
    route_id: str | None = None,
    source_tick: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    all_routes = routes if routes is not None else load_service_routes()
    source_route = select_service_route(all_routes, profile=profile, service_type=service_type, route_id=route_id)
    return_route = _return_route_from_route(source_route) if isinstance(source_route, dict) else None
    working_state = route_state if route_state is not None else ServiceRouteState()
    working_state.reset_for_route(return_route.get("routeId") if isinstance(return_route, dict) else None)
    resource_return = _dict(resource_return_context)
    if not source_route or not return_route:
        payload = _context_payload(
            route=None,
            status="WARN",
            route_step_status="return_route_missing",
            warnings=["no return route prior available for this profile/service type"],
            missing_capabilities=["return.route"],
            route_state=working_state,
            source_tick=source_tick,
            started=started,
        )
        payload["schema"] = RETURN_ROUTE_CONTEXT_SCHEMA
        payload["sourceRouteId"] = source_route.get("routeId") if isinstance(source_route, dict) else None
        payload["returnRouteId"] = None
        payload["state"] = "return_blocked"
        payload["returnActionReady"] = False
        payload["returnBlockedReason"] = "return_route_missing"
        return payload

    if resource_return.get("returnDestinationAvailable") is not True:
        payload = _context_payload(
            route=return_route,
            status="WARN",
            route_step_status="resource_anchor_missing",
            warnings=["return route exists but no resource return destination is available"],
            missing_capabilities=["resource.return.destination"],
            route_state=working_state,
            source_tick=source_tick,
            started=started,
        )
        return _augment_return_context(payload, source_route=source_route, return_route=return_route, resource_return_context=resource_return)

    plane = _player_plane(player_context)
    player_tile = _player_tile(player_context)
    candidates = _candidate_lists(target_context, service_context)
    route_object_census = _route_object_census(
        return_route,
        candidates,
        player_plane=plane,
        player_tile=player_tile,
        completed_steps=list(working_state.completed_steps),
    )
    index, step, candidate = _best_route_interaction_from_census(return_route, route_object_census)
    if step is not None and candidate is not None and index is not None:
        target = _interaction_target_from_candidate(return_route, step, index, candidate)
        _record_anchor(working_state, route=return_route, step=step, index=index, candidate=target, source_tick=source_tick)
        payload = _context_payload(
            route=return_route,
            status="PASS",
            route_step_status="route_interaction_visible",
            current_step_index=index,
            current_step=step,
            visible_interaction_target=target,
            action_ready=True,
            current_node_id=_step_node_id(step, index),
            next_edge=_edge_for_step(return_route, step, index),
            completed_steps=list(working_state.completed_steps),
            route_state=working_state,
            source_tick=source_tick,
            started=started,
            route_object_census=route_object_census,
        )
        return _augment_return_context(payload, source_route=source_route, return_route=return_route, resource_return_context=resource_return)

    nav_index, nav_step = _navigation_step_for_plane(return_route, plane, player_tile=player_tile, completed_steps=list(working_state.completed_steps))
    if nav_step is not None and nav_index is not None:
        target = _route_target_from_step(return_route, nav_step, nav_index)
        if target is not None:
            target["targetType"] = "tile"
            target["classId"] = "resource_return"
            target["returnDestinationTile"] = dict(target.get("worldLocation") or {})
            payload = _context_payload(
                route=return_route,
                status="WARN",
                route_step_status="static_route_prior",
                current_step_index=nav_index,
                current_step=nav_step,
                current_navigation_target=target,
                warnings=["return route prior is unverified; use as a scouting/navigation target until resources or route objects are observed"],
                current_node_id=_step_node_id(nav_step, nav_index),
                next_edge=_edge_for_step(return_route, nav_step, nav_index),
                completed_steps=list(working_state.completed_steps),
                route_state=working_state,
                source_tick=source_tick,
                started=started,
                route_object_census=route_object_census,
            )
            return _augment_return_context(payload, source_route=source_route, return_route=return_route, resource_return_context=resource_return)

    first_step = _route_step(return_route, 0)
    payload = _context_payload(
        route=return_route,
        status="WARN",
        route_step_status="route_anchor_missing",
        current_step_index=0 if first_step else None,
        current_step=first_step or None,
        warnings=["return route exists but no live return transition object or static world anchor is currently available"],
        missing_capabilities=["return.route.anchor"],
        current_node_id=_step_node_id(first_step, 0) if first_step else None,
        next_edge=_edge_for_step(return_route, first_step, 0) if first_step else None,
        completed_steps=list(working_state.completed_steps),
        route_state=working_state,
        source_tick=source_tick,
        started=started,
        route_object_census=route_object_census,
    )
    return _augment_return_context(payload, source_route=source_route, return_route=return_route, resource_return_context=resource_return)
