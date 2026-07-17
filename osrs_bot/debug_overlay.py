from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from .engine_frame import EngineFrame, EngineFramePublisher, EngineStage
from .model import ScreenBounds, ScreenPoint
from .task_contract import TargetEvidence


SELECTED_COLOR = "#38d267"
ELIGIBLE_COLOR = "#e8ad32"
REJECTED_COLOR = "#e44848"
ROUTE_COLOR = "#4da3ff"
MANDATORY_ROUTE_COLOR = "#ff7a45"
SKIPPED_ROUTE_COLOR = "#8b96a8"
TARGET_SHAPE_COLOR = "#38bdf8"
TARGET_INSET_COLOR = "#22d3ee"
CANDIDATE_COLOR = "#fde047"
CAMERA_REGION_COLOR = "#d946ef"
POINTER_PATH_COLOR = "#c084fc"
TEXT_COLOR = "#f1f4f8"
BACKGROUND_COLOR = "#010203"
TERMINAL_BANNER_SECONDS = 8.0

WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
PASSIVE_EX_STYLE_MASK = (
    WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
)
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
GA_ROOT = 2
_DPI_AWARENESS_SET = False


@dataclass(frozen=True, slots=True)
class OverlayRectangle:
    bounds: ScreenBounds
    color: str
    label: str


@dataclass(frozen=True, slots=True)
class OverlayPoint:
    point: ScreenPoint
    color: str
    label: str = ""
    radius: int = 3


@dataclass(frozen=True, slots=True)
class OverlayPolyline:
    points: tuple[ScreenPoint, ...]
    color: str
    label: str = ""
    width: int = 2
    smooth: bool = False


@dataclass(frozen=True, slots=True)
class OverlayScene:
    source_sequence: int
    canvas_bounds: ScreenBounds | None
    rectangles: tuple[OverlayRectangle, ...]
    text_lines: tuple[str, ...]
    points: tuple[OverlayPoint, ...] = ()
    polylines: tuple[OverlayPolyline, ...] = ()


@dataclass(frozen=True, slots=True)
class PassiveWindowProof:
    hwnd: int
    extended_style: int
    click_through: bool
    non_focusable: bool
    tool_window: bool
    layered: bool

    @property
    def valid(self) -> bool:
        return (
            self.hwnd > 0
            and self.click_through
            and self.non_focusable
            and self.tool_window
            and self.layered
        )


def build_overlay_scene(
    frame: EngineFrame,
    *,
    show_rejected: bool = False,
    presentation: object | None = None,
    bound_run_id: str | None = None,
    now: datetime | None = None,
) -> OverlayScene:
    """Format only the evidence already published by the runtime."""

    if not isinstance(frame, EngineFrame):
        raise TypeError("frame must be EngineFrame")
    if not isinstance(show_rejected, bool):
        raise TypeError("show_rejected must be bool")

    current_time = now or datetime.now(timezone.utc)
    geometry_allowed = _frame_geometry_is_live(frame, current_time)
    if presentation is not None:
        geometry_allowed = bool(getattr(presentation, "geometry_allowed", False))
        presentation_run_id = getattr(presentation, "run_id", None)
        if bound_run_id is not None and presentation_run_id != bound_run_id:
            geometry_allowed = False

    rectangles: list[OverlayRectangle] = []
    points: list[OverlayPoint] = []
    polylines: list[OverlayPolyline] = []
    selected = frame.selected_target
    selected_key = selected.key if selected is not None else None
    selected_bounds = (
        _current_target_bounds(frame, selected) if geometry_allowed else None
    )
    diagnostic_geometry_allowed = bool(
        geometry_allowed and selected is not None and selected_bounds is not None
    )
    if selected is not None and selected_bounds is not None:
        rectangles.append(
            OverlayRectangle(selected_bounds, SELECTED_COLOR, _target_label(selected))
        )
    for target in frame.eligible_targets:
        if target.key == selected_key:
            continue
        bounds = _current_target_bounds(frame, target) if geometry_allowed else None
        if bounds is not None:
            rectangles.append(
                OverlayRectangle(bounds, ELIGIBLE_COLOR, _target_label(target))
            )
    if show_rejected:
        for rejected in frame.rejected_targets:
            bounds = (
                _current_target_bounds(frame, rejected.target)
                if geometry_allowed
                else None
            )
            if bounds is not None:
                label = (
                    f"{_target_label(rejected.target)} "
                    f"[{','.join(rejected.rejection_codes)}]"
                )
                rectangles.append(
                    OverlayRectangle(bounds, REJECTED_COLOR, label)
                )

    if geometry_allowed and frame.decision is not None:
        evidence = frame.decision.evidence
        if diagnostic_geometry_allowed:
            _append_route_geometry(evidence.route, points, polylines)
            _append_targeting_geometry(
                evidence.targeting, rectangles, points, polylines
            )
            _append_pointer_geometry(frame, points, polylines)
        if _camera_geometry_is_current(
            frame,
            evidence.camera,
            legacy_geometry_allowed=diagnostic_geometry_allowed,
        ):
            _append_camera_geometry(evidence.camera, rectangles, points)

    task = frame.task
    presentation_state = _presentation_state_text(presentation)
    age_seconds = _frame_age_seconds(frame, current_time)
    terminal = frame.stage is EngineStage.TERMINAL or presentation_state in {
        "COMPLETE",
        "BLOCKED",
        "SAFE_STOPPED",
        "ERROR",
    }
    if terminal and presentation_state is None:
        task_state = frame.task.status.value.upper()
        presentation_state = (
            task_state
            if task_state in {"COMPLETE", "BLOCKED", "SAFE_STOPPED", "ERROR"}
            else "TERMINAL"
        )
    terminal_age_seconds = _timestamp_age_seconds(frame.published_at, current_time)
    if terminal and terminal_age_seconds > TERMINAL_BANNER_SECONDS:
        return OverlayScene(
            source_sequence=frame.sequence,
            canvas_bounds=(
                frame.observation.canvas_bounds
                if frame.observation is not None
                else None
            ),
            rectangles=(),
            text_lines=(
                f"{presentation_state or 'TERMINAL'} — terminal summary retained in operator GUI",
            ),
        )

    text = [
        _overlay_banner(presentation_state, age_seconds, geometry_allowed),
        f"{task.task_id} | {task.state} | {task.status.value}",
        _binding_line(frame),
        (
            "decision: none"
            if frame.decision is None
            else f"decision: {frame.decision.reason}"
        ),
        (
            "target: none"
            if selected is None
            else (
                f"target: {_target_label(selected)} @ {_point_text(selected)}"
                if selected_bounds is not None
                else f"last known target: {_target_label(selected)} @ geometry suppressed"
            )
        ),
        _safety_line(frame),
        _verification_line(frame),
        _outcome_line(frame),
        (
            "cleanup: safe"
            if frame.cleanup.safe
            else (
                "cleanup: not attempted"
                if not frame.cleanup.attempted
                else "cleanup: unsafe or incomplete"
            )
        ),
    ]
    if frame.blocker:
        text.append(f"blocker: {frame.blocker}")
    if frame.decision is not None:
        evidence = frame.decision.evidence
        text.extend(
            line
            for line in (
                _route_line(evidence.route),
                _camera_line(evidence.camera),
                _targeting_line(
                    evidence.targeting,
                    geometry_allowed=diagnostic_geometry_allowed,
                ),
                _pointer_line(frame),
                _timing_line(evidence.timing),
            )
            if line is not None
        )
    return OverlayScene(
        source_sequence=frame.sequence,
        canvas_bounds=(
            frame.observation.canvas_bounds
            if frame.observation is not None
            else None
        ),
        rectangles=tuple(rectangles),
        text_lines=tuple(text),
        points=tuple(points),
        polylines=tuple(polylines),
    )


def _append_route_geometry(
    route: object | None,
    points: list[OverlayPoint],
    polylines: list[OverlayPolyline],
) -> None:
    if route is None:
        return
    projected = _first_screen_points(
        route, "projected_route_points", "projected_points"
    )
    if len(projected) >= 2:
        polylines.append(
            OverlayPolyline(projected, ROUTE_COLOR, "route corridor", width=4)
        )
    mandatory = _first_screen_points(
        route, "mandatory_route_points", "mandatory_points"
    )
    skipped = _first_screen_points(route, "skipped_route_points", "skipped_points")
    points.extend(
        OverlayPoint(point, MANDATORY_ROUTE_COLOR, "mandatory", radius=4)
        for point in mandatory
    )
    points.extend(
        OverlayPoint(point, SKIPPED_ROUTE_COLOR, "", radius=2)
        for point in skipped
    )
    selected = getattr(route, "selected_screen_point", None)
    if isinstance(selected, ScreenPoint):
        points.append(OverlayPoint(selected, SELECTED_COLOR, "route target", radius=5))


def _append_camera_geometry(
    camera: object | None,
    rectangles: list[OverlayRectangle],
    points: list[OverlayPoint],
) -> None:
    if camera is None:
        return
    desired = getattr(camera, "desired_region", None)
    if isinstance(desired, ScreenBounds):
        rectangles.append(
            OverlayRectangle(desired, CAMERA_REGION_COLOR, "desired camera framing")
        )
    target_bounds = getattr(camera, "target_bounds", None)
    if isinstance(target_bounds, ScreenBounds):
        rectangles.append(
            OverlayRectangle(
                target_bounds, CAMERA_REGION_COLOR, "camera target shape"
            )
        )
    lookahead_bounds = getattr(camera, "lookahead_bounds", None)
    if isinstance(lookahead_bounds, ScreenBounds):
        rectangles.append(
            OverlayRectangle(
                lookahead_bounds, ROUTE_COLOR, "camera lookahead bounds"
            )
        )
    target = getattr(camera, "target_point", None)
    if isinstance(target, ScreenPoint):
        points.append(
            OverlayPoint(target, CAMERA_REGION_COLOR, "camera target", radius=5)
        )
    lookahead = _first_screen_points(camera, "lookahead_points")
    points.extend(
        OverlayPoint(point, ROUTE_COLOR, "camera lookahead", radius=3)
        for point in lookahead
    )


def _append_targeting_geometry(
    targeting: object | None,
    rectangles: list[OverlayRectangle],
    points: list[OverlayPoint],
    polylines: list[OverlayPolyline],
) -> None:
    if targeting is None:
        return
    polygon = _first_screen_points(
        targeting, "shape_polygon", "authoritative_polygon", "target_polygon"
    )
    if len(polygon) >= 3:
        polylines.append(
            OverlayPolyline(
                (*polygon, polygon[0]),
                TARGET_SHAPE_COLOR,
                f"target {getattr(targeting, 'geometry_source', 'shape')}",
            )
        )
    else:
        shape = getattr(targeting, "shape_bounds", None)
        if isinstance(shape, ScreenBounds):
            rectangles.append(
                OverlayRectangle(
                    shape,
                    TARGET_SHAPE_COLOR,
                    f"target {getattr(targeting, 'geometry_source', 'shape')}",
                )
            )
    inset = getattr(targeting, "inset_region", None)
    if isinstance(inset, ScreenBounds):
        rectangles.append(OverlayRectangle(inset, TARGET_INSET_COLOR, "usable aim"))
    candidates = _first_screen_points(targeting, "candidate_points")
    points.extend(
        OverlayPoint(point, CANDIDATE_COLOR, "", radius=2) for point in candidates
    )
    selected = getattr(targeting, "selected_point", None)
    if isinstance(selected, ScreenPoint):
        points.append(OverlayPoint(selected, SELECTED_COLOR, "selected aim", radius=5))


def _append_pointer_geometry(
    frame: EngineFrame,
    points: list[OverlayPoint],
    polylines: list[OverlayPolyline],
) -> None:
    receipt = frame.last_execution_receipt
    motion = receipt.pointer_motion if receipt is not None else None
    if motion is None or getattr(motion, "plan_count", 0) <= 0:
        return
    start = getattr(motion, "requested_start", None)
    target = getattr(motion, "requested_target", None)
    controls = _first_screen_points(motion, "control_points")
    if isinstance(start, ScreenPoint) and isinstance(target, ScreenPoint):
        polylines.append(
            OverlayPolyline(
                (start, *controls, target),
                POINTER_PATH_COLOR,
                "recent pointer path",
                width=2,
                smooth=True,
            )
        )
    settled = getattr(motion, "settled_target", None)
    if isinstance(settled, ScreenPoint):
        points.append(OverlayPoint(settled, POINTER_PATH_COLOR, "settled", radius=3))


def _first_screen_points(value: object, *field_names: str) -> tuple[ScreenPoint, ...]:
    for field_name in field_names:
        raw = getattr(value, field_name, None)
        if isinstance(raw, tuple) and all(isinstance(point, ScreenPoint) for point in raw):
            return raw
    return ()


def _route_line(route: object | None) -> str | None:
    if route is None:
        return None
    actual = getattr(route, "actual_progress_tiles", None)
    actual_text = "-" if actual is None else f"{actual:+.1f}"
    rejections = getattr(route, "candidate_rejections", ())
    rejection_text = "-"
    if isinstance(rejections, tuple) and rejections:
        first = rejections[0]
        step_id = str(getattr(first, "step_id", "-"))[:40]
        codes = getattr(first, "rejection_codes", ())
        first_code = (
            str(codes[0])[:40]
            if isinstance(codes, tuple) and codes
            else "unknown"
        )
        rejection_text = f"{len(rejections)} ({step_id}:{first_code})"
    return (
        f"route: progress {getattr(route, 'progress_tiles', 0.0):.1f} | "
        f"target {getattr(route, 'selected_step_id', None) or '-'} | "
        f"request {getattr(route, 'requested_distance_tiles', 0.0):.1f} tiles | "
        f"actual {actual_text} | skipped "
        f"{len(getattr(route, 'skipped_guidance_points', ()))} | "
        f"deviation {getattr(route, 'lateral_deviation_tiles', 0.0):.1f} | "
        f"rejected {rejection_text}"
    )


def _camera_line(camera: object | None) -> str | None:
    if camera is None:
        return None
    context = str(getattr(camera, "framing_context", "interaction"))[:32]
    classification = str(getattr(camera, "classification", "-"))[:32]
    action = str(getattr(camera, "action", "-"))[:32]
    bias = str(getattr(camera, "route_direction_bias", "-"))[:32]
    correction = float(getattr(camera, "correction_distance_px", 0.0))
    clearance = getattr(camera, "edge_clearance_px", None)
    clearance_text = "-"
    if isinstance(clearance, (int, float)) and not isinstance(clearance, bool):
        clearance_text = f"{float(clearance):.1f}"
    required_margin = getattr(camera, "required_edge_margin_px", 0)
    attempt = getattr(camera, "correction_attempt", 0)
    limit = getattr(camera, "correction_limit", 0)
    cumulative = getattr(camera, "cumulative_hold_millis", 0)
    state = getattr(camera, "acquisition_state", "idle")
    state_text = str(getattr(state, "value", state))[:32]
    locked_target = str(getattr(camera, "locked_target_key", None) or "-")[:40]
    capability_max = getattr(camera, "capability_max_hold_millis", 250)
    response_samples = getattr(camera, "response_sample_count", 0)
    return (
        f"camera: {context} {classification} -> {action} "
        f"{getattr(camera, 'hold_millis', 0)} ms | "
        f"correction {correction:.1f} px | "
        f"clearance {clearance_text}/{required_margin} px | "
        f"attempt {attempt}/{limit} | cumulative {cumulative} ms | "
        f"episode {state_text} target {locked_target} | "
        f"cap {capability_max} ms model {response_samples} | "
        f"bias {bias}"
    )


def _targeting_line(
    targeting: object | None, *, geometry_allowed: bool
) -> str | None:
    if targeting is None:
        return None
    if not geometry_allowed:
        return "aim: geometry suppressed"
    selected = getattr(targeting, "selected_point", None)
    selected_text = (
        f"{selected.x},{selected.y}" if isinstance(selected, ScreenPoint) else "-"
    )
    return (
        f"aim: {getattr(targeting, 'geometry_source', '-')} | candidates "
        f"{len(getattr(targeting, 'candidate_points', ()))} | selected {selected_text} | "
        f"score {getattr(targeting, 'selected_score', 0.0):.3f} | "
        f"seed {getattr(targeting, 'seed', '-')}"
    )


def _pointer_line(frame: EngineFrame) -> str | None:
    receipt = frame.last_execution_receipt
    motion = receipt.pointer_motion if receipt is not None else None
    if motion is None or motion.plan_count <= 0:
        return None
    correction = "-"
    if motion.requested_target is not None and motion.settled_target is not None:
        correction = (
            f"{motion.settled_target.x - motion.requested_target.x:+d},"
            f"{motion.settled_target.y - motion.requested_target.y:+d}"
        )
    return (
        f"pointer: {motion.context or '-'} {motion.style or '-'} | "
        f"{motion.direct_distance_px:.1f} px / "
        f"{motion.planned_duration_seconds:.3f} s | "
        f"steps {motion.executed_step_count}/{motion.planned_step_count} | "
        f"correction {correction}"
    )


def _timing_line(timing: object | None) -> str | None:
    if timing is None:
        return None
    return (
        f"timing: pre {getattr(timing, 'pre_move_delay_seconds', 0.0):.3f} | "
        f"settle {getattr(timing, 'settle_delay_seconds', 0.0):.3f} | "
        f"click {getattr(timing, 'pre_click_delay_seconds', 0.0):.3f} | "
        f"post {getattr(timing, 'post_action_delay_seconds', 0.0):.3f} | "
        f"route {getattr(timing, 'route_pause_seconds', 0.0):.3f} s"
    )


def configure_passive_window(
    hwnd: int,
    bounds: ScreenBounds,
    *,
    native: Any | None = None,
) -> PassiveWindowProof:
    """Apply and verify click-through/no-activate Win32 properties."""

    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        raise ValueError("hwnd must be a positive integer")
    if not isinstance(bounds, ScreenBounds):
        raise TypeError("bounds must be ScreenBounds")
    adapter = native or _Win32Adapter()
    host_hwnd = int(adapter.get_root_window(hwnd))
    if host_hwnd <= 0:
        raise RuntimeError("passive overlay top-level window could not be resolved")
    current = int(adapter.get_extended_style(host_hwnd))
    adapter.set_extended_style(host_hwnd, current | PASSIVE_EX_STYLE_MASK)
    applied = int(adapter.get_extended_style(host_hwnd))
    proof = PassiveWindowProof(
        hwnd=host_hwnd,
        extended_style=applied,
        click_through=bool(applied & WS_EX_TRANSPARENT),
        non_focusable=bool(applied & WS_EX_NOACTIVATE),
        tool_window=bool(applied & WS_EX_TOOLWINDOW),
        layered=bool(applied & WS_EX_LAYERED),
    )
    if not proof.valid:
        raise RuntimeError("passive overlay window styles could not be verified")
    adapter.position_topmost_no_activate(host_hwnd, bounds)
    adapter.show_no_activate(host_hwnd)
    return proof


class _Win32Adapter:
    GWL_EXSTYLE = -20

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("the diagnostic overlay is supported only on Windows")
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if hasattr(self._user32, "GetWindowLongPtrW"):
            self._get = self._user32.GetWindowLongPtrW
            self._set = self._user32.SetWindowLongPtrW
            long_pointer = ctypes.c_ssize_t
        else:
            self._get = self._user32.GetWindowLongW
            self._set = self._user32.SetWindowLongW
            long_pointer = ctypes.c_long
        self._get.argtypes = (wintypes.HWND, ctypes.c_int)
        self._get.restype = long_pointer
        self._set.argtypes = (wintypes.HWND, ctypes.c_int, long_pointer)
        self._set.restype = long_pointer
        self._user32.SetWindowPos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        self._user32.SetWindowPos.restype = wintypes.BOOL
        self._user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self._user32.GetAncestor.restype = wintypes.HWND

    def get_root_window(self, hwnd: int) -> int:
        return int(self._user32.GetAncestor(hwnd, GA_ROOT) or 0)

    def get_extended_style(self, hwnd: int) -> int:
        return int(self._get(hwnd, self.GWL_EXSTYLE))

    def set_extended_style(self, hwnd: int, style: int) -> None:
        self._set(hwnd, self.GWL_EXSTYLE, style)

    def position_topmost_no_activate(self, hwnd: int, bounds: ScreenBounds) -> None:
        ok = self._user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            bounds.x,
            bounds.y,
            bounds.width,
            bounds.height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        if not ok:
            raise RuntimeError("overlay SetWindowPos failed")

    def show_no_activate(self, hwnd: int) -> None:
        self._user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)


class DebugOverlay:
    """Optional passive Tk drawing host over the latest immutable EngineFrame."""

    def __init__(
        self,
        publisher: EngineFramePublisher,
        *,
        show_rejected: bool = False,
        poll_milliseconds: int = 100,
        presentation_provider: Callable[[], object] | None = None,
        bound_run_id: str | None = None,
    ) -> None:
        if not isinstance(publisher, EngineFramePublisher):
            raise TypeError("publisher must be EngineFramePublisher")
        if not isinstance(show_rejected, bool):
            raise TypeError("show_rejected must be bool")
        if (
            not isinstance(poll_milliseconds, int)
            or isinstance(poll_milliseconds, bool)
            or not 25 <= poll_milliseconds <= 2_000
        ):
            raise ValueError("poll_milliseconds must be between 25 and 2000")
        self._publisher = publisher
        self._show_rejected = show_rejected
        self._poll_milliseconds = poll_milliseconds
        self._presentation_provider = presentation_provider
        self._bound_run_id = bound_run_id
        self._stop = threading.Event()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    def start(self, *, timeout_seconds: float = 3.0) -> None:
        if os.name != "nt":
            raise RuntimeError("the diagnostic overlay is supported only on Windows")
        if self._thread is not None:
            raise RuntimeError("diagnostic overlay is already started")
        self._thread = threading.Thread(
            target=self._run,
            name="osrs-diagnostic-overlay",
            daemon=True,
        )
        self._thread.start()
        if not self._started.wait(timeout_seconds):
            self._stop.set()
            self._thread.join(min(1.0, max(0.0, timeout_seconds)))
            raise RuntimeError("diagnostic overlay did not initialize in time")
        if self._error is not None:
            raise RuntimeError(f"diagnostic overlay failed: {self._error}") from self._error

    def stop(self, *, timeout_seconds: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout_seconds)
            if thread.is_alive():
                raise RuntimeError("diagnostic overlay did not stop in time")
        self._thread = None
        if self._error is not None:
            raise RuntimeError(
                f"diagnostic overlay runtime failed: {self._error}"
            ) from self._error

    def _run(self) -> None:
        root: Any | None = None
        canvas: Any | None = None
        poll: Any | None = None
        try:
            _enable_per_monitor_dpi_awareness()
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.configure(background=BACKGROUND_COLOR)
            root.wm_attributes("-topmost", True)
            root.wm_attributes("-transparentcolor", BACKGROUND_COLOR)
            canvas = tk.Canvas(
                root,
                highlightthickness=0,
                borderwidth=0,
                background=BACKGROUND_COLOR,
            )
            canvas.pack(fill="both", expand=True)
            root.update_idletasks()
            hwnd = int(root.winfo_id())
            initial = self._publisher.latest()
            bounds = (
                initial.observation.canvas_bounds
                if initial is not None and initial.observation is not None
                else ScreenBounds(0, 0, 1, 1)
            )
            root.geometry(
                f"{bounds.width}x{bounds.height}+{bounds.x}+{bounds.y}"
            )
            configure_passive_window(hwnd, bounds)
            root.deiconify()
            last_sequence = 0

            def poll() -> None:
                nonlocal last_sequence, bounds
                try:
                    if self._stop.is_set():
                        # Leave the callback before destroying Tcl. Tk's
                        # registered callback wrapper can then unregister on
                        # its owning thread and mainloop returns normally.
                        root.quit()
                        return
                    frame = self._publisher.latest()
                    if frame is not None:
                        presentation = (
                            self._presentation_provider()
                            if self._presentation_provider is not None
                            else None
                        )
                        scene = build_overlay_scene(
                            frame,
                            show_rejected=self._show_rejected,
                            presentation=presentation,
                            bound_run_id=self._bound_run_id,
                        )
                        if scene.canvas_bounds is not None:
                            bounds = scene.canvas_bounds
                            root.geometry(
                                f"{bounds.width}x{bounds.height}+{bounds.x}+{bounds.y}"
                            )
                            configure_passive_window(hwnd, bounds)
                        self._render(canvas, scene, bounds)
                        last_sequence = frame.sequence
                    root.after(self._poll_milliseconds, poll)
                except Exception as error:  # never leave frozen stale evidence visible
                    self._error = error
                    try:
                        canvas.delete("all")
                        root.withdraw()
                        root.destroy()
                    except Exception:
                        pass

            self._started.set()
            root.after(0, poll)
            root.mainloop()
        except Exception as error:  # startup/runtime failure is reported to caller
            self._error = error
            if root is not None:
                try:
                    root.withdraw()
                    root.destroy()
                except Exception:
                    pass
            self._started.set()
        finally:
            # Tk owns thread-affine Tcl async handlers. Destroy after mainloop
            # has returned, then release the root/canvas/callback references on
            # this same host thread rather than at interpreter shutdown.
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
            poll = None
            canvas = None
            root = None

    @staticmethod
    def _render(canvas: Any, scene: OverlayScene, canvas_bounds: ScreenBounds) -> None:
        canvas.delete("all")
        for polyline in scene.polylines:
            if len(polyline.points) < 2:
                continue
            coordinates = tuple(
                coordinate
                for point in polyline.points
                for coordinate in (
                    point.x - canvas_bounds.x,
                    point.y - canvas_bounds.y,
                )
            )
            canvas.create_line(
                *coordinates,
                fill=polyline.color,
                width=polyline.width,
                smooth=polyline.smooth,
                splinesteps=24 if polyline.smooth else 12,
            )
            if polyline.label:
                first = polyline.points[0]
                canvas.create_text(
                    first.x - canvas_bounds.x + 2,
                    first.y - canvas_bounds.y - 2,
                    anchor="sw",
                    fill=polyline.color,
                    text=polyline.label,
                    font=("Segoe UI", 8, "bold"),
                )
        for rectangle in scene.rectangles:
            bounds = rectangle.bounds
            left = bounds.x - canvas_bounds.x
            top = bounds.y - canvas_bounds.y
            right = left + bounds.width
            bottom = top + bounds.height
            canvas.create_rectangle(
                left,
                top,
                right,
                bottom,
                outline=rectangle.color,
                width=2,
            )
            canvas.create_text(
                left + 2,
                max(2, top - 2),
                anchor="sw",
                fill=rectangle.color,
                text=rectangle.label,
                font=("Segoe UI", 8, "bold"),
            )
        for point in scene.points:
            x = point.point.x - canvas_bounds.x
            y = point.point.y - canvas_bounds.y
            canvas.create_oval(
                x - point.radius,
                y - point.radius,
                x + point.radius,
                y + point.radius,
                outline=point.color,
                fill=point.color,
                width=1,
            )
            if point.label:
                canvas.create_text(
                    x + point.radius + 2,
                    y,
                    anchor="w",
                    fill=point.color,
                    text=point.label,
                    font=("Segoe UI", 8, "bold"),
                )
        canvas.create_text(
            8,
            8,
            anchor="nw",
            fill=TEXT_COLOR,
            text="\n".join(scene.text_lines),
            font=("Consolas", 9),
        )


def _enable_per_monitor_dpi_awareness() -> None:
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return
    if os.name != "nt":
        raise RuntimeError("the diagnostic overlay is supported only on Windows")
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if setter is not None:
            bits = ctypes.sizeof(ctypes.c_void_p) * 8
            per_monitor_v2 = ctypes.c_void_p((-4) & ((1 << bits) - 1))
            setter(per_monitor_v2)
        else:
            user32.SetProcessDPIAware()
    except Exception as error:  # noqa: BLE001
        raise RuntimeError(f"overlay DPI awareness failed: {error}") from error
    _DPI_AWARENESS_SET = True


def _current_target_bounds(
    frame: EngineFrame, target: TargetEvidence | None
) -> ScreenBounds | None:
    observation = frame.observation
    if target is None or observation is None or observation.canvas_bounds is None:
        return None
    if (
        target.source_tick != observation.source_tick
        or not target.geometry_frame_id
        or target.geometry_frame_id != observation.geometry_frame_id
    ):
        return None
    bounds = target.bounds
    if bounds is None and target.point is not None:
        bounds = ScreenBounds(target.point.x - 4, target.point.y - 4, 9, 9)
    if bounds is None:
        return None
    canvas = observation.canvas_bounds
    corners = (
        bounds.center,
        ScreenPoint(bounds.x, bounds.y),
        ScreenPoint(
            bounds.x + bounds.width - 1,
            bounds.y + bounds.height - 1,
        ),
    )
    return bounds if all(canvas.contains(point) for point in corners) else None


def _frame_age_seconds(frame: EngineFrame, now: datetime) -> float:
    observation = frame.observation
    if observation is None:
        return float("inf")
    return _timestamp_age_seconds(observation.captured_at, now)


def _timestamp_age_seconds(stamp: datetime, now: datetime) -> float:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return max(0.0, (current - stamp).total_seconds())


def _frame_geometry_is_live(frame: EngineFrame, now: datetime) -> bool:
    observation = frame.observation
    if observation is None or frame.stage is EngineStage.TERMINAL:
        return False
    return bool(
        observation.loaded_scene
        and observation.fresh
        and observation.cache_wall_clock_fresh
        and observation.source_coherent
        and _frame_age_seconds(frame, now)
        <= observation.max_source_age_millis / 1_000.0
    )


def _camera_geometry_is_current(
    frame: EngineFrame,
    camera: object | None,
    *,
    legacy_geometry_allowed: bool,
) -> bool:
    if camera is None:
        return False
    source_tick = getattr(camera, "source_tick", None)
    geometry_frame_id = getattr(camera, "geometry_frame_id", None)
    if source_tick is None and geometry_frame_id is None:
        return legacy_geometry_allowed
    observation = frame.observation
    return bool(
        observation is not None
        and source_tick == observation.source_tick
        and isinstance(geometry_frame_id, str)
        and bool(geometry_frame_id)
        and geometry_frame_id == observation.geometry_frame_id
    )


def _presentation_state_text(presentation: object | None) -> str | None:
    if presentation is None:
        return None
    state = getattr(presentation, "state", None)
    value = getattr(state, "value", state)
    return str(value).upper() if value is not None else None


def _overlay_banner(
    presentation_state: str | None,
    age_seconds: float,
    geometry_allowed: bool,
) -> str:
    state = presentation_state or ("LIVE" if geometry_allowed else "STALE")
    if state == "DISCONNECTED":
        return f"DISCONNECTED — last live frame was {age_seconds:.1f} seconds ago"
    if state == "STALE":
        return f"STALE — last live frame was {age_seconds:.1f} seconds ago"
    if state in {"COMPLETE", "BLOCKED", "SAFE_STOPPED", "ERROR", "TERMINAL"}:
        return f"{state} — target geometry cleared"
    if not geometry_allowed:
        return f"{state} — informational text only"
    return f"{state} — live EngineFrame"


def _target_label(target: TargetEvidence) -> str:
    return f"{target.name}#{target.object_id} {target.action}"


def _point_text(target: TargetEvidence) -> str:
    return (
        "no-point"
        if target.point is None
        else f"{target.point.x},{target.point.y}"
    )


def _binding_line(frame: EngineFrame) -> str:
    task = frame.task
    binding = f"definition: {task.definition_id or '-'} | profile: {task.profile_id or '-'}"
    if task.progress is None:
        return binding
    return (
        f"{binding} | {task.progress.label}: "
        f"{task.progress.current}/{task.progress.total}"
    )


def _safety_line(frame: EngineFrame) -> str:
    if not frame.safety_checks:
        return "safety: no execution checks"
    last = frame.safety_checks[-1]
    return f"safety: {last.stage}/{last.code} {'PASS' if last.allowed else 'FAIL'}"


def _verification_line(frame: EngineFrame) -> str:
    pending = frame.pending_verification
    return (
        "pending verification: none"
        if pending is None
        else f"pending verification: {pending.kind.value} <= tick {pending.deadline_tick}"
    )


def _outcome_line(frame: EngineFrame) -> str:
    verification = frame.last_verification
    if verification is None:
        return "last outcome: none"
    if verification.outcome is not None:
        return f"last outcome: {verification.outcome.kind.value}"
    return f"last verification: {verification.status.value}/{verification.reason}"
