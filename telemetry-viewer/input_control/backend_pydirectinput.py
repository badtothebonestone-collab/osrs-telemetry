from __future__ import annotations


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

    def move_and_click(self, plan, *, button: str = "left") -> None:
        pydirectinput = self._load_pydirectinput()
        for point in plan.points:
            pydirectinput.moveTo(int(point.x), int(point.y))
        pydirectinput.click(int(plan.click_point.x), int(plan.click_point.y), button=button)

    def press(self, key: str) -> None:
        self._load_pydirectinput().press(key)
