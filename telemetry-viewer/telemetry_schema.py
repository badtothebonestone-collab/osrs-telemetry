from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = "telemetry_schema.v1"
NORMALIZED_SCHEMA_VERSION = "normalized_telemetry.v1"
FIELD_SCAN_SCHEMA_VERSION = "telemetry_field_presence_scan.v1"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    aliases: tuple[str, ...]
    category: str = "needs_manual_review"
    description: str = ""


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("tick", ("latestTick", "latestTickProcessed", "tick", "tickId", "gameTick", "game_tick", "**.latestTick", "**.tickId"), "present"),
    FieldSpec("export_sequence", ("latestSequence", "compactPacketLastSequence", "**.latestSequence", "**.compactPacketLastSequence", "**.liveCacheLatestSequence", "**.sequence"), "present"),
    FieldSpec("state_freshness", ("freshness", "liveFreshness", "sourceFreshness", "**.ageMillis", "**.age_seconds", "**.stale"), "computable_in_sidecar"),
    FieldSpec("game_state", ("gameState", "baseline.gameState", "clientTickHot.gameState", "**.gameState"), "present"),
    FieldSpec("player_world_point", ("player.worldLocation", "playerWorldLocation", "localPlayer.worldLocation", "localPlayer.worldX", "**.playerWorldLocation", "**.worldX"), "present"),
    FieldSpec("player_local_point", ("player.localLocation", "player.localX", "localPlayer.localX", "**.playerLocalX", "**.localX"), "present"),
    FieldSpec("plane", ("plane", "player.plane", "localPlayer.plane", "**.plane"), "present"),
    FieldSpec("player_animation", ("player.animation", "localPlayer.animation", "**.animation"), "present"),
    FieldSpec("player_pose_animation", ("player.poseAnimation", "localPlayer.poseAnimation", "**.poseAnimation"), "present"),
    FieldSpec("destination", ("destination", "localDestination", "localDestinationLocation", "**.destination", "**.localDestination"), "requires_bridge_export"),
    FieldSpec("run_energy", ("runEnergy", "run_energy", "player.runEnergy", "status.runEnergy", "**.runEnergy"), "present"),
    FieldSpec("inventory", ("inventory", "inventoryState", "payloads.inventory", "**.inventoryState", "**.inventory"), "present"),
    FieldSpec("inventory_slots", ("inventory.items", "inventory.slots", "inventoryState.items", "**.inventorySlots", "**.slots"), "present"),
    FieldSpec("equipment", ("equipment", "equipmentState", "payloads.equipment", "**.equipment"), "present"),
    FieldSpec("equipment_slots", ("equipment.items", "equipment.slots", "**.equipmentSlots"), "present"),
    FieldSpec("bank_ui", ("bank_ui", "bankUi", "bankUI", "payloads.bank_ui", "**.bank_ui", "**.bankUi"), "present"),
    FieldSpec("bank_state", ("bank_ui", "bankUi", "bankUI", "bank", "payloads.bank_ui", "**.bankOpen", "**.depositBoxOpen", "**.bankUi"), "present"),
    FieldSpec("bank_container", ("bankContainer", "bankContainerItems", "bankWidgetItems", "bankItems", "bankUi.items", "bank_ui.bankItems", "bank_ui.bankSummary", "**.bankContainer", "**.bankContainerItems", "**.bankWidgetItems", "**.bankItems", "**.bankSummary"), "present"),
    FieldSpec("bank_open", ("bank_ui.bankOpen", "bankUi.bankOpen", "**.bankOpen"), "present"),
    FieldSpec("deposit_box_open", ("bank_ui.depositBoxOpen", "bankUi.depositBoxOpen", "**.depositBoxOpen"), "present"),
    FieldSpec("active_bank_like_interface", ("bank_ui.activeBankLikeInterface", "bankUi.activeBankLikeInterface", "**.activeBankLikeInterface"), "computable_in_sidecar"),
    FieldSpec("bank_widget_root", ("bankUi.bankRootWidget", "bank_ui.bankRootWidget", "**.bankRootWidget", "**.depositBoxWidgetRoot"), "present"),
    FieldSpec("bank_inventory_snapshot", ("bankUi.inventorySummary", "bank_ui.inventorySummary", "bank_ui.inventorySlots", "**.bankInventoryWidget", "**.inventorySlotWidgets", "**.inventorySummary"), "present"),
    FieldSpec("bank_action_widgets", ("bankUi.depositInventoryButtonWidget", "bank_ui.depositInventoryButtonWidget", "**.depositInventoryButtonWidget"), "present"),
    FieldSpec("bank_item_deltas", ("lastBankContainerChange", "bankContainerDelta", "bankItemDeltas", "itemContainerChangedBank", "**.lastBankContainerChange", "**.bankContainerDelta", "**.bankItemDeltas", "**.itemContainerChangedBank"), "requires_bridge_export"),
    FieldSpec("banking_bank_ui", ("bank_ui", "bankUi", "payloads.bank_ui", "**.bank_ui", "**.bankUi"), "present"),
    FieldSpec("banking_bank_open", ("bank_ui.bankOpen", "bankUi.bankOpen", "**.bankOpen"), "present"),
    FieldSpec("banking_deposit_box_open", ("bank_ui.depositBoxOpen", "bankUi.depositBoxOpen", "**.depositBoxOpen"), "present"),
    FieldSpec("banking_active_bank_like_interface", ("bank_ui.activeBankLikeInterface", "bankUi.activeBankLikeInterface", "**.activeBankLikeInterface"), "computable_in_sidecar"),
    FieldSpec("banking_bank_widget_root", ("bank_ui.bankRootWidget", "bankUi.bankRootWidget", "**.bankRootWidget"), "present"),
    FieldSpec("banking_deposit_box_widget_root", ("bank_ui.depositBoxWidgetRoot", "bankUi.depositBoxWidgetRoot", "**.depositBoxWidgetRoot"), "present"),
    FieldSpec("banking_bank_container_available", ("bank_ui.bankContainerVisible", "bankUi.bankContainerVisible", "**.bankContainerVisible", "**.bankSummary.known"), "present"),
    FieldSpec("banking_bank_container_items", ("bank_ui.bankSummary.totalQuantityByItemId", "bankUi.bankSummary.totalQuantityByItemId", "bank_ui.bankItems", "**.bankSummary.totalQuantityByItemId", "**.bankItems"), "present"),
    FieldSpec("banking_bank_container_delta", ("bank_ui.bankContainerDelta", "bankUi.bankContainerDelta", "bankContainerDelta", "**.bankContainerDelta", "**.bankItemDeltas"), "computable_in_sidecar"),
    FieldSpec("banking_inventory_snapshot", ("bank_ui.inventorySummary", "bankUi.inventorySummary", "**.inventorySummary", "**.inventoryState"), "present"),
    FieldSpec("banking_inventory_delta", ("inventoryContainerDelta", "itemContainerChangedInventory", "recentInventoryDeltas", "**.inventoryContainerDelta", "**.recentInventoryDeltas"), "computable_in_sidecar"),
    FieldSpec("banking_deposited_items", ("banking_lifecycle.deposit.items", "deposit.items", "**.depositedItems", "**.deposit.items"), "computable_in_sidecar"),
    FieldSpec("banking_withdrawn_items", ("banking_lifecycle.withdraw.items", "withdraw.items", "**.withdrawnItems", "**.withdraw.items"), "computable_in_sidecar"),
    FieldSpec("banking_lifecycle", ("banking_lifecycle", "bankingLifecycle", "**.banking_lifecycle", "**.bankingLifecycle"), "computable_in_sidecar"),
    FieldSpec("combat_state", ("combat_state", "combatState", "payloads.combat_state", "**.combat_state", "**.combatState", "**.inCombat"), "present"),
    FieldSpec("combat_in_combat", ("combat_state.inCombat", "combatState.inCombat", "**.inCombat"), "present"),
    FieldSpec("combat_player_interacting", ("combat_state.playerInteracting", "combatState.playerInteracting", "**.playerInteracting"), "present"),
    FieldSpec("combat_actors_interacting_with_player", ("combat_state.actorsInteractingWithPlayer", "combatState.actorsInteractingWithPlayer", "**.actorsInteractingWithPlayer"), "present"),
    FieldSpec("combat_recent_hitsplats", ("combat_state.recentHitsplats", "combatState.recentHitsplats", "**.recentHitsplats", "**.HitsplatApplied"), "present"),
    FieldSpec("combat_recent_chat_messages", ("combat_state.recentChatMessages", "combatState.recentChatMessages", "**.recentChatMessages", "**.ChatMessage"), "present"),
    FieldSpec("combat_recent_stat_changes", ("combat_state.recentStatChanges", "combatState.recentStatChanges", "**.recentStatChanges", "**.StatChanged"), "present"),
    FieldSpec("combat_recent_actor_deaths", ("combat_state.recentActorDeaths", "combatState.recentActorDeaths", "**.recentActorDeaths", "**.NpcDeath"), "present"),
    FieldSpec("combat_recent_animations", ("combat_state.recentAnimations", "combatState.recentAnimations", "**.recentAnimations", "**.AnimationChanged"), "present"),
    FieldSpec("combat_player_health", ("combat_state.playerHealth", "combatState.playerHealth", "**.playerHealth", "**.hitpointsBoosted", "**.localHealthRatio"), "present"),
    FieldSpec("combat_hitsplat_amount", ("combat_state.recentHitsplats.amount", "combatState.recentHitsplats.amount", "**.recentHitsplats.*.amount", "**.hitsplat.amount"), "present"),
    FieldSpec("combat_damage_summary", ("combat_damage_summary", "combatDamageSummary", "**.combat_damage_summary", "**.combatDamageSummary"), "computable_in_sidecar"),
    FieldSpec("combat_damage_taken", ("combat_damage_summary.damageTaken", "combatDamageSummary.damageTaken", "**.damageTakenTotal", "**.damageTaken"), "computable_in_sidecar"),
    FieldSpec("combat_damage_dealt", ("combat_damage_summary.damageDealt", "combatDamageSummary.damageDealt", "**.damageDealtTotal", "**.damageDealt"), "computable_in_sidecar"),
    FieldSpec("combat_primary_opponent", ("combat_damage_summary.primaryOpponent", "combatDamageSummary.primaryOpponent", "**.primaryOpponent"), "computable_in_sidecar"),
    FieldSpec("interruption_lifecycle", ("interruption_lifecycle", "interruptionLifecycle", "**.interruption_lifecycle", "**.interruptionLifecycle"), "computable_in_sidecar"),
    FieldSpec("inventory_item_container_delta", ("itemContainerChangedInventory", "inventoryContainerDelta", "**.itemContainerChangedInventory", "**.inventoryContainerDelta"), "requires_bridge_export"),
    FieldSpec("nearby_objects", ("nearby_objects", "nearbyObjects", "sceneObjects", "objects", "liveCandidates", "candidates", "payloads.scene_object_census.objects", "**.objects"), "present"),
    FieldSpec(
        "route_objects",
        (
            "worldModelRouteObjectCensus.objects",
            "routeObjectCensus.objects",
            "route_objects",
            "routeObjects",
            "status.worldModelRouteObjectCensus.objects",
            "payloads.route_object_census.objects",
            "**.worldModelRouteObjectCensus.objects",
            "**.routeObjectCensus.objects",
        ),
        "present",
    ),
    FieldSpec("nearby_npcs", ("nearby_npcs", "nearbyNpcs", "npcs", "actors", "payloads.full_world_model_debug.actors", "**.npcs", "**.actors"), "present"),
    FieldSpec("effective_object_names", ("**.objectName", "**.name"), "present"),
    FieldSpec("effective_npc_names", ("**.npcName", "**.name"), "present"),
    FieldSpec("effective_object_actions", ("**.actions", "**.objectActions"), "present"),
    FieldSpec("effective_npc_actions", ("**.npcActions", "**.actions"), "requires_bridge_export"),
    FieldSpec("stable_refs", ("**.objectKey", "**.ref", "**.hash"), "present"),
    FieldSpec("distances", ("**.distanceTiles", "**.distanceToPlayer", "**.targetDistanceChebyshev"), "computable_in_sidecar"),
    FieldSpec("canvas_clickbox_aim_geometry", ("**.aimPoint", "**.clickboxBounds", "**.clickboxPolygon", "**.canvasLocation", "**.projection"), "present"),
    FieldSpec("object_candidate_geometry", ("**.geometry", "**.geometryAvailable", "**.projection", "**.aimPoint", "**.canvasLocation"), "present"),
    FieldSpec("object_candidate_clickbox", ("**.clickboxBounds", "**.clickboxPolygon", "**.geometry.clickbox", "**.projection.clickboxBounds"), "present"),
    FieldSpec("object_candidate_aim_point", ("**.aimPoint", "**.geometry.aimPoint", "**.projection.aimPoint"), "present"),
    FieldSpec("object_candidate_canvas_location", ("**.canvasLocation", "**.canvasPoint", "**.geometry.canvas", "**.projection.canvasLocation"), "present"),
    FieldSpec("object_candidate_canvas_tile_poly", ("**.canvasTilePolygon", "**.tilePolygon", "**.canvasTilePoly"), "present"),
    FieldSpec("woodcutting_tree_geometry", ("**.nearby_objects.*.geometry", "**.route_objects.*.geometry", "**.aimPoint", "**.geometry.aimPoint"), "present"),
    FieldSpec("woodcutting_tree_aim_point", ("**.nearby_objects.*.geometry.aimPoint", "**.route_objects.*.geometry.aimPoint", "**.aimPoint"), "present"),
    FieldSpec("woodcutting_tree_clickbox", ("woodcutting.treeClickbox", "woodcutting.treeClickboxes", "**.woodcuttingTreeClickbox", "**.treeClickbox"), "present"),
    FieldSpec("hover_menu_target_ref", ("**.hover.topIdentifier", "**.hover.topParam0", "**.hover.topParam1", "**.topIdentifier", "**.topParam0", "**.topParam1"), "present"),
    FieldSpec("menu_entry_target_ref", ("**.entries.*.identifier", "**.entries.*.param0", "**.entries.*.param1", "**.identifier", "**.param0", "**.param1"), "present"),
    FieldSpec("hover_entries", ("clientTickHot.hoverMenu.entries", "hoverMenu.entries", "postMenuSort.entries", "**.hoverMenu", "**.postMenuSort"), "present"),
    FieldSpec("open_menu_state", ("clientTickHot.hoverMenu.menuOpen", "hoverMenu.menuOpen", "**.menuOpen"), "present"),
    FieldSpec("open_menu_entries", ("menu.entries", "hoverMenu.entries", "postMenuSort.entries", "**.entries"), "present"),
    FieldSpec("open_menu_bounds", ("clientTickHot.hoverMenu.menuBounds", "hoverMenu.menuBounds", "postMenuSort.menuBounds", "**.menuBounds"), "present"),
    FieldSpec("open_menu_row_geometry", ("**.rowBounds", "**.menuRowBounds", "**.rowsVisualOrder.*.bounds"), "computable_in_sidecar"),
    FieldSpec("selected_item_spell_widget_state", ("selectedItem", "selectedSpell", "selectedWidget", "**.selectedItem", "**.selectedSpell", "**.selectedWidget"), "requires_bridge_export"),
    FieldSpec("top_level_interface_widget_state", ("widgets", "widgetState", "topLevelInterface", "**.widgets", "**.widget"), "present"),
    FieldSpec("allowlisted_widgets", ("allowlistedWidgets", "dialogueState", "bankUi", "**.allowlistedWidgets", "**.dialogueState"), "present"),
    FieldSpec("camera_canvas_window_metadata", ("cameraViewport", "viewport", "windowRect", "canvasRect", "**.cameraYaw", "**.canvasWidth", "**.windowRect"), "present"),
)

SIDE_CAR_COMPUTABLE = {
    "state_freshness",
    "distances",
    "source_freshness",
    "field_presence",
    "capability_summary",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any], limit: int = 8) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = repr(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def compact_value(value: Any, *, limit: int = 5) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key in list(value)[:limit]:
            child = value.get(key)
            if isinstance(child, (dict, list)):
                compact[key] = compact_value(child, limit=3)
            else:
                compact[key] = child
        if len(value) > limit:
            compact["_truncated_keys"] = len(value) - limit
        return compact
    if isinstance(value, list):
        items = [compact_value(item, limit=3) for item in value[:limit]]
        if len(value) > limit:
            items.append({"_truncated_items": len(value) - limit})
        return items
    return value


def compact_menu_entry(entry: Any) -> Any:
    if not isinstance(entry, dict):
        return compact_value(entry, limit=4)
    keep = (
        "option",
        "target",
        "type",
        "identifier",
        "itemId",
        "param0",
        "param1",
        "widgetId",
        "worldViewId",
        "rowIndex",
        "rowBounds",
        "bounds",
        "center",
    )
    return {key: compact_value(entry.get(key), limit=4) for key in keep if key in entry}


def compact_menu_sample(value: Any, *, entry_limit: int = 8) -> Any:
    if not isinstance(value, dict):
        return compact_value(value, limit=8)
    keep = (
        "schema",
        "sampleSource",
        "sourceEvent",
        "clientTick",
        "gameTickAtSample",
        "timestampUtc",
        "wallTimeMillis",
        "monotonicTimeNanos",
        "gameState",
        "menuOpen",
        "menuBounds",
        "bounds",
        "entryCount",
        "menuEntryCount",
        "entriesDisplayOrder",
        "topOption",
        "topTarget",
        "topType",
        "topIdentifier",
        "topParam0",
        "topParam1",
        "mouseCanvasX",
        "mouseCanvasY",
        "isInCanvas",
        "lastMenuOptionClicked",
    )
    compact = {key: compact_value(value.get(key), limit=6) for key in keep if key in value}
    entries = value.get("entries")
    if isinstance(entries, list):
        compact["entries"] = [compact_menu_entry(entry) for entry in entries[:entry_limit]]
        if len(entries) > entry_limit:
            compact["entries"].append({"_truncated_items": len(entries) - entry_limit})
    unknown = len([key for key in value if key not in set(keep) | {"entries"}])
    if unknown:
        compact["_truncated_keys"] = unknown
    return compact


def _iter_child_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, list):
        return list(value)
    return []


def _lookup_parts(value: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [value]
    part = parts[0]
    rest = parts[1:]
    matches: list[Any] = []
    if part == "**":
        matches.extend(_lookup_parts(value, rest))
        for child in _iter_child_values(value):
            matches.extend(_lookup_parts(child, parts))
        return matches
    if part == "*":
        for child in _iter_child_values(value):
            matches.extend(_lookup_parts(child, rest))
        return matches
    if isinstance(value, dict):
        if part in value:
            matches.extend(_lookup_parts(value[part], rest))
        lower = part.lower()
        for key, child in value.items():
            if str(key).lower() == lower and key != part:
                matches.extend(_lookup_parts(child, rest))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_lookup_parts(child, parts))
    return matches


def lookup_alias(root: Any, alias: str) -> list[Any]:
    parts = [part for part in str(alias).split(".") if part]
    if not parts:
        return []
    return _lookup_parts(root, parts)


def lookup_any(root: Any, aliases: tuple[str, ...] | list[str]) -> tuple[list[Any], list[str]]:
    values: list[Any] = []
    paths: list[str] = []
    for alias in aliases:
        found = [value for value in lookup_alias(root, alias) if value not in (None, "", [], {})]
        if found:
            values.extend(found)
            paths.append(alias)
    return _dedupe(values), paths


def scan_field_presence(root: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    available: list[str] = []
    missing: list[str] = []
    for spec in FIELD_SPECS:
        values, paths = lookup_any(root, spec.aliases)
        present = bool(values)
        fields[spec.name] = {
            "present": present,
            "paths": paths,
            "sample": compact_value(values[0]) if values else None,
            "category_hint": spec.category,
            "description": spec.description,
        }
        if present:
            available.append(spec.name)
        else:
            missing.append(spec.name)
    return {
        "schema_version": FIELD_SCAN_SCHEMA_VERSION,
        "available_fields": available,
        "missing_fields": missing,
        "fields": fields,
    }


def categorize_schema_gaps(scan: dict[str, Any]) -> dict[str, list[str]]:
    categories = {
        "present": [],
        "missing": [],
        "computable_in_sidecar": [],
        "requires_bridge_export": [],
        "needs_manual_review": [],
    }
    fields = _dict(scan.get("fields"))
    for spec in FIELD_SPECS:
        field = _dict(fields.get(spec.name))
        if field.get("present"):
            categories["present"].append(spec.name)
            continue
        if spec.name in SIDE_CAR_COMPUTABLE or spec.category == "computable_in_sidecar":
            categories["computable_in_sidecar"].append(spec.name)
        elif spec.category == "requires_bridge_export":
            categories["requires_bridge_export"].append(spec.name)
        elif spec.category == "needs_manual_review":
            categories["needs_manual_review"].append(spec.name)
        else:
            categories["missing"].append(spec.name)
    return categories


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


def point_from(value: Any) -> dict[str, Any] | None:
    data = _dict(value)
    world_x = _int(_first(data.get("worldX"), data.get("x")))
    world_y = _int(_first(data.get("worldY"), data.get("y")))
    plane = _int(data.get("plane"))
    if world_x is None or world_y is None:
        return None
    point = {"worldX": world_x, "worldY": world_y}
    if plane is not None:
        point["plane"] = plane
    return point


def _slot_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    data = _dict(value)
    items = _list(_first(data.get("items"), data.get("slots"), value if isinstance(value, list) else None))
    result: list[dict[str, Any]] = []
    for item in items[:limit]:
        record = _dict(item)
        if not record:
            continue
        result.append(
            {
                key: _first(record.get(key), record.get(key.lower()))
                for key in ("slot", "id", "itemId", "name", "quantity", "count")
                if _first(record.get(key), record.get(key.lower())) is not None
            }
        )
    return result


def inventory_summary(value: Any, *, limit: int = 28) -> dict[str, Any]:
    data = _dict(value)
    return {
        "known": data.get("known"),
        "itemCount": _first(data.get("itemCount"), data.get("filledSlots"), data.get("count")),
        "freeSlots": data.get("freeSlots"),
        "inventoryFull": data.get("inventoryFull"),
        "changedRecently": data.get("changedRecently"),
        "resourceCounts": data.get("resourceCounts"),
        "items": _slot_items(data, limit=limit),
    }


def _all_dicts_at(root: Any, aliases: tuple[str, ...], *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for alias in aliases:
        for value in lookup_alias(root, alias):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        result.append(item)
            elif isinstance(value, dict):
                result.append(value)
            if len(result) >= limit:
                return result[:limit]
    return result[:limit]


def _record_kind(record: dict[str, Any], fallback: str) -> str:
    text = str(_first(record.get("targetType"), record.get("kind"), record.get("type"), fallback) or fallback)
    lowered = text.lower()
    if "npc" in lowered:
        return "npc"
    if "object" in lowered or "scene" in lowered or "wall" in lowered or "ground" in lowered:
        return "object"
    return fallback


def normalized_candidate(record: dict[str, Any], *, fallback_kind: str = "object") -> dict[str, Any]:
    target = _dict(record.get("target"))
    geometry = _dict(record.get("geometry"))
    projection = _dict(_first(record.get("projection"), record.get("projectionStatus"), record.get("resourceProjectionStatus")))
    aim_point = _first(record.get("aimPoint"), record.get("aimPointContext"), geometry.get("aimPoint"), projection.get("aimPoint"))
    actions = _first(record.get("actions"), record.get("effectiveActions"), target.get("actions"), record.get("npcActions"))
    if isinstance(actions, str):
        actions = [actions]
    actions = [str(action) for action in actions or [] if action not in (None, "")]
    world_point = point_from(record) or point_from(record.get("worldPoint")) or point_from(record.get("worldLocation")) or point_from(target.get("worldLocation"))
    local_point = _dict(record.get("localPoint"))
    local = {
        key: _int(_first(record.get(key), local_point.get(key), target.get(key)))
        for key in ("localX", "localY", "sceneX", "sceneY")
        if _int(_first(record.get(key), local_point.get(key), target.get(key))) is not None
    }
    on_screen = _first(record.get("onScreen"), projection.get("onScreen"), projection.get("visible"))
    geometry_available = _first(record.get("geometryAvailable"), geometry.get("available"), projection.get("geometryAvailable"), projection.get("actionableByCanvas"))
    return {
        "ref": _first(record.get("ref"), record.get("objectKey"), record.get("npcKey"), record.get("hash"), target.get("objectKey")),
        "kind": _record_kind(record, fallback_kind),
        "rawId": _first(record.get("rawId"), record.get("id"), record.get("npcId"), target.get("id")),
        "effectiveId": _first(record.get("effectiveId"), record.get("id"), target.get("id")),
        "effectiveName": _first(record.get("effectiveName"), record.get("objectName"), record.get("npcName"), record.get("name"), target.get("name")),
        "effectiveActions": actions,
        "worldPoint": world_point,
        "localPoint": local or None,
        "distance": _first(record.get("distance"), record.get("distanceTiles"), record.get("distanceToPlayer"), record.get("targetDistanceChebyshev")),
        "onScreen": on_screen,
        "geometry": {
            "available": geometry_available,
            "aimPoint": compact_value(aim_point) if aim_point else None,
            "clickbox": compact_value(_first(record.get("clickboxBounds"), record.get("clickboxPolygon"), geometry.get("clickbox"), projection.get("clickboxBounds"))),
            "canvas": compact_value(_first(record.get("canvasLocation"), record.get("canvasPoint"), geometry.get("canvas"), projection.get("canvasLocation"))),
        },
        "menuActionAvailable": bool(actions),
        "freshness": _first(record.get("freshness"), record.get("ageMillis"), record.get("lastUpdatedTick"), record.get("lastSeenTick")),
        "confidence": _float(_first(record.get("confidence"), record.get("targetLiveStateConfidence"), record.get("score"))),
        "source": _first(record.get("source"), target.get("source")),
    }


def _clean_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    if not candidate.get("ref"):
        missing.append("ref")
    if not candidate.get("effectiveName"):
        missing.append("effectiveName")
    if not candidate.get("effectiveActions"):
        missing.append("effectiveActions")
    if not candidate.get("worldPoint"):
        missing.append("worldPoint")
    if candidate.get("distance") is None:
        missing.append("distance")
    if not _dict(candidate.get("geometry")).get("aimPoint"):
        missing.append("aimGeometry")
    return {key: value for key, value in {**candidate, "missingFields": missing}.items() if value not in (None, "", [], {})}


ROUTE_OBJECT_ALIASES: tuple[str, ...] = (
    "route_objects",
    "routeObjects",
    "worldModelRouteObjectCensus.objects",
    "routeObjectCensus.objects",
    "status.worldModelRouteObjectCensus.objects",
    "payloads.route_object_census.objects",
    "**.worldModelRouteObjectCensus.objects",
    "**.routeObjectCensus.objects",
)


def nearby_candidates(root: Any, *, kind: str, limit: int = 25) -> list[dict[str, Any]]:
    if kind == "npc":
        aliases = ("nearby_npcs", "nearbyNpcs", "npcs", "actors", "payloads.full_world_model_debug.actors", "**.npcs", "**.actors")
    else:
        aliases = (
            "nearby_objects",
            "nearbyObjects",
            "sceneObjects",
            "objects",
            "candidates",
            "liveCandidates",
            "payloads.scene_object_census.objects",
            "payloads.resource_object_census.objects",
            "payloads.route_object_census.objects",
            "worldModelRouteObjectCensus.objects",
            "routeObjectCensus.objects",
            "status.worldModelRouteObjectCensus.objects",
            "payloads.service_object_census.objects",
            "**.objects",
        )
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in _all_dicts_at(root, aliases, limit=limit * 4):
        candidate = normalized_candidate(record, fallback_kind=kind)
        if candidate.get("kind") != kind:
            continue
        key = str(_first(candidate.get("ref"), candidate.get("effectiveName"), repr(record)))
        if key in seen:
            continue
        seen.add(key)
        result.append(_clean_candidate(candidate))
        if len(result) >= limit:
            break
    return result


def normalized_route_object(record: dict[str, Any]) -> dict[str, Any]:
    candidate = normalized_candidate(record, fallback_kind="object")
    candidate["kind"] = "route"
    candidate["routeObjectCandidate"] = _first(record.get("routeObjectCandidate"), record.get("routeCandidate"), True)
    candidate["routeObjectKind"] = _first(record.get("routeObjectKind"), record.get("routeKind"), record.get("classification"))
    candidate["source"] = candidate.get("source") or "route_object_census"
    return _clean_candidate(candidate)


def route_candidates(root: Any, *, limit: int = 25) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for record in _all_dicts_at(root, ROUTE_OBJECT_ALIASES, limit=limit * 4):
        candidate = normalized_route_object(record)
        key = str(_first(candidate.get("ref"), candidate.get("effectiveName"), repr(record)))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
        if len(result) >= limit:
            break
    return result


def target_score(candidate: dict[str, Any], query: str, *, mode: str) -> tuple[float, list[str]]:
    text = query.lower().strip()
    name = str(candidate.get("effectiveName") or "").lower()
    actions = " ".join(str(action).lower() for action in candidate.get("effectiveActions") or [])
    distance = _float(candidate.get("distance"))
    score = 0.0
    reasons: list[str] = []
    if text and text in name:
        score += 0.45
        reasons.append("name_match")
    elif text and text in actions:
        score += 0.25
        reasons.append("action_match")
    elif not text:
        score += 0.1
        reasons.append("unfiltered")
    if candidate.get("onScreen") is True:
        score += 0.15
        reasons.append("on_screen")
    if _dict(candidate.get("geometry")).get("aimPoint"):
        score += 0.15
        reasons.append("aim_geometry")
    if candidate.get("effectiveActions"):
        score += 0.1
        reasons.append("actions_available")
    if distance is not None:
        if mode == "nearest":
            score += max(0.0, 0.2 - min(distance, 20.0) / 100.0)
            reasons.append("distance_ranked")
        else:
            score += max(0.0, 0.1 - min(distance, 20.0) / 200.0)
            reasons.append("nearby")
    if candidate.get("confidence") is not None:
        score += min(0.1, max(0.0, float(candidate["confidence"]) / 10.0))
        reasons.append("source_confidence")
    return round(score, 3), reasons


def select_target(root: Any, *, kind: str, query: str, mode: str = "best", limit: int = 25) -> dict[str, Any]:
    candidates = route_candidates(root, limit=limit) if kind == "route" else nearby_candidates(root, kind=kind, limit=limit)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for candidate in candidates:
        name = str(candidate.get("effectiveName") or "").lower()
        actions = " ".join(str(action).lower() for action in candidate.get("effectiveActions") or [])
        if query and query.lower() not in name and query.lower() not in actions:
            continue
        score, reasons = target_score(candidate, query, mode=mode)
        scored.append((score, candidate, reasons))
    if mode == "nearest":
        scored.sort(key=lambda item: (_float(item[1].get("distance")) is None, _float(item[1].get("distance")) or 999999, -item[0]))
    else:
        scored.sort(key=lambda item: (-item[0], _float(item[1].get("distance")) or 999999))
    if not scored:
        return {
            "status": "WARN",
            "query": query,
            "kind": kind,
            "candidate": None,
            "candidatesConsidered": len(candidates),
            "warnings": [f"no observed {kind} matched {query!r}"],
            "missingFields": ["route_objects" if kind == "route" else f"nearby_{kind}s"],
        }
    score, candidate, reasons = scored[0]
    candidate = dict(candidate)
    candidate["confidence"] = score
    candidate["reasons"] = reasons
    return {
        "status": "PASS" if score >= 0.35 else "WARN",
        "query": query,
        "kind": kind,
        "candidate": candidate,
        "candidatesConsidered": len(candidates),
        "warnings": [] if score >= 0.35 else ["target confidence is low"],
        "missingFields": candidate.get("missingFields") or [],
    }


def normalized_telemetry(root: Any, *, max_items: int = 10) -> dict[str, Any]:
    scan = scan_field_presence(root)
    tick_values, _ = lookup_any(root, ("latestTick", "latestTickProcessed", "tick", "tickId", "**.latestTick", "**.latestTickProcessed"))
    sequence_values, _ = lookup_any(root, ("latestSequence", "compactPacketLastSequence", "**.latestSequence", "**.compactPacketLastSequence", "**.liveCacheLatestSequence", "**.sequence"))
    inventory_values, _ = lookup_any(root, ("inventoryState", "inventory", "payloads.inventory", "**.inventoryState"))
    equipment_values, _ = lookup_any(root, ("equipment", "equipmentState", "**.equipment"))
    bank_values, _ = lookup_any(root, ("bank_ui", "bankUi", "bankUI", "bank", "payloads.bank_ui", "**.bankOpen"))
    combat_values, _ = lookup_any(root, ("combat_state", "combatState", "payloads.combat_state", "**.combatState", "**.inCombat"))
    hover_values, _ = lookup_any(root, ("clientTickHot.hoverMenu", "hoverMenu", "postMenuSort", "**.hoverMenu"))
    menu_values, _ = lookup_any(root, ("menu", "hoverMenu", "postMenuSort", "**.menuOpen"))
    widget_values, _ = lookup_any(root, ("widgets", "widgetState", "dialogueState", "bankUi", "**.widgets"))
    camera_yaw_values, _ = lookup_any(root, ("cameraYaw", "camera.yaw", "camera.cameraYaw", "**.cameraYaw"))
    camera_pitch_values, _ = lookup_any(root, ("cameraPitch", "camera.pitch", "camera.cameraPitch", "**.cameraPitch"))
    camera_zoom_values, _ = lookup_any(root, ("cameraZoom", "camera.zoom", "camera.cameraZoom", "**.cameraZoom"))
    canvas_values, _ = lookup_any(root, ("canvas", "canvasRect", "viewport", "cameraViewport", "**.canvasRect", "**.cameraViewport"))
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "latest_tick": tick_values[0] if tick_values else None,
        "latest_export_sequence": sequence_values[0] if sequence_values else None,
        "field_presence": scan,
        "game_state": (lookup_any(root, ("gameState", "**.gameState"))[0] or [None])[0],
        "player": {
            "worldPoint": point_from((lookup_any(root, ("player.worldLocation", "playerWorldLocation", "localPlayer", "**.playerWorldLocation"))[0] or [None])[0]),
            "animation": (lookup_any(root, ("player.animation", "localPlayer.animation", "**.animation"))[0] or [None])[0],
            "poseAnimation": (lookup_any(root, ("player.poseAnimation", "localPlayer.poseAnimation", "**.poseAnimation"))[0] or [None])[0],
            "runEnergy": (lookup_any(root, ("runEnergy", "status.runEnergy", "**.runEnergy"))[0] or [None])[0],
        },
        "inventory": inventory_summary(inventory_values[0], limit=max_items) if inventory_values else {"known": False, "missing": True},
        "equipment": inventory_summary(equipment_values[0], limit=max_items) if equipment_values else {"known": False, "missing": True},
        "bank": compact_value(bank_values[0], limit=8) if bank_values else {"missing": True},
        "combat": compact_value(combat_values[0], limit=10) if combat_values else {"missing": True},
        "widgets": compact_value(widget_values[0], limit=8) if widget_values else {"missing": True},
        "hover": compact_menu_sample(hover_values[0]) if hover_values else {"missing": True},
        "menu": compact_menu_sample(menu_values[0]) if menu_values else {"missing": True},
        "camera": {
            "cameraYaw": camera_yaw_values[0] if camera_yaw_values else None,
            "cameraPitch": camera_pitch_values[0] if camera_pitch_values else None,
            "cameraZoom": camera_zoom_values[0] if camera_zoom_values else None,
            "canvasOrViewport": compact_value(canvas_values[0], limit=8) if canvas_values else None,
            "known": bool(camera_yaw_values or camera_pitch_values or camera_zoom_values or canvas_values),
        },
        "nearby_objects": nearby_candidates(root, kind="object", limit=max_items),
        "route_objects": route_candidates(root, limit=max_items),
        "nearby_npcs": nearby_candidates(root, kind="npc", limit=max_items),
    }
