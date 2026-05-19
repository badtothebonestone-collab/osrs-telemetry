from __future__ import annotations

import time
from typing import Any


class PyAutoGuiBackend:
    name = "pyautogui"

    def __init__(
        self,
        *,
        focus_runelite: bool = False,
        window_title_filter: str = "RuneLite",
        pause_seconds: float = 0.0,
    ) -> None:
        self.focus_runelite = focus_runelite
        self.window_title_filter = window_title_filter
        self.pause_seconds = max(0.0, float(pause_seconds))
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

    def current_position(self) -> tuple[int, int]:
        pyautogui = self._load_pyautogui()
        point = pyautogui.position()
        return int(point.x), int(point.y)

    def move_and_click(self, plan: Any, *, button: str = "left") -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        last_time = 0
        for point in plan.points:
            duration = max(0.0, (int(point.timestamp_ms or 0) - last_time) / 1000.0)
            pyautogui.moveTo(int(point.x), int(point.y), duration=duration)
            last_time = int(point.timestamp_ms or 0)
        pyautogui.click(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def press(self, key: str) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.press(key)

    def hotkey(self, *keys: str) -> None:
        pyautogui = self._load_pyautogui()
        self.focus_window()
        pyautogui.hotkey(*keys)
