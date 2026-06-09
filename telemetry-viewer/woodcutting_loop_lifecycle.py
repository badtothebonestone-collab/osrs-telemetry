from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "woodcutting_loop_lifecycle.v1"
COMPACT_SCHEMA_VERSION = "woodcutting_loop_lifecycle_compact.v1"
NORMAL_LOG_ITEM_IDS = {1511, 1521}
NEAR_FULL_FREE_SLOT_THRESHOLD = 1
ROUTE_LEG_SPECS = {
    "woodcutting_area_to_bank": {
        "phase": "route_to_bank",
        "label": "Route to Bank",
        "routeName": "woodcutting_area_to_bank",
        "fromArea": "woodcutting_area",
        "toArea": "bank_area",
    },
    "bank_to_woodcutting_area": {
        "phase": "route_to_trees",
        "label": "Route to Trees",
        "routeName": "Bank_to_Woodcutting_area",
        "fromArea": "bank_area",
        "toArea": "woodcutting_area",
    },
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


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


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    return None


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_artifact(recording: Path | None, filename: str, summary: dict[str, Any], summary_key: str) -> dict[str, Any]:
    if isinstance(summary.get(summary_key), dict):
        return _dict(summary.get(summary_key))
    if recording:
        return _read_json(recording / filename)
    return {}


def _compact_woodcutting(lifecycle: dict[str, Any]) -> dict[str, Any]:
    inventory = _dict(lifecycle.get("inventory"))
    clicks = _dict(lifecycle.get("clicks"))
    animation = _dict(lifecycle.get("animation"))
    current = _dict(lifecycle.get("current"))
    interruption = _dict(lifecycle.get("interruption"))
    cycles = _list(lifecycle.get("cycles"))
    cycle_logs_gained = sum(_int(_dict(cycle).get("logsGained")) or 0 for cycle in cycles)
    inventory_logs_gained = _int(inventory.get("normalLogsGained")) or 0
    logs_gained = inventory_logs_gained if inventory_logs_gained > 0 else cycle_logs_gained
    free_slots_start = _int(inventory.get("freeSlotsStart"))
    inventory_full_observed = _bool(inventory.get("inventoryFull"))
    if not inventory_full_observed and free_slots_start is not None and cycle_logs_gained >= free_slots_start > 0:
        inventory_full_observed = True
    warnings = _list(lifecycle.get("warnings"))[:8]
    if cycle_logs_gained > 0:
        warnings = [warning for warning in warnings if str(warning) != "No positive normal log gain was found."]
    return {
        "status": lifecycle.get("status"),
        "phase": lifecycle.get("phase"),
        "confidence": lifecycle.get("confidence"),
        "normalLogsStart": inventory.get("normalLogsStart"),
        "normalLogsEnd": inventory.get("normalLogsEnd"),
        "normalLogsGained": logs_gained,
        "netNormalLogsGained": inventory_logs_gained,
        "cycleLogsGained": cycle_logs_gained,
        "freeSlotsStart": inventory.get("freeSlotsStart"),
        "freeSlotsEnd": inventory.get("freeSlotsEnd"),
        "inventoryFull": inventory_full_observed,
        "nearFull": _near_full(inventory.get("freeSlotsEnd")),
        "inventoryFilledDuringLoop": bool(inventory_full_observed and cycle_logs_gained > 0),
        "freshChopClickCount": clicks.get("freshChopClickCount") or 0,
        "activeSnapshotCount": animation.get("activeSnapshotCount") or 0,
        "animationActive": current.get("animationActive"),
        "cycleCount": len(cycles),
        "interruption": interruption or None,
        "warnings": warnings,
    }


def _compact_banking(lifecycle: dict[str, Any]) -> dict[str, Any]:
    bank = _dict(lifecycle.get("bank"))
    deposit = _dict(lifecycle.get("deposit"))
    actions = _dict(lifecycle.get("actions"))
    inventory = _dict(lifecycle.get("inventory"))
    deposited = _list(deposit.get("items"))
    if not deposited:
        for raw in _list(bank.get("changedItems")):
            item = _dict(raw)
            delta = _int(item.get("delta"))
            if delta is None or delta <= 0:
                continue
            deposited.append(
                {
                    "id": _first(item.get("id"), item.get("itemId")),
                    "name": item.get("name"),
                    "quantity": delta,
                    "confirmationLevel": lifecycle.get("depositConfirmationLevel") or "bank_container_delta_confirmed",
                    "source": "bank_container_delta",
                }
            )
    deposit_action_count = _int(actions.get("depositActionCount")) or 0
    deposited_count = sum(_int(_dict(item).get("quantity")) or 0 for item in deposited)
    deposit_detected = bool(deposit.get("detected")) or bool(deposited and (deposit_action_count > 0 or bank.get("bankContainerDeltaAvailable") or lifecycle.get("bankContainerDeltaAvailable")))
    confirmation_level = lifecycle.get("depositConfirmationLevel") or deposit.get("confirmationLevel")
    if (not confirmation_level or confirmation_level == "none") and deposited and bank.get("changedItems"):
        confirmation_level = "bank_container_delta_confirmed"
    return {
        "status": lifecycle.get("status"),
        "phase": lifecycle.get("phase"),
        "confidence": lifecycle.get("confidence"),
        "bankLikeInterface": lifecycle.get("bankLikeInterface"),
        "bankOpenSeen": bank.get("openSeen"),
        "depositBoxOpenSeen": bank.get("depositBoxOpenSeen"),
        "bankContainerAvailable": bank.get("containerAvailable"),
        "bankContainerDeltaAvailable": lifecycle.get("bankContainerDeltaAvailable", bank.get("bankContainerDeltaAvailable")),
        "depositDetected": deposit_detected,
        "depositedItems": deposited,
        "depositedItemCount": deposit.get("totalDepositedCount") or deposited_count,
        "depositConfirmationLevel": confirmation_level,
        "normalLogsBefore": inventory.get("normalLogsBefore"),
        "normalLogsAfter": inventory.get("normalLogsAfter"),
        "freeSlotsBefore": inventory.get("freeSlotsBefore"),
        "freeSlotsAfter": inventory.get("freeSlotsAfter"),
        "depositActionCount": deposit_action_count,
        "warnings": _list(lifecycle.get("warnings"))[:8],
        "missingCapabilities": _list(lifecycle.get("missingCapabilities"))[:8],
    }


def _route_areas(traversal: dict[str, Any], comparison: dict[str, Any], monitor: dict[str, Any], history: dict[str, Any]) -> tuple[str | None, str | None]:
    start = _first(
        _dict(traversal.get("start")).get("areaLabel"),
        comparison.get("detectedStartArea"),
        monitor.get("startArea"),
        history.get("startArea"),
    )
    end = _first(
        _dict(traversal.get("end")).get("areaLabel"),
        comparison.get("detectedEndArea"),
        monitor.get("currentArea"),
        history.get("currentArea"),
        history.get("endArea"),
    )
    return str(start) if start else None, str(end) if end else None


def _route_passed(traversal: dict[str, Any], comparison: dict[str, Any], monitor: dict[str, Any], history: dict[str, Any]) -> bool:
    statuses = {
        _lower(traversal.get("status")),
        _lower(comparison.get("status")),
        _lower(comparison.get("statusReason")),
        _lower(monitor.get("status")),
        _lower(history.get("status")),
    }
    return bool(statuses & {"pass", "pass_base_template", "pass_registered_variant"})


def _area_transitions(traversal: dict[str, Any]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    source_steps = _list(traversal.get("rawSteps")) or _list(traversal.get("steps"))
    for step in source_steps:
        step_dict = _dict(step)
        postcondition = _dict(step_dict.get("postcondition"))
        before = postcondition.get("areaBefore")
        after = postcondition.get("areaAfter")
        if not before or not after or before == after:
            continue
        transition = {
            "fromArea": before,
            "toArea": after,
            "stepIndex": step_dict.get("stepIndex"),
            "startTick": step_dict.get("startTick"),
            "endTick": step_dict.get("endTick"),
            "action": step_dict.get("action"),
            "targetName": step_dict.get("targetName"),
        }
        if transition not in transitions:
            transitions.append(transition)
    return transitions


def _route_directions(
    traversal: dict[str, Any],
    comparison: dict[str, Any],
    monitor: dict[str, Any],
    history: dict[str, Any],
    start: str | None,
    end: str | None,
    route_name: Any,
) -> list[str]:
    directions: list[str] = []

    def add(direction: str) -> None:
        if direction not in directions:
            directions.append(direction)

    if start == "woodcutting_area" and end == "bank_area":
        add("woodcutting_area_to_bank")
    if start == "bank_area" and end == "woodcutting_area":
        add("bank_to_woodcutting_area")

    name_text = _lower(route_name)
    if "woodcutting_area_to_bank" in name_text or "tree_area_to_bank" in name_text:
        add("woodcutting_area_to_bank")
    if "bank_to_woodcutting" in name_text or "bank_to_tree" in name_text:
        add("bank_to_woodcutting_area")

    for transition in _area_transitions(traversal):
        if transition.get("fromArea") == "woodcutting_area" and transition.get("toArea") == "bank_area":
            add("woodcutting_area_to_bank")
        elif transition.get("fromArea") == "bank_area" and transition.get("toArea") == "woodcutting_area":
            add("bank_to_woodcutting_area")

    return directions


def _route_leg_for_direction(direction: str, transitions: list[dict[str, Any]], passed: bool) -> dict[str, Any]:
    spec = ROUTE_LEG_SPECS.get(direction, {})
    from_area = spec.get("fromArea")
    to_area = spec.get("toArea")
    matched_transition: dict[str, Any] = {}
    for transition in transitions:
        if transition.get("fromArea") == from_area and transition.get("toArea") == to_area:
            matched_transition = transition
            break
    leg = {
        "phase": spec.get("phase") or direction,
        "label": spec.get("label") or direction,
        "direction": direction,
        "routeName": spec.get("routeName") or direction,
        "fromArea": from_area,
        "toArea": to_area,
        "status": "PASS" if passed and matched_transition else "WARN",
        "matched": bool(matched_transition),
    }
    if matched_transition:
        leg["transition"] = matched_transition
    return leg


def _route_legs(directions: list[str], transitions: list[dict[str, Any]], passed: bool) -> list[dict[str, Any]]:
    return [_route_leg_for_direction(direction, transitions, passed) for direction in directions]


def _route_leg_name(route: dict[str, Any], direction: str) -> str:
    for raw_leg in _list(route.get("routeLegs")):
        leg = _dict(raw_leg)
        if leg.get("direction") == direction:
            return str(leg.get("routeName") or direction)
    return direction


def _route_summary(traversal: dict[str, Any], comparison: dict[str, Any], monitor: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    start, end = _route_areas(traversal, comparison, monitor, history)
    route_name = _first(traversal.get("routeName"), comparison.get("detectedRouteName"), monitor.get("routeName"), history.get("routeName"))
    status = _first(comparison.get("status"), monitor.get("status"), history.get("status"), traversal.get("status"))
    directions = _route_directions(traversal, comparison, monitor, history, start, end, route_name)
    direction = directions[0] if len(directions) == 1 else "multi_leg_loop" if len(directions) > 1 else "unknown"
    transitions = _area_transitions(traversal)
    passed = _route_passed(traversal, comparison, monitor, history)
    return {
        "routeName": route_name,
        "direction": direction,
        "directions": directions,
        "routeLegs": _route_legs(directions, transitions, passed),
        "areaTransitions": transitions,
        "status": status,
        "passed": passed,
        "startArea": start,
        "endArea": end,
        "routeState": _first(monitor.get("routeState"), history.get("routeState"), traversal.get("phase")),
        "templateStatus": comparison.get("status"),
        "templateStatusReason": comparison.get("statusReason"),
        "matchedSegmentCount": _first(comparison.get("matchedSegmentCount"), history.get("completedSegmentCount"), monitor.get("completedSegmentCount")),
        "requiredSegmentCount": _first(comparison.get("requiredSegmentCount"), monitor.get("requiredSegmentCount")),
        "warnings": (_list(traversal.get("warnings")) + _list(comparison.get("warnings")) + _list(monitor.get("warnings")) + _list(history.get("warnings")))[:8],
    }


def _near_full(value: Any, threshold: int = NEAR_FULL_FREE_SLOT_THRESHOLD) -> bool:
    free = _int(value)
    return free is not None and 0 <= free <= threshold


def _has_logs(items: list[Any]) -> bool:
    for raw in items:
        item = _dict(raw)
        item_id = _int(_first(item.get("id"), item.get("itemId")))
        name = _lower(item.get("name"))
        qty = _int(_first(item.get("quantity"), item.get("count"), item.get("delta")))
        if qty is not None and qty <= 0:
            continue
        if item_id in NORMAL_LOG_ITEM_IDS or "logs" in name:
            return True
    return False


def _logs_quantity(items: list[Any]) -> int:
    total = 0
    for raw in items:
        item = _dict(raw)
        item_id = _int(_first(item.get("id"), item.get("itemId")))
        name = _lower(item.get("name"))
        if item_id not in NORMAL_LOG_ITEM_IDS and "logs" not in name:
            continue
        total += _int(_first(item.get("quantity"), item.get("count"), item.get("delta"))) or 0
    return total


def _phase(name: str, *, status: str = "PASS", confidence: float = 0.8, evidence: list[str] | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "phase": name,
        "status": status,
        "confidence": round(max(0.0, min(0.99, float(confidence))), 3),
        "evidence": evidence or [],
        "details": details or {},
    }


def _phase_ref(name: str, label: str, evidence: str | None = None) -> dict[str, Any]:
    payload = {"phase": name, "label": label}
    if evidence:
        payload["reason"] = evidence
    return payload


def _best_status(phases: list[dict[str, Any]], warnings: list[str]) -> str:
    if not phases:
        return "FAIL"
    if any(_lower(phase.get("status")) == "PASS".lower() and (_float(phase.get("confidence")) or 0.0) >= 0.7 for phase in phases):
        return "PASS" if not warnings else "WARN"
    return "WARN"


def _confidence(phases: list[dict[str, Any]]) -> float:
    if not phases:
        return 0.0
    return round(min(0.95, max(_float(phase.get("confidence")) or 0.0 for phase in phases)), 3)


def _dedupe(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _attach_recording_inputs(recording_path: str | Path | None, summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    recording = Path(recording_path) if recording_path else None
    if recording and recording.is_file():
        recording = recording.parent
    return {
        "woodcutting": _load_artifact(recording, "woodcutting_lifecycle.json", summary, "woodcutting_lifecycle"),
        "banking": _load_artifact(recording, "banking_lifecycle.json", summary, "banking_lifecycle"),
        "traversal": _load_artifact(recording, "traversal_lifecycle.json", summary, "traversal_lifecycle"),
        "comparison": _load_artifact(recording, "route_template_comparison.json", summary, "route_template_comparison"),
        "monitor": _load_artifact(recording, "route_monitor_status.json", summary, "route_monitor"),
        "history": _load_artifact(recording, "route_history_summary.json", summary, "route_history"),
        "interruption": _load_artifact(recording, "interruption_lifecycle.json", summary, "interruption_lifecycle"),
        "damage": _load_artifact(recording, "combat_damage_summary.json", summary, "combat_damage_summary"),
        "human": _load_artifact(recording, "human_click_profile.json", summary, "human_click_profile"),
    }


def analyze_data(
    *,
    summary: dict[str, Any] | None = None,
    woodcutting_lifecycle: dict[str, Any] | None = None,
    banking_lifecycle: dict[str, Any] | None = None,
    traversal_lifecycle: dict[str, Any] | None = None,
    route_template_comparison: dict[str, Any] | None = None,
    route_monitor_status: dict[str, Any] | None = None,
    route_history_summary: dict[str, Any] | None = None,
    interruption_lifecycle: dict[str, Any] | None = None,
    combat_damage_summary: dict[str, Any] | None = None,
    human_click_profile: dict[str, Any] | None = None,
    recording_path: str | Path | None = None,
    near_full_threshold: int = NEAR_FULL_FREE_SLOT_THRESHOLD,
) -> dict[str, Any]:
    summary = _dict(summary)
    loaded = _attach_recording_inputs(recording_path, summary)
    woodcutting = _dict(woodcutting_lifecycle) or loaded["woodcutting"]
    banking = _dict(banking_lifecycle) or loaded["banking"]
    traversal = _dict(traversal_lifecycle) or loaded["traversal"]
    comparison = _dict(route_template_comparison) or loaded["comparison"]
    monitor = _dict(route_monitor_status) or loaded["monitor"]
    history = _dict(route_history_summary) or loaded["history"]
    interruption = _dict(interruption_lifecycle) or loaded["interruption"] or _dict(woodcutting.get("interruption"))
    damage = _dict(combat_damage_summary) or loaded["damage"]
    human = _dict(human_click_profile) or loaded["human"]

    phases: list[dict[str, Any]] = []
    evidence: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    wood = _compact_woodcutting(woodcutting) if woodcutting else {}
    bank = _compact_banking(banking) if banking else {}
    route = _route_summary(traversal, comparison, monitor, history) if any((traversal, comparison, monitor, history)) else {}

    start_area = _dict(traversal.get("start")).get("areaLabel") or comparison.get("detectedStartArea") or summary.get("detectedStartArea")
    end_area = _dict(traversal.get("end")).get("areaLabel") or comparison.get("detectedEndArea") or monitor.get("currentArea") or summary.get("detectedEndArea")
    current_area = _first(monitor.get("currentArea"), end_area, start_area)

    wood_status = _lower(woodcutting.get("status"))
    logs_gained = _int(wood.get("normalLogsGained")) or 0
    cycle_logs_gained = _int(wood.get("cycleLogsGained")) or 0
    fresh_chop = _int(wood.get("freshChopClickCount")) or 0
    animation_count = _int(wood.get("activeSnapshotCount")) or 0
    cutting_evidence = logs_gained > 0 or fresh_chop > 0 or animation_count > 0 or wood.get("phase") in {"chopping", "inventory_full"}
    inventory_full = bool(wood.get("inventoryFull")) or (_int(wood.get("freeSlotsEnd")) == 0)
    near_full = _near_full(wood.get("freeSlotsEnd"), near_full_threshold)
    route_directions = set(_list(route.get("directions")))

    if current_area == "woodcutting_area" or start_area == "woodcutting_area" or end_area == "woodcutting_area":
        if cutting_evidence or "bank_to_woodcutting_area" in route_directions or route.get("direction") == "bank_to_woodcutting_area":
            phases.append(_phase("at_trees", confidence=0.75, evidence=["woodcutting area evidence present"], details={"currentArea": current_area}))
            evidence.append("woodcutting area evidence present")

    if cutting_evidence:
        status = "PASS" if wood_status == "pass" or cycle_logs_gained > 0 else "WARN"
        confidence = max(_float(wood.get("confidence")) or 0.0, 0.75 if logs_gained > 0 else 0.6)
        phases.append(
            _phase(
                "cutting",
                status=status,
                confidence=confidence,
                evidence=[
                    item
                    for item in [
                        f"normal logs gained {logs_gained}" if logs_gained else None,
                        f"fresh Chop down clicks {fresh_chop}" if fresh_chop else None,
                        f"animation 879 snapshots {animation_count}" if animation_count else None,
                    ]
                    if item
                ],
                details=wood,
            )
        )
        evidence.append("woodcutting lifecycle signals indicate cutting")

    if inventory_full or near_full:
        phase_name = "inventory_full" if inventory_full else "inventory_near_full"
        fullness_evidence = f"free slots ended at {wood.get('freeSlotsEnd')}"
        if inventory_full and wood.get("inventoryFilledDuringLoop"):
            fullness_evidence = f"cycle log gain {cycle_logs_gained} filled starting free slots {wood.get('freeSlotsStart')}"
        phases.append(
            _phase(
                phase_name,
                confidence=0.9 if inventory_full else 0.75,
                evidence=[fullness_evidence],
                details={"freeSlotsEnd": wood.get("freeSlotsEnd"), "freeSlotsStart": wood.get("freeSlotsStart"), "cycleLogsGained": cycle_logs_gained, "threshold": near_full_threshold},
            )
        )
        evidence.append(f"woodcutting inventory is {'full' if inventory_full else 'near full'}")

    if route.get("direction") == "woodcutting_area_to_bank" or "woodcutting_area_to_bank" in route_directions:
        route_leg_name = _route_leg_name(route, "woodcutting_area_to_bank")
        phases.append(
            _phase(
                "routing_to_bank",
                status="PASS" if route.get("passed") else "WARN",
                confidence=0.9 if route.get("passed") else 0.65,
                evidence=[f"route leg {route_leg_name} reached bank_area"],
                details=route,
            )
        )
        evidence.append("route/traversal evidence shows woodcutting area to bank")

    if bank and (_lower(banking.get("status")) in {"pass", "warn"} and (bank.get("bankOpenSeen") or bank.get("depositBoxOpenSeen") or bank.get("depositDetected") or bank.get("depositedItems"))):
        phases.append(
            _phase(
                "banking",
                status="PASS" if _lower(banking.get("status")) == "pass" else "WARN",
                confidence=max(_float(bank.get("confidence")) or 0.0, 0.65),
                evidence=[
                    item
                    for item in [
                        "bank open observed" if bank.get("bankOpenSeen") else None,
                        "deposit box open observed" if bank.get("depositBoxOpenSeen") else None,
                        f"active interface {bank.get('bankLikeInterface')}" if bank.get("bankLikeInterface") else None,
                    ]
                    if item
                ],
                details=bank,
            )
        )
        evidence.append("banking lifecycle evidence present")

    deposit_items = _list(bank.get("depositedItems"))
    deposited_logs = _has_logs(deposit_items)
    if bank.get("depositDetected") and deposit_items:
        phases.append(
            _phase(
                "deposit_complete",
                status="PASS" if _lower(banking.get("status")) == "pass" else "WARN",
                confidence=max(_float(bank.get("confidence")) or 0.0, 0.9 if deposited_logs else 0.75),
                evidence=[f"deposited {bank.get('depositedItemCount') or _logs_quantity(deposit_items)} items"],
                details={"depositedItems": deposit_items, "depositConfirmationLevel": bank.get("depositConfirmationLevel"), "depositedLogs": deposited_logs},
            )
        )
        evidence.append("banking lifecycle shows deposit complete")

    if route.get("direction") == "bank_to_woodcutting_area" or "bank_to_woodcutting_area" in route_directions:
        route_leg_name = _route_leg_name(route, "bank_to_woodcutting_area")
        phases.append(
            _phase(
                "routing_to_trees",
                status="PASS" if route.get("passed") else "WARN",
                confidence=0.9 if route.get("passed") else 0.65,
                evidence=[f"route leg {route_leg_name} reached woodcutting_area"],
                details=route,
            )
        )
        evidence.append("route/traversal evidence shows bank to woodcutting area")

    task_resumed = bool(interruption.get("taskResumed")) or bool(_dict(damage.get("taskResume")).get("taskResumed"))
    if interruption.get("interruptionDetected"):
        phases.append(
            _phase(
                "interrupted",
                status="PASS" if _lower(interruption.get("status")) == "pass" else "WARN",
                confidence=max(_float(interruption.get("confidence")) or 0.0, 0.65),
                evidence=[f"{interruption.get('interruptionType') or 'unknown'} interruption cause={interruption.get('primaryCause') or 'unknown'}"],
                details={
                    "interruptionType": interruption.get("interruptionType"),
                    "primaryCause": interruption.get("primaryCause"),
                    "taskResumed": task_resumed,
                    "combatDamageSummary": damage if damage else None,
                },
            )
        )
        evidence.append("interruption lifecycle evidence present")
        if not task_resumed:
            warnings.append("Task interruption did not show a resume.")
    if task_resumed:
        phases.append(
            _phase(
                "resumed_cutting",
                confidence=0.9,
                evidence=["task resumed after interruption"],
                details={"interruptionType": interruption.get("interruptionType"), "primaryCause": interruption.get("primaryCause")},
            )
        )
        evidence.append("woodcutting resumed after interruption")

    if human:
        evidence.append("human click/camera profile available")

    loop_state = "unknown"
    current_phase = _phase_ref("unknown", "Unknown")
    next_phase = _phase_ref("unknown", "Unknown")

    phase_names = [str(item.get("phase")) for item in phases]
    if interruption.get("interruptionDetected") and not task_resumed:
        loop_state = "interrupted"
        current_phase = _phase_ref("interrupted", "Interrupted", "task stopped without resume")
        next_phase = _phase_ref("recover_or_resume_task", "Recover or resume task", "interruption did not show taskResumed=true")
    elif "deposit_complete" in phase_names:
        loop_state = "deposit_complete"
        current_phase = _phase_ref("deposit_complete", "Deposit complete", "logs deposited")
        next_phase = _phase_ref("route_to_woodcutting_area", "Route to woodcutting area", "inventory is ready after deposit")
    elif "routing_to_trees" in phase_names:
        loop_state = "routing_to_trees"
        current_phase = _phase_ref("routing_to_trees", "Routing to trees", "route ended at woodcutting area")
        next_phase = _phase_ref("resume_cutting", "Resume cutting", "arrived at woodcutting area")
    elif "routing_to_bank" in phase_names:
        loop_state = "routing_to_bank"
        current_phase = _phase_ref("routing_to_bank", "Routing to bank", "route ended at bank area")
        next_phase = _phase_ref("banking_deposit", "Banking deposit", "arrived at bank area")
    elif "banking" in phase_names:
        loop_state = "banking"
        current_phase = _phase_ref("banking", "Banking", "banking lifecycle is active")
        next_phase = _phase_ref("banking_deposit", "Banking deposit", "deposit has not completed yet")
    elif interruption.get("interruptionDetected") and task_resumed:
        loop_state = "resumed_cutting"
        current_phase = _phase_ref("resumed_cutting", "Resumed cutting", "task resumed after interruption")
        next_phase = _phase_ref("continue_current_phase", "Continue current phase", "interruption already recovered")
    elif inventory_full:
        loop_state = "inventory_full"
        current_phase = _phase_ref("inventory_full", "Inventory full", "woodcutting lifecycle ended full")
        next_phase = _phase_ref("route_to_bank", "Route to bank", "logs should be deposited")
    elif near_full and cutting_evidence:
        loop_state = "cutting"
        current_phase = _phase_ref("cutting", "Cutting", "inventory is near full")
        next_phase = _phase_ref("continue_cutting", "Continue cutting", "inventory is near full but not full")
    elif cutting_evidence:
        loop_state = "cutting"
        current_phase = _phase_ref("cutting", "Cutting", "woodcutting lifecycle is active")
        next_phase = _phase_ref("continue_cutting", "Continue cutting", "inventory is not full")
    elif "at_trees" in phase_names:
        loop_state = "at_trees"
        current_phase = _phase_ref("at_trees", "At trees", "woodcutting area evidence present")
        next_phase = _phase_ref("resume_cutting", "Resume cutting", "ready at woodcutting area")

    full_loop_phases = {"cutting", "inventory_full", "routing_to_bank", "banking", "deposit_complete", "routing_to_trees"}
    if full_loop_phases.issubset(set(phase_names)):
        loop_state = "complete"
        current_phase = _phase_ref("complete", "Full loop complete", "all loop phases detected")
        if "resumed_cutting" in phase_names:
            next_phase = _phase_ref("continue_current_phase", "Continue current phase", "loop completed and cutting resumed")
        else:
            next_phase = _phase_ref("resume_cutting", "Resume cutting", "loop completed and character returned to trees")

    status = _best_status(phases, warnings)
    if not phases:
        warnings.append("No woodcutting, banking, route, or interruption lifecycle evidence was available for loop classification.")
        missing.extend(["woodcutting_lifecycle", "banking_lifecycle", "traversal_lifecycle"])

    relevant_warnings: list[Any] = []
    if any(name in phase_names for name in ("cutting", "inventory_full", "inventory_near_full", "at_trees", "resumed_cutting", "interrupted")):
        relevant_warnings.extend(_list(wood.get("warnings")))
    if any(name in phase_names for name in ("banking", "deposit_complete")):
        relevant_warnings.extend(_list(bank.get("warnings")))
    if route.get("direction") != "unknown":
        relevant_warnings.extend(_list(route.get("warnings")))

    relevant_missing: list[Any] = list(missing)
    if any(name in phase_names for name in ("banking", "deposit_complete")):
        relevant_missing.extend(_list(bank.get("missingCapabilities")))

    result = {
        "schema": SCHEMA_VERSION,
        "generatedAtUtc": _utc_now(),
        "status": status,
        "loopState": loop_state if phases else "unknown",
        "confidence": _confidence(phases),
        "detectedPhases": phases,
        "currentPhase": current_phase,
        "nextExpectedPhase": next_phase,
        "evidence": _dedupe(evidence),
        "warnings": _dedupe(warnings + relevant_warnings),
        "missingCapabilities": _dedupe(relevant_missing),
        "woodcutting": wood,
        "banking": bank,
        "routes": route,
        "interruptions": {
            "status": interruption.get("status"),
            "interruptionDetected": bool(interruption.get("interruptionDetected")),
            "interruptionType": interruption.get("interruptionType"),
            "primaryCause": interruption.get("primaryCause"),
            "taskResumed": task_resumed,
            "combatDamageSummary": damage if damage else None,
        },
        "humanInput": {
            "profileAvailable": bool(human),
            "status": human.get("status"),
            "clicks": human.get("clicks"),
            "camera": human.get("camera"),
        } if human else {},
        "recordingPath": str(Path(recording_path)) if recording_path else None,
    }
    return result


def analyze_recording(path: str | Path, *, near_full_threshold: int = NEAR_FULL_FREE_SLOT_THRESHOLD) -> dict[str, Any]:
    recording = Path(path)
    if recording.is_file():
        recording = recording.parent
    summary = _read_json(recording / "summary.json")
    return analyze_data(summary=summary, recording_path=recording, near_full_threshold=near_full_threshold)


def analyze_context(context: dict[str, Any]) -> dict[str, Any]:
    return analyze_data(
        summary=context,
        woodcutting_lifecycle=_dict(_first(context.get("woodcutting_lifecycle"), context.get("woodcuttingLifecycle"))),
        banking_lifecycle=_dict(_first(context.get("banking_lifecycle"), context.get("bankingLifecycle"))),
        traversal_lifecycle=_dict(_first(context.get("traversal_lifecycle"), context.get("traversalLifecycle"))),
        route_template_comparison=_dict(_first(context.get("route_template_comparison"), context.get("routeTemplateComparison"))),
        route_monitor_status=_dict(_first(context.get("route_monitor"), context.get("routeMonitor"))),
        route_history_summary=_dict(_first(context.get("route_history"), context.get("routeHistory"))),
        interruption_lifecycle=_dict(_first(context.get("interruption_lifecycle"), context.get("interruptionLifecycle"))),
        combat_damage_summary=_dict(_first(context.get("combat_damage_summary"), context.get("combatDamageSummary"))),
        human_click_profile=_dict(_first(context.get("human_click_profile"), context.get("humanClickProfile"))),
    )


def compact_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    current = _dict(lifecycle.get("currentPhase"))
    next_phase = _dict(lifecycle.get("nextExpectedPhase"))
    wood = _dict(lifecycle.get("woodcutting"))
    bank = _dict(lifecycle.get("banking"))
    interruptions = _dict(lifecycle.get("interruptions"))
    route = _dict(lifecycle.get("routes"))
    route_legs = _list(route.get("routeLegs"))
    return {
        "schema": COMPACT_SCHEMA_VERSION,
        "status": lifecycle.get("status"),
        "loopState": lifecycle.get("loopState"),
        "currentPhase": current.get("phase") or lifecycle.get("loopState"),
        "currentPhaseLabel": current.get("label"),
        "nextExpectedPhase": next_phase.get("phase"),
        "nextExpectedPhaseLabel": next_phase.get("label"),
        "confidence": lifecycle.get("confidence"),
        "detectedPhaseCount": len(_list(lifecycle.get("detectedPhases"))),
        "detectedPhases": [str(_dict(item).get("phase")) for item in _list(lifecycle.get("detectedPhases"))],
        "inventoryFull": bool(wood.get("inventoryFull")),
        "nearFull": bool(wood.get("nearFull")),
        "normalLogsGained": wood.get("normalLogsGained"),
        "freeSlotsEnd": wood.get("freeSlotsEnd"),
        "depositComplete": bool(bank.get("depositDetected")),
        "depositedItems": bank.get("depositedItems") or [],
        "depositedLogs": _has_logs(_list(bank.get("depositedItems"))),
        "routeDirection": route.get("direction"),
        "routePassed": route.get("passed"),
        "routeLegCount": len(route_legs),
        "routeLegs": route_legs,
        "interruptionDetected": bool(interruptions.get("interruptionDetected")),
        "interruptionType": interruptions.get("interruptionType"),
        "taskResumed": bool(interruptions.get("taskResumed")),
        "warnings": _list(lifecycle.get("warnings"))[:8],
        "missingCapabilities": _list(lifecycle.get("missingCapabilities"))[:8],
    }


def write_lifecycle(lifecycle: dict[str, Any], target: str | Path, *, pretty: bool = True) -> Path:
    path = Path(target)
    out_path = path if path.suffix.lower() == ".json" else path / "woodcutting_loop_lifecycle.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(lifecycle, indent=2 if pretty else None, default=str), encoding="utf-8")
    return out_path


def summary_text(lifecycle: dict[str, Any]) -> str:
    compact = compact_lifecycle(lifecycle)
    lines = [
        f"Woodcutting loop: {compact.get('status')} ({compact.get('loopState')})",
        f"Current phase: {compact.get('currentPhase')}",
        f"Next expected phase: {compact.get('nextExpectedPhase')}",
        f"Confidence: {compact.get('confidence')}",
    ]
    warnings = compact.get("warnings") or []
    if warnings:
        lines.append("Warnings: " + "; ".join(str(item) for item in warnings[:3]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze a task-level woodcutting loop lifecycle from existing recording artifacts.")
    parser.add_argument("recording", help="Recording folder or an artifact path inside a recording folder.")
    parser.add_argument("--json", action="store_true", help="Print pretty JSON output.")
    parser.add_argument("--out", help="Optional output path or directory. Defaults to woodcutting_loop_lifecycle.json in the recording folder.")
    args = parser.parse_args(argv)
    lifecycle = analyze_recording(args.recording)
    target = args.out or (Path(args.recording).parent if Path(args.recording).is_file() else Path(args.recording))
    write_lifecycle(lifecycle, target, pretty=True)
    print(json.dumps(lifecycle, indent=2 if args.json else None, default=str) if args.json else summary_text(lifecycle))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
