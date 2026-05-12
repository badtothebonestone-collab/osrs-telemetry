from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


SCHEMA = "resource_progress_state.v1"
OLD_CUMULATIVE_HISTORY_WARNING = "old cumulative progress history ignored; daily progress uses held-vs-baseline snapshot count"
INVALID_MATCHED_SLOT_WARNING = "invalid matched slot without itemId"
RESOURCE_COUNTS_FALLBACK_WARNING = "inventory item snapshot missing; using resourceCounts for held count only"
PROGRESS_REPAIR_WARNING = OLD_CUMULATIVE_HISTORY_WARNING
PARTIAL_PROGRESS_REPAIR_WARNING = OLD_CUMULATIVE_HISTORY_WARNING
OLD_PROGRESS_HISTORY_WARNING = OLD_CUMULATIVE_HISTORY_WARNING
BALANCED_CHURN_REPAIR_WARNING = OLD_CUMULATIVE_HISTORY_WARNING
BASELINE_DRIFT_REPAIR_WARNING = OLD_CUMULATIVE_HISTORY_WARNING


def as_int(value: Any) -> int | None:
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


def item_id(item: dict) -> int | None:
    for key in ("itemId", "id", "rawId"):
        value = as_int(item.get(key))
        if value is not None and value > 0:
            return value
    return None


def item_slot(item: dict) -> int | None:
    return as_int(item.get("slot"))


def item_quantity(item: dict) -> int:
    quantity = as_int(item.get("quantity"))
    if quantity is None:
        quantity = as_int(item.get("qty"))
    if quantity is None:
        return 1
    return max(0, quantity)


@dataclass(frozen=True)
class ResourceDefinition:
    id: str
    item_ids: tuple[int, ...]
    display_name: str = "resources"


@dataclass(frozen=True)
class InventorySnapshot:
    session_path: str | None
    latest_tick: int | None
    inventory_signature: str | None
    inventory_slot_count: int | None
    items: tuple[dict, ...] | None = None
    resource_counts: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResourceProgressState:
    schema: str = SCHEMA
    task: str = "woodcutting"
    session_path: str | None = None
    goal_count: int | None = None
    resource_group: str = "woodcutting_logs"
    baseline_established: bool = False
    baseline_held_count: int | None = None
    baseline_tick: int | None = None
    baseline_inventory_signature: str | None = None
    last_inventory_signature: str | None = None
    last_inventory_tick: int | None = None
    current_held_count: int | None = None
    displayed_goal_progress: int = 0
    goal_complete: bool = False
    progress_source: str = "baseline_pending"
    matched_slots: list[dict] = field(default_factory=list)
    repair_warnings: list[str] = field(default_factory=list)
    progress_invalid_snapshot_count: int = 0
    progress_retained_previous_count: int = 0
    progress_flicker_prevented_count: int = 0
    last_progress_invalid_reason: str | None = None
    last_progress_retained_tick: int | None = None
    last_valid_progress_tick: int | None = None
    last_valid_inventory_signature: str | None = None
    progress_retained_from_previous: bool = False
    retained_reason: str | None = None
    retained_age_ticks: int | None = None
    progress_drop_reason: str | None = None
    progress_held_reason: str | None = None


@dataclass
class ResourceProgressResult:
    state: ResourceProgressState
    current_held_count: int | None
    baseline_held_count: int | None
    displayed_goal_progress: int | None
    goal_complete: bool
    matched_slots: list[int]
    matched_slot_details: list[dict]
    matched_item_ids: list[int]
    source: str
    reason: str
    warnings: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    progress_update_applied: bool = False
    duplicate_snapshot: bool = False
    current_snapshot_valid: bool = False
    snapshot_validity_missing: list[str] = field(default_factory=list)
    progress_state_repaired: bool = False
    repair_reason: str | None = None
    invariant_violations: list[str] = field(default_factory=list)
    invalid_matched_slots: list[dict] = field(default_factory=list)
    summary_derived: bool = False
    observe_only: bool = False
    progress_retained_from_previous: bool = False
    retained_reason: str | None = None
    retained_age_ticks: int | None = None
    progress_drop_reason: str | None = None
    progress_held_reason: str | None = None
    progress_invalid_snapshot_count: int = 0
    progress_retained_previous_count: int = 0
    progress_flicker_prevented_count: int = 0
    last_progress_invalid_reason: str | None = None
    last_progress_retained_tick: int | None = None
    last_valid_progress_tick: int | None = None
    last_valid_inventory_signature: str | None = None


def clone_state(state: ResourceProgressState) -> ResourceProgressState:
    return copy.deepcopy(state)


def publish_state(target: ResourceProgressState, source: ResourceProgressState) -> ResourceProgressState:
    target.__dict__.update(copy.deepcopy(source.__dict__))
    return target


def normalize_items(items: Any) -> tuple[dict, ...] | None:
    if not isinstance(items, list):
        return None
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        iid = item_id(item)
        if iid is None:
            continue
        quantity = item_quantity(item)
        if quantity <= 0:
            continue
        normalized.append({"slot": item_slot(item), "itemId": iid, "quantity": quantity})
    normalized.sort(key=lambda row: (row.get("slot") is None, row.get("slot"), row.get("itemId")))
    return tuple(normalized)


def build_inventory_snapshot(
    *,
    session_path: str | None,
    latest_tick: int | None,
    inventory_signature: str | None,
    inventory_slot_count: int | None,
    items: Any = None,
    resource_counts: dict[str, Any] | None = None,
    items_present: bool | None = None,
) -> InventorySnapshot:
    if items_present is None:
        items_present = isinstance(items, list)
    raw_items = tuple(item for item in (items or []) if isinstance(item, dict)) if items_present else None
    return InventorySnapshot(
        session_path=session_path,
        latest_tick=latest_tick,
        inventory_signature=inventory_signature,
        inventory_slot_count=inventory_slot_count,
        items=raw_items,
        resource_counts=resource_counts if isinstance(resource_counts, dict) else {},
    )


def count_from_resource_counts(resource_counts: dict[str, Any], definition: ResourceDefinition) -> dict | None:
    target_ids = set(definition.item_ids)
    best: dict | None = None
    best_score: tuple[int, int, int] | None = None
    for _resource_id, record in resource_counts.items():
        if not isinstance(record, dict):
            continue
        record_item_ids = {iid for iid in (as_int(value) for value in record.get("itemIds", [])) if iid is not None}
        matched_ids = {iid for iid in (as_int(value) for value in record.get("matchedItemIds", [])) if iid is not None}
        by_item_id = record.get("byItemId") if isinstance(record.get("byItemId"), dict) else {}
        by_item_ids = {iid for iid in (as_int(value) for value in by_item_id.keys()) if iid is not None}
        candidate_ids = record_item_ids or matched_ids or by_item_ids
        if not (candidate_ids & target_ids):
            continue
        if by_item_id:
            count = sum(as_int(by_item_id.get(str(iid))) or as_int(by_item_id.get(iid)) or 0 for iid in target_ids)
        else:
            count = as_int(record.get("count"))
        if count is None:
            continue
        score = (1 if candidate_ids == target_ids else 0, len(candidate_ids & target_ids), int(count))
        if best_score is None or score > best_score:
            best = {
                "known": True,
                "count": max(0, int(count)),
                "matchedItems": [],
                "matchedSlots": [],
                "matchedSlotDetails": [],
                "matchedItemIds": sorted(candidate_ids & target_ids),
                "source": "inventory_resource_counts",
                "summaryDerived": True,
                "warnings": [RESOURCE_COUNTS_FALLBACK_WARNING],
                "invalidMatchedSlots": [],
            }
            best_score = score
    return best


def count_resource_items(snapshot: InventorySnapshot, definition: ResourceDefinition) -> dict:
    target_ids = set(definition.item_ids)
    if snapshot.items is not None:
        count = 0
        matched: list[dict] = []
        matched_ids: set[int] = set()
        invalid_slots: list[dict] = []
        for item in snapshot.items:
            if not isinstance(item, dict):
                continue
            iid = item_id(item)
            if iid is None:
                if item.get("counted"):
                    invalid_slots.append({"slot": item.get("slot"), "itemId": item.get("itemId"), "quantity": item.get("quantity"), "counted": True})
                continue
            if iid not in target_ids:
                continue
            quantity = item_quantity(item)
            if quantity <= 0:
                continue
            row = {
                "slot": item_slot(item),
                "itemId": iid,
                "quantity": quantity,
                "resourceName": definition.id,
                "counted": True,
                "source": "inventory.items",
                "summaryDerived": False,
            }
            count += quantity
            matched.append(row)
            matched_ids.add(iid)
        warnings = [INVALID_MATCHED_SLOT_WARNING] if invalid_slots else []
        resource_result = count_from_resource_counts(snapshot.resource_counts, definition) if snapshot.resource_counts else None
        if resource_result is not None and as_int(resource_result.get("count")) != count:
            warnings.append("resourceCounts disagreed with inventory item snapshot; using slot item snapshot")
        return {
            "known": True,
            "count": count,
            "matchedItems": [{"slot": row.get("slot"), "itemId": row.get("itemId"), "quantity": row.get("quantity")} for row in matched],
            "matchedSlots": sorted(slot for slot in (row.get("slot") for row in matched) if slot is not None),
            "matchedSlotDetails": matched,
            "matchedItemIds": sorted(matched_ids),
            "source": "inventory_snapshot_items",
            "summaryDerived": False,
            "warnings": warnings,
            "invalidMatchedSlots": invalid_slots,
        }
    resource_result = count_from_resource_counts(snapshot.resource_counts, definition) if snapshot.resource_counts else None
    if resource_result is not None:
        return resource_result
    return {
        "known": False,
        "count": None,
        "matchedItems": [],
        "matchedSlots": [],
        "matchedSlotDetails": [],
        "matchedItemIds": [],
        "source": "unknown",
        "summaryDerived": False,
        "warnings": [],
        "invalidMatchedSlots": [],
        "missingReason": "inventory item list missing",
    }


def is_valid_inventory_snapshot(snapshot: InventorySnapshot, count_result: dict) -> dict:
    missing: list[str] = []
    if not snapshot.session_path:
        missing.append("sessionPath")
    if snapshot.latest_tick is None:
        missing.append("latestTick")
    if not snapshot.inventory_signature:
        missing.append("inventorySignature")
    if not count_result.get("known"):
        missing.append("currentHeldResourceCount")
    if snapshot.items is None and not snapshot.resource_counts:
        missing.append("inventoryItemsOrResourceCounts")
    return {
        "valid": not missing,
        "missing": missing,
        "reason": "valid inventory snapshot" if not missing else "invalid inventory snapshot missing " + ", ".join(missing),
    }


def state_from_dict(value: dict | None, *, task: str = "woodcutting", goal_count: int | None = None, resource_group: str = "woodcutting_logs") -> ResourceProgressState:
    value = value if isinstance(value, dict) else {}
    warnings = [str(item) for item in value.get("repairWarnings", [])]
    deprecated_values = [
        value.get("observedGained"),
        value.get("observedRemoved"),
        value.get("cumulativeGained"),
        value.get("cumulativeLostOrRemoved"),
    ]
    deprecated_history_present = any(as_int(item) for item in deprecated_values)
    if deprecated_history_present and OLD_CUMULATIVE_HISTORY_WARNING not in warnings:
        warnings.append(OLD_CUMULATIVE_HISTORY_WARNING)
    trusted_schema = value.get("schema") == SCHEMA and not deprecated_history_present
    return ResourceProgressState(
        task=str(value.get("task") or task),
        session_path=value.get("sessionPath"),
        goal_count=value.get("goalCount", goal_count),
        resource_group=str(value.get("resourceGroup") or resource_group),
        baseline_established=bool(value.get("baselineEstablished")) if trusted_schema else False,
        baseline_held_count=as_int(value.get("baselineHeldCount")) if trusted_schema else None,
        baseline_tick=as_int(value.get("baselineTick")) if trusted_schema else None,
        baseline_inventory_signature=value.get("baselineInventorySignature") if trusted_schema else None,
        last_inventory_signature=(value.get("lastInventorySignature") or value.get("previousInventorySignature") or value.get("lastProcessedInventorySignature")) if trusted_schema else None,
        last_inventory_tick=as_int(value.get("lastInventoryTick", value.get("previousInventoryTick", value.get("lastProcessedInventoryTick")))) if trusted_schema else None,
        current_held_count=as_int(value.get("currentHeldCount")) if trusted_schema else None,
        displayed_goal_progress=as_int(value.get("displayedGoalProgress")) if trusted_schema else 0,
        goal_complete=bool(value.get("goalComplete")) if trusted_schema else False,
        progress_source=str(value.get("progressSource") or "baseline_pending") if trusted_schema else "baseline_pending",
        matched_slots=[dict(row) for row in value.get("matchedSlots", []) if isinstance(row, dict)] if trusted_schema else [],
        repair_warnings=warnings,
        progress_invalid_snapshot_count=as_int(value.get("progressInvalidSnapshotCount")) or 0,
        progress_retained_previous_count=as_int(value.get("progressRetainedPreviousCount")) or 0,
        progress_flicker_prevented_count=as_int(value.get("progressFlickerPreventedCount")) or 0,
        last_progress_invalid_reason=value.get("lastProgressInvalidReason") if trusted_schema else None,
        last_progress_retained_tick=as_int(value.get("lastProgressRetainedTick")) if trusted_schema else None,
        last_valid_progress_tick=as_int(value.get("lastValidProgressTick")) if trusted_schema else None,
        last_valid_inventory_signature=value.get("lastValidInventorySignature") if trusted_schema else None,
    )


def state_to_dict(state: ResourceProgressState) -> dict:
    return {
        "schema": SCHEMA,
        "task": state.task,
        "sessionPath": state.session_path,
        "goalCount": state.goal_count,
        "resourceGroup": state.resource_group,
        "baselineEstablished": state.baseline_established,
        "baselineHeldCount": state.baseline_held_count,
        "baselineTick": state.baseline_tick,
        "baselineInventorySignature": state.baseline_inventory_signature,
        "lastInventorySignature": state.last_inventory_signature,
        "lastInventoryTick": state.last_inventory_tick,
        "currentHeldCount": state.current_held_count,
        "displayedGoalProgress": state.displayed_goal_progress,
        "goalComplete": state.goal_complete,
        "progressSource": state.progress_source,
        "matchedSlots": list(state.matched_slots),
        "repairWarnings": list(state.repair_warnings),
        "progressInvalidSnapshotCount": state.progress_invalid_snapshot_count,
        "progressRetainedPreviousCount": state.progress_retained_previous_count,
        "progressFlickerPreventedCount": state.progress_flicker_prevented_count,
        "lastProgressInvalidReason": state.last_progress_invalid_reason,
        "lastProgressRetainedTick": state.last_progress_retained_tick,
        "lastValidProgressTick": state.last_valid_progress_tick,
        "lastValidInventorySignature": state.last_valid_inventory_signature,
        "progressRetainedFromPrevious": state.progress_retained_from_previous,
        "retainedReason": state.retained_reason,
        "retainedAgeTicks": state.retained_age_ticks,
        "progressDropReason": state.progress_drop_reason,
        "progressHeldReason": state.progress_held_reason,
        "observedGained": None,
        "observedRemoved": None,
        "cumulativeGained": None,
        "cumulativeLostOrRemoved": None,
    }


def initialize_or_update_progress(
    state: ResourceProgressState,
    snapshot: InventorySnapshot,
    definition: ResourceDefinition,
    goal_count: int | None,
) -> ResourceProgressResult:
    published_state = state
    state = clone_state(state)

    def publish(result: ResourceProgressResult) -> ResourceProgressResult:
        result.state = publish_state(published_state, result.state)
        return result

    state.goal_count = goal_count
    state.resource_group = definition.id
    count_result = count_resource_items(snapshot, definition)
    current_count = as_int(count_result.get("count")) if count_result.get("known") else None
    validity = is_valid_inventory_snapshot(snapshot, count_result)
    warnings = dedupe(list(state.repair_warnings) + list(count_result.get("warnings") or []))
    invalid_slots = list(count_result.get("invalidMatchedSlots") or [])
    duplicate = bool(snapshot.inventory_signature and snapshot.inventory_signature == state.last_inventory_signature)
    update_applied = False
    source = "observe_only" if goal_count is None else "baseline_pending"
    reason = "progress disabled because no goal count was supplied" if goal_count is None else "waiting for valid inventory snapshot"

    if not validity.get("valid"):
        invalid_reason = count_result.get("missingReason") or validity.get("reason") or reason
        warnings.append(invalid_reason)
        state.progress_invalid_snapshot_count += 1
        state.last_progress_invalid_reason = str(invalid_reason)
        if state.baseline_established and state.current_held_count is not None:
            retained_reason = "inventory snapshot unavailable this poll; retaining previous progress"
            state.progress_retained_previous_count += 1
            if int(state.displayed_goal_progress or 0) > 0:
                state.progress_flicker_prevented_count += 1
            state.progress_retained_from_previous = True
            state.retained_reason = retained_reason
            state.progress_source = "retained_previous_progress"
            state.last_progress_retained_tick = snapshot.latest_tick
            if snapshot.latest_tick is not None and state.last_valid_progress_tick is not None:
                state.retained_age_ticks = max(0, int(snapshot.latest_tick) - int(state.last_valid_progress_tick))
            else:
                state.retained_age_ticks = None
            state.progress_held_reason = "invalid_snapshot_retained_previous"
            details = list(state.matched_slots)
            matched_ids = sorted({as_int(row.get("itemId")) for row in details if as_int(row.get("itemId")) is not None})
            return publish(ResourceProgressResult(
                state=state,
                current_held_count=state.current_held_count,
                baseline_held_count=state.baseline_held_count,
                displayed_goal_progress=state.displayed_goal_progress,
                goal_complete=state.goal_complete,
                matched_slots=sorted(slot for slot in (as_int(row.get("slot")) for row in details) if slot is not None),
                matched_slot_details=details,
                matched_item_ids=matched_ids,
                source="retained_previous_progress",
                reason=retained_reason,
                warnings=dedupe(warnings),
                progress_update_applied=False,
                duplicate_snapshot=duplicate,
                current_snapshot_valid=False,
                snapshot_validity_missing=list(validity.get("missing") or []),
                invalid_matched_slots=invalid_slots,
                summary_derived=bool(count_result.get("summaryDerived")),
                observe_only=goal_count is None,
                progress_retained_from_previous=True,
                retained_reason=retained_reason,
                retained_age_ticks=state.retained_age_ticks,
                progress_held_reason="invalid_snapshot_retained_previous",
                progress_invalid_snapshot_count=state.progress_invalid_snapshot_count,
                progress_retained_previous_count=state.progress_retained_previous_count,
                progress_flicker_prevented_count=state.progress_flicker_prevented_count,
                last_progress_invalid_reason=state.last_progress_invalid_reason,
                last_progress_retained_tick=state.last_progress_retained_tick,
                last_valid_progress_tick=state.last_valid_progress_tick,
                last_valid_inventory_signature=state.last_valid_inventory_signature,
            ))
        state.progress_retained_from_previous = False
        state.retained_reason = None
        state.retained_age_ticks = None
        state.progress_source = source
        return publish(ResourceProgressResult(
            state=state,
            current_held_count=current_count,
            baseline_held_count=state.baseline_held_count,
            displayed_goal_progress=None if goal_count is None else 0,
            goal_complete=False,
            matched_slots=list(count_result.get("matchedSlots") or []),
            matched_slot_details=list(count_result.get("matchedSlotDetails") or []),
            matched_item_ids=list(count_result.get("matchedItemIds") or []),
            source=source,
            reason=invalid_reason,
            warnings=dedupe(warnings),
            progress_update_applied=False,
            duplicate_snapshot=duplicate,
            current_snapshot_valid=False,
            snapshot_validity_missing=list(validity.get("missing") or []),
            invalid_matched_slots=invalid_slots,
            summary_derived=bool(count_result.get("summaryDerived")),
            observe_only=goal_count is None,
            progress_invalid_snapshot_count=state.progress_invalid_snapshot_count,
            progress_retained_previous_count=state.progress_retained_previous_count,
            progress_flicker_prevented_count=state.progress_flicker_prevented_count,
            last_progress_invalid_reason=state.last_progress_invalid_reason,
            last_progress_retained_tick=state.last_progress_retained_tick,
            last_valid_progress_tick=state.last_valid_progress_tick,
            last_valid_inventory_signature=state.last_valid_inventory_signature,
        ))

    if goal_count is None:
        state.current_held_count = current_count
        state.last_inventory_signature = snapshot.inventory_signature
        state.last_inventory_tick = snapshot.latest_tick
        state.matched_slots = list(count_result.get("matchedSlotDetails") or [])
        state.progress_source = "observe_only"
        state.progress_retained_from_previous = False
        state.retained_reason = None
        state.retained_age_ticks = None
        state.last_valid_progress_tick = snapshot.latest_tick
        state.last_valid_inventory_signature = snapshot.inventory_signature
        return publish(ResourceProgressResult(
            state=state,
            current_held_count=current_count,
            baseline_held_count=None,
            displayed_goal_progress=None,
            goal_complete=False,
            matched_slots=list(count_result.get("matchedSlots") or []),
            matched_slot_details=list(count_result.get("matchedSlotDetails") or []),
            matched_item_ids=list(count_result.get("matchedItemIds") or []),
            source="observe_only",
            reason="progress disabled because no goal count was supplied",
            warnings=dedupe(warnings),
            progress_update_applied=True,
            duplicate_snapshot=duplicate,
            current_snapshot_valid=True,
            invalid_matched_slots=invalid_slots,
            summary_derived=bool(count_result.get("summaryDerived")),
            observe_only=True,
            last_valid_progress_tick=state.last_valid_progress_tick,
            last_valid_inventory_signature=state.last_valid_inventory_signature,
        ))

    if not state.baseline_established:
        state.baseline_established = True
        state.baseline_held_count = current_count
        state.baseline_tick = snapshot.latest_tick
        state.baseline_inventory_signature = snapshot.inventory_signature
        state.current_held_count = current_count
        state.displayed_goal_progress = 0
        state.goal_complete = False
        state.last_inventory_signature = snapshot.inventory_signature
        state.last_inventory_tick = snapshot.latest_tick
        state.matched_slots = list(count_result.get("matchedSlotDetails") or [])
        state.progress_source = "baseline_initialized"
        state.progress_retained_from_previous = False
        state.retained_reason = None
        state.retained_age_ticks = None
        state.progress_drop_reason = None
        state.progress_held_reason = None
        state.last_valid_progress_tick = snapshot.latest_tick
        state.last_valid_inventory_signature = snapshot.inventory_signature
        source = "baseline_initialized"
        reason = "baseline initialized; existing held resources did not count as gained"
        update_applied = True
    else:
        previous_displayed = int(state.displayed_goal_progress or 0)
        baseline = int(state.baseline_held_count or 0)
        instantaneous_displayed = max(0, int(current_count or 0) - baseline)
        displayed = max(previous_displayed, instantaneous_displayed)
        state.current_held_count = current_count
        state.displayed_goal_progress = displayed
        state.goal_complete = bool(displayed >= int(goal_count))
        state.last_inventory_signature = snapshot.inventory_signature
        state.last_inventory_tick = snapshot.latest_tick
        state.matched_slots = list(count_result.get("matchedSlotDetails") or [])
        state.progress_source = "inventory_snapshot_held_vs_baseline"
        state.progress_retained_from_previous = False
        state.retained_reason = None
        state.retained_age_ticks = None
        state.last_valid_progress_tick = snapshot.latest_tick
        state.last_valid_inventory_signature = snapshot.inventory_signature
        source = state.progress_source
        state.progress_drop_reason = None
        if instantaneous_displayed < previous_displayed:
            state.progress_held_reason = "valid_inventory_count_decreased_retained_monotonic_progress"
            reason = "held count decreased; retained monotonic gained-since-start progress"
        elif duplicate:
            state.progress_held_reason = "duplicate_snapshot_did_not_change_progress"
            reason = "duplicate snapshot did not change held-vs-baseline progress"
        else:
            state.progress_held_reason = None
            reason = "daily gained-since-start is monotonic held-vs-baseline progress"
        update_applied = not duplicate

    return publish(ResourceProgressResult(
        state=state,
        current_held_count=current_count,
        baseline_held_count=state.baseline_held_count,
        displayed_goal_progress=state.displayed_goal_progress,
        goal_complete=state.goal_complete,
        matched_slots=list(count_result.get("matchedSlots") or []),
        matched_slot_details=list(count_result.get("matchedSlotDetails") or []),
        matched_item_ids=list(count_result.get("matchedItemIds") or []),
        source=source,
        reason=reason,
        warnings=dedupe(warnings),
        progress_update_applied=update_applied,
        duplicate_snapshot=duplicate,
        current_snapshot_valid=True,
        invalid_matched_slots=invalid_slots,
        summary_derived=bool(count_result.get("summaryDerived")),
        progress_retained_from_previous=state.progress_retained_from_previous,
        retained_reason=state.retained_reason,
        retained_age_ticks=state.retained_age_ticks,
        progress_drop_reason=state.progress_drop_reason,
        progress_held_reason=state.progress_held_reason,
        progress_invalid_snapshot_count=state.progress_invalid_snapshot_count,
        progress_retained_previous_count=state.progress_retained_previous_count,
        progress_flicker_prevented_count=state.progress_flicker_prevented_count,
        last_progress_invalid_reason=state.last_progress_invalid_reason,
        last_progress_retained_tick=state.last_progress_retained_tick,
        last_valid_progress_tick=state.last_valid_progress_tick,
        last_valid_inventory_signature=state.last_valid_inventory_signature,
    ))


def update_resource_progress(
    state: ResourceProgressState,
    snapshot: InventorySnapshot,
    definition: ResourceDefinition,
    goal_count: int | None,
) -> ResourceProgressResult:
    return initialize_or_update_progress(state, snapshot, definition, goal_count)


def sanitize_resource_progress_state(state: ResourceProgressState, snapshot: InventorySnapshot, definition: ResourceDefinition) -> ResourceProgressState:
    return initialize_or_update_progress(state, snapshot, definition, state.goal_count).state


def build_progress_diagnostic(state: ResourceProgressState, snapshot: InventorySnapshot, definition: ResourceDefinition) -> dict:
    count = count_resource_items(snapshot, definition)
    validity = is_valid_inventory_snapshot(snapshot, count)
    return {
        "state": state_to_dict(state),
        "currentCount": count,
        "snapshotValid": validity.get("valid"),
        "snapshotValidityMissing": validity.get("missing") or [],
        "duplicateSnapshot": bool(snapshot.inventory_signature and snapshot.inventory_signature == state.last_inventory_signature),
        "invalidMatchedSlots": count.get("invalidMatchedSlots") or [],
    }


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
