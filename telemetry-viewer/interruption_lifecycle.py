from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import telemetry_schema


SCHEMA_VERSION = "interruption_lifecycle.v1"


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
        parsed = parsed.replace(tzinfo=timezone.utc)
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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_recording_events(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
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


def _event_time(record: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _dict(fallback)
    elapsed = _float(_first(record.get("elapsedSeconds"), record.get("elapsed_seconds"), base.get("elapsed_seconds")))
    tick = _int(_first(record.get("tick"), record.get("gameTick"), record.get("gameTickAtSample"), base.get("latest_tick"), base.get("tick")))
    wall_time = _first(record.get("wallTimeUtc"), record.get("timestampUtc"), record.get("generatedAtUtc"), base.get("wall_time_utc"))
    return {
        "tick": tick,
        "elapsedSeconds": elapsed,
        "wallTimeUtc": wall_time,
    }


def _compact_actor(actor: Any) -> dict[str, Any]:
    value = _dict(actor)
    if not value:
        return {}
    world = _dict(_first(value.get("world"), value.get("worldPoint"), value.get("location")))
    return {
        "name": _first(value.get("name"), value.get("actorName"), value.get("targetName")),
        "type": _first(value.get("type"), value.get("actorType"), value.get("targetType")),
        "id": _first(value.get("id"), value.get("npcId"), value.get("index")),
        "combatLevel": _first(value.get("combatLevel"), value.get("level")),
        "world": {
            "x": _first(world.get("x"), world.get("worldX"), value.get("worldX")),
            "y": _first(world.get("y"), world.get("worldY"), value.get("worldY")),
            "plane": _first(world.get("plane"), value.get("plane")),
        },
    }


def _actor_has_identity(actor: dict[str, Any]) -> bool:
    actor_id = _int(actor.get("id"))
    actor_type = str(actor.get("type") or "").strip().lower()
    return bool(
        _first(actor.get("name"), actor.get("combatLevel"))
        or (actor_id is not None and actor_id >= 0)
        or (actor_type and actor_type not in {"unknown", "none", "null"})
    )


def _extract_combat_state_from_source(source: dict[str, Any]) -> dict[str, Any] | None:
    name = str(source.get("name") or "").lower()
    data = _source_data(source)
    if name == "combat_state" and isinstance(data, dict):
        return data
    if isinstance(data, dict):
        schema = str(data.get("schema") or data.get("schema_version") or "")
        if schema == "combat_state.v1" or data.get("inCombat") is not None or data.get("recentHitsplats") is not None:
            return data
        payloads = _dict(data.get("payloads"))
        nested = _dict(_first(payloads.get("combat_state"), payloads.get("combatState"), data.get("combat_state"), data.get("combatState")))
        if nested:
            return nested
    return None


def _extract_combat_snapshots(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    snapshots: list[dict[str, Any]] = []
    configured = False
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        for source in event.get("sources") or []:
            if not isinstance(source, dict):
                continue
            if str(source.get("name") or "") == "combat_state":
                configured = True
            combat = _extract_combat_state_from_source(source)
            if not combat:
                continue
            record = dict(combat)
            record.setdefault("_sourceEvent", _event_time(record, event))
            key = (
                record.get("tick"),
                record.get("exportSeq"),
                record.get("_sourceEvent", {}).get("elapsedSeconds"),
                repr(record.get("recentHitsplats")),
                repr(record.get("actorsInteractingWithPlayer")),
            )
            if key in seen:
                continue
            seen.add(key)
            snapshots.append(record)
    snapshots.sort(key=lambda item: (_float(_dict(item.get("_sourceEvent")).get("elapsedSeconds")) is None, _float(_dict(item.get("_sourceEvent")).get("elapsedSeconds")) or 0.0))
    return snapshots, configured


def _collect_recent_records(snapshots: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for snapshot in snapshots:
        source_time = _dict(snapshot.get("_sourceEvent"))
        for raw in snapshot.get(key) or []:
            record = _dict(raw)
            if not record:
                continue
            item = dict(record)
            item.setdefault("time", _event_time(record, source_time))
            dedupe = (
                key,
                item.get("eventType"),
                item.get("tick"),
                item.get("timestampUtc"),
                item.get("message"),
                item.get("name"),
                repr(item.get("actor")),
                repr(item.get("target")),
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)
            records.append(item)
    return records[:100]


def _legacy_timeline_records(events: list[dict[str, Any]], event_types: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for event in events:
        for source in event.get("sources") or []:
            if not isinstance(source, dict):
                continue
            data = _source_data(source)
            candidates: list[Any] = []
            if isinstance(data, dict):
                candidates.extend(data.get("timelineEvents") or [])
                candidates.extend(_dict(data.get("eventTimeline")).get("events") or [])
            elif isinstance(data, list):
                candidates.extend(data)
            for raw in candidates:
                record = _dict(raw)
                event_type = str(_first(record.get("eventType"), record.get("type"), record.get("schema")) or "")
                if event_type not in event_types:
                    continue
                key = (event_type, record.get("tick"), record.get("generatedAtUtc"), repr(record.get("currentValue")), record.get("summary"))
                if key in seen:
                    continue
                seen.add(key)
                item = dict(record)
                item.setdefault("time", _event_time(record, event))
                records.append(item)
    return records


def _combat_actor_name(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        for key in ("actor", "source", "target", "interacting", "npc"):
            name = _compact_actor(record.get(key)).get("name")
            if name:
                return str(name)
        for key in ("actorName", "sourceName", "targetName", "name"):
            if record.get(key):
                return str(record.get(key))
    return None


def _health_changed(snapshots: list[dict[str, Any]]) -> bool:
    values: list[tuple[Any, Any, Any, Any]] = []
    for snapshot in snapshots:
        health = _dict(snapshot.get("playerHealth"))
        if not health:
            continue
        values.append(
            (
                health.get("ratio"),
                health.get("scale"),
                health.get("boostedHitpoints"),
                health.get("realHitpoints"),
            )
        )
    return len({value for value in values if any(item is not None for item in value)}) > 1


def _contains_level_or_stat_message(record: dict[str, Any]) -> bool:
    text = " ".join(
        str(_first(record.get(key), ""))
        for key in ("message", "summary", "name", "skill", "stat", "type", "eventType")
    ).lower()
    return any(token in text for token in ("level", "advanced", "xp", "experience", "stat"))


def _woodcutting_gap(woodcutting_lifecycle: dict[str, Any]) -> dict[str, Any]:
    clicks = _list(_dict(woodcutting_lifecycle.get("clicks")).get("freshChopClicks"))
    active_ticks = [_int(item) for item in _dict(woodcutting_lifecycle.get("animation")).get("activeTicks") or []]
    active_ticks = [tick for tick in active_ticks if tick is not None]
    click_points: list[dict[str, Any]] = []
    for click in clicks:
        elapsed = _float(_first(click.get("elapsedSeconds"), _dict(click.get("time")).get("elapsedSeconds")))
        tick = _int(click.get("tick"))
        if elapsed is not None or tick is not None:
            click_points.append({"kind": "chop_click", "elapsedSeconds": elapsed, "tick": tick, "record": click})
    click_points.sort(key=lambda item: (_float(item.get("elapsedSeconds")) is None, _float(item.get("elapsedSeconds")) or 0.0, _int(item.get("tick")) or 0))
    best_gap: dict[str, Any] = {}
    for before, after in zip(click_points, click_points[1:]):
        before_elapsed = _float(before.get("elapsedSeconds"))
        after_elapsed = _float(after.get("elapsedSeconds"))
        elapsed_gap = after_elapsed - before_elapsed if before_elapsed is not None and after_elapsed is not None else None
        before_tick = _int(before.get("tick"))
        after_tick = _int(after.get("tick"))
        tick_gap = after_tick - before_tick if before_tick is not None and after_tick is not None else None
        if (elapsed_gap is not None and elapsed_gap >= 45) or (tick_gap is not None and tick_gap >= 75):
            if not best_gap or (elapsed_gap or 0) > (best_gap.get("durationSeconds") or 0):
                best_gap = {
                    "before": before,
                    "after": after,
                    "durationSeconds": elapsed_gap,
                    "tickGap": tick_gap,
                }
    if best_gap:
        return best_gap
    if active_ticks:
        gaps = [
            {"beforeTick": before, "afterTick": after, "tickGap": after - before}
            for before, after in zip(active_ticks, active_ticks[1:])
            if after - before >= 75
        ]
        if gaps:
            gap = max(gaps, key=lambda item: item["tickGap"])
            return {
                "before": {"kind": "woodcutting_animation", "tick": gap["beforeTick"]},
                "after": {"kind": "woodcutting_animation", "tick": gap["afterTick"]},
                "durationSeconds": None,
                "tickGap": gap["tickGap"],
            }
    return {}


def _record_tick(record: dict[str, Any]) -> int | None:
    return _int(
        _first(
            record.get("tick"),
            record.get("gameTick"),
            record.get("gameTickAtSample"),
            _dict(record.get("time")).get("tick"),
            _dict(record.get("_sourceEvent")).get("tick"),
        )
    )


def _combat_window(
    snapshots: list[dict[str, Any]],
    hitsplats: list[dict[str, Any]],
    actor_deaths: list[dict[str, Any]],
) -> dict[str, Any]:
    first_combat: dict[str, Any] = {}
    last_combat: dict[str, Any] = {}
    first_clear: dict[str, Any] = {}

    for snapshot in snapshots:
        source_time = _dict(snapshot.get("_sourceEvent"))
        time = _event_time(snapshot, source_time)
        actors = [_compact_actor(actor) for actor in snapshot.get("actorsInteractingWithPlayer") or []]
        actors = [actor for actor in actors if _actor_has_identity(actor)]
        target = _compact_actor(snapshot.get("playerInteracting"))
        direct_combat = bool(snapshot.get("inCombat") or actors or _actor_has_identity(target))
        if direct_combat:
            if not first_combat:
                first_combat = time
            last_combat = time
        elif first_combat and not first_clear:
            first_clear = time

    for record in hitsplats + actor_deaths:
        time = _event_time(record, _dict(record.get("time")))
        if _record_tick(time) is None:
            continue
        if not first_combat or (_record_tick(time) or 0) < (_record_tick(first_combat) or 0):
            first_combat = time
        if not last_combat or (_record_tick(time) or 0) > (_record_tick(last_combat) or 0):
            last_combat = time

    return {
        "firstCombat": first_combat,
        "lastCombat": last_combat,
        "firstClear": first_clear,
        "resumeAfterTick": _record_tick(first_clear) if first_clear else _record_tick(last_combat),
    }


def _is_woodcutting_message(record: dict[str, Any]) -> bool:
    text = str(_first(record.get("message"), record.get("summary"), record.get("text")) or "").lower()
    return any(token in text for token in ("swing your axe", "get some logs", "chop down", "woodcut"))


def _woodcutting_after_tick(
    woodcutting_lifecycle: dict[str, Any],
    messages: list[dict[str, Any]],
    after_tick: int | None,
) -> dict[str, Any]:
    if after_tick is None:
        return {}

    candidates: list[dict[str, Any]] = []
    clicks = _list(_dict(woodcutting_lifecycle.get("clicks")).get("freshChopClicks"))
    for click in clicks:
        tick = _record_tick(click)
        if tick is not None and tick > after_tick:
            candidates.append({"kind": "chop_click", "tick": tick, "record": click})

    for tick in _dict(woodcutting_lifecycle.get("animation")).get("activeTicks") or []:
        tick_int = _int(tick)
        if tick_int is not None and tick_int > after_tick:
            candidates.append({"kind": "woodcutting_animation", "tick": tick_int})

    for cycle in _list(woodcutting_lifecycle.get("cycles")):
        cycle_record = _dict(cycle)
        if not cycle_record:
            continue
        tick = _int(_first(cycle_record.get("logGainTick"), cycle_record.get("endTick")))
        logs_gained = _int(cycle_record.get("logsGained")) or 0
        if tick is not None and tick > after_tick and logs_gained > 0:
            candidates.append({"kind": "logs_gained", "tick": tick, "record": cycle_record})

    for message in messages:
        tick = _record_tick(message)
        if tick is not None and tick > after_tick and _is_woodcutting_message(message):
            candidates.append({"kind": "woodcutting_message", "tick": tick, "record": message})

    if not candidates:
        return {}
    candidates.sort(key=lambda item: item.get("tick") or 0)
    return {
        "after": candidates[0],
        "count": len(candidates),
        "afterTick": candidates[0].get("tick"),
    }


def _messages_from_combat(snapshots: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = _collect_recent_records(snapshots, "recentChatMessages")
    records.extend(_legacy_timeline_records(events, {"ChatMessage", "chat_message", "plugin_chat_message.v1"}))
    return records[:50]


def _stat_changes_from_combat(snapshots: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = _collect_recent_records(snapshots, "recentStatChanges")
    records.extend(_legacy_timeline_records(events, {"StatChanged", "stat_changed", "plugin_stat_changed.v1"}))
    return records[:50]


def analyze_data(
    *,
    events: list[dict[str, Any]] | None = None,
    woodcutting_lifecycle: dict[str, Any] | None = None,
    summaries: dict[str, Any] | None = None,
    recording_path: str | Path | None = None,
) -> dict[str, Any]:
    events = list(events or [])
    summaries = summaries or {}
    recording = Path(recording_path) if recording_path else None
    if not woodcutting_lifecycle:
        woodcutting_lifecycle = _dict(_dict(summaries.get("summary")).get("woodcutting_lifecycle"))
    if not woodcutting_lifecycle and recording:
        woodcutting_lifecycle = _load_json(recording / "woodcutting_lifecycle.json")

    combat_snapshots, combat_configured = _extract_combat_snapshots(events)
    hitsplats = _collect_recent_records(combat_snapshots, "recentHitsplats")
    interacting_changes = _collect_recent_records(combat_snapshots, "recentInteractingChanges")
    actor_deaths = _collect_recent_records(combat_snapshots, "recentActorDeaths")
    messages = _messages_from_combat(combat_snapshots, events)
    stat_changes = _stat_changes_from_combat(combat_snapshots, events)
    actors_targeting = []
    player_targets: list[dict[str, Any]] = []
    hostile_npcs: list[dict[str, Any]] = []
    in_combat_seen = False
    for snapshot in combat_snapshots:
        in_combat_seen = in_combat_seen or bool(snapshot.get("inCombat"))
        actors_targeting.extend(_compact_actor(actor) for actor in snapshot.get("actorsInteractingWithPlayer") or [])
        player_target = _compact_actor(snapshot.get("playerInteracting"))
        if _actor_has_identity(player_target):
            player_targets.append(player_target)
        hostile_npcs.extend(_compact_actor(actor) for actor in snapshot.get("nearbyHostileNpcs") or [])
    actors_targeting = [item for item in actors_targeting if _actor_has_identity(item)]
    player_targets = [item for item in player_targets if _actor_has_identity(item)]
    hostile_npcs = [item for item in hostile_npcs if _actor_has_identity(item)]

    gap = _woodcutting_gap(_dict(woodcutting_lifecycle))
    health_changed = _health_changed(combat_snapshots)
    combat_observed = bool(in_combat_seen or actors_targeting or player_targets or hitsplats or health_changed)
    combat_window = _combat_window(combat_snapshots, hitsplats, actor_deaths)
    post_combat_woodcutting = _woodcutting_after_tick(
        _dict(woodcutting_lifecycle),
        messages,
        _int(combat_window.get("resumeAfterTick")),
    )
    task_interrupted = bool(gap or (combat_observed and woodcutting_lifecycle))
    task_resumed = bool((gap and gap.get("after")) or post_combat_woodcutting)
    stat_or_level = bool(stat_changes or any(_contains_level_or_stat_message(message) for message in messages))
    has_messages = bool(messages)

    interruption_type = "none"
    primary_cause = "none"
    confidence = 0.0
    evidence: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    if combat_snapshots:
        evidence.append(f"combat_state observed in {len(combat_snapshots)} snapshot(s)")
    elif combat_configured:
        missing.append("combat_state")
        warnings.append("combat_state was configured but not preserved in this recording.")
    else:
        missing.extend(["combat_state", "combat.recentHitsplats", "combat.recentChatMessages", "combat.recentStatChanges"])

    if gap:
        evidence.append("task stop/resume gap detected in woodcutting evidence")
    elif task_interrupted:
        evidence.append("task interruption evidence observed during woodcutting")
    if gap and task_resumed:
        evidence.append("woodcutting evidence resumed after interruption gap")
    elif task_resumed:
        evidence.append("woodcutting evidence resumed after interruption evidence")
    if post_combat_woodcutting:
        evidence.append(
            f"woodcutting evidence continued after combat window ({post_combat_woodcutting.get('count')} post-combat signal(s))"
        )
    if hitsplats:
        evidence.append(f"{len(hitsplats)} recent hitsplat event(s) observed")
    if actors_targeting:
        evidence.append(f"{len(actors_targeting)} actor(s) targeting the player observed")
    if player_targets:
        evidence.append("local player interacting target observed")
    if health_changed:
        evidence.append("player health changed across combat_state snapshots")
    if stat_changes:
        evidence.append(f"{len(stat_changes)} recent stat change event(s) observed")
    if messages:
        evidence.append(f"{len(messages)} recent chat/game message(s) observed")

    if combat_observed:
        interruption_type = "combat"
        actor_name = _first(
            _dict(actors_targeting[0] if actors_targeting else {}).get("name"),
            _dict(hostile_npcs[0] if hostile_npcs else {}).get("name"),
            _dict(player_targets[0] if player_targets else {}).get("name"),
            _combat_actor_name(interacting_changes + hitsplats),
        )
        primary_cause = "mugger_attack" if actor_name and "mugger" in str(actor_name).lower() else ("hostile_npc" if actors_targeting or hostile_npcs else "player_combat")
        confidence = 0.8
        if hitsplats or health_changed:
            confidence += 0.1
        if task_interrupted or task_resumed:
            confidence += 0.05
    elif stat_or_level:
        interruption_type = "level_up"
        primary_cause = "level_up"
        confidence = 0.7 if task_interrupted else 0.55
    elif has_messages:
        interruption_type = "chat_message"
        primary_cause = "chat"
        confidence = 0.55 if task_interrupted else 0.35
    elif task_interrupted:
        interruption_type = "unknown"
        primary_cause = "unknown"
        confidence = 0.45
        warnings.append("Task interruption/resume was inferred, but direct cause evidence was not recorded.")
    else:
        interruption_type = "none"
        primary_cause = "none"
        confidence = 0.2 if combat_snapshots else 0.0

    if task_interrupted and not combat_observed and not stat_or_level and not has_messages:
        for item in ("combat_state", "combat.actorsInteractingWithPlayer", "combat.recentHitsplats", "combat.recentChatMessages", "combat.recentStatChanges", "combat.playerHealth"):
            if item not in missing:
                missing.append(item)

    status = "PASS"
    if task_interrupted and primary_cause == "unknown":
        status = "WARN"
    elif not task_interrupted and not combat_observed and not combat_snapshots:
        status = "WARN"
        warnings.append("No interruption or direct combat_state evidence was found.")
    elif combat_observed and not task_resumed and task_interrupted:
        status = "WARN"
        warnings.append("Combat evidence was present, but task resume was not proven.")

    interrupted_at = {}
    resumed_at = {}
    duration_ms = None
    if gap:
        interrupted_at = _event_time(_dict(_dict(gap.get("before")).get("record")), _dict(gap.get("before")))
        resumed_at = _event_time(_dict(_dict(gap.get("after")).get("record")), _dict(gap.get("after")))
        duration = _float(gap.get("durationSeconds"))
        duration_ms = int(duration * 1000) if duration is not None else None
    elif combat_window:
        interrupted_at = _dict(combat_window.get("firstCombat"))
        if post_combat_woodcutting:
            resumed_at = _event_time(
                _dict(_dict(post_combat_woodcutting.get("after")).get("record")),
                _dict(post_combat_woodcutting.get("after")),
            )

    result = {
        "schema": SCHEMA_VERSION,
        "status": status,
        "interruptionDetected": bool(task_interrupted or combat_observed or stat_or_level),
        "interruptionType": interruption_type,
        "confidence": round(min(0.95, confidence), 3),
        "primaryCause": primary_cause,
        "taskBefore": "woodcutting" if woodcutting_lifecycle else None,
        "taskInterruptedAt": interrupted_at,
        "taskResumedAt": resumed_at,
        "durationMs": duration_ms,
        "taskResumed": bool(task_resumed),
        "combat": {
            "combatObserved": bool(combat_observed),
            "combatStateSnapshotCount": len(combat_snapshots),
            "npcTargetedPlayer": bool(actors_targeting),
            "playerTargetedNpc": bool(player_targets),
            "hitsplatsSeen": len(hitsplats),
            "damageTaken": None,
            "playerHealthChanged": bool(health_changed),
            "hostileNpcs": hostile_npcs[:20],
            "actorsInteractingWithPlayer": actors_targeting[:20],
            "playerTargets": player_targets[:20],
            "actorDeathsSeen": len(actor_deaths),
            "recentHitsplats": hitsplats[:20],
        },
        "messages": messages[:20],
        "statChanges": stat_changes[:20],
        "evidence": evidence,
        "warnings": sorted(set(warnings)),
        "missingCapabilities": sorted(set(missing)),
    }
    if combat_observed or hitsplats:
        try:
            import combat_damage_summary

            damage = combat_damage_summary.analyze_data(
                events=events,
                interruption_lifecycle_summary=result,
                recording_path=recording_path,
            )
            compact = combat_damage_summary.compact_summary(damage)
            result["combatDamageSummary"] = compact
            result["combat"].update(
                {
                    "damageSummaryStatus": compact.get("status"),
                    "damageTakenTotal": compact.get("damageTakenTotal"),
                    "damageDealtTotal": compact.get("damageDealtTotal"),
                    "primaryOpponent": compact.get("primaryOpponent"),
                    "hpChanged": compact.get("hpChanged"),
                    "actorDeathSeen": compact.get("actorDeathSeen"),
                    "combatWindow": compact.get("combatWindow"),
                }
            )
        except Exception as error:
            result.setdefault("warnings", []).append(f"combat damage summary failed: {type(error).__name__}: {error}")
            result["warnings"] = sorted(set(result.get("warnings") or []))
    return result


def analyze_recording(path: str | Path) -> dict[str, Any]:
    recording = Path(path)
    events, warnings = load_recording_events(recording)
    lifecycle = analyze_data(events=events, recording_path=recording)
    if warnings:
        lifecycle["warnings"] = sorted(set(list(lifecycle.get("warnings") or []) + warnings))
    return lifecycle


def analyze_context(context: dict[str, Any]) -> dict[str, Any]:
    combat = _dict(_first(context.get("combat_state"), context.get("combatState"), _dict(context.get("normalized")).get("combat")))
    event = {
        "event_type": "source_snapshot",
        "sources": [{"name": "combat_state", "data": combat, "parse_status": "ok"}] if combat else [],
    }
    return analyze_data(events=[event] if combat else [], summaries={"summary": context})


def compact_lifecycle(lifecycle: dict[str, Any]) -> dict[str, Any]:
    combat = _dict(lifecycle.get("combat"))
    damage = _dict(lifecycle.get("combatDamageSummary"))
    return {
        "schema": "interruption_lifecycle_compact.v1",
        "status": lifecycle.get("status"),
        "interruptionDetected": bool(lifecycle.get("interruptionDetected")),
        "interruptionType": lifecycle.get("interruptionType"),
        "primaryCause": lifecycle.get("primaryCause"),
        "taskBefore": lifecycle.get("taskBefore"),
        "taskResumed": bool(lifecycle.get("taskResumed")),
        "confidence": lifecycle.get("confidence"),
        "combatObserved": bool(combat.get("combatObserved")),
        "hitsplatsSeen": combat.get("hitsplatsSeen") or 0,
        "npcTargetedPlayer": bool(combat.get("npcTargetedPlayer")),
        "playerTargetedNpc": bool(combat.get("playerTargetedNpc")),
        "damageTakenTotal": damage.get("damageTakenTotal"),
        "damageDealtTotal": damage.get("damageDealtTotal"),
        "primaryOpponent": damage.get("primaryOpponent") or combat.get("primaryOpponent"),
        "hpChanged": damage.get("hpChanged") if damage else combat.get("hpChanged"),
        "actorDeathSeen": damage.get("actorDeathSeen") if damage else bool(combat.get("actorDeathsSeen")),
        "combatWindow": damage.get("combatWindow") or combat.get("combatWindow") or {},
        "missingCapabilities": lifecycle.get("missingCapabilities") or [],
        "warnings": lifecycle.get("warnings") or [],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze task interruptions from Record Everything telemetry.")
    parser.add_argument("recording", help="Recording folder or events.jsonl path.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")
    args = parser.parse_args(argv)
    lifecycle = analyze_recording(args.recording)
    print(json.dumps(lifecycle, indent=2 if args.json else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
