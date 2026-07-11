from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from .engine_frame import EngineFrame, EngineFramePublisher
from .model import ScreenBounds, ScreenPoint
from .task_contract import TargetEvidence


SELECTED_COLOR = "#38d267"
ELIGIBLE_COLOR = "#e8ad32"
REJECTED_COLOR = "#e44848"
TEXT_COLOR = "#f1f4f8"
BACKGROUND_COLOR = "#010203"

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
class OverlayScene:
    source_sequence: int
    canvas_bounds: ScreenBounds | None
    rectangles: tuple[OverlayRectangle, ...]
    text_lines: tuple[str, ...]


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
    frame: EngineFrame, *, show_rejected: bool = False
) -> OverlayScene:
    """Format only the evidence already published by the runtime."""

    if not isinstance(frame, EngineFrame):
        raise TypeError("frame must be EngineFrame")
    if not isinstance(show_rejected, bool):
        raise TypeError("show_rejected must be bool")

    rectangles: list[OverlayRectangle] = []
    selected = frame.selected_target
    selected_key = selected.key if selected is not None else None
    selected_bounds = _current_target_bounds(frame, selected)
    if selected is not None and selected_bounds is not None:
        rectangles.append(
            OverlayRectangle(selected_bounds, SELECTED_COLOR, _target_label(selected))
        )
    for target in frame.eligible_targets:
        if target.key == selected_key:
            continue
        bounds = _current_target_bounds(frame, target)
        if bounds is not None:
            rectangles.append(
                OverlayRectangle(bounds, ELIGIBLE_COLOR, _target_label(target))
            )
    if show_rejected:
        for rejected in frame.rejected_targets:
            bounds = _current_target_bounds(frame, rejected.target)
            if bounds is not None:
                label = (
                    f"{_target_label(rejected.target)} "
                    f"[{','.join(rejected.rejection_codes)}]"
                )
                rectangles.append(
                    OverlayRectangle(bounds, REJECTED_COLOR, label)
                )

    task = frame.task
    text = [
        f"{task.task_id} | {task.state} | {task.status.value}",
        _binding_line(frame),
        (
            "target: none"
            if selected is None
            else (
                f"target: {_target_label(selected)} @ {_point_text(selected)}"
                if selected_bounds is not None
                else f"target: {_target_label(selected)} @ geometry suppressed"
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
    return OverlayScene(
        source_sequence=frame.sequence,
        canvas_bounds=(
            frame.observation.canvas_bounds
            if frame.observation is not None
            else None
        ),
        rectangles=tuple(rectangles),
        text_lines=tuple(text),
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
                    if frame is not None and frame.sequence != last_sequence:
                        scene = build_overlay_scene(
                            frame, show_rejected=self._show_rejected
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
