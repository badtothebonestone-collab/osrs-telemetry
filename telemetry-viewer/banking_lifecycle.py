from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_schema
import telemetry_sources


SCHEMA_VERSION = "banking_lifecycle.v1"
NORMAL_LOG_ITEM_IDS = {1511}
ITEM_NAMES = {1511: "Logs"}


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
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def _plain(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def _lower(value: Any) -> str:
    return _plain(value).lower()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jsonl_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return _jsonl_records(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError):
        return []


def _load_events(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    recording = Path(path)
    events_path = recording / "events.jsonl" if recording.is_dir() else recording
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        with events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    warnings.append(f"events.jsonl line {line_number} JSONDecodeError: {error.msg}")
                    continue
                if isinstance(value, dict):
                    events.append(value)
    except FileNotFoundError:
        warnings.append(f"events file missing: {events_path}")
    except OSError as error:
        warnings.append(f"events file unreadable: {type(error).__name__}: {error}")
    return events, warnings


def _source_data(source: dict[str, Any]) -> Any:
    if "data" in source:
        return source.get("data")
    raw = source.get("raw")
    if not isinstance(raw, str) or not raw:
        return None
    name = str(source.get("name") or "").lower()
    path = str(source.get("path") or "").lower()
    if name == "events" or path.endswith(".jsonl") or path.endswith(".ndjson"):
        return _jsonl_records(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _payloads_by_source(event: dict[str, Any]) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for source in event.get("sources") or []:
        if not isinstance(source, dict):
            continue
        name = str(source.get("name") or source.get("path") or "")
        if not name:
            continue
        data = _source_data(source)
        if data is not None:
            payloads[name] = data
    return payloads


def _bank_ui_source_metadata(event: dict[str, Any]) -> dict[str, Any]:
    for source in event.get("sources") or []:
        if not isinstance(source, dict):
            continue
        if str(source.get("name") or "") != "bank_ui":
            continue
        freshness = _dict(source.get("freshness"))
        return {
            "sourceKind": source.get("source_kind"),
            "url": source.get("url"),
            "need": source.get("need"),
            "parseStatus": source.get("parse_status"),
            "httpStatus": source.get("http_status"),
            "modifiedUtc": source.get("modified_utc"),
            "ageSeconds": source.get("age_seconds"),
            "stale": source.get("stale"),
            "latestTick": source.get("latest_tick") or freshness.get("latestTick"),
            "latestExportSequence": source.get("latest_export_sequence"),
            "freshness": freshness or None,
            "warnings": source.get("warnings") or [],
        }
    return {}


def _nested_bank_payload(value: Any, *, max_depth: int = 5) -> dict[str, Any]:
    if max_depth < 0:
        return {}
    if isinstance(value, dict):
        if any(key in value for key in ("bankOpen", "depositBoxOpen", "bankRootVisible", "bankSummary", "bankContainer")):
            return value
        for key in ("banking", "bankUi", "bankUI", "bank_ui", "bank", "payload"):
            found = _nested_bank_payload(value.get(key), max_depth=max_depth - 1)
            if found:
                return found
        for child in value.values():
            found = _nested_bank_payload(child, max_depth=max_depth - 1)
            if found:
                return found
    return {}


def _item_count_map(items: Any) -> tuple[dict[int, int], dict[int, str]]:
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for raw in items or []:
        item = _dict(raw)
        item_id = _int(_first(item.get("itemId"), item.get("id")))
        if item_id is None or item_id <= 0:
            continue
        quantity = _int(_first(item.get("quantity"), item.get("count"), item.get("qty"))) or 1
        counts[item_id] = counts.get(item_id, 0) + max(0, quantity)
        name = _plain(_first(item.get("name"), item.get("displayName")))
        if name:
            names[item_id] = name
    return counts, names


def _counts_from_resource_counts(resource_counts: dict[str, Any]) -> tuple[dict[int, int], dict[int, str]]:
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for raw in resource_counts.values():
        record = _dict(raw)
        display = _plain(record.get("displayName"))
        by_item = _dict(record.get("byItemId"))
        if by_item:
            for key, value in by_item.items():
                item_id = _int(key)
                count = _int(value)
                if item_id is None or count is None:
                    continue
                counts[item_id] = max(counts.get(item_id, 0), count)
                if display:
                    names.setdefault(item_id, display)
            continue
        for item in record.get("matchedItems") or []:
            item_id = _int(_first(_dict(item).get("itemId"), _dict(item).get("id")))
            if item_id is None:
                continue
            quantity = _int(_first(_dict(item).get("quantity"), _dict(item).get("count"))) or 1
            counts[item_id] = counts.get(item_id, 0) + quantity
            if display:
                names.setdefault(item_id, display)
    return counts, names


def _normal_logs_count(inventory: dict[str, Any]) -> int | None:
    resource_counts = _dict(inventory.get("resourceCounts"))
    for key in ("normal_logs", "woodcutting_logs", "logs"):
        count = _int(_dict(resource_counts.get(key)).get("count"))
        if count is not None:
            return count
    counts, _names = _item_count_map(_first(inventory.get("items"), inventory.get("slots"), inventory.get("inventorySlots")) or [])
    total = sum(count for item_id, count in counts.items() if item_id in NORMAL_LOG_ITEM_IDS)
    return total if total else None


def _inventory_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    items = _first(raw.get("items"), raw.get("slots"), raw.get("inventorySlots"), raw.get("inventorySlotWidgets")) or []
    counts, names = _item_count_map(items)
    resource_counts = _dict(raw.get("resourceCounts"))
    resource_item_counts, resource_names = _counts_from_resource_counts(resource_counts)
    for item_id, count in resource_item_counts.items():
        counts[item_id] = max(counts.get(item_id, 0), count)
    names.update(resource_names)
    for item_id, name in ITEM_NAMES.items():
        if item_id in counts:
            names.setdefault(item_id, name)
    return {
        "known": _first(raw.get("known"), raw.get("itemsKnown"), bool(raw)),
        "freeSlots": _int(raw.get("freeSlots")),
        "filledSlots": _int(raw.get("filledSlots")),
        "itemCount": _int(_first(raw.get("itemCount"), raw.get("totalQuantity"))),
        "inventoryFull": _bool(raw.get("inventoryFull")),
        "itemCounts": {str(key): value for key, value in sorted(counts.items())},
        "itemNames": {str(key): value for key, value in sorted(names.items())},
        "normalLogs": _normal_logs_count(raw),
    }


def _inventory_from_payloads(payloads: dict[str, Any], high_value: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    activity = _dict(payloads.get("activity"))
    bank_ui = _nested_bank_payload(payloads)
    return _inventory_snapshot(
        _dict(
            _first(
                activity.get("inventoryState"),
                activity.get("inventory"),
                high_value.get("inventory"),
                baseline.get("inventory"),
                bank_ui.get("inventorySummary"),
            )
        )
    )


def _bank_snapshot(raw: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    if not raw or raw.get("missing") is True:
        return {"present": False}
    source = _dict(source)
    bank_container = _dict(_first(raw.get("bankContainer"), raw.get("container")))
    bank_summary = _dict(raw.get("bankSummary"))
    raw_items = _first(bank_container.get("items"), raw.get("bankItems"), raw.get("bankWidgetItems")) or []
    counts, names = _item_count_map(raw_items)
    by_item = _dict(bank_summary.get("totalQuantityByItemId"))
    for key, value in by_item.items():
        item_id = _int(key)
        quantity = _int(value)
        if item_id is not None and quantity is not None:
            counts[item_id] = quantity
    for item_id, name in ITEM_NAMES.items():
        if item_id in counts:
            names.setdefault(item_id, name)
    container_available = _bool(
        _first(
            bank_container.get("available"),
            raw.get("bankContainerAvailable"),
            bank_summary.get("known"),
            bool(counts) if raw_items else None,
        )
    )
    bank_open = _bool(_first(raw.get("bankOpen"), raw.get("open")))
    deposit_box_open = _bool(_first(raw.get("depositBoxOpen"), raw.get("depositRootVisible")))
    root_visible = _bool(_first(raw.get("bankRootVisible"), raw.get("depositBoxRootVisible"), raw.get("depositRootVisible")))
    container_visible = _bool(raw.get("bankContainerVisible"))
    active_interface = _plain(raw.get("activeBankLikeInterface")) or "unknown"
    if active_interface == "unknown" and deposit_box_open:
        active_interface = "deposit_box"
    elif active_interface == "unknown" and bank_open:
        active_interface = "bank"
    elif active_interface == "unknown" and root_visible:
        active_interface = "bank"
    raw_delta = _dict(_first(raw.get("bankContainerDelta"), raw.get("bankItemDeltas"), raw.get("itemContainerChangedBank")))
    return {
        "present": True,
        "source": source or None,
        "bankOpen": bank_open,
        "depositBoxOpen": deposit_box_open,
        "bankPinOpen": _bool(raw.get("bankPinOpen")),
        "bankRootVisible": root_visible,
        "bankContainerVisible": container_visible,
        "bankInventoryVisible": _bool(raw.get("bankInventoryVisible")),
        "depositInventoryButtonVisible": _bool(raw.get("depositInventoryButtonVisible")),
        "topLevelInterfaceId": _int(raw.get("topLevelInterfaceId")),
        "activeBankLikeInterface": active_interface,
        "containerAvailable": bool(container_available),
        "itemCount": _int(_first(bank_summary.get("itemCount"), bank_container.get("itemCount"))),
        "nonEmptySlots": _int(_first(bank_summary.get("filledSlots"), bank_summary.get("nonEmptySlots"), bank_container.get("nonEmptySlots"))),
        "freeSlots": _int(bank_summary.get("freeSlots")),
        "itemCounts": {str(key): value for key, value in sorted(counts.items())},
        "itemNames": {str(key): value for key, value in sorted(names.items())},
        "bankContainerDelta": _bank_container_delta(raw_delta),
        "widgets": {
            "bankRootWidget": raw.get("bankRootWidget"),
            "depositBoxWidgetRoot": raw.get("depositBoxWidgetRoot"),
            "bankContainerWidget": raw.get("bankContainerWidget"),
            "bankInventoryWidget": raw.get("bankInventoryWidget"),
            "depositInventoryButtonWidget": raw.get("depositInventoryButtonWidget"),
        },
    }


def _bank_container_delta(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {"available": False, "changedItems": [], "warnings": []}
    changed: list[dict[str, Any]] = []
    for item in _list(_first(raw.get("changedItems"), raw.get("quantityChanges"), raw.get("items"))):
        record = _dict(item)
        item_id = _int(_first(record.get("id"), record.get("itemId")))
        before = _int(_first(record.get("beforeQuantity"), record.get("before")))
        after = _int(_first(record.get("afterQuantity"), record.get("after")))
        delta = _int(record.get("delta"))
        if item_id is None:
            continue
        if delta is None and before is not None and after is not None:
            delta = after - before
        if delta in (None, 0):
            continue
        changed.append(
            {
                "id": item_id,
                "name": _plain(_first(record.get("name"), record.get("displayName"))) or ITEM_NAMES.get(item_id) or f"item {item_id}",
                "before": before,
                "after": after,
                "delta": delta,
                "source": _first(record.get("source"), raw.get("source"), raw.get("eventSource"), "bank_container_delta"),
            }
        )
    return {
        "schema": raw.get("schema") or "bank_container_delta.v1",
        "available": bool(_first(raw.get("available"), bool(changed))),
        "tick": raw.get("tick"),
        "exportSeq": _first(raw.get("exportSeq"), raw.get("exportSequence")),
        "changedItems": changed,
        "warnings": raw.get("warnings") or [],
        "source": _first(raw.get("source"), raw.get("eventSource")),
    }


def _bank_from_payloads(payloads: dict[str, Any], high_value: dict[str, Any]) -> dict[str, Any]:
    raw = _nested_bank_payload(payloads)
    if not raw:
        raw = _dict(_first(high_value.get("bank"), high_value.get("bankUi"), high_value.get("bank_ui")))
    return _bank_snapshot(raw)


def _event_tick(event: dict[str, Any], high_value: dict[str, Any], payloads: dict[str, Any]) -> int | None:
    status = _dict(payloads.get("status"))
    baseline = _dict(payloads.get("baseline"))
    activity = _dict(payloads.get("activity"))
    return _int(
        _first(
            event.get("latest_tick"),
            high_value.get("latest_tick"),
            status.get("latestTickProcessed"),
            status.get("latestTick"),
            activity.get("latestTick"),
            baseline.get("latestTick"),
        )
    )


def _menu_entries(payloads: dict[str, Any], high_value: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in (
        _dict(_dict(payloads.get("status")).get("clientTickHot")).get("postMenuSort"),
        _dict(_dict(payloads.get("status")).get("clientTickHot")).get("hoverMenu"),
        _dict(payloads.get("status")).get("postMenuSort"),
        _dict(high_value.get("menu")),
        _dict(high_value.get("hover")),
    ):
        for entry in _dict(source).get("entries") or []:
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _candidate_records(payloads: dict[str, Any], high_value: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    status = _dict(payloads.get("status"))
    for key in ("nearby_objects", "route_objects"):
        records.extend(item for item in high_value.get(key) or [] if isinstance(item, dict))
    for census_key in ("worldModelRouteObjectCensus", "worldModelResourceObjectCensus", "worldModelServiceObjectCensus"):
        records.extend(item for item in _dict(status.get(census_key)).get("objects") or [] if isinstance(item, dict))
    records.extend(item for item in payloads.get("candidates") or [] if isinstance(item, dict))
    return records


def _is_bankish_name_or_action(name: Any, actions: Any = None) -> bool:
    text = f"{_lower(name)} {' '.join(_lower(action) for action in actions or []) if isinstance(actions, list) else _lower(actions)}"
    return any(token in text for token in ("bank", "banker", "deposit box", "deposit-box", "deposit"))


def _candidate_summary(record: dict[str, Any]) -> dict[str, Any] | None:
    candidate = telemetry_schema.normalized_candidate(record, fallback_kind="object")
    name = _first(candidate.get("effectiveName"), record.get("objectName"), record.get("name"))
    actions = _first(candidate.get("effectiveActions"), record.get("actions"))
    if not _is_bankish_name_or_action(name, actions):
        return None
    return {
        key: value
        for key, value in {
            "ref": _first(candidate.get("ref"), record.get("objectKey"), record.get("hash")),
            "id": _first(candidate.get("effectiveId"), candidate.get("rawId"), record.get("id")),
            "name": _plain(name),
            "actions": candidate.get("effectiveActions") or actions or [],
            "worldPoint": candidate.get("worldPoint"),
            "distance": candidate.get("distance"),
            "onScreen": _first(candidate.get("onScreen"), _dict(record.get("projection")).get("onScreen")),
            "source": _first(record.get("source"), _dict(candidate.get("source")).get("type"), "candidate"),
        }.items()
        if value not in (None, "", [], {})
    }


def _snapshot_from_event(event: dict[str, Any]) -> dict[str, Any]:
    payloads = _payloads_by_source(event)
    high_value = _dict(event.get("high_value_fields"))
    baseline = _dict(payloads.get("baseline"))
    inventory = _inventory_from_payloads(payloads, high_value, baseline)
    bank_source = _bank_ui_source_metadata(event)
    raw_bank = _nested_bank_payload(payloads)
    if not raw_bank:
        raw_bank = _dict(_first(high_value.get("bank"), high_value.get("bankUi"), high_value.get("bank_ui")))
    bank = _bank_snapshot(raw_bank, bank_source)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in _candidate_records(payloads, high_value):
        candidate = _candidate_summary(record)
        if not candidate:
            continue
        key = str(_first(candidate.get("ref"), candidate.get("name"), repr(candidate)))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return {
        "elapsedSeconds": _float(event.get("elapsed_seconds")),
        "wallTimeUtc": event.get("wall_time_utc"),
        "tick": _event_tick(event, high_value, payloads),
        "inventory": inventory,
        "bank": bank,
        "bankTargets": candidates,
        "menuEntries": _menu_entries(payloads, high_value),
    }


def _snapshot_from_context(context: dict[str, Any]) -> dict[str, Any]:
    payloads = {
        "baseline": context.get("baseline") or {},
        "status": context.get("status") or {},
        "activity": context.get("activity") or {},
        "candidates": context.get("candidates") or [],
        "events": context.get("events") or [],
        "bank_ui": context.get("bank_ui") or context.get("bankUi") or context.get("bank") or {},
    }
    event = {
        "event_type": "source_snapshot",
        "wall_time_utc": telemetry_sources.utc_now(),
        "elapsed_seconds": 0.0,
        "high_value_fields": telemetry_schema.normalized_telemetry(context),
        "sources": [{"name": name, "data": value} for name, value in payloads.items()],
    }
    return _snapshot_from_event(event)


def _action_from_classification(record: dict[str, Any]) -> dict[str, Any] | None:
    if str(record.get("eventKind") or "").lower() not in {"click", "mouse_down", "mouse_up"}:
        return None
    menu = _dict(record.get("menuContext"))
    target_context = _dict(record.get("targetContext"))
    option = _first(menu.get("hoverOption"), target_context.get("targetAction"), record.get("option"))
    target = _first(menu.get("hoverTarget"), target_context.get("targetName"), record.get("target"))
    if not option and not target:
        return None
    return {
        "source": "input_action_classification",
        "eventSeq": record.get("eventSeq"),
        "eventKind": record.get("eventKind"),
        "classification": record.get("classification"),
        "button": record.get("button"),
        "option": _plain(option),
        "target": _plain(target),
        "elapsedSeconds": _dict(record.get("time")).get("elapsedSeconds"),
        "wallTimeUtc": _dict(record.get("time")).get("wallTimeUtc"),
        "confidence": record.get("confidence"),
        "region": record.get("region"),
        "menuOpenBefore": menu.get("menuOpenBefore"),
        "menuOpenAfter": menu.get("menuOpenAfter"),
        "warnings": record.get("warnings") or [],
    }


def _action_from_target_quality(record: dict[str, Any]) -> dict[str, Any] | None:
    option = _first(record.get("targetAction"), record.get("action"), record.get("option"))
    target = _first(record.get("targetName"), record.get("target"), record.get("name"))
    if not option and not target:
        return None
    return {
        "source": "target_match_quality",
        "eventSeq": record.get("eventSeq"),
        "option": _plain(option),
        "target": _plain(target),
        "quality": record.get("quality"),
        "score": record.get("score"),
        "confidence": record.get("confidence"),
    }


def _action_from_menu_interaction(record: dict[str, Any]) -> dict[str, Any] | None:
    option = _first(record.get("selectedOption"), record.get("option"), _dict(record.get("selection")).get("option"))
    target = _first(record.get("selectedTarget"), record.get("target"), _dict(record.get("selection")).get("target"))
    if not option and not target:
        return None
    return {
        "source": "menu_interaction",
        "eventSeq": record.get("selectedClickEventSeq") or record.get("eventSeq"),
        "option": _plain(option),
        "target": _plain(target),
        "rowGeometryProven": record.get("rowGeometryProven") or record.get("rowBoundsPresent"),
        "confidence": record.get("confidence"),
    }


def _action_from_menu_entry(entry: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    option = _plain(entry.get("option"))
    target = _plain(entry.get("target"))
    if not option and not target:
        return None
    if not _is_bankish_name_or_action(target, [option]):
        return None
    return {
        "source": "menu_entry_snapshot",
        "option": option,
        "target": target,
        "tick": snapshot.get("tick"),
        "elapsedSeconds": snapshot.get("elapsedSeconds"),
    }


def _action_key(action: dict[str, Any]) -> tuple[Any, ...]:
    return (
        action.get("source"),
        action.get("eventSeq"),
        action.get("eventKind"),
        action.get("option"),
        action.get("target"),
        action.get("tick"),
        action.get("elapsedSeconds"),
    )


def _collect_actions(
    snapshots: list[dict[str, Any]],
    input_action_classifications: list[dict[str, Any]],
    target_match_quality: list[dict[str, Any]],
    menu_interactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_actions: list[dict[str, Any]] = []
    raw_actions.extend(item for item in (_action_from_classification(record) for record in input_action_classifications) if item)
    raw_actions.extend(item for item in (_action_from_target_quality(record) for record in target_match_quality) if item)
    raw_actions.extend(item for item in (_action_from_menu_interaction(record) for record in menu_interactions) if item)
    for snapshot in snapshots:
        raw_actions.extend(item for item in (_action_from_menu_entry(entry, snapshot) for entry in snapshot.get("menuEntries") or []) if item)
    actions: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for action in raw_actions:
        key = _action_key(action)
        if key in seen:
            continue
        seen.add(key)
        actions.append(action)
    return actions


def _is_bank_action(action: dict[str, Any]) -> bool:
    return _is_bankish_name_or_action(action.get("target"), [action.get("option")])


def _is_deposit_action(action: dict[str, Any]) -> bool:
    option = _lower(action.get("option"))
    target = _lower(action.get("target"))
    return "deposit" in option or "deposit" in target


def _is_withdraw_action(action: dict[str, Any]) -> bool:
    option = _lower(action.get("option"))
    return "withdraw" in option or "take" in option


def _inventory_delta(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_counts = {int(key): value for key, value in _dict(before.get("itemCounts")).items() if _int(key) is not None for value in [_int(value) or 0]}
    after_counts = {int(key): value for key, value in _dict(after.get("itemCounts")).items() if _int(key) is not None for value in [_int(value) or 0]}
    names = {**_dict(before.get("itemNames")), **_dict(after.get("itemNames"))}
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(before_counts) | set(after_counts)):
        before_count = before_counts.get(item_id, 0)
        after_count = after_counts.get(item_id, 0)
        delta = after_count - before_count
        if delta == 0:
            continue
        changes.append(
            {
                "id": item_id,
                "name": names.get(str(item_id)) or ITEM_NAMES.get(item_id) or f"item {item_id}",
                "before": before_count,
                "after": after_count,
                "delta": delta,
            }
        )
    return changes


def _bank_delta(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    if not before.get("containerAvailable") or not after.get("containerAvailable"):
        return []
    before_counts = {int(key): value for key, value in _dict(before.get("itemCounts")).items() if _int(key) is not None for value in [_int(value) or 0]}
    after_counts = {int(key): value for key, value in _dict(after.get("itemCounts")).items() if _int(key) is not None for value in [_int(value) or 0]}
    names = {**_dict(before.get("itemNames")), **_dict(after.get("itemNames"))}
    changes: list[dict[str, Any]] = []
    for item_id in sorted(set(before_counts) | set(after_counts)):
        before_count = before_counts.get(item_id, 0)
        after_count = after_counts.get(item_id, 0)
        delta = after_count - before_count
        if delta == 0:
            continue
        changes.append(
            {
                "id": item_id,
                "name": names.get(str(item_id)) or ITEM_NAMES.get(item_id) or f"item {item_id}",
                "before": before_count,
                "after": after_count,
                "delta": delta,
            }
        )
    return changes


def _bank_delta_from_snapshots(bank_snapshots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None, str]:
    explicit: list[dict[str, Any]] = []
    explicit_source: dict[str, Any] | None = None
    for snapshot in bank_snapshots:
        delta = _dict(snapshot.get("bankContainerDelta"))
        items = [item for item in _list(delta.get("changedItems")) if _int(_dict(item).get("delta")) not in (None, 0)]
        if items:
            explicit = items
            explicit_source = snapshot
    if explicit:
        return explicit, explicit_source, explicit_source, "plugin_bank_container_delta"

    available = [snapshot for snapshot in bank_snapshots if snapshot.get("containerAvailable") and _dict(snapshot.get("itemCounts"))]
    if len(available) < 2:
        return [], available[0] if available else None, available[-1] if available else None, "missing_or_single_bank_container_snapshot"
    before = available[0]
    after = available[-1]
    return _bank_delta(before, after), before, after, "recorded_bank_snapshot_diff"


def _event_record(event_type: str, *, snapshot: dict[str, Any] | None = None, action: dict[str, Any] | None = None, evidence: list[str] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    snap = _dict(snapshot)
    act = _dict(action)
    return {
        key: value
        for key, value in {
            "eventType": event_type,
            "tick": _first(snap.get("tick"), act.get("tick")),
            "time": _first(snap.get("elapsedSeconds"), act.get("elapsedSeconds")),
            "wallTimeUtc": _first(snap.get("wallTimeUtc"), act.get("wallTimeUtc")),
            "inputEventSeq": act.get("eventSeq"),
            "option": act.get("option"),
            "target": act.get("target"),
            "source": act.get("source"),
            "evidence": evidence or [],
            "warnings": warnings or [],
        }.items()
        if value not in (None, "", [], {})
    }


def _preferred_click_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((action for action in actions if str(action.get("eventKind") or "").lower() == "click"), None) or (actions[0] if actions else None)


def _item_from_change(change: dict[str, Any], *, source: str) -> dict[str, Any]:
    quantity = abs(_int(change.get("delta")) or 0)
    return {
        "id": change.get("id"),
        "name": change.get("name"),
        "quantity": quantity,
        "before": change.get("before"),
        "after": change.get("after"),
        "source": source,
    }


def _confirmation_level(
    *,
    deposited_items: list[dict[str, Any]],
    withdrawn_items: list[dict[str, Any]],
    bank_changes: list[dict[str, Any]],
    bank_open_direct: bool,
    deposit_action_seen: bool,
    withdraw_action_seen: bool,
) -> str:
    if bank_changes and (deposited_items or withdrawn_items):
        return "bank_container_delta_confirmed"
    if (deposit_action_seen or withdraw_action_seen) and bank_open_direct and (deposited_items or withdrawn_items):
        return "combined"
    if bank_open_direct and (deposited_items or withdrawn_items):
        return "bank_open_plus_inventory"
    if deposit_action_seen or withdraw_action_seen:
        return "widget_action_confirmed"
    if deposited_items or withdrawn_items:
        return "inventory_only"
    return "none"


def _schema_gap(
    *,
    bank_ui_present: bool,
    bank_target_seen: bool,
    deposit_action_seen: bool,
    bank_open_direct: bool,
    bank_root_seen: bool,
    bank_container_available: bool,
    inventory_before: dict[str, Any],
    inventory_after: dict[str, Any],
    inventory_changes: list[dict[str, Any]],
    bank_changes: list[dict[str, Any]],
) -> dict[str, list[str]]:
    present: list[str] = []
    present_but_weak: list[str] = []
    computable: list[str] = []
    bridge: list[str] = []
    review: list[str] = []
    analyzer_gap: list[str] = []

    if bank_ui_present:
        present.append("bank_ui live-cache payload")
    else:
        bridge.append("bank_ui live-cache payload in recorded source snapshots")
    if bank_target_seen:
        present.append("bank/deposit-box target evidence")
    else:
        review.append("bank/deposit-box target evidence")
    if deposit_action_seen:
        present.append("deposit action/menu context")
    else:
        review.append("deposit action/menu context")
    if bank_open_direct:
        present.append("bankOpen/depositBoxOpen")
    else:
        bridge.append("bankOpen/depositBoxOpen in recorded source snapshots")
        present_but_weak.append("bank open inferred from deposit action and inventory delta")
    if bank_root_seen:
        present.append("bank widget/root visibility")
    else:
        bridge.append("bank widget/root visibility in recorded source snapshots")
    if bank_container_available:
        present.append("bank container summary/items")
    else:
        bridge.append("bank container contents and item-slot deltas")
    if inventory_before and inventory_after:
        present.append("inventory before/after")
    else:
        review.append("inventory before/after")
    if any(change.get("id") == 1511 for change in inventory_changes):
        computable.append("deposited normal logs itemId 1511 from inventory delta")
    if inventory_changes:
        computable.append("inventory changed item counts")
    if bank_changes:
        present.append("bank contents changing")
    else:
        bridge.append("bank item count change after deposit/withdraw")
    analyzer_gap.append("classify Deposit-All menu-context click as banking even when region classifier says minimap_click")
    return {
        "present": sorted(set(present)),
        "present_but_weak": sorted(set(present_but_weak)),
        "computable_in_python": sorted(set(computable)),
        "requires_bridge_export": sorted(set(bridge)),
        "needs_manual_review": sorted(set(review)),
        "analyzer_gap": sorted(set(analyzer_gap)),
    }


def analyze_data(
    *,
    events: list[dict[str, Any]] | None = None,
    input_action_classifications: list[dict[str, Any]] | None = None,
    target_match_quality: list[dict[str, Any]] | None = None,
    menu_interactions: list[dict[str, Any]] | None = None,
    summaries: dict[str, Any] | None = None,
    recording_path: str | Path | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if events is None and recording_path:
        events, load_warnings = _load_events(recording_path)
    else:
        events = list(events or [])
        load_warnings = []
    recording = Path(recording_path) if recording_path else None
    if recording and recording.is_dir():
        input_action_classifications = input_action_classifications or _load_jsonl(recording / "input_action_classifications.jsonl")
        target_match_quality = target_match_quality or _load_jsonl(recording / "target_match_quality.jsonl")
        menu_interactions = menu_interactions or _load_jsonl(recording / "menu_interactions.jsonl")
    input_action_classifications = list(input_action_classifications or [])
    target_match_quality = list(target_match_quality or [])
    menu_interactions = list(menu_interactions or [])
    warnings_out = list(warnings or []) + load_warnings
    snapshots = sorted(
        [_snapshot_from_event(event) for event in events if event.get("event_type") == "source_snapshot"],
        key=lambda snapshot: (_float(snapshot.get("elapsedSeconds")) is None, _float(snapshot.get("elapsedSeconds")) or 0.0),
    )
    actions = _collect_actions(snapshots, input_action_classifications, target_match_quality, menu_interactions)
    bank_actions = [action for action in actions if _is_bank_action(action)]
    deposit_actions = [action for action in actions if _is_deposit_action(action)]
    withdraw_actions = [action for action in actions if _is_withdraw_action(action)]
    inventory_snapshots = [snapshot.get("inventory") for snapshot in snapshots if _dict(snapshot.get("inventory")).get("known")]
    bank_snapshots = [snapshot.get("bank") for snapshot in snapshots if _dict(snapshot.get("bank")).get("present")]
    bank_ui_present = bool(bank_snapshots)
    bank_ui_sources = [_dict(_dict(snapshot.get("bank")).get("source")) for snapshot in snapshots if _dict(_dict(snapshot.get("bank")).get("source"))]
    bank_ui_stale_sources = [source for source in bank_ui_sources if source.get("stale")]
    bank_target_snapshots = [snapshot for snapshot in snapshots if snapshot.get("bankTargets")]
    first_inventory = _dict(inventory_snapshots[0] if inventory_snapshots else {})
    last_inventory = _dict(inventory_snapshots[-1] if inventory_snapshots else {})
    first_bank = _dict(bank_snapshots[0] if bank_snapshots else {})
    last_bank = _dict(bank_snapshots[-1] if bank_snapshots else {})
    inventory_changes = _inventory_delta(first_inventory, last_inventory) if first_inventory and last_inventory else []
    bank_changes, bank_delta_before, bank_delta_after, bank_delta_source = _bank_delta_from_snapshots([_dict(item) for item in bank_snapshots])
    free_before = _int(first_inventory.get("freeSlots"))
    free_after = _int(last_inventory.get("freeSlots"))
    free_delta = free_after - free_before if free_before is not None and free_after is not None else None
    normal_before = _int(first_inventory.get("normalLogs"))
    normal_after = _int(last_inventory.get("normalLogs"))
    ticks = [_int(snapshot.get("tick")) for snapshot in snapshots if _int(snapshot.get("tick")) is not None]
    duration = None
    stop = next((event for event in reversed(events) if event.get("event_type") == "recording_stop"), None)
    if stop:
        duration = _first(_dict(stop).get("duration_seconds"), _dict(stop).get("elapsed_seconds"))
    if duration is None and len(snapshots) >= 2:
        duration = (_float(snapshots[-1].get("elapsedSeconds")) or 0.0) - (_float(snapshots[0].get("elapsedSeconds")) or 0.0)

    bank_open_direct = any(_dict(snapshot.get("bank")).get("bankOpen") is True or _dict(snapshot.get("bank")).get("depositBoxOpen") is True for snapshot in snapshots)
    bank_root_seen = any(_dict(snapshot.get("bank")).get("bankRootVisible") is True for snapshot in snapshots)
    deposit_box_open_seen = any(_dict(snapshot.get("bank")).get("depositBoxOpen") is True for snapshot in snapshots)
    bank_container_available = any(_dict(snapshot.get("bank")).get("containerAvailable") for snapshot in snapshots)
    deposit_button_seen = any(_dict(snapshot.get("bank")).get("depositInventoryButtonVisible") is True for snapshot in snapshots)
    bank_target_seen = bool(bank_actions or bank_target_snapshots)
    deposit_action_seen = bool(deposit_actions)
    withdraw_action_seen = bool(withdraw_actions)

    deposited_items: list[dict[str, Any]] = []
    withdrawn_items: list[dict[str, Any]] = []
    for change in inventory_changes:
        delta = _int(change.get("delta")) or 0
        bank_match = next((item for item in bank_changes if item.get("id") == change.get("id") and (_int(item.get("delta")) or 0) == -delta), None)
        if delta < 0 and (deposit_action_seen or bank_match or bank_target_seen):
            source_parts = ["inventory_delta"]
            if bank_match:
                source_parts.append("bank_delta")
            if deposit_action_seen:
                source_parts.append("menu_action")
            deposited_items.append(_item_from_change(change, source="|".join(source_parts)))
        elif delta > 0 and (withdraw_action_seen or bank_match):
            source_parts = ["inventory_delta"]
            if bank_match:
                source_parts.append("bank_delta")
            if withdraw_action_seen:
                source_parts.append("menu_action")
            withdrawn_items.append(_item_from_change(change, source="|".join(source_parts)))

    evidence: list[str] = []
    if bank_target_seen:
        target_names = sorted({_plain(_first(action.get("target"), action.get("name"))) for action in bank_actions if _first(action.get("target"), action.get("name"))})
        candidate_names = sorted({_plain(target.get("name")) for snapshot in bank_target_snapshots for target in snapshot.get("bankTargets") or [] if target.get("name")})
        names = (target_names + candidate_names)[:6]
        evidence.append(f"bank/deposit target evidence observed: {names}")
    if deposit_action_seen:
        first = _preferred_click_action(deposit_actions) or deposit_actions[0]
        evidence.append(f"deposit action observed: {first.get('option')} {first.get('target')} from {first.get('source')}")
    if inventory_changes:
        changed = [
            f"{item.get('name')} {item.get('before')}->{item.get('after')}"
            for item in inventory_changes[:5]
        ]
        evidence.append(f"inventory changed items: {changed}")
    if free_delta is not None:
        evidence.append(f"free slots changed {free_before} -> {free_after} ({free_delta:+d})")
    if normal_before is not None and normal_after is not None:
        evidence.append(f"normal logs itemId 1511 changed {normal_before} -> {normal_after}")
    if bank_open_direct:
        evidence.append("bankOpen/depositBoxOpen was directly observed")
    if bank_container_available:
        evidence.append("bank container was directly observed")
    if bank_ui_present:
        evidence.append("bank_ui live-cache payload was preserved in source snapshots")
    if bank_changes:
        changed = [
            f"{item.get('name')} {item.get('before')}->{item.get('after')}"
            for item in bank_changes[:5]
        ]
        evidence.append(f"bank container changed items: {changed} ({bank_delta_source})")

    events_out: list[dict[str, Any]] = []
    if bank_actions:
        events_out.append(_event_record("bank_target_click", action=_preferred_click_action(bank_actions), evidence=["bank/deposit action or target"]))
    if bank_open_direct:
        snapshot = next((snapshot for snapshot in snapshots if _dict(snapshot.get("bank")).get("bankOpen") is True or _dict(snapshot.get("bank")).get("depositBoxOpen") is True), None)
        events_out.append(_event_record("bank_opened", snapshot=snapshot, evidence=["bankOpen/depositBoxOpen true"]))
    elif bank_target_seen and (deposit_action_seen or deposited_items):
        events_out.append(_event_record("bank_opened", snapshot=snapshots[0] if snapshots else None, evidence=["inferred from banking action and inventory delta"], warnings=["bank open was not directly observed"]))
    if deposit_actions:
        events_out.append(_event_record("deposit_action", action=_preferred_click_action(deposit_actions), evidence=["deposit option/menu context"]))
    if inventory_changes:
        events_out.append(_event_record("inventory_changed", snapshot=snapshots[-1] if snapshots else None, evidence=["inventory item/free-slot delta"]))
    if bank_changes:
        events_out.append(_event_record("bank_container_changed", snapshot=snapshots[-1] if snapshots else None, evidence=["bank container delta"]))
    if bank_open_direct and snapshots:
        later_closed = next((snapshot for snapshot in snapshots if _dict(snapshot.get("bank")).get("bankOpen") is False and _dict(snapshot.get("bank")).get("depositBoxOpen") is not True), None)
        if later_closed:
            events_out.append(_event_record("bank_closed", snapshot=later_closed, evidence=["bankOpen false after being open"]))

    missing_capabilities: list[str] = []
    if not bank_ui_present:
        missing_capabilities.append("banking.bank_ui")
        warnings_out.append("bank_ui live-cache payload was not present in the recording; banking state is inferred from other evidence.")
    if bank_ui_stale_sources:
        warnings_out.append("bank_ui live-cache payload was present but stale in at least one source snapshot.")
    if not bank_open_direct:
        missing_capabilities.append("banking.bankOpen_or_depositBoxOpen")
        warnings_out.append("Bank open/closed state was not directly observed in the recording.")
    if not bank_root_seen:
        missing_capabilities.append("banking.bankWidgetRoot")
        warnings_out.append("Bank widget/root visibility was not directly observed.")
    if not bank_container_available:
        missing_capabilities.append("banking.bankContainer.items")
        warnings_out.append("Bank container contents were not directly observed.")
    if not bank_changes:
        missing_capabilities.append("banking.bankContainer.delta")
        if deposited_items or withdrawn_items:
            warnings_out.append("bankContainer.delta missing; deposit/withdraw was confirmed by direct bank state plus inventory delta, but bank item-count change was not directly proven.")
    if deposited_items and not bank_container_available:
        warnings_out.append("Deposit was inferred from inventory/menu evidence because bank container telemetry was missing.")
    if any(action.get("classification") == "minimap_click" and _is_deposit_action(action) for action in deposit_actions):
        warnings_out.append("Deposit-All click carried banking menu context but the input classifier labeled the screen region as minimap_click.")

    signals = sum(
        bool(value)
        for value in (
            bank_target_seen,
            bank_open_direct,
            bank_root_seen,
            bank_container_available,
            deposit_action_seen,
            withdraw_action_seen,
            deposited_items,
            withdrawn_items,
            inventory_changes,
        )
    )
    if signals == 0:
        status = "FAIL"
        phase = "unknown"
        warnings_out.append("No banking lifecycle signals were found.")
    elif (deposited_items or withdrawn_items) and bank_open_direct and bank_container_available:
        status = "PASS"
        phase = "complete"
    elif deposited_items or withdrawn_items:
        status = "WARN"
        phase = "complete"
    elif bank_open_direct:
        status = "PASS"
        phase = "bank_open"
    else:
        status = "WARN"
        phase = "partial"

    confidence = 0.0
    if signals:
        confidence = 0.2
        if bank_target_seen:
            confidence += 0.1
        if bank_open_direct:
            confidence += 0.2
        if bank_root_seen:
            confidence += 0.1
        if bank_container_available:
            confidence += 0.15
        if deposit_action_seen or withdraw_action_seen:
            confidence += 0.15
        if deposited_items or withdrawn_items:
            confidence += 0.2
        if bank_changes:
            confidence += 0.1

    deposit_confirmation_level = _confirmation_level(
        deposited_items=deposited_items,
        withdrawn_items=withdrawn_items,
        bank_changes=bank_changes,
        bank_open_direct=bank_open_direct,
        deposit_action_seen=deposit_action_seen,
        withdraw_action_seen=withdraw_action_seen,
    )
    deposited_confirmation = [
        {
            **item,
            "confirmationLevel": "bank_container_delta_confirmed"
            if any(change.get("id") == item.get("id") and (_int(change.get("delta")) or 0) > 0 for change in bank_changes)
            else deposit_confirmation_level,
        }
        for item in deposited_items
    ]
    withdrawn_confirmation = [
        {
            **item,
            "confirmationLevel": "bank_container_delta_confirmed"
            if any(change.get("id") == item.get("id") and (_int(change.get("delta")) or 0) < 0 for change in bank_changes)
            else deposit_confirmation_level,
        }
        for item in withdrawn_items
    ]

    bank_like_interface = "unknown"
    deposit_box_action_seen = any("deposit box" in _lower(action.get("target")) for action in bank_actions)
    bank_booth_action_seen = any(
        any(token in _lower(action.get("target")) for token in ("bank booth", "banker", "bank chest"))
        for action in bank_actions
    )
    if deposit_box_open_seen or (deposit_box_action_seen and not bank_booth_action_seen):
        bank_like_interface = "deposit_box"
    elif bank_open_direct or bank_target_seen:
        bank_like_interface = "bank"
    elif signals == 0:
        bank_like_interface = "none"

    schema_gap = _schema_gap(
        bank_ui_present=bank_ui_present,
        bank_target_seen=bank_target_seen,
        deposit_action_seen=deposit_action_seen,
        bank_open_direct=bank_open_direct,
        bank_root_seen=bank_root_seen,
        bank_container_available=bank_container_available,
        inventory_before=first_inventory,
        inventory_after=last_inventory,
        inventory_changes=inventory_changes,
        bank_changes=bank_changes,
    )

    lifecycle = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "phase": phase,
        "confidence": round(min(0.95, confidence), 3),
        "bankLikeInterface": bank_like_interface,
        "durationSeconds": duration,
        "tickRange": {"start": min(ticks) if ticks else None, "end": max(ticks) if ticks else None},
        "events": events_out,
        "inventory": {
            "before": first_inventory,
            "after": last_inventory,
            "freeSlotsBefore": free_before,
            "freeSlotsAfter": free_after,
            "freeSlotDelta": free_delta,
            "normalLogsBefore": normal_before,
            "normalLogsAfter": normal_after,
            "changedItems": inventory_changes,
        },
        "bank": {
            "openSeen": bool(bank_open_direct),
            "depositBoxOpenSeen": bool(deposit_box_open_seen),
            "widgetRootSeen": bool(bank_root_seen),
            "depositButtonSeen": bool(deposit_button_seen),
            "containerAvailable": bool(bank_container_available),
            "bankUiPresent": bool(bank_ui_present),
            "bankUiSnapshotCount": len(bank_snapshots),
            "bankUiFreshness": {
                "sourceCount": len(bank_ui_sources),
                "staleSourceCount": len(bank_ui_stale_sources),
                "latestTick": next((source.get("latestTick") for source in reversed(bank_ui_sources) if source.get("latestTick") is not None), None),
                "latestExportSequence": next((source.get("latestExportSequence") for source in reversed(bank_ui_sources) if source.get("latestExportSequence") is not None), None),
                "latestAgeSeconds": next((source.get("ageSeconds") for source in reversed(bank_ui_sources) if source.get("ageSeconds") is not None), None),
                "sourceKind": next((source.get("sourceKind") for source in reversed(bank_ui_sources) if source.get("sourceKind")), None),
            },
            "before": first_bank,
            "after": last_bank,
            "deltaBefore": bank_delta_before,
            "deltaAfter": bank_delta_after,
            "bankContainerDeltaAvailable": bool(bank_changes),
            "bankContainerDeltaSource": bank_delta_source,
            "changedItems": bank_changes,
            "targetEvidence": [target for snapshot in bank_target_snapshots for target in snapshot.get("bankTargets") or []][:10],
        },
        "actions": {
            "bankTargetActions": bank_actions[:20],
            "depositActions": deposit_actions[:20],
            "withdrawActions": withdraw_actions[:20],
            "depositActionCount": len(deposit_actions),
            "withdrawActionCount": len(withdraw_actions),
            "bankActionCount": len(bank_actions),
        },
        "deposit": {
            "detected": bool(deposited_items),
            "items": deposited_confirmation,
            "totalDepositedCount": sum(_int(item.get("quantity")) or 0 for item in deposited_items),
            "confirmationLevel": deposit_confirmation_level if deposited_items else "none",
        },
        "withdraw": {
            "detected": bool(withdrawn_items),
            "items": withdrawn_confirmation,
            "totalWithdrawnCount": sum(_int(item.get("quantity")) or 0 for item in withdrawn_items),
            "confirmationLevel": deposit_confirmation_level if withdrawn_items else "none",
        },
        "depositConfirmationLevel": deposit_confirmation_level,
        "bankContainerDeltaAvailable": bool(bank_changes),
        "depositedItemsConfirmation": deposited_confirmation,
        "withdrawnItemsConfirmation": withdrawn_confirmation,
        "schemaGap": schema_gap,
        "warnings": sorted(set(str(warning) for warning in warnings_out if warning)),
        "missingCapabilities": sorted(set(missing_capabilities)),
        "evidence": evidence,
    }
    if recording:
        lifecycle["recordingPath"] = str(recording)
    return lifecycle


def analyze_recording(path: str | Path) -> dict[str, Any]:
    return analyze_data(recording_path=path)


def analyze_context(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = _snapshot_from_context(context)
    return _single_snapshot_lifecycle(snapshot)


def _single_snapshot_lifecycle(snapshot: dict[str, Any]) -> dict[str, Any]:
    bank = _dict(snapshot.get("bank"))
    inventory = _dict(snapshot.get("inventory"))
    bank_open = bank.get("bankOpen") is True or bank.get("depositBoxOpen") is True
    container_available = bool(bank.get("containerAvailable"))
    bank_targets = snapshot.get("bankTargets") or []
    warnings: list[str] = []
    missing: list[str] = []
    if not bank_open:
        missing.append("banking.bankOpen_or_depositBoxOpen")
    if not container_available:
        missing.append("banking.bankContainer.items")
    if missing:
        warnings.append("Direct banking state is incomplete in the current context.")
    status = "PASS" if bank_open and container_available else ("WARN" if bank_open or bank_targets or inventory else "FAIL")
    return {
        "schema": SCHEMA_VERSION,
        "status": status,
        "phase": "bank_open" if bank_open else ("partial" if bank_targets or inventory else "unknown"),
        "confidence": 0.7 if bank_open else (0.35 if bank_targets or inventory else 0.0),
        "bankLikeInterface": "deposit_box" if bank.get("depositBoxOpen") is True else ("bank" if bank_open else "unknown"),
        "durationSeconds": 0.0,
        "tickRange": {"start": snapshot.get("tick"), "end": snapshot.get("tick")},
        "events": [_event_record("bank_opened", snapshot=snapshot, evidence=["current bankOpen/depositBoxOpen true"])] if bank_open else [],
        "inventory": {
            "before": inventory,
            "after": inventory,
            "freeSlotsBefore": inventory.get("freeSlots"),
            "freeSlotsAfter": inventory.get("freeSlots"),
            "freeSlotDelta": 0,
            "normalLogsBefore": inventory.get("normalLogs"),
            "normalLogsAfter": inventory.get("normalLogs"),
            "changedItems": [],
        },
        "bank": {
            "openSeen": bank_open,
            "depositBoxOpenSeen": bank.get("depositBoxOpen") is True,
            "widgetRootSeen": bank.get("bankRootVisible") is True,
            "depositButtonSeen": bank.get("depositInventoryButtonVisible") is True,
            "containerAvailable": container_available,
            "bankUiPresent": bool(bank.get("present")),
            "bankUiSnapshotCount": 1 if bank.get("present") else 0,
            "bankUiFreshness": _dict(bank.get("source")),
            "before": bank,
            "after": bank,
            "changedItems": [],
            "targetEvidence": bank_targets[:10],
        },
        "actions": {"bankTargetActions": [], "depositActions": [], "withdrawActions": [], "depositActionCount": 0, "withdrawActionCount": 0, "bankActionCount": 0},
        "deposit": {"detected": False, "items": [], "totalDepositedCount": 0},
        "withdraw": {"detected": False, "items": [], "totalWithdrawnCount": 0},
        "depositConfirmationLevel": "none",
        "bankContainerDeltaAvailable": False,
        "depositedItemsConfirmation": [],
        "withdrawnItemsConfirmation": [],
        "schemaGap": _schema_gap(
            bank_ui_present=bool(bank.get("present")),
            bank_target_seen=bool(bank_targets),
            deposit_action_seen=False,
            bank_open_direct=bank_open,
            bank_root_seen=bank.get("bankRootVisible") is True,
            bank_container_available=container_available,
            inventory_before=inventory,
            inventory_after=inventory,
            inventory_changes=[],
            bank_changes=[],
        ),
        "warnings": warnings,
        "missingCapabilities": missing,
        "evidence": ["current banking context snapshot analyzed"] if bank_open or bank_targets or inventory else [],
    }


def compact_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    bank = _dict(lifecycle.get("bank"))
    deposit = _dict(lifecycle.get("deposit"))
    withdraw = _dict(lifecycle.get("withdraw"))
    inventory = _dict(lifecycle.get("inventory"))
    return {
        "schema": lifecycle.get("schema") or SCHEMA_VERSION,
        "status": lifecycle.get("status"),
        "phase": lifecycle.get("phase"),
        "confidence": lifecycle.get("confidence"),
        "bankLikeInterface": lifecycle.get("bankLikeInterface"),
        "bankOpenSeen": bank.get("openSeen"),
        "depositBoxOpenSeen": bank.get("depositBoxOpenSeen"),
        "bankWidgetRootSeen": bank.get("widgetRootSeen"),
        "bankContainerAvailable": bank.get("containerAvailable"),
        "bankContainerDeltaAvailable": lifecycle.get("bankContainerDeltaAvailable", bank.get("bankContainerDeltaAvailable")),
        "bankContainerDeltaSource": bank.get("bankContainerDeltaSource"),
        "bankUiPresent": bank.get("bankUiPresent"),
        "bankUiSnapshotCount": bank.get("bankUiSnapshotCount"),
        "bankUiFreshness": bank.get("bankUiFreshness"),
        "depositDetected": deposit.get("detected"),
        "withdrawDetected": withdraw.get("detected"),
        "depositedItemCount": deposit.get("totalDepositedCount") or 0,
        "withdrawnItemCount": withdraw.get("totalWithdrawnCount") or 0,
        "depositedItems": deposit.get("items") or [],
        "withdrawnItems": withdraw.get("items") or [],
        "depositConfirmationLevel": lifecycle.get("depositConfirmationLevel") or deposit.get("confirmationLevel"),
        "depositedItemsConfirmation": lifecycle.get("depositedItemsConfirmation") or deposit.get("items") or [],
        "withdrawnItemsConfirmation": lifecycle.get("withdrawnItemsConfirmation") or withdraw.get("items") or [],
        "freeSlotsBefore": inventory.get("freeSlotsBefore"),
        "freeSlotsAfter": inventory.get("freeSlotsAfter"),
        "freeSlotDelta": inventory.get("freeSlotDelta"),
        "normalLogsBefore": inventory.get("normalLogsBefore"),
        "normalLogsAfter": inventory.get("normalLogsAfter"),
        "missingCapabilities": lifecycle.get("missingCapabilities") or [],
        "warnings": lifecycle.get("warnings") or [],
    }
