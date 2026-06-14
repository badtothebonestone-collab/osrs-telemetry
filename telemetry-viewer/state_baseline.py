"""Read-only R1 recovery state baseline payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import live_context_query as query


STATE_BASELINE_SCHEMA = "recovery_state_baseline.v1"


def _dict_value(*values: Any) -> dict:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _bool_value(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None


def compact_player(player: dict) -> dict:
    world_location = _dict_value(player.get("worldLocation"), player.get("location"))
    return {
        "worldX": query.first_value(player.get("worldX"), player.get("world_x"), player.get("playerWorldX"), world_location.get("worldX"), world_location.get("x")),
        "worldY": query.first_value(player.get("worldY"), player.get("world_y"), player.get("playerWorldY"), world_location.get("worldY"), world_location.get("y")),
        "plane": query.first_value(player.get("plane"), player.get("z"), player.get("playerPlane"), world_location.get("plane"), world_location.get("z")),
        "sceneX": query.first_value(player.get("sceneX"), player.get("scene_x"), player.get("playerSceneX")),
        "sceneY": query.first_value(player.get("sceneY"), player.get("scene_y"), player.get("playerSceneY")),
        "localX": query.first_value(player.get("localX"), player.get("local_x")),
        "localY": query.first_value(player.get("localY"), player.get("local_y")),
        "animation": player.get("animation"),
        "isMoving": player.get("isMoving"),
        "runEnergy": query.first_value(player.get("runEnergy"), player.get("runEnergyPercent")),
    }


def compact_inventory(inventory: dict) -> dict:
    normalized = query.normalize_inventory_state(inventory)
    if not normalized.get("known"):
        return {}
    return {
        "known": normalized.get("known"),
        "freeSlots": normalized.get("freeSlots"),
        "filledSlots": normalized.get("filledSlots"),
        "slotCount": normalized.get("slotCount"),
        "itemCount": normalized.get("itemCount"),
        "inventoryFull": normalized.get("inventoryFull"),
        "signature": normalized.get("signature"),
    }


def compact_activity(activity: dict) -> dict:
    if not isinstance(activity, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "apparentState",
        "currentActivity",
        "state",
        "confidence",
        "isBusy",
        "isMoving",
        "animation",
        "poseAnimation",
        "changedRecently",
        "latestTick",
        "generatedAtUtc",
        "timestampUtc",
    ):
        if key in activity:
            summary[key] = activity.get(key)
    evidence = activity.get("evidence")
    if isinstance(evidence, list):
        summary["evidenceCount"] = len(evidence)
    return summary


def _state_timestamp(status_doc: dict, baseline: dict, activity: dict) -> str | None:
    value = query.first_value(
        status_doc.get("generatedAtUtc"),
        status_doc.get("updatedAtUtc"),
        status_doc.get("timestampUtc"),
        status_doc.get("timestamp"),
        baseline.get("generatedAtUtc"),
        baseline.get("updatedAtUtc"),
        baseline.get("timestampUtc"),
        baseline.get("timestamp"),
        activity.get("generatedAtUtc"),
        activity.get("updatedAtUtc"),
        activity.get("timestampUtc"),
        activity.get("timestamp"),
    )
    return str(value) if value not in (None, "") else None


def _state_age_millis(timestamp: str | None) -> float | None:
    parsed = query.parse_utc(timestamp)
    if parsed is None:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() * 1000.0), 3)


def _state_source_files(context: dict) -> list[dict]:
    wanted = {"baseline", "status", "activity"}
    source_files = []
    for item in context.get("sourceFiles") or []:
        if isinstance(item, dict) and item.get("name") in wanted:
            source_files.append(
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "exists": item.get("exists"),
                    "modifiedUtc": item.get("modifiedUtc"),
                    "sizeBytes": item.get("sizeBytes"),
                }
            )
    return source_files


def state_baseline_payload(context: dict, args) -> dict[str, Any]:
    status_doc = context.get("status") if isinstance(context.get("status"), dict) else {}
    baseline = context.get("baseline") if isinstance(context.get("baseline"), dict) else {}
    activity = context.get("activity") if isinstance(context.get("activity"), dict) else {}
    client_tick_hot = _dict_value(status_doc.get("clientTickHot"), baseline.get("clientTickHot"))
    status_baseline = _dict_value(status_doc.get("baseline"))
    player = _dict_value(
        baseline.get("player"),
        status_doc.get("player"),
        activity.get("player"),
        client_tick_hot.get("player"),
        status_doc.get("playerLocation"),
    )
    inventory_source = _dict_value(
        activity.get("inventoryState"),
        activity.get("inventory"),
        baseline.get("inventory"),
        status_doc.get("inventory"),
    )
    timestamp = _state_timestamp(status_doc, baseline, activity)
    age_millis = _state_age_millis(timestamp)
    stale_threshold_ms = int(getattr(args, "state_stale_ms", query.DEFAULT_FRESHNESS_MS) or query.DEFAULT_FRESHNESS_MS)
    warnings = sorted(set(str(warning) for warning in context.get("warnings") or [] if warning))
    missing = sorted(set(str(field) for field in context.get("missingFields") or [] if field))
    if age_millis is None:
        warnings.append("state timestamp is unavailable; age cannot be computed.")
    elif age_millis > stale_threshold_ms:
        warnings.append(f"state timestamp is stale by {int(age_millis)} ms.")
    if not baseline and not status_doc:
        warnings.append("no readable baseline or status state is available.")

    game_state = query.first_value(
        status_doc.get("gameState"),
        client_tick_hot.get("gameState"),
        status_baseline.get("gameState"),
        baseline.get("gameState"),
        status_doc.get("game_state"),
        baseline.get("game_state"),
        status_doc.get("clientGameState"),
        baseline.get("clientGameState"),
    )
    logged_in = _bool_value(
        status_doc.get("loggedIn"),
        status_doc.get("isLoggedIn"),
        baseline.get("loggedIn"),
        baseline.get("isLoggedIn"),
        client_tick_hot.get("loggedIn"),
        client_tick_hot.get("isLoggedIn"),
    )
    if logged_in is None and isinstance(game_state, str):
        logged_in = game_state.upper() == "LOGGED_IN"

    bank = _dict_value(activity.get("bankState"), activity.get("bankUiContext"), status_doc.get("bankUiContext"), baseline.get("bank"))
    activity_state = _dict_value(activity.get("activityState"), activity.get("activity"))
    status = "PASS"
    if warnings or missing or not (baseline or status_doc):
        status = "WARN"

    payload: dict[str, Any] = {
        "schema": STATE_BASELINE_SCHEMA,
        "status": status,
        "generatedAtUtc": query.utc_now(),
        "sessionPath": str(context.get("session")) if context.get("session") else None,
        "gameState": game_state,
        "loggedIn": logged_in,
        "latestTick": query.latest_tick({"status": status_doc, "baseline": baseline}),
        "timestampUtc": timestamp,
        "stateAgeMillis": age_millis,
        "staleThresholdMillis": stale_threshold_ms,
        "player": compact_player(player),
        "inventory": compact_inventory(inventory_source),
        "warnings": sorted(set(warnings)),
        "missingFields": missing,
        "sourceFiles": _state_source_files(context),
    }
    if bank:
        payload["bank"] = bank
    if activity_state:
        payload["activity"] = activity_state
    return payload
