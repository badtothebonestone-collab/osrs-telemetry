import argparse
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_paths import find_newest_session, get_sessions_dir


SUMMARY_SCHEMA = "live_context_summary.v1"
ANSWER_SCHEMA = "live_context_answer.v1"
TASK_SCHEMA = "live_task_context.v1"
SELF_TEST_SCHEMA = "live_context_self_test.v1"
DEFAULT_FRESHNESS_TICKS = 5
DEFAULT_FRESHNESS_MS = 5000
TREE_CLASSES = {"tree", "oak_tree", "willow_tree", "maple_tree", "yew_tree", "magic_tree"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def json_dump_compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=False)


def resolve_session(args) -> tuple[Path | None, list[str]]:
    warnings = []
    if args.session:
        return Path(args.session).expanduser(), warnings
    if args.latest_session:
        return find_newest_session(get_sessions_dir(args.sessions_dir)), warnings
    warnings.append("No --session or --latest-session supplied; using newest session for compatibility.")
    return find_newest_session(get_sessions_dir(args.sessions_dir)), warnings


def live_paths(session: Path) -> dict[str, Path]:
    live_dir = session / "interaction_geometry" / "live"
    return {
        "baseline": live_dir / "live_baseline_state.json",
        "context": live_dir / "live_context_index.json",
        "candidates": live_dir / "live_candidates.jsonl",
        "status": live_dir / "live_status.json",
        "navigation": live_dir / "live_navigation_summary.json",
    }


def source_files_payload(paths: dict[str, Path]) -> list[dict]:
    payload = []
    for name, path in paths.items():
        payload.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "sizeBytes": path.stat().st_size if path.exists() else None,
                "modifiedUtc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
                if path.exists()
                else None,
            }
        )
    return payload


def read_json(path: Path, warnings: list[str], missing_fields: list[str], label: str) -> dict:
    if not path.exists():
        warnings.append(f"{label} missing: {path}")
        missing_fields.append(label)
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{label} unreadable: {exc}")
        missing_fields.append(label)
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{label} did not contain a JSON object.")
        missing_fields.append(label)
        return {}
    return value


def read_jsonl(path: Path, warnings: list[str], missing_fields: list[str], label: str) -> list[dict]:
    records = []
    if not path.exists():
        warnings.append(f"{label} missing: {path}")
        missing_fields.append(label)
        return records
    malformed = 0
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError as exc:
        warnings.append(f"{label} unreadable: {exc}")
        missing_fields.append(label)
        return records
    if malformed:
        warnings.append(f"{label} had {malformed} malformed JSONL line(s).")
    return records


def load_live_context(session: Path) -> dict:
    warnings: list[str] = []
    missing_fields: list[str] = []
    paths = live_paths(session)
    baseline = read_json(paths["baseline"], warnings, missing_fields, "live_baseline_state")
    context = read_json(paths["context"], warnings, missing_fields, "live_context_index")
    status = read_json(paths["status"], warnings, missing_fields, "live_status")
    navigation = read_json(paths["navigation"], warnings, missing_fields, "live_navigation_summary") if paths["navigation"].exists() else {}
    candidates = read_jsonl(paths["candidates"], warnings, missing_fields, "live_candidates")
    if paths["candidates"].exists() and not candidates:
        warnings.append("live_candidates is present but empty.")
    return {
        "session": session,
        "paths": paths,
        "baseline": baseline,
        "context": context,
        "status": status,
        "navigation": navigation,
        "candidates": candidates,
        "warnings": warnings,
        "missingFields": missing_fields,
        "sourceFiles": source_files_payload(paths),
    }


def first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def target_payload(candidate: dict) -> dict:
    target = candidate.get("target")
    return target if isinstance(target, dict) else {}


def candidate_class_ids(candidate: dict) -> set[str]:
    ids = set()
    for key in ("classId", "targetClass"):
        value = candidate.get(key)
        if value:
            ids.add(str(value).lower())
    for value in candidate.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    target = target_payload(candidate)
    for value in target.get("targetClassIds") or []:
        if value:
            ids.add(str(value).lower())
    return ids


def candidate_class_id(candidate: dict) -> str:
    ids = candidate_class_ids(candidate)
    if ids:
        return sorted(ids)[0] if candidate.get("classId") is None else str(candidate.get("classId")).lower()
    target = target_payload(candidate)
    category = first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"))
    return str(category or "unclassified").lower()


def candidate_matches_class(candidate: dict, class_id: str) -> bool:
    wanted = class_id.lower()
    if wanted in candidate_class_ids(candidate):
        return True
    target = target_payload(candidate)
    category = str(first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"), "")).lower()
    target_type = str(first_value(candidate.get("targetType"), target.get("targetType"), "")).lower()
    tags = {str(tag).lower() for tag in first_value(candidate.get("tags"), candidate.get("targetTags"), target.get("targetTags"), []) or []}
    return wanted in {category, target_type} or wanted in tags


def is_tree_like(candidate: dict) -> bool:
    if candidate_class_ids(candidate) & TREE_CLASSES:
        return True
    target = target_payload(candidate)
    name = str(first_value(candidate.get("name"), target.get("name"), target.get("targetName"), "")).lower()
    category = str(first_value(candidate.get("category"), candidate.get("targetCategory"), target.get("targetCategory"), "")).lower()
    tags = {str(tag).lower() for tag in first_value(candidate.get("targetTags"), target.get("targetTags"), []) or []}
    return category == "tree" or "tree" in tags or any(text in name for text in ("tree", "oak", "willow", "maple", "yew"))


def candidate_distance(candidate: dict) -> float | None:
    for key in ("distanceTiles", "targetDistanceChebyshev", "targetDistanceTiles"):
        value = as_number(candidate.get(key))
        if value is not None:
            return value
    return None


def candidate_tick(candidate: dict) -> int | None:
    return as_int(first_value(candidate.get("tick"), candidate.get("tickId")))


def latest_tick(context: dict) -> int | None:
    status = context["status"]
    baseline = context["baseline"]
    return as_int(first_value(status.get("latestTickProcessed"), status.get("latestTick"), baseline.get("latestTick")))


def candidate_aim_point(candidate: dict) -> dict | None:
    context = candidate.get("aimPointContext")
    if isinstance(context, dict):
        x = as_number(first_value(context.get("canvasX"), context.get("x")))
        y = as_number(first_value(context.get("canvasY"), context.get("y")))
        if x is not None and y is not None:
            return {"canvasX": x, "canvasY": y, "source": context.get("source")}
    aim = candidate.get("aimPoint")
    if not isinstance(aim, dict):
        geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
        aim = geometry.get("aimPoint")
    if isinstance(aim, dict):
        x = as_number(first_value(aim.get("canvasX"), aim.get("x")))
        y = as_number(first_value(aim.get("canvasY"), aim.get("y")))
        if x is not None and y is not None:
            source = candidate.get("preferredGeometryType")
            geometry = candidate.get("geometry") if isinstance(candidate.get("geometry"), dict) else {}
            if not source:
                source = geometry.get("preferredAimGeometryType")
            return {
                "canvasX": x,
                "canvasY": y,
                "source": source,
            }
    return None


def candidate_profile(candidate: dict) -> str | None:
    value = candidate.get("profileId") or candidate.get("profile")
    return str(value) if value else None


def filter_candidates(candidates: list[dict], *, profile: str | None = None) -> list[dict]:
    if not profile:
        return list(candidates)
    wanted = profile.lower()
    filtered = []
    for candidate in candidates:
        profile_id = candidate_profile(candidate)
        if profile_id is None or profile_id.lower() == wanted:
            filtered.append(candidate)
    return filtered


def best_sort_key(candidate: dict) -> tuple:
    quality = as_number(candidate.get("qualityScore")) or 0.0
    score = as_number(candidate.get("score")) or 0.0
    rank = as_number(candidate.get("rank"))
    distance = candidate_distance(candidate)
    return (-quality, -score, rank if rank is not None else 999999, distance if distance is not None else 999999)


def nearest_sort_key(candidate: dict) -> tuple:
    distance = candidate_distance(candidate)
    quality = as_number(candidate.get("qualityScore")) or 0.0
    score = as_number(candidate.get("score")) or 0.0
    return (distance if distance is not None else 999999, -quality, -score)


def select_candidates(candidates: list[dict], class_id: str, *, max_distance: float | None = None, profile: str | None = None) -> list[dict]:
    matches = [candidate for candidate in filter_candidates(candidates, profile=profile) if candidate_matches_class(candidate, class_id)]
    if max_distance is not None:
        matches = [candidate for candidate in matches if candidate_distance(candidate) is not None and candidate_distance(candidate) <= max_distance]
    return matches


def nearest_candidate(candidates: list[dict], class_id: str, max_distance: float | None = None, profile: str | None = None) -> dict | None:
    matches = select_candidates(candidates, class_id, max_distance=max_distance, profile=profile)
    return min(matches, key=nearest_sort_key) if matches else None


def best_candidate(candidates: list[dict], class_id: str, max_distance: float | None = None, profile: str | None = None) -> dict | None:
    matches = select_candidates(candidates, class_id, max_distance=max_distance, profile=profile)
    return min(matches, key=best_sort_key) if matches else None


def freshness_info(context: dict, candidate: dict | None, freshness_ticks: int, freshness_ms: int) -> tuple[dict, list[str]]:
    warnings = []
    status = context["status"]
    baseline = context["baseline"]
    latest = latest_tick(context)
    tick = candidate_tick(candidate) if candidate else None
    tick_delta = latest - tick if isinstance(latest, int) and isinstance(tick, int) else None
    generated = parse_utc(first_value(status.get("generatedAtUtc"), baseline.get("generatedAtUtc")))
    age_ms = None
    if generated:
        age_ms = (datetime.now(timezone.utc) - generated).total_seconds() * 1000.0
    if tick_delta is None:
        warnings.append("candidate/latest tick freshness is unknown.")
    elif tick_delta > freshness_ticks:
        warnings.append(f"candidate tick is stale by {tick_delta} tick(s).")
    if age_ms is None:
        warnings.append("live processor file age is unknown.")
    elif age_ms > freshness_ms:
        warnings.append(f"live processor files are stale by {int(age_ms)} ms.")
    return {
        "candidateTick": tick,
        "latestTick": latest,
        "tickDelta": tick_delta,
        "liveFileAgeMillis": round(age_ms, 3) if age_ms is not None else None,
        "freshnessTickThreshold": freshness_ticks,
        "freshnessMillisThreshold": freshness_ms,
        "freshByTicks": tick_delta is not None and tick_delta <= freshness_ticks,
        "freshByMillis": age_ms is not None and age_ms <= freshness_ms,
    }, warnings


def candidate_answer(candidate: dict | None, context: dict, freshness_ticks: int, freshness_ms: int) -> tuple[dict, str, float, list[str], list[str], list[str]]:
    reasons = []
    warnings = []
    missing = []
    if not candidate:
        return {}, "FAIL", 0.0, reasons, ["no matching candidate found."], ["candidate"]

    target = target_payload(candidate)
    aim = candidate_aim_point(candidate)
    freshness, freshness_warnings = freshness_info(context, candidate, freshness_ticks, freshness_ms)
    warnings.extend(freshness_warnings)
    on_screen = candidate.get("onScreen")
    geometry_available = candidate.get("geometryAvailable")
    ui_blocked = candidate.get("uiBlocked")

    if on_screen is True:
        reasons.append("candidate is on screen.")
    elif on_screen is False:
        warnings.append("candidate is off screen.")
    else:
        warnings.append("candidate onScreen is unknown.")
        missing.append("onScreen")

    if geometry_available is True:
        reasons.append("candidate has geometry.")
    elif geometry_available is False:
        warnings.append("candidate geometry is unavailable.")
    else:
        warnings.append("candidate geometry availability is unknown.")
        missing.append("geometryAvailable")

    if aim:
        reasons.append("candidate has an aim point telemetry field.")
    else:
        warnings.append("candidate has no aim point telemetry field.")
        missing.append("aimPoint")

    if ui_blocked is True:
        warnings.append("candidate aim point is UI-blocked.")
    elif ui_blocked is False:
        reasons.append("candidate is not UI-blocked.")
    else:
        warnings.append("candidate UI blocking is unknown.")

    if context["status"].get("budgetExceeded") is True:
        warnings.append("live processor budget was exceeded on the latest update.")
    if as_int(context["status"].get("writeFailureCount")) and as_int(context["status"].get("writeFailureCount")) > 0:
        warnings.append("live processor reported write failures.")
    if context["status"].get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    if context["status"].get("sourceSceneKnowledgeComplete") is False:
        warnings.append("source scene knowledge is not complete.")

    canvas = context["baseline"].get("cameraViewport") if isinstance(context["baseline"].get("cameraViewport"), dict) else {}
    if aim and as_number(canvas.get("canvasWidth")) and as_number(canvas.get("canvasHeight")):
        width = as_number(canvas.get("canvasWidth"))
        height = as_number(canvas.get("canvasHeight"))
        if aim["canvasX"] < 0 or aim["canvasY"] < 0 or aim["canvasX"] > width or aim["canvasY"] > height:
            warnings.append("candidate aim point is outside known canvas bounds.")

    pass_conditions = [
        freshness.get("freshByTicks"),
        freshness.get("freshByMillis"),
        on_screen is True,
        geometry_available is True,
        bool(aim),
        ui_blocked is False,
    ]
    if all(pass_conditions):
        status = "PASS"
    elif bool(aim) and on_screen is not False and geometry_available is not False:
        status = "WARN"
    else:
        status = "FAIL"

    confidence = 0.3
    confidence += 0.15 if freshness.get("freshByTicks") else 0.0
    confidence += 0.15 if freshness.get("freshByMillis") else 0.0
    confidence += 0.15 if on_screen is True else 0.0
    confidence += 0.15 if geometry_available is True else 0.0
    confidence += 0.1 if aim else 0.0
    confidence += 0.1 if ui_blocked is False else 0.0
    confidence = min(1.0, confidence)

    answer = {
        "classId": candidate_class_id(candidate),
        "targetName": first_value(candidate.get("name"), target.get("name"), target.get("targetName")),
        "id": first_value(candidate.get("id"), target.get("id")),
        "rawId": first_value(candidate.get("rawId"), target.get("rawId")),
        "hash": first_value(candidate.get("hash"), target.get("hash")),
        "worldX": first_value(candidate.get("worldX"), target.get("worldX")),
        "worldY": first_value(candidate.get("worldY"), target.get("worldY")),
        "plane": first_value(candidate.get("plane"), target.get("plane")),
        "sceneX": first_value(candidate.get("sceneX"), target.get("sceneX")),
        "sceneY": first_value(candidate.get("sceneY"), target.get("sceneY")),
        "distanceTiles": candidate_distance(candidate),
        "onScreen": on_screen,
        "geometryAvailable": geometry_available,
        "uiBlocked": ui_blocked,
        "blockingUiRegions": candidate.get("blockingUiRegions") or [],
        "qualityScore": candidate.get("qualityScore"),
        "qualityTier": candidate.get("qualityTier"),
        "aimPoint": aim,
        "preferredGeometryType": candidate.get("preferredGeometryType") or (candidate.get("geometry") or {}).get("preferredAimGeometryType")
        if isinstance(candidate.get("geometry"), dict)
        else candidate.get("preferredGeometryType"),
        "positiveSignals": candidate.get("positiveSignals") or [],
        "negativeSignals": candidate.get("negativeSignals") or [],
        "tick": candidate_tick(candidate),
        "freshness": freshness,
    }
    return answer, status, round(confidence, 3), reasons, warnings, missing


def direct_query_payload(context: dict, query_type: str, class_id: str, args) -> dict:
    candidate = (
        nearest_candidate(context["candidates"], class_id, args.max_distance, args.profile)
        if query_type == "nearest"
        else best_candidate(context["candidates"], class_id, args.max_distance, args.profile)
    )
    answer, status, confidence, reasons, warnings, missing = candidate_answer(
        candidate,
        context,
        args.freshness_ticks,
        args.freshness_ms,
    )
    warnings = list(context["warnings"]) + warnings
    missing = sorted(set(context["missingFields"] + missing))
    payload = {
        "schema": ANSWER_SCHEMA,
        "query": {
            "type": query_type,
            "classId": class_id,
            "profile": args.profile,
            "maxDistance": args.max_distance,
        },
        "answer": answer,
        "status": status,
        "confidence": confidence,
        "reasons": reasons,
        "warnings": warnings,
        "missingFields": missing,
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }
    # Backwards-compatible aliases for the earlier tiny helper.
    payload[query_type] = class_id
    payload["candidate"] = answer
    return payload


def player_location_known(player: dict) -> bool:
    return player.get("worldX") is not None and player.get("worldY") is not None and player.get("plane") is not None


def player_busy_summary(baseline: dict) -> dict:
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    animation = player.get("animation")
    interacting = player.get("interacting")
    moving = player.get("isMoving")
    evidence = {
        "animation": animation,
        "interacting": interacting,
        "isMoving": moving,
        "poseAnimation": player.get("poseAnimation"),
        "idlePoseAnimation": player.get("idlePoseAnimation"),
    }
    if animation not in (None, -1, 0) or interacting not in (None, "", -1):
        value = True
        reason = "non-idle animation or interacting field is present."
    elif animation in (-1, 0) and interacting in (None, "", -1):
        value = False
        reason = "idle animation and no interacting field were observed."
    else:
        value = None
        reason = "animation/interacting fields are unavailable."
    return {"value": value, "evidence": evidence, "reason": reason}


def inventory_readiness(baseline: dict) -> dict:
    inventory = baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {}
    known = any(inventory.get(key) is not None for key in ("freeSlots", "itemCount", "signature"))
    return {
        "known": known,
        "freeSlots": inventory.get("freeSlots"),
        "itemCount": inventory.get("itemCount"),
        "signature": inventory.get("signature"),
    }


def navigation_readiness(navigation: dict, baseline: dict) -> dict:
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    if not navigation:
        return {
            "status": "unknown",
            "collisionKnown": None,
            "currentPlaneKnown": player.get("plane") is not None,
            "playerTileKnown": player.get("sceneX") is not None and player.get("sceneY") is not None,
            "reachabilityComputed": False,
            "warning": "collision/navigation data unavailable; reachability questions cannot be answered yet",
        }
    collision_known = navigation.get("collisionKnown")
    return {
        "status": "known" if collision_known else "unknown",
        "collisionKnown": collision_known,
        "plane": navigation.get("plane"),
        "currentPlaneKnown": navigation.get("plane") is not None or player.get("plane") is not None,
        "playerSceneX": navigation.get("playerSceneX"),
        "playerSceneY": navigation.get("playerSceneY"),
        "playerTileKnown": navigation.get("playerSceneX") is not None and navigation.get("playerSceneY") is not None,
        "mapBounds": navigation.get("mapBounds"),
        "blockedMovementTileCount": navigation.get("blockedMovementTileCount"),
        "obstaclesKnown": navigation.get("obstaclesKnown"),
        "reachabilityComputed": False,
        "notes": navigation.get("notes") or [],
    }


def candidate_identity(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    target = target_payload(candidate)
    return "|".join(
        str(part)
        for part in (
            first_value(candidate.get("objectKey"), target.get("objectKey")),
            first_value(candidate.get("targetKey"), target.get("targetKey")),
            first_value(candidate.get("id"), candidate.get("rawId"), target.get("id"), target.get("rawId")),
            first_value(candidate.get("worldX"), target.get("worldX")),
            first_value(candidate.get("worldY"), target.get("worldY")),
            first_value(candidate.get("plane"), target.get("plane")),
        )
        if part is not None
    ) or None


def aim_jitter(candidates: list[dict]) -> float | None:
    points = [candidate_aim_point(candidate) for candidate in candidates]
    points = [point for point in points if point]
    if len(points) < 2:
        return None
    base = points[0]
    return round(max(math.hypot(point["canvasX"] - base["canvasX"], point["canvasY"] - base["canvasY"]) for point in points[1:]), 3)


def stability_for(candidates: list[dict]) -> dict:
    by_tick: dict[int, list[dict]] = defaultdict(list)
    for candidate in candidates:
        tick = candidate_tick(candidate)
        if tick is not None:
            by_tick[tick].append(candidate)
    if len(by_tick) < 2:
        return {
            "candidateStable": None,
            "reason": "candidate stability unknown; run rolling candidate output to evaluate stability.",
            "recentTickCount": len(by_tick),
        }
    best_by_tick = [min(values, key=best_sort_key) for _tick, values in sorted(by_tick.items())]
    identity_counts = Counter(candidate_identity(candidate) or "unknown" for candidate in best_by_tick)
    top_identity, same_count = identity_counts.most_common(1)[0]
    same_candidates = [candidate for candidate in best_by_tick if candidate_identity(candidate) == top_identity]
    jitter = aim_jitter(same_candidates)
    stable = same_count >= max(2, int(len(best_by_tick) * 0.7)) and (jitter is None or jitter <= 10)
    return {
        "candidateSeenAcrossRecentTicks": dict(identity_counts.most_common(5)),
        "sameBestCandidateTicks": same_count,
        "aimPointJitterPixels": jitter,
        "candidateStable": stable,
        "reason": "same best candidate persisted across recent ticks." if stable else "best candidate flickered or aim point moved noticeably.",
        "recentTickCount": len(by_tick),
    }


def tree_candidates(candidates: list[dict], profile: str | None = None) -> list[dict]:
    return [candidate for candidate in filter_candidates(candidates, profile=profile) if is_tree_like(candidate)]


def woodcutting_task_payload(context: dict, args) -> dict:
    warnings = list(context["warnings"])
    missing = list(context["missingFields"])
    baseline = context["baseline"]
    status_doc = context["status"]
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    candidates = tree_candidates(context["candidates"], profile=args.profile)
    visible = [candidate for candidate in candidates if candidate.get("onScreen") is True]
    nearest_tree = min(candidates, key=nearest_sort_key) if candidates else None
    best_tree = min(candidates, key=best_sort_key) if candidates else None
    nearest_oak = nearest_candidate(candidates, "oak_tree")
    nearest_willow = nearest_candidate(candidates, "willow_tree")
    best_answer, best_status, _confidence, best_reasons, best_warnings, best_missing = candidate_answer(
        best_tree,
        context,
        args.freshness_ticks,
        args.freshness_ms,
    )
    warnings.extend(best_warnings)
    missing.extend(best_missing)
    freshness, freshness_warnings = freshness_info(context, best_tree, args.freshness_ticks, args.freshness_ms)
    warnings.extend(freshness_warnings)
    busy = player_busy_summary(baseline)
    inventory = inventory_readiness(baseline)
    navigation = navigation_readiness(context["navigation"], baseline)

    if not player_location_known(player):
        warnings.append("no player location.")
        missing.append("player.location")
    if not candidates:
        warnings.append("no tree-like live candidates.")
    if not inventory["known"]:
        warnings.append("no inventory/status information available for woodcutting context.")
    if navigation.get("status") == "unknown":
        warnings.append(navigation.get("warning") or "collision/navigation data unavailable; reachability questions cannot be answered yet")
    if not baseline.get("latestFramePath"):
        warnings.append("no frame path in live baseline.")
    if status_doc.get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    if status_doc.get("sourceSceneKnowledgeComplete") is False:
        warnings.append("source scene knowledge is not complete.")

    core_ok = bool(player_location_known(player) and candidates and best_answer.get("aimPoint") and freshness.get("freshByTicks") and freshness.get("freshByMillis"))
    if not candidates or not baseline:
        overall = "FAIL"
    elif best_status == "PASS" and core_ok:
        overall = "PASS" if inventory["known"] and navigation.get("currentPlaneKnown") else "WARN"
    else:
        overall = "WARN" if core_ok or candidates else "FAIL"

    return {
        "schema": TASK_SCHEMA,
        "task": "woodcutting",
        "status": overall,
        "canAnswerCoreQuestions": core_ok,
        "observations": {
            "whereIsPlayerKnown": player_location_known(player),
            "treeCandidatesVisible": bool(visible),
            "bestCandidateHasAimPoint": bool(best_answer.get("aimPoint")),
            "bestCandidateUiBlocked": best_answer.get("uiBlocked"),
            "liveFeedFresh": bool(freshness.get("freshByTicks") and freshness.get("freshByMillis")),
            "playerAppearsBusy": busy,
        },
        "candidateSummary": {
            "visibleTreeCandidateCount": len(visible),
            "treeCandidateCount": len(candidates),
            "nearestTree": candidate_answer(nearest_tree, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_tree else None,
            "bestTree": best_answer,
            "nearestOakTree": candidate_answer(nearest_oak, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_oak else None,
            "nearestWillowTree": candidate_answer(nearest_willow, context, args.freshness_ticks, args.freshness_ms)[0] if nearest_willow else None,
            "bestCandidateAimPoint": best_answer.get("aimPoint"),
            "stability": stability_for(candidates),
        },
        "stateSummary": {
            "player": player,
            "playerAppearsBusy": busy,
            "inventoryKnown": inventory["known"],
            "inventory": inventory,
            "freshness": freshness,
            "sourceSceneKnowledgeComplete": status_doc.get("sourceSceneKnowledgeComplete"),
            "sourceCapHit": status_doc.get("sourceCapHit"),
        },
        "navigationReadiness": navigation,
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "missingFields": sorted(set(str(field) for field in missing if field)),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
        "reasons": best_reasons,
    }


def summary_payload(context: dict, args) -> dict:
    status = context["status"]
    baseline = context["baseline"]
    context_index = context["context"]
    candidates = filter_candidates(context["candidates"], profile=args.profile)
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    freshness, freshness_warnings = freshness_info(context, candidates[0] if candidates else None, args.freshness_ticks, args.freshness_ms)
    counts_by_class = Counter(candidate_class_id(candidate) for candidate in candidates)
    counts_by_category = Counter(str(first_value(candidate.get("category"), candidate.get("targetCategory"), target_payload(candidate).get("targetCategory"), "unknown")) for candidate in candidates)
    counts_by_quality = Counter(str(candidate.get("qualityTier") or "unknown") for candidate in candidates)
    warnings = list(context["warnings"]) + freshness_warnings
    if status.get("budgetExceeded") is True:
        warnings.append("live processor budgetExceeded is true.")
    if as_int(status.get("writeFailureCount")) and as_int(status.get("writeFailureCount")) > 0:
        warnings.append("live processor reported write failures.")
    if status.get("sourceCapHit") is True:
        warnings.append("source scene object cap was hit.")
    return {
        "schema": SUMMARY_SCHEMA,
        "sessionPath": str(context["session"]),
        "generatedAtUtc": utc_now(),
        "latestTick": latest_tick(context),
        "liveProcessorFreshness": freshness,
        "activeProfile": args.profile or status.get("profile") or context_index.get("activeProfile"),
        "player": {
            "worldX": player.get("worldX"),
            "worldY": player.get("worldY"),
            "plane": player.get("plane"),
            "sceneX": player.get("sceneX"),
            "sceneY": player.get("sceneY"),
            "localX": player.get("localX"),
            "localY": player.get("localY"),
        },
        "playerState": {
            "animation": player.get("animation"),
            "interacting": player.get("interacting"),
            "isMoving": player.get("isMoving"),
            "healthRatio": player.get("healthRatio"),
            "healthScale": player.get("healthScale"),
            "runEnergy": player.get("runEnergy") or baseline.get("player", {}).get("runEnergy") if isinstance(baseline.get("player"), dict) else None,
            "inventory": baseline.get("inventory") if isinstance(baseline.get("inventory"), dict) else {},
        },
        "candidateCount": len(candidates),
        "candidateCountsByClassId": dict(counts_by_class.most_common()),
        "candidateCountsByCategory": dict(counts_by_category.most_common()),
        "candidateCountsByQualityTier": dict(counts_by_quality.most_common()),
        "bestCandidateByClassId": context_index.get("bestCandidateByClassId") or {},
        "nearestCandidateByClassId": context_index.get("nearestCandidateByClassId") or {},
        "sourceSceneKnowledgeComplete": status.get("sourceSceneKnowledgeComplete"),
        "sourceCapHit": status.get("sourceCapHit"),
        "budgetExceeded": status.get("budgetExceeded"),
        "writeFailures": status.get("writeFailureCount"),
        "warningCount": len(warnings) + int(status.get("warningCount") or 0),
        "liveFileAgeMillis": freshness.get("liveFileAgeMillis"),
        "warnings": sorted(set(str(warning) for warning in warnings if warning)),
        "missingFields": sorted(set(context["missingFields"])),
        "sourceFiles": context["sourceFiles"],
    }


def status_for_checks(checks: list[dict]) -> str:
    if any(check["status"] == "FAIL" for check in checks):
        return "FAIL"
    if any(check["status"] == "WARN" for check in checks):
        return "WARN"
    return "PASS"


def check(name: str, ok: bool | None, reason: str) -> dict:
    if ok is True:
        status = "PASS"
    elif ok is False:
        status = "FAIL"
    else:
        status = "WARN"
    return {"name": name, "status": status, "reason": reason}


def self_test_payload(context: dict, args) -> dict:
    status = context["status"]
    baseline = context["baseline"]
    context_index = context["context"]
    candidates = context["candidates"]
    active_profile = args.profile or status.get("profile")
    nearest_tree = nearest_candidate(candidates, "tree", profile=args.profile)
    best_tree = best_candidate(candidates, "tree", profile=args.profile)
    freshness, freshness_warnings = freshness_info(context, best_tree or (candidates[0] if candidates else None), args.freshness_ticks, args.freshness_ms)
    checks = [
        check("baseline readable", bool(baseline), "live_baseline_state.json was loaded."),
        check("status readable", bool(status), "live_status.json was loaded."),
        check("context index readable", bool(context_index), "live_context_index.json was loaded."),
        check("candidates readable", context["paths"]["candidates"].exists(), "live_candidates.jsonl was present."),
        check("latest tick known", latest_tick(context) is not None, "latest tick is available in live status/baseline."),
        check("candidate count > 0", len(candidates) > 0, f"{len(candidates)} candidate record(s) loaded."),
        check("live data fresh", freshness.get("freshByTicks") and freshness.get("freshByMillis"), "candidate and live files are inside freshness thresholds."),
        check("no write failures", not (as_int(status.get("writeFailureCount")) and as_int(status.get("writeFailureCount")) > 0), "live_status writeFailureCount is zero."),
        check("source cap not hit", status.get("sourceCapHit") is not True, "sourceCapHit is not true."),
    ]
    if str(active_profile or "").lower() == "woodcutting":
        checks.extend(
            [
                check("nearest tree candidate", nearest_tree is not None, "woodcutting profile has a nearest tree candidate."),
                check("best tree candidate", best_tree is not None, "woodcutting profile has a best tree candidate."),
                check("best tree aim point", candidate_aim_point(best_tree) is not None if best_tree else False, "best tree has an aim point."),
            ]
        )
    return {
        "schema": SELF_TEST_SCHEMA,
        "status": status_for_checks(checks),
        "checks": checks,
        "warnings": sorted(set(context["warnings"] + freshness_warnings)),
        "missingFields": sorted(set(context["missingFields"])),
        "sourceFiles": context["sourceFiles"],
        "generatedAtUtc": utc_now(),
    }


def print_summary_human(payload: dict) -> None:
    print("Live Context Summary")
    print(f"session: {payload.get('sessionPath')}")
    print(f"latest tick: {payload.get('latestTick', 'unknown')}")
    print(f"active profile: {payload.get('activeProfile') or 'unknown'}")
    print(f"candidate count: {payload.get('candidateCount')}")
    player = payload.get("player") or {}
    print(f"player world: {player.get('worldX', 'unknown')},{player.get('worldY', 'unknown')} plane={player.get('plane', 'unknown')}")
    print(f"player scene: {player.get('sceneX', 'unknown')},{player.get('sceneY', 'unknown')}")
    print(f"source complete: {payload.get('sourceSceneKnowledgeComplete')} capHit={payload.get('sourceCapHit')}")
    print(f"budgetExceeded: {payload.get('budgetExceeded')} writeFailures={payload.get('writeFailures')}")
    print("counts by class:", payload.get("candidateCountsByClassId") or {})
    print("counts by quality:", payload.get("candidateCountsByQualityTier") or {})
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def print_answer_human(payload: dict) -> None:
    print(f"Live Context Answer: {payload.get('status')} confidence={payload.get('confidence')}")
    print(f"query: {payload.get('query')}")
    answer = payload.get("answer") or {}
    if answer:
        print(f"candidate: {answer.get('targetName')} class={answer.get('classId')} quality={answer.get('qualityTier')} score={answer.get('qualityScore')}")
        print(f"distance: {answer.get('distanceTiles')} onScreen={answer.get('onScreen')} geometry={answer.get('geometryAvailable')} uiBlocked={answer.get('uiBlocked')}")
        print(f"aimPoint: {answer.get('aimPoint')}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def print_task_human(payload: dict) -> None:
    print(f"Live Task Context: {payload.get('task')} status={payload.get('status')}")
    print(f"can answer core questions: {payload.get('canAnswerCoreQuestions')}")
    summary = payload.get("candidateSummary") or {}
    print(f"visible tree candidates: {summary.get('visibleTreeCandidateCount')}")
    best = summary.get("bestTree") or {}
    if best:
        print(f"best tree: {best.get('targetName')} distance={best.get('distanceTiles')} aim={best.get('aimPoint')}")
    state = payload.get("stateSummary") or {}
    busy = state.get("playerAppearsBusy") or {}
    print(f"player appears busy: {busy.get('value')} ({busy.get('reason')})")
    navigation = payload.get("navigationReadiness") or {}
    print(f"navigation readiness: {navigation.get('status')} collisionKnown={navigation.get('collisionKnown')}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def print_self_test_human(payload: dict) -> None:
    print(f"Live Context Self-Test: {payload.get('status')}")
    for item in payload.get("checks") or []:
        print(f"{item.get('status'):4} {item.get('name')}: {item.get('reason')}")
    if payload.get("warnings"):
        print("warnings:")
        for warning in payload["warnings"]:
            print(f"- {warning}")


def build_payload(args) -> tuple[dict, int]:
    session, session_warnings = resolve_session(args)
    if session is None:
        payload = {
            "schema": ANSWER_SCHEMA,
            "query": {},
            "answer": {},
            "status": "FAIL",
            "confidence": 0.0,
            "reasons": [],
            "warnings": session_warnings + ["No telemetry session found."],
            "missingFields": ["session"],
            "sourceFiles": [],
            "generatedAtUtc": utc_now(),
        }
        return payload, 1
    context = load_live_context(session)
    context["warnings"] = session_warnings + context["warnings"]
    if args.self_test:
        return self_test_payload(context, args), 0
    if args.task:
        if args.task.lower() == "woodcutting":
            return woodcutting_task_payload(context, args), 0
        return {
            "schema": TASK_SCHEMA,
            "task": args.task,
            "status": "FAIL",
            "canAnswerCoreQuestions": False,
            "observations": {},
            "candidateSummary": {},
            "stateSummary": {},
            "navigationReadiness": {},
            "warnings": context["warnings"] + [f"Unsupported task context: {args.task}"],
            "missingFields": context["missingFields"],
            "sourceFiles": context["sourceFiles"],
            "generatedAtUtc": utc_now(),
        }, 1
    if args.nearest:
        return direct_query_payload(context, "nearest", args.nearest, args), 0
    if args.best:
        return direct_query_payload(context, "best", args.best, args), 0
    if args.baseline:
        return context["baseline"], 0
    return summary_payload(context, args), 0


def print_payload(payload: dict, args) -> None:
    if args.json:
        print(json_dump_compact(payload))
        return
    schema = payload.get("schema")
    if schema == SUMMARY_SCHEMA:
        print_summary_human(payload)
    elif schema == ANSWER_SCHEMA:
        print_answer_human(payload)
    elif schema == TASK_SCHEMA:
        print_task_human(payload)
    elif schema == SELF_TEST_SCHEMA:
        print_self_test_human(payload)
    else:
        print(json.dumps(payload, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description="Read-only live context QA/query helper for rolling target telemetry files.")
    parser.add_argument("--session", help="Telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override sessions directory when --session is omitted.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest telemetry session.")
    parser.add_argument("--summary", action="store_true", help="Print a compact live context summary.")
    parser.add_argument("--nearest", help="Return nearest candidate for a class id, such as tree or npc.")
    parser.add_argument("--best", help="Return best quality candidate for a class id, such as tree or npc.")
    parser.add_argument("--profile", help="Filter candidates by active profile id.")
    parser.add_argument("--max-distance", type=float, help="Maximum candidate Chebyshev tile distance for nearest/best queries.")
    parser.add_argument("--freshness-ticks", type=int, default=DEFAULT_FRESHNESS_TICKS, help="Maximum acceptable candidate tick delta.")
    parser.add_argument("--freshness-ms", type=int, default=DEFAULT_FRESHNESS_MS, help="Maximum acceptable live file age in milliseconds.")
    parser.add_argument("--task", help="Run a task-context QA report, currently woodcutting.")
    parser.add_argument("--self-test", action="store_true", help="Run read-only live context readiness checks.")
    parser.add_argument("--baseline", action="store_true", help="Return live_baseline_state.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--watch", action="store_true", help="Repeat the query until interrupted.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between --watch refreshes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    exit_code = 0
    while True:
        payload, code = build_payload(args)
        exit_code = max(exit_code, code)
        print_payload(payload, args)
        if not args.watch:
            break
        try:
            time.sleep(max(0.1, args.interval))
        except KeyboardInterrupt:
            break
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
