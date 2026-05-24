from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


LIVE_DIR_RELATIVE = Path("interaction_geometry") / "live"
LIVE_OUTPUT_FILES = {
    "baseline": "live_baseline_state.json",
    "contextIndex": "live_context_index.json",
    "candidates": "live_candidates.jsonl",
    "status": "live_status.json",
    "activity": "live_activity_state.json",
    "navigation": "live_navigation_summary.json",
    "overlayDebug": "overlay_debug_state.json",
    "lastActionTrace": "last_action_trace.json",
}


def live_dir(session: Path | None) -> Path | None:
    return session / LIVE_DIR_RELATIVE if session else None


def live_output_path(session: Path | None, name: str) -> Path | None:
    root = live_dir(session)
    filename = LIVE_OUTPUT_FILES.get(name)
    return root / filename if root and filename else None


def path_text(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    records.append(decoded)
    except OSError:
        return []
    return records


def file_age_seconds(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def overlay_targets(overlay: dict[str, Any]) -> list[dict[str, Any]]:
    intent = _dict(overlay.get("intentState"))
    markers = _list(intent.get("markers")) or _list(overlay.get("markers")) or _list(overlay.get("targets"))
    return [item for item in markers if isinstance(item, dict)]


def load_live_files(session: Path | None) -> dict[str, Any]:
    paths = {
        "liveDir": live_dir(session),
        "baseline": live_output_path(session, "baseline"),
        "contextIndex": live_output_path(session, "contextIndex"),
        "candidates": live_output_path(session, "candidates"),
        "status": live_output_path(session, "status"),
        "activity": live_output_path(session, "activity"),
        "navigation": live_output_path(session, "navigation"),
        "overlayDebug": live_output_path(session, "overlayDebug"),
        "lastActionTrace": live_output_path(session, "lastActionTrace"),
    }
    overlay = read_json(paths["overlayDebug"])
    candidates = read_jsonl(paths["candidates"])
    missing = [
        name
        for name in ("overlayDebug", "candidates")
        for path in [paths.get(name)]
        if path is not None and not path.exists()
    ]
    return {
        "paths": paths,
        "baseline": read_json(paths["baseline"]),
        "contextIndex": read_json(paths["contextIndex"]),
        "liveCandidates": candidates,
        "status": read_json(paths["status"]),
        "activity": read_json(paths["activity"]),
        "navigation": read_json(paths["navigation"]),
        "overlayDebug": overlay,
        "overlayTargets": overlay_targets(overlay),
        "lastActionTrace": read_json(paths["lastActionTrace"]),
        "missing": missing,
    }
