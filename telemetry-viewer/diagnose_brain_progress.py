from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import brain_core
import diagnose_inventory_slots
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "brain_progress_diagnostic.v1"
DAILY_NOISE_WARNINGS = {
    "no frame path in live baseline.",
    "no frame path in live baseline",
}
LIVE_DIR = Path("interaction_geometry") / "live"


def safe_load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def expand_path(path: str | None) -> Path | None:
    if not path:
        return None
    return brain_core.expand_state_path(path)


def fetch_json(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def daemon_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def resolve_session(args: argparse.Namespace) -> Path:
    if args.session:
        session = expand_path(args.session)
        if session is None or not session.exists():
            raise RuntimeError(f"Session does not exist: {args.session}")
        return session.resolve()
    if not args.latest_session:
        raise RuntimeError("Pass --session or --latest-session.")
    session = find_newest_session(get_sessions_dir(args.sessions_dir))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
    return session.resolve()


def resource_definition(task: str, resource_id: str | None) -> dict:
    registry = brain_core.load_task_resources()
    tasks = registry.get("tasks") if isinstance(registry.get("tasks"), dict) else {}
    task_def = tasks.get(task) if isinstance(tasks.get(task), dict) else {}
    resources = task_def.get("resources") if isinstance(task_def.get("resources"), dict) else {}
    groups = task_def.get("resourceGroups") if isinstance(task_def.get("resourceGroups"), dict) else {}
    resource_id = resource_id or task_def.get("defaultResourceGroup") or "woodcutting_logs"
    if resource_id in groups:
        return {"id": resource_id, **groups[resource_id]}
    if resource_id in resources:
        return {"id": resource_id, **resources[resource_id]}
    return brain_core.task_resource_group(task, resource_id, registry)


def load_live_inventory(session: Path) -> tuple[dict, str, dict, dict, dict]:
    live_dir = session / LIVE_DIR
    baseline = safe_load_json(live_dir / "live_baseline_state.json")
    activity = safe_load_json(live_dir / "live_activity_state.json")
    watch_values = safe_load_json(live_dir / "live_watch_values.json")
    inventory, source = diagnose_inventory_slots.first_inventory_from(
        ("live_activity_state.json", activity),
        ("live_baseline_state.json", baseline),
        ("live_watch_values.json", watch_values),
    )
    return inventory, source, baseline, activity, watch_values


def resource_name_for_item(item_id: int | None) -> str | None:
    if item_id is None:
        return None
    registry = brain_core.load_task_resources()
    woodcutting = ((registry.get("tasks") or {}).get("woodcutting") or {})
    resources = woodcutting.get("resources") if isinstance(woodcutting.get("resources"), dict) else {}
    for resource_id, definition in resources.items():
        if item_id in {brain_core.as_int(value) for value in definition.get("itemIds", [])}:
            return str(resource_id)
    return None


def current_matched_slot_table(inventory: dict, item_ids: list[int]) -> list[dict]:
    target_ids = {int(item_id) for item_id in item_ids}
    items = brain_core.inventory_items_for_progress(inventory) or []
    rows: list[dict] = []
    for item in items:
        item_id = brain_core.inventory_item_id(item)
        counted = item_id in target_ids
        rows.append(
            {
                "slot": brain_core.inventory_item_slot(item),
                "itemId": item_id,
                "quantity": brain_core.inventory_item_quantity(item),
                "resourceName": resource_name_for_item(item_id),
                "counted": counted,
            }
        )
    rows.sort(key=lambda row: (row.get("slot") is None, row.get("slot"), row.get("itemId")))
    return rows


def state_previous_items(state: dict) -> list[dict] | None:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    value = progress.get("previousInventoryItems")
    if isinstance(value, list):
        return value
    value = state.get("previousInventoryItems")
    if isinstance(value, list):
        return value
    value = brain_core.safe_get(state, "currentInventory.items")
    return value if isinstance(value, list) else None


def state_baseline_items(state: dict) -> list[dict] | None:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    value = progress.get("baselineInventoryItems")
    if isinstance(value, list):
        return value
    value = state.get("baselineInventoryItems")
    return value if isinstance(value, list) else None


def explain(payload: dict) -> list[str]:
    explanations: list[str] = []
    count = payload.get("currentCount") or {}
    progress = payload.get("progressEstimate") or {}
    warnings = filter_daily_noise_warnings(progress.get("warnings") or [])
    if payload.get("invalidMatchedSlots"):
        explanations.append("invalid matched slot without itemId; slot was not counted")
    if payload.get("oldCumulativeFieldsIgnored") or progress.get("oldCumulativeFieldsIgnored"):
        explanations.append("old cumulative progress history ignored")
    if progress.get("duplicateSnapshot"):
        explanations.append("duplicate snapshot did not change held-vs-baseline progress")
    if progress.get("progressRetainedFromPrevious"):
        explanations.append(progress.get("retainedReason") or "invalid snapshot retained previous progress")
    if progress.get("currentInventorySignature") is None:
        explanations.append("daemon did not expose current inventory signature")
    if progress.get("source") in {"baseline_pending", "inventory_snapshot_invalid"}:
        explanations.append(progress.get("reason") or "invalid partial progress state")
    if progress.get("progressStateRepaired"):
        explanations.append(progress.get("repairReason") or "invalid progress state repaired")
    if progress.get("source") == "baseline_initialized":
        explanations.append("baseline initialized; existing held logs did not count as gained")
    if progress.get("source") == "inventory_snapshot_held_vs_baseline":
        explanations.append("daily gained since start is monotonic held-vs-baseline progress until reset")
    if any("resourceCounts disagreed" in str(warning) for warning in warnings):
        explanations.append("resourceCounts disagreed with inventory item snapshot; inventory.items won")
    if count.get("known") and progress.get("currentHeldCount") != count.get("count"):
        explanations.append("slot is matched but not counted")
    state = payload.get("brainState") or {}
    if state.get("resourceGroup") and state.get("resourceGroup") != payload.get("resourceGroup"):
        explanations.append("resource group mismatch")
    if count.get("known") and not explanations:
        explanations.append("current matched count and progress accumulator agree")
    if not count.get("known"):
        explanations.append(count.get("missingReason") or "inventory resource count unknown")
    return explanations


def strict_check(payload: dict) -> dict:
    failures: list[str] = []
    progress = payload.get("progressEstimate") if isinstance(payload.get("progressEstimate"), dict) else {}
    state = payload.get("brainState") if isinstance(payload.get("brainState"), dict) else {}
    current_count = payload.get("currentCount") if isinstance(payload.get("currentCount"), dict) else {}
    invalid_slots = payload.get("invalidMatchedSlots") if isinstance(payload.get("invalidMatchedSlots"), list) else []

    if invalid_slots:
        failures.append("matched slot has counted=true without a real itemId")
    if state.get("baselineEstablished") is True and state.get("baselineHeldCount") is None:
        failures.append("baselineEstablished is true but baselineHeldCount is missing")

    source = str(progress.get("source") or "")
    if source in {"inventory_snapshot_invalid", "baseline_pending"} and progress.get("progressUpdateApplied"):
        failures.append("invalid or pending snapshot reported a progress update")
    if source in {"inventory_snapshot_invalid", "baseline_pending"} and progress.get("displayedGoalProgress") not in (None, 0) and not progress.get("progressRetainedFromPrevious"):
        failures.append("invalid snapshot changed progress instead of retaining previous progress")
    if (
        progress.get("currentSnapshotValid") is False
        and state.get("baselineEstablished") is True
        and progress.get("lastValidProgressTick") is not None
        and not progress.get("progressRetainedFromPrevious")
    ):
        failures.append("invalid snapshot did not retain the previous valid progress result")
    if progress.get("currentSnapshotValid") is False and progress.get("progressDropReason") == "valid_inventory_count_decreased":
        failures.append("invalid snapshot was labelled as a valid inventory count decrease")

    if current_count.get("source") in {"inventory_snapshot_items", "inventory.items"} and current_count.get("count") and not current_count.get("matchedSlotDetails"):
        failures.append("item-list inventory count is known but matched slot details are missing")
    if (
        progress.get("currentSnapshotValid") is True
        and not progress.get("progressRetainedFromPrevious")
        and current_count.get("known") is True
        and progress.get("currentHeldCount") != current_count.get("count")
    ):
        failures.append("daemon progress current held count disagrees with diagnostic inventory count")
    if progress.get("source") == "unchanged_snapshot" and progress.get("progressUpdateApplied"):
        failures.append("unchanged snapshot reported a progress update")

    return {
        "strict": True,
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
    }


def filter_daily_noise_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if str(warning).strip() not in DAILY_NOISE_WARNINGS]


def diagnose(session: Path, state_file: str | None, task: str, goal_count: int | None, resource_id: str) -> dict:
    inventory_raw, inventory_source, baseline, activity, watch_values = load_live_inventory(session)
    inventory = brain_core.inventory_summary({"inventory": inventory_raw})
    resource = resource_definition(task, resource_id)
    item_ids = [item_id for item_id in resource.get("itemIds", []) if brain_core.as_int(item_id) is not None]
    state_path = expand_path(state_file)
    state_present = bool(state_path and state_path.exists())
    state = brain_core.load_state(str(state_path), task, goal_count) if state_path else brain_core.default_state(task, goal_count)
    current_count = brain_core.count_inventory_items(inventory, item_ids)
    current_signature = brain_core.inventory_signature_for_progress(inventory, brain_core.inventory_items_for_progress(inventory))
    current_tick = activity.get("latestTick") or baseline.get("latestTick") or watch_values.get("latestTick")
    progress = brain_core.estimate_progress(
        state,
        {
            "schema": "context_response.v1",
            "latestTick": current_tick,
            "recentInventoryDeltas": inventory.get("recentItemDeltas") or inventory_raw.get("recentItemDeltas"),
        },
        inventory,
        [],
        goal_count,
        task=task,
    )
    state_progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    invalid_matched_slots = [
        row
        for row in current_count.get("matchedSlotDetails") or []
        if isinstance(row, dict) and row.get("counted") and row.get("itemId") is None
    ]
    old_cumulative_ignored = any(
        brain_core.as_int(value)
        for value in (
            state_progress.get("observedGained"),
            state_progress.get("observedRemoved"),
            state_progress.get("cumulativeGained"),
            state_progress.get("cumulativeLostOrRemoved"),
            state.get("observedGained"),
            state.get("observedRemoved"),
        )
    )
    payload = {
        "schema": SCHEMA,
        "sessionPath": str(session),
        "stateFile": str(state_path) if state_path else None,
        "stateFileExists": state_present,
        "latestTick": current_tick,
        "task": task,
        "resourceGroup": resource.get("id") or resource_id,
        "itemIdsCounted": item_ids,
        "itemIdLabels": {
            "1511": "logs",
            "1521": "oak logs",
            "1519": "willow logs",
            "1517": "maple logs",
            "1515": "yew logs",
            "1513": "magic logs",
        },
        "inventorySource": inventory_source,
        "warnings": [],
        "currentMatchedSlots": current_matched_slot_table(inventory, item_ids),
        "currentCount": {
            "known": current_count.get("known"),
            "count": current_count.get("count"),
            "source": current_count.get("source"),
            "matchedSlots": current_count.get("matchedSlots")
            or sorted(
                slot
                for slot in (brain_core.as_int(item.get("slot")) for item in current_count.get("matchedItems") or [])
                if slot is not None
            ),
            "matchedItems": current_count.get("matchedItems") or [],
            "matchedSlotDetails": current_count.get("matchedSlotDetails") or [],
            "warnings": current_count.get("warnings") or [],
            "missingReason": current_count.get("missingReason"),
        },
        "invalidMatchedSlots": invalid_matched_slots,
        "oldCumulativeFieldsIgnored": bool(old_cumulative_ignored),
        "brainState": {
            "schema": state.get("schema"),
            "stateVersion": state.get("stateVersion"),
            "resourceGroup": state.get("goalResourceGroup") or state_progress.get("resourceGroup"),
            "baselineEstablished": state_progress.get("baselineEstablished") or state.get("baselineEstablished"),
            "baselineHeldCount": state_progress.get("baselineHeldCount") or (state.get("resourceBaselineCounts") or {}).get(resource.get("id") or resource_id),
            "currentHeldCount": state_progress.get("currentHeldCount") or (state.get("resourceCurrentCounts") or {}).get(resource.get("id") or resource_id),
            "previousResourceCount": state_progress.get("previousResourceCount") or state.get("previousResourceCount"),
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": state_progress.get("displayedGoalProgress") if state_progress.get("displayedGoalProgress") is not None else state.get("displayedGoalProgress"),
            "hasValidPostBaselineProgressHistory": False,
            "cumulativeGained": None,
            "cumulativeLostOrRemoved": None,
            "goalComplete": state.get("goalComplete"),
            "lastSeenTick": state.get("lastSeenTick"),
            "lastProcessedInventorySignature": state_progress.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature"),
            "lastProcessedInventoryTick": state_progress.get("lastProcessedInventoryTick") or state.get("lastProcessedInventoryTick"),
            "currentInventorySignature": current_signature,
            "duplicateSnapshot": bool(current_signature and (state_progress.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature")) == current_signature),
            "progressStateRepaired": state_progress.get("progressStateRepaired"),
            "repairReason": state_progress.get("repairReason"),
            "invariantViolations": state_progress.get("invariantViolations") or [],
            "resetApplied": False,
            "baselineInitialized": state_progress.get("progressSource") == "baseline_initialized",
            "previousInventorySignature": state_progress.get("currentInventorySignature") or state.get("lastInventorySignature"),
            "previousInventorySnapshotAvailable": None,
            "baselineInventorySnapshotAvailable": None,
        },
        "baseline": {"matchedSlots": [], "heldCount": state_progress.get("baselineHeldCount")},
        "previous": {"matchedSlots": [], "heldCount": None},
        "slotDiff": {"available": False},
        "progressEstimate": {
            "baselineHeldCount": progress.get("baselineResourceCount"),
            "currentHeldCount": progress.get("currentHeldResourceCount"),
            "netChangeFromBaseline": progress.get("netChangeFromBaseline"),
            "cumulativeGained": None,
            "cumulativeLostOrRemoved": None,
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": progress.get("displayedGoalProgress"),
            "hasValidPostBaselineProgressHistory": False,
            "gainedSinceStart": progress.get("gainedSinceBaseline"),
            "goalCount": progress.get("goalCount"),
            "goalComplete": progress.get("complete"),
            "source": progress.get("progressSource"),
            "baselineEstablished": progress.get("baselineEstablished"),
            "previousResourceCount": progress.get("previousResourceCount"),
            "lastProcessedInventorySignature": progress.get("lastProcessedInventorySignature"),
            "currentInventorySignature": progress.get("currentInventorySignature"),
            "lastProcessedInventoryTick": progress.get("lastProcessedInventoryTick"),
            "currentInventoryTick": progress.get("currentInventoryTick"),
            "duplicateSnapshot": progress.get("duplicateSnapshot"),
            "progressUpdateApplied": progress.get("progressUpdateApplied"),
            "progressUpdateReason": progress.get("progressUpdateReason"),
            "progressStateRepaired": progress.get("progressStateRepaired"),
            "repairReason": progress.get("repairReason"),
            "invariantViolations": progress.get("invariantViolations") or [],
            "resetApplied": False,
            "baselineInitialized": progress.get("progressSource") == "baseline_initialized",
            "matchedSlots": progress.get("matchedSlots"),
            "matchedItemIds": progress.get("matchedItemIds"),
            "warnings": progress.get("warnings") or [],
            "reason": progress.get("reason"),
            "progressRetainedFromPrevious": progress.get("progressRetainedFromPrevious"),
            "retainedReason": progress.get("retainedReason"),
            "retainedAgeTicks": progress.get("retainedAgeTicks"),
            "progressDropReason": progress.get("progressDropReason"),
            "progressHeldReason": progress.get("progressHeldReason"),
            "progressInvalidSnapshotCount": progress.get("progressInvalidSnapshotCount"),
            "progressRetainedPreviousCount": progress.get("progressRetainedPreviousCount"),
            "progressFlickerPreventedCount": progress.get("progressFlickerPreventedCount"),
            "lastProgressInvalidReason": progress.get("lastProgressInvalidReason"),
            "lastProgressRetainedTick": progress.get("lastProgressRetainedTick"),
            "lastValidProgressTick": progress.get("lastValidProgressTick"),
            "lastValidInventorySignature": progress.get("lastValidInventorySignature"),
            "oldCumulativeFieldsIgnored": bool(old_cumulative_ignored),
        },
    }
    if invalid_matched_slots:
        payload["warnings"].append("invalid matched slot without itemId")
    if inventory_source == "none":
        payload["warnings"].append("live debug files are disabled or unavailable; use --from-daemon or enable --write-debug-live-files")
    payload["explanation"] = explain(payload)
    return payload


def diagnose_from_daemon(base_url: str, task: str, goal_count: int | None, resource_id: str) -> dict:
    status = fetch_json(daemon_url(base_url, "/status"))
    decision = status.get("brain") if isinstance(status.get("brain"), dict) else {}
    if not decision:
        query = urllib.parse.urlencode({"task": task})
        try:
            decision = fetch_json(daemon_url(base_url, f"/brain?{query}"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            decision = {}
    progress = decision.get("progress") if isinstance(decision.get("progress"), dict) else {}
    goal_progress = decision.get("goalProgress") if isinstance(decision.get("goalProgress"), dict) else {}
    context = decision.get("currentContextSummary") if isinstance(decision.get("currentContextSummary"), dict) else {}
    inventory = context.get("inventory") if isinstance(context.get("inventory"), dict) else {}
    resource = resource_definition(task, resource_id)
    item_ids = [item_id for item_id in resource.get("itemIds", []) if brain_core.as_int(item_id) is not None]
    current_count = brain_core.count_inventory_items(inventory, item_ids)
    matched_details = (
        progress.get("matchedSlotDetails")
        or goal_progress.get("matchedSlotDetails")
        or current_count.get("matchedSlotDetails")
        or []
    )
    if not matched_details:
        matched_details = [
            {
                "slot": slot,
                "itemId": None,
                "quantity": None,
                "counted": False,
                "source": "matchedSlotsSummary",
                "summaryDerived": True,
            }
            for slot in (progress.get("matchedSlots") or goal_progress.get("matchedSlots") or current_count.get("matchedSlots") or [])
        ]
    invalid_matched_slots = [
        dict(row)
        for row in matched_details
        if isinstance(row, dict) and row.get("counted") and row.get("itemId") is None
    ]
    old_cumulative_ignored = any(
        brain_core.as_int(value)
        for value in (
            goal_progress.get("observedGained"),
            goal_progress.get("observedRemoved"),
            goal_progress.get("cumulativeGained"),
            goal_progress.get("cumulativeLostOrRemoved"),
            progress.get("observedGained"),
            progress.get("observedRemoved"),
        )
    )
    payload = {
        "schema": SCHEMA,
        "source": "daemon",
        "daemonUrl": base_url,
        "sessionPath": status.get("sessionPath"),
        "latestTick": status.get("latestTick") or decision.get("latestTick"),
        "inputSourceActive": status.get("inputSourceActive"),
        "liveCoreDaemonActive": status.get("liveCoreDaemonActive"),
        "writeDebugLiveFiles": status.get("writeDebugLiveFiles"),
        "task": task,
        "resourceGroup": resource.get("id") or resource_id,
        "itemIdsCounted": item_ids,
        "currentMatchedSlots": [
            {
                "slot": row.get("slot"),
                "itemId": row.get("itemId"),
                "quantity": row.get("quantity"),
                "resourceName": resource.get("id") or resource_id,
                "counted": bool(row.get("counted") and row.get("itemId") is not None),
                "source": row.get("source"),
                "summaryDerived": bool(row.get("summaryDerived")),
            }
            for row in matched_details
            if isinstance(row, dict)
        ],
        "currentCount": {
            "known": current_count.get("known"),
            "count": current_count.get("count"),
            "source": current_count.get("source"),
            "matchedSlots": progress.get("matchedSlots") or current_count.get("matchedSlots") or [],
            "matchedSlotDetails": matched_details,
            "warnings": current_count.get("warnings") or [],
            "missingReason": current_count.get("missingReason"),
        },
        "invalidMatchedSlots": invalid_matched_slots,
        "oldCumulativeFieldsIgnored": bool(old_cumulative_ignored),
        "brainState": {
            "phase": decision.get("phase"),
            "baselineEstablished": goal_progress.get("baselineEstablished"),
            "baselineHeldCount": goal_progress.get("baselineHeldCount"),
            "currentHeldCount": goal_progress.get("currentHeldCount"),
            "previousResourceCount": goal_progress.get("previousResourceCount"),
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": goal_progress.get("displayedGoalProgress"),
            "hasValidPostBaselineProgressHistory": False,
            "goalComplete": decision.get("goalComplete"),
            "lastProcessedInventorySignature": goal_progress.get("lastProcessedInventorySignature"),
            "lastProcessedInventoryTick": goal_progress.get("lastProcessedInventoryTick"),
            "currentInventorySignature": goal_progress.get("currentInventorySignature"),
            "previousInventorySnapshotAvailable": goal_progress.get("previousInventorySnapshotAvailable"),
            "duplicateSnapshot": goal_progress.get("duplicateSnapshot"),
            "progressStateRepaired": goal_progress.get("progressStateRepaired"),
            "repairReason": goal_progress.get("repairReason"),
            "invariantViolations": goal_progress.get("invariantViolations") or [],
            "resetApplied": status.get("brainResetApplied"),
            "baselineInitialized": goal_progress.get("source") == "baseline_initialized",
            "currentSnapshotValid": goal_progress.get("currentSnapshotValid"),
            "snapshotValidityMissing": goal_progress.get("snapshotValidityMissing") or [],
            "progressRetainedFromPrevious": goal_progress.get("progressRetainedFromPrevious"),
            "retainedReason": goal_progress.get("retainedReason"),
            "retainedAgeTicks": goal_progress.get("retainedAgeTicks"),
            "progressDropReason": goal_progress.get("progressDropReason"),
            "progressHeldReason": goal_progress.get("progressHeldReason"),
            "progressInvalidSnapshotCount": goal_progress.get("progressInvalidSnapshotCount"),
            "progressRetainedPreviousCount": goal_progress.get("progressRetainedPreviousCount"),
            "progressFlickerPreventedCount": goal_progress.get("progressFlickerPreventedCount"),
            "lastProgressInvalidReason": goal_progress.get("lastProgressInvalidReason"),
            "lastProgressRetainedTick": goal_progress.get("lastProgressRetainedTick"),
            "lastValidProgressTick": goal_progress.get("lastValidProgressTick"),
            "lastValidInventorySignature": goal_progress.get("lastValidInventorySignature"),
        },
        "progressEstimate": {
            "baselineHeldCount": goal_progress.get("baselineHeldCount"),
            "currentHeldCount": goal_progress.get("currentHeldCount"),
            "previousResourceCount": goal_progress.get("previousResourceCount"),
            "netChangeFromBaseline": goal_progress.get("netChangeFromBaseline"),
            "observedGained": None,
            "observedRemoved": None,
            "displayedGoalProgress": goal_progress.get("displayedGoalProgress"),
            "hasValidPostBaselineProgressHistory": False,
            "gainedSinceStart": goal_progress.get("gainedSinceStart"),
            "goalCount": goal_progress.get("goalCount") if goal_progress.get("goalCount") is not None else goal_count,
            "goalComplete": goal_progress.get("complete"),
            "source": goal_progress.get("source"),
            "baselineEstablished": goal_progress.get("baselineEstablished"),
            "currentInventorySignature": goal_progress.get("currentInventorySignature"),
            "previousInventorySnapshotAvailable": goal_progress.get("previousInventorySnapshotAvailable"),
            "currentSnapshotValid": goal_progress.get("currentSnapshotValid"),
            "snapshotValidityMissing": goal_progress.get("snapshotValidityMissing") or [],
            "lastProcessedInventorySignature": goal_progress.get("lastProcessedInventorySignature"),
            "lastProcessedInventoryTick": goal_progress.get("lastProcessedInventoryTick"),
            "duplicateSnapshot": goal_progress.get("duplicateSnapshot"),
            "progressUpdateApplied": goal_progress.get("progressUpdateApplied"),
            "progressUpdateReason": goal_progress.get("progressUpdateReason"),
            "progressStateRepaired": goal_progress.get("progressStateRepaired"),
            "repairReason": goal_progress.get("repairReason"),
            "invariantViolations": goal_progress.get("invariantViolations") or [],
            "resetApplied": status.get("brainResetApplied"),
            "baselineInitialized": goal_progress.get("source") == "baseline_initialized",
            "matchedSlots": goal_progress.get("matchedSlots") or [],
            "matchedItemIds": goal_progress.get("matchedItemIds") or [],
            "warnings": filter_daily_noise_warnings(goal_progress.get("warnings") or decision.get("warnings") or []),
            "reason": goal_progress.get("note"),
            "progressRetainedFromPrevious": goal_progress.get("progressRetainedFromPrevious"),
            "retainedReason": goal_progress.get("retainedReason"),
            "retainedAgeTicks": goal_progress.get("retainedAgeTicks"),
            "progressDropReason": goal_progress.get("progressDropReason"),
            "progressHeldReason": goal_progress.get("progressHeldReason"),
            "progressInvalidSnapshotCount": goal_progress.get("progressInvalidSnapshotCount"),
            "progressRetainedPreviousCount": goal_progress.get("progressRetainedPreviousCount"),
            "progressFlickerPreventedCount": goal_progress.get("progressFlickerPreventedCount"),
            "lastProgressInvalidReason": goal_progress.get("lastProgressInvalidReason"),
            "lastProgressRetainedTick": goal_progress.get("lastProgressRetainedTick"),
            "lastValidProgressTick": goal_progress.get("lastValidProgressTick"),
            "lastValidInventorySignature": goal_progress.get("lastValidInventorySignature"),
            "oldCumulativeFieldsIgnored": bool(old_cumulative_ignored),
        },
        "warnings": [],
    }
    if invalid_matched_slots:
        payload["warnings"].append("invalid matched slot without itemId")
    if old_cumulative_ignored:
        payload["warnings"].append("old cumulative progress history ignored; daily progress uses held-vs-baseline snapshot count")
    if not decision:
        payload["warnings"].append("daemon did not expose brain progress; start live_core_daemon with --human-dashboard or query /brain first")
    payload["explanation"] = explain(payload)
    return payload


def format_human(payload: dict) -> str:
    lines = [
        "BRAIN PROGRESS DIAGNOSTIC",
        "",
        f"Session: {payload.get('sessionPath')}",
        f"State file: {payload.get('stateFile') or 'not supplied'}",
        f"State file exists: {payload.get('stateFileExists')}",
        f"Latest tick: {payload.get('latestTick')}",
        f"Resource group: {payload.get('resourceGroup')}",
        f"Item IDs counted: {', '.join(str(item_id) for item_id in payload.get('itemIdsCounted') or [])}",
        "",
        "Current matched slots:",
    ]
    rows = [row for row in payload.get("currentMatchedSlots") or [] if row.get("counted")]
    if rows:
        for row in rows:
            lines.append(
                f"  slot {row.get('slot')}: itemId={row.get('itemId')} qty={row.get('quantity')} resource={row.get('resourceName')} counted={row.get('counted')}"
            )
    else:
        lines.append("  none")
    progress = payload.get("progressEstimate") or {}
    state = payload.get("brainState") or {}
    lines.extend(
        [
            "",
            "Brain state:",
            f"  baseline established: {state.get('baselineEstablished')}",
            f"  baseline held: {state.get('baselineHeldCount')}",
            f"  previous resource count: {state.get('previousResourceCount')}",
            f"  displayed goal progress: {state.get('displayedGoalProgress')}",
            f"  old cumulative fields ignored: {payload.get('oldCumulativeFieldsIgnored')}",
            f"  progress state repaired: {state.get('progressStateRepaired')}",
            f"  repair reason: {state.get('repairReason')}",
            f"  last processed signature: {state.get('lastProcessedInventorySignature')}",
            f"  current signature: {state.get('currentInventorySignature')}",
            f"  last processed tick: {state.get('lastProcessedInventoryTick')}",
            f"  previous snapshot: {state.get('previousInventorySnapshotAvailable')}",
            "",
            "Progress estimate:",
            f"  current held: {progress.get('currentHeldCount')}",
            f"  baseline established: {progress.get('baselineEstablished')}",
            f"  baseline held: {progress.get('baselineHeldCount')}",
            f"  previous resource count: {progress.get('previousResourceCount')}",
            f"  net change from baseline: {progress.get('netChangeFromBaseline')}",
            f"  displayed goal progress: {progress.get('displayedGoalProgress')}",
            f"  progress state repaired: {progress.get('progressStateRepaired')}",
            f"  repair reason: {progress.get('repairReason')}",
            f"  gained since start: {progress.get('gainedSinceStart')} / {progress.get('goalCount')}",
            f"  goal complete: {progress.get('goalComplete')}",
            f"  source: {progress.get('source')}",
            f"  duplicate snapshot: {progress.get('duplicateSnapshot')}",
            f"  progress update applied: {progress.get('progressUpdateApplied')}",
            f"  reason: {progress.get('progressUpdateReason')}",
            f"  retained previous progress: {progress.get('progressRetainedFromPrevious')}",
            f"  retained reason: {progress.get('retainedReason')}",
            f"  flicker prevented count: {progress.get('progressFlickerPreventedCount')}",
            f"  matched slots: {', '.join(str(slot) for slot in progress.get('matchedSlots') or []) or 'none'}",
        ]
    )
    strict = payload.get("strictCheck") if isinstance(payload.get("strictCheck"), dict) else {}
    if strict:
        lines.extend(["", f"Strict check: {strict.get('status')}"])
        for failure in strict.get("failures") or []:
            lines.append(f"  {failure}")
    warnings = filter_daily_noise_warnings(progress.get("warnings") or [])
    diagnostic_warnings = filter_daily_noise_warnings(payload.get("warnings") if isinstance(payload.get("warnings"), list) else [])
    invalid = payload.get("invalidMatchedSlots") if isinstance(payload.get("invalidMatchedSlots"), list) else []
    lines.extend(["", "Warnings:"])
    if warnings or diagnostic_warnings or invalid:
        for warning in diagnostic_warnings + warnings:
            lines.append(f"  {warning}")
        for row in invalid:
            lines.append(f"  invalid matched slot: slot {row.get('slot')} itemId={row.get('itemId')} counted={row.get('counted')}")
    else:
        lines.append("  none")
    lines.extend(["", "Explanation:"])
    for item in payload.get("explanation") or []:
        lines.append(f"  {item}")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose read-only brain woodcutting resource progress.")
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when using --latest-session.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest available telemetry session.")
    parser.add_argument("--state-file", help="Optional brain_state.v1 file.")
    parser.add_argument("--task", default="woodcutting", help="Task name. Default: woodcutting.")
    parser.add_argument("--goal-count", type=int, default=None, help="Goal count, e.g. logs to collect.")
    parser.add_argument("--resource", default="woodcutting_logs", help="Resource/group id. Default: woodcutting_logs.")
    parser.add_argument("--from-daemon", action="store_true", help="Read progress from live_core_daemon memory instead of rolling live files.")
    parser.add_argument("--daemon-url", default="http://127.0.0.1:8890", help="live_core_daemon base URL for --from-daemon.")
    parser.add_argument("--strict", action="store_true", help="Fail if daemon progress state violates daily invariants.")
    parser.add_argument("--json", action="store_true", help="Print JSON diagnostic.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.from_daemon:
            payload = diagnose_from_daemon(args.daemon_url, args.task, args.goal_count, args.resource)
        else:
            session = resolve_session(args)
            payload = diagnose(session, args.state_file, args.task, args.goal_count, args.resource)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if args.strict:
        payload["strictCheck"] = strict_check(payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload), end="")
    if args.strict and payload.get("strictCheck", {}).get("status") == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
