from __future__ import annotations

import time
from typing import Any

import brain_core
import capabilities

from analyzers.live_state import BrainContext


def brain_status_fields(state: dict[str, Any], reset_applied: bool) -> dict[str, Any]:
    progress = state.get("resourceProgress") if isinstance(state.get("resourceProgress"), dict) else {}
    return {
        "brainResetApplied": bool(reset_applied),
        "brainBaselineEstablished": bool(progress.get("baselineEstablished") or state.get("baselineEstablished")),
        "brainBaselineTick": progress.get("baselineTick") or state.get("brainBaselineTick"),
        "brainLastProcessedInventorySignature": progress.get("lastProcessedInventorySignature") or state.get("lastProcessedInventorySignature"),
        "brainLastProcessedInventoryTick": progress.get("lastProcessedInventoryTick") or state.get("lastProcessedInventoryTick"),
        "brainObservedGained": None,
        "brainObservedRemoved": None,
        "brainCurrentHeldCount": progress.get("currentHeldCount"),
        "brainBaselineHeldCount": progress.get("baselineHeldCount") if progress.get("baselineHeldCount") is not None else state.get("baselineHeldCount"),
        "brainHasValidPostBaselineProgressHistory": False,
        "brainProgressStateRepaired": bool(progress.get("progressStateRepaired")),
        "brainProgressRepairReason": progress.get("repairReason"),
        "brainCurrentInventorySignature": progress.get("currentInventorySignature"),
        "brainCurrentSnapshotValid": progress.get("currentSnapshotValid"),
        "brainPreviousInventorySnapshotAvailable": progress.get("previousInventorySnapshotAvailable"),
        "progressInvalidSnapshotCount": progress.get("progressInvalidSnapshotCount", 0),
        "progressRetainedPreviousCount": progress.get("progressRetainedPreviousCount", 0),
        "progressFlickerPreventedCount": progress.get("progressFlickerPreventedCount", 0),
        "lastProgressInvalidReason": progress.get("lastProgressInvalidReason"),
        "lastProgressRetainedTick": progress.get("lastProgressRetainedTick"),
        "lastValidProgressTick": progress.get("lastValidProgressTick"),
        "lastValidInventorySignature": progress.get("lastValidInventorySignature"),
        "progressRetainedPreviousThisPoll": bool(progress.get("progressRetainedFromPrevious")),
        "progressInvalidSnapshotThisPoll": progress.get("currentSnapshotValid") is False,
        "progressRetainedFromPrevious": bool(progress.get("progressRetainedFromPrevious")),
        "progressRetainedReason": progress.get("retainedReason"),
    }


def evaluate_brain_context(
    response: dict[str, Any],
    state: dict[str, Any],
    *,
    task: str,
    goal_count: int | None,
    max_events: int,
    reset_applied: bool = False,
) -> BrainContext:
    started = time.perf_counter()
    decision, updated = brain_core.evaluate_brain(
        response,
        state,
        task=task,
        goal_count=goal_count,
        max_events=max_events,
    )
    missing = capabilities.normalize_capability_names(decision.get("missingCapabilities") or [])
    warnings = [str(item) for item in decision.get("warnings") or [] if item]
    return BrainContext(
        status=str(decision.get("contextStatus") or decision.get("status") or "PASS").upper(),
        warnings=warnings,
        missing_capabilities=missing,
        source_tick=response.get("latestTick") if isinstance(response.get("latestTick"), int) else None,
        retained_from_previous=bool((updated.get("resourceProgress") or {}).get("progressRetainedFromPrevious")) if isinstance(updated.get("resourceProgress"), dict) else False,
        timing_millis=(time.perf_counter() - started) * 1000.0,
        decision=decision,
        updated_state=updated,
        status_fields=brain_status_fields(updated, reset_applied),
    )
