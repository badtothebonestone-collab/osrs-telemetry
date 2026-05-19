from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

import bootstrap_vision
import bootstrap_window
from input_control.backend_pyautogui import PyAutoGuiBackend


SCHEMA = "bootstrap_template_capture.v1"
DEFAULT_OUTPUT_DIR = Path("telemetry-viewer") / "assets" / "bootstrap_templates"


def parse_region(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in str(value or "").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be x,y,w,h")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("region values must be integers") from error
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("region width and height must be positive")
    return x, y, width, height


def _default_screenshot() -> Any:
    try:
        from PIL import ImageGrab

        return ImageGrab.grab(all_screens=True)
    except Exception:  # noqa: BLE001
        pass
    import pyautogui  # type: ignore

    return pyautogui.screenshot()


def _default_window_finder(filters: list[str]) -> dict[str, Any]:
    return bootstrap_window.find_and_prepare_window(filters, execute=True)


def _default_focus() -> dict[str, Any]:
    backend = PyAutoGuiBackend(focus_runelite=True, window_title_filter="RuneLite")
    try:
        focused = bool(backend.focus_window())
        return {"focused": focused, "warnings": [] if focused else ["window focus not confirmed"]}
    except Exception as error:  # noqa: BLE001
        return {"focused": False, "warnings": [f"window focus failed: {type(error).__name__}: {error}"]}


def _template_filename(name: str) -> str | None:
    return bootstrap_vision.SUPPORTED_TEMPLATE_FILES.get(name)


def capture_template(
    *,
    name: str,
    output_dir: Path,
    region: tuple[int, int, int, int] | None,
    overwrite: bool,
    screenshot_func: Callable[[], Any] = _default_screenshot,
    window_finder: Callable[[list[str]], dict[str, Any]] = _default_window_finder,
    focus_func: Callable[[], dict[str, Any]] = _default_focus,
    window_title_filter: str = "RuneLite",
) -> dict[str, Any]:
    warnings: list[str] = []
    failures: list[str] = []
    filename = _template_filename(name)
    if filename is None:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "templateName": name,
            "outputPath": None,
            "region": region,
            "window": {},
            "warnings": [],
            "failures": [f"unsupported template name: {name}"],
        }
    if region is None:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "templateName": name,
            "outputPath": str(output_dir / filename),
            "region": None,
            "window": {},
            "warnings": [],
            "failures": ["region required; pass --region x,y,w,h or --interactive"],
        }
    output_path = output_dir / filename
    if output_path.exists() and not overwrite:
        return {
            "schema": SCHEMA,
            "status": "FAIL",
            "templateName": name,
            "outputPath": str(output_path),
            "region": region,
            "window": {},
            "warnings": [],
            "failures": ["template exists; pass --overwrite to replace it"],
        }
    filters = bootstrap_window.unique_title_filters(window_title_filter)
    window = window_finder(filters)
    warnings.extend(str(item) for item in window.get("warnings") or [])
    focus = focus_func()
    warnings.extend(str(item) for item in focus.get("warnings") or [])
    try:
        screenshot = screenshot_func()
        x, y, width, height = region
        cropped = screenshot.crop((x, y, x + width, y + height))
        output_dir.mkdir(parents=True, exist_ok=True)
        cropped.save(str(output_path))
    except Exception as error:  # noqa: BLE001
        failures.append(f"capture failed: {type(error).__name__}: {error}")
    return {
        "schema": SCHEMA,
        "status": "FAIL" if failures else "PASS",
        "templateName": name,
        "outputPath": str(output_path),
        "region": region,
        "window": {**window, **focus},
        "warnings": list(dict.fromkeys(warnings)),
        "failures": failures,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one RuneLite bootstrap button template.")
    parser.add_argument("--name", required=True, choices=sorted(bootstrap_vision.SUPPORTED_TEMPLATE_FILES))
    parser.add_argument("--window-title-filter", default="RuneLite")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--region", type=parse_region)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def region_from_interactive(input_func: Callable[[str], str] = input) -> tuple[int, int, int, int]:
    text = input_func("Enter template region as x,y,w,h: ")
    return parse_region(text)


def format_human(payload: dict[str, Any]) -> str:
    lines = [
        f"BOOTSTRAP TEMPLATE CAPTURE - {payload.get('status') or 'UNKNOWN'}",
        f"  template: {payload.get('templateName')}",
        f"  output: {payload.get('outputPath') or 'none'}",
        f"  region: {payload.get('region') or 'none'}",
        f"  window: {payload.get('window', {}).get('matchedWindowTitle') or 'unknown'}",
        "Warnings:",
    ]
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    lines.extend(f"  WARN: {warning}" for warning in warnings) if warnings else lines.append("  none")
    failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
    if failures:
        lines.append("Failures:")
        lines.extend(f"  FAIL: {failure}" for failure in failures)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    region = args.region
    if args.interactive and region is None:
        print("Move/size the RuneLite window so the button is visible, then enter the screen crop region.")
        region = region_from_interactive()
    payload = capture_template(
        name=args.name,
        output_dir=Path(args.output_dir),
        region=region,
        overwrite=bool(args.overwrite),
        window_title_filter=args.window_title_filter,
    )
    print(json.dumps(payload, indent=2, sort_keys=False) if args.json else format_human(payload), end="")
    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
