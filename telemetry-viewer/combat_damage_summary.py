from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import interruption_lifecycle


SCHEMA_VERSION = "combat_damage_summary.v1"
COMPACT_SCHEMA_VERSION = "combat_damage_summary_compact.v1"


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


def _actor_kind(actor: dict[str, Any]) -> str:
    raw = str(_first(actor.get("actorType"), actor.get("type"), actor.get("kind")) or "").strip().lower()
    if raw in {"local_player", "player"}:
        if actor.get("actorType") == "LOCAL_PLAYER" or actor.get("type") == "LOCAL_PLAYER":
            return "local_player"
        return "player"
    if raw == "npc":
        return "npc"
    return raw or "unknown"


def _actor_name(actor: dict[str, Any]) -> str | None:
    return _first(actor.get("name"), actor.get("actorName"), actor.get("targetName"))


def _actor_id(actor: dict[str, Any]) -> int | None:
    return _int(_first(actor.get("id"), actor.get("npcId"), actor.get("index")))


def _world(actor: dict[str, Any]) -> dict[str, Any]:
    world = _dict(_first(actor.get("world"), actor.get("worldPoint"), actor.get("location")))
    return {
        "x": _first(world.get("x"), world.get("worldX"), actor.get("worldX")),
        "y": _first(world.get("y"), world.get("worldY"), actor.get("worldY")),
        "plane": _first(world.get("plane"), actor.get("plane")),
    }


def _compact_actor(raw: Any) -> dict[str, Any]:
    actor = _dict(raw)
    if not actor:
        return {}
    kind = _actor_kind(actor)
    return {
        "name": _actor_name(actor),
        "kind": kind,
        "type": _first(actor.get("actorType"), actor.get("type")),
        "id": _actor_id(actor),
        "index": _int(actor.get("index")),
        "world": _world(actor),
    }


def _is_local_player(actor: dict[str, Any]) -> bool:
    return _actor_kind(actor) == "local_player"


def _is_npc(actor: dict[str, Any]) -> bool:
    return _actor_kind(actor) == "npc"


def _tick(record: dict[str, Any]) -> int | None:
    return _int(
        _first(
            record.get("tick"),
            record.get("gameTick"),
            record.get("gameTickAtSample"),
            _dict(record.get("time")).get("tick"),
            _dict(record.get("_sourceEvent")).get("tick"),
        )
    )


def _elapsed(record: dict[str, Any]) -> float | None:
    return _float(
        _first(
            record.get("elapsedSeconds"),
            record.get("elapsed_seconds"),
            _dict(record.get("time")).get("elapsedSeconds"),
            _dict(record.get("_sourceEvent")).get("elapsedSeconds"),
        )
    )


def _time(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick": _tick(record),
        "elapsedSeconds": _elapsed(record),
        "wallTimeUtc": _first(record.get("wallTimeUtc"), record.get("timestampUtc"), _dict(record.get("time")).get("wallTimeUtc")),
    }


def _name_key(actor: dict[str, Any], fallback: str = "unknown") -> str:
    name = _actor_name(actor)
    actor_id = _actor_id(actor)
    if name:
        return str(name)
    if actor_id is not None and actor_id >= 0:
        return f"id:{actor_id}"
    return fallback


def _primary_opponent(
    actors_targeting: list[dict[str, Any]],
    player_targets: list[dict[str, Any]],
    hitsplats: list[dict[str, Any]],
    actor_deaths: list[dict[str, Any]],
) -> dict[str, Any]:
    scores: Counter[str] = Counter()
    samples: dict[str, dict[str, Any]] = {}

    def add(actor: dict[str, Any], weight: int) -> None:
        if not actor or not _is_npc(actor):
            return
        key = _name_key(actor)
        scores[key] += weight
        samples.setdefault(key, actor)

    for actor in actors_targeting:
        add(_compact_actor(actor), 4)
    for actor in player_targets:
        add(_compact_actor(actor), 3)
    for hit in hitsplats:
        add(_compact_actor(_dict(hit.get("actor"))), 2)
        add(_compact_actor(_dict(_dict(hit.get("actor")).get("interacting"))), 1)
    for death in actor_deaths:
        add(_compact_actor(death), 3)

    if not scores:
        return {"name": None, "kind": None, "id": None, "confidence": 0.0}
    key, score = scores.most_common(1)[0]
    sample = samples.get(key) or {}
    total = sum(scores.values())
    confidence = min(0.99, max(0.5, score / total if total else 0.0))
    return {
        "name": sample.get("name") or (key if not key.startswith("id:") else None),
        "kind": sample.get("kind") or "npc",
        "id": sample.get("id"),
        "confidence": round(confidence, 3),
    }


def _health_summary(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    boosted: list[int] = []
    ratios: list[int] = []
    scales: list[int] = []
    real_values: list[int] = []
    for snapshot in snapshots:
        health = _dict(snapshot.get("playerHealth"))
        boosted_hp = _int(health.get("boostedHitpoints"))
        ratio = _int(health.get("ratio"))
        scale = _int(health.get("scale"))
        real_hp = _int(health.get("realHitpoints"))
        if boosted_hp is not None and boosted_hp >= 0:
            boosted.append(boosted_hp)
        if ratio is not None and ratio >= 0:
            ratios.append(ratio)
        if scale is not None and scale >= 0:
            scales.append(scale)
        if real_hp is not None and real_hp >= 0:
            real_values.append(real_hp)

    hp_before = boosted[0] if boosted else None
    hp_after = boosted[-1] if boosted else None
    hp_delta = hp_after - hp_before if hp_before is not None and hp_after is not None else None
    return {
        "hpBefore": hp_before,
        "hpAfter": hp_after,
        "hpDelta": hp_delta,
        "lowestObservedHp": min(boosted) if boosted else None,
        "highestObservedHp": max(boosted) if boosted else None,
        "healthChanged": len(set(boosted)) > 1 or len(set(ratios)) > 1,
        "ratioBefore": ratios[0] if ratios else None,
        "ratioAfter": ratios[-1] if ratios else None,
        "scale": scales[-1] if scales else None,
        "realHitpoints": real_values[-1] if real_values else None,
    }


def _combat_window(
    snapshots: list[dict[str, Any]],
    hitsplats: list[dict[str, Any]],
    actor_deaths: list[dict[str, Any]],
    interruption: dict[str, Any],
) -> dict[str, Any]:
    combat_ticks: list[int] = []
    clear_ticks: list[int] = []
    tick_elapsed: dict[int, float] = {}
    for snapshot in snapshots:
        source_time = _dict(snapshot.get("_sourceEvent"))
        tick = _tick(snapshot) or _tick(source_time)
        elapsed = _elapsed(snapshot) if _elapsed(snapshot) is not None else _elapsed(source_time)
        if tick is not None and elapsed is not None:
            tick_elapsed.setdefault(tick, elapsed)
        actors = _list(snapshot.get("actorsInteractingWithPlayer"))
        player_target = _dict(snapshot.get("playerInteracting"))
        active = bool(snapshot.get("inCombat") or actors or interruption_lifecycle._actor_has_identity(interruption_lifecycle._compact_actor(player_target)))
        if tick is not None and active:
            combat_ticks.append(tick)
        elif tick is not None:
            clear_ticks.append(tick)

    for record in hitsplats + actor_deaths:
        tick = _tick(record)
        if tick is not None:
            combat_ticks.append(tick)
        elapsed = _elapsed(record)
        if tick is not None and elapsed is not None:
            tick_elapsed.setdefault(tick, elapsed)

    interrupted_at = _dict(interruption.get("taskInterruptedAt"))
    resumed_at = _dict(interruption.get("taskResumedAt"))
    if _tick(interrupted_at) is not None:
        combat_ticks.append(_tick(interrupted_at) or 0)
    if _tick(resumed_at) is not None:
        clear_ticks.append(_tick(resumed_at) or 0)

    start_tick = min(combat_ticks) if combat_ticks else None
    last_combat_tick = max(combat_ticks) if combat_ticks else None
    end_tick = None
    for tick in sorted(clear_ticks):
        if last_combat_tick is not None and tick >= last_combat_tick:
            end_tick = tick
            break
    if end_tick is None and _tick(resumed_at) is not None:
        end_tick = _tick(resumed_at)

    start_elapsed = tick_elapsed.get(start_tick) if start_tick is not None else None
    end_elapsed = tick_elapsed.get(end_tick) if end_tick is not None else None
    duration_ms = int((end_elapsed - start_elapsed) * 1000) if start_elapsed is not None and end_elapsed is not None and end_elapsed >= start_elapsed else None
    return {
        "startTick": start_tick,
        "endTick": end_tick,
        "startTimeMs": int(start_elapsed * 1000) if start_elapsed is not None else None,
        "endTimeMs": int(end_elapsed * 1000) if end_elapsed is not None else None,
        "durationMs": duration_ms,
        "endInferred": end_tick is not None and end_tick not in combat_ticks,
    }


def _sum_by_key(records: list[dict[str, Any]], key_func) -> tuple[int | None, int, list[dict[str, Any]], int]:
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "hitsplatCount": 0, "missingAmountCount": 0})
    total = 0
    missing_amount = 0
    count = 0
    for record in records:
        amount = _int(record.get("amount"))
        actor = _compact_actor(_dict(record.get("actor")))
        key, label = key_func(record, actor)
        if not key:
            continue
        count += 1
        totals[key].update(label)
        totals[key]["hitsplatCount"] += 1
        if amount is None:
            totals[key]["missingAmountCount"] += 1
            missing_amount += 1
            continue
        totals[key]["total"] += amount
        total += amount
    if count and missing_amount == count:
        total_value: int | None = None
    else:
        total_value = total
    return total_value, count, list(totals.values()), missing_amount


def analyze_data(
    *,
    events: list[dict[str, Any]] | None = None,
    combat_state: dict[str, Any] | None = None,
    interruption_lifecycle_summary: dict[str, Any] | None = None,
    recording_path: str | Path | None = None,
) -> dict[str, Any]:
    events = list(events or [])
    if combat_state:
        events.append({"event_type": "source_snapshot", "sources": [{"name": "combat_state", "data": combat_state, "parse_status": "ok"}]})
    recording = Path(recording_path) if recording_path else None
    interruption = _dict(interruption_lifecycle_summary)
    if not interruption and recording:
        interruption = _read_json(recording / "interruption_lifecycle.json")

    snapshots, combat_configured = interruption_lifecycle._extract_combat_snapshots(events)
    hitsplats = interruption_lifecycle._collect_recent_records(snapshots, "recentHitsplats")
    actor_deaths = interruption_lifecycle._collect_recent_records(snapshots, "recentActorDeaths")
    actors_targeting: list[dict[str, Any]] = []
    player_targets: list[dict[str, Any]] = []
    for snapshot in snapshots:
        actors_targeting.extend(_compact_actor(actor) for actor in snapshot.get("actorsInteractingWithPlayer") or [])
        target = _compact_actor(snapshot.get("playerInteracting"))
        if interruption_lifecycle._actor_has_identity({"name": target.get("name"), "type": target.get("type"), "id": target.get("id"), "combatLevel": None}):
            player_targets.append(target)
    actors_targeting = [actor for actor in actors_targeting if actor.get("name") or actor.get("id") not in (None, -1)]
    player_targets = [actor for actor in player_targets if actor.get("name") or actor.get("id") not in (None, -1)]

    opponent = _primary_opponent(actors_targeting, player_targets, hitsplats, actor_deaths)

    local_hits = [hit for hit in hitsplats if _is_local_player(_compact_actor(_dict(hit.get("actor"))))]
    npc_hits = [hit for hit in hitsplats if _is_npc(_compact_actor(_dict(hit.get("actor"))))]
    ambiguous_hits = [hit for hit in hitsplats if hit not in local_hits and hit not in npc_hits]

    def taken_key(_record: dict[str, Any], _actor: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = str(opponent.get("name") or "unknown_source")
        return name, {"name": opponent.get("name"), "kind": opponent.get("kind"), "id": opponent.get("id")}

    def dealt_key(_record: dict[str, Any], actor: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = _name_key(actor, "unknown_target")
        return name, {"name": actor.get("name"), "kind": actor.get("kind"), "id": actor.get("id")}

    taken_total, taken_count, sources, taken_missing = _sum_by_key(local_hits, taken_key)
    dealt_total, dealt_count, targets, dealt_missing = _sum_by_key(npc_hits, dealt_key)
    amount_missing = taken_missing + dealt_missing + sum(1 for hit in ambiguous_hits if _int(hit.get("amount")) is None)
    health = _health_summary(snapshots)
    combat_window = _combat_window(snapshots, hitsplats, actor_deaths, interruption)

    warnings: list[str] = []
    missing: list[str] = []
    if not snapshots and not combat_configured:
        missing.append("combat_state")
        warnings.append("combat_state was not available for damage summary.")
    if hitsplats and amount_missing:
        warnings.append(f"{amount_missing} hitsplat(s) were missing damage amount.")
        missing.append("combat.hitsplat.amount")
    if hitsplats and ambiguous_hits:
        warnings.append(f"{len(ambiguous_hits)} hitsplat(s) could not be attributed to player or NPC.")
    if hitsplats and not opponent.get("name"):
        warnings.append("Primary opponent could not be identified from combat interactions.")
    if not hitsplats and snapshots:
        missing.append("combat.recentHitsplats")
        warnings.append("combat_state was present but no hitsplats were available.")

    confidence = 0.0
    if hitsplats:
        confidence += 0.45
    if actors_targeting or player_targets:
        confidence += 0.25
    if health.get("healthChanged"):
        confidence += 0.15
    if opponent.get("name"):
        confidence += 0.1
    if actor_deaths:
        confidence += 0.05
    confidence = min(0.95, confidence)

    status = "PASS"
    if not snapshots and not hitsplats:
        status = "FAIL"
    elif warnings or missing:
        status = "WARN"

    task_resumed = bool(interruption.get("taskResumed"))
    resume_evidence = [
        item for item in _list(interruption.get("evidence"))
        if "resumed" in str(item).lower() or "post-combat" in str(item).lower() or "continued after combat" in str(item).lower()
    ]

    return {
        "schema": SCHEMA_VERSION,
        "status": status,
        "combatObserved": bool(snapshots or hitsplats or _dict(interruption.get("combat")).get("combatObserved")),
        "combatWindow": combat_window,
        "primaryOpponent": opponent,
        "damageTaken": {
            "total": taken_total,
            "hitsplatCount": taken_count,
            "nonzeroHitsplatCount": sum(1 for hit in local_hits if (_int(hit.get("amount")) or 0) > 0),
            "sources": sources,
            "missingAmountCount": taken_missing,
        },
        "damageDealt": {
            "total": dealt_total,
            "hitsplatCount": dealt_count,
            "nonzeroHitsplatCount": sum(1 for hit in npc_hits if (_int(hit.get("amount")) or 0) > 0),
            "targets": targets,
            "missingAmountCount": dealt_missing,
        },
        "health": health,
        "hitsplats": {
            "total": len(hitsplats),
            "localPlayerHitsplats": len(local_hits),
            "opponentHitsplats": len(npc_hits),
            "ambiguousHitsplats": len(ambiguous_hits),
            "missingAmountCount": amount_missing,
        },
        "actorDeaths": [_compact_actor(actor) for actor in actor_deaths[:20]],
        "taskResume": {
            "taskResumed": task_resumed,
            "resumeEvidence": resume_evidence,
        },
        "confidence": round(confidence, 3),
        "warnings": sorted(set(warnings)),
        "missingCapabilities": sorted(set(missing)),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def analyze_recording(path: str | Path) -> dict[str, Any]:
    recording = Path(path)
    events, warnings = interruption_lifecycle.load_recording_events(recording)
    lifecycle = analyze_data(events=events, recording_path=recording)
    if warnings:
        lifecycle["warnings"] = sorted(set(list(lifecycle.get("warnings") or []) + warnings))
    return lifecycle


def analyze_context(context: dict[str, Any]) -> dict[str, Any]:
    combat = _dict(_first(context.get("combat_state"), context.get("combatState"), context.get("combat"), _dict(context.get("normalized")).get("combat")))
    interruption = _dict(_first(context.get("interruption_lifecycle"), context.get("interruptionLifecycle")))
    if not interruption and combat:
        interruption = interruption_lifecycle.analyze_context({"combat_state": combat, "woodcutting_lifecycle": _dict(context.get("woodcutting_lifecycle") or context.get("woodcuttingLifecycle"))})
    return analyze_data(combat_state=combat, interruption_lifecycle_summary=interruption)


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    damage_taken = _dict(summary.get("damageTaken"))
    damage_dealt = _dict(summary.get("damageDealt"))
    hitsplats = _dict(summary.get("hitsplats"))
    health = _dict(summary.get("health"))
    resume = _dict(summary.get("taskResume"))
    return {
        "schema": COMPACT_SCHEMA_VERSION,
        "status": summary.get("status"),
        "combatObserved": bool(summary.get("combatObserved")),
        "primaryOpponent": summary.get("primaryOpponent") or {},
        "damageTakenTotal": damage_taken.get("total"),
        "damageTakenHitsplats": damage_taken.get("hitsplatCount") or 0,
        "damageDealtTotal": damage_dealt.get("total"),
        "damageDealtHitsplats": damage_dealt.get("hitsplatCount") or 0,
        "hitsplatCount": hitsplats.get("total") or 0,
        "hpChanged": bool(health.get("healthChanged")),
        "hpBefore": health.get("hpBefore"),
        "hpAfter": health.get("hpAfter"),
        "lowestObservedHp": health.get("lowestObservedHp"),
        "actorDeathSeen": bool(summary.get("actorDeaths")),
        "taskResumed": bool(resume.get("taskResumed")),
        "combatWindow": summary.get("combatWindow") or {},
        "confidence": summary.get("confidence"),
        "warnings": summary.get("warnings") or [],
        "missingCapabilities": summary.get("missingCapabilities") or [],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Summarize compact combat damage evidence from Record Everything telemetry.")
    parser.add_argument("recording", help="Recording folder or events.jsonl path.")
    parser.add_argument("--json", action="store_true", help="Print pretty JSON output.")
    args = parser.parse_args(argv)
    payload = analyze_recording(args.recording)
    print(json.dumps(payload, indent=2 if args.json else None, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
