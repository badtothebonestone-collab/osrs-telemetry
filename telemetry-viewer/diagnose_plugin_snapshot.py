import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import build_world_target_geometry as world_builder
import live_packet_reader
import live_target_processor as live
import select_target_candidates as candidate_builder
from telemetry_paths import find_newest_session, get_sessions_dir


SCHEMA = "plugin_snapshot_diagnostic.v1"


def request_snapshot(args) -> tuple[dict | None, str | None, int]:
    body = live.plugin_snapshot_request_body(
        SimpleNamespace(
            profile=getattr(args, "profile", "woodcutting"),
            target_type=getattr(args, "target_type", "all"),
            limit=getattr(args, "limit", 100),
            plugin_snapshot_tier=getattr(args, "tier", live.PLUGIN_SNAPSHOT_DEFAULT_TIER),
            plugin_snapshot_max_age_ticks=getattr(args, "max_age_ticks", 5),
            plugin_snapshot_max_projection_refs=getattr(args, "max_projection_refs", None),
            plugin_snapshot_include_geometry=bool(getattr(args, "include_geometry", False)),
            plugin_snapshot_response_mode=getattr(args, "response_mode", "compact"),
            plugin_snapshot_projection_field_mode=getattr(args, "projection_field_mode", "compact"),
        )
    )
    encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        live.plugin_snapshot_url(args.host, args.port, "/snapshot"),
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if args.token:
        request.add_header("X-Plugin-Snapshot-Token", args.token)
    request_started = time.perf_counter()
    http_ms = 0.0
    read_ms = 0.0
    parse_ms = 0.0
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            opened_at = time.perf_counter()
            http_ms = (opened_at - request_started) * 1000.0
            read_started = time.perf_counter()
            raw = response.read()
            read_ms = (time.perf_counter() - read_started) * 1000.0
    except urllib.error.HTTPError as error:
        opened_at = time.perf_counter()
        http_ms = (opened_at - request_started) * 1000.0
        raw = error.read()
        read_ms = (time.perf_counter() - opened_at) * 1000.0
        if raw:
            try:
                parse_started = time.perf_counter()
                payload = json.loads(raw.decode("utf-8", errors="replace"))
                parse_ms = (time.perf_counter() - parse_started) * 1000.0
            except json.JSONDecodeError as decode_error:
                return None, f"HTTPError {error.code}; JSONDecodeError: {decode_error}", len(raw)
            if isinstance(payload, dict):
                payload["_diagnosticTiming"] = {
                    "httpRequestMillis": round(http_ms, 3),
                    "responseReadMillis": round(read_ms, 3),
                    "jsonParseMillis": round(parse_ms, 3),
                    "requestTotalMillis": round(http_ms + read_ms + parse_ms, 3),
                }
            return payload if isinstance(payload, dict) else None, None, len(raw)
        return None, f"HTTPError {error.code}: {error}", 0
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        return None, f"{type(error).__name__}: {error}", 0
    try:
        parse_started = time.perf_counter()
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        parse_ms = (time.perf_counter() - parse_started) * 1000.0
    except json.JSONDecodeError as error:
        return None, f"JSONDecodeError: {error}", len(raw)
    if isinstance(payload, dict):
        payload["_diagnosticTiming"] = {
            "httpRequestMillis": round(http_ms, 3),
            "responseReadMillis": round(read_ms, 3),
            "jsonParseMillis": round(parse_ms, 3),
            "requestTotalMillis": round(http_ms + read_ms + parse_ms, 3),
        }
    return payload if isinstance(payload, dict) else None, None, len(raw)


def latest_projection_packet(session: Path) -> dict | None:
    segment = live_packet_reader.latest_segment_path(session)
    if not segment:
        return None
    latest = None
    for result in live_packet_reader.iter_live_packets([segment], packet_type=live.COMPACT_PACKET_TYPES["projection"]):
        if result.record:
            latest = result.record
    return latest


def latest_compact_synthetic_tick(session: Path) -> dict | None:
    segment = live_packet_reader.latest_segment_path(session)
    if not segment:
        return None
    packets_by_tick: dict[int, list[dict]] = {}
    for result in live_packet_reader.iter_live_packets([segment]):
        if not result.record:
            continue
        tick = live.packet_tick(result.record)
        if tick is None:
            continue
        packets_by_tick.setdefault(tick, []).append(result.record)
    if not packets_by_tick:
        return None
    latest_tick = max(packets_by_tick)
    return live.compact_packets_to_tick(packets_by_tick[latest_tick])


def profile_diagnostics(session: Path | None, tick: dict | None, profile_id: str, limit: int) -> dict:
    if session is None or tick is None:
        return {
            "worldTargetsBuilt": 0,
            "profileMatchedWorldTargets": 0,
            "candidateCount": 0,
            "rejectReasons": {},
            "warnings": ["session or converted tick unavailable"],
        }

    args = SimpleNamespace(
        target_type="all",
        exclude_ui_blocked=False,
        profile=profile_id,
        target_library=str(live.DEFAULT_TARGET_LIBRARY_PATH),
        target_profiles=str(live.DEFAULT_TARGET_PROFILES_PATH),
        limit=limit,
    )
    overrides, override_warnings = world_builder.load_target_overrides()
    library, profiles, profile, profile_warnings = live.load_profile_documents(args)
    target_types = live.target_types_for_profile(args, profile)
    build_started = time.perf_counter()
    world_records, build_stats = live.build_world_records_for_tick(
        world_builder.session_id_for(session),
        session,
        tick,
        target_types,
        overrides,
    )
    world_records = live.filter_target_type(world_records, args)
    profile_records = [record for record in world_records if live.profile_source_record(record, library, profile)]
    world_build_ms = (time.perf_counter() - build_started) * 1000.0
    candidate_started = time.perf_counter()
    candidates, candidate_stats, candidate_warnings = live.rank_live_candidates(
        session,
        [tick],
        profile_records,
        [],
        args,
        library,
        profile,
    )
    candidate_ms = (time.perf_counter() - candidate_started) * 1000.0
    return {
        "worldTargetsBuilt": len(world_records),
        "profileMatchedWorldTargets": len(profile_records),
        "candidateCount": len(candidates),
        "worldBuildMillis": round(world_build_ms, 3),
        "candidateMillis": round(candidate_ms, 3),
        "candidateCountsByClassId": live.counts_for_candidates(candidates).get("classId", {}),
        "bestCandidate": live.compare_candidate_summary(candidates[0]) if candidates else None,
        "buildStats": build_stats,
        "candidateStats": candidate_stats,
        "rejectReasons": candidate_stats.get("rejectReasons") or {},
        "warnings": list(override_warnings) + list(profile_warnings) + list(candidate_warnings),
    }


def compact_projection_diagnostics(session: Path | None) -> dict:
    if session is None:
        return {"available": False, "reason": "session unavailable"}
    packet = latest_projection_packet(session)
    if not packet:
        return {"available": False, "reason": "latest live_projection_packet.v1 not found"}
    payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
    diagnostics = live.projection_payload_diagnostics(payload)
    return {
        "available": True,
        "tick": packet.get("tick"),
        "sequence": packet.get("sequence"),
        "payloadDiagnostics": diagnostics,
    }


def shape_payload(tick: dict | None) -> dict:
    if not isinstance(tick, dict):
        return {"available": False}
    diag = live.synthetic_tick_ref_diagnostics(tick)
    first_ref = diag.get("firstNormalizedRef") if isinstance(diag.get("firstNormalizedRef"), dict) else {}
    return {
        "available": True,
        "tickId": live.tick_id_for(tick),
        "topLevelKeys": sorted(tick.keys()),
        "projectionRelatedKeys": sorted(key for key in tick.keys() if "projection" in str(key).lower() or "visible" in str(key).lower()),
        "sceneIndexRelatedKeys": sorted(key for key in tick.keys() if "scene" in str(key).lower() or "index" in str(key).lower()),
        "refPathCounts": diag.get("pathCounts") or {},
        "visibleRefsExpectedPathCount": diag.get("visibleRefsExpectedPathCount"),
        "sceneObjectRefsAtExpectedPath": diag.get("sceneObjectRefsAtExpectedPath"),
        "projectionRefsAtExpectedPath": diag.get("projectionRefsAtExpectedPath"),
        "refsAcceptedForWorldTargets": diag.get("refsAcceptedForWorldTargets"),
        "refsIgnoredWrongPath": diag.get("refsIgnoredWrongPath"),
        "refsIgnoredWrongPathCounts": diag.get("refsIgnoredWrongPathCounts") or {},
        "refsIgnoredReasons": diag.get("refsIgnoredReasons") or {},
        "refsWithOnScreenTrue": diag.get("refsWithOnScreenTrue"),
        "refsWithGeometryAvailableTrue": diag.get("refsWithGeometryAvailableTrue"),
        "refsWithTargetTypeSceneObject": diag.get("refsWithTargetTypeSceneObject"),
        "refsWithIdHashWorldSceneAim": diag.get("refsWithIdHashWorldSceneAim"),
        "firstNormalizedRef": {
            key: first_ref.get(key)
            for key in (
                "objectKey",
                "targetType",
                "id",
                "rawId",
                "hash",
                "name",
                "objectName",
                "kind",
                "worldX",
                "worldY",
                "plane",
                "sceneX",
                "sceneY",
                "onScreen",
                "geometryAvailable",
                "aimPoint",
                "canvasLocation",
                "bounds",
                "clickboxBounds",
                "geometrySource",
            )
            if key in first_ref
        },
        "builderReadsPaths": list(world_builder.SCENE_OBJECT_COLLECTIONS),
        "placesRefsAtBuilderPath": int(diag.get("refsAcceptedForWorldTargets") or 0) > 0,
    }


def conclusion(snapshot_diag: dict, profile_diag: dict, compact_diag: dict) -> str:
    projection_diag = snapshot_diag.get("projectionDiagnostics") or {}
    if snapshot_diag.get("requestFailed") and snapshot_diag.get("errorCode") == "response_too_large":
        return "snapshot endpoint available but response exceeded configured size limit"
    if not snapshot_diag.get("available"):
        return "snapshot endpoint unavailable"
    if not projection_diag.get("refListFound"):
        return "projection payload shape mismatch"
    if int(projection_diag.get("refsConverted") or 0) <= 0:
        return "target fields missing after snapshot conversion"
    if profile_diag.get("worldTargetsBuilt", 0) <= 0:
        return "conversion produced no visible refs"
    if profile_diag.get("profileMatchedWorldTargets", 0) <= 0:
        missing = projection_diag.get("fieldMissingCounts") or {}
        if missing.get("name") == projection_diag.get("refCount"):
            return "profile matching failed due to missing name/action/class fields"
        return "world targets built but none matched the selected profile"
    if profile_diag.get("candidateCount", 0) <= 0:
        return "profile refs matched but candidate ranking rejected them"
    if snapshot_diag.get("projectionCapped") and compact_diag.get("available"):
        compact_count = ((compact_diag.get("payloadDiagnostics") or {}).get("refCount") or 0)
        snapshot_count = projection_diag.get("refCount") or 0
        if compact_count > snapshot_count:
            return "tree refs may be missing due to projection cap/order"
    return "snapshot conversion produced candidates"


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser()
    if args.latest_session:
        return find_newest_session(get_sessions_dir(args.sessions_dir))
    return None


def print_human(payload: dict) -> None:
    print("Plugin snapshot diagnostic")
    print(f"status: {payload.get('status')}")
    print(f"endpoint: {payload.get('endpoint')}")
    print(f"session: {payload.get('sessionPath') or 'none'}")
    snapshot = payload.get("snapshot") or {}
    print(f"snapshot available: {str(bool(snapshot.get('available'))).lower()}")
    print(f"snapshot latest tick: {snapshot.get('latestTick')}")
    print(f"snapshot status: {snapshot.get('status')}")
    if snapshot.get("requestFailed"):
        print(f"snapshot request failed: true error={snapshot.get('errorCode') or snapshot.get('error')}")
    if snapshot.get("responseSizing"):
        print(f"response sizing: {snapshot.get('responseSizing')}")
    print(f"payload types: {snapshot.get('payloadTypes') or []}")
    projection = snapshot.get("projectionDiagnostics") or {}
    print(f"projection keys: {projection.get('topLevelKeys') or []}")
    print(f"projection ref list: {projection.get('refListPath') or 'missing'}")
    print(f"projection refs: {projection.get('refCount')} converted={projection.get('refsConverted')}")
    print(f"first ref keys: {projection.get('firstRefKeys') or []}")
    print(f"missing fields: {projection.get('fieldMissingCounts') or {}}")
    profile = payload.get("profileConversion") or {}
    print(f"world targets built: {profile.get('worldTargetsBuilt')}")
    print(f"profile-matched world targets: {profile.get('profileMatchedWorldTargets')}")
    print(f"candidate count: {profile.get('candidateCount')}")
    if profile.get("bestCandidate"):
        print(f"best candidate: {profile.get('bestCandidate')}")
    if profile.get("rejectReasons"):
        print(f"reject reasons: {profile.get('rejectReasons')}")
    if payload.get("syntheticShape"):
        shape = payload.get("syntheticShape") or {}
        plugin_shape = shape.get("pluginSnapshot") or {}
        compact_shape = shape.get("compactPackets") or {}
        print("synthetic shape:")
        print(f"  plugin keys: {plugin_shape.get('topLevelKeys') or []}")
        print(f"  compact keys: {compact_shape.get('topLevelKeys') or []}")
        print(f"  plugin ref paths: {plugin_shape.get('refPathCounts') or {}}")
        print(f"  compact ref paths: {compact_shape.get('refPathCounts') or {}}")
        print(f"  builder reads: {plugin_shape.get('builderReadsPaths') or []}")
        print(f"  plugin refs accepted by builder: {plugin_shape.get('refsAcceptedForWorldTargets')}")
        print(f"  compact refs accepted by builder: {compact_shape.get('refsAcceptedForWorldTargets')}")
        print(f"  plugin first normalized ref: {plugin_shape.get('firstNormalizedRef') or {}}")
        print(f"  compact first normalized ref: {compact_shape.get('firstNormalizedRef') or {}}")
    compact = payload.get("compactPackets") or {}
    if compact.get("available"):
        compact_projection = compact.get("payloadDiagnostics") or {}
        print(f"compact projection refs: {compact_projection.get('refCount')} path={compact_projection.get('refListPath')}")
    print(f"conclusion: {payload.get('conclusion')}")


def run_diagnostic(args, session: Path | None) -> dict:
    started = time.perf_counter()
    response, error, response_bytes = request_snapshot(args)
    response_schema = response.get("schema") if isinstance(response, dict) else None
    tick = live.plugin_snapshot_to_tick(response) if response_schema == "plugin_snapshot_response.v1" else None
    compact_tick = latest_compact_synthetic_tick(session) if session else None
    response_error_code = response.get("errorCode") if isinstance(response, dict) and isinstance(response.get("errorCode"), str) else None
    response_status = response.get("status") if isinstance(response, dict) else None
    structured_endpoint_response = response_schema in {"plugin_snapshot_response.v1", "plugin_snapshot_error.v1"}
    request_failed = bool(response_error_code or response_status == "FAIL")
    snapshot_diag = {
        "available": bool(response and structured_endpoint_response),
        "requestFailed": request_failed,
        "errorCode": response_error_code,
        "tier": response.get("snapshotTier") if isinstance(response, dict) else getattr(args, "tier", None),
        "latestTick": response.get("latestTick") if isinstance(response, dict) else None,
        "status": response_status,
        "warnings": response.get("warnings") if isinstance(response, dict) else [],
        "missingCapabilities": response.get("missingCapabilities") if isinstance(response, dict) else [],
        "responseSizing": response.get("responseSizing") if isinstance(response, dict) and isinstance(response.get("responseSizing"), dict) else {},
        "maxResponseBytes": response.get("maxResponseBytes") if isinstance(response, dict) else None,
        "estimatedResponseBytes": response.get("estimatedResponseBytes") if isinstance(response, dict) else None,
        "payloadTypes": live.plugin_snapshot_payload_types(response),
        "projectionRefs": live.plugin_snapshot_projection_ref_count(response),
        "projectionCapped": live.plugin_snapshot_is_projection_capped(response),
        "projectionDiagnostics": live.plugin_snapshot_projection_diagnostics(response),
        "responseBytes": response_bytes,
        "diagnosticTiming": response.get("_diagnosticTiming") if isinstance(response, dict) and isinstance(response.get("_diagnosticTiming"), dict) else {},
        "endpointServiceMillis": response.get("serviceTimingMillis") if isinstance(response, dict) else None,
        "error": error,
    }
    profile_diag = profile_diagnostics(session, tick, args.profile, args.limit)
    compact_diag = compact_projection_diagnostics(session)
    status = "PASS" if profile_diag.get("candidateCount", 0) > 0 else "WARN" if snapshot_diag.get("available") else "FAIL"
    return {
        "schema": SCHEMA,
        "generatedAtUtc": live.utc_now(),
        "status": status,
        "endpoint": f"http://{args.host}:{args.port}/snapshot",
        "sessionPath": str(session) if session else None,
        "profile": args.profile,
        "tier": getattr(args, "tier", None),
        "requestedMaxProjectionRefs": live.effective_plugin_snapshot_max_projection_refs(
            SimpleNamespace(
                plugin_snapshot_tier=getattr(args, "tier", live.PLUGIN_SNAPSHOT_DEFAULT_TIER),
                plugin_snapshot_max_projection_refs=getattr(args, "max_projection_refs", None),
            )
        ),
        "elapsedMillis": round((time.perf_counter() - started) * 1000.0, 3),
        "snapshot": snapshot_diag,
        "syntheticTick": {
            "available": tick is not None,
            "tickId": live.tick_id_for(tick) if tick else None,
            "keys": sorted(tick.keys()) if isinstance(tick, dict) else [],
            "visibleSceneObjectRefs": len(tick.get("visibleSceneObjectRefs") or []) if isinstance(tick, dict) else 0,
            "rawCounts": live.raw_counts_for_tick(tick) if isinstance(tick, dict) else {},
        },
        "syntheticShape": {
            "pluginSnapshot": shape_payload(tick),
            "compactPackets": shape_payload(compact_tick),
            "explicitQuestions": {
                "sameRefPath": (
                    shape_payload(tick).get("refPathCounts", {}).get("visibleSceneObjectRefs")
                    == shape_payload(compact_tick).get("refPathCounts", {}).get("visibleSceneObjectRefs")
                ) if tick and compact_tick else None,
                "downstreamBuilderReadsPath": list(world_builder.SCENE_OBJECT_COLLECTIONS),
                "pluginRefsAtReadPath": shape_payload(tick).get("refsAcceptedForWorldTargets") if tick else 0,
                "compactRefsAtReadPath": shape_payload(compact_tick).get("refsAcceptedForWorldTargets") if compact_tick else 0,
            },
        } if args.dump_synthetic_shape else None,
        "profileConversion": profile_diag,
        "compactPackets": compact_diag,
        "conclusion": conclusion(snapshot_diag, profile_diag, compact_diag),
    }


def tier_sweep_payload(args, session: Path | None) -> dict:
    tiers = {}
    compact_diag = compact_projection_diagnostics(session)
    compact_candidates = None
    for tier in ("hot", "expanded", "audit"):
        tier_args = SimpleNamespace(**vars(args))
        tier_args.tier = tier
        tier_args.max_projection_refs = None
        payload = run_diagnostic(tier_args, session)
        profile = payload.get("profileConversion") or {}
        snapshot = payload.get("snapshot") or {}
        timing = snapshot.get("diagnosticTiming") or {}
        tiers[tier] = {
            "status": payload.get("status"),
            "requestedMaxProjectionRefs": payload.get("requestedMaxProjectionRefs"),
            "refsReturned": snapshot.get("projectionRefs"),
            "responseBytes": snapshot.get("responseBytes"),
            "endpointServiceMillis": snapshot.get("endpointServiceMillis"),
            "httpRequestMillis": timing.get("httpRequestMillis"),
            "responseReadMillis": timing.get("responseReadMillis"),
            "jsonParseMillis": timing.get("jsonParseMillis"),
            "requestTotalMillis": timing.get("requestTotalMillis"),
            "responseSizing": snapshot.get("responseSizing") or {},
            "candidates": profile.get("candidateCount"),
            "worldBuildMillis": profile.get("worldBuildMillis"),
            "candidateMillis": profile.get("candidateMillis"),
            "bestCandidate": profile.get("bestCandidate"),
            "candidateCountsByClassId": profile.get("candidateCountsByClassId") or {},
            "conclusion": payload.get("conclusion"),
        }
        if compact_candidates is None:
            compact_candidates = profile.get("candidateCount")
    recommendation = "hot"
    if int((tiers.get("hot") or {}).get("candidates") or 0) <= 0 and int((tiers.get("expanded") or {}).get("candidates") or 0) > 0:
        recommendation = "expanded"
    elif (tiers.get("hot") or {}).get("status") == "FAIL" and (tiers.get("expanded") or {}).get("status") == "FAIL":
        recommendation = "compact-packets"
    return {
        "schema": "plugin_snapshot_tier_sweep.v1",
        "generatedAtUtc": live.utc_now(),
        "endpoint": f"http://{args.host}:{args.port}/snapshot",
        "sessionPath": str(session) if session else None,
        "profile": args.profile,
        "tiers": tiers,
        "compactPackets": compact_diag,
        "recommendation": recommendation,
    }


def print_tier_sweep(payload: dict) -> None:
    print("Plugin snapshot tier sweep")
    print(f"endpoint: {payload.get('endpoint')}")
    print(f"session: {payload.get('sessionPath') or 'none'}")
    for tier, data in (payload.get("tiers") or {}).items():
        print(
            f"{tier}: status={data.get('status')} refs={data.get('refsReturned')} "
            f"bytes={data.get('responseBytes')} endpointMs={data.get('endpointServiceMillis')} "
            f"httpMs={data.get('httpRequestMillis')} parseMs={data.get('jsonParseMillis')} "
            f"worldMs={data.get('worldBuildMillis')} candidateMs={data.get('candidateMillis')} "
            f"candidates={data.get('candidates')} "
            f"best={data.get('bestCandidate')}"
        )
    print(f"recommendation: {payload.get('recommendation')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose experimental plugin snapshot projection conversion.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8893)
    parser.add_argument("--token", default="")
    parser.add_argument("--timeout", type=float, default=0.5)
    parser.add_argument("--session")
    parser.add_argument("--sessions-dir")
    parser.add_argument("--latest-session", action="store_true")
    parser.add_argument("--profile", default="woodcutting")
    parser.add_argument("--tier", choices=sorted(live.PLUGIN_SNAPSHOT_TIERS), default=live.PLUGIN_SNAPSHOT_DEFAULT_TIER)
    parser.add_argument("--max-projection-refs", type=int)
    parser.add_argument("--max-age-ticks", type=int, default=5)
    parser.add_argument("--include-geometry", action="store_true")
    parser.add_argument("--response-mode", choices=["compact", "normal", "full"], default="compact")
    parser.add_argument("--projection-field-mode", choices=sorted(live.PLUGIN_SNAPSHOT_PROJECTION_FIELD_MODES), default="compact")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--tier-sweep", action="store_true")
    parser.add_argument("--dump-synthetic-shape", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    session = resolve_session(args)
    payload = tier_sweep_payload(args, session) if args.tier_sweep else run_diagnostic(args, session)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    elif args.tier_sweep:
        print_tier_sweep(payload)
    else:
        print_human(payload)
    status = payload.get("status", "WARN")
    return 0 if status in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
