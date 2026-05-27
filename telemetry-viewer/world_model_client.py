from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from typing import Any

import world_model_core


DEFAULT_NEEDS = list(world_model_core.WORLD_MODEL_NEEDS)


def normalize_snapshot_url(url: str) -> str:
    """Accept either the 8893 service base URL or the concrete /snapshot endpoint."""
    value = str(url or "").strip() or "http://127.0.0.1:8893/snapshot"
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return value
    path = parts.path.rstrip("/")
    if path.endswith("/snapshot"):
        return value
    if not path:
        path = "/snapshot"
    else:
        path = f"{path}/snapshot"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def build_request(
    *,
    needs: list[str] | None = None,
    max_objects: int = 160,
    radius_tiles: int = 48,
    include_projection: bool = False,
    include_collision: bool = False,
    center_world_location: dict[str, Any] | None = None,
    destination_world_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    world_model: dict[str, Any] = {
        "maxObjects": max(0, int(max_objects)),
        "radiusTiles": max(1, int(radius_tiles)),
        "includeProjection": bool(include_projection),
        "includeCollision": bool(include_collision),
    }
    if center_world_location:
        world_model["centerWorldLocation"] = dict(center_world_location)
    if destination_world_location:
        world_model["destinationWorldLocation"] = dict(destination_world_location)
    return {
        "schema": "plugin_snapshot_request.v1",
        "needs": list(needs or DEFAULT_NEEDS),
        "maxAgeTicks": 5,
        "responseMode": "compact",
        "worldModel": world_model,
    }


def fetch(
    url: str = "http://127.0.0.1:8893/snapshot",
    *,
    token: str = "",
    timeout: float = 0.75,
    request: dict[str, Any] | None = None,
    needs: list[str] | None = None,
) -> dict[str, Any]:
    body = json.dumps(request or build_request(needs=needs), separators=(",", ":")).encode("utf-8")
    http_request = urllib.request.Request(
        normalize_snapshot_url(url),
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        http_request.add_header("X-Plugin-Snapshot-Token", token)
    with urllib.request.urlopen(http_request, timeout=max(0.001, float(timeout))) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return payload if isinstance(payload, dict) else {}


def fetch_payloads(
    url: str = "http://127.0.0.1:8893/snapshot",
    *,
    token: str = "",
    timeout: float = 0.75,
    request: dict[str, Any] | None = None,
    needs: list[str] | None = None,
) -> dict[str, Any]:
    return world_model_core.extract_world_model_payloads(fetch(url, token=token, timeout=timeout, request=request, needs=needs))


def quality(payloads: dict[str, Any] | None) -> dict[str, Any]:
    return world_model_core.world_model_quality(payloads)
