from __future__ import annotations

import time
from typing import Any

import capabilities
import navigation_reachability
from analyzers.live_state import NavigationContext

DEFAULT_COLLISION_WINDOW_STALE_TICKS = 5


def _reachability(candidate: dict[str, Any]) -> str:
    nav = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    return str(nav.get("directReachability") or candidate.get("directReachability") or candidate.get("reachability") or "unknown").lower()


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _collision_payload(navigation: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("collisionWindow", "collision_window", "collision"):
        value = navigation.get(key)
        if isinstance(value, dict):
            return value
    if isinstance(navigation.get("flags"), list):
        return navigation
    return None


def _collision_window_center(navigation: dict[str, Any], payload: dict[str, Any] | None) -> dict[str, Any] | None:
    value = navigation.get("collisionWindowCenterWorld")
    if isinstance(value, dict):
        return dict(value)
    if isinstance(payload, dict) and isinstance(payload.get("centerWorld"), dict):
        return dict(payload["centerWorld"])
    world_x = _as_int(navigation.get("playerWorldX"))
    world_y = _as_int(navigation.get("playerWorldY"))
    plane = _as_int(navigation.get("playerPlane"))
    if plane is None:
        plane = _as_int(navigation.get("plane"))
    if world_x is None or world_y is None or plane is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": plane}


def _collision_window_age_ticks(navigation: dict[str, Any]) -> int | None:
    explicit_age = _as_int(navigation.get("collisionWindowAgeTicks"))
    if explicit_age is not None:
        return explicit_age
    latest_tick = _as_int(navigation.get("latestTick"))
    window_tick = _as_int(navigation.get("collisionWindowTick"))
    if latest_tick is None or window_tick is None:
        return None
    return max(0, latest_tick - window_tick)


def _collision_window_fresh(navigation: dict[str, Any], age_ticks: int | None, available: bool) -> bool | None:
    explicit = _as_bool(navigation.get("collisionWindowFresh"))
    if explicit is not None:
        return explicit
    if not available:
        return None
    if age_ticks is None:
        return True
    return age_ticks <= DEFAULT_COLLISION_WINDOW_STALE_TICKS


def analyze_navigation(navigation: dict[str, Any] | None, candidates: list[dict[str, Any]] | None) -> NavigationContext:
    started = time.perf_counter()
    navigation = navigation if isinstance(navigation, dict) else {}
    candidates = [candidate for candidate in (candidates or []) if isinstance(candidate, dict)]
    reachable = blocked = unknown = 0
    for candidate in candidates:
        reachability = _reachability(candidate)
        if reachability == "reachable":
            reachable += 1
        elif reachability in {"blocked", "unreachable"}:
            blocked += 1
        else:
            unknown += 1
    payload = _collision_payload(navigation)
    parsed_window = navigation_reachability.parse_collision_window(payload)
    explicit_available = _as_bool(navigation.get("collisionWindowAvailable"))
    collision_window_available = bool(parsed_window) if explicit_available is None else bool(explicit_available and parsed_window)
    age_ticks = _collision_window_age_ticks(navigation)
    fresh = _collision_window_fresh(navigation, age_ticks, collision_window_available)
    radius = _as_int(navigation.get("collisionWindowRadius"))
    if radius is None and parsed_window is not None:
        radius = parsed_window.radius
    plane = _as_int(navigation.get("collisionWindowPlane"))
    if plane is None and parsed_window is not None:
        plane = parsed_window.plane
    bounds = navigation.get("collisionWindowBounds") if isinstance(navigation.get("collisionWindowBounds"), dict) else None
    if bounds is None and parsed_window is not None:
        bounds = {
            "minSceneX": parsed_window.min_scene_x,
            "maxSceneX": parsed_window.max_scene_x,
            "minSceneY": parsed_window.min_scene_y,
            "maxSceneY": parsed_window.max_scene_y,
            "width": parsed_window.width,
            "height": parsed_window.height,
        }
    missing_reason = None
    if payload is None:
        missing_reason = "collision_window_missing"
    elif parsed_window is None:
        missing_reason = "collision_window_payload_without_flags"
    elif fresh is False:
        missing_reason = "collision_window_stale"
    missing: list[str] = []
    if not collision_window_available or fresh is False:
        missing.append("navigation.local_collision_window")
    if navigation.get("status") in {"summary", "local", None}:
        missing.append("navigation.full_pathfinding")
    normalized_raw = dict(navigation)
    normalized_raw.update(
        {
            "collisionWindowAvailable": collision_window_available,
            "collisionWindowFresh": fresh,
            "collisionWindowRadius": radius,
            "collisionWindowCenterWorld": _collision_window_center(navigation, payload),
            "collisionWindowPlane": plane,
            "collisionWindowAgeTicks": age_ticks,
            "collisionWindowBounds": bounds,
            "collisionWindowMissingReason": missing_reason,
        }
    )
    if parsed_window is not None and isinstance(payload, dict):
        normalized_raw["collisionWindow"] = payload
    return NavigationContext(
        status="WARN" if missing else "PASS",
        warnings=[],
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=navigation.get("latestTick") if isinstance(navigation.get("latestTick"), int) else None,
        retained_from_previous=False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        collision_known=navigation.get("collisionKnown"),
        collision_window_available=collision_window_available,
        collision_window_fresh=fresh,
        collision_window_radius=radius,
        collision_window_center_world=_collision_window_center(navigation, payload),
        collision_window_plane=plane,
        collision_window_age_ticks=age_ticks,
        collision_window_tiles=payload if parsed_window is not None else None,
        collision_window_bounds=bounds,
        collision_window_missing_reason=missing_reason,
        reachable_count=reachable,
        blocked_count=blocked,
        unknown_count=unknown,
        raw=normalized_raw,
    )
