from __future__ import annotations

import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import camera_control
from .mouse_movement import (
    MouseMovementPlan,
    MouseMovementProfile,
    MousePoint,
    MouseTarget,
    plan_mouse_movement,
)


INPUT_PROFILES = {"instant_debug", "steady", "natural", "manual_calibrated"}


@dataclass(frozen=True)
class HumanInputProfile:
    name: str
    movement_generator: str
    min_move_ms: int
    max_move_ms: int
    fitts_a_ms: float
    fitts_b_ms: float
    endpoint_jitter_px: int
    path_jitter_px: int
    pre_click_settle_ms: tuple[int, int]
    click_hold_ms: tuple[int, int]
    post_click_settle_ms: tuple[int, int]
    reaction_delays_ms: dict[str, tuple[int, int]]
    min_camera_hold_ms: int
    max_camera_hold_ms: int
    camera_sample_interval_ms: tuple[int, int]
    direction_switch_cooldown_ms: tuple[int, int]


@dataclass(frozen=True)
class HumanInputContext:
    reason: str = "generic"
    action_intent_type: str | None = None
    target_width_px: int | None = None
    safe_hull_width_px: int | None = None
    safe_radius_px: int | None = None


@dataclass
class HumanInputMetrics:
    profile: str
    movement_generator: str
    mouse_move_durations_ms: list[int] = field(default_factory=list)
    click_hold_durations_ms: list[int] = field(default_factory=list)
    reaction_delays_ms: list[int] = field(default_factory=list)
    camera_hold_durations_ms: list[int] = field(default_factory=list)
    camera_direction_switches: int = 0
    correction_count: int = 0
    endpoint_jitter_px: list[int] = field(default_factory=list)
    direct_backend_bypass_count: int = 0
    click_count: int = 0
    movement_count: int = 0
    key_hold_count: int = 0


def resolve_input_profile(name: str | None) -> HumanInputProfile:
    normalized = str(name or "instant_debug").strip().lower()
    if normalized not in INPUT_PROFILES:
        normalized = "instant_debug"
    instant_delays = {
        "target_switch": (0, 0),
        "after_resource_gain": (0, 0),
        "after_navigation_progress": (0, 0),
        "after_camera_adjust": (0, 0),
        "after_hover_mismatch": (0, 0),
        "after_service_transition": (0, 0),
        "no_safe_target_wait": (0, 0),
    }
    steady_delays = {
        "target_switch": (300, 700),
        "after_resource_gain": (350, 900),
        "after_navigation_progress": (300, 300),
        "after_camera_adjust": (120, 260),
        "after_hover_mismatch": (160, 360),
        "after_service_transition": (350, 850),
        "no_safe_target_wait": (180, 420),
    }
    natural_delays = {
        "target_switch": (350, 1200),
        "after_resource_gain": (400, 1400),
        "after_navigation_progress": (350, 950),
        "after_camera_adjust": (140, 420),
        "after_hover_mismatch": (200, 650),
        "after_service_transition": (450, 1400),
        "no_safe_target_wait": (250, 800),
    }
    if normalized == "instant_debug":
        return HumanInputProfile(
            name=normalized,
            movement_generator="configured",
            min_move_ms=0,
            max_move_ms=300,
            fitts_a_ms=0.0,
            fitts_b_ms=0.0,
            endpoint_jitter_px=0,
            path_jitter_px=0,
            pre_click_settle_ms=(0, 0),
            click_hold_ms=(0, 0),
            post_click_settle_ms=(0, 0),
            reaction_delays_ms=instant_delays,
            min_camera_hold_ms=0,
            max_camera_hold_ms=900,
            camera_sample_interval_ms=(20, 40),
            direction_switch_cooldown_ms=(0, 0),
        )
    if normalized == "steady":
        return HumanInputProfile(
            name=normalized,
            movement_generator="fitts_guided",
            min_move_ms=140,
            max_move_ms=900,
            fitts_a_ms=90.0,
            fitts_b_ms=115.0,
            endpoint_jitter_px=0,
            path_jitter_px=1,
            pre_click_settle_ms=(40, 120),
            click_hold_ms=(55, 110),
            post_click_settle_ms=(40, 120),
            reaction_delays_ms=steady_delays,
            min_camera_hold_ms=120,
            max_camera_hold_ms=900,
            camera_sample_interval_ms=(20, 40),
            direction_switch_cooldown_ms=(120, 180),
        )
    # manual_calibrated intentionally starts from the natural envelope until a
    # future calibration file is explicitly introduced.
    return HumanInputProfile(
        name=normalized,
        movement_generator="wind_mouse",
        min_move_ms=170,
        max_move_ms=1250,
        fitts_a_ms=110.0,
        fitts_b_ms=130.0,
        endpoint_jitter_px=2,
        path_jitter_px=3,
        pre_click_settle_ms=(50, 180),
        click_hold_ms=(45, 140),
        post_click_settle_ms=(50, 160),
        reaction_delays_ms=natural_delays,
        min_camera_hold_ms=150,
        max_camera_hold_ms=1200,
        camera_sample_interval_ms=(20, 50),
        direction_switch_cooldown_ms=(120, 250),
    )


def _midpoint(bounds: tuple[int, int]) -> int:
    left, right = bounds
    return int((int(left) + int(right)) / 2)


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _inverse_smoothstep(value: float) -> float:
    target = max(0.0, min(1.0, float(value)))
    low = 0.0
    high = 1.0
    for _ in range(16):
        mid = (low + high) / 2.0
        smoothed = mid * mid * (3.0 - 2.0 * mid)
        if smoothed < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _retime_minimum_jerk(plan: MouseMovementPlan) -> MouseMovementPlan:
    if plan.duration_ms <= 0 or len(plan.points) <= 2:
        return plan
    points = []
    total = len(plan.points) - 1
    previous = -1
    for index, point in enumerate(plan.points):
        progress = index / total
        timestamp = int(round(plan.duration_ms * _inverse_smoothstep(progress)))
        if index == 0:
            timestamp = 0
        elif index == total:
            timestamp = plan.duration_ms
        elif timestamp <= previous:
            timestamp = previous + 1
        previous = timestamp
        points.append(MousePoint(point.x, point.y, timestamp))
    return MouseMovementPlan(
        schema=plan.schema,
        profile_name=plan.profile_name,
        start=points[0],
        target=plan.target,
        duration_ms=plan.duration_ms,
        points=points,
        click_point=points[-1],
        path_length_px=plan.path_length_px,
        estimated_difficulty=plan.estimated_difficulty,
        warnings=list(plan.warnings),
        validation_status=plan.validation_status,
    )


class HumanInputController:
    def __init__(
        self,
        backend: Any,
        *,
        profile: str | HumanInputProfile | None = "instant_debug",
        sleep_func=time.sleep,
        monotonic_func=time.monotonic,
        seed: int | None = None,
    ) -> None:
        self.backend = backend
        self.profile = profile if isinstance(profile, HumanInputProfile) else resolve_input_profile(profile)
        self.sleep_func = sleep_func
        self.monotonic_func = monotonic_func
        self.rng = random.Random(seed)
        self._metrics = HumanInputMetrics(profile=self.profile.name, movement_generator=self.profile.movement_generator)
        self._last_camera_command: str | None = None

    def plan_mouse_movement(
        self,
        start: MousePoint | tuple[int, int],
        target: MouseTarget | dict[str, Any],
        movement_profile: str | MouseMovementProfile | None,
        *,
        context: HumanInputContext | None = None,
    ) -> MouseMovementPlan:
        if self.profile.name == "instant_debug":
            return plan_mouse_movement(start, target, movement_profile)
        target_obj = target if isinstance(target, MouseTarget) else MouseTarget(
            x=int(target.get("x")),
            y=int(target.get("y")),
            radius_px=int(target.get("radiusPx", target.get("radius_px", 4))),
            width_px=target.get("widthPx") or target.get("width_px"),
            height_px=target.get("heightPx") or target.get("height_px"),
            label=str(target.get("label") or "target"),
            source=str(target.get("source") or "unknown"),
        )
        context = context or HumanInputContext()
        width = context.safe_hull_width_px or context.target_width_px or target_obj.width_px
        radius = context.safe_radius_px or target_obj.radius_px
        target_obj = MouseTarget(
            target_obj.x,
            target_obj.y,
            radius_px=max(1, int(radius or target_obj.radius_px)),
            width_px=int(width) if width else target_obj.width_px,
            height_px=target_obj.height_px,
            label=target_obj.label,
            source=target_obj.source,
        )
        generator = self.profile.movement_generator
        profile = MouseMovementProfile(
            name="fitts_guided" if generator == "fitts_guided" else "wind_mouse",
            min_duration_ms=self.profile.min_move_ms,
            max_duration_ms=self.profile.max_move_ms,
            waypoint_count=30 if generator == "fitts_guided" else 40,
            path_jitter_px=self.profile.path_jitter_px,
            endpoint_jitter_px=min(self.profile.endpoint_jitter_px, max(0, target_obj.radius_px)),
            curve_type="fitts" if generator == "fitts_guided" else "wind",
            seed=self.rng.randint(0, 2_147_483_647),
        )
        plan = plan_mouse_movement(start, target_obj, profile)
        return _retime_minimum_jerk(plan)

    def move_mouse(self, plan: MouseMovementPlan, *, context: HumanInputContext | None = None) -> None:
        mover = getattr(self.backend, "move", None)
        if not callable(mover):
            raise RuntimeError(f"backend does not support governed mouse movement: {getattr(self.backend, 'name', self.backend.__class__.__name__)}")
        mover(plan)
        self._metrics.mouse_move_durations_ms.append(max(0, int(plan.duration_ms or 0)))
        self._metrics.movement_count += 1

    def click_at(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        hold_ms: int | None = None,
        context: HumanInputContext | None = None,
    ) -> None:
        pre_ms = _midpoint(self.profile.pre_click_settle_ms)
        if pre_ms > 0:
            self.sleep_func(pre_ms / 1000.0)
        effective_hold = max(0, int(hold_ms or 0))
        if effective_hold <= 0:
            effective_hold = _midpoint(self.profile.click_hold_ms)
        clicker = getattr(self.backend, "click_at", None)
        if not callable(clicker):
            raise RuntimeError(f"backend does not support governed click: {getattr(self.backend, 'name', self.backend.__class__.__name__)}")
        clicker(int(x), int(y), button=button, hold_ms=effective_hold)
        self._metrics.click_hold_durations_ms.append(effective_hold)
        self._metrics.click_count += 1
        post_ms = _midpoint(self.profile.post_click_settle_ms)
        if post_ms > 0:
            self.sleep_func(post_ms / 1000.0)

    def move_and_click(
        self,
        plan: MouseMovementPlan,
        *,
        button: str = "left",
        hold_ms: int | None = None,
        context: HumanInputContext | None = None,
    ) -> None:
        if not callable(getattr(self.backend, "move", None)) or not callable(getattr(self.backend, "click_at", None)):
            combined = getattr(self.backend, "move_and_click", None)
            if not callable(combined):
                raise RuntimeError(
                    f"backend does not support governed move-and-click: {getattr(self.backend, 'name', self.backend.__class__.__name__)}"
                )
            combined(plan, button=button)
            self._metrics.mouse_move_durations_ms.append(max(0, int(plan.duration_ms or 0)))
            self._metrics.click_hold_durations_ms.append(max(0, int(hold_ms or 0)))
            self._metrics.movement_count += 1
            self._metrics.click_count += 1
            return
        self.move_mouse(plan, context=context)
        self.click_at(plan.click_point.x, plan.click_point.y, button=button, hold_ms=hold_ms, context=context)

    @contextmanager
    def hold_mouse_button(self, *, button: str = "left", context: HumanInputContext | None = None) -> Iterator[None]:
        mouse_down = getattr(self.backend, "mouse_down", None)
        mouse_up = getattr(self.backend, "mouse_up", None)
        if not callable(mouse_down) or not callable(mouse_up):
            raise RuntimeError(f"backend does not support governed held mouse input: {getattr(self.backend, 'name', self.backend.__class__.__name__)}")
        started = float(self.monotonic_func())
        pressed = False
        try:
            mouse_down(button=button)
            pressed = True
            yield
        finally:
            if pressed:
                try:
                    mouse_up(button=button)
                except Exception:  # noqa: BLE001
                    pass
            elapsed_ms = max(0, int(round((float(self.monotonic_func()) - started) * 1000.0)))
            self._metrics.click_hold_durations_ms.append(elapsed_ms)
            self._metrics.click_count += 1

    def press_key(self, key: str, *, context: HumanInputContext | None = None) -> None:
        presser = getattr(self.backend, "press", None)
        if callable(presser):
            presser(key)
            return
        with self.hold_keys((key,), context=context):
            hold_ms = max(25, _midpoint(self.profile.click_hold_ms))
            if hold_ms > 0:
                self.sleep_func(hold_ms / 1000.0)

    @contextmanager
    def hold_keys(self, keys: tuple[str, ...] | list[str], *, context: HumanInputContext | None = None) -> Iterator[None]:
        key_down = getattr(self.backend, "key_down", None)
        key_up = getattr(self.backend, "key_up", None)
        if not callable(key_down) or not callable(key_up):
            raise RuntimeError("backend does not support governed held keyboard input")
        key_tuple = tuple(keys)
        if self._last_camera_command is not None and self._last_camera_command != "+".join(key_tuple):
            self._metrics.camera_direction_switches += 1
        self._last_camera_command = "+".join(key_tuple)
        held: list[str] = []
        started = float(self.monotonic_func())
        try:
            for key in key_tuple:
                key_down(key)
                held.append(key)
            self._metrics.key_hold_count += 1
            yield
        finally:
            for key in reversed(held):
                try:
                    key_up(key)
                except Exception:  # noqa: BLE001
                    pass
            elapsed_ms = max(0, int(round((float(self.monotonic_func()) - started) * 1000.0)))
            self._metrics.camera_hold_durations_ms.append(elapsed_ms)

    def camera_drag_pulse(self, spec: camera_control.CameraInputSpec, *, duration_ms: int) -> None:
        camera_control.apply_middle_mouse_drag_pulse(self.backend, spec, duration_ms=duration_ms, sleep_func=self.sleep_func)
        self._metrics.camera_hold_durations_ms.append(max(0, int(duration_ms or 0)))

    def apply_fixed_delay(self, reason: str, delay_ms: int) -> int:
        delay = max(0, int(delay_ms or 0))
        if delay > 0:
            self.sleep_func(delay / 1000.0)
            self._metrics.reaction_delays_ms.append(delay)
        return delay

    def apply_reaction_delay(self, reason: str) -> int:
        delay = _midpoint(self.profile.reaction_delays_ms.get(reason, (0, 0)))
        return self.apply_fixed_delay(reason, delay)

    def metrics(self) -> dict[str, Any]:
        camera_holds = self._metrics.camera_hold_durations_ms
        return {
            "profile": self._metrics.profile,
            "movementGenerator": self._metrics.movement_generator,
            "movementCount": self._metrics.movement_count,
            "clickCount": self._metrics.click_count,
            "keyHoldCount": self._metrics.key_hold_count,
            "averageMouseMoveMs": _average(self._metrics.mouse_move_durations_ms),
            "mouseMoveMinMs": min(self._metrics.mouse_move_durations_ms) if self._metrics.mouse_move_durations_ms else None,
            "mouseMoveMaxMs": max(self._metrics.mouse_move_durations_ms) if self._metrics.mouse_move_durations_ms else None,
            "averageClickHoldMs": _average(self._metrics.click_hold_durations_ms),
            "clickHoldMinMs": min(self._metrics.click_hold_durations_ms) if self._metrics.click_hold_durations_ms else None,
            "clickHoldMaxMs": max(self._metrics.click_hold_durations_ms) if self._metrics.click_hold_durations_ms else None,
            "averageReactionDelayMs": _average(self._metrics.reaction_delays_ms),
            "reactionDelayMinMs": min(self._metrics.reaction_delays_ms) if self._metrics.reaction_delays_ms else None,
            "reactionDelayMaxMs": max(self._metrics.reaction_delays_ms) if self._metrics.reaction_delays_ms else None,
            "cameraHoldMinMs": min(camera_holds) if camera_holds else None,
            "cameraHoldAvgMs": _average(camera_holds),
            "cameraHoldMaxMs": max(camera_holds) if camera_holds else None,
            "cameraDirectionSwitches": self._metrics.camera_direction_switches,
            "correctionCount": self._metrics.correction_count,
            "directBackendBypassCount": self._metrics.direct_backend_bypass_count,
        }
