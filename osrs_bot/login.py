from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image, ImageChops, ImageGrab, ImageStat

from .input_coordinator import (
    ApprovedPointerIntent,
    InputCoordinator,
    InputFailureKind,
    InputPurpose,
    InputReceipt,
    InputValidation,
    MouseButton,
)
from .model import Observation, ScreenBounds, ScreenPoint
from .observation import ObservationClient


MAX_LOGIN_CLICKS = 4
MAX_LOGIN_SECONDS = 180.0
TRANSITION_SECONDS = 15.0
MAX_LOADED_SCENE_PROOF_AGE_SECONDS = 2.0
CLIENT_MARGIN_PX = 8
MAX_TEMPLATE_SEARCH_ZONE_PIXELS = 4_000_000
MAX_TEMPLATE_ANCHOR_CANDIDATES = 20_000
MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE = 100_000
MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES = 600_000
MAX_LOADED_SCENE_TEMPLATE_ANCHOR_CANDIDATES = 1_000_000
MAX_LOADED_SCENE_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE = 2_000_000
MAX_LOADED_SCENE_TEMPLATE_FIRST_ANCHOR_CANDIDATES = 8_000_000
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


class LoginCandidateLimitError(LoginSafetyError):
    pass


@dataclass(frozen=True, slots=True)
class RuneLiteWindow:
    hwnd: int
    pid: int
    title: str
    client_bounds: ScreenBounds
    outer_bounds: ScreenBounds | None = None


@dataclass(frozen=True, slots=True)
class LoginCandidate:
    name: str
    point: ScreenPoint
    match_bounds: ScreenBounds
    confidence: float


@dataclass(frozen=True, slots=True)
class LoginClick:
    name: str
    point: ScreenPoint
    source_game_state: str
    source_tick: int
    receipt: InputReceipt

    @property
    def sent(self) -> bool:
        return self.receipt.successful

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "point": {"x": self.point.x, "y": self.point.y},
            "sourceGameState": self.source_game_state,
            "sourceTick": self.source_tick,
            "sent": self.sent,
            "receipt": self.receipt.to_dict(),
        }


@dataclass(frozen=True, slots=True)
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
        return {
            "status": self.status,
            "reason": self.reason,
            "loadedScene": self.loaded_scene,
            "elapsedSeconds": round(self.elapsed_seconds, 3),
            "clicks": [click.to_dict() for click in self.clicks],
        }


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


def _anchor_candidate_origins(
    bright_mask: Image.Image,
    anchors: tuple[tuple[int, int], ...],
    *,
    origin_width: int,
    origin_height: int,
    origin_x: int = 0,
    origin_y: int = 0,
    candidate_limit: int = MAX_TEMPLATE_ANCHOR_CANDIDATES,
    first_candidate_limit: int = MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE,
) -> tuple[set[tuple[int, int]], bytes, int, int]:
    """Return the original first-anchor superset plus its fast hit scores."""

    if (
        bright_mask.mode != "L"
        or not anchors
        or len(anchors) > 255
        or origin_width <= 0
        or origin_height <= 0
        or candidate_limit <= 0
        or first_candidate_limit <= 0
    ):
        raise LoginSafetyError("login template anchor evidence is invalid")
    anchor_score = Image.new("L", (origin_width, origin_height), 0)
    for anchor_x, anchor_y in anchors:
        anchor_score = ImageChops.add(
            anchor_score,
            bright_mask.crop(
                (
                    anchor_x,
                    anchor_y,
                    anchor_x + origin_width,
                    anchor_y + origin_height,
                )
            ),
        )
    score_bytes = anchor_score.tobytes()
    first_anchor_x, first_anchor_y = anchors[0]
    first_anchor_bytes = bright_mask.crop(
        (
            first_anchor_x,
            first_anchor_y,
            first_anchor_x + origin_width,
            first_anchor_y + origin_height,
        )
    ).tobytes()
    anchor_candidate_count = sum(
        score_bytes.count(bytes((anchor_hits,)))
        for anchor_hits in range(len(anchors) - 1, len(anchors) + 1)
    )
    if anchor_candidate_count > candidate_limit:
        raise LoginCandidateLimitError(
            "login template anchor candidates exceed the bounded limit"
        )
    first_anchor_candidate_count = first_anchor_bytes.count(b"\x01")
    if first_anchor_candidate_count > first_candidate_limit:
        raise LoginCandidateLimitError(
            "login template first-anchor candidates exceed the bounded limit"
        )
    origins: set[tuple[int, int]] = set()
    score_index = first_anchor_bytes.find(b"\x01")
    while score_index >= 0:
        local_y, local_x = divmod(score_index, origin_width)
        origins.add((origin_x + local_x, origin_y + local_y))
        score_index = first_anchor_bytes.find(b"\x01", score_index + 1)
    return (
        origins,
        score_bytes,
        anchor_candidate_count,
        first_anchor_candidate_count,
    )


def _best_template_match(
    image: Image.Image,
    template: Image.Image,
    zone: tuple[int, int, int, int],
    *,
    anchor_candidate_limit: int = MAX_TEMPLATE_ANCHOR_CANDIDATES,
    first_anchor_candidate_limit_per_scale: int = (
        MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE
    ),
    first_anchor_candidate_limit: int = MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES,
) -> tuple[int, int, int, int, float] | None:
    """Locate the white OSRS label shape without trusting background animation."""

    haystack = image.convert("RGB")
    hay_pixels = haystack.load()
    left, top, right, bottom = zone
    best: tuple[int, int, int, int, float] | None = None
    if (
        left < 0
        or top < 0
        or right <= left
        or bottom <= top
        or right > haystack.width
        or bottom > haystack.height
        or (right - left) * (bottom - top) > MAX_TEMPLATE_SEARCH_ZONE_PIXELS
        or haystack.width * haystack.height > 0xFFFFFFFF
    ):
        raise LoginSafetyError(
            "login template search zone is invalid or exceeds the bounded limit"
        )
    # The search region is identical for every allowed template scale. Build
    # its bright-pixel index once so fresh post-move validation stays inside
    # the firmware lease without narrowing the full ambiguity scan.
    zone_width = right - left
    zone_height = bottom - top
    bright_mask = bytearray((right - left) * (bottom - top))
    for screen_y in range(top, bottom):
        mask_row_offset = (screen_y - top) * zone_width
        for screen_x in range(left, right):
            if _bright(hay_pixels[screen_x, screen_y]):
                mask_index = mask_row_offset + screen_x - left
                bright_mask[mask_index] = 1
    bright_mask_image = Image.frombytes(
        "L", (zone_width, zone_height), bytes(bright_mask)
    )
    remaining_anchor_candidates = anchor_candidate_limit
    remaining_first_anchor_candidates = first_anchor_candidate_limit

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
        origin_width = zone_width - width + 1
        origin_height = zone_height - height + 1
        (
            candidate_origins,
            anchor_scores,
            anchor_candidate_count,
            first_anchor_candidate_count,
        ) = (
            _anchor_candidate_origins(
                bright_mask_image,
                anchors,
                origin_width=origin_width,
                origin_height=origin_height,
                origin_x=left,
                origin_y=top,
                candidate_limit=remaining_anchor_candidates,
                first_candidate_limit=min(
                    first_anchor_candidate_limit_per_scale,
                    remaining_first_anchor_candidates,
                ),
            )
        )
        remaining_anchor_candidates -= anchor_candidate_count
        remaining_first_anchor_candidates -= first_anchor_candidate_count

        positive_offsets = tuple(
            py * zone_width + px for px, py in positive_samples
        )
        negative_offsets = tuple(
            py * zone_width + px for px, py in negative_samples
        )
        for x, y in candidate_origins:
            anchor_score_index = (
                (y - top) * origin_width + x - left
            )
            if anchor_scores[anchor_score_index] < len(anchors) - 1:
                continue
            mask_origin = (y - top) * zone_width + x - left
            positive_ratio = sum(
                1
                for offset in positive_offsets
                if bright_mask[mask_origin + offset]
            ) / len(positive_samples)
            if positive_ratio < 0.86:
                continue
            negative_ratio = sum(
                1
                for offset in negative_offsets
                if not bright_mask[mask_origin + offset]
            ) / max(1, len(negative_samples))
            # A solid bright rectangle satisfies every positive sample but is
            # not text. Require the dark gaps that define the glyph shape.
            if negative_ratio < 0.75:
                continue
            patch_bright_ratio = sum(
                sum(
                    bright_mask[
                        mask_origin + patch_y * zone_width:
                        mask_origin + patch_y * zone_width + width
                    ]
                )
                for patch_y in range(height)
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


def _detect_login_surfaces_with_limits(
    screenshot: Image.Image,
    *,
    template_dir: Path,
    anchor_candidate_limit: int,
    first_anchor_candidate_limit_per_scale: int,
    first_anchor_candidate_limit: int,
    include_disconnected_dialog: bool,
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
        match = _best_template_match(
            screenshot,
            template,
            zone,
            anchor_candidate_limit=anchor_candidate_limit,
            first_anchor_candidate_limit_per_scale=(
                first_anchor_candidate_limit_per_scale
            ),
            first_anchor_candidate_limit=first_anchor_candidate_limit,
        )
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
    if not candidates and include_disconnected_dialog:
        disconnected = _disconnected_dialog_candidate(screenshot)
        if disconnected is not None:
            candidates.append(disconnected)
    return tuple(sorted(candidates, key=lambda item: item.confidence, reverse=True))


def detect_login_surfaces(
    screenshot: Image.Image,
    *,
    template_dir: Path = TEMPLATE_DIR,
) -> tuple[LoginCandidate, ...]:
    """Return only visually proven, already-authenticated RuneLite prompts."""

    return _detect_login_surfaces_with_limits(
        screenshot,
        template_dir=template_dir,
        anchor_candidate_limit=MAX_TEMPLATE_ANCHOR_CANDIDATES,
        first_anchor_candidate_limit_per_scale=(
            MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE
        ),
        first_anchor_candidate_limit=MAX_TEMPLATE_FIRST_ANCHOR_CANDIDATES,
        include_disconnected_dialog=True,
    )


def detect_loaded_scene_login_surfaces(
    screenshot: Image.Image,
) -> tuple[LoginCandidate, ...]:
    """Run the exact detector with a larger read-only loaded-world budget."""

    return _detect_login_surfaces_with_limits(
        screenshot,
        template_dir=TEMPLATE_DIR,
        anchor_candidate_limit=MAX_LOADED_SCENE_TEMPLATE_ANCHOR_CANDIDATES,
        first_anchor_candidate_limit_per_scale=(
            MAX_LOADED_SCENE_TEMPLATE_FIRST_ANCHOR_CANDIDATES_PER_SCALE
        ),
        first_anchor_candidate_limit=(
            MAX_LOADED_SCENE_TEMPLATE_FIRST_ANCHOR_CANDIDATES
        ),
        # This fallback exists only to finish the bounded template search that
        # capped on a coherent loaded scene.  The disconnect heuristic is
        # state-specific, never density-capped, and is actionable only on a
        # LOGIN_SCREEN observation; it cannot contribute to this negative
        # loaded-scene proof.
        include_disconnected_dialog=False,
    )


_WinPoint = wintypes.POINT
_WinRect = wintypes.RECT


def _user32() -> Any:
    if os.name != "nt":
        raise LoginSafetyError("RuneLite login assistance is supported only on Windows")
    _enable_windows_dpi_awareness()
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _enable_windows_dpi_awareness() -> None:
    """Keep Win32 bounds, screenshots, and Arduino cursor coordinates aligned."""
    global _DPI_AWARENESS_SET
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        context_getter = getattr(user32, "GetThreadDpiAwarenessContext", None)
        contexts_equal = getattr(user32, "AreDpiAwarenessContextsEqual", None)
        setter = getattr(user32, "SetProcessDpiAwarenessContext", None)
        if not all(callable(function) for function in (context_getter, contexts_equal, setter)):
            raise LoginSafetyError(
                "Windows per-monitor-v2 DPI awareness APIs are unavailable"
            )

        context_getter.argtypes = ()
        context_getter.restype = ctypes.c_void_p
        contexts_equal.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        contexts_equal.restype = ctypes.c_bool
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = ctypes.c_bool

        bits = ctypes.sizeof(ctypes.c_void_p) * 8
        per_monitor_v2 = ctypes.c_void_p((-4) & ((1 << bits) - 1))

        def is_per_monitor_v2_aware() -> bool:
            active_context = context_getter()
            return bool(active_context) and bool(
                contexts_equal(active_context, per_monitor_v2)
            )

        if not is_per_monitor_v2_aware():
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is a documented
            # negative pseudo-handle whose pointer width must be preserved.
            # A false setter result can also mean a manifest established the
            # context first, so the verified active context is authoritative.
            setter(per_monitor_v2)
            if not is_per_monitor_v2_aware():
                raise LoginSafetyError(
                    "Windows per-monitor-v2 DPI awareness could not be established"
                )

        _DPI_AWARENESS_SET = True
    except LoginSafetyError:
        _DPI_AWARENESS_SET = False
        raise
    except Exception as error:
        _DPI_AWARENESS_SET = False
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
            outer_rect = _WinRect()
            outer_bounds = None
            if user32.GetWindowRect(hwnd, ctypes.byref(outer_rect)):
                candidate_outer = ScreenBounds(
                    int(outer_rect.left),
                    int(outer_rect.top),
                    int(outer_rect.right - outer_rect.left),
                    int(outer_rect.bottom - outer_rect.top),
                )
                client_corners = (
                    ScreenPoint(bounds.x, bounds.y),
                    ScreenPoint(
                        bounds.x + bounds.width - 1,
                        bounds.y + bounds.height - 1,
                    ),
                )
                if (
                    candidate_outer.width > 0
                    and candidate_outer.height > 0
                    and all(
                        candidate_outer.contains(point)
                        for point in client_corners
                    )
                ):
                    outer_bounds = candidate_outer
            matches.append(
                RuneLiteWindow(
                    int(hwnd), int(pid.value), title, bounds, outer_bounds
                )
            )
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
    _enable_windows_dpi_awareness()
    image = ImageGrab.grab(
        bbox=(bounds.x, bounds.y, bounds.x + bounds.width, bounds.y + bounds.height),
        all_screens=True,
    )
    expected_size = (bounds.width, bounds.height)
    if image.size != expected_size:
        raise LoginSafetyError(
            "RuneLite client screenshot dimensions do not match exact Win32 bounds"
        )
    return image.convert("RGB")


class LoginPromptHelper:
    def __init__(
        self,
        observation_source: _ObservationSource,
        coordinator: InputCoordinator,
        *,
        window_finder: Callable[[int], RuneLiteWindow] = find_runelite_window,
        focus_window: Callable[[RuneLiteWindow], bool] = focus_exact_window,
        point_owner: Callable[[RuneLiteWindow, ScreenPoint], bool] = point_belongs_to_window,
        screenshot: Callable[[ScreenBounds], Image.Image] = capture_client,
        detector: Callable[[Image.Image], tuple[LoginCandidate, ...]] = detect_login_surfaces,
        loaded_scene_detector: Callable[
            [Image.Image], tuple[LoginCandidate, ...]
        ] = detect_loaded_scene_login_surfaces,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_seconds: float = 1.0,
        transition_seconds: float = TRANSITION_SECONDS,
    ) -> None:
        if not isinstance(coordinator, InputCoordinator):
            raise TypeError("coordinator must be InputCoordinator")
        self._observations = observation_source
        self._coordinator = coordinator
        self._window_finder = window_finder
        self._focus_window = focus_window
        self._point_owner = point_owner
        self._screenshot = screenshot
        self._detector = detector
        self._loaded_scene_detector = loaded_scene_detector
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
        cursor_replans = 0
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
                try:
                    candidates = self._detector(image)
                except LoginCandidateLimitError:
                    if not observation.loaded_scene:
                        raise
                    fallback_candidates = self._loaded_scene_detector(image)
                    if fallback_candidates:
                        raise LoginSafetyError(
                            "loaded-scene fallback found a supported prompt but "
                            "cannot authorize input"
                        )
                    candidates = ()
            except Exception as error:
                return self._result("BLOCKED", f"window_proof_failed: {type(error).__name__}: {error}", False, started, clicks)

            if observation.loaded_scene and not candidates:
                if (
                    observation.age_seconds
                    > MAX_LOADED_SCENE_PROOF_AGE_SECONDS
                ):
                    try:
                        refreshed = self._observations.fetch()
                    except Exception as error:
                        return self._result(
                            "ERROR",
                            "loaded_scene_refresh_unavailable: "
                            f"{type(error).__name__}: {error}",
                            False,
                            started,
                            clicks,
                        )
                    if (
                        refreshed.client_process_id
                        != observation.client_process_id
                        or refreshed.session_id != observation.session_id
                    ):
                        return self._result(
                            "BLOCKED",
                            "loaded_scene_refresh_identity_changed",
                            False,
                            started,
                            clicks,
                        )
                    if refreshed.tick < observation.tick:
                        return self._result(
                            "BLOCKED",
                            "loaded_scene_refresh_tick_regressed",
                            False,
                            started,
                            clicks,
                        )
                    if (
                        not refreshed.loaded_scene
                        or refreshed.age_seconds
                        > MAX_LOADED_SCENE_PROOF_AGE_SECONDS
                    ):
                        loaded_proof = None
                        self._sleep(self._poll_seconds)
                        continue
                    observation = refreshed
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
            if sum(click.sent for click in clicks) >= click_limit:
                return self._result("BLOCKED", "maximum_login_clicks_reached", False, started, clicks)

            local = candidates[0]
            candidate = self._to_screen_candidate(local, window.client_bounds)
            if not self._safe_candidate(window, candidate):
                return self._result("BLOCKED", "candidate_outside_exact_runelite_client", False, started, clicks)
            try:
                click, receipt = self._click(window, candidate, observation)
            except (LoginSafetyError, TypeError, ValueError) as error:
                return self._result(
                    "BLOCKED",
                    f"login_click_intent_invalid: {type(error).__name__}: {error}",
                    False,
                    started,
                    clicks,
                )
            clicks.append(click)
            if not receipt.successful:
                if (
                    cursor_replans < 1
                    and self._may_retry_cursor_state(receipt)
                ):
                    cursor_replans += 1
                    self._sleep(self._poll_seconds)
                    continue
                result_status = "BLOCKED" if receipt.status == "BLOCKED" else "ERROR"
                return self._result(
                    result_status,
                    f"login_click_{receipt.status.lower()}: {receipt.reason}",
                    False,
                    started,
                    clicks,
                )

            transitioned, latest = self._wait_for_transition(
                observation, local.name, window, min(deadline, self._monotonic() + self._transition_seconds)
            )
            if not transitioned:
                return self._result("BLOCKED", f"{local.name}_did_not_transition", False, started, clicks)

        return self._result("BLOCKED", "login_assist_timeout", False, started, clicks)

    @staticmethod
    def _may_retry_cursor_state(receipt: InputReceipt) -> bool:
        preactivation = {
            "STOP_ALL",
            "PING",
            "IDENTIFY",
            "CAPS",
            "STATUS",
            "ARM",
            "MOVE",
            "DISARM",
        }
        return bool(
            receipt.status == "BLOCKED"
            and receipt.failure_kind
            is InputFailureKind.CURSOR_STATE_INVALIDATED
            and receipt.backend_closed
            and (
                receipt.safely_unsent
                or (
                    receipt.connected
                    and receipt.stop_all_acknowledged
                    and receipt.disarm_acknowledged
                    and receipt.firmware_status_acknowledged
                    and receipt.firmware_status is not None
                    and receipt.firmware_status.safe
                    and receipt.unresolved_command_count == 0
                    and receipt.failed_command_count == 0
                    and receipt.ack_missing_count == 0
                    and receipt.ledger_complete
                    and receipt.ledger_closed
                )
            )
            and all(command.command in preactivation for command in receipt.commands)
        )

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
    ) -> tuple[LoginClick, InputReceipt]:
        if observation.client_process_id != window.pid:
            raise LoginSafetyError("RuneLite window PID does not match telemetry")
        match = candidate.match_bounds
        match_corners = (
            ScreenPoint(match.x, match.y),
            ScreenPoint(match.x + match.width - 1, match.y + match.height - 1),
        )
        if not window.client_bounds.contains(candidate.point) or not all(
            window.client_bounds.contains(point) for point in match_corners
        ):
            raise LoginSafetyError(
                "recognized prompt is outside the exact RuneLite client"
            )

        intent = ApprovedPointerIntent(
            intent_id=f"login:{candidate.name}:{observation.tick}",
            purpose=InputPurpose.LOGIN_PROMPT,
            target=candidate.point,
            movement_bounds=window.client_bounds,
            target_bounds=candidate.match_bounds,
            expected_pid=window.pid,
            expected_hwnd=window.hwnd,
            button=MouseButton.LEFT,
            reacquisition_bounds=window.outer_bounds,
        )

        def validate(
            approved: ApprovedPointerIntent,
            actual_point: ScreenPoint,
        ) -> InputValidation:
            if approved != intent:
                return InputValidation.deny("login intent identity changed")
            try:
                post_move = self._detector(self._screenshot(window.client_bounds))
            except Exception as error:  # noqa: BLE001 - fail closed in receipt
                return InputValidation.deny(
                    f"login prompt revalidation failed: {type(error).__name__}"
                )
            if len(post_move) != 1:
                return InputValidation.deny(
                    "recognized prompt disappeared or became ambiguous after pointer movement"
                )
            refreshed = self._to_screen_candidate(
                post_move[0], window.client_bounds
            )
            if (
                not self._same_candidate(candidate, refreshed)
                or not self._safe_candidate(window, refreshed)
                or not refreshed.match_bounds.contains(actual_point)
            ):
                return InputValidation.deny(
                    "recognized prompt changed after pointer movement"
                )
            if not self._point_owner(window, actual_point):
                return InputValidation.deny(
                    "click point no longer belongs to the exact RuneLite window"
                )
            return InputValidation.allow("login prompt identity revalidated")

        receipt = self._coordinator.execute_pointer(intent, validate=validate)
        click = LoginClick(
            candidate.name,
            candidate.point,
            observation.game_state,
            observation.tick,
            receipt,
        )
        return click, receipt

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
    coordinator = InputCoordinator.for_arduino_port(
        args.arduino_port,
        serial_owner="osrs-login-helper",
    )
    helper = LoginPromptHelper(client, coordinator)
    result = helper.run(max_clicks=args.max_clicks, timeout_seconds=args.timeout_seconds)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.successful else 2


if __name__ == "__main__":
    raise SystemExit(main())
