from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
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
        move_relative(spec.drag_dx, spec.drag_dy, duration_ms=max(0, int(duration_ms or 0)))
        if duration_ms > 0:
            sleep_func(max(0.0, float(duration_ms) / 1000.0))
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
