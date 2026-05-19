from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SUPPORTED_TEMPLATE_FILES = {
    "play_now": "play_now.png",
    "continue": "continue.png",
    "click_here_to_play": "click_here_to_play.png",
}


@dataclass(frozen=True)
class VisionButtonCandidate:
    name: str
    source: str
    screen_point: dict[str, int] | None
    canvas_point: dict[str, int] | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "screenPoint": self.screen_point,
            "canvasPoint": self.canvas_point,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidateMethod": "template" if self.source == "template" else self.source,
        }


def _default_screenshot() -> Any:
    import pyautogui  # type: ignore

    return pyautogui.screenshot()


def _default_locate(template: str, screenshot: Any, **kwargs: Any) -> Any:
    import pyautogui  # type: ignore

    return pyautogui.locateCenterOnScreen(template, **kwargs) if screenshot is None else pyautogui.locateCenter(template, screenshot, **kwargs)


def _point_from_located(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        return {"x": int(round(float(value[0]))), "y": int(round(float(value[1])))}
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    if x is not None and y is not None:
        return {"x": int(round(float(x))), "y": int(round(float(y)))}
    return None


def template_status(template_dir: Path, *, confidence: float = 0.8) -> dict[str, Any]:
    found: list[str] = []
    missing: list[str] = []
    for name, filename in SUPPORTED_TEMPLATE_FILES.items():
        if (template_dir / filename).exists():
            found.append(name)
        else:
            missing.append(name)
    return {
        "templateDir": str(template_dir),
        "supported": list(SUPPORTED_TEMPLATE_FILES),
        "found": found,
        "missing": missing,
        "confidence": float(confidence),
    }


def template_candidates(
    template_dir: Path,
    *,
    screenshot_func: Callable[[], Any] | None = None,
    locate_func: Callable[..., Any] | None = None,
    save_debug_screenshot: bool = False,
    confidence: float = 0.8,
) -> tuple[list[VisionButtonCandidate], list[str]]:
    warnings: list[str] = []
    existing: list[tuple[str, Path]] = []
    for name, filename in SUPPORTED_TEMPLATE_FILES.items():
        path = template_dir / filename
        if path.exists():
            existing.append((name, path))
    if not existing:
        return [], [f"templates not found in {template_dir}; using percent fallback"]
    screenshot = None
    try:
        screenshot = (screenshot_func or _default_screenshot)()
    except Exception as error:  # noqa: BLE001
        return [], [f"screenshot unavailable for template matching: {type(error).__name__}: {error}"]
    if save_debug_screenshot and hasattr(screenshot, "save"):
        screenshot.save(str(template_dir / "bootstrap_debug_screenshot.png"))
    locate = locate_func or _default_locate
    candidates: list[VisionButtonCandidate] = []
    for name, path in existing:
        try:
            located = locate(str(path), screenshot, confidence=float(confidence))
        except (TypeError, NotImplementedError):
            located = locate(str(path), screenshot)
        except Exception as error:  # noqa: BLE001
            warnings.append(f"template match failed for {path.name}: {type(error).__name__}: {error}")
            continue
        point = _point_from_located(located)
        if point:
            candidates.append(
                VisionButtonCandidate(
                    name=name,
                    source="template",
                    screen_point=point,
                    canvas_point=None,
                    confidence=float(confidence),
                    reason=f"matched template {path.name}",
                )
            )
    return candidates, warnings
