from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import brain_core
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "inventory_slot_diagnostic.v1"
LIVE_DIR = Path("interaction_geometry") / "live"
LOG_RESOURCE_ORDER = [
    "normal_logs",
    "oak_logs",
    "willow_logs",
    "maple_logs",
    "yew_logs",
    "magic_logs",
    "woodcutting_logs",
]


def safe_load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_session(args: argparse.Namespace) -> Path:
    if args.session:
        session = Path(args.session).expanduser()
        if not session.exists():
            raise RuntimeError(f"Session does not exist: {session}")
        return session.resolve()
    if not args.latest_session:
        raise RuntimeError("Pass --session or --latest-session.")
    session = find_newest_session(get_sessions_dir(args.sessions_dir))
    if session is None:
        raise RuntimeError(f"No sessions found in: {get_sessions_dir(args.sessions_dir)}")
    return session.resolve()


def first_inventory_from(*docs: dict) -> tuple[dict, str]:
    for source, doc in docs:
        if not isinstance(doc, dict):
            continue
        for key in ("inventoryState", "inventory"):
            inventory = doc.get(key)
            if isinstance(inventory, dict) and inventory:
                return inventory, source
        values = doc.get("valuesByAlias") if isinstance(doc.get("valuesByAlias"), dict) else {}
        summary = values.get("inventory_summary") if isinstance(values.get("inventory_summary"), dict) else {}
        value = summary.get("value") if isinstance(summary.get("value"), dict) else {}
        if value:
            return value, source
    return {}, "none"


def item_slot(item: dict) -> int | None:
    return brain_core.as_int(item.get("slot"))


def normalized_items(inventory: dict) -> list[dict]:
    items = inventory.get("items") if isinstance(inventory.get("items"), list) else []
    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = brain_core.inventory_item_id(item)
        if item_id is None:
            continue
        normalized.append(
            {
                "slot": item_slot(item),
                "itemId": item_id,
                "quantity": brain_core.inventory_item_quantity(item),
            }
        )
    normalized.sort(key=lambda item: (item.get("slot") is None, item.get("slot"), item.get("itemId")))
    return normalized


def resource_definitions() -> dict[str, dict]:
    registry = brain_core.load_task_resources()
    task = (registry.get("tasks") or {}).get("woodcutting") if isinstance(registry.get("tasks"), dict) else {}
    resources = task.get("resources") if isinstance(task, dict) and isinstance(task.get("resources"), dict) else {}
    groups = task.get("resourceGroups") if isinstance(task, dict) and isinstance(task.get("resourceGroups"), dict) else {}
    combined: dict[str, dict] = {}
    combined.update(resources)
    combined.update(groups)
    return combined


def resource_counts(inventory: dict, resource_id: str) -> dict:
    definitions = resource_definitions()
    selected_ids = list(LOG_RESOURCE_ORDER)
    if resource_id and resource_id not in selected_ids:
        selected_ids.append(resource_id)
    counts: dict[str, dict] = {}
    for key in selected_ids:
        definition = definitions.get(key)
        if not isinstance(definition, dict):
            continue
        result = brain_core.count_inventory_items(inventory, definition.get("itemIds") or [])
        counts[key] = {
            "displayName": definition.get("displayName") or key,
            "itemIds": definition.get("itemIds") or [],
            "count": result.get("count") if result.get("known") else None,
            "known": bool(result.get("known")),
            "matchedSlots": sorted(
                slot
                for slot in (brain_core.as_int(item.get("slot")) for item in result.get("matchedItems") or [])
                if slot is not None
            ),
            "matchedItems": result.get("matchedItems") or [],
            "missingReason": result.get("missingReason"),
            "source": result.get("source"),
        }
    return counts


def slot_table(inventory: dict, resources: dict[str, dict]) -> list[dict]:
    items = normalized_items(inventory)
    slot_count = brain_core.as_int(inventory.get("inventorySlotCount"))
    if slot_count is None:
        slot_count = brain_core.as_int(inventory.get("slotCount"))
    if slot_count is None:
        slot_count = 28
    by_slot: dict[int, list[dict]] = {}
    for item in items:
        slot = item.get("slot")
        if slot is None:
            continue
        by_slot.setdefault(slot, []).append(item)
    item_id_to_resource: dict[int, str] = {}
    for resource_id, definition in resource_definitions().items():
        for item_id in definition.get("itemIds") or []:
            parsed = brain_core.as_int(item_id)
            if parsed is not None and resource_id != "woodcutting_logs":
                item_id_to_resource.setdefault(parsed, resource_id)
    table: list[dict] = []
    for slot in range(max(0, slot_count)):
        entries = by_slot.get(slot) or []
        item = entries[0] if entries else {}
        item_id = item.get("itemId")
        resource_name = item_id_to_resource.get(item_id)
        table.append(
            {
                "slot": slot,
                "itemId": item_id,
                "quantity": item.get("quantity"),
                "resourceMatch": bool(resource_name),
                "resourceName": resource_name,
                "duplicateEntries": len(entries) if len(entries) > 1 else 0,
            }
        )
    return table


def diagnose(session: Path, resource_id: str) -> dict:
    live_dir = session / LIVE_DIR
    baseline = safe_load_json(live_dir / "live_baseline_state.json")
    activity = safe_load_json(live_dir / "live_activity_state.json")
    watch_values = safe_load_json(live_dir / "live_watch_values.json")
    inventory, source = first_inventory_from(("live_activity_state.json", activity), ("live_baseline_state.json", baseline), ("live_watch_values.json", watch_values))
    items = normalized_items(inventory)
    slot_diagnostics = brain_core.inventory_slot_diagnostics({**inventory, "items": items})
    counts = resource_counts({**inventory, "items": items}, resource_id)
    filled_slots = brain_core.as_int(inventory.get("filledSlots"))
    free_slots = brain_core.as_int(inventory.get("freeSlots"))
    slot_count = brain_core.as_int(inventory.get("inventorySlotCount"))
    if slot_count is None:
        slot_count = brain_core.as_int(inventory.get("slotCount"))
    if slot_count is None and filled_slots is not None and free_slots is not None:
        slot_count = max(28, filled_slots + free_slots)
    conclusion = "no issue detected"
    if not inventory:
        conclusion = "inventory state unavailable"
    elif slot_diagnostics.get("invalidSlots"):
        conclusion = "compact inventory contains invalid slot indexes"
    elif slot_diagnostics.get("duplicateSlots"):
        conclusion = "compact inventory contains duplicate slot entries"
    elif slot_diagnostics.get("warnings"):
        conclusion = "inventory summary is inconsistent"
    elif slot_count == 28:
        conclusion = "all 28 inventory slots are represented by slot indexes; empty slots may be omitted from items"
    return {
        "schema": SCHEMA,
        "sessionPath": str(session),
        "latestTick": activity.get("latestTick") or baseline.get("latestTick") or watch_values.get("latestTick"),
        "inventorySource": source,
        "inventoryKnown": bool(inventory),
        "inventorySlotCount": slot_count,
        "filledSlots": filled_slots,
        "freeSlots": free_slots,
        "inventoryFull": inventory.get("inventoryFull"),
        "slotTable": slot_table({**inventory, "items": items}, counts),
        "resourceCounts": counts,
        "missingSlots": slot_diagnostics.get("emptyOrMissingSlots") or [],
        "duplicateSlots": slot_diagnostics.get("duplicateSlots") or [],
        "invalidSlots": slot_diagnostics.get("invalidSlots") or [],
        "warnings": slot_diagnostics.get("warnings") or [],
        "conclusion": conclusion,
    }


def format_human(payload: dict, resource_id: str) -> str:
    lines = [
        "INVENTORY SLOT DIAGNOSTIC",
        "",
        f"Session: {payload.get('sessionPath')}",
        f"Latest tick: {payload.get('latestTick')}",
        f"Inventory source: {payload.get('inventorySource')}",
        f"Slots: {payload.get('inventorySlotCount')} total, {payload.get('filledSlots')} filled, {payload.get('freeSlots')} free",
        f"Inventory full: {payload.get('inventoryFull')}",
        "",
        "Resource counts:",
    ]
    for key in LOG_RESOURCE_ORDER:
        record = (payload.get("resourceCounts") or {}).get(key)
        if not isinstance(record, dict):
            continue
        slots = record.get("matchedSlots") or []
        slot_text = ", ".join(str(slot) for slot in slots) if slots else "none"
        lines.append(f"  {key}: {record.get('count')} (slots: {slot_text})")
    if resource_id not in LOG_RESOURCE_ORDER:
        record = (payload.get("resourceCounts") or {}).get(resource_id)
        if isinstance(record, dict):
            slots = record.get("matchedSlots") or []
            slot_text = ", ".join(str(slot) for slot in slots) if slots else "none"
            lines.append(f"  {resource_id}: {record.get('count')} (slots: {slot_text})")
    slot_count = payload.get("inventorySlotCount")
    slot_label = f"Slots 0..{int(slot_count) - 1}:" if isinstance(slot_count, int) and slot_count > 0 else "Slots:"
    lines.extend(["", slot_label])
    for row in payload.get("slotTable") or []:
        marker = "resource" if row.get("resourceMatch") else ""
        item_id = row.get("itemId") if row.get("itemId") is not None else "-"
        quantity = row.get("quantity") if row.get("quantity") is not None else "-"
        resource = row.get("resourceName") or ""
        duplicate = f" duplicateEntries={row.get('duplicateEntries')}" if row.get("duplicateEntries") else ""
        lines.append(f"  {row.get('slot'):>2}: itemId={item_id} qty={quantity} {marker} {resource}{duplicate}".rstrip())
    warnings = payload.get("warnings") or []
    lines.extend(["", "Warnings:"])
    if warnings:
        for warning in warnings:
            lines.append(f"  {warning}")
    else:
        lines.append("  none")
    lines.extend(["", f"Conclusion: {payload.get('conclusion')}"])
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose read-only inventory slot/resource counting for live telemetry.")
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when using --latest-session.")
    parser.add_argument("--latest-session", action="store_true", help="Use newest available telemetry session.")
    parser.add_argument("--resource", default="woodcutting_logs", help="Resource id to highlight. Default: woodcutting_logs.")
    parser.add_argument("--json", action="store_true", help="Print JSON diagnostic.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        session = resolve_session(args)
        payload = diagnose(session, args.resource)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print(format_human(payload, args.resource), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
