from __future__ import annotations

from typing import Any


def point_label(point: Any) -> str:
    if not isinstance(point, dict):
        return "none"
    x = point.get("x")
    y = point.get("y")
    if x is None or y is None:
        return "none"
    return f"{x},{y}"


def tile_label(tile: Any) -> str:
    if not isinstance(tile, dict):
        return "none"
    x = tile.get("worldX")
    y = tile.get("worldY")
    plane = tile.get("plane", 0)
    if x is None or y is None:
        return "none"
    return f"{x},{y},{plane}"
