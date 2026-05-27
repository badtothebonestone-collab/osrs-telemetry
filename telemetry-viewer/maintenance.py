from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_REPORT = "legacy_live_packets_report.v1"
SCHEMA_PRUNE = "legacy_live_packets_prune.v1"
DEFAULT_ROOT = Path.home() / ".osrs-telemetry" / "sessions"


@dataclass
class LegacyFile:
    path: Path
    size_bytes: int
    mtime: float


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _sessions_root(value: str | None) -> Path:
    root = Path(value).expanduser() if value else DEFAULT_ROOT
    resolved = root.resolve()
    default_sessions = DEFAULT_ROOT.resolve()
    if resolved != default_sessions and not _is_under(resolved, default_sessions):
        raise RuntimeError(f"legacy live packet cleanup is limited to {default_sessions}")
    return resolved


def _legacy_file(path: Path, sessions_root: Path) -> bool:
    if not path.is_file() or not _is_under(path, sessions_root):
        return False
    name = path.name.lower()
    in_live_packets = any(part.lower() == "live_packets" for part in path.parts)
    live_segment = name.startswith("live-") and (name.endswith(".ndjson") or name.endswith(".jsonl"))
    return in_live_packets and live_segment


def _find_legacy_files(sessions_root: Path) -> list[LegacyFile]:
    files: list[LegacyFile] = []
    if not sessions_root.exists():
        return files
    for path in sessions_root.rglob("*"):
        if not _legacy_file(path, sessions_root):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(LegacyFile(path=path, size_bytes=int(stat.st_size), mtime=float(stat.st_mtime)))
    files.sort(key=lambda item: item.size_bytes, reverse=True)
    return files


def _bytes_mb(size: int | float) -> float:
    return round(float(size) / (1024 * 1024), 3)


def _root_total_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return total
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            total += int(path.stat().st_size)
        except OSError:
            pass
    return total


def _file_summary(files: list[LegacyFile], limit: int) -> list[dict[str, Any]]:
    return [
        {
            "path": str(item.path),
            "sizeBytes": item.size_bytes,
            "sizeMb": _bytes_mb(item.size_bytes),
            "mtime": item.mtime,
        }
        for item in files[: max(0, limit)]
    ]


def _open_file_hint(path: Path) -> bool | None:
    if os.name != "nt":
        return None
    try:
        with path.open("ab"):
            return False
    except PermissionError:
        return True
    except OSError:
        return None


def live_packets_report(sessions_root: Path, *, top: int = 30) -> dict[str, Any]:
    files = _find_legacy_files(sessions_root)
    total = sum(item.size_bytes for item in files)
    open_hints = []
    for item in files[: max(0, min(top, 30))]:
        hint = _open_file_hint(item.path)
        if hint is not None:
            open_hints.append({"path": str(item.path), "possiblyOpen": hint})
    telemetry_root = sessions_root.parent
    return {
        "schema": SCHEMA_REPORT,
        "status": "WARN" if files else "PASS",
        "sessionsRoot": str(sessions_root),
        "telemetryRoot": str(telemetry_root),
        "telemetryRootTotalBytes": _root_total_bytes(telemetry_root),
        "telemetryRootTotalMb": _bytes_mb(_root_total_bytes(telemetry_root)),
        "legacyLivePacketFilesPresent": bool(files),
        "legacyLivePacketFileCount": len(files),
        "legacyLivePacketTotalBytes": total,
        "legacyLivePacketTotalMb": _bytes_mb(total),
        "cleanupRecommended": bool(files),
        "livePacketsRuntimeRemoved": True,
        "ndjsonRuntimeRemoved": True,
        "jsonlRuntimeRemoved": True,
        "livePacketWriterActive": False,
        "topFiles": _file_summary(files, top),
        "openFileHints": open_hints,
        "dryRunCommand": "python telemetry-viewer\\maintenance.py --prune-legacy-live-packets --dry-run",
        "applyCommand": "python telemetry-viewer\\maintenance.py --prune-legacy-live-packets --apply",
    }


def prune_legacy_live_packets(sessions_root: Path, *, apply: bool, top: int = 30) -> dict[str, Any]:
    files = _find_legacy_files(sessions_root)
    planned_bytes = sum(item.size_bytes for item in files)
    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if apply:
        for item in files:
            try:
                if not _legacy_file(item.path, sessions_root):
                    raise RuntimeError("refused non-legacy path")
                item.path.unlink()
                deleted.append({"path": str(item.path), "sizeBytes": item.size_bytes, "sizeMb": _bytes_mb(item.size_bytes)})
            except (OSError, RuntimeError) as error:
                errors.append({"path": str(item.path), "error": str(error)})
    return {
        "schema": SCHEMA_PRUNE,
        "status": "FAIL" if errors else "PASS",
        "sessionsRoot": str(sessions_root),
        "dryRun": not apply,
        "apply": apply,
        "candidateCount": len(files),
        "reclaimableBytes": planned_bytes,
        "reclaimableMb": _bytes_mb(planned_bytes),
        "deletedCount": len(deleted),
        "deletedBytes": sum(item["sizeBytes"] for item in deleted),
        "deletedMb": _bytes_mb(sum(item["sizeBytes"] for item in deleted)),
        "deleted": deleted[:top],
        "errors": errors,
        "topCandidates": _file_summary(files, top),
        "livePacketsRuntimeRemoved": True,
        "ndjsonRuntimeRemoved": True,
        "jsonlRuntimeRemoved": True,
        "livePacketWriterActive": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintenance-only report/prune tooling for retired legacy live packet archives.")
    parser.add_argument("--sessions-root", help="Sessions root. Defaults to %USERPROFILE%\\.osrs-telemetry\\sessions.")
    parser.add_argument("--live-packets-report", action="store_true", help="Report legacy live_packets/live-*.ndjson files.")
    parser.add_argument("--prune-legacy-live-packets", action="store_true", help="Dry-run or apply deletion of legacy live packet files.")
    parser.add_argument("--dry-run", action="store_true", help="Report prune candidates without deleting. This is the default.")
    parser.add_argument("--apply", action="store_true", help="Actually delete only legacy live packet files under the sessions root.")
    parser.add_argument("--top", type=int, default=30, help="Number of largest files to include. Default: 30.")
    args = parser.parse_args(argv)
    if not args.live_packets_report and not args.prune_legacy_live_packets:
        args.live_packets_report = True
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sessions_root = _sessions_root(args.sessions_root)
        if args.prune_legacy_live_packets:
            payload = prune_legacy_live_packets(sessions_root, apply=bool(args.apply), top=args.top)
        else:
            payload = live_packets_report(sessions_root, top=args.top)
    except RuntimeError as error:
        payload = {"schema": "legacy_live_packets_maintenance_error.v1", "status": "FAIL", "error": str(error)}
    print(json.dumps(payload, indent=2, sort_keys=False))
    return 0 if payload.get("status") in {"PASS", "WARN"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
