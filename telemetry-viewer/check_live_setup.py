from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_paths import directory_size, find_newest_session, get_sessions_dir, list_tick_files


SCHEMA = "live_setup_check.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_session(args: argparse.Namespace) -> Path | None:
    if args.session:
        return Path(args.session).expanduser().resolve()
    if args.latest_session:
        newest = find_newest_session(get_sessions_dir(args.sessions_dir))
        return newest.resolve() if newest else None
    return None


def size_payload(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": directory_size(path),
    }


def legacy_live_packet_report(session: Path | None) -> dict[str, Any]:
    packet_dir = session / "live_packets" if session else None
    files: list[Path] = []
    if packet_dir and packet_dir.exists():
        files = sorted(list(packet_dir.glob("live-*.ndjson")) + list(packet_dir.glob("live-*.jsonl")))
    total_bytes = 0
    newest_age = None
    now = time.time()
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        total_bytes += int(stat.st_size)
        age = max(0.0, now - stat.st_mtime)
        newest_age = age if newest_age is None else min(newest_age, age)
    return {
        "runtimeRemoved": True,
        "writerActive": False,
        "legacyLivePacketFilesPresent": bool(files),
        "legacyLivePacketFileCount": len(files),
        "legacyLivePacketTotalBytes": total_bytes,
        "legacyLivePacketTotalMb": round(total_bytes / (1024 * 1024), 3),
        "newestLegacyLivePacketAgeSeconds": newest_age,
        "path": str(packet_dir) if packet_dir else None,
        "cleanupRecommended": bool(files),
    }


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


def plugin_snapshot_check(host: str = "127.0.0.1", port: int = 8893, timeout: float = 0.2) -> dict[str, Any]:
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
        "needs": [
            "baseline",
            "projection",
            "inventory",
            "navigation",
            "collision_window",
            "writer_health",
            "world_model_summary",
        ],
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


def check_live_setup(
    session: Path | None,
    *,
    plugin_snapshot_host: str = "127.0.0.1",
    plugin_snapshot_port: int = 8893,
    plugin_snapshot_timeout: float = 0.2,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    warnings: list[str] = []
    failures: list[str] = []
    session_exists = bool(session and session.exists())

    def add_check(name: str, ok: bool, message: str, *, required: bool = False) -> None:
        status = "PASS" if ok else "FAIL" if required else "WARN"
        checks.append({"name": name, "status": status, "message": message})
        if not ok and required:
            failures.append(message)
        elif not ok:
            warnings.append(message)

    add_check("session exists", session_exists, f"session path: {session}", required=True)
    legacy_report = legacy_live_packet_report(session)
    snapshot_check = plugin_snapshot_check(plugin_snapshot_host, plugin_snapshot_port, plugin_snapshot_timeout)
    health = snapshot_check.get("health") or {}
    snapshot = snapshot_check.get("snapshot") or {}
    writer_health = snapshot.get("writerHealth") if isinstance(snapshot.get("writerHealth"), dict) else {}
    world_model = snapshot.get("worldModelSummary") if isinstance(snapshot.get("worldModelSummary"), dict) else {}
    raw_ticks = list_tick_files(session) if session and session.exists() else []

    if session_exists:
        add_check(
            "plugin snapshot endpoint",
            health.get("status") in {"PASS", "WARN"} and snapshot.get("schema") == "plugin_snapshot_response.v1",
            f"health={health.get('status')} latestTick={health.get('latestTick')} snapshot={snapshot.get('status')}",
            required=False,
        )
        add_check(
            "live packet runtime removed",
            writer_health.get("livePacketWriterActive") is False or not writer_health,
            "livePacketsRuntimeRemoved=true and livePacketWriterActive=false are expected",
        )
        if legacy_report.get("legacyLivePacketFilesPresent"):
            warnings.append(
                f"legacy live packet files remain on disk ({legacy_report.get('legacyLivePacketTotalMb')} MB); use maintenance.py dry-run/apply cleanup"
            )

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    recommended = "python telemetry-viewer\\context_service.py --query current-debug-context"
    return {
        "schema": SCHEMA,
        "generatedAtUtc": utc_now(),
        "status": status,
        "sessionPath": str(session) if session else None,
        "livePacketsRuntimeRemoved": True,
        "ndjsonRuntimeRemoved": True,
        "jsonlRuntimeRemoved": True,
        "livePacketWriterActive": False,
        "legacyLivePackets": legacy_report,
        "rawTicksAvailable": bool(raw_ticks),
        "rawTickFileCount": len(raw_ticks),
        "pluginSnapshotEndpointEnabled": bool(snapshot_check.get("enabled")),
        "pluginSnapshotHealthStatus": health.get("status") if isinstance(health, dict) else None,
        "pluginSnapshotLatestTick": health.get("latestTick") if isinstance(health, dict) else None,
        "pluginSnapshotBasicSnapshotStatus": snapshot.get("status") if isinstance(snapshot, dict) else None,
        "pluginSnapshotBasicSnapshotWarnings": snapshot.get("warnings") if isinstance(snapshot, dict) else [],
        "worldModelAvailable": world_model.get("worldModelAvailable") if isinstance(world_model, dict) else None,
        "worldModelObjectCount": world_model.get("objectCount") if isinstance(world_model, dict) else None,
        "diskUsage": {
            "legacyLivePackets": size_payload(Path(legacy_report["path"])) if legacy_report.get("path") else {},
            "ticks": size_payload(session / "ticks") if session else {},
            "frames": size_payload(session / "frames") if session else {},
            "rollingLive": size_payload(session / "interaction_geometry" / "live") if session else {},
        },
        "checks": checks,
        "warnings": warnings,
        "failures": failures,
        "recommendedNextCommand": recommended,
        "notes": [
            "The live packet NDJSON/JSONL archive is retired and cannot be used as live truth.",
            "Runtime truth comes from PluginSnapshotEndpoint, WorldModelCache, the daemon/context service, and Knowledge Fabric queries.",
            "Old live_packets files are legacy disk cleanup only.",
            "Explicit bounded JSON debug/replay/context artifacts remain intentional.",
        ],
    }


def print_human(payload: dict[str, Any]) -> None:
    legacy = payload.get("legacyLivePackets") or {}
    print(f"Live setup check: {payload.get('status')}")
    print(f"session: {payload.get('sessionPath')}")
    print("live packet archive: retired")
    print(f"legacy live packet files: {legacy.get('legacyLivePacketFileCount', 0)} ({legacy.get('legacyLivePacketTotalMb', 0)} MB)")
    print(
        "plugin snapshot endpoint: "
        f"health={payload.get('pluginSnapshotHealthStatus')} latestTick={payload.get('pluginSnapshotLatestTick')} "
        f"snapshot={payload.get('pluginSnapshotBasicSnapshotStatus')}"
    )
    print(f"world model: available={payload.get('worldModelAvailable')} objects={payload.get('worldModelObjectCount')}")
    for check in payload.get("checks") or []:
        print(f"{check.get('status'):4} {check.get('name')}: {check.get('message')}")
    for warning in payload.get("warnings") or []:
        print(f"WARN {warning}")
    print(f"recommended next command: {payload.get('recommendedNextCommand')}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether the current read-only live path is ready.")
    parser.add_argument("--session", help="Explicit telemetry session directory.")
    parser.add_argument("--sessions-dir", help="Override telemetry sessions directory when --latest-session is used.")
    parser.add_argument("--latest-session", action="store_true", help="Use the newest available session.")
    parser.add_argument("--plugin-snapshot-host", default="127.0.0.1", help="Plugin snapshot endpoint host to probe. Default: 127.0.0.1.")
    parser.add_argument("--plugin-snapshot-port", type=int, default=8893, help="Plugin snapshot endpoint port to probe. Default: 8893.")
    parser.add_argument("--plugin-snapshot-timeout", type=float, default=0.2, help="Plugin snapshot probe timeout seconds. Default: 0.2.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    session = resolve_session(args)
    payload = check_live_setup(
        session,
        plugin_snapshot_host=args.plugin_snapshot_host,
        plugin_snapshot_port=args.plugin_snapshot_port,
        plugin_snapshot_timeout=args.plugin_snapshot_timeout,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=False))
    else:
        print_human(payload)
    return 0 if payload.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
