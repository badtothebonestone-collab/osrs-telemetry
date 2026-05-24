from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from live_file_core import live_output_path
from telemetry_paths import find_newest_live_session, find_newest_session, get_sessions_dir


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def path_from_text(value: Any) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value).expanduser()
    return None


def same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def daemon_session_from_status(status: dict[str, Any]) -> Path | None:
    return path_from_text(status.get("sessionPath"))


def fetch_daemon_session(daemon_url: str, timeout: float = 3.0) -> Path | None:
    try:
        status = fetch_json(daemon_status_url(daemon_url), timeout=timeout)
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None
    return daemon_session_from_status(status)


def newest_sessions(sessions_dir: str | Path | None = None) -> dict[str, Path | None]:
    root = get_sessions_dir(sessions_dir)
    return {
        "root": root,
        "latest": find_newest_session(root),
        "latestLive": find_newest_live_session(root),
    }


def choose_highlighter_session(action_session: Path | None, latest_live_session: Path | None) -> Path | None:
    overlay_path = live_output_path(action_session, "overlayDebug")
    if action_session is not None and overlay_path is not None and overlay_path.exists():
        return action_session
    return latest_live_session


def resolve_session_for_args(args: Any) -> Path | None:
    if getattr(args, "session", None):
        return Path(getattr(args, "session")).expanduser()
    if getattr(args, "from_daemon", False):
        daemon_session = fetch_daemon_session(
            getattr(args, "daemon_url", "http://127.0.0.1:8890"),
            timeout=float(getattr(args, "daemon_timeout", getattr(args, "timeout", 3.0)) or 3.0),
        )
        if daemon_session is not None:
            return daemon_session
    root = get_sessions_dir(getattr(args, "sessions_dir", None))
    if getattr(args, "live", False):
        live_session = find_newest_live_session(root)
        if live_session is not None:
            return live_session
    return find_newest_session(root)
