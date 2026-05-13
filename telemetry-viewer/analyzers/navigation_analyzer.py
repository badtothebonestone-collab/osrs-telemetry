from __future__ import annotations

import time
from typing import Any

import capabilities
from analyzers.live_state import NavigationContext


def _reachability(candidate: dict[str, Any]) -> str:
    nav = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    return str(nav.get("directReachability") or candidate.get("directReachability") or candidate.get("reachability") or "unknown").lower()


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
    missing: list[str] = []
    if not navigation.get("collisionWindowAvailable"):
        missing.append("navigation.local_collision_window")
    if navigation.get("status") in {"summary", "local", None}:
        missing.append("navigation.full_pathfinding")
    return NavigationContext(
        status="WARN" if missing else "PASS",
        warnings=[],
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=navigation.get("latestTick") if isinstance(navigation.get("latestTick"), int) else None,
        retained_from_previous=False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        collision_known=navigation.get("collisionKnown"),
        collision_window_available=navigation.get("collisionWindowAvailable"),
        reachable_count=reachable,
        blocked_count=blocked,
        unknown_count=unknown,
        raw=dict(navigation),
    )
