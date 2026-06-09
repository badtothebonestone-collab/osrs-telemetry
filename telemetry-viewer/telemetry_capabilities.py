from __future__ import annotations

from typing import Any

import telemetry_schema
import telemetry_sources


CAPABILITIES_VERSION = "telemetry_capabilities.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_warning(read: dict[str, Any]) -> str | None:
    status = read.get("parse_status")
    error = read.get("read_error")
    if status in {"ok", "missing"} and not error:
        return None
    return f"{read.get('name')}: {status}" + (f" ({error})" if error else "")


def _bank_ui_capability(reads: list[dict[str, Any]], scan: dict[str, Any]) -> dict[str, Any]:
    field = _dict(_dict(scan.get("fields")).get("bank_ui"))
    bank_reads = [read for read in reads if str(read.get("name") or "") == "bank_ui"]
    present_reads = [read for read in bank_reads if read.get("exists") and read.get("parse_status") in {"ok", "partial"}]
    stale_reads = [read for read in present_reads if read.get("stale")]
    endpoint_known = any(read.get("source_kind") == "plugin_snapshot" for read in bank_reads)
    if present_reads and stale_reads:
        status = "present_but_stale"
    elif present_reads or field.get("present"):
        status = "present"
    elif endpoint_known:
        status = "missing_from_recording_but_live_source_known"
    else:
        status = "requires_bridge_export"
    return {
        "schema": "bank_ui_capability.v1",
        "status": status,
        "present": bool(present_reads or field.get("present")),
        "sourceKnown": bool(endpoint_known),
        "sourceCount": len(bank_reads),
        "latestTick": next((read.get("latest_tick") for read in reversed(present_reads) if read.get("latest_tick") is not None), None),
        "latestExportSequence": next((read.get("latest_export_sequence") for read in reversed(present_reads) if read.get("latest_export_sequence") is not None), None),
        "warnings": [warning for read in bank_reads for warning in (read.get("warnings") or [])],
    }


def _live_payload_capability(reads: list[dict[str, Any]], scan: dict[str, Any], *, name: str, field_name: str, schema: str) -> dict[str, Any]:
    field = _dict(_dict(scan.get("fields")).get(field_name))
    matching_reads = [read for read in reads if str(read.get("name") or "") == name]
    present_reads = [read for read in matching_reads if read.get("exists") and read.get("parse_status") in {"ok", "partial"}]
    stale_reads = [read for read in present_reads if read.get("stale")]
    endpoint_known = any(read.get("source_kind") == "plugin_snapshot" for read in matching_reads)
    if present_reads and stale_reads:
        status = "present_but_stale"
    elif present_reads or field.get("present"):
        status = "present"
    elif endpoint_known:
        status = "missing_from_recording_but_live_source_known"
    else:
        status = "requires_bridge_export"
    return {
        "schema": schema,
        "status": status,
        "present": bool(present_reads or field.get("present")),
        "sourceKnown": bool(endpoint_known),
        "sourceCount": len(matching_reads),
        "latestTick": next((read.get("latest_tick") for read in reversed(present_reads) if read.get("latest_tick") is not None), None),
        "latestExportSequence": next((read.get("latest_export_sequence") for read in reversed(present_reads) if read.get("latest_export_sequence") is not None), None),
        "warnings": [warning for read in matching_reads for warning in (read.get("warnings") or [])],
    }


def _field_status(scan: dict[str, Any], field_name: str, *, default_missing: str = "missing") -> str:
    field = _dict(_dict(scan.get("fields")).get(field_name))
    if field.get("present"):
        return "present"
    category = str(field.get("category") or "")
    if category == "computable_in_sidecar":
        return "computable_in_python"
    if category == "requires_bridge_export":
        return "requires_plugin_export"
    return default_missing


def _banking_capabilities(scan: dict[str, Any], bank_ui_capability: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "banking.bankUi": "banking_bank_ui",
        "banking.bankOpen": "banking_bank_open",
        "banking.depositBoxOpen": "banking_deposit_box_open",
        "banking.activeBankLikeInterface": "banking_active_bank_like_interface",
        "banking.bankWidgetRoot": "banking_bank_widget_root",
        "banking.depositBoxWidgetRoot": "banking_deposit_box_widget_root",
        "banking.bankContainer.available": "banking_bank_container_available",
        "banking.bankContainer.items": "banking_bank_container_items",
        "banking.bankContainer.delta": "banking_bank_container_delta",
        "banking.inventory.snapshot": "banking_inventory_snapshot",
        "banking.inventory.delta": "banking_inventory_delta",
        "banking.depositedItems": "banking_deposited_items",
        "banking.withdrawnItems": "banking_withdrawn_items",
        "banking.lifecycle": "banking_lifecycle",
    }
    capabilities = {name: _field_status(scan, field_name) for name, field_name in fields.items()}
    if bank_ui_capability.get("status") == "present_but_stale":
        capabilities["banking.bankUi"] = "stale"
    elif bank_ui_capability.get("status") == "missing_from_recording_but_live_source_known":
        capabilities["banking.bankUi"] = "missing_from_recording_but_live_source_known"
    elif bank_ui_capability.get("status") == "requires_bridge_export":
        capabilities["banking.bankUi"] = "requires_plugin_export"
    return {
        "schema": "banking_capability_summary.v1",
        "fields": capabilities,
        "present": sorted(name for name, status in capabilities.items() if status == "present"),
        "missing": sorted(name for name, status in capabilities.items() if status == "missing"),
        "stale": sorted(name for name, status in capabilities.items() if status == "stale"),
        "computable_in_python": sorted(name for name, status in capabilities.items() if status == "computable_in_python"),
        "requires_plugin_export": sorted(name for name, status in capabilities.items() if status == "requires_plugin_export"),
    }


def _combat_capabilities(scan: dict[str, Any], combat_state_capability: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "combat_state": "combat_state",
        "combat.inCombat": "combat_in_combat",
        "combat.playerInteracting": "combat_player_interacting",
        "combat.actorsInteractingWithPlayer": "combat_actors_interacting_with_player",
        "combat.recentHitsplats": "combat_recent_hitsplats",
        "combat.recentChatMessages": "combat_recent_chat_messages",
        "combat.recentStatChanges": "combat_recent_stat_changes",
        "combat.recentActorDeaths": "combat_recent_actor_deaths",
        "combat.recentAnimations": "combat_recent_animations",
        "combat.playerHealth": "combat_player_health",
        "combat.hitsplatAmount": "combat_hitsplat_amount",
        "combat.damageSummary": "combat_damage_summary",
        "combat.damageTaken": "combat_damage_taken",
        "combat.damageDealt": "combat_damage_dealt",
        "combat.primaryOpponent": "combat_primary_opponent",
        "interruption.lifecycle": "interruption_lifecycle",
    }
    capabilities = {name: _field_status(scan, field_name) for name, field_name in fields.items()}
    if combat_state_capability.get("status") == "present_but_stale":
        capabilities["combat_state"] = "stale"
    elif combat_state_capability.get("status") == "missing_from_recording_but_live_source_known":
        capabilities["combat_state"] = "missing_from_recording_but_live_source_known"
    elif combat_state_capability.get("status") == "requires_bridge_export":
        capabilities["combat_state"] = "requires_plugin_export"
    return {
        "schema": "combat_capability_summary.v1",
        "fields": capabilities,
        "present": sorted(name for name, status in capabilities.items() if status == "present"),
        "missing": sorted(name for name, status in capabilities.items() if status == "missing"),
        "stale": sorted(name for name, status in capabilities.items() if status == "stale"),
        "computable_in_python": sorted(name for name, status in capabilities.items() if status == "computable_in_python"),
        "requires_plugin_export": sorted(name for name, status in capabilities.items() if status == "requires_plugin_export"),
    }


def _object_geometry_capabilities(scan: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "objectCandidate.geometry": "object_candidate_geometry",
        "objectCandidate.clickbox": "object_candidate_clickbox",
        "objectCandidate.aimPoint": "object_candidate_aim_point",
        "objectCandidate.canvasLocation": "object_candidate_canvas_location",
        "objectCandidate.canvasTilePoly": "object_candidate_canvas_tile_poly",
        "woodcutting.treeGeometry": "woodcutting_tree_geometry",
        "woodcutting.treeAimPoint": "woodcutting_tree_aim_point",
        "woodcutting.treeClickbox": "woodcutting_tree_clickbox",
        "hoverMenu.targetRef": "hover_menu_target_ref",
        "menuEntry.targetRef": "menu_entry_target_ref",
    }
    capabilities = {name: _field_status(scan, field_name) for name, field_name in fields.items()}
    return {
        "schema": "object_geometry_capability_summary.v1",
        "fields": capabilities,
        "present": sorted(name for name, status in capabilities.items() if status == "present"),
        "missing": sorted(name for name, status in capabilities.items() if status == "missing"),
        "stale": sorted(name for name, status in capabilities.items() if status == "stale"),
        "computable_in_python": sorted(name for name, status in capabilities.items() if status == "computable_in_python"),
        "requires_plugin_export": sorted(name for name, status in capabilities.items() if status == "requires_plugin_export"),
    }


def capability_summary_from_reads(reads: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = telemetry_sources.parsed_payload_by_source(reads)
    return capability_summary_from_payload(
        payloads,
        source_files=reads,
        source_freshness=telemetry_sources.source_freshness_summary(reads),
    )


def capability_summary_from_payload(
    payload: Any,
    *,
    source_files: list[dict[str, Any]] | None = None,
    source_freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scan = telemetry_schema.scan_field_presence(payload)
    normalized = telemetry_schema.normalized_telemetry(payload)
    categories = telemetry_schema.categorize_schema_gaps(scan)
    source_files = source_files or []
    stale = [
        {
            "name": read.get("name"),
            "path": read.get("path"),
            "age_seconds": read.get("age_seconds"),
        }
        for read in source_files
        if read.get("stale")
    ]
    parse_warnings = [warning for read in source_files for warning in [_source_warning(read)] if warning]
    bank_ui_capability = _bank_ui_capability(source_files, scan)
    combat_state_capability = _live_payload_capability(
        source_files,
        scan,
        name="combat_state",
        field_name="combat_state",
        schema="combat_state_capability.v1",
    )
    return {
        "schema_version": telemetry_schema.SCHEMA_VERSION,
        "capabilities_version": CAPABILITIES_VERSION,
        "generated_at_utc": telemetry_sources.utc_now(),
        "status": "WARN" if categories["requires_bridge_export"] or stale or parse_warnings else "PASS",
        "available_fields": scan.get("available_fields") or [],
        "missing_fields": scan.get("missing_fields") or [],
        "stale_fields": [item["name"] for item in stale if item.get("name")],
        "source_files": [
            {
                "name": read.get("name"),
                "path": read.get("path"),
                "url": read.get("url"),
                "source_kind": read.get("source_kind"),
                "need": read.get("need"),
                "exists": read.get("exists"),
                "size_bytes": read.get("size_bytes"),
                "modified_utc": read.get("modified_utc"),
                "age_seconds": read.get("age_seconds"),
                "stale": read.get("stale"),
                "parse_status": read.get("parse_status"),
                "read_error": read.get("read_error"),
                "http_status": read.get("http_status"),
            }
            for read in source_files
        ],
        "bank_ui": bank_ui_capability,
        "banking": _banking_capabilities(scan, bank_ui_capability),
        "combat_state": combat_state_capability,
        "combat": _combat_capabilities(scan, combat_state_capability),
        "object_geometry": _object_geometry_capabilities(scan),
        "source_freshness": source_freshness or {},
        "last_update_time": (source_freshness or {}).get("last_update_time"),
        "latest_tick": normalized.get("latest_tick"),
        "latest_export_sequence": normalized.get("latest_export_sequence"),
        "parse_warnings": parse_warnings,
        "field_scan": scan,
        "gap_categories": categories,
        "normalized": {
            "game_state": normalized.get("game_state"),
            "player": normalized.get("player"),
            "inventory_known": not _dict(normalized.get("inventory")).get("missing"),
            "equipment_known": not _dict(normalized.get("equipment")).get("missing"),
            "bank_known": not _dict(normalized.get("bank")).get("missing"),
            "nearby_object_count": len(normalized.get("nearby_objects") or []),
            "route_object_count": len(normalized.get("route_objects") or []),
            "nearby_npc_count": len(normalized.get("nearby_npcs") or []),
        },
    }


def capability_summary_from_context(context: dict[str, Any]) -> dict[str, Any]:
    source_files = []
    for item in context.get("sourceFiles") or []:
        if not isinstance(item, dict):
            continue
        read = dict(item)
        read.setdefault("parse_status", "ok" if read.get("exists") else "missing")
        read.setdefault("age_seconds", None)
        read.setdefault("stale", False)
        source_files.append(read)
    return capability_summary_from_payload(
        context,
        source_files=source_files,
        source_freshness=telemetry_sources.source_freshness_summary(source_files),
    )
