from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_KNOWLEDGE_SCHEMA = "project_knowledge.v1"
RECORDINGS_INDEX_SCHEMA = "project_recordings_index.v1"
CAPABILITY_REGISTRY_SCHEMA = "project_capability_registry.v1"
SCRIPT_API_MAP_SCHEMA = "project_script_api_map.v1"
OPEN_GAPS_SCHEMA = "project_open_gaps.v1"
UPDATE_RESULT_SCHEMA = "project_knowledge_update_result.v1"

MANUAL_BEGIN = "<!-- BEGIN MANUAL NOTES -->"
MANUAL_END = "<!-- END MANUAL NOTES -->"

REQUIRED_DOC_FILES = [
    "PROJECT_STATE.md",
    "ENTRYPOINTS.md",
    "CAPABILITY_REGISTRY.md",
    "API_DATA_PATHS.md",
    "SCRIPT_API_MAP.md",
    "OPEN_GAPS.md",
]

REQUIRED_INDEX_FILES = [
    "project_knowledge.json",
    "recordings_index.json",
    "capability_registry.json",
    "script_api_map.json",
    "open_gaps.json",
]

KEY_CAPABILITY_INDEXES = {
    "Record Everything": ("record_everything_simple_mode",),
    "banking_lifecycle": ("banking_lifecycle",),
    "bank_ui": ("bank_ui_preservation",),
    "bankContainerDelta": ("bank_container_delta",),
    "woodcutting_lifecycle": ("woodcutting_lifecycle",),
    "woodcutting_loop_lifecycle": ("woodcutting_loop_lifecycle",),
    "traversal_lifecycle": ("traversal_lifecycle",),
    "route_template": ("route_templates_variants",),
    "route_monitor": ("route_monitor_history",),
    "route_demonstration": ("route_demonstration_guide",),
    "interruption_lifecycle": ("combat_interruption_lifecycle",),
    "combat_damage_summary": ("combat_damage_summary",),
    "human_click_profile": ("human_click_profile",),
    "human_click_planning": ("human_click_planning",),
    "bot_eval_runner": ("bot_eval_runner",),
    "loaded_scene_recovery": ("loaded_scene_recovery",),
    "input_geometry_resolver": ("input_geometry_resolver",),
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _slug(value: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip()).strip("_")
    return text or "unknown"


def safe_load_json(path: str | Path, default: Any = None) -> Any:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return default
    return value


def atomic_write_json(path: str | Path, payload: Any, *, pretty: bool = True) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2 if pretty else None, sort_keys=False, default=str)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
        os.replace(temp_name, target)
    finally:
        try:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        except OSError:
            pass
    return target


def write_text_preserving_manual_notes(path: str | Path, generated: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    manual = ""
    try:
        existing = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        existing = ""
    start = existing.find(MANUAL_BEGIN)
    end = existing.find(MANUAL_END)
    if start >= 0 and end >= start:
        manual = existing[start + len(MANUAL_BEGIN) : end].strip("\n")
    text = generated.rstrip() + "\n\n## Manual Notes\n\n" + MANUAL_BEGIN + "\n"
    if manual:
        text += manual.rstrip() + "\n"
    text += MANUAL_END + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def recording_dirs(root: Path | None = None) -> list[Path]:
    base = root or repo_root() / "recordings"
    if not base.exists():
        return []
    return sorted([path for path in base.iterdir() if path.is_dir()], key=lambda path: path.name)


def detect_activity(summary: dict[str, Any], recording: Path | None = None) -> str:
    label = " ".join(str(summary.get(key) or "") for key in ("label", "description", "recording_id", "recording_path")).lower()
    if recording is not None:
        label += " " + recording.name.lower()
    loop = _dict(summary.get("woodcutting_loop_lifecycle"))
    banking = _dict(summary.get("banking_lifecycle"))
    traversal = _dict(summary.get("traversal_lifecycle"))
    woodcutting = _dict(summary.get("woodcutting_lifecycle"))
    menu = _dict(summary.get("menu_interaction_summary"))
    route_name = str(traversal.get("routeName") or summary.get("routeName") or summary.get("detectedRouteName") or "")
    if loop and str(loop.get("loopState") or "").lower() not in {"", "unknown"}:
        return "Woodcutting Loop"
    if traversal and (route_name and route_name != "route_unknown"):
        return "Route / Traversal"
    if any(token in label for token in ("bank_to", "tree_area_to_bank", "woodcutting_area_to_bank", "route")) and traversal:
        return "Route / Traversal"
    phase = str(woodcutting.get("phase") or "").lower()
    if woodcutting and phase not in {"", "idle", "unknown"}:
        return "Woodcutting"
    bank = _dict(banking.get("bank"))
    if banking and (
        _dict(banking.get("deposit")).get("detected")
        or _dict(banking.get("withdraw")).get("detected")
        or bank.get("openSeen")
        or bank.get("depositBoxOpenSeen")
        or any(token in label for token in ("bank_open", "deposit", "withdraw"))
    ):
        return "Banking"
    if _safe_int(menu.get("menuSelectionCount")) > 0:
        return "Menu Interaction"
    if summary.get("input_trace") or summary.get("input_action_summary"):
        return "Human Input / Camera"
    return "Generic Telemetry"


def best_verdict(summary: dict[str, Any], activity: str) -> str:
    if activity == "Banking":
        return str(summary.get("bankingLifecycleStatus") or _dict(summary.get("banking_lifecycle")).get("status") or summary.get("status") or "WARN")
    if activity == "Route / Traversal":
        return str(
            summary.get("routeTemplateStatus")
            or summary.get("traversalStatus")
            or _dict(summary.get("route_template_comparison")).get("status")
            or _dict(summary.get("traversal_lifecycle")).get("status")
            or summary.get("status")
            or "WARN"
        )
    if activity == "Woodcutting":
        return str(_dict(summary.get("woodcutting_lifecycle")).get("status") or summary.get("status") or "WARN")
    if activity == "Woodcutting Loop":
        return str(_dict(summary.get("woodcutting_loop_lifecycle")).get("status") or summary.get("status") or "WARN")
    return str(summary.get("status") or "WARN")


def _item_summaries(items: Any) -> list[str]:
    out: list[str] = []
    for item in _list(items):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or f"item {item.get('id')}")
        qty = item.get("quantity")
        if qty is not None:
            out.append(f"{name} x{qty}")
    return out


def has_full_woodcutting_loop_fixture(recordings: list[dict[str, Any]]) -> bool:
    for entry in recordings:
        if entry.get("activityType") != "Woodcutting Loop":
            continue
        evidence_text = " ".join(str(item).lower() for item in _list(entry.get("usefulEvidence")))
        if "loop state complete" in evidence_text:
            return True
    return False


def recording_evidence(summary: dict[str, Any], activity: str) -> list[str]:
    evidence: list[str] = []
    if activity == "Banking":
        lifecycle = _dict(summary.get("banking_lifecycle"))
        bank = _dict(lifecycle.get("bank"))
        deposit = _dict(lifecycle.get("deposit"))
        bank_ui_present = summary.get("bankUiPresent", bank.get("bankUiPresent"))
        if bank_ui_present:
            evidence.append(f"bank_ui snapshots={summary.get('bankUiSnapshotCount', bank.get('bankUiSnapshotCount'))}")
        if summary.get("bankOpenSeen", bank.get("openSeen")):
            evidence.append("bank open directly observed")
        if summary.get("bankContainerAvailable", bank.get("containerAvailable")):
            evidence.append("bank container available")
        delta_available = summary.get("bankContainerDeltaAvailable", lifecycle.get("bankContainerDeltaAvailable"))
        if delta_available:
            evidence.append(f"bank delta={summary.get('depositConfirmationLevel') or lifecycle.get('depositConfirmationLevel') or 'available'}")
        deposited = _item_summaries(summary.get("depositedItems") or deposit.get("items"))
        if deposited:
            evidence.append("deposited " + ", ".join(deposited))
    elif activity == "Route / Traversal":
        lifecycle = _dict(summary.get("traversal_lifecycle"))
        route_name = summary.get("routeName") or summary.get("detectedRouteName") or lifecycle.get("routeName")
        if route_name:
            evidence.append(f"route {route_name}")
        start = summary.get("startArea") or summary.get("detectedStartArea") or _dict(lifecycle.get("start")).get("areaLabel")
        end = summary.get("endArea") or summary.get("detectedEndArea") or _dict(lifecycle.get("end")).get("areaLabel")
        if start or end:
            evidence.append(f"areas {start or '?'} -> {end or '?'}")
        segment_count = summary.get("routeSegmentCount", lifecycle.get("routeSegmentCount"))
        if segment_count is not None:
            evidence.append(f"segments {summary.get('successfulSegmentCount', lifecycle.get('successfulSegmentCount'))}/{segment_count} success")
        if summary.get("routeTemplateStatus"):
            evidence.append(f"template {summary.get('routeTemplateStatus')}")
    elif activity == "Woodcutting":
        lifecycle = _dict(summary.get("woodcutting_lifecycle"))
        if lifecycle.get("phase"):
            evidence.append(f"phase {lifecycle.get('phase')}")
        inventory = _dict(lifecycle.get("inventory"))
        if inventory.get("normalLogsGained") is not None:
            evidence.append(f"logs gained {inventory.get('normalLogsGained')}")
        interruption = _dict(summary.get("interruption_lifecycle") or lifecycle.get("interruption"))
        if interruption and interruption.get("interruptionDetected"):
            evidence.append(f"interruption {interruption.get('interruptionType')} cause={interruption.get('primaryCause')}")
        damage = _dict(summary.get("combat_damage_summary"))
        if damage and damage.get("combatObserved"):
            opponent = _dict(damage.get("primaryOpponent")).get("name")
            taken = _dict(damage.get("damageTaken")).get("total")
            dealt = _dict(damage.get("damageDealt")).get("total")
            hitsplats = _dict(damage.get("hitsplats")).get("total")
            evidence.append(f"combat damage opponent={opponent or '?'} taken={taken} dealt={dealt} hitsplats={hitsplats}")
    elif activity == "Woodcutting Loop":
        lifecycle = _dict(summary.get("woodcutting_loop_lifecycle"))
        next_phase = _dict(lifecycle.get("nextExpectedPhase")).get("phase")
        if lifecycle.get("loopState"):
            evidence.append(f"loop state {lifecycle.get('loopState')}")
        if next_phase:
            evidence.append(f"next phase {next_phase}")
        phases = [str(_dict(item).get("phase")) for item in _list(lifecycle.get("detectedPhases")) if _dict(item).get("phase")]
        if phases:
            evidence.append("phases " + ", ".join(phases[:6]))
        evidence.extend(str(item) for item in _list(lifecycle.get("evidence"))[:4])
    else:
        human = _dict(summary.get("human_click_profile"))
        if human:
            clicks = _dict(human.get("clicks"))
            camera = _dict(human.get("camera"))
            evidence.append(f"human click profile target_relative={clicks.get('targetRelativeClicks')}")
            evidence.append(f"camera segments={camera.get('cameraSegmentCount')}")
        if summary.get("strongTargetClicks") is not None:
            evidence.append(f"strong target clicks {summary.get('strongTargetClicks')}")
        if summary.get("menuSelections") is not None:
            evidence.append(f"menu selections {summary.get('menuSelections')}")
    return evidence[:8]


def recording_missing_data(summary: dict[str, Any], activity: str | None = None) -> list[str]:
    missing: list[str] = []
    for key in ("bankingMissingCapabilities", "missingCapabilities"):
        for item in _list(summary.get(key)):
            missing.append(str(item))
    sections_by_activity = {
        "Banking": ("banking_lifecycle",),
        "Woodcutting": ("woodcutting_lifecycle", "interruption_lifecycle", "combat_damage_summary"),
        "Woodcutting Loop": ("woodcutting_loop_lifecycle", "woodcutting_lifecycle", "banking_lifecycle", "traversal_lifecycle", "interruption_lifecycle", "combat_damage_summary"),
        "Route / Traversal": ("traversal_lifecycle", "route_template_comparison"),
        "Menu Interaction": ("menu_interaction_summary",),
    }
    section_names = sections_by_activity.get(str(activity or ""), ("banking_lifecycle", "interruption_lifecycle", "combat_damage_summary", "traversal_lifecycle", "woodcutting_lifecycle", "route_template_comparison"))
    for section_name in section_names:
        section = _dict(summary.get(section_name))
        for item in _list(section.get("missingCapabilities")):
            missing.append(str(item))
    for item in _list(summary.get("warnings"))[:3]:
        missing.append("warning: " + str(item))
    return sorted(dict.fromkeys(missing))


def summarize_recording(recording: Path) -> dict[str, Any] | None:
    summary = safe_load_json(recording / "summary.json", {})
    if not isinstance(summary, dict) or not summary:
        return None
    activity = detect_activity(summary, recording)
    verdict = best_verdict(summary, activity)
    evidence = recording_evidence(summary, activity)
    missing = recording_missing_data(summary, activity)
    return {
        "recordingId": recording.name,
        "folder": str(recording),
        "label": summary.get("label"),
        "activityType": activity,
        "verdict": verdict,
        "durationSeconds": summary.get("duration_seconds"),
        "tickRange": summary.get("tick_range"),
        "usefulEvidence": evidence,
        "missingData": missing,
        "goodFixture": verdict.startswith("PASS") and bool(evidence),
        "summaryPath": str(recording / "summary.json"),
        "reportPath": str(recording / "schema_gap_report.md") if (recording / "schema_gap_report.md").exists() else None,
    }


def scan_recordings(recording: str | Path | None = None, *, recordings_root: Path | None = None) -> list[dict[str, Any]]:
    if recording:
        target = Path(recording)
        entry = summarize_recording(target)
        return [entry] if entry else []
    entries: list[dict[str, Any]] = []
    for path in recording_dirs(recordings_root):
        entry = summarize_recording(path)
        if entry:
            entries.append(entry)
    return entries


def source_reports(root: Path) -> list[str]:
    docs = root / "docs"
    if not docs.exists():
        return []
    names = sorted(path.name for path in docs.glob("recording_analysis_*.md"))
    return [str(docs / name) for name in names]


def route_template_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted((root / "route_templates").glob("*.route_template.json")):
        payload = safe_load_json(path, {})
        if not isinstance(payload, dict):
            continue
        segments = _list(payload.get("segments"))
        entries.append(
            {
                "path": str(path),
                "routeName": payload.get("routeName") or path.stem.replace(".route_template", ""),
                "templateRevision": payload.get("templateRevision"),
                "requiredSegmentCount": sum(1 for item in segments if _dict(item).get("required", True)),
                "segmentCount": len(segments),
                "variants": [variant.get("variantName") for variant in _list(payload.get("variants")) if isinstance(variant, dict)],
            }
        )
    return entries


def telemetry_field_specs() -> list[dict[str, Any]]:
    try:
        import telemetry_schema
    except Exception:
        return []
    specs = []
    for spec in getattr(telemetry_schema, "FIELD_SPECS", ()):
        specs.append(
            {
                "id": getattr(spec, "id", None),
                "defaultStatus": getattr(spec, "default_category", None),
                "patterns": list(getattr(spec, "patterns", ()) or ())[:8],
            }
        )
    return specs


def base_capabilities(recordings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {entry["recordingId"]: entry for entry in recordings}

    def has_recording(*names: str) -> list[str]:
        found = []
        for name in names:
            if name in by_id:
                found.append(name)
        return found

    return [
        {
            "id": "record_everything_simple_mode",
            "name": "Record Everything Simple Mode",
            "status": "implemented",
            "layers": ["manual_recorder", "telemetry_ui", "analyzer"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit", "20260606_201613_Bank_to_tree_area"),
            "scriptExposure": "indirect through analyzer/context outputs",
            "notes": "Default UI records broad telemetry without requiring route/Arduino/task selection.",
        },
        {
            "id": "polling_input_capture",
            "name": "Polling input capture",
            "status": "implemented",
            "layers": ["manual_recorder", "input_trace_joiner", "input_action_classifier"],
            "evidenceRecordings": has_recording("20260605_204307_manual_action-menu_row_validation_live_mirror_controlled", "20260607_120446_Bank_opening_deposit"),
            "scriptExposure": "context input summaries and analyzer artifacts",
            "notes": "Captures mouse/keyboard/window context and joins to telemetry.",
        },
        {
            "id": "coordinate_alignment",
            "name": "Coordinate alignment",
            "status": "implemented",
            "layers": ["coordinate_spaces", "analyzer"],
            "evidenceRecordings": has_recording("20260605_204307_manual_action-menu_row_validation_live_mirror_controlled"),
            "scriptExposure": "analysis summary",
            "notes": "Client inverse DPI transform is proven in menu/route recordings.",
        },
        {
            "id": "menu_interactions_row_geometry",
            "name": "Menu interactions and row geometry",
            "status": "implemented",
            "layers": ["manual_recorder", "menu_interaction_model", "analyzer"],
            "evidenceRecordings": has_recording("20260605_204307_manual_action-menu_row_validation_live_mirror_controlled"),
            "scriptExposure": "debug/review evidence",
            "notes": "Menu snapshots are paired with selections; missing row geometry is no longer a hard route failure.",
        },
        {
            "id": "target_match_quality",
            "name": "Target match quality",
            "status": "implemented",
            "layers": ["target_match_quality", "analyzer"],
            "evidenceRecordings": has_recording(
                "20260605_204307_manual_action-menu_row_validation_live_mirror_controlled",
                "20260606_094608_manual_route-bank_to_woodcutting_area_v2",
                "20260607_190145_Cutting_a_tree_or_two_with_camera_movement",
            ),
            "scriptExposure": "analysis/context route summaries",
            "notes": "Strong/medium target tiers support traversal and menu diagnostics; hover/menu identity now rejects unrelated nearby geometry through targetAssociation diagnostics.",
        },
        {
            "id": "woodcutting_target_geometry",
            "name": "Woodcutting target aim geometry recovery",
            "status": "partially_implemented",
            "layers": ["manual_recorder", "target_match_quality", "human_click_profile", "schema/capabilities"],
            "evidenceRecordings": has_recording("20260607_190145_Cutting_a_tree_or_two_with_camera_movement"),
            "scriptExposure": "advisory through target_match_quality geometry and human_click_profile landing summaries",
            "notes": "Preserved nearby Tree candidates can now provide aimPoint geometry for hover/menu-resolved Chop down clicks; Tree clickbox/hull geometry remains unavailable in the validated fixture.",
        },
        {
            "id": "human_click_profile",
            "name": "Human click and camera profile",
            "status": "implemented",
            "layers": ["human_click_profile", "analyzer", "context_service", "task_script_api", "knowledge_fabric", "input_control.executor"],
            "evidenceRecordings": has_recording("20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor", "20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory", "20260607_120446_Bank_opening_deposit"),
            "scriptExposure": "get_human_click_profile, get_task_click_profile, click_landing_profile context need, executor handoff",
            "notes": "Aggregates target-relative click variance, hover/menu evidence, camera segments, mouse path, and imperfect successful clicks across Record Everything recordings.",
        },
        {
            "id": "human_click_planning",
            "name": "Human-profile-informed click planning",
            "status": "implemented/advisory",
            "layers": ["input_control.click_planner", "task_script_api", "context_service", "knowledge_fabric", "input_control.executor", "execute_next_action"],
            "evidenceRecordings": ["synthetic dry-run plans", *has_recording("20260607_130931_Bank_to_wood_cutting_area_stair_option_select_bottom_floor", "20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory")],
            "scriptExposure": "get_click_planning_context, get_human_click_plan, get_next_click_plan, context need click_plan, execute_next_action --dry-run-click-plan",
            "notes": "Produces bounded advisory click plans that compare center clicks with profile-informed aim offsets while preserving readiness, hover/menu, route, banking, and inventory gates.",
        },
        {
            "id": "bot_eval_runner",
            "name": "Bot eval runner",
            "status": "implemented/live_guarded",
            "layers": ["bot_eval_runner", "task_script_api", "live_readiness_core", "liveness_recovery_core", "input_control.executor"],
            "evidenceRecordings": has_recording("20260607_171427_Wood_cutting_attacked"),
            "scriptExposure": "bot_eval_runner.py --task woodcutting_loop supports replay, preflight, live smoke, and guarded live action",
            "notes": "Live action delegates readiness/recovery/execution to existing canonical modules and must not silently downgrade to dry-run.",
        },
        {
            "id": "loaded_scene_recovery",
            "name": "Loaded-scene recovery",
            "status": "implemented/live_guarded",
            "layers": ["liveness_recovery_core", "context_service --ensure-loaded-scene", "start_game_command", "execute_next_action", "bot_eval_runner"],
            "evidenceRecordings": [],
            "scriptExposure": "context_service.py --ensure-loaded-scene; bot_eval_runner.py --auto-recover-loaded-scene; execute_next_action.py --auto-recover-loaded-scene",
            "notes": "Recovery owns loaded-scene proof, Start Game relaunch classification, recovery state-machine artifacts, and fail-closed blockers.",
        },
        {
            "id": "input_geometry_resolver",
            "name": "Input geometry resolver",
            "status": "implemented/live_guarded",
            "layers": ["input_control.input_geometry", "live_readiness_core", "bot_eval_runner", "input_control.executor", "telemetry_ui"],
            "evidenceRecordings": [],
            "scriptExposure": "bot_eval_runner.py --check-input-geometry; live readiness inputGeometry; executor pre-click geometry gate",
            "notes": "Resolves plugin/file/window geometry, performs bounded RuneLite focus/visibility repair, verifies screen/client/canvas transforms, and fails closed before gameplay input when geometry is stale or unsafe.",
        },
        {
            "id": "woodcutting_lifecycle",
            "name": "Woodcutting lifecycle",
            "status": "implemented",
            "layers": ["woodcutting_lifecycle", "analyzer", "context_service"],
            "evidenceRecordings": has_recording("20260607_104119_manual_recording_20260607_104113"),
            "scriptExposure": "context_service woodcutting lifecycle and task evidence variables",
            "notes": "Logs, animation, click/menu, and target depletion are summarized when present.",
        },
        {
            "id": "woodcutting_loop_lifecycle",
            "name": "Woodcutting loop lifecycle",
            "status": "implemented",
            "layers": ["woodcutting_loop_lifecycle", "analyzer", "context_service", "task_script_api", "knowledge_fabric"],
            "evidenceRecordings": has_recording(
                "20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory",
                "20260607_104613_Woodcutting_area_to_bank",
                "20260607_120446_Bank_opening_deposit",
                "20260606_201613_Bank_to_tree_area",
                "20260607_154606_Wood_cutting_attacked",
                "20260607_171427_Wood_cutting_attacked",
            ),
            "scriptExposure": "get_woodcutting_loop_lifecycle, get_next_expected_phase, should_route_to_bank, should_route_to_trees",
            "notes": "Combines woodcutting, banking, route, interruption, combat, and human-profile artifacts into current task phase and next expected phase.",
        },
        {
            "id": "combat_interruption_lifecycle",
            "name": "Combat state and interruption lifecycle",
            "status": "implemented",
            "layers": ["RuneLite plugin", "manual_recorder", "interruption_lifecycle", "woodcutting_lifecycle", "analyzer", "context_service", "mcp_server", "task_script_api", "knowledge_fabric"],
            "evidenceRecordings": has_recording("20260607_154606_Wood_cutting_attacked", "20260607_144144_Wood_cutting_area_empty_inventory_to_full_regular_logs"),
            "scriptExposure": "get_combat_state, get_interruption_lifecycle, was_task_interrupted, get_interruption_cause",
            "notes": "Direct mugger fixture proves combat_state preservation, hitsplats, HP change, actor death, and task resume. Older interrupted woodcutting remains useful for unknown-cause WARN behavior.",
        },
        {
            "id": "combat_damage_summary",
            "name": "Combat damage summary",
            "status": "implemented",
            "layers": ["combat_state", "combat_damage_summary", "interruption_lifecycle", "analyzer", "context_service", "mcp_server", "task_script_api", "knowledge_fabric"],
            "evidenceRecordings": has_recording("20260607_154606_Wood_cutting_attacked"),
            "scriptExposure": "get_combat_damage_summary, get_damage_taken, get_damage_dealt, get_primary_opponent, did_take_damage, did_deal_damage",
            "notes": "Computes compact damage taken/dealt, primary opponent, HP delta, actor death, and task resume from preserved combat_state hitsplats and interactions.",
        },
        {
            "id": "traversal_lifecycle",
            "name": "Traversal lifecycle",
            "status": "implemented",
            "layers": ["traversal_lifecycle", "route_template", "analyzer"],
            "evidenceRecordings": has_recording("20260606_094608_manual_route-bank_to_woodcutting_area_v2", "20260606_121630_bank_to_WC"),
            "scriptExposure": "context route summary and route monitor",
            "notes": "Uses routeSegments as primary route model; raw steps are debug evidence.",
        },
        {
            "id": "route_templates_variants",
            "name": "Route templates and variants",
            "status": "implemented",
            "layers": ["route_template", "analyzer", "telemetry_ui"],
            "evidenceRecordings": has_recording("20260606_094608_manual_route-bank_to_woodcutting_area_v2", "20260606_105427_manual_route-bank_to_woodcutting_area_v3"),
            "scriptExposure": "route comparison summaries",
            "notes": "Templates compare routeSegments, allow navigation-support substitutions, and preserve variants.",
        },
        {
            "id": "route_monitor_history",
            "name": "Route monitor and persistent route history",
            "status": "implemented",
            "layers": ["route_monitor", "context_service", "telemetry_ui"],
            "evidenceRecordings": has_recording("20260606_121630_bank_to_WC"),
            "scriptExposure": "route_monitor context needs and history artifacts",
            "notes": "Template path resolution and arrival gating are fixed; live stale data is not trusted.",
        },
        {
            "id": "route_demonstration_guide",
            "name": "Route demonstration guide",
            "status": "implemented/live_guarded",
            "layers": ["route_demonstration", "action_proposal", "task_script_api", "knowledge_fabric", "input_control.executor"],
            "evidenceRecordings": has_recording(
                "20260606_094608_manual_route-bank_to_woodcutting_area_v2",
                "20260606_121630_bank_to_WC",
                "20260607_104613_Woodcutting_area_to_bank",
                "20260606_201613_Bank_to_tree_area",
            ),
            "scriptExposure": "get_route_demonstration_guide, get_route_guide_progress",
            "notes": "Live proposal consumes demonstrated guide progress before stale route monitor fallback and blocks full-inventory Tree fallbacks.",
        },
        {
            "id": "banking_lifecycle",
            "name": "Banking lifecycle",
            "status": "implemented",
            "layers": ["banking_lifecycle", "analyzer", "context_service", "task_script_api"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit", "20260607_104744_Opening_Bank_and_Deposit_all_logs"),
            "scriptExposure": "get_deposit_result, is_bank_open, did_deposit_item",
            "notes": "Direct bank_ui plus inventory and bank container deltas produce PASS when available.",
        },
        {
            "id": "bank_ui_preservation",
            "name": "bank_ui preservation",
            "status": "implemented",
            "layers": ["RuneLite plugin", "telemetry_sources", "manual_recorder", "analyzer"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit"),
            "scriptExposure": "context bank_state and banking lifecycle",
            "notes": "Record Everything preserves bank_ui snapshots from the plugin snapshot endpoint.",
        },
        {
            "id": "bank_container_delta",
            "name": "Bank container delta",
            "status": "implemented",
            "layers": ["RuneLite plugin", "banking_lifecycle", "schema/capabilities"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit"),
            "scriptExposure": "depositConfirmationLevel=bank_container_delta_confirmed",
            "notes": "Plugin exports compact future delta; analyzer can recover historical snapshot diffs.",
        },
        {
            "id": "context_banking_route_needs",
            "name": "Context banking and route needs",
            "status": "implemented",
            "layers": ["context_service", "mcp_server"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit", "20260606_121630_bank_to_WC"),
            "scriptExposure": "banking, deposit_result, route_monitor, route_history",
            "notes": "Compact context avoids dumping giant raw arrays by default.",
        },
        {
            "id": "task_script_banking_api",
            "name": "Task script banking API",
            "status": "implemented",
            "layers": ["task_script_api", "knowledge_fabric"],
            "evidenceRecordings": has_recording("20260607_120446_Bank_opening_deposit"),
            "scriptExposure": "script helpers and runtime evidence variables",
            "notes": "Scripts do not need to parse banking_lifecycle.json directly.",
        },
        {
            "id": "arduino_probe_mapping",
            "name": "Arduino probe/mapping evidence",
            "status": "partial",
            "layers": ["manual_recorder", "arduino_mirror_verifier", "vm_mouse_arduino_mapper"],
            "evidenceRecordings": has_recording("20260605_204307_manual_action-menu_row_validation_live_mirror_controlled"),
            "scriptExposure": "diagnostic only",
            "notes": "Arduino is optional for Record Everything; map_only avoids duplicate live clicks.",
        },
        {
            "id": "live_mirror",
            "name": "Live mirror",
            "status": "advanced_experimental",
            "layers": ["arduino_live_mirror", "manual_recorder", "telemetry_ui diagnostics"],
            "evidenceRecordings": has_recording("20260605_204307_manual_action-menu_row_validation_live_mirror_controlled"),
            "scriptExposure": "not a normal script dependency",
            "notes": "Useful for validation; not required for normal recordings.",
        },
    ]


def open_gaps(*, full_loop_available: bool = False) -> list[dict[str, Any]]:
    gaps = [
        {
            "id": "deposit_all_region_classification",
            "title": "Deposit-All menu-context click can be region-classified as minimap_click",
            "severity": "low",
            "requiredLayer": "analyzer",
            "status": "open",
            "evidence": ["20260607_120446_Bank_opening_deposit"],
            "suggestedNextTask": "Use menu context to override screen-region labels for bank UI menu clicks.",
        },
        {
            "id": "bank_container_slot_provenance",
            "title": "Bank container compact delta lacks slot-level and event provenance",
            "severity": "medium",
            "requiredLayer": "plugin/analyzer",
            "status": "open",
            "evidence": ["bankContainerDelta snapshot diff is implemented"],
            "suggestedNextTask": "Add ItemContainerChanged provenance only if a task needs slot-level proof.",
        },
        {
            "id": "selected_item_spell_widget",
            "title": "Selected item/spell/widget state remains a targeted bridge export gap",
            "severity": "medium",
            "requiredLayer": "plugin",
            "status": "open",
            "evidence": ["telemetry_schema selected_item_spell_widget_state"],
            "suggestedNextTask": "Record an item-on-object or spell interaction sample, then export only the missing selected state.",
        },
        {
            "id": "route_template_coverage",
            "title": "Route template coverage should grow per proven route direction",
            "severity": "medium",
            "requiredLayer": "recording/analyzer",
            "status": "open",
            "evidence": ["Bank_to_Woodcutting_area and woodcutting_area_to_bank templates exist"],
            "suggestedNextTask": "Extract templates for new proven route directions after two clean recordings.",
        },
        {
            "id": "full_woodcutting_loop_fixture",
            "title": "A single full woodcutting loop fixture is still needed",
            "severity": "medium",
            "requiredLayer": "recording",
            "status": "open",
            "evidence": ["Loop phases are proven across separate woodcutting, route, banking, and return recordings."],
            "suggestedNextTask": "Record one full loop from trees to full inventory, bank deposit, return to trees, and resumed chopping.",
        },
        {
            "id": "live_mirror_ownership",
            "title": "Full live mirror ownership remains advanced and experimental",
            "severity": "medium",
            "requiredLayer": "input/arduino",
            "status": "open",
            "evidence": ["map_only duplicate-click prevention is stable"],
            "suggestedNextTask": "Keep normal Record Everything map-only/no-live-click; test live mirror only in isolated validation recordings.",
        },
        {
            "id": "authenticated_start_game_missing",
            "title": "Authenticated live Start Game path must be validated before live bot actions",
            "severity": "high",
            "requiredLayer": "startup/recovery",
            "status": "partially_resolved",
            "evidence": [
                "start_game_command.py separates devStartCommand from liveStartCommand.",
                "Jagex Launcher quick launch is discoverable with --launch=osrs_runelite when installed.",
                "dev_gradle_run remains classified as a dev/plugin launch and is not accepted as the live authenticated start path.",
                "2026-06-13 Jagex quick-launch recovery started RuneLite but stopped at disconnected_dialog / LOGIN_SCREEN with stale_login_screen_after_relaunch.",
            ],
            "suggestedNextTask": "Use the existing safe recovery/launcher session path to clear disconnected/login, then rerun context_service.py --ensure-loaded-scene until loadedSceneVerified=true before live actions.",
        },
        {
            "id": "input_geometry_live_source_stale",
            "title": "Live input geometry must come from a current RuneLite/window source before actions",
            "severity": "medium",
            "requiredLayer": "telemetry/live_readiness/input",
            "status": "open",
            "evidence": ["2026-06-09 check-input-geometry saw refused telemetry endpoints, no RuneLite window match, and only stale file-session geometry."],
            "suggestedNextTask": "Restore or attach RuneLite plus context/snapshot endpoints, then rerun --check-input-geometry and allow live actions only after input_geometry_pass.",
        },
        {
            "id": "clickbox_geometry_incomplete_for_profile",
            "title": "Tree clickbox/hull geometry is still missing after Tree aim recovery",
            "severity": "medium",
            "requiredLayer": "analyzer/geometry",
            "status": "open",
            "evidence": ["20260607_190145_Cutting_a_tree_or_two_with_camera_movement now recovers Tree aimPoint and rejects Gate/Close, but clickboxAvailable remains false and insideClickbox is unknown."],
            "suggestedNextTask": "Record/verify a woodcutting sample with object clickbox or tile polygon export so click-plan validation can compare actual clicks against hull containment, not only aim distance.",
        },
        {
            "id": "menu_row_geometry_profile_gaps",
            "title": "Menu row geometry is sometimes missing in useful human recordings",
            "severity": "low",
            "requiredLayer": "analyzer",
            "status": "open",
            "evidence": ["menu_row_geometry_missing warnings in route and woodcutting recordings"],
            "suggestedNextTask": "Keep menu hover/target/postcondition evidence as backup when row bounds are absent.",
        },
        {
            "id": "pure_normal_logs_woodcutting_fixture",
            "title": "Pure normal-log-only full-inventory woodcutting fixture is still useful",
            "severity": "low",
            "requiredLayer": "recording",
            "status": "open",
            "evidence": ["20260607_131157_Wood_Cutting_area_no_logs_to_full_inventory included normal logs and oak logs"],
            "suggestedNextTask": "Record one tree-area sample that fills inventory with only normal Logs if a task needs item-specific timing.",
        },
        {
            "id": "combat_damage_source_attribution_multi_actor",
            "title": "Multi-actor combat damage attribution still needs a fixture",
            "severity": "low",
            "requiredLayer": "recording",
            "status": "open",
            "evidence": ["20260607_154606_Wood_cutting_attacked proves single Mugger attribution"],
            "suggestedNextTask": "If combat routing matters later, collect a multi-NPC interruption fixture to validate source attribution under ambiguity.",
        },
        {
            "id": "validate_human_click_plans_against_recordings",
            "title": "Human-profile click plans need replay/recording validation before live placement",
            "severity": "medium",
            "requiredLayer": "recording/input_control",
            "status": "open",
            "evidence": ["human_click_planning is advisory and dry-run only"],
            "suggestedNextTask": "Compare dry-run planned aim points against successful human clicks in future Record Everything fixtures before changing live click generation.",
        },
        {
            "id": "knowledge_manual_curation",
            "title": "Project knowledge is generated plus manual notes, not a substitute for curated decisions",
            "severity": "low",
            "requiredLayer": "docs",
            "status": "open",
            "evidence": ["docs/knowledge manual note sections"],
            "suggestedNextTask": "After each milestone, add one concise manual note when generated summaries miss intent.",
        },
    ]
    if full_loop_available:
        gaps = [gap for gap in gaps if gap.get("id") != "full_woodcutting_loop_fixture"]
    return gaps


def decisions() -> list[dict[str, Any]]:
    return [
        {"id": "record_everything_default", "decision": "Record Everything Simple Mode is the default workflow.", "reason": "Record broadly first; analyzer decides what matters later."},
        {"id": "no_required_arduino", "decision": "Arduino is optional for recording and route monitoring.", "reason": "Human recordings must still be useful without hardware."},
        {"id": "map_only_default", "decision": "map_only is the safe default when Arduino mapping evidence is enabled.", "reason": "Avoid duplicate live Arduino clicks."},
        {"id": "route_segments_primary", "decision": "Route templates compare routeSegments, not raw clicks.", "reason": "Raw clicks include support/review evidence and normal route variants."},
        {"id": "bank_to_wc_rev3", "decision": "Door/Open is optional for Bank_to_Woodcutting_area revision 3.", "reason": "Walk here Large door can be navigation support; required segments are start, walk, stair, walk, arrival."},
        {"id": "bank_ui_direct_proof", "decision": "Banking must consume direct bank_ui when available.", "reason": "Inventory-only inference is useful but weaker than direct widget/container evidence."},
        {"id": "promote_useful_data", "decision": "Useful telemetry should not stay recorder-only when scripts need it.", "reason": "Promote through analyzer, context_service, MCP where useful, and task_script_api."},
        {"id": "human_click_profile_reference", "decision": "Human click/camera behavior is a profile reference, not an execution shortcut.", "reason": "Use it to shape tolerances and recommendations while preserving existing guarded input paths."},
        {"id": "human_click_planning_advisory", "decision": "Human-profile click planning is dry-run/advisory until replay validation proves it.", "reason": "Target readiness, hover/menu proof, and task state gates must remain stronger than a profile offset."},
        {"id": "knowledge_repo_owned", "decision": "Project state belongs in docs/knowledge and telemetry-viewer/knowledge_base.", "reason": "Do not rely on chat memory alone."},
        {"id": "live_start_separate_from_dev_start", "decision": "Live bot recovery uses liveStartCommand or discovered Jagex quick launch, not devStartCommand.", "reason": "Gradle launches are useful for plugin development but do not prove launcher-authenticated loaded-scene access."},
    ]


def next_tasks(*, full_loop_available: bool = False) -> list[dict[str, Any]]:
    tasks = [
        {
            "id": "validate_authenticated_live_start",
            "priority": 1,
            "task": "Validate the discovered Jagex Launcher RuneLite quick-launch path and loaded-scene recovery.",
            "successCriteria": [
                "start_game_command.py --validate-live reports jagex_launcher_runelite_quick_launch.",
                "context_service.py --ensure-loaded-scene reaches loadedSceneVerified=true without dev_launch_not_loaded.",
            ],
        },
        {
            "id": "fix_deposit_region_label",
            "priority": 1,
            "task": "Clean up Deposit-All menu-context region classification.",
            "successCriteria": ["Deposit-All bank UI clicks no longer appear as minimap_click when menu context proves bank UI."],
        },
        {
            "id": "record_second_bank_direct_sample",
            "priority": 2,
            "task": "Record another bank open/deposit/close sample with bankContainerDelta explicit plugin field.",
            "successCriteria": ["bankContainerDeltaSource is explicit plugin delta or recorded diff, lifecycle PASS."],
        },
        {
            "id": "extract_more_route_templates",
            "priority": 3,
            "task": "Extract templates for new route directions after repeated PASS recordings.",
            "successCriteria": ["Two clean route recordings compare PASS against the new template."],
        },
        {
            "id": "record_full_woodcutting_loop",
            "priority": 4,
            "task": "Record one full tree-to-bank-to-tree woodcutting loop.",
            "successCriteria": ["woodcutting_loop_lifecycle detects cutting, full inventory, route to bank, deposit, route to trees, and resumed cutting."],
        },
        {
            "id": "selected_state_recording",
            "priority": 5,
            "task": "Record a selected item/spell/widget interaction to decide the smallest bridge export.",
            "successCriteria": ["Schema gap identifies exact selected-state fields needed."],
        },
    ]
    if full_loop_available:
        tasks = [task for task in tasks if task.get("id") != "record_full_woodcutting_loop"]
    return tasks


def api_data_paths() -> list[dict[str, Any]]:
    return [
        {
            "family": "banking",
            "pluginLiveSource": "TelemetryPlugin bank_ui payload via plugin snapshot endpoint need=bank_ui",
            "recorderArtifact": "events.jsonl source=bank_ui plus summary source files",
            "analyzerOutput": "banking_lifecycle.json / summary banking fields",
            "contextField": "banking, bank_state, inventory_delta, deposit_result",
            "mcpField": "get_banking_state, get_banking_lifecycle, get_inventory_delta, get_deposit_result",
            "scriptApi": "get_bank_state, get_deposit_result, did_deposit_item",
            "tests": "test_banking_lifecycle.py, test_context_service.py, test_task_script_api.py",
        },
        {
            "family": "route/traversal",
            "pluginLiveSource": "player world/plane, objects, menu/click telemetry",
            "recorderArtifact": "events.jsonl, joined_input_telemetry.jsonl, target/menu summaries",
            "analyzerOutput": "traversal_lifecycle.json, route_template_comparison.json, route_history_summary.json",
            "contextField": "route_monitor, route_history, latest_recording_traversal",
            "mcpField": "get_context with route needs",
            "scriptApi": "routeProgress runtime evidence variable",
            "tests": "test_traversal_lifecycle.py, test_route_template.py, test_route_monitor.py",
        },
        {
            "family": "woodcutting",
            "pluginLiveSource": "inventory, player animation, candidate objects, menu/click telemetry",
            "recorderArtifact": "events.jsonl and input/target summaries",
            "analyzerOutput": "woodcutting_lifecycle.json, interruption_lifecycle.json when stop/resume or combat evidence appears",
            "contextField": "woodcutting_lifecycle, interruption_lifecycle",
            "mcpField": "get_woodcutting_lifecycle, get_interruption_lifecycle",
            "scriptApi": "resourceCount, inventory, phaseIntent, interruptionLifecycle runtime variables",
            "tests": "test_woodcutting_lifecycle.py, test_interruption_lifecycle.py, test_task_script_api.py",
        },
        {
            "family": "woodcutting_loop",
            "pluginLiveSource": "existing woodcutting, bank_ui, route/traversal, combat_state, and human-input telemetry",
            "recorderArtifact": "existing lifecycle artifacts plus summary.json",
            "analyzerOutput": "woodcutting_loop_lifecycle.json / summary woodcuttingLoop fields",
            "contextField": "woodcutting_loop, woodcutting_loop_lifecycle, task_loop, next_expected_phase",
            "mcpField": "get_context with woodcutting_loop needs",
            "scriptApi": "get_woodcutting_loop_lifecycle, get_next_expected_phase, should_route_to_bank, should_route_to_trees",
            "tests": "test_woodcutting_loop_lifecycle.py, test_context_service.py, test_task_script_api.py",
        },
        {
            "family": "combat/interruption",
            "pluginLiveSource": "TelemetryPlugin combat_state payload via plugin snapshot endpoint need=combat_state",
            "recorderArtifact": "events.jsonl source=combat_state plus summary source files",
            "analyzerOutput": "interruption_lifecycle.json, combat_damage_summary.json / summary combat fields",
            "contextField": "combat_state, combat, interruption_lifecycle, combat_damage_summary, damage_taken, damage_dealt, primary_opponent",
            "mcpField": "get_combat_state, get_interruption_lifecycle, get_combat_damage_summary, get_damage_taken, get_damage_dealt, get_primary_opponent",
            "scriptApi": "get_combat_state, is_in_combat, get_interruption_lifecycle, get_combat_damage_summary, get_damage_taken, get_damage_dealt, did_take_damage, did_deal_damage",
            "tests": "test_interruption_lifecycle.py, test_combat_damage_summary.py, test_context_service.py, test_task_script_api.py",
        },
        {
            "family": "menu/input",
            "pluginLiveSource": "PostMenuSort/MenuOptionClicked/client tick menu state",
            "recorderArtifact": "input_events.jsonl, menu_interactions.jsonl, target_match_quality.jsonl",
            "analyzerOutput": "menu_interaction_summary.json, target_match_summary.json",
            "contextField": "debug/action input visibility and target/menu summaries",
            "mcpField": "get_action_input_visibility, get_latest_action_trace",
            "scriptApi": "hoverTarget and menuOptionClicked runtime variables",
            "tests": "test_menu_interaction_model.py, test_input_action_classifier.py, test_target_match_quality.py",
        },
        {
            "family": "human_click_profile",
            "pluginLiveSource": "mouse/input events, menu state, target candidates, camera yaw/pitch, lifecycle postconditions",
            "recorderArtifact": "input_events.jsonl, input_action_classifications.jsonl, target_match_quality.jsonl, camera_behavior_summary.json",
            "analyzerOutput": "human_click_profile.json / summary human_click_profile fields",
            "contextField": "human_click_profile, task_click_profile, click_landing_profile, camera_action_profile",
            "mcpField": "get_context with human_click_profile needs",
            "scriptApi": "get_human_click_profile, get_task_click_profile, get_click_landing_profile, get_camera_action_profile",
            "tests": "test_human_click_profile.py, test_context_service.py, test_task_script_api.py",
        },
        {
            "family": "human_click_planning",
            "pluginLiveSource": "target/readiness candidates, hover/menu evidence, route/banking/woodcutting loop state, and human_click_profile",
            "recorderArtifact": "recording artifacts remain unchanged; planner consumes analyzer/context/script summaries",
            "analyzerOutput": "dry-run human_click_plan.v1 reports and optional summary fields",
            "contextField": "click_plan, human_click_plan, click_planning_context",
            "mcpField": "get_context with click_plan needs",
            "scriptApi": "get_click_planning_context, get_human_click_plan, get_next_click_plan",
            "tests": "test_human_click_planning.py, test_context_service.py, test_task_script_api.py, test_input_control_executor.py",
        },
        {
            "family": "arduino/mapping",
            "pluginLiveSource": "Raw Input and VM mouse mapping evidence where available",
            "recorderArtifact": "arduino_* artifacts, vm_mouse_arduino_mapping.json",
            "analyzerOutput": "input_path_integrity_summary.json, arduino_mirror_verification.json",
            "contextField": "input path and mirror verification summaries",
            "mcpField": "debug/action input visibility",
            "scriptApi": "inputIntegrity runtime evidence variable",
            "tests": "test_arduino_mirror_verifier.py, test_vm_mouse_arduino_mapper.py",
        },
        {
            "family": "input_geometry",
            "pluginLiveSource": "RuneLite/plugin canvas fields when fresh, Win32 RuneLite window/client geometry as bounded repair fallback",
            "recorderArtifact": "live_baseline_state.json inputGeometry and input trace geometry fields",
            "analyzerOutput": "bot input geometry readiness report and executor action traces",
            "contextField": "live readiness inputGeometry / inputGeometryReady",
            "mcpField": "diagnostic through context/readiness surfaces",
            "scriptApi": "bot_eval_runner.py --check-input-geometry",
            "tests": "test_live_readiness.py, test_bot_eval_runner.py, test_input_control_executor.py, test_telemetry_ui.py",
        },
    ]


def script_api_map() -> list[dict[str, Any]]:
    functions = [
        ("get_bank_state(source)", "Bank/deposit UI state, bank container availability, bank_ui freshness.", "banking_lifecycle.bank / context bank_state"),
        ("get_banking_lifecycle(source)", "Compact lifecycle status, phase, confidence, warnings.", "banking_lifecycle.json / context banking_lifecycle"),
        ("is_bank_open(source)", "Boolean direct bank-open proof.", "bank_state.bankOpen"),
        ("is_deposit_box_open(source)", "Boolean direct deposit-box proof.", "bank_state.depositBoxOpen"),
        ("get_active_bank_like_interface(source)", "bank, deposit_box, or unknown.", "bank_state.activeBankLikeInterface"),
        ("get_inventory_delta(source)", "Free slots and deposited/withdrawn item deltas.", "banking_lifecycle.inventory"),
        ("get_deposit_result(source)", "Deposit complete, items, confidence, confirmation level.", "banking_lifecycle.deposit"),
        ("get_deposited_items(source)", "List of deposited item summaries.", "deposit_result.depositedItems"),
        ("did_deposit_item(source, item_id)", "True when a deposited item id is present.", "deposit_result.depositedItems"),
        ("get_banking_missing_capabilities(source)", "Compact list of missing banking capabilities.", "banking_lifecycle.missingCapabilities"),
        ("get_combat_state(source)", "Compact combat targeting, hitsplat, hostile NPC, and health evidence.", "interruption_lifecycle.combat / combat_state"),
        ("is_in_combat(source)", "Boolean direct combat observation.", "interruption_lifecycle.combat.combatObserved"),
        ("get_interruption_lifecycle(source)", "Compact interruption status, cause, resume, confidence, and warnings.", "interruption_lifecycle.json"),
        ("was_task_interrupted(source)", "Boolean task interruption/resume or combat/message/stat signal.", "interruption_lifecycle.interruptionDetected"),
        ("get_interruption_cause(source)", "Primary interruption cause such as hostile_npc, mugger_attack, level_up, or unknown.", "interruption_lifecycle.primaryCause"),
        ("get_combat_damage_summary(source)", "Compact damage taken/dealt, opponent, HP, hitsplat, actor death, and task resume evidence.", "combat_damage_summary.json"),
        ("get_damage_taken(source)", "Damage taken total, hitsplat count, and HP before/after evidence.", "combat_damage_summary.damageTaken / health"),
        ("get_damage_dealt(source)", "Damage dealt total, hitsplat count, and target evidence.", "combat_damage_summary.damageDealt"),
        ("get_primary_opponent(source)", "Primary opponent name/id/confidence.", "combat_damage_summary.primaryOpponent"),
        ("did_take_damage(source)", "Boolean damage-taken proof from amount, HP change, or player hitsplats.", "combat_damage_summary.damageTaken"),
        ("did_deal_damage(source)", "Boolean damage-dealt proof from amount or opponent hitsplats.", "combat_damage_summary.damageDealt"),
        ("get_recent_hitsplats(source)", "Recent hitsplat evidence from combat_state/interruption lifecycle.", "combat_state.recentHitsplats"),
        ("get_recent_stat_changes(source)", "Recent stat/level change evidence.", "combat_state.recentStatChanges"),
        ("get_recent_game_messages(source)", "Recent chat/game message evidence.", "combat_state.recentChatMessages"),
        ("get_human_click_profile(source)", "Compact aggregate human click/camera profile.", "human_click_profile.json"),
        ("get_task_click_profile(activity, source)", "Task-specific click profile bucket.", "human_click_profile.taskProfiles"),
        ("get_click_landing_profile(activity, source)", "Aim distance and clickbox/menu-row landing summary.", "human_click_profile.landing"),
        ("get_camera_action_profile(activity, source)", "Camera segment and camera-before-click summary.", "human_click_profile.camera"),
        ("get_click_planning_context(activity, source)", "Compact task, route, banking, target/readiness, and profile context for advisory planning.", "task_script_api runtime state + human_click_profile"),
        ("get_human_click_plan(target, action, activity, source)", "Human-profile-informed advisory click plan with blockers, confidence, and center-vs-profile aim.", "input_control.click_planner"),
        ("get_next_click_plan(source)", "Best available advisory next-click plan from current script evidence.", "task_script_api + click_planner"),
        ("get_woodcutting_loop_lifecycle(source)", "Compact full woodcutting task loop phase and next expected phase.", "woodcutting_loop_lifecycle.json"),
        ("get_current_task_phase(source)", "Current woodcutting loop phase.", "woodcutting_loop_lifecycle.currentPhase"),
        ("get_next_expected_phase(source)", "Next expected woodcutting loop phase.", "woodcutting_loop_lifecycle.nextExpectedPhase"),
        ("is_inventory_full_for_woodcutting(source)", "Boolean inventory-full gate for routing to bank.", "woodcutting_loop_lifecycle.woodcutting"),
        ("did_deposit_logs(source)", "Boolean proof that logs were deposited.", "woodcutting_loop_lifecycle.banking.depositedItems"),
        ("should_route_to_bank(source)", "True when the loop next phase is route_to_bank.", "woodcutting_loop_lifecycle.nextExpectedPhase"),
        ("should_route_to_trees(source)", "True when the loop next phase is route_to_woodcutting_area.", "woodcutting_loop_lifecycle.nextExpectedPhase"),
        ("was_interrupted(source)", "Boolean interruption flag from loop/interruption lifecycle.", "woodcutting_loop_lifecycle.interruptions"),
        ("did_resume_after_interruption(source)", "Boolean task-resumed proof from loop/interruption lifecycle.", "woodcutting_loop_lifecycle.interruptions"),
        ("build_task_script_evidence_plan(script)", "Variables a script must prove before/after primitives.", "task_script_api runtime evidence catalog"),
        ("compare_task_runtime_evidence_snapshots(before, after)", "State-delta proof for script steps.", "task runtime evidence snapshots"),
    ]
    return [
        {"function": name, "purpose": purpose, "underlyingTelemetry": source, "status": "implemented"}
        for name, purpose, source in functions
    ]


def activity_knowledge(recordings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_activity: dict[str, list[dict[str, Any]]] = {}
    for entry in recordings:
        by_activity.setdefault(str(entry.get("activityType") or "Generic Telemetry"), []).append(entry)
    full_loop_available = has_full_woodcutting_loop_fixture(recordings)
    return [
        {
            "activity": "Banking",
            "knownSignals": ["bank_ui", "bankOpen/depositBoxOpen", "bank widget/root", "bank container", "inventory delta", "deposit/withdraw menu context"],
            "usefulFields": ["bankContainerDeltaAvailable", "depositConfirmationLevel", "depositedItems", "missingCapabilities"],
            "lifecycleOutputs": ["banking_lifecycle.json", "summary banking fields"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Banking", []) if entry.get("goodFixture")],
            "gaps": ["deposit_all_region_classification", "bank_container_slot_provenance"],
        },
        {
            "activity": "Route / Traversal",
            "knownSignals": ["routeSegments", "world path", "plane changes", "target quality", "menu row evidence"],
            "usefulFields": ["routeName", "routeSegments", "routeTemplateStatus", "routeState"],
            "lifecycleOutputs": ["traversal_lifecycle.json", "route_template_comparison.json", "route_history_summary.json"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Route / Traversal", []) if entry.get("goodFixture")],
            "gaps": ["route_template_coverage"],
        },
        {
            "activity": "Woodcutting",
            "knownSignals": ["tree target", "Chop down", "animation", "inventory log delta", "target depletion", "interruption stop/resume gaps", "combat damage/resume evidence"],
            "usefulFields": ["phase", "normalLogsGained", "freshChopClickCount", "interruption.interruptionType", "combatDamageSummary.damageTakenTotal"],
            "lifecycleOutputs": ["woodcutting_lifecycle.json", "interruption_lifecycle.json", "combat_damage_summary.json"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Woodcutting", []) if entry.get("goodFixture")],
            "gaps": ["pure_normal_logs_woodcutting_fixture", "combat_damage_source_attribution_multi_actor"],
        },
        {
            "activity": "Woodcutting Loop",
            "knownSignals": ["woodcutting lifecycle", "inventory fullness", "route to bank", "bank deposit", "route to trees", "interruption resume"],
            "usefulFields": ["loopState", "currentPhase", "nextExpectedPhase", "detectedPhases", "depositComplete", "taskResumed"],
            "lifecycleOutputs": ["woodcutting_loop_lifecycle.json"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Woodcutting Loop", []) if entry.get("goodFixture")],
            "gaps": ["pure_normal_logs_woodcutting_fixture"] if full_loop_available else ["full_woodcutting_loop_fixture", "pure_normal_logs_woodcutting_fixture"],
        },
        {
            "activity": "Combat / Interruption",
            "knownSignals": ["combat_state", "NPC/player interaction", "hitsplats", "HP changes", "chat/game messages", "stat changes", "task stop/resume"],
            "usefulFields": ["interruptionType", "primaryCause", "taskResumed", "combat.hitsplatsSeen", "damageTakenTotal", "damageDealtTotal", "primaryOpponent"],
            "lifecycleOutputs": ["interruption_lifecycle.json", "combat_damage_summary.json"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Woodcutting", []) if any("interruption" in str(item).lower() for item in entry.get("usefulEvidence") or [])],
            "gaps": ["combat_damage_source_attribution_multi_actor"],
        },
        {
            "activity": "Menu Interaction",
            "knownSignals": ["menuBounds", "entries", "row bounds", "MenuOptionClicked", "target match"],
            "usefulFields": ["menuSelectionCount", "rowGeometryProven", "selectedSnapshotId"],
            "lifecycleOutputs": ["menu_interaction_summary.json", "target_match_summary.json"],
            "provenRecordings": [entry["recordingId"] for entry in by_activity.get("Menu Interaction", []) if entry.get("goodFixture")],
            "gaps": ["selected_item_spell_widget"],
        },
        {
            "activity": "Input / Camera / Arduino",
            "knownSignals": ["input_events", "raw OS clicks", "camera segments", "mapping", "mirror verification", "human click/camera profile"],
            "usefulFields": ["inputPathIntegrity", "coordinateTransform", "clickPolicyUsed", "duplicateClickLikelyCount", "medianAimDistancePx", "imperfectSuccessfulClickCount"],
            "lifecycleOutputs": ["input_action_summary.json", "camera_behavior_summary.json", "input_path_integrity_summary.json", "human_click_profile.json"],
            "provenRecordings": [entry["recordingId"] for entry in recordings if "input" in " ".join(entry.get("usefulEvidence") or []).lower()],
            "gaps": ["live_mirror_ownership", "clickbox_geometry_incomplete_for_profile", "menu_row_geometry_profile_gaps"],
        },
    ]


def build_project_knowledge(
    *,
    recording: str | Path | None = None,
    scan_all_recordings: bool = True,
    root: Path | None = None,
) -> dict[str, Any]:
    base = root or repo_root()
    if recording and not scan_all_recordings:
        recordings = scan_recordings(recording)
    else:
        recordings = scan_recordings(recordings_root=base / "recordings")
        if recording:
            target_entry = summarize_recording(Path(recording))
            if target_entry and target_entry["recordingId"] not in {entry["recordingId"] for entry in recordings}:
                recordings.append(target_entry)
    full_loop_available = has_full_woodcutting_loop_fixture(recordings)
    caps = base_capabilities(recordings)
    gaps = open_gaps(full_loop_available=full_loop_available)
    scripts = script_api_map()
    paths = api_data_paths()
    activities = activity_knowledge(recordings)
    route_templates = route_template_entries(base)
    return {
        "schema": PROJECT_KNOWLEDGE_SCHEMA,
        "updatedAtUtc": utc_now(),
        "repoRoot": str(base),
        "sourceReports": source_reports(base),
        "routeTemplates": route_templates,
        "capabilities": caps,
        "recordings": recordings,
        "activityKnowledge": activities,
        "apiDataPaths": paths,
        "scriptApis": scripts,
        "telemetryFieldSpecs": telemetry_field_specs(),
        "gaps": gaps,
        "decisions": decisions(),
        "nextTasks": next_tasks(full_loop_available=full_loop_available),
    }


def write_indexes(model: dict[str, Any], *, knowledge_out: str | Path | None = None) -> dict[str, str]:
    out = Path(knowledge_out) if knowledge_out else repo_root() / "telemetry-viewer" / "knowledge_base"
    out.mkdir(parents=True, exist_ok=True)
    updated = model.get("updatedAtUtc")
    paths = {
        "project": atomic_write_json(out / "project_knowledge.json", model),
        "recordings": atomic_write_json(out / "recordings_index.json", {"schema": RECORDINGS_INDEX_SCHEMA, "updatedAtUtc": updated, "recordings": model.get("recordings") or []}),
        "capabilities": atomic_write_json(out / "capability_registry.json", {"schema": CAPABILITY_REGISTRY_SCHEMA, "updatedAtUtc": updated, "capabilities": model.get("capabilities") or [], "telemetryFieldSpecs": model.get("telemetryFieldSpecs") or []}),
        "scriptApi": atomic_write_json(out / "script_api_map.json", {"schema": SCRIPT_API_MAP_SCHEMA, "updatedAtUtc": updated, "scriptApis": model.get("scriptApis") or [], "apiDataPaths": model.get("apiDataPaths") or []}),
        "openGaps": atomic_write_json(out / "open_gaps.json", {"schema": OPEN_GAPS_SCHEMA, "updatedAtUtc": updated, "gaps": model.get("gaps") or [], "nextTasks": model.get("nextTasks") or []}),
    }
    return {key: str(path) for key, path in paths.items()}


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = []
        for value in row:
            text = str(value if value is not None else "").replace("\n", " ").replace("|", "\\|")
            cells.append(text)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_project_state(model: dict[str, Any]) -> str:
    return f"""# Project State

Updated: `{model.get('updatedAtUtc')}`

## Current Architecture

RuneLite plugin / bridge exports read-only live telemetry through the snapshot
endpoint. Record Everything preserves useful live packets into recordings.
Analyzer modules convert those recordings into lifecycle and quality artifacts.
`context_service.py`, MCP wrappers, and `task_script_api.py` expose compact,
script-readable summaries.

## Stable Workflow

1. Open OSRS Telemetry Recorder.
2. Start Game.
3. Start Telemetry.
4. Start Recording.
5. Do the task normally.
6. Stop Recording.
7. Let automatic analysis finish.
8. Read the summary or open the output folder.

## What Not To Rebuild

- Do not replace Record Everything with per-task recording knobs.
- Do not create a second live input API.
- Do not make scripts parse raw recording JSON when context/task APIs expose
  compact fields.
- Do not treat route raw clicks as required template progress; use routeSegments.

## Main Checks

```powershell
python telemetry-viewer\\update_project_knowledge.py --check
python telemetry-viewer\\telemetry_ui.py --check
python telemetry-viewer\\tests\\test_project_knowledge.py
```
"""


def render_entrypoints(_model: dict[str, Any]) -> str:
    rows = [
        ["Start Game", "`telemetry-viewer\\start_game_command.py` (`resolve_start_game_command`, `launch_start_game`)", "`telemetry_ui.py`, `bot_eval_runner.py`, recovery scripts", "UI, recovery, and bot eval share launch classification. `devStartCommand` is for Gradle/plugin testing; live recovery must use `liveStartCommand`, discovered Jagex quick launch, or an already-loaded client."],
        ["Loaded-scene recovery", "`liveness_recovery_core.py`; `context_service.py --ensure-loaded-scene`", "Bot runners, UI, executor ad hoc relaunch code", "`execute_next_action.py --auto-recover-loaded-scene` and `bot_eval_runner.py --auto-recover-loaded-scene` may call this path."],
        ["Live readiness", "`live_readiness_core.py`; bot preflight/readiness wrappers", "Bot eval one-off checks, UI-only checks", "Use shared readiness logic or context-service equivalent."],
        ["Input geometry", "`input_control\\input_geometry.py` (`resolve_input_geometry_status`, `repair_runelite_focus`, `validate_screen_point_inside_geometry`)", "`live_readiness_core.py`, `bot_eval_runner.py`, `input_control\\executor.py`, `telemetry_ui.py` ad hoc geometry checks", "`bot_eval_runner.py --check-input-geometry` and executor pre-click gates must use this resolver."],
        ["Record Everything", "`telemetry_ui.py` Simple Mode; `manual_recorder.py`; `analyze_manual_recording.py`; `update_project_knowledge.py`", "Per-task recorder forks", "Broad capture is the default and analyzer decides what matters."],
        ["Knowledge base", "`docs\\knowledge`; `telemetry-viewer\\knowledge_base`; `update_project_knowledge.py`", "Chat-only memory, stale handoff docs", "Update docs and JSON indexes after telemetry/API/analyzer/context changes."],
        ["Bot eval", "`bot_eval_runner.py`", "New live-loop launchers", "Replay, preflight, live smoke, and guarded live action belong here."],
        ["Script-facing API", "`task_script_api.py`; `knowledge_fabric.py`", "Scripts parsing raw recording JSON", "Scripts consume compact helpers and evidence variables."],
        ["Context API", "`context_service.py`; `mcp_server.py`", "New mutable MCP/input endpoints", "Context/MCP surfaces are read-only unless explicitly changed."],
        ["Click/action planning", "`input_control\\click_planner.py`; `input_control\\action_proposal.py`; `input_control\\executor.py`; `candidate_core.py`", "Bot eval runner, route monitor, UI", "Planner is advisory until guarded executor/readiness proves live action is safe."],
        ["Routes", "`route_template.py`; `route_monitor.py`; `route_demonstration.py`; `traversal_lifecycle.py`", "Bot eval route parsers, raw-click template logic", "Use route segments/templates/guides; raw clicks are support evidence."],
        ["Banking", "`TelemetryPlugin.java` (`bank_ui`, `bankContainerDelta`); `banking_lifecycle.py`; task_script_api banking helpers", "Inventory-only deposit inference paths", "Direct bank UI/container evidence outranks inference."],
        ["Woodcutting", "`woodcutting_lifecycle.py`; `woodcutting_loop_lifecycle.py`", "Bot eval phase reimplementation", "Loop state combines task lifecycle evidence."],
        ["Combat/interruption", "`interruption_lifecycle.py`; `combat_damage_summary.py`", "Woodcutting-only combat heuristics", "Combat cause/damage summaries stay independent."],
        ["Human profile", "`human_click_profile.py`; `click_planner.py` consumes it in advisory mode", "Executor randomization or click shortcuts", "Human profile informs tolerances and recommendations, not bypasses."],
    ]
    return "# Entrypoints And Ownership\n\nFuture cleanup and live-bot work must reuse these canonical paths.\n\n" + _table(
        ["Responsibility", "Canonical module/function/command", "Do not duplicate in", "Notes"],
        rows,
    ) + "\n"


def render_capability_registry(model: dict[str, Any]) -> str:
    rows = []
    for cap in _list(model.get("capabilities")):
        rows.append([
            cap.get("id"),
            cap.get("status"),
            ", ".join(_list(cap.get("layers"))),
            ", ".join(_list(cap.get("evidenceRecordings"))),
            cap.get("scriptExposure"),
        ])
    return "# Capability Registry\n\n" + _table(["Capability", "Status", "Layers", "Evidence recordings", "Script/API exposure"], rows) + "\n"


def render_recording_index(model: dict[str, Any]) -> str:
    rows = []
    for rec in _list(model.get("recordings")):
        rows.append([
            rec.get("recordingId"),
            rec.get("activityType"),
            rec.get("verdict"),
            "; ".join(_list(rec.get("usefulEvidence"))[:4]),
            "; ".join(_list(rec.get("missingData"))[:3]),
            "yes" if rec.get("goodFixture") else "no",
        ])
    return "# Recording Index\n\n" + _table(["Recording", "Activity", "Verdict", "Useful evidence", "Missing data / warnings", "Good fixture"], rows) + "\n"


def render_activity_knowledge(model: dict[str, Any]) -> str:
    lines = ["# Activity Knowledge", ""]
    for item in _list(model.get("activityKnowledge")):
        lines.extend(
            [
                f"## {item.get('activity')}",
                "",
                f"- Known signals: {', '.join(_list(item.get('knownSignals')))}",
                f"- Useful fields: {', '.join(_list(item.get('usefulFields')))}",
                f"- Lifecycle outputs: {', '.join(_list(item.get('lifecycleOutputs')))}",
                f"- Proven recordings: {', '.join(_list(item.get('provenRecordings'))) or 'none yet'}",
                f"- Gaps: {', '.join(_list(item.get('gaps'))) or 'none tracked'}",
                "",
            ]
        )
    return "\n".join(lines)


def render_api_data_paths(model: dict[str, Any]) -> str:
    rows = []
    for path in _list(model.get("apiDataPaths")):
        rows.append([
            path.get("family"),
            path.get("pluginLiveSource"),
            path.get("recorderArtifact"),
            path.get("analyzerOutput"),
            path.get("contextField"),
            path.get("mcpField"),
            path.get("scriptApi"),
        ])
    return "# API Data Paths\n\n" + _table(["Family", "Plugin/live", "Recorder", "Analyzer", "Context API", "MCP", "Script API"], rows) + "\n"


def render_script_api_map(model: dict[str, Any]) -> str:
    rows = []
    for api in _list(model.get("scriptApis")):
        rows.append([api.get("function"), api.get("purpose"), api.get("underlyingTelemetry"), api.get("status")])
    return "# Script API Map\n\n" + _table(["Function/object", "Purpose", "Consumes", "Status"], rows) + "\n\nExample:\n\n```python\nimport task_script_api as api\nresult = api.get_deposit_result(recording_folder)\napi.did_deposit_item(recording_folder, 1511)\n```\n"


def render_open_gaps(model: dict[str, Any]) -> str:
    rows = []
    for gap in _list(model.get("gaps")):
        rows.append([gap.get("id"), gap.get("severity"), gap.get("requiredLayer"), gap.get("status"), gap.get("suggestedNextTask")])
    return "# Open Gaps\n\n" + _table(["Gap", "Severity", "Layer", "Status", "Suggested next task"], rows) + "\n"


def render_decisions(model: dict[str, Any]) -> str:
    rows = [[item.get("id"), item.get("decision"), item.get("reason")] for item in _list(model.get("decisions"))]
    return "# Decisions\n\n" + _table(["ID", "Decision", "Reason"], rows) + "\n"


def render_next_tasks(model: dict[str, Any]) -> str:
    rows = [[item.get("priority"), item.get("id"), item.get("task"), "; ".join(_list(item.get("successCriteria")))] for item in _list(model.get("nextTasks"))]
    return "# Next Tasks\n\n" + _table(["Priority", "ID", "Task", "Success criteria"], rows) + "\n"


DOC_RENDERERS = {
    "PROJECT_STATE.md": render_project_state,
    "ENTRYPOINTS.md": render_entrypoints,
    "CAPABILITY_REGISTRY.md": render_capability_registry,
    "RECORDING_INDEX.md": render_recording_index,
    "ACTIVITY_KNOWLEDGE.md": render_activity_knowledge,
    "API_DATA_PATHS.md": render_api_data_paths,
    "SCRIPT_API_MAP.md": render_script_api_map,
    "OPEN_GAPS.md": render_open_gaps,
    "DECISIONS.md": render_decisions,
    "NEXT_TASKS.md": render_next_tasks,
}


def write_docs(model: dict[str, Any], *, docs_out: str | Path | None = None) -> dict[str, str]:
    out = Path(docs_out) if docs_out else repo_root() / "docs" / "knowledge"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for name, renderer in DOC_RENDERERS.items():
        written[name] = str(write_text_preserving_manual_notes(out / name, renderer(model)))
    return written


def update_knowledge(
    *,
    recording: str | Path | None = None,
    scan_recordings: bool = True,
    write_docs_flag: bool = False,
    knowledge_out: str | Path | None = None,
    docs_out: str | Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    model = build_project_knowledge(recording=recording, scan_all_recordings=scan_recordings, root=root)
    index_paths = write_indexes(model, knowledge_out=knowledge_out)
    doc_paths = write_docs(model, docs_out=docs_out) if write_docs_flag else {}
    recording_indexed = False
    if recording:
        target_id = Path(recording).name
        recording_indexed = any(entry.get("recordingId") == target_id for entry in _list(model.get("recordings")))
    return {
        "schema": UPDATE_RESULT_SCHEMA,
        "status": "PASS",
        "updatedAtUtc": model.get("updatedAtUtc"),
        "knowledgeIndexPath": index_paths.get("project"),
        "indexPaths": index_paths,
        "docPaths": doc_paths,
        "recordingIndexed": recording_indexed if recording else None,
        "recordingCount": len(_list(model.get("recordings"))),
        "capabilityCount": len(_list(model.get("capabilities"))),
        "gapCount": len(_list(model.get("gaps"))),
        "warnings": [],
    }


def _json_file_readable(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict)


def _capability_search_text(capability: dict[str, Any]) -> str:
    fields = [
        capability.get("id"),
        capability.get("name"),
        capability.get("status"),
        capability.get("scriptExposure"),
        capability.get("notes"),
        " ".join(str(item) for item in _list(capability.get("layers"))),
    ]
    return " ".join(str(item or "") for item in fields).lower()


def _indexed_capability_keys(model: dict[str, Any]) -> tuple[list[str], list[str]]:
    capabilities = _list(model.get("capabilities"))
    capability_ids = {str(item.get("id") or "") for item in capabilities if isinstance(item, dict)}
    searchable = " ".join(_capability_search_text(item) for item in capabilities if isinstance(item, dict))
    found: list[str] = []
    missing: list[str] = []
    for key, ids in KEY_CAPABILITY_INDEXES.items():
        if any(capability_id in capability_ids for capability_id in ids) or key.lower() in searchable:
            found.append(key)
        else:
            missing.append(key)
    return sorted(found), sorted(missing)


def check_knowledge(*, root: Path | None = None) -> dict[str, Any]:
    base = root or repo_root()
    model = build_project_knowledge(root=base)
    docs_dir = base / "docs" / "knowledge"
    index_dir = base / "telemetry-viewer" / "knowledge_base"
    errors: list[str] = []
    warnings: list[str] = []
    if not docs_dir.exists():
        errors.append(f"docs/knowledge missing: {docs_dir}")
    missing_docs = [name for name in REQUIRED_DOC_FILES if not (docs_dir / name).exists()]
    if missing_docs:
        errors.append("missing knowledge docs: " + ", ".join(missing_docs))
    if not index_dir.exists():
        errors.append(f"knowledge_base missing: {index_dir}")
    missing_indexes = [name for name in REQUIRED_INDEX_FILES if not (index_dir / name).exists()]
    unreadable_indexes = [name for name in REQUIRED_INDEX_FILES if (index_dir / name).exists() and not _json_file_readable(index_dir / name)]
    if missing_indexes:
        errors.append("missing machine-readable indexes: " + ", ".join(missing_indexes))
    if unreadable_indexes:
        errors.append("unreadable machine-readable indexes: " + ", ".join(unreadable_indexes))
    if not model.get("capabilities"):
        warnings.append("no capabilities indexed")
    if not model.get("recordings"):
        warnings.append("no recordings indexed")
    if not model.get("apiDataPaths"):
        errors.append("no API data paths indexed")
    if not model.get("scriptApis"):
        errors.append("script API map is empty")
    if not model.get("gaps"):
        errors.append("open gaps are not indexed")
    indexed_keys, missing_keys = _indexed_capability_keys(model)
    if missing_keys:
        errors.append("missing key capabilities: " + ", ".join(missing_keys))
    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "schema": "project_knowledge_check.v1",
        "status": status,
        "repoRoot": str(base),
        "docsDir": str(docs_dir),
        "knowledgeBaseDir": str(index_dir),
        "recordingCount": len(_list(model.get("recordings"))),
        "capabilityCount": len(_list(model.get("capabilities"))),
        "gapCount": len(_list(model.get("gaps"))),
        "scriptApiCount": len(_list(model.get("scriptApis"))),
        "apiDataPathCount": len(_list(model.get("apiDataPaths"))),
        "docFiles": sorted(DOC_RENDERERS),
        "requiredDocFiles": REQUIRED_DOC_FILES,
        "missingDocFiles": missing_docs,
        "indexFiles": REQUIRED_INDEX_FILES,
        "missingIndexFiles": missing_indexes,
        "unreadableIndexFiles": unreadable_indexes,
        "keyCapabilitiesIndexed": indexed_keys,
        "missingKeyCapabilities": missing_keys,
        "openGapsIndexed": bool(model.get("gaps")),
        "scriptApiMapIndexed": bool(model.get("scriptApis")),
        "errors": errors,
        "warnings": warnings,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update the repo-owned OSRS telemetry project knowledge base.")
    parser.add_argument("--scan-recordings", action="store_true", help="Scan all recording folders and write JSON indexes.")
    parser.add_argument("--recording", help="Index one recording folder. By default this is merged with the full scan.")
    parser.add_argument("--write-docs", action="store_true", help="Write docs/knowledge Markdown pages.")
    parser.add_argument("--check", action="store_true", help="Validate that the knowledge model can be built.")
    parser.add_argument("--json", action="store_true", help="Print JSON result.")
    parser.add_argument("--knowledge-out", help="Override JSON index output directory.")
    parser.add_argument("--docs-out", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        result = check_knowledge()
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"{result['status']}: recordings={result['recordingCount']} capabilities={result['capabilityCount']} gaps={result['gapCount']}")
            for error in result.get("errors") or []:
                print(f"ERROR: {error}")
            for warning in result.get("warnings") or []:
                print(f"WARN: {warning}")
        return 0 if result.get("status") in {"PASS", "WARN"} else 1

    should_scan = bool(args.scan_recordings or not args.recording)
    result = update_knowledge(
        recording=args.recording,
        scan_recordings=should_scan,
        write_docs_flag=bool(args.write_docs),
        knowledge_out=args.knowledge_out,
        docs_out=args.docs_out,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"{result['status']}: wrote {result.get('knowledgeIndexPath')}")
        if result.get("docPaths"):
            print(f"docs: {len(result['docPaths'])}")
        if args.recording:
            print(f"recordingIndexed: {result.get('recordingIndexed')}")
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
