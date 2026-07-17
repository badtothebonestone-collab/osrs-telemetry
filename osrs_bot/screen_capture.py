from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageGrab

from .model import ScreenBounds, ScreenPoint


_DPI_AWARENESS_SET = False


@dataclass(frozen=True, slots=True)
class CaptureMetadata:
    canvas_bounds: ScreenBounds
    captured_bounds: ScreenBounds
    relative_bounds: ScreenBounds
    method: str = "windows_imagegrab"


def bounded_region_around(
    canvas_bounds: ScreenBounds,
    center: ScreenPoint,
    *,
    width: int = 320,
    height: int = 240,
) -> ScreenBounds:
    if not isinstance(canvas_bounds, ScreenBounds):
        raise TypeError("canvas_bounds must be ScreenBounds")
    _require_positive_bounds(canvas_bounds, "canvas_bounds")
    if not isinstance(center, ScreenPoint) or not canvas_bounds.contains(center):
        raise ValueError("center must be inside canvas_bounds")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or width <= 0
        or not isinstance(height, int)
        or isinstance(height, bool)
        or height <= 0
    ):
        raise ValueError("capture dimensions must be positive integers")
    width = min(width, canvas_bounds.width)
    height = min(height, canvas_bounds.height)
    left = min(
        max(canvas_bounds.x, center.x - width // 2),
        canvas_bounds.x + canvas_bounds.width - width,
    )
    top = min(
        max(canvas_bounds.y, center.y - height // 2),
        canvas_bounds.y + canvas_bounds.height - height,
    )
    return ScreenBounds(left, top, width, height)


def capture_canvas_region(
    canvas_bounds: ScreenBounds,
    captured_bounds: ScreenBounds | None = None,
    *,
    grab: Callable[..., Image.Image] = ImageGrab.grab,
) -> tuple[Image.Image, CaptureMetadata]:
    """Read pixels only; never focus a window or synthesize input."""

    if os.name != "nt":
        raise RuntimeError("canvas capture is supported only on Windows")
    if not isinstance(canvas_bounds, ScreenBounds):
        raise TypeError("canvas_bounds must be ScreenBounds")
    _require_positive_bounds(canvas_bounds, "canvas_bounds")
    region = captured_bounds or canvas_bounds
    if not isinstance(region, ScreenBounds):
        raise TypeError("captured_bounds must be ScreenBounds or None")
    _require_positive_bounds(region, "captured_bounds")
    corners = (
        ScreenPoint(region.x, region.y),
        ScreenPoint(region.x + region.width - 1, region.y + region.height - 1),
    )
    if not all(canvas_bounds.contains(point) for point in corners):
        raise ValueError("captured_bounds must be inside the verified canvas")
    _enable_windows_dpi_awareness()
    image = grab(
        bbox=(
            region.x,
            region.y,
            region.x + region.width,
            region.y + region.height,
        ),
        all_screens=True,
    ).convert("RGB")
    if image.size != (region.width, region.height):
        raise RuntimeError("captured image dimensions do not match the verified region")
    metadata = CaptureMetadata(
        canvas_bounds=canvas_bounds,
        captured_bounds=region,
        relative_bounds=ScreenBounds(
            region.x - canvas_bounds.x,
            region.y - canvas_bounds.y,
            region.width,
            region.height,
        ),
    )
    return image, metadata


def _require_positive_bounds(bounds: ScreenBounds, label: str) -> None:
    if bounds.width <= 0 or bounds.height <= 0:
        raise ValueError(f"{label} must have positive dimensions")


def _enable_windows_dpi_awareness() -> None:
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if setter is not None:
            bits = ctypes.sizeof(ctypes.c_void_p) * 8
            setter(ctypes.c_void_p((-4) & ((1 << bits) - 1)))
        else:
            user32.SetProcessDPIAware()
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"screen capture DPI awareness failed: {error}") from error
    _DPI_AWARENESS_SET = True
