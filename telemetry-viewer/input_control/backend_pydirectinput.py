from __future__ import annotations

import time


class PyDirectInputBackend:
    name = "pydirectinput"

    def __init__(self, **_: object) -> None:
        self._pydirectinput = None

    def _load_pydirectinput(self):
        if self._pydirectinput is not None:
            return self._pydirectinput
        try:
            import pydirectinput  # type: ignore
        except ImportError as error:
            raise RuntimeError("pydirectinput is not installed. Install with: pip install pydirectinput") from error
        self._pydirectinput = pydirectinput
        return pydirectinput

    def current_position(self) -> tuple[int, int]:
        try:
            import pyautogui  # type: ignore
        except ImportError:
            return (0, 0)
        point = pyautogui.position()
        return int(point.x), int(point.y)

    def move(self, plan) -> None:
        pydirectinput = self._load_pydirectinput()
        for point in plan.points:
            pydirectinput.moveTo(int(point.x), int(point.y))

    def click_at(self, x: int, y: int, *, button: str = "left", hold_ms: int = 0) -> None:
        pydirectinput = self._load_pydirectinput()
        if hold_ms > 0:
            pydirectinput.mouseDown(int(x), int(y), button=button)
            time.sleep(max(0.0, float(hold_ms) / 1000.0))
            pydirectinput.mouseUp(int(x), int(y), button=button)
            return
        pydirectinput.click(int(x), int(y), button=button)

    def move_and_click(self, plan, *, button: str = "left") -> None:
        self.move(plan)
        self.click_at(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def press(self, key: str) -> None:
        self._load_pydirectinput().press(key)

    def key_down(self, key: str) -> None:
        self._load_pydirectinput().keyDown(key)

    def key_up(self, key: str) -> None:
        self._load_pydirectinput().keyUp(key)

    def mouse_down(self, *, button: str = "left") -> None:
        self._load_pydirectinput().mouseDown(button=button)

    def mouse_up(self, *, button: str = "left") -> None:
        self._load_pydirectinput().mouseUp(button=button)

    def move_relative(self, dx: int, dy: int, *, duration_ms: int = 0) -> None:
        pydirectinput = self._load_pydirectinput()
        try:
            pydirectinput.moveRel(int(dx), int(dy), duration=max(0.0, float(duration_ms) / 1000.0))
        except TypeError:
            pydirectinput.moveRel(int(dx), int(dy))
