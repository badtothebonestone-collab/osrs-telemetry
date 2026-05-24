from __future__ import annotations

import time
from typing import Any


def scale_canvas_point_to_screen(
    point: dict[str, Any],
    *,
    origin: tuple[int, int],
    client_size: tuple[int, int],
    canvas_size: tuple[int, int],
) -> dict[str, int]:
    canvas_width = max(1, int(canvas_size[0]))
    scale = max(1.0, float(client_size[0]) / float(canvas_width))
    return {
        "x": int(round(float(origin[0]) + float(point["x"]) * scale)),
        "y": int(round(float(origin[1]) + float(point["y"]) * scale)),
    }


class PyAutoGuiBackend:
    name = "pyautogui"

    def __init__(
        self,
        *,
        focus_runelite: bool = False,
        window_title_filter: str = "RuneLite",
        pause_seconds: float = 0.0,
        canvas_size: tuple[int, int] = (765, 503),
    ) -> None:
        self.focus_runelite = focus_runelite
        self.window_title_filter = window_title_filter
        self.pause_seconds = max(0.0, float(pause_seconds))
        self.canvas_size = canvas_size
        self._pyautogui = None

    def _load_pyautogui(self):
        if self._pyautogui is not None:
            return self._pyautogui
        try:
            import pyautogui  # type: ignore
        except ImportError as error:
            raise RuntimeError("pyautogui is not installed. Install with: pip install pyautogui pygetwindow") from error
        pyautogui.PAUSE = self.pause_seconds
        self._pyautogui = pyautogui
        return pyautogui

    def focus_window(self) -> bool:
        if not self.focus_runelite:
            return False
        handle = self._find_window_handle()
        if handle:
            try:
                import ctypes

                ctypes.windll.user32.ShowWindow(handle, 9)
                ctypes.windll.user32.SetForegroundWindow(handle)
                time.sleep(0.1)
                return True
            except Exception:  # noqa: BLE001
                pass
        try:
            import pygetwindow  # type: ignore
        except ImportError:
            return False
        matches = [
            window
            for window in pygetwindow.getAllWindows()
            if self.window_title_filter.lower() in (window.title or "").lower()
        ]
        if not matches:
            return False
        matches[0].activate()
        time.sleep(0.1)
        return True

    def _find_window_handle(self) -> int | None:
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:  # noqa: BLE001
            return None
        user32 = ctypes.windll.user32
        handles: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd, _lparam):  # type: ignore[no-untyped-def]
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if self.window_title_filter.lower() in buffer.value.lower():
                handles.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(enum_proc, 0)
        return handles[0] if handles else None

    def canvas_client_geometry(self) -> tuple[tuple[int, int], tuple[int, int]]:
        handle = self._find_window_handle()
        if not handle:
            raise RuntimeError(f"window matching title filter not found: {self.window_title_filter}")
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(f"Windows client coordinate conversion unavailable: {error}") from error

        point = wintypes.POINT(0, 0)
        if not ctypes.windll.user32.ClientToScreen(handle, ctypes.byref(point)):
            raise RuntimeError("ClientToScreen failed for RuneLite window")
        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(handle, ctypes.byref(rect)):
            raise RuntimeError("GetClientRect failed for RuneLite window")
        origin = (int(point.x), int(point.y))
        client_size = (max(1, int(rect.right - rect.left)), max(1, int(rect.bottom - rect.top)))
        return origin, client_size

    def canvas_origin_screen(self) -> tuple[int, int]:
        origin, _client_size = self.canvas_client_geometry()
        return origin

    def canvas_to_screen_point(self, point: dict[str, Any]) -> dict[str, int]:
        origin, client_size = self.canvas_client_geometry()
        return scale_canvas_point_to_screen(
            point,
            origin=origin,
            client_size=client_size,
            canvas_size=self.canvas_size,
        )

    def current_position(self) -> tuple[int, int]:
        pyautogui = self._load_pyautogui()
        point = pyautogui.position()
        return int(point.x), int(point.y)

    def move(self, plan: Any) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        last_time = 0
        for point in plan.points:
            duration = max(0.0, (int(point.timestamp_ms or 0) - last_time) / 1000.0)
            pyautogui.moveTo(int(point.x), int(point.y), duration=duration)
            last_time = int(point.timestamp_ms or 0)

    def click_at(self, x: int, y: int, *, button: str = "left", hold_ms: int = 0) -> None:
        pyautogui = self._load_pyautogui()
        pyautogui.moveTo(int(x), int(y), duration=0)
        if hold_ms > 0:
            pyautogui.mouseDown(button=button)
            time.sleep(max(0.0, float(hold_ms) / 1000.0))
            pyautogui.mouseUp(button=button)
            return
        pyautogui.click(button=button)

    def move_and_click(self, plan: Any, *, button: str = "left") -> None:
        self.move(plan)
        self.click_at(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def press(self, key: str) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.press(key)

    def key_down(self, key: str) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.keyDown(key)

    def key_up(self, key: str) -> None:
        pyautogui = self._load_pyautogui()
        pyautogui.keyUp(key)

    def mouse_down(self, *, button: str = "left") -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.mouseDown(button=button)

    def mouse_up(self, *, button: str = "left") -> None:
        pyautogui = self._load_pyautogui()
        pyautogui.mouseUp(button=button)

    def move_relative(self, dx: int, dy: int, *, duration_ms: int = 0) -> None:
        pyautogui = self._load_pyautogui()
        pyautogui.moveRel(int(dx), int(dy), duration=max(0.0, float(duration_ms) / 1000.0))

    def hotkey(self, *keys: str) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.hotkey(*keys)
