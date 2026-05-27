from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
from typing import Any, Iterator


KEYBOARD_METHODS = {"keyboard_arrows", "keyboard_wasd"}
CAMERA_METHODS = {"auto", "keyboard_arrows", "keyboard_wasd", "middle_mouse_drag"}
CAMERA_COMMANDS = {
    "yaw_left",
    "yaw_right",
    "pitch_up",
    "pitch_down",
    "yaw_left_pitch_up",
    "yaw_right_pitch_up",
    "yaw_left_pitch_down",
    "yaw_right_pitch_down",
}


DEFAULT_VIEW_TOLERANCE_PX = 72.0

_ARROW_KEYS = {
    "yaw_left": ("left",),
    "yaw_right": ("right",),
    "pitch_up": ("up",),
    "pitch_down": ("down",),
    "yaw_left_pitch_up": ("left", "up"),
    "yaw_right_pitch_up": ("right", "up"),
    "yaw_left_pitch_down": ("left", "down"),
    "yaw_right_pitch_down": ("right", "down"),
}

_WASD_KEYS = {
    "yaw_left": ("a",),
    "yaw_right": ("d",),
    "pitch_up": ("w",),
    "pitch_down": ("s",),
    "yaw_left_pitch_up": ("a", "w"),
    "yaw_right_pitch_up": ("d", "w"),
    "yaw_left_pitch_down": ("a", "s"),
    "yaw_right_pitch_down": ("d", "s"),
}

_DRAG_DELTAS = {
    "yaw_left": (-80, 0),
    "yaw_right": (80, 0),
    "pitch_up": (0, -55),
    "pitch_down": (0, 55),
    "yaw_left_pitch_up": (-70, -45),
    "yaw_right_pitch_up": (70, -45),
    "yaw_left_pitch_down": (-70, 45),
    "yaw_right_pitch_down": (70, 45),
}


@dataclass(frozen=True)
class CameraInputSpec:
    method: str
    command: str
    keys: tuple[str, ...] = ()
    drag_dx: int = 0
    drag_dy: int = 0
    continuous_hover: bool = True


@dataclass(frozen=True)
class CameraMotorPlan:
    schema: str
    input_method: str
    command: str
    hold_duration_ms: int
    error_magnitude: float
    tolerance_px: float
    reason: str
    key_combination: tuple[str, ...] = ()
    drag_dx: int = 0
    drag_dy: int = 0


def normalize_camera_method(method: str | None) -> str:
    normalized = str(method or "auto").strip().lower()
    return normalized if normalized in CAMERA_METHODS else "auto"


def camera_method_sequence(method: str | None) -> list[str]:
    normalized = normalize_camera_method(method)
    if normalized == "auto":
        return ["keyboard_arrows", "middle_mouse_drag", "keyboard_wasd"]
    return [normalized]


def camera_input_spec(*, method: str | None, command: str, drag_pixels: int = 80) -> CameraInputSpec:
    normalized_method = normalize_camera_method(method)
    normalized_command = str(command or "yaw_right").strip().lower()
    if normalized_command not in CAMERA_COMMANDS:
        normalized_command = "yaw_right"
    if normalized_method == "auto":
        normalized_method = "keyboard_arrows"
    if normalized_method == "keyboard_arrows":
        return CameraInputSpec(method=normalized_method, command=normalized_command, keys=_ARROW_KEYS[normalized_command])
    if normalized_method == "keyboard_wasd":
        return CameraInputSpec(method=normalized_method, command=normalized_command, keys=_WASD_KEYS[normalized_command])
    dx, dy = _DRAG_DELTAS[normalized_command]
    scale = max(1, int(drag_pixels or 80)) / 80.0
    return CameraInputSpec(
        method="middle_mouse_drag",
        command=normalized_command,
        drag_dx=int(round(dx * scale)),
        drag_dy=int(round(dy * scale)),
        continuous_hover=False,
    )


def _float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _point_xy(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    x = _float(value.get("x", value.get("canvasX", value.get("screenX"))))
    y = _float(value.get("y", value.get("canvasY", value.get("screenY"))))
    if x is None or y is None:
        return None
    return x, y


def viewport_rect(viewport: dict[str, Any] | None = None, *, canvas_size: dict[str, Any] | None = None) -> dict[str, float]:
    viewport = viewport if isinstance(viewport, dict) else {}
    canvas_size = canvas_size if isinstance(canvas_size, dict) else {}
    x = _float(viewport.get("viewportXOffset"), 0.0) or 0.0
    y = _float(viewport.get("viewportYOffset"), 0.0) or 0.0
    width = _float(viewport.get("viewportWidth"), _float(viewport.get("canvasWidth"), _float(canvas_size.get("canvasWidth"), 765.0)))
    height = _float(viewport.get("viewportHeight"), _float(viewport.get("canvasHeight"), _float(canvas_size.get("canvasHeight"), 503.0)))
    width = 765.0 if width is None or width <= 0 else width
    height = 503.0 if height is None or height <= 0 else height
    return {
        "left": x,
        "top": y,
        "right": x + width,
        "bottom": y + height,
        "centerX": x + width / 2.0,
        "centerY": y + height / 2.0,
        "width": width,
        "height": height,
    }


def exposure_error_from_canvas_point(
    point: dict[str, Any] | None,
    *,
    viewport: dict[str, Any] | None = None,
    canvas_size: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rect = viewport_rect(viewport, canvas_size=canvas_size)
    xy = _point_xy(point)
    if xy is None:
        return {
            "schema": "camera_exposure_error.v1",
            "status": "unknown",
            "reason": "canvas_point_unavailable",
            "errorMagnitude": 0.0,
            "dxFromCenter": None,
            "dyFromCenter": None,
            "viewport": rect,
        }
    x, y = xy
    dx = x - rect["centerX"]
    dy = y - rect["centerY"]
    outside_x = 0.0
    outside_y = 0.0
    if x < rect["left"]:
        outside_x = rect["left"] - x
    elif x > rect["right"]:
        outside_x = x - rect["right"]
    if y < rect["top"]:
        outside_y = rect["top"] - y
    elif y > rect["bottom"]:
        outside_y = y - rect["bottom"]
    magnitude = math.hypot(dx, dy)
    outside = math.hypot(outside_x, outside_y)
    return {
        "schema": "camera_exposure_error.v1",
        "status": "offscreen" if outside > 0 else "in_viewport",
        "reason": "point_outside_viewport" if outside > 0 else "point_inside_viewport",
        "errorMagnitude": round(max(magnitude, outside), 3),
        "outsideViewportDistancePx": round(outside, 3),
        "dxFromCenter": round(dx, 3),
        "dyFromCenter": round(dy, 3),
        "viewport": rect,
    }


def camera_command_from_exposure_error(
    error: dict[str, Any] | None,
    *,
    allow_pitch: bool = True,
    allow_diagonal: bool = True,
) -> tuple[str, str]:
    error = error if isinstance(error, dict) else {}
    dx = _float(error.get("dxFromCenter"), 0.0) or 0.0
    dy = _float(error.get("dyFromCenter"), 0.0) or 0.0
    horizontal = "yaw_right" if dx >= 0 else "yaw_left"
    vertical = "pitch_up" if dy >= 0 else "pitch_down"
    if allow_pitch and allow_diagonal and abs(dy) >= DEFAULT_VIEW_TOLERANCE_PX:
        return f"{horizontal}_{vertical}", "canvas_point_offscreen_diagonal"
    if allow_pitch and abs(dx) < DEFAULT_VIEW_TOLERANCE_PX and abs(dy) >= DEFAULT_VIEW_TOLERANCE_PX:
        return vertical, "canvas_point_offscreen_vertical"
    return horizontal, "canvas_point_offscreen_horizontal"


def fitts_hold_duration_ms(
    error_magnitude: float | int | None,
    *,
    tolerance_px: float = DEFAULT_VIEW_TOLERANCE_PX,
    min_ms: int = 120,
    max_ms: int = 900,
    base_ms: int = 110,
    slope_ms: int = 95,
) -> int:
    distance = max(0.0, float(error_magnitude or 0.0))
    width = max(1.0, float(tolerance_px or DEFAULT_VIEW_TOLERANCE_PX))
    duration = float(base_ms) + float(slope_ms) * math.log2(1.0 + distance / width)
    return max(int(min_ms), min(int(max_ms), int(round(duration))))


def service_camera_motor_plan(
    exposure: dict[str, Any] | None,
    *,
    method: str | None = "keyboard_arrows",
    min_ms: int = 120,
    max_ms: int = 900,
    allow_pitch: bool = True,
    allow_diagonal: bool = True,
) -> CameraMotorPlan:
    exposure = exposure if isinstance(exposure, dict) else {}
    error = exposure.get("exposureError") if isinstance(exposure.get("exposureError"), dict) else exposure
    command, reason = camera_command_from_exposure_error(error, allow_pitch=allow_pitch, allow_diagonal=allow_diagonal)
    tolerance = float(error.get("tolerancePx") or exposure.get("tolerancePx") or DEFAULT_VIEW_TOLERANCE_PX)
    hold_ms = fitts_hold_duration_ms(
        error.get("errorMagnitude"),
        tolerance_px=tolerance,
        min_ms=min_ms,
        max_ms=max_ms,
    )
    spec = camera_input_spec(method=method, command=command)
    return CameraMotorPlan(
        schema="camera_motor_plan.v1",
        input_method=spec.method,
        command=spec.command,
        hold_duration_ms=hold_ms,
        error_magnitude=float(error.get("errorMagnitude") or 0.0),
        tolerance_px=tolerance,
        reason=reason,
        key_combination=tuple(spec.keys),
        drag_dx=spec.drag_dx,
        drag_dy=spec.drag_dy,
    )


def smoothstep(value: float) -> float:
    t = min(1.0, max(0.0, float(value)))
    return 10.0 * t**3 - 15.0 * t**4 + 6.0 * t**5


def smooth_drag_segments(dx: int, dy: int, *, steps: int) -> list[tuple[int, int]]:
    steps = max(1, int(steps or 1))
    segments: list[tuple[int, int]] = []
    last_x = 0
    last_y = 0
    for index in range(1, steps + 1):
        factor = smoothstep(index / steps)
        next_x = int(round(float(dx) * factor))
        next_y = int(round(float(dy) * factor))
        segments.append((next_x - last_x, next_y - last_y))
        last_x = next_x
        last_y = next_y
    return [(x, y) for x, y in segments if x or y]


@contextmanager
def hold_camera_input(backend: Any, spec: CameraInputSpec) -> Iterator[None]:
    if spec.method not in KEYBOARD_METHODS:
        raise RuntimeError(f"camera method is not a holdable keyboard method: {spec.method}")
    key_down = getattr(backend, "key_down", None)
    key_up = getattr(backend, "key_up", None)
    if not callable(key_down) or not callable(key_up):
        raise RuntimeError("backend does not support held keyboard camera input")
    held: list[str] = []
    try:
        for key in spec.keys:
            key_down(key)
            held.append(key)
        yield
    finally:
        for key in reversed(held):
            try:
                key_up(key)
            except Exception:  # noqa: BLE001
                pass


def apply_middle_mouse_drag_pulse(
    backend: Any,
    spec: CameraInputSpec,
    *,
    duration_ms: int,
    sleep_func,
) -> None:
    if spec.method != "middle_mouse_drag":
        raise RuntimeError(f"camera method is not middle mouse drag: {spec.method}")
    mouse_down = getattr(backend, "mouse_down", None)
    mouse_up = getattr(backend, "mouse_up", None)
    move_relative = getattr(backend, "move_relative", None)
    if not callable(mouse_down) or not callable(mouse_up) or not callable(move_relative):
        raise RuntimeError("backend does not support middle mouse camera drag")
    try:
        mouse_down(button="middle")
        duration = max(0, int(duration_ms or 0))
        steps = max(1, min(12, int(round(duration / 45.0)) if duration > 0 else 1))
        per_step_ms = max(0, int(round(duration / steps))) if steps else duration
        for dx, dy in smooth_drag_segments(spec.drag_dx, spec.drag_dy, steps=steps):
            move_relative(dx, dy, duration_ms=per_step_ms)
            if per_step_ms > 0:
                sleep_func(max(0.0, float(per_step_ms) / 1000.0))
    finally:
        mouse_up(button="middle")


def camera_angle_delta(before: Any, after: Any, *, modulus: int = 2048) -> int | None:
    if before is None or after is None:
        return None
    try:
        raw = int(after) - int(before)
    except (TypeError, ValueError):
        return None
    half = int(modulus) // 2
    return ((raw + half) % int(modulus)) - half
