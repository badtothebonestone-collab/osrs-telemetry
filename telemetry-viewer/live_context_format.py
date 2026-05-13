from __future__ import annotations

from typing import Any

import capabilities


def safe_get(value: Any, path: str | list[str], default: Any = None) -> Any:
    parts = path.split(".") if isinstance(path, str) else path
    current = value
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return default
        if current is None:
            return default
    return current


def text(value: Any, default: str = "unknown") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def bool_label(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def full_label(value: Any) -> str:
    if value is True:
        return "full"
    if value is False:
        return "not full"
    return "unknown"


def reachability_label(value: Any) -> str:
    return {
        "reachable": "yes",
        "blocked": "blocked",
        "unknown": "unknown",
        None: "unknown",
    }.get(value, str(value))


def liveness_label(value: Any) -> str:
    return {
        "live": "live",
        "live_assumed": "assumed live",
        "unknown": "unknown",
        "depleted_or_stump": "depleted/stump",
        "recently_despawned": "recently disappeared",
        "stale": "stale",
        "changed": "changed",
        None: "unknown",
    }.get(value, str(value))


def missing_capability_label(value: Any) -> str:
    value = capabilities.normalize_capability_name(value)
    return {
        "navigation.full_pathfinding": "full pathfinding is not implemented yet",
        "inventory.deltas": "inventory change tracking is not available yet",
        "activity.animation_frame": "animation frame detail is unavailable",
        "activity.explicit_movement_state": "explicit movement state is unavailable",
        "navigation.local_collision_window": "local collision window is unavailable",
        "collisionGridPathing": "collision grid pathing is not available yet",
        "collisionSummary": "collision summary is unavailable",
        "collisionWindow": "local collision window is unavailable",
    }.get(str(value), str(value))


def aim_label(candidate: dict | None) -> str:
    if not isinstance(candidate, dict):
        return "unknown"
    aim = candidate.get("aimPoint")
    if not isinstance(aim, dict):
        return "unknown"
    x = aim.get("canvasX", aim.get("x"))
    y = aim.get("canvasY", aim.get("y"))
    if x is None or y is None:
        return "unknown"
    try:
        return f"{float(x):.1f}, {float(y):.1f}"
    except (TypeError, ValueError):
        return f"{x}, {y}"


def location_label(candidate: dict | None) -> str:
    if not isinstance(candidate, dict):
        return "unknown"
    world_x = candidate.get("worldX")
    world_y = candidate.get("worldY")
    plane = candidate.get("plane")
    if world_x is None or world_y is None:
        return "unknown"
    return f"{world_x}, {world_y}, plane {text(plane)}"


def distance_label(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == 1:
        return "1 tile"
    if number.is_integer():
        return f"{int(number)} tiles"
    return f"{number:.1f} tiles"


def candidate_identity(candidate: dict | None) -> tuple[Any, ...] | None:
    if not isinstance(candidate, dict):
        return None
    return (
        candidate.get("id"),
        candidate.get("hash"),
        candidate.get("worldX"),
        candidate.get("worldY"),
        candidate.get("plane"),
    )


def candidate_navigation(candidate: dict | None) -> dict:
    if not isinstance(candidate, dict):
        return {}
    nav = candidate.get("navigation")
    return nav if isinstance(nav, dict) else {}


def candidate_block(title: str, candidate: dict | None, *, compact: bool = False) -> list[str]:
    if not isinstance(candidate, dict) or not candidate:
        return [f"{title}:", "  unavailable"]
    nav = candidate_navigation(candidate)
    lines = [
        f"{title}:",
        f"  Name: {text(candidate.get('targetName') or candidate.get('name'))}",
        f"  ID: {text(candidate.get('id'))}",
        f"  Distance: {distance_label(candidate.get('distanceTiles'))}",
        f"  Reachable: {reachability_label(nav.get('directReachability'))}",
        f"  Aim point: {aim_label(candidate)}",
        f"  Liveness: {liveness_label(candidate.get('targetLiveState'))}",
        f"  Quality: {text(candidate.get('qualityTier'))}",
    ]
    if candidate.get("qualityScore") is not None:
        lines[-1] += f" ({candidate.get('qualityScore')})"
    if compact:
        return lines
    lines.insert(3, f"  Location: {location_label(candidate)}")
    lines.extend(
        [
            f"  On screen: {bool_label(candidate.get('onScreen'))}",
            f"  Geometry: {bool_label(candidate.get('geometryAvailable'))}",
            f"  UI blocked: {bool_label(candidate.get('uiBlocked'))}",
            f"  Path length: {distance_label(nav.get('pathLengthTiles'))}",
        ]
    )
    return lines


def response_best(response: dict, class_id: str) -> dict | None:
    return safe_get(response, ["bestCandidates", class_id]) or safe_get(response, ["taskSummary", "bestTree"]) or safe_get(response, ["candidateSummary", "bestTree"])


def response_nearest(response: dict, class_id: str) -> dict | None:
    return safe_get(response, ["nearestCandidates", class_id]) or safe_get(response, ["taskSummary", "nearestTree"]) or safe_get(response, ["candidateSummary", "nearestTree"])


def response_reachability_summary(response: dict, class_id: str) -> dict:
    summary = safe_get(response, ["reachabilitySummary", class_id])
    if isinstance(summary, dict):
        return summary
    summary = safe_get(response, ["taskSummary", "reachabilitySummary"])
    if isinstance(summary, dict):
        return summary
    summary = response.get("reachabilitySummary")
    return summary if isinstance(summary, dict) else {}


def response_navigation(response: dict) -> dict:
    nav = response.get("navigationReadiness")
    if isinstance(nav, dict):
        return nav
    nav = safe_get(response, "taskSummary.navigationReadiness")
    return nav if isinstance(nav, dict) else {}


def response_inventory(response: dict) -> dict:
    inventory = response.get("inventory")
    if isinstance(inventory, dict):
        return inventory
    inventory = response.get("inventoryState")
    if isinstance(inventory, dict):
        return inventory
    inventory = safe_get(response, "taskSummary.inventoryState")
    return inventory if isinstance(inventory, dict) else {}


def response_player(response: dict) -> dict:
    player = safe_get(response, "baseline.player")
    if isinstance(player, dict):
        return player
    player = safe_get(response, "stateSummary.player")
    if isinstance(player, dict):
        return player
    player = safe_get(response, "taskSummary.player")
    return player if isinstance(player, dict) else {}


def response_activity(response: dict) -> tuple[str, str]:
    activity = response.get("activity")
    if not isinstance(activity, dict):
        activity = response.get("activityState")
    if not isinstance(activity, dict):
        activity = safe_get(response, "taskSummary.activityState")
    if not isinstance(activity, dict):
        activity = {}
    woodcutting = response.get("woodcuttingState")
    if not isinstance(woodcutting, dict):
        woodcutting = safe_get(response, "taskSummary.woodcuttingState")
    if not isinstance(woodcutting, dict):
        woodcutting = {}
    woodcutting_state = text(woodcutting.get("woodcuttingState"), "")
    if woodcutting_state in {"target_depleted", "waiting_for_respawn", "depleted_or_stump"}:
        woodcutting_state = ""
    return text(activity.get("apparentState")), woodcutting_state


def response_recent_inventory_deltas(response: dict) -> list[dict]:
    deltas = response.get("recentInventoryDeltas")
    if not isinstance(deltas, list):
        deltas = safe_get(response, "taskSummary.recentInventoryDeltas")
    if not isinstance(deltas, list):
        inventory = response_inventory(response)
        deltas = inventory.get("recentItemDeltas") if isinstance(inventory.get("recentItemDeltas"), list) else []
    return [delta for delta in deltas if isinstance(delta, dict)]


def response_recent_events(response: dict) -> list[dict]:
    events = response.get("events")
    if not isinstance(events, list):
        events = response.get("recentEvents")
    if not isinstance(events, list):
        events = safe_get(response, "taskSummary.recentEvents")
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def inventory_delta_label(delta: dict) -> str:
    changes = delta.get("changes") if isinstance(delta.get("changes"), list) else delta.get("quantityChanges")
    if isinstance(changes, list) and changes:
        parts = []
        for change in changes[:3]:
            if not isinstance(change, dict):
                continue
            item_id = text(change.get("itemId"))
            amount = change.get("delta")
            if amount is None:
                before = change.get("beforeQuantity")
                after = change.get("afterQuantity")
                parts.append(f"item {item_id}: {text(before)} -> {text(after)}")
            else:
                sign = "+" if isinstance(amount, (int, float)) and amount > 0 else ""
                parts.append(f"item {item_id}: {sign}{amount}")
        if parts:
            extra = "" if len(changes) <= 3 else f", +{len(changes) - 3} more"
            return "; ".join(parts) + extra
    changed_slots = delta.get("changedSlots") if isinstance(delta.get("changedSlots"), list) else []
    if changed_slots:
        return f"{len(changed_slots)} slot changes"
    return "inventory signature changed"


def all_warnings(response: dict, compact: bool = False) -> list[str]:
    values: list[str] = []
    missing_values: list[Any] = []
    for item in response.get("missingCapabilities") or []:
        missing_values.append(item)
    for item in response.get("warnings") or []:
        values.append(str(item))
    task = response.get("taskSummary")
    if isinstance(task, dict):
        for item in task.get("missingCapabilities") or []:
            missing_values.append(item)
        for item in task.get("warnings") or []:
            values.append(str(item))
    for item in capabilities.normalize_capability_names(missing_values):
        values.append(missing_capability_label(item))
    seen = set()
    result = []
    for value in values:
        lowered = str(value).lower()
        if compact and ("no frame path" in lowered or ("frame path" in lowered and "baseline" in lowered)):
            continue
        if compact and value == missing_capability_label("activity.animation_frame"):
            continue
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def recent_task_signals(response: dict, woodcutting_state: str, events: list[dict]) -> list[str]:
    signals: list[str] = []
    if woodcutting_state in {"target_depleted", "waiting_for_respawn", "depleted_or_stump"}:
        signals.append("target depleted recently")
    for event in events:
        joined = " ".join(str(event.get(key) or "") for key in ("eventType", "summary", "severity")).lower()
        if any(token in joined for token in ("target_depleted", "depleted", "stump", "despawned")):
            signals.append("target depleted recently")
        if "inventory" in joined and "change" in joined:
            signals.append("inventory changed recently")
    task_signals = response.get("recentTaskSignals")
    if isinstance(task_signals, list):
        signals.extend(str(item) for item in task_signals if item)
    seen: set[str] = set()
    result: list[str] = []
    for signal in signals:
        if signal and signal not in seen:
            seen.add(signal)
            result.append(signal)
    return result


def diagnostics_lines(response: dict) -> list[str]:
    diagnostics = response.get("diagnostics") if isinstance(response.get("diagnostics"), dict) else {}
    input_source = diagnostics.get("inputSource") if isinstance(diagnostics.get("inputSource"), dict) else {}
    lines = [
        "Diagnostics:",
        f"  Source cap hit: {bool_label(diagnostics.get('sourceCapHit'))}",
        f"  Budget exceeded: {bool_label(diagnostics.get('budgetExceeded'))}",
        f"  Write failures: {text(diagnostics.get('writeFailures'), '0')}",
        f"  Input source: {text(input_source.get('inputSourceActive'))}",
    ]
    if response.get("serviceTimingMillis") is not None:
        lines.append(f"  Service timing: {response.get('serviceTimingMillis')} ms")
    return lines


def format_woodcutting_summary(response: dict, compact: bool = False, top: int = 3) -> str:
    status = response.get("status") or safe_get(response, "taskSummary.status", "unknown")
    latest_tick = response.get("latestTick") or safe_get(response, "taskSummary.latestTick") or safe_get(response, "stateSummary.freshness.latestTick")
    freshness = response.get("freshness") if isinstance(response.get("freshness"), dict) else safe_get(response, "stateSummary.freshness", {})
    player = response_player(response)
    inventory = response_inventory(response)
    raw_woodcutting = response.get("woodcuttingState")
    if not isinstance(raw_woodcutting, dict):
        raw_woodcutting = safe_get(response, "taskSummary.woodcuttingState")
    raw_woodcutting_state = text(raw_woodcutting.get("woodcuttingState") if isinstance(raw_woodcutting, dict) else None, "")
    activity, woodcutting_state = response_activity(response)
    recent_inventory_deltas = response_recent_inventory_deltas(response)
    recent_events = response_recent_events(response)
    best = response_best(response, "tree")
    nearest = response_nearest(response, "tree")
    navigation = response_navigation(response)
    reachability = response_reachability_summary(response, "tree")

    header = f"WOODCUTTING CONTEXT - {text(status).upper()}"
    if latest_tick is not None:
        header += f" (tick {latest_tick})"

    lines = [header, ""]
    if freshness.get("liveFileAgeMillis") is not None:
        lines.append(f"Freshness: {int(freshness.get('liveFileAgeMillis'))} ms old")
    active_profile = safe_get(response, "diagnostics.activeProfile")
    if active_profile:
        lines.append(f"Active profile: {active_profile}")
    if lines[-1] != "":
        lines.append("")

    lines.extend(
        [
            "Player:",
            f"  Location: {text(player.get('worldX'))}, {text(player.get('worldY'))}, plane {text(player.get('plane'))}",
            f"  Scene tile: {text(player.get('sceneX'))}, {text(player.get('sceneY'))}",
            f"  Activity: {activity if not woodcutting_state else activity + ' / ' + woodcutting_state}",
            f"  Inventory: {text(inventory.get('freeSlots'))} free slots, {full_label(inventory.get('inventoryFull'))}",
            f"  Inventory changed recently: {bool_label(inventory.get('changedRecently'))}",
            "",
        ]
    )
    signals = recent_task_signals(response, raw_woodcutting_state, recent_events)
    if signals:
        lines.append("Recent task signals:")
        for signal in signals[:top]:
            lines.append(f"  - {signal}")
        lines.append("")
    if recent_inventory_deltas and not compact:
        lines.append("Recent inventory changes:")
        for delta in recent_inventory_deltas[:3]:
            tick = delta.get("toTick") or delta.get("tick")
            lines.append(f"  Tick {text(tick)}: {inventory_delta_label(delta)}")
        lines.append("")

    if recent_events:
        lines.append("Recent events:")
        for event in recent_events[:top]:
            tick = text(event.get("tick"))
            severity = text(event.get("severity"), "info")
            lines.append(f"  - [tick {tick}] {event.get('summary') or event.get('eventType')} ({severity})")
        lines.append("")

    lines.extend(candidate_block("Best tree", best, compact=compact))
    lines.append("")
    if nearest and candidate_identity(nearest) == candidate_identity(best):
        lines.extend(["Nearest tree:", "  same as best"])
    else:
        lines.extend(candidate_block("Nearest tree", nearest, compact=True))
    lines.append("")

    lines.extend(
        [
            "Reachability:",
            f"  Nearby tree candidates: {text(reachability.get('candidateCount'))}",
            f"  Reachable: {text(reachability.get('reachableCount'), '0')}",
            f"  Blocked: {text(reachability.get('blockedCount'), '0')}",
            f"  Unknown: {text(reachability.get('unknownCount'), '0')}",
            f"  Collision known: {bool_label(navigation.get('collisionKnown'))}",
            f"  Collision window: {('available' if navigation.get('collisionWindowAvailable') else 'unavailable')}, radius {text(navigation.get('collisionWindowRadius'))}",
            "",
        ]
    )

    warnings = all_warnings(response, compact=compact)
    if warnings:
        lines.append("Warnings:")
        for warning in warnings[:10 if compact else 20]:
            lines.append(f"  - {warning}")
        if len(warnings) > (10 if compact else 20):
            lines.append(f"  - ... {len(warnings) - (10 if compact else 20)} more")
        lines.append("")

    if not compact:
        lines.extend(diagnostics_lines(response))

    return "\n".join(lines).rstrip() + "\n"


def format_reachability_summary(response: dict, class_id: str = "tree", top: int = 3) -> str:
    summary = response_reachability_summary(response, class_id)
    if not summary:
        summary = response.get("reachabilitySummary") if isinstance(response.get("reachabilitySummary"), dict) else {}
    player = response.get("player") if isinstance(response.get("player"), dict) else response_player(response)
    window = response.get("collisionWindow") if isinstance(response.get("collisionWindow"), dict) else safe_get(response, ["reachabilitySummary", class_id, "collisionWindow"], {})
    if not isinstance(window, dict):
        window = {}
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        candidates = safe_get(response, ["reachabilityCandidates", class_id], [])
    candidates = candidates if isinstance(candidates, list) else []

    lines = [
        f"CANDIDATE REACHABILITY QA - {text(response.get('status')).upper()}",
        f"Class: {class_id}",
        f"Latest tick: {text(response.get('latestTick'))}",
        f"Player scene: {text(player.get('sceneX'))}, {text(player.get('sceneY'))}, plane {text(player.get('plane'))}",
        f"Collision window: {('available' if window.get('available') else 'unavailable')}, radius {text(window.get('radius'))}",
        "",
        "Counts:",
        f"  Candidates: {text(summary.get('candidateCount'))}",
        f"  Inside window: {text(summary.get('candidatesInsideCollisionWindow'))}",
        f"  Outside window: {text(summary.get('candidatesOutsideCollisionWindow'))}",
        f"  Reachable: {text(summary.get('reachableCount'), '0')}",
        f"  Blocked: {text(summary.get('blockedCount'), '0')}",
        f"  Unknown: {text(summary.get('unknownCount'), '0')}",
    ]
    if candidates:
        lines.extend(["", f"Top {min(top, len(candidates))}:"])
        for index, candidate in enumerate(candidates[:top], start=1):
            lines.append(
                f"  {index}. {text(candidate.get('targetName'))} id={text(candidate.get('id'))} "
                f"distance={distance_label(candidate.get('distanceTiles'))} "
                f"reachable={reachability_label(candidate.get('directReachability'))} "
                f"aim={aim_label(candidate)} live={liveness_label(candidate.get('targetLiveState'))}"
            )
    warnings = all_warnings(response)
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings[:10]:
            lines.append(f"  - {warning}")
    return "\n".join(lines).rstrip() + "\n"


def format_context_human(response: dict, compact: bool = False, top: int = 3) -> str:
    schema = response.get("schema")
    if schema == "live_candidate_reachability_qa.v1":
        return format_reachability_summary(response, response.get("classId") or "tree", top=top)
    if schema == "context_response.v1":
        if response.get("reachabilitySummary") and not (response.get("bestCandidates") or response.get("taskSummary")):
            class_id = next(iter(response.get("reachabilitySummary") or {"tree": {}}), "tree")
            return format_reachability_summary(response, class_id, top=top)
        return format_woodcutting_summary(response, compact=compact, top=top)
    if schema == "live_task_context.v1":
        return format_woodcutting_summary(response, compact=compact, top=top)
    return format_woodcutting_summary(response, compact=compact, top=top)
