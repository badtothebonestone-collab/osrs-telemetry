from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import live_packet_reader
from telemetry_paths import directory_size, find_newest_session, get_sessions_dir, list_tick_files


SCHEMA = "live_setup_check.v1"
RECENT_SECONDS = 120.0


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_session(args) -> Path | None:
    if args.session:
        return Path(args.session).expanduser().resolve()
    if args.latest_session:
        newest = find_newest_session(get_sessions_dir(args.sessions_dir))
        return newest.resolve() if newest else None
    return None


def size_payload(path: Path) -> dict:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": directory_size(path),
    }


def file_age_seconds(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def first_present(*values):
    for value in values:
        if value is not None:
            return value
    return None


def plugin_snapshot_url(host: str, port: int, path: str) -> str:
    return f"http://{host or '127.0.0.1'}:{int(port or 8893)}{path}"


def request_json(url: str, *, method: str = "GET", body: dict | None = None, timeout: float = 0.2) -> tuple[dict | None, str | None, int | None]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=max(0.001, float(timeout))) as response:
            raw = response.read()
            status = response.getcode()
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else None, None, status
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}", None


def plugin_snapshot_check(host: str = "127.0.0.1", port: int = 8893, timeout: float = 0.2) -> dict:
    health, health_error, health_status_code = request_json(plugin_snapshot_url(host, port, "/health"), timeout=timeout)
    result = {
        "enabled": bool(health),
        "healthStatusCode": health_status_code,
        "healthError": health_error,
        "health": health if isinstance(health, dict) else None,
        "snapshotStatusCode": None,
        "snapshotError": None,
        "snapshot": None,
    }
    if not isinstance(health, dict):
        return result
    request = {
        "schema": "plugin_snapshot_request.v1",
        "needs": ["baseline", "projection", "inventory", "navigation", "collision_window", "writer_health"],
        "maxAgeTicks": 5,
        "maxProjectionRefs": 25,
        "responseMode": "compact",
    }
    snapshot, snapshot_error, snapshot_status_code = request_json(
        plugin_snapshot_url(host, port, "/snapshot"),
        method="POST",
        body=request,
        timeout=timeout,
    )
    result["snapshotStatusCode"] = snapshot_status_code
    result["snapshotError"] = snapshot_error
    result["snapshot"] = snapshot if isinstance(snapshot, dict) else None
    return result


def parse_latest_segment(session: Path, max_lines: int = 500) -> dict:
    latest = live_packet_reader.latest_segment_path(session)
    counts = {}
    malformed = 0
    latest_tick = None
    latest_sequence = None
    parsed = 0
    latest_navigation_tick = None
    collision_known = None
    latest_collision_window_tick = None
    collision_window_available = False
    collision_window_radius = None
    collision_window_width = None
    collision_window_height = None
    collision_window_tile_count = None
    collision_window_hash = None

    if latest is None:
        return {
            "latestSegment": None,
            "latestSegmentExists": False,
            "parsedPackets": 0,
            "malformedLines": 0,
            "packetCountsByType": {},
            "latestTick": None,
            "latestSequence": None,
        }

    for result in live_packet_reader.iter_live_packets([latest], max_lines=max_lines, ignore_partial_last_line=True):
        if result.error:
            malformed += 1
            continue
        packet = result.record
        if not isinstance(packet, dict):
            malformed += 1
            continue
        parsed += 1
        packet_type = str(packet.get("packetType") or "unknown")
        counts[packet_type] = counts.get(packet_type, 0) + 1
        tick = packet.get("tick")
        sequence = packet.get("sequence")
        if isinstance(tick, int):
            latest_tick = tick if latest_tick is None else max(latest_tick, tick)
        if isinstance(sequence, int):
            latest_sequence = sequence if latest_sequence is None else max(latest_sequence, sequence)
        if packet_type == "live_navigation_packet.v1":
            latest_navigation_tick = tick if isinstance(tick, int) else latest_navigation_tick
            payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
            collision = payload.get("collision") if isinstance(payload.get("collision"), dict) else {}
            if collision.get("collisionKnown") is not None:
                collision_known = collision.get("collisionKnown")
        if packet_type == "live_collision_window_packet.v1":
            latest_collision_window_tick = tick if isinstance(tick, int) else latest_collision_window_tick
            payload = packet.get("payload") if isinstance(packet.get("payload"), dict) else {}
            collision_window_available = bool(payload.get("flags"))
            collision_window_radius = payload.get("windowRadius")
            collision_window_width = payload.get("width")
            collision_window_height = payload.get("height")
            collision_window_tile_count = payload.get("collisionWindowTileCount")
            collision_window_hash = payload.get("collisionWindowHash") or payload.get("windowHash")

    return {
        "latestSegment": str(latest),
        "latestSegmentExists": latest.exists(),
        "latestSegmentAgeSeconds": file_age_seconds(latest),
        "parsedPackets": parsed,
        "malformedLines": malformed,
        "packetCountsByType": counts,
        "latestTick": latest_tick,
        "latestSequence": latest_sequence,
        "latestNavigationTick": latest_navigation_tick,
        "collisionKnown": collision_known,
        "latestCollisionWindowTick": latest_collision_window_tick,
        "collisionWindowAvailable": collision_window_available,
        "collisionWindowRadius": collision_window_radius,
        "collisionWindowWidth": collision_window_width,
        "collisionWindowHeight": collision_window_height,
        "collisionWindowTileCount": collision_window_tile_count,
        "collisionWindowHash": collision_window_hash,
    }


def check_live_setup(
    session: Path | None,
    *,
    require_compact_packets: bool = False,
    plugin_snapshot_host: str = "127.0.0.1",
    plugin_snapshot_port: int = 8893,
    plugin_snapshot_timeout: float = 0.2,
) -> dict:
    checks = []
    warnings = []
    failures = []
    session_exists = bool(session and session.exists())

    def add_check(name: str, ok: bool, message: str, *, required: bool = False) -> None:
        status = "PASS" if ok else "FAIL" if required else "WARN"
        checks.append({"name": name, "status": status, "message": message})
        if not ok and required:
            failures.append(message)
        elif not ok:
            warnings.append(message)

    add_check("session exists", session_exists, f"session path: {session}", required=True)
    if not session_exists:
        return {
            "schema": SCHEMA,
            "generatedAtUtc": utc_now(),
            "status": "FAIL",
            "sessionPath": str(session) if session else None,
            "checks": checks,
            "warnings": warnings,
            "failures": failures,
            "recommendedNextCommand": "python telemetry-viewer\\live_target_processor.py --latest-session --input-source auto --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark",
        }

    assert session is not None
    live_dir = live_packet_reader.live_packet_dir(session)
    index_path = live_packet_reader.live_packet_index_path(session)
    pointer_path = live_packet_reader.latest_segment_pointer_path(session)
    index = live_packet_reader.read_index(session)
    latest = live_packet_reader.latest_segment_path(session, index=index)
    latest_parse = parse_latest_segment(session)
    latest_age = latest_parse.get("latestSegmentAgeSeconds")
    compact_available = bool(live_dir.exists() and index_path.exists() and pointer_path.exists() and latest is not None and latest.exists())
    compact_recent = bool(compact_available and isinstance(latest_age, (int, float)) and latest_age <= RECENT_SECONDS)
    raw_ticks = list_tick_files(session)
    live_output_dir = session / "interaction_geometry" / "live"
    live_status_path = live_output_dir / "live_status.json"
    manifest_path = session / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    try:
        live_status = json.loads(live_status_path.read_text(encoding="utf-8")) if live_status_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        live_status = {}
    if not isinstance(live_status, dict):
        live_status = {}
    recording_mode = manifest.get("recordingMode")
    raw_tick_recording_enabled = manifest.get("rawTickRecordingEnabled")
    raw_event_recording_enabled = manifest.get("rawEventRecordingEnabled")
    frame_recording_enabled = manifest.get("frameRecordingEnabled")
    compact_packet_recording_enabled = first_present(
        live_status.get("compactPacketRecordingEnabled"),
        live_status.get("compactLiveEnabled"),
        manifest.get("compactPacketRecordingEnabled"),
        manifest.get("compactLivePacketsEnabled"),
    )
    compact_live_packet_files_enabled = first_present(
        live_status.get("compactLivePacketFilesEnabled"),
        manifest.get("compactLivePacketFilesEnabled"),
    )
    compact_live_stream_enabled = first_present(
        live_status.get("compactLiveStreamEnabled"),
        manifest.get("compactLiveStreamEnabled"),
    )
    compact_live_stream_also_write_files = first_present(
        live_status.get("compactLiveStreamAlsoWriteFiles"),
        manifest.get("compactLiveStreamAlsoWriteFiles"),
    )
    snapshot_check = plugin_snapshot_check(plugin_snapshot_host, plugin_snapshot_port, plugin_snapshot_timeout)
    stream_only_file_warning = ""
    if require_compact_packets and latest is None and (
        compact_live_stream_enabled is True
        or compact_live_packet_files_enabled is False
        or compact_live_stream_also_write_files is False
    ):
        stream_only_file_warning = (
            "Compact packet files are required but no latest segment exists. "
            "If using stream mode, enable 'Stream also writes files' or switch input source to compact-stream experimental."
        )

    add_check("compact live packet directory", live_dir.exists(), f"directory: {live_dir}", required=require_compact_packets)
    add_check("live_packet_index.json", index_path.exists() and isinstance(index, dict), f"index: {index_path}", required=require_compact_packets)
    add_check("latest_segment.txt", pointer_path.exists(), f"pointer: {pointer_path}", required=require_compact_packets)
    add_check("latest segment exists", latest is not None and latest.exists(), f"latest segment: {latest}", required=require_compact_packets)
    if stream_only_file_warning:
        add_check("stream file mirror", False, stream_only_file_warning)
    add_check("latest segment parses", int(latest_parse.get("parsedPackets") or 0) > 0, "latest segment has parseable packets", required=require_compact_packets)
    add_check("compact packets recent", compact_recent, f"latest segment age seconds: {latest_age}", required=require_compact_packets)
    add_check("compact navigation packets present", int((latest_parse.get("packetCountsByType") or {}).get("live_navigation_packet.v1") or 0) > 0, f"latest navigation tick: {latest_parse.get('latestNavigationTick')}")
    add_check("collision summary known", latest_parse.get("collisionKnown") is True, f"collisionKnown: {latest_parse.get('collisionKnown')}")
    add_check("collision window packets present", int((latest_parse.get("packetCountsByType") or {}).get("live_collision_window_packet.v1") or 0) > 0, f"latest collision window tick: {latest_parse.get('latestCollisionWindowTick')}")
    add_check("collision window available", latest_parse.get("collisionWindowAvailable") is True, f"radius: {latest_parse.get('collisionWindowRadius')} size: {latest_parse.get('collisionWindowWidth')}x{latest_parse.get('collisionWindowHeight')}")
    add_check("normal live input available", compact_available or bool(raw_ticks), f"compact packets: {compact_available}; raw tick files: {len(raw_ticks)}")
    stream_missing_types = [
        str(packet_type)
        for packet_type in (
            live_status.get("compactStreamMissingRequiredTypesForLatestTick")
            or live_status.get("compactStreamKnownMissingTypes")
            or []
        )
    ]
    stream_missing_required = sorted(
        packet_type
        for packet_type in stream_missing_types
        if packet_type in {"live_baseline_packet.v1", "live_projection_packet.v1"}
    )
    if live_status.get("inputSourceActive") == "compact-stream" and stream_missing_required:
        add_check(
            "compact stream completeness",
            False,
            "compact-stream is experimental and missing required packet types "
            + ", ".join(stream_missing_required)
            + "; use --input-source compact-packets --require-compact-packets for stable live",
        )
    if snapshot_check.get("enabled"):
        health = snapshot_check.get("health") or {}
        snapshot = snapshot_check.get("snapshot") or {}
        add_check(
            "plugin snapshot endpoint",
            health.get("status") in {"PASS", "WARN"} and snapshot.get("schema") == "plugin_snapshot_response.v1",
            "experimental plugin snapshot endpoint "
            f"health={health.get('status')} latestTick={health.get('latestTick')} "
            f"snapshot={snapshot.get('status')}",
        )
    if recording_mode == "LIVE_COMPACT_ONLY" and not raw_ticks:
        checks.append({
            "name": "raw debug ticks optional",
            "status": "PASS",
            "message": "raw tick recording is disabled for compact-only live mode",
        })
    add_check("rolling live output directory", live_output_dir.exists() or compact_available, f"live output: {live_output_dir}")

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    recommended = (
        "python telemetry-viewer\\live_target_processor.py --latest-session --input-source compact-packets --require-compact-packets --profile woodcutting --follow --latency-mode realtime --liveness-mode delta --liveness-budget-ms 20 --no-startup-backfill --max-new-ticks-per-update 1 --candidate-output-window latest --window-ticks 10 --limit 100 --no-ui-targets --emit-world-targets candidates --drain-backlog-on-overrun --summary --benchmark"
        if compact_available
        else "python telemetry-viewer\\inspect_live_packets.py --latest-session --summary"
    )

    return {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "status": status,
        "sessionPath": str(session),
        "compactPacketsAvailable": compact_available,
        "compactPacketsRecent": compact_recent,
        "compactPacketIndexPath": str(index_path),
        "compactPacketLatestSegment": str(latest) if latest else None,
        "compactPacketLatestTick": (index or {}).get("latestTick") if isinstance(index, dict) else latest_parse.get("latestTick"),
        "compactPacketLatestSequence": (index or {}).get("latestSequence") if isinstance(index, dict) else latest_parse.get("latestSequence"),
        "latestSegmentParsedPackets": latest_parse.get("parsedPackets"),
        "latestSegmentMalformedLines": latest_parse.get("malformedLines"),
        "packetCountsByType": latest_parse.get("packetCountsByType"),
        "navigationPacketsPresent": int((latest_parse.get("packetCountsByType") or {}).get("live_navigation_packet.v1") or 0) > 0,
        "latestNavigationTick": latest_parse.get("latestNavigationTick"),
        "collisionKnown": latest_parse.get("collisionKnown"),
        "collisionWindowPacketsPresent": int((latest_parse.get("packetCountsByType") or {}).get("live_collision_window_packet.v1") or 0) > 0,
        "latestCollisionWindowTick": latest_parse.get("latestCollisionWindowTick"),
        "collisionWindowAvailable": latest_parse.get("collisionWindowAvailable"),
        "collisionWindowRadius": latest_parse.get("collisionWindowRadius"),
        "collisionWindowDimensions": {
            "width": latest_parse.get("collisionWindowWidth"),
            "height": latest_parse.get("collisionWindowHeight"),
        },
        "collisionWindowTileCount": latest_parse.get("collisionWindowTileCount"),
        "collisionWindowHash": latest_parse.get("collisionWindowHash"),
        "rawTicksAvailable": bool(raw_ticks),
        "rawTickFileCount": len(raw_ticks),
        "recordingMode": recording_mode,
        "rawTickRecordingEnabled": raw_tick_recording_enabled,
        "rawEventRecordingEnabled": raw_event_recording_enabled,
        "frameRecordingEnabled": frame_recording_enabled,
        "compactPacketRecordingEnabled": compact_packet_recording_enabled,
        "compactLivePacketFilesEnabled": compact_live_packet_files_enabled,
        "compactLiveStreamEnabled": compact_live_stream_enabled,
        "compactLiveStreamAlsoWriteFiles": compact_live_stream_also_write_files,
        "streamOnlyFileWarning": stream_only_file_warning,
        "liveOutputExists": live_output_dir.exists(),
        "activeInputSource": live_status.get("inputSourceActive"),
        "streamMissingRequiredTypes": stream_missing_required,
        "pluginSnapshotExperimental": True,
        "pluginSnapshotEndpointEnabled": bool(snapshot_check.get("enabled")),
        "pluginSnapshotHealthStatus": (snapshot_check.get("health") or {}).get("status") if snapshot_check.get("health") else None,
        "pluginSnapshotLatestTick": (snapshot_check.get("health") or {}).get("latestTick") if snapshot_check.get("health") else None,
        "pluginSnapshotCachedPacketTypes": (snapshot_check.get("health") or {}).get("cachedPacketTypes") if snapshot_check.get("health") else [],
        "pluginSnapshotHealthError": snapshot_check.get("healthError"),
        "pluginSnapshotBasicSnapshotStatus": (snapshot_check.get("snapshot") or {}).get("status") if snapshot_check.get("snapshot") else None,
        "pluginSnapshotBasicSnapshotWarnings": (snapshot_check.get("snapshot") or {}).get("warnings") if snapshot_check.get("snapshot") else [],
        "pluginSnapshotBasicSnapshotMissingCapabilities": (snapshot_check.get("snapshot") or {}).get("missingCapabilities") if snapshot_check.get("snapshot") else [],
        "pluginSnapshotSnapshotError": snapshot_check.get("snapshotError"),
        "stableLiveInputRecommendation": "--input-source compact-packets --require-compact-packets",
        "retention": {
            "compactLiveRetentionBytes": (index or {}).get("retentionBytes") if isinstance(index, dict) else None,
            "compactLiveRetentionSegments": (index or {}).get("retentionSegments") if isinstance(index, dict) else None,
            "compactLiveRetentionTicks": (index or {}).get("retentionTicks") if isinstance(index, dict) else None,
        },
        "diskUsage": {
            "livePackets": size_payload(live_dir),
            "ticks": size_payload(session / "ticks"),
            "frames": size_payload(session / "frames"),
            "rollingLive": size_payload(live_output_dir),
        },
        "checks": checks,
        "warnings": warnings,
        "failures": failures,
        "recommendedNextCommand": recommended,
        "notes": [
            "Compact packets are the default live input path.",
            "compact-stream is experimental; use compact-packets for stable normal live.",
            "Raw ticks are optional and are normally only present in DEBUG_RECORDING or explicit debug modes.",
            "Batch/debug builders require DEBUG_RECORDING mode when they need raw tick JSONL.",
        ],
    }


def print_human(payload: dict) -> None:
    print(f"Live setup check: {payload.get('status')}")
    print(f"session: {payload.get('sessionPath')}")
    print(f"compact packets available: {payload.get('compactPacketsAvailable')}")
    print(f"compact packets recent: {payload.get('compactPacketsRecent')}")
    print(f"latest segment: {payload.get('compactPacketLatestSegment')}")
    print(f"latest tick: {payload.get('compactPacketLatestTick')} sequence: {payload.get('compactPacketLatestSequence')}")
    print(
        "recording mode: "
        f"{payload.get('recordingMode') or 'unknown'} "
        f"rawTicks={payload.get('rawTickRecordingEnabled')} "
        f"rawEvents={payload.get('rawEventRecordingEnabled')} "
        f"frames={payload.get('frameRecordingEnabled')}"
    )
    print(f"latest navigation tick: {payload.get('latestNavigationTick')} collisionKnown={payload.get('collisionKnown')}")
    dimensions = payload.get("collisionWindowDimensions") or {}
    print(
        "latest collision window tick: "
        f"{payload.get('latestCollisionWindowTick')} "
        f"available={payload.get('collisionWindowAvailable')} "
        f"radius={payload.get('collisionWindowRadius')} "
        f"size={dimensions.get('width')}x{dimensions.get('height')}"
    )
    print(f"raw debug tick files present: {payload.get('rawTicksAvailable')}")
    if payload.get("activeInputSource"):
        print(f"active live processor input: {payload.get('activeInputSource')}")
    if payload.get("streamMissingRequiredTypes"):
        print("stream warning: compact-stream is incomplete; switch to compact-packets.")
    if payload.get("streamOnlyFileWarning"):
        print(f"file bridge warning: {payload.get('streamOnlyFileWarning')}")
    if payload.get("pluginSnapshotEndpointEnabled"):
        print(
            "plugin snapshot endpoint (experimental): "
            f"health={payload.get('pluginSnapshotHealthStatus')} "
            f"latestTick={payload.get('pluginSnapshotLatestTick')} "
            f"snapshot={payload.get('pluginSnapshotBasicSnapshotStatus')}"
        )
    for check in payload.get("checks") or []:
        print(f"{check.get('status'):4} {check.get('name')}: {check.get('message')}")
    if not payload.get("compactPacketsAvailable"):
        print("Compact packets are missing. Enable compact live packets in RuneLite telemetry config and collect a fresh session.")
        print("Inspect command: python telemetry-viewer\\inspect_live_packets.py --latest-session --summary")
    print(f"recommended next command: {payload.get('recommendedNextCommand')}")


def parse_args():
    parser = argparse.ArgumentParser(description="Check whether the read-only compact-packet live path is ready.")
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session.")
    parser.add_argument("--require-compact-packets", action="store_true", help="Return FAIL if compact packets are missing or stale.")
    parser.add_argument("--plugin-snapshot-host", default="127.0.0.1", help="Experimental plugin snapshot endpoint host to probe. Default: 127.0.0.1.")
    parser.add_argument("--plugin-snapshot-port", type=int, default=8893, help="Experimental plugin snapshot endpoint port to probe. Default: 8893.")
    parser.add_argument("--plugin-snapshot-timeout", type=float, default=0.2, help="Experimental plugin snapshot probe timeout seconds. Default: 0.2.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session = resolve_session(args)
    payload = check_live_setup(
        session,
        require_compact_packets=args.require_compact_packets,
        plugin_snapshot_host=args.plugin_snapshot_host,
        plugin_snapshot_port=args.plugin_snapshot_port,
        plugin_snapshot_timeout=args.plugin_snapshot_timeout,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print_human(payload)
    return 0 if payload.get("status") in {"PASS", "WARN"} and not args.require_compact_packets else 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
