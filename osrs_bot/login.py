from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image, ImageGrab, ImageStat

from .arduino import ArduinoHIDBackend
from .model import Observation, ScreenBounds, ScreenPoint
from .observation import ObservationClient


MAX_LOGIN_CLICKS = 4
MAX_LOGIN_SECONDS = 180.0
TRANSITION_SECONDS = 15.0
CLIENT_MARGIN_PX = 8
SUPPORTED_SURFACES = ("play_now", "click_here_to_play", "disconnected_ok")
_TEMPLATE_SURFACES = ("play_now", "click_here_to_play")
TEMPLATE_DIR = Path(__file__).resolve().parent / "assets" / "login"
_TEMPLATE_FILES = {
    "play_now": "play_now.png",
    "click_here_to_play": "click_here_to_play.png",
}
_SEARCH_ZONES = {
    "play_now": (0.24, 0.30, 0.76, 0.68),
    "click_here_to_play": (0.18, 0.45, 0.82, 0.86),
}
_DPI_AWARENESS_SET = False


class _ObservationSource(Protocol):
    def fetch(self) -> Observation: ...


class LoginSafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuneLiteWindow:
    hwnd: int
    pid: int
    title: str
    client_bounds: ScreenBounds


@dataclass(frozen=True)
class LoginCandidate:
    name: str
    point: ScreenPoint
    match_bounds: ScreenBounds
    confidence: float


@dataclass(frozen=True)
class LoginClick:
    name: str
    point: ScreenPoint
    source_game_state: str
    source_tick: int
    stop_all_confirmed: bool
    disarm_confirmed: bool


@dataclass(frozen=True)
class LoginResult:
    status: str
    reason: str
    loaded_scene: bool
    elapsed_seconds: float
    clicks: tuple[LoginClick, ...] = ()

    @property
    def successful(self) -> bool:
        return self.status == "PASS" and self.loaded_scene

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["loadedScene"] = value.pop("loaded_scene")
        value["elapsedSeconds"] = round(value.pop("elapsed_seconds"), 3)
        for click in value["clicks"]:
            click["sourceGameState"] = click.pop("source_game_state")
            click["sourceTick"] = click.pop("source_tick")
            click["stopAllConfirmed"] = click.pop("stop_all_confirmed")
            click["disarmConfirmed"] = click.pop("disarm_confirmed")
        return value


def _bright(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red >= 165 and green >= 145 and blue >= 115 and (red + green + blue) >= 500


def _even_samples(points: list[tuple[int, int]], limit: int) -> tuple[tuple[int, int], ...]:
    if len(points) <= limit:
        return tuple(points)
    return tuple(points[index * len(points) // limit] for index in range(limit))


def _scaled_templates(template: Image.Image) -> tuple[Image.Image, ...]:
    rgb = template.convert("RGB")
    output: list[Image.Image] = []
    for scale in (0.80, 0.90, 1.00, 1.10, 1.15, 1.17, 1.20):
        size = (max(1, round(rgb.width * scale)), max(1, round(rgb.height * scale)))
        output.append(rgb if size == rgb.size else rgb.resize(size, Image.Resampling.BILINEAR))
    return tuple(output)


def _best_template_match(
    image: Image.Image,
    template: Image.Image,
    zone: tuple[int, int, int, int],
) -> tuple[int, int, int, int, float] | None:
    """Locate the white OSRS label shape without trusting background animation."""

    haystack = image.convert("RGB")
    hay_pixels = haystack.load()
    left, top, right, bottom = zone
    best: tuple[int, int, int, int, float] | None = None

    for needle in _scaled_templates(template):
        width, height = needle.size
        if width > right - left or height > bottom - top:
            continue
        needle_pixels = needle.load()
        positive = [
            (x, y)
            for y in range(height)
            for x in range(width)
            if _bright(needle_pixels[x, y])
        ]
        if len(positive) < 40:
            continue
        positive_samples = _even_samples(positive, 96)
        template_bright_ratio = len(positive) / (width * height)
        anchors = _even_samples(positive, 9)
        negative = [
            (x, y)
            for y in range(0, height, max(1, height // 20))
            for x in range(0, width, max(1, width // 32))
            if not _bright(needle_pixels[x, y])
        ]
        negative_samples = _even_samples(negative, 96)
        anchor_x, anchor_y = anchors[0]
        candidate_origins: set[tuple[int, int]] = set()
        for screen_y in range(top + anchor_y, bottom - height + anchor_y + 1):
            for screen_x in range(left + anchor_x, right - width + anchor_x + 1):
                if _bright(hay_pixels[screen_x, screen_y]):
                    candidate_origins.add((screen_x - anchor_x, screen_y - anchor_y))

        for x, y in candidate_origins:
            anchor_hits = sum(
                1 for px, py in anchors if _bright(hay_pixels[x + px, y + py])
            )
            if anchor_hits < len(anchors) - 1:
                continue
            positive_ratio = sum(
                1 for px, py in positive_samples if _bright(hay_pixels[x + px, y + py])
            ) / len(positive_samples)
            if positive_ratio < 0.86:
                continue
            negative_ratio = sum(
                1 for px, py in negative_samples if not _bright(hay_pixels[x + px, y + py])
            ) / max(1, len(negative_samples))
            # A solid bright rectangle satisfies every positive sample but is
            # not text. Require the dark gaps that define the glyph shape.
            if negative_ratio < 0.75:
                continue
            patch_bright_ratio = sum(
                1
                for patch_y in range(y, y + height)
                for patch_x in range(x, x + width)
                if _bright(hay_pixels[patch_x, patch_y])
            ) / (width * height)
            if not (
                template_bright_ratio * 0.55
                <= patch_bright_ratio
                <= template_bright_ratio * 1.65
            ):
                continue
            confidence = positive_ratio * 0.82 + negative_ratio * 0.18
            if confidence >= 0.90 and (best is None or confidence > best[4]):
                best = (x, y, width, height, confidence)
    return best


def _mean_luma(image: Image.Image) -> float:
    return float(ImageStat.Stat(image.convert("L")).mean[0])


def _disconnected_dialog_candidate(
    screenshot: Image.Image,
) -> LoginCandidate | None:
    """Retain the proven 5.5 idle-disconnect dialog geometry, narrowly."""

    width, height = screenshot.size
    center_x = round(width * 0.50)
    center_y = round(height * 0.61)
    dialog = screenshot.crop(
        (round(width * 0.25), round(height * 0.34),
         round(width * 0.75), round(height * 0.69))
    )
    button = screenshot.crop(
        (center_x - 120, center_y - 45, center_x + 120, center_y + 45)
    )
    top_border = screenshot.crop(
        (center_x - 120, center_y - 45, center_x + 120, center_y - 30)
    )
    bottom_border = screenshot.crop(
        (center_x - 120, center_y + 30, center_x + 120, center_y + 45)
    )
    if min(dialog.width, dialog.height, button.width, button.height) <= 0:
        return None

    dialog_luma = _mean_luma(dialog)
    button_luma = _mean_luma(button)
    border_luma = min(_mean_luma(top_border), _mean_luma(bottom_border))
    button_gray = button.convert("L")
    stats = ImageStat.Stat(button_gray)
    histogram = button_gray.histogram()
    bright_ratio = sum(histogram[165:]) / max(1, sum(histogram))
    detected = (
        28.0 <= dialog_luma <= 120.0
        and 20.0 <= button_luma <= 115.0
        and border_luma < button_luma + 10.0
        and (float(stats.stddev[0]) >= 12.0 or bright_ratio >= 0.004)
    )
    if not detected:
        return None
    return LoginCandidate(
        name="disconnected_ok",
        point=ScreenPoint(center_x, center_y),
        match_bounds=ScreenBounds(
            round(width * 0.40), round(height * 0.565),
            round(width * 0.20), round(height * 0.09),
        ),
        confidence=0.92,
    )


def detect_login_surfaces(
    screenshot: Image.Image,
    *,
    template_dir: Path = TEMPLATE_DIR,
) -> tuple[LoginCandidate, ...]:
    """Return only visually proven, already-authenticated RuneLite prompts."""

    if screenshot.width < 500 or screenshot.height < 400:
        return ()
    candidates: list[LoginCandidate] = []
    for name in _TEMPLATE_SURFACES:
        template_path = template_dir / _TEMPLATE_FILES[name]
        if not template_path.is_file():
            continue
        with Image.open(template_path) as opened:
            template = opened.convert("RGB")
        x1, y1, x2, y2 = _SEARCH_ZONES[name]
        zone = (
            round(screenshot.width * x1),
            round(screenshot.height * y1),
            round(screenshot.width * x2),
            round(screenshot.height * y2),
        )
        match = _best_template_match(screenshot, template, zone)
        if match is None:
            continue
        x, y, width, height, confidence = match
        candidates.append(
            LoginCandidate(
                name=name,
                point=ScreenPoint(x + width // 2, y + height // 2),
                match_bounds=ScreenBounds(x, y, width, height),
                confidence=confidence,
            )
        )
    if not candidates:
        disconnected = _disconnected_dialog_candidate(screenshot)
        if disconnected is not None:
            candidates.append(disconnected)
    return tuple(sorted(candidates, key=lambda item: item.confidence, reverse=True))


class _WinPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class _WinRect(ctypes.Structure):
    _fields_ = (
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    )


def _user32() -> Any:
    if os.name != "nt":
        raise LoginSafetyError("RuneLite login assistance is supported only on Windows")
    _enable_windows_dpi_awareness()
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _enable_windows_dpi_awareness() -> None:
    """Keep Win32 bounds, screenshots, and Arduino cursor coordinates aligned."""
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET or os.name != "nt":
        return
    _DPI_AWARENESS_SET = True
    try:
        bits = ctypes.sizeof(ctypes.c_void_p) * 8
        per_monitor_v2 = ctypes.c_void_p((-4) & ((1 << bits) - 1))
        setter = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
        if setter and setter(per_monitor_v2):
            return
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception as error:
        raise LoginSafetyError(f"Windows DPI awareness could not be established: {error}") from error


def find_runelite_window(expected_pid: int) -> RuneLiteWindow:
    user32 = _user32()
    matches: list[RuneLiteWindow] = []

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def collect(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value
        if "runelite" not in title.casefold():
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) != int(expected_pid):
            return True
        client = _WinRect()
        origin = _WinPoint(0, 0)
        if not user32.GetClientRect(hwnd, ctypes.byref(client)):
            return True
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return True
        bounds = ScreenBounds(
            int(origin.x),
            int(origin.y),
            int(client.right - client.left),
            int(client.bottom - client.top),
        )
        if bounds.width >= 500 and bounds.height >= 400:
            matches.append(RuneLiteWindow(int(hwnd), int(pid.value), title, bounds))
        return True

    user32.EnumWindows(collect, 0)
    if len(matches) != 1:
        raise LoginSafetyError(
            f"expected exactly one visible RuneLite window for telemetry PID {expected_pid}; found {len(matches)}"
        )
    return matches[0]


def focus_exact_window(window: RuneLiteWindow) -> bool:
    user32 = _user32()
    foreground = int(user32.GetForegroundWindow() or 0)
    if foreground != window.hwnd:
        user32.ShowWindow(window.hwnd, 5)
        user32.SetForegroundWindow(window.hwnd)
        time.sleep(0.15)
    foreground = int(user32.GetForegroundWindow() or 0)
    if foreground != window.hwnd:
        return False
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    return int(pid.value) == window.pid


def point_belongs_to_window(window: RuneLiteWindow, point: ScreenPoint) -> bool:
    user32 = _user32()
    hwnd = int(user32.WindowFromPoint(_WinPoint(point.x, point.y)) or 0)
    root = int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
    return root == window.hwnd


def capture_client(bounds: ScreenBounds) -> Image.Image:
    return ImageGrab.grab(
        bbox=(bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height),
        all_screens=True,
    ).convert("RGB")


class LoginPromptHelper:
    def __init__(
        self,
        observation_source: _ObservationSource,
        *,
        arduino_port: str,
        backend_factory: Callable[[], Any] | None = None,
        window_finder: Callable[[int], RuneLiteWindow] = find_runelite_window,
        focus_window: Callable[[RuneLiteWindow], bool] = focus_exact_window,
        point_owner: Callable[[RuneLiteWindow, ScreenPoint], bool] = point_belongs_to_window,
        screenshot: Callable[[ScreenBounds], Image.Image] = capture_client,
        detector: Callable[[Image.Image], tuple[LoginCandidate, ...]] = detect_login_surfaces,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 1.0,
        transition_seconds: float = TRANSITION_SECONDS,
    ) -> None:
        if not arduino_port:
            raise ValueError("arduino_port is required")
        self._observations = observation_source
        self._backend_factory = backend_factory or (
            lambda: ArduinoHIDBackend(port=arduino_port, serial_owner="osrs-login-helper")
        )
        self._window_finder = window_finder
        self._focus_window = focus_window
        self._point_owner = point_owner
        self._screenshot = screenshot
        self._detector = detector
        self._monotonic = monotonic
        self._sleep = sleep
        self._poll_seconds = max(0.05, float(poll_seconds))
        self._transition_seconds = max(0.1, float(transition_seconds))

    def run(
        self,
        *,
        max_clicks: int = MAX_LOGIN_CLICKS,
        timeout_seconds: float = MAX_LOGIN_SECONDS,
    ) -> LoginResult:
        click_limit = min(MAX_LOGIN_CLICKS, max(0, int(max_clicks)))
        runtime_limit = min(MAX_LOGIN_SECONDS, max(0.1, float(timeout_seconds)))
        started = self._monotonic()
        deadline = started + runtime_limit
        clicks: list[LoginClick] = []
        misses = 0
        loaded_proof: tuple[int, str | None, int] | None = None

        while self._monotonic() < deadline:
            try:
                observation = self._observations.fetch()
            except Exception as error:
                return self._result("ERROR", f"observation_unavailable: {type(error).__name__}: {error}", False, started, clicks)
            if observation.client_process_id is None:
                return self._result("BLOCKED", "telemetry_client_process_id_unavailable", False, started, clicks)
            if observation.game_state not in {"LOGIN_SCREEN", "LOGGED_IN", "LOGGING_IN", "LOADING"}:
                return self._result("BLOCKED", f"unsupported_game_state:{observation.game_state}", False, started, clicks)
            if observation.game_state in {"LOGGING_IN", "LOADING"}:
                self._sleep(self._poll_seconds)
                continue

            try:
                window = self._window_finder(observation.client_process_id)
                if not self._focus_window(window):
                    raise LoginSafetyError("exact RuneLite telemetry window could not be focused")
                image = self._screenshot(window.client_bounds)
                candidates = self._detector(image)
            except Exception as error:
                return self._result("BLOCKED", f"window_proof_failed: {type(error).__name__}: {error}", False, started, clicks)

            if observation.loaded_scene and not candidates:
                proof = (
                    observation.client_process_id,
                    observation.session_id,
                    observation.tick,
                )
                if (
                    loaded_proof is not None
                    and proof[:2] == loaded_proof[:2]
                    and proof[2] > loaded_proof[2]
                ):
                    return self._result("PASS", "loaded_scene_verified", True, started, clicks)
                loaded_proof = proof
                self._sleep(self._poll_seconds)
                continue
            loaded_proof = None
            if not candidates:
                misses += 1
                if misses >= 3:
                    return self._result("BLOCKED", "no_supported_authenticated_prompt", False, started, clicks)
                self._sleep(self._poll_seconds)
                continue
            misses = 0
            if len(candidates) != 1:
                return self._result("BLOCKED", "ambiguous_supported_prompts", False, started, clicks)
            if candidates[0].name == "disconnected_ok" and observation.game_state != "LOGIN_SCREEN":
                return self._result("BLOCKED", "disconnected_dialog_outside_login_screen", False, started, clicks)
            if len(clicks) >= click_limit:
                return self._result("BLOCKED", "maximum_login_clicks_reached", False, started, clicks)

            local = candidates[0]
            candidate = self._to_screen_candidate(local, window.client_bounds)
            if not self._safe_candidate(window, candidate):
                return self._result("BLOCKED", "candidate_outside_exact_runelite_client", False, started, clicks)
            click, error = self._click(window, candidate, observation)
            if click is not None:
                clicks.append(click)
            if error:
                return self._result("ERROR", error, False, started, clicks)

            transitioned, latest = self._wait_for_transition(
                observation, local.name, window, min(deadline, self._monotonic() + self._transition_seconds)
            )
            if not transitioned:
                return self._result("BLOCKED", f"{local.name}_did_not_transition", False, started, clicks)

        return self._result("BLOCKED", "login_assist_timeout", False, started, clicks)

    def _wait_for_transition(
        self,
        before: Observation,
        clicked_name: str,
        window: RuneLiteWindow,
        deadline: float,
    ) -> tuple[bool, Observation | None]:
        latest: Observation | None = None
        while self._monotonic() < deadline:
            self._sleep(self._poll_seconds)
            try:
                latest = self._observations.fetch()
            except Exception:
                continue
            if latest.loaded_scene:
                return True, latest
            if latest.client_process_id != before.client_process_id:
                return False, latest
            if latest.game_state != before.game_state:
                return True, latest
            if not self._focus_window(window):
                return False, latest
            try:
                names = {candidate.name for candidate in self._detector(self._screenshot(window.client_bounds))}
            except Exception:
                continue
            if clicked_name not in names:
                return True, latest
        return False, latest

    def _click(
        self,
        window: RuneLiteWindow,
        candidate: LoginCandidate,
        observation: Observation,
    ) -> tuple[LoginClick | None, str | None]:
        backend = self._backend_factory()
        connected = False
        stop_all_confirmed = False
        disarm_confirmed = False
        sent = False
        error: str | None = None
        try:
            backend.connect()
            connected = True
            bounds = window.client_bounds
            region = {"x": bounds.x, "y": bounds.y, "width": bounds.width, "height": bounds.height}
            start_x, start_y = backend.current_position()
            transit_region = self._transit_region(bounds, ScreenPoint(start_x, start_y))
            backend.configure_movement_safety(
                allowed_region=transit_region,
                allowed_foreground_titles=["RuneLite"],
                enabled=True,
                margin_px=0,
            )
            backend.arm()
            backend.assert_foreground(["RuneLite"], expected_pid=window.pid)
            backend.move_to_absolute(
                {"x": candidate.point.x, "y": candidate.point.y},
                allowed_region=transit_region,
                allowed_foreground_titles=["RuneLite"],
                margin_px=0,
            )
            # The cross-monitor transit never clicks. Once inside RuneLite,
            # restore the strict client-only constraint before revalidation.
            backend.configure_movement_safety(
                allowed_region=region,
                allowed_foreground_titles=["RuneLite"],
                enabled=True,
                margin_px=CLIENT_MARGIN_PX,
            )
            post_move = self._detector(self._screenshot(window.client_bounds))
            if len(post_move) != 1:
                raise LoginSafetyError("recognized prompt disappeared or became ambiguous after pointer movement")
            refreshed = self._to_screen_candidate(post_move[0], window.client_bounds)
            if not self._same_candidate(candidate, refreshed) or not self._safe_candidate(window, refreshed):
                raise LoginSafetyError("recognized prompt changed after pointer movement")
            backend.assert_foreground(["RuneLite"], expected_pid=window.pid)
            if not self._point_owner(window, candidate.point):
                raise LoginSafetyError("click point no longer belongs to the exact RuneLite window")
            backend.mouse_down(button="left")
            self._sleep(0.06)
            backend.mouse_up(button="left")
            sent = True
        except Exception as exc:
            error = f"login_click_failed: {type(exc).__name__}: {exc}"
        finally:
            if connected:
                try:
                    backend.stop_all()
                    stop_all_confirmed = True
                except Exception:
                    stop_all_confirmed = False
                try:
                    backend.disarm()
                    disarm_confirmed = True
                except Exception:
                    disarm_confirmed = False
                try:
                    backend.close()
                except Exception:
                    pass
        click = None
        if sent:
            click = LoginClick(
                candidate.name,
                candidate.point,
                observation.game_state,
                observation.tick,
                stop_all_confirmed,
                disarm_confirmed,
            )
        if connected and (not stop_all_confirmed or not disarm_confirmed):
            prior = f"; prior={error}" if error else ""
            error = f"login_click_cleanup_not_confirmed{prior}"
        return click, error

    @staticmethod
    def _to_screen_candidate(candidate: LoginCandidate, bounds: ScreenBounds) -> LoginCandidate:
        return LoginCandidate(
            candidate.name,
            ScreenPoint(bounds.x + candidate.point.x, bounds.y + candidate.point.y),
            ScreenBounds(
                bounds.x + candidate.match_bounds.x,
                bounds.y + candidate.match_bounds.y,
                candidate.match_bounds.width,
                candidate.match_bounds.height,
            ),
            candidate.confidence,
        )

    def _safe_candidate(self, window: RuneLiteWindow, candidate: LoginCandidate) -> bool:
        bounds = window.client_bounds
        safe = ScreenBounds(
            bounds.x + CLIENT_MARGIN_PX,
            bounds.y + CLIENT_MARGIN_PX,
            bounds.width - CLIENT_MARGIN_PX * 2,
            bounds.height - CLIENT_MARGIN_PX * 2,
        )
        match = candidate.match_bounds
        match_corners = (
            ScreenPoint(match.x, match.y),
            ScreenPoint(match.x + match.width - 1, match.y + match.height - 1),
        )
        return (
            candidate.name in SUPPORTED_SURFACES
            and candidate.confidence >= 0.90
            and safe.contains(candidate.point)
            and all(safe.contains(corner) for corner in match_corners)
            and self._point_owner(window, candidate.point)
        )

    @staticmethod
    def _same_candidate(before: LoginCandidate, after: LoginCandidate) -> bool:
        before_bounds = before.match_bounds
        after_bounds = after.match_bounds
        return (
            before.name == after.name
            and max(abs(before.point.x - after.point.x), abs(before.point.y - after.point.y)) <= 4
            and max(
                abs(before_bounds.x - after_bounds.x),
                abs(before_bounds.y - after_bounds.y),
                abs(before_bounds.width - after_bounds.width),
                abs(before_bounds.height - after_bounds.height),
            )
            <= 6
        )

    @staticmethod
    def _transit_region(bounds: ScreenBounds, start: ScreenPoint) -> dict[str, int]:
        left = min(bounds.x, start.x)
        top = min(bounds.y, start.y)
        right = max(bounds.x + bounds.width, start.x + 1)
        bottom = max(bounds.y + bounds.height, start.y + 1)
        return {"x": left, "y": top, "width": right - left, "height": bottom - top}

    def _result(
        self,
        status: str,
        reason: str,
        loaded_scene: bool,
        started: float,
        clicks: list[LoginClick],
    ) -> LoginResult:
        return LoginResult(status, reason, loaded_scene, self._monotonic() - started, tuple(clicks))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Click only recognized already-authenticated RuneLite prompts through Arduino HID."
    )
    parser.add_argument("--arduino-port", required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8893")
    parser.add_argument("--auth-token", default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"))
    parser.add_argument("--timeout-seconds", type=float, default=MAX_LOGIN_SECONDS)
    parser.add_argument("--max-clicks", type=int, default=MAX_LOGIN_CLICKS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    client = ObservationClient(args.endpoint, auth_token=args.auth_token, timeout_seconds=3.0)
    helper = LoginPromptHelper(client, arduino_port=args.arduino_port)
    result = helper.run(max_clicks=args.max_clicks, timeout_seconds=args.timeout_seconds)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
