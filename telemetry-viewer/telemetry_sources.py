from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from telemetry_paths import find_newest_live_session, find_newest_session


SOURCES_SCHEMA_VERSION = "telemetry_sources.v1"
SOURCE_READ_SCHEMA_VERSION = "telemetry_source_read.v1"
DEFAULT_MAX_BYTES = 1_000_000
STALE_AFTER_SECONDS = 5.0
DEFAULT_PLUGIN_SNAPSHOT_URL = "http://127.0.0.1:8893/snapshot"
DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS = 0.2
PLUGIN_SNAPSHOT_TOKEN_ENV = ("OSRS_PLUGIN_SNAPSHOT_TOKEN", "PLUGIN_SNAPSHOT_TOKEN")

LIVE_SOURCE_FILES: dict[str, str] = {
    "baseline": "live_baseline_state.json",
    "context": "live_context_index.json",
    "candidates": "live_candidates.jsonl",
    "status": "live_status.json",
    "activity": "live_activity_state.json",
    "events": "live_event_timeline.jsonl",
    "navigation": "live_navigation_summary.json",
    "watchValues": "live_watch_values.json",
    "overlayDebug": "overlay_debug_state.json",
    "lastActionTrace": "last_action_trace.json",
    "inputIntegrity": "input_integrity_status.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def path_text(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def live_dir_for_session(session: Path) -> Path:
    return session / "interaction_geometry" / "live"


def default_source_paths(session: str | Path | None = None, *, include_missing: bool = True) -> dict[str, Path]:
    if session is None:
        live_dir = Path("interaction_geometry") / "live"
    else:
        live_dir = live_dir_for_session(Path(session).expanduser())

    paths = {name: live_dir / filename for name, filename in LIVE_SOURCE_FILES.items()}
    if include_missing:
        return paths
    return {name: path for name, path in paths.items() if path.exists()}


def parse_sources_override(value: str | None) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if not value:
        return paths
    for index, item in enumerate(value.split(","), start=1):
        text = item.strip()
        if not text:
            continue
        if "=" in text:
            name, raw_path = text.split("=", 1)
            label = name.strip() or f"source{index}"
            path = Path(raw_path.strip()).expanduser()
        else:
            path = Path(text).expanduser()
            label = path.stem or f"source{index}"
        paths[label] = path
    return paths


def resolve_session(
    *,
    session: str | Path | None = None,
    latest_session: bool = False,
    latest_live: bool = True,
    sessions_dir: str | Path | None = None,
) -> Path | None:
    if session:
        return Path(session).expanduser()
    if latest_session:
        finder = find_newest_live_session if latest_live else find_newest_session
        found = finder(sessions_dir)
        return found.expanduser() if found else None
    return None


def discover_sources(
    *,
    session: str | Path | None = None,
    latest_session: bool = False,
    sources_override: str | None = None,
    sessions_dir: str | Path | None = None,
    include_missing: bool = True,
    plugin_snapshot_needs: list[str] | tuple[str, ...] | None = None,
    plugin_snapshot_url: str | None = None,
) -> dict[str, Any]:
    override = parse_sources_override(sources_override)
    resolved_session = resolve_session(
        session=session,
        latest_session=latest_session,
        latest_live=True,
        sessions_dir=sessions_dir,
    )
    if override:
        paths = override
        discovery = "explicit --sources override"
    else:
        paths = default_source_paths(resolved_session, include_missing=include_missing)
        discovery = (
            "session interaction_geometry/live defaults"
            if resolved_session
            else "repo interaction_geometry/live defaults"
        )
    endpoint_sources = [
        {
            "name": str(need),
            "kind": "plugin_snapshot",
            "url": plugin_snapshot_url or DEFAULT_PLUGIN_SNAPSHOT_URL,
            "method": "POST",
            "need": str(need),
        }
        for need in (plugin_snapshot_needs or [])
        if str(need or "").strip()
    ]
    sources = [{"name": name, "path": path_text(path), "kind": "file"} for name, path in paths.items()]
    sources.extend(endpoint_sources)
    return {
        "schema_version": SOURCES_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "session_path": path_text(resolved_session) if resolved_session else None,
        "discovery": discovery,
        "sources": sources,
        "endpoint_sources": endpoint_sources,
        "paths": paths,
    }


def source_signature(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "mtime_ns": None, "size_bytes": None}
    except PermissionError as error:
        return {"exists": False, "mtime_ns": None, "size_bytes": None, "error": f"PermissionError: {error}"}
    except OSError as error:
        return {"exists": False, "mtime_ns": None, "size_bytes": None, "error": f"{type(error).__name__}: {error}"}
    return {
        "exists": True,
        "mtime_ns": int(stat.st_mtime_ns),
        "size_bytes": int(stat.st_size),
    }


def _read_text_limited(path: Path, max_bytes: int) -> tuple[str, bool]:
    limit = max(1, int(max_bytes or DEFAULT_MAX_BYTES))
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit:
            handle.seek(max(0, size - limit))
            data = handle.read(limit)
            if b"\n" in data:
                data = data.split(b"\n", 1)[1]
            return data.decode("utf-8", errors="replace"), True
        return handle.read().decode("utf-8", errors="replace"), False


def _parse_jsonl(text: str) -> tuple[list[dict[str, Any]], int, list[str]]:
    records: list[dict[str, Any]] = []
    malformed = 0
    warnings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            warnings.append(f"line {line_number} was JSON but not an object")
    return records, malformed, warnings


def read_source(
    path: str | Path,
    *,
    name: str | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    include_raw: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    source_path = Path(path).expanduser()
    signature = source_signature(source_path)
    metadata: dict[str, Any] = {
        "schema_version": SOURCE_READ_SCHEMA_VERSION,
        "name": name or source_path.stem,
        "path": path_text(source_path),
        "exists": bool(signature.get("exists")),
        "size_bytes": signature.get("size_bytes"),
        "mtime_ns": signature.get("mtime_ns"),
        "modified_utc": None,
        "age_seconds": None,
        "stale": None,
        "parse_status": "missing",
        "read_error": signature.get("error"),
        "truncated": False,
        "record_count": None,
        "malformed_line_count": 0,
        "warnings": [],
        "data": None,
    }
    if not signature.get("exists"):
        return metadata

    try:
        stat = source_path.stat()
        metadata["modified_utc"] = iso_from_timestamp(stat.st_mtime)
        metadata["age_seconds"] = max(0.0, now - stat.st_mtime)
        metadata["stale"] = metadata["age_seconds"] > STALE_AFTER_SECONDS
        text, truncated = _read_text_limited(source_path, max_bytes)
        metadata["truncated"] = truncated
    except FileNotFoundError:
        metadata["parse_status"] = "missing"
        metadata["read_error"] = "FileNotFoundError: file disappeared during read"
        metadata["exists"] = False
        return metadata
    except PermissionError as error:
        metadata["parse_status"] = "permission_error"
        metadata["read_error"] = f"PermissionError: {error}"
        return metadata
    except OSError as error:
        metadata["parse_status"] = "read_error"
        metadata["read_error"] = f"{type(error).__name__}: {error}"
        return metadata

    suffix = source_path.suffix.lower()
    try:
        if suffix in {".jsonl", ".ndjson"}:
            records, malformed, warnings = _parse_jsonl(text)
            metadata["data"] = records
            metadata["record_count"] = len(records)
            metadata["malformed_line_count"] = malformed
            metadata["warnings"].extend(warnings)
            metadata["parse_status"] = "partial" if malformed else "ok"
        else:
            value = json.loads(text)
            metadata["data"] = value
            metadata["parse_status"] = "ok"
            if isinstance(value, list):
                metadata["record_count"] = len(value)
    except json.JSONDecodeError as error:
        metadata["parse_status"] = "json_error"
        metadata["read_error"] = f"JSONDecodeError: {error.msg}"
    except RuntimeError as error:
        metadata["parse_status"] = "parse_error"
        metadata["read_error"] = f"{type(error).__name__}: {error}"

    if include_raw:
        metadata["raw"] = text
    return metadata


def read_sources(
    sources: dict[str, Path],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    include_raw: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    return [
        read_source(path, name=name, max_bytes=max_bytes, include_raw=include_raw, now=now)
        for name, path in sources.items()
    ]


def _plugin_snapshot_token(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    for name in PLUGIN_SNAPSHOT_TOKEN_ENV:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _extract_need_payload(response: Any, need: str) -> Any:
    if not isinstance(response, dict):
        return None
    payloads = response.get("payloads")
    if isinstance(payloads, dict) and need in payloads:
        return payloads.get(need)
    if need in response:
        return response.get(need)
    payload = response.get("payload")
    if isinstance(payload, dict):
        nested_payloads = payload.get("payloads")
        if isinstance(nested_payloads, dict) and need in nested_payloads:
            return nested_payloads.get(need)
        if need in payload:
            return payload.get(need)
    return None


def read_plugin_snapshot_need(
    need: str,
    *,
    name: str | None = None,
    snapshot_url: str | None = None,
    timeout_seconds: float = DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS,
    auth_token: str | None = None,
    include_raw: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    source_name = name or need
    url = snapshot_url or DEFAULT_PLUGIN_SNAPSHOT_URL
    metadata: dict[str, Any] = {
        "schema_version": SOURCE_READ_SCHEMA_VERSION,
        "name": source_name,
        "source_kind": "plugin_snapshot",
        "url": url,
        "method": "POST",
        "need": need,
        "exists": False,
        "size_bytes": None,
        "mtime_ns": None,
        "modified_utc": None,
        "age_seconds": None,
        "stale": None,
        "parse_status": "missing",
        "read_error": None,
        "truncated": False,
        "record_count": None,
        "malformed_line_count": 0,
        "warnings": [],
        "data": None,
        "http_status": None,
        "latest_tick": None,
        "latest_export_sequence": None,
        "freshness": None,
    }
    request_payload = {
        "schema": "plugin_snapshot_request.v1",
        "requestId": f"manual_recorder_{source_name}_{int(now * 1000)}",
        "needs": [need],
        "snapshotTier": "hot",
        "includeGeometry": False,
        "includeCollisionWindow": False,
        "includeWatchValues": False,
    }
    body = json.dumps(request_payload, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    token = _plugin_snapshot_token(auth_token)
    if token:
        request.add_header("X-Plugin-Snapshot-Token", token)

    try:
        with urlopen(request, timeout=max(0.05, float(timeout_seconds or DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS))) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw_bytes = response.read(DEFAULT_MAX_BYTES + 1)
    except HTTPError as error:
        metadata["http_status"] = int(getattr(error, "code", 0) or 0)
        try:
            raw_bytes = error.read(DEFAULT_MAX_BYTES + 1)
            if raw_bytes and include_raw:
                metadata["raw"] = raw_bytes[:DEFAULT_MAX_BYTES].decode("utf-8", errors="replace")
        except OSError:
            pass
        metadata["parse_status"] = "http_error"
        metadata["read_error"] = f"HTTPError: {error.code}"
        return metadata
    except (TimeoutError, URLError, OSError) as error:
        metadata["parse_status"] = "connection_error"
        metadata["read_error"] = f"{type(error).__name__}: {error}"
        return metadata

    truncated = len(raw_bytes) > DEFAULT_MAX_BYTES
    text = raw_bytes[:DEFAULT_MAX_BYTES].decode("utf-8", errors="replace")
    metadata["http_status"] = status_code
    metadata["size_bytes"] = min(len(raw_bytes), DEFAULT_MAX_BYTES)
    metadata["truncated"] = truncated
    if include_raw:
        metadata["raw"] = text

    try:
        response_payload = json.loads(text)
    except json.JSONDecodeError as error:
        metadata["parse_status"] = "json_error"
        metadata["read_error"] = f"JSONDecodeError: {error.msg}"
        return metadata

    metadata["modified_utc"] = response_payload.get("generatedAtUtc") if isinstance(response_payload, dict) else utc_now()
    metadata["latest_tick"] = response_payload.get("latestTick") if isinstance(response_payload, dict) else None
    metadata["latest_export_sequence"] = response_payload.get("latestSequence") if isinstance(response_payload, dict) else None
    freshness = response_payload.get("freshness") if isinstance(response_payload, dict) else None
    metadata["freshness"] = freshness if isinstance(freshness, dict) else None
    age_ms = None
    if isinstance(freshness, dict):
        age_by_need = freshness.get("ageMillisByNeed")
        if isinstance(age_by_need, dict):
            age_ms = age_by_need.get(need)
        metadata["stale"] = not bool(freshness.get("fresh", True))
    if isinstance(age_ms, (int, float)):
        metadata["age_seconds"] = max(0.0, float(age_ms) / 1000.0)
        metadata["stale"] = metadata["age_seconds"] > STALE_AFTER_SECONDS

    if isinstance(response_payload, dict):
        metadata["warnings"] = [str(item) for item in response_payload.get("warnings") or []]
        missing = response_payload.get("missingCapabilities")
        if isinstance(missing, list) and need in missing:
            metadata["warnings"].append(f"missing capability: {need}")

    payload = _extract_need_payload(response_payload, need)
    if payload is None:
        metadata["parse_status"] = "missing"
        metadata["read_error"] = f"plugin snapshot response did not include {need}"
        return metadata

    metadata["exists"] = True
    metadata["parse_status"] = "ok"
    metadata["data"] = payload
    if isinstance(payload, list):
        metadata["record_count"] = len(payload)
    return metadata


def read_plugin_snapshot_needs(
    needs: list[str] | tuple[str, ...],
    *,
    snapshot_url: str | None = None,
    timeout_seconds: float = DEFAULT_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS,
    include_raw: bool = False,
    now: float | None = None,
) -> list[dict[str, Any]]:
    return [
        read_plugin_snapshot_need(
            str(need),
            snapshot_url=snapshot_url,
            timeout_seconds=timeout_seconds,
            include_raw=include_raw,
            now=now,
        )
        for need in needs
        if str(need or "").strip()
    ]


def parsed_payload_by_source(reads: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        str(read.get("name")): read.get("data")
        for read in reads
        if read.get("parse_status") in {"ok", "partial"} and read.get("data") is not None
    }


def newest_source_update(reads: list[dict[str, Any]]) -> str | None:
    modified = [read.get("modified_utc") for read in reads if read.get("modified_utc")]
    return max(modified) if modified else None


def source_freshness_summary(reads: list[dict[str, Any]]) -> dict[str, Any]:
    present = [read for read in reads if read.get("exists")]
    stale = [read for read in present if read.get("stale")]
    parse_warnings = [
        f"{read.get('name')}: {read.get('parse_status')}"
        for read in reads
        if read.get("parse_status") not in {"ok", "missing"} or read.get("read_error")
    ]
    latest_age = min((read.get("age_seconds") for read in present if isinstance(read.get("age_seconds"), (int, float))), default=None)
    return {
        "schema_version": "telemetry_source_freshness.v1",
        "file_count": len(reads),
        "source_count": len(reads),
        "present_count": len(present),
        "missing_count": len(reads) - len(present),
        "stale_count": len(stale),
        "latest_age_seconds": latest_age,
        "last_update_time": newest_source_update(reads),
        "parse_warnings": parse_warnings,
    }


def atomic_write_json(path: str | Path, payload: Any, *, pretty: bool = True) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        if pretty:
            json.dump(payload, handle, indent=2, sort_keys=False, default=str)
        else:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=False, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(target)
