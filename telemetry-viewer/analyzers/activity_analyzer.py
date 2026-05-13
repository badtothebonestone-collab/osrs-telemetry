from __future__ import annotations

import time
from typing import Any

import capabilities
from analyzers.live_state import ActivityContext


VALID_CURRENT_STATES = {"idle", "moving", "interacting", "animating", "likely_busy", "unknown"}


def _activity_payload(activity: dict[str, Any]) -> dict[str, Any]:
    if isinstance(activity.get("activityState"), dict):
        return activity["activityState"]
    if isinstance(activity.get("activity"), dict):
        return activity["activity"]
    return activity


def _event_mentions(event: dict[str, Any], *needles: str) -> bool:
    haystack = " ".join(str(event.get(key) or "") for key in ("eventType", "summary", "message")).lower()
    return any(needle in haystack for needle in needles)


def analyze_activity(activity: dict[str, Any] | None, events: list[dict[str, Any]] | None = None) -> ActivityContext:
    started = time.perf_counter()
    activity = activity if isinstance(activity, dict) else {}
    payload = _activity_payload(activity)
    current = str(payload.get("apparentState") or payload.get("state") or "unknown").lower()
    if current not in VALID_CURRENT_STATES:
        current = "unknown"
    signals: list[str] = []
    woodcutting = activity.get("woodcuttingState") if isinstance(activity.get("woodcuttingState"), dict) else {}
    if str(woodcutting.get("woodcuttingState") or "").lower() == "target_depleted":
        signals.append("target depleted recently")
    for event in events or []:
        if isinstance(event, dict) and _event_mentions(event, "target_depleted", "depleted", "despawned"):
            if "target depleted recently" not in signals:
                signals.append("target depleted recently")
    missing: list[str] = []
    if payload.get("animationFrame") is None:
        missing.append("activity.animation_frame")
    if payload.get("isMoving") is None:
        missing.append("activity.explicit_movement_state")
    return ActivityContext(
        status="WARN" if current == "unknown" else "PASS",
        warnings=[],
        missing_capabilities=capabilities.normalize_capability_names(missing),
        source_tick=activity.get("latestTick") if isinstance(activity.get("latestTick"), int) else None,
        retained_from_previous=False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        current_activity=current,
        recent_task_signals=signals,
        raw=dict(activity),
    )
