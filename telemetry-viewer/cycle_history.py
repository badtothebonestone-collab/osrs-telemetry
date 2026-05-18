from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import diagnose_woodcut_bank_cycle


DEFAULT_CAPACITY = 100
SIGNATURE_KEYS = (
    "cycleStage",
    "phase",
    "activeIntent",
    "selectedTargetName",
    "selectedTargetType",
    "bankOpen",
    "bankingComplete",
    "serviceReady",
    "closeBankNeeded",
    "resourceTargetAvailable",
)


def _as_int(value: Any) -> int | None:
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


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _target_name(target: Any) -> str | None:
    if not isinstance(target, dict):
        return None
    value = target.get("targetName") or target.get("name") or target.get("label") or target.get("classId") or target.get("targetType")
    return str(value) if value is not None else None


def _target_type(target: Any) -> str | None:
    if not isinstance(target, dict):
        return None
    value = target.get("classId") or target.get("targetType") or target.get("type")
    return str(value) if value is not None else None


def _tick_from_status(status: dict[str, Any]) -> int | None:
    for key in ("lastProcessedTick", "latestTickProcessed", "latestTick", "pluginSnapshotLatestTick"):
        tick = _as_int(status.get(key))
        if tick is not None:
            return tick
    return None


def entry_from_cycle_payload(
    payload: dict[str, Any],
    *,
    tick: int | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    inventory = _dict(payload.get("inventory"))
    service = _dict(payload.get("service"))
    bank = _dict(payload.get("bank"))
    operation = _dict(payload.get("bankOperation"))
    close_bank = _dict(payload.get("closeBank"))
    post_bank = _dict(payload.get("postBank"))
    return_context = _dict(payload.get("returnToResource"))
    overlay = _dict(payload.get("overlay"))
    selected = _dict(overlay.get("selected"))
    return {
        "tick": tick,
        "timestamp": timestamp,
        "cycleStage": payload.get("cycleStage"),
        "phase": payload.get("phase"),
        "activeIntent": payload.get("activeIntent"),
        "reason": payload.get("reason"),
        "selectedTargetName": _target_name(selected),
        "selectedTargetType": _target_type(selected),
        "inventoryFreeSlots": inventory.get("freeSlots"),
        "inventoryFull": inventory.get("inventoryFull"),
        "serviceReady": payload.get("serviceReady", service.get("serviceReady")),
        "bankOpen": payload.get("bankOpen", bank.get("bankOpen")),
        "bankReadable": payload.get("bankReadable", bank.get("bankReadable")),
        "operationNeeded": operation.get("operationNeeded"),
        "bankingComplete": operation.get("bankingComplete"),
        "closeBankNeeded": payload.get("closeBankNeeded", close_bank.get("closeBankNeeded")),
        "postBankReason": post_bank.get("reason"),
        "returnReason": return_context.get("reason"),
        "resourceTargetAvailable": return_context.get("resourceTargetAvailable", post_bank.get("resourceTargetAvailable")),
        "warningCount": len(_list(payload.get("warnings"))),
        "missingCapabilityCount": len(_list(payload.get("missingCapabilities"))),
    }


def entry_from_status(status: dict[str, Any], *, timestamp: str | None = None) -> dict[str, Any]:
    payload = diagnose_woodcut_bank_cycle.build_from_daemon(status)
    return entry_from_cycle_payload(payload, tick=_tick_from_status(status), timestamp=timestamp)


def signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(key) for key in SIGNATURE_KEYS)


@dataclass
class CycleHistoryTracker:
    capacity: int = DEFAULT_CAPACITY
    entries: deque[dict[str, Any]] = field(init=False)
    last_signature: tuple[Any, ...] | None = None
    transition_count: int = 0
    current_stage_started_tick: int | None = None
    last_seen_tick: int | None = None
    last_stage_change_tick: int | None = None
    last_cycle_stage: str | None = None
    last_transition_reason: str | None = None

    def __post_init__(self) -> None:
        self.entries = deque(maxlen=max(1, int(self.capacity)))

    def update(self, entry: dict[str, Any]) -> bool:
        row = dict(entry)
        tick = _as_int(row.get("tick"))
        if tick is not None:
            self.last_seen_tick = tick
        current_signature = signature(row)
        if self.last_signature == current_signature:
            return False

        previous = self.entries[-1] if self.entries else None
        previous_stage = previous.get("cycleStage") if isinstance(previous, dict) else None
        current_stage = row.get("cycleStage")
        stage_changed = bool(previous and previous_stage != current_stage)
        row["previousCycleStage"] = previous_stage
        row["transition"] = stage_changed
        if stage_changed:
            self.transition_count += 1
            self.last_cycle_stage = str(previous_stage) if previous_stage is not None else None
            self.last_transition_reason = str(row.get("reason") or current_stage or "state_changed")
            self.current_stage_started_tick = tick
            self.last_stage_change_tick = tick
        elif not previous:
            self.current_stage_started_tick = tick
            self.last_stage_change_tick = tick
            self.last_transition_reason = str(row.get("reason") or current_stage or "initial")
        else:
            self.last_transition_reason = str(row.get("reason") or current_stage or "state_changed")
        self.entries.append(row)
        self.last_signature = current_signature
        return True

    def current_stable_for_ticks(self) -> int | None:
        if self.current_stage_started_tick is None or self.last_seen_tick is None:
            return None
        return max(0, int(self.last_seen_tick) - int(self.current_stage_started_tick))

    def summary(self, *, tail: int = 10) -> dict[str, Any]:
        rows = list(self.entries)
        tail_count = max(0, int(tail))
        selected = rows[-tail_count:] if tail_count else []
        current = rows[-1] if rows else {}
        warning_summary = {
            "warningCount": current.get("warningCount"),
            "missingCapabilityCount": current.get("missingCapabilityCount"),
        }
        return {
            "currentCycleStage": current.get("cycleStage"),
            "currentCycleStageStableForTicks": self.current_stable_for_ticks(),
            "lastCycleStage": self.last_cycle_stage,
            "lastCycleTransitionReason": self.last_transition_reason,
            "lastStageChangeTick": self.last_stage_change_tick,
            "cycleHistoryCount": len(rows),
            "transitionCount": self.transition_count,
            "cycleHistory": [dict(row) for row in selected],
            "lastWarningSummary": warning_summary,
        }
