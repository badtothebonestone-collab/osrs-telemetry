from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import arduino_input_bridge
import vm_mouse_arduino_mapper


LIVE_MIRROR_STATUS_SCHEMA = "arduino_live_mirror_status.v1"
LIVE_MIRROR_SUMMARY_SCHEMA = "arduino_live_mirror_summary.v1"

BUTTON_KINDS = {"CLICK", "MOUSE_DOWN", "MOUSE_UP"}
BUTTON_INPUT_KINDS = {"mouse_down", "mouse_up", "click", "double_click", "drag_start", "drag_end"}
MOVE_INPUT_KINDS = {"mouse_move", "drag_move"}
IGNORED_CAPTURE_KINDS = {"capture_start", "capture_stop", "capture_error"}
UI_REGIONS = {"inventory/sidebar", "sidebar", "inventory", "chatbox", "topbar", "window_chrome", "external"}
ARM_MODES = {"test_window", "recording_persistent", "manual"}
MIRROR_PROFILES = {"observe_only", "click_only", "move_only", "full_live_mirror", "validation_menu_row"}
CLICK_POLICIES = {"off", "map_only", "live_unsuppressed", "live_requires_source_suppression", "arduino_source_only"}
TARGET_QUALITY_ORDER = {"off": 99, "weak": 1, "medium": 2, "strong": 3}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: str | Path, payload: dict[str, Any], *, pretty: bool = True) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, default=str)
        handle.write("\n")
    temp.replace(output)


def _event_seq(event: dict[str, Any]) -> Any:
    return event.get("event_seq", event.get("eventSeq"))


def _event_time(event: dict[str, Any]) -> Any:
    return event.get("monotonic_time", event.get("monotonicTime", event.get("elapsed_seconds")))


def _button(event: dict[str, Any]) -> str:
    return str(event.get("button") or "left").lower()


def _point(event: dict[str, Any]) -> tuple[int | None, int | None]:
    for x_key, y_key in (("screen_x", "screen_y"), ("client_x", "client_y"), ("canvas_x", "canvas_y"), ("x", "y")):
        if event.get(x_key) is not None and event.get(y_key) is not None:
            try:
                return int(round(float(event.get(x_key)))), int(round(float(event.get(y_key))))
            except (TypeError, ValueError):
                return None, None
    return None, None


def _distance(a: tuple[int | None, int | None], b: tuple[int | None, int | None]) -> float:
    if a[0] is None or a[1] is None or b[0] is None or b[1] is None:
        return 0.0
    return math.hypot(float(a[0] - b[0]), float(a[1] - b[1]))


def _command_name(record: dict[str, Any]) -> str:
    return str(record.get("command") or record.get("command_kind") or record.get("commandKind") or "").upper()


def _command_time(record: dict[str, Any]) -> float | None:
    for key in ("sent_at_monotonic", "sentAtMonotonic", "monotonic_time", "elapsed_seconds"):
        try:
            value = record.get(key)
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _source_seq(record: dict[str, Any]) -> Any:
    return record.get("sourceInputEventSeq", record.get("source_event_seq"))


def _window_count(values: deque[float], now: float, seconds: float) -> int:
    cutoff = now - max(0.001, seconds)
    while values and values[0] < cutoff:
        values.popleft()
    return len(values)


def _max_window_count(times: list[float], seconds: float) -> int:
    if not times:
        return 0
    best = 0
    left = 0
    for right, value in enumerate(times):
        while value - times[left] > seconds:
            left += 1
        best = max(best, right - left + 1)
    return best


def _rate_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    sent = [record for record in records if record.get("kind") != "command_dropped"]
    times = sorted(t for t in (_command_time(record) for record in sent) if t is not None)
    click_times = sorted(
        t
        for t in (
            _command_time(record)
            for record in sent
            if _command_name(record) in arduino_input_bridge.ARDUINO_CLICK_COMMANDS
        )
        if t is not None
    )
    max_total = _max_window_count(times, 1.0)
    max_click = _max_window_count(click_times, 1.0)
    sources = Counter(_source_seq(record) for record in sent if _source_seq(record) is not None)
    duplicate_sources = {str(key): value for key, value in sources.items() if value > 1}
    safety: list[str] = []
    if max_click > 8 or len(click_times) >= 20:
        safety.append("live_mirror_click_storm")
    if max_click > 4:
        safety.append("live_mirror_rate_limited")
    if len(click_times) >= 10 and not duplicate_sources:
        safety.append("live_mirror_feedback_suspected")
    if any(record.get("duplicateClickLikely") for record in sent):
        safety.append("live_mirror_duplicate_click_risk")
    dropped_reasons = Counter(
        str(record.get("dropReason") or record.get("reason") or "unknown")
        for record in records
        if record.get("kind") == "command_dropped"
    )
    throttled = sum(1 for record in records if record.get("throttled") or record.get("dropReason") in {"rate_limited", "click_cooldown", "same_button_cooldown"})
    panic_stops = sum(1 for record in records if record.get("panicStop") or record.get("dropReason") == "panic_stopped")
    if dropped_reasons.get("telemetry_ui_window") or dropped_reasons.get("ui_control_event"):
        safety.append("live_mirror_ui_click_loop_suspected")
    if dropped_reasons.get("panic_stopped") or any(record.get("panicStop") for record in records):
        safety.append("live_mirror_panic_stopped")
    if dropped_reasons.get("rate_limited") or dropped_reasons.get("click_cooldown") or dropped_reasons.get("same_button_cooldown"):
        safety.append("live_mirror_rate_limited")
    return {
        "maxCommandsPerSecondObserved": max_total,
        "maxClickCommandsPerSecondObserved": max_click,
        "duplicateSourceEventCount": sum(value - 1 for value in sources.values() if value > 1),
        "repeatedClickSourceCount": sum(
            value - 1
            for key, value in sources.items()
            if value > 1
            and any(
                _source_seq(record) == key and _command_name(record) in arduino_input_bridge.ARDUINO_CLICK_COMMANDS
                for record in sent
            )
        ),
        "sourceEventCommandCounts": dict(sorted(duplicate_sources.items())[:20]),
        "droppedCommandCount": sum(dropped_reasons.values()),
        "throttledCommandCount": throttled,
        "panicStopCount": panic_stops,
        "droppedCommandsByReason": dict(sorted(dropped_reasons.items())),
        "liveMirrorSafetyClassifications": sorted(set(safety)),
    }


def command_safety_diagnostics(commands: list[dict[str, Any]]) -> dict[str, Any]:
    return _rate_diagnostics(list(commands or []))


@dataclass
class LiveMirrorSettings:
    mirror_profile: str = "full_live_mirror"
    mirror_click_policy: str = "live_unsuppressed"
    require_click_source_suppression: bool = False
    allow_unsuppressed_live_clicks: bool = False
    max_live_clicks_per_recording: int = 0
    auto_disable_live_clicks_after_first_game_action: bool = False
    disable_movement: bool = False
    disable_clicks: bool = False
    echo_suppression: bool = False
    echo_window_ms: int = 250
    click_echo_window_ms: int = 300
    echo_max_error_px: int = 100
    max_queue_size: int = 25
    drop_move_older_than_ms: int = 150
    clear_queue_on_game_action: bool = False
    clear_queue_on_menu_selection: bool = False
    clear_queue_on_plane_change: bool = False
    clear_queue_on_target_action: bool = False
    auto_pause_after_first_game_action: bool = False
    auto_pause_after_menu_selection: bool = False
    auto_pause_after_plane_change: bool = False
    auto_pause_after_target_quality: str = "off"
    validation_mode: str = "custom"
    move_min_px: int = 1
    max_step_px: int = 25
    send_interval_ms: int = 5
    scale_x: float = 1.0
    scale_y: float = 1.0
    invert_x: bool = False
    invert_y: bool = False
    button_mode: str = "click"
    keys_enabled: bool = False
    feedback_suppression_ms: int = 250
    drag_threshold_px: int = 8
    allow_drag_clicks: bool = False
    max_clicks_per_second: int = 4
    max_button_commands_per_second: int = 8
    max_move_commands_per_second: int = 120
    max_total_commands_per_second: int = 150
    click_cooldown_ms: int = 120
    same_button_cooldown_ms: int = 80
    max_burst_commands: int = 50
    panic_command_threshold: int = 100
    panic_window_ms: int = 1000
    arm_delay_ms: int = 500
    arm_mode: str = "recording_persistent"
    persist_until_stop: bool = True
    keep_armed_while_recording: bool = True
    disarm_on_focus_lost: bool = False
    test_duration_sec: float = 0.0
    panic_stop_file: str = ""
    arm_only_when_runelite_focused: bool = False
    allow_ui_events: bool = False
    window_title_allow: str = "RuneLite"
    exclude_window_title: str = "OSRS Telemetry Control"
    region: str = "client"
    ignore_ui_clicks: bool = True

    def __post_init__(self) -> None:
        self.mirror_profile = _normalized_profile(self.mirror_profile)
        self.mirror_click_policy = _normalized_click_policy(self.mirror_click_policy)
        if self.mirror_profile == "validation_menu_row" and self.mirror_click_policy == "live_unsuppressed" and not self.allow_unsuppressed_live_clicks:
            self.mirror_click_policy = "map_only"

    @classmethod
    def from_args(cls, args: Any) -> "LiveMirrorSettings":
        profile = _normalized_profile(getattr(args, "mirror_profile", "full_live_mirror"))
        allow_unsuppressed = bool(getattr(args, "allow_unsuppressed_live_clicks", False))
        click_policy = _normalized_click_policy(getattr(args, "mirror_click_policy", "live_unsuppressed"))
        if profile == "validation_menu_row" and click_policy == "live_unsuppressed" and not allow_unsuppressed:
            click_policy = "map_only"
        return cls(
            mirror_profile=profile,
            mirror_click_policy=click_policy,
            require_click_source_suppression=bool(getattr(args, "require_click_source_suppression", False)),
            allow_unsuppressed_live_clicks=allow_unsuppressed,
            max_live_clicks_per_recording=max(0, int(getattr(args, "max_live_clicks_per_recording", 0) or 0)),
            auto_disable_live_clicks_after_first_game_action=bool(getattr(args, "auto_disable_live_clicks_after_first_game_action", False)),
            disable_movement=bool(getattr(args, "mirror_disable_movement", False)),
            disable_clicks=bool(getattr(args, "mirror_disable_clicks", False)),
            echo_suppression=bool(getattr(args, "mirror_echo_suppression", False) or profile == "validation_menu_row"),
            echo_window_ms=max(0, int(getattr(args, "mirror_echo_window_ms", 250) or 250)),
            click_echo_window_ms=max(0, int(getattr(args, "mirror_click_echo_window_ms", 300) or 300)),
            echo_max_error_px=max(0, int(getattr(args, "mirror_echo_max_error_px", 100) or 100)),
            max_queue_size=max(1, int(getattr(args, "mirror_max_queue_size", 25) or 25)),
            drop_move_older_than_ms=max(0, int(getattr(args, "mirror_drop_move_older_than_ms", 150) or 150)),
            clear_queue_on_game_action=bool(getattr(args, "mirror_clear_queue_on_game_action", False) or profile == "validation_menu_row"),
            clear_queue_on_menu_selection=bool(getattr(args, "mirror_clear_queue_on_menu_selection", False) or profile == "validation_menu_row"),
            clear_queue_on_plane_change=bool(getattr(args, "mirror_clear_queue_on_plane_change", False) or profile == "validation_menu_row"),
            clear_queue_on_target_action=bool(getattr(args, "mirror_clear_queue_on_target_action", False)),
            auto_pause_after_first_game_action=bool(getattr(args, "mirror_auto_pause_after_first_game_action", False)),
            auto_pause_after_menu_selection=bool(getattr(args, "mirror_auto_pause_after_menu_selection", False) or profile == "validation_menu_row"),
            auto_pause_after_plane_change=bool(getattr(args, "mirror_auto_pause_after_plane_change", False) or profile == "validation_menu_row"),
            auto_pause_after_target_quality=str(getattr(args, "mirror_auto_pause_after_target_quality", "off") or "off"),
            validation_mode=str(getattr(args, "mirror_validation_mode", "custom") or "custom"),
            move_min_px=max(0, int(getattr(args, "mirror_move_min_px", 1) or 1)),
            max_step_px=max(1, int(getattr(args, "mirror_max_step_px", 25) or 25)),
            send_interval_ms=max(0, int(getattr(args, "mirror_send_interval_ms", 5) or 5)),
            scale_x=float(getattr(args, "mirror_scale_x", 1.0) or 1.0),
            scale_y=float(getattr(args, "mirror_scale_y", 1.0) or 1.0),
            invert_x=bool(getattr(args, "mirror_invert_x", False)),
            invert_y=bool(getattr(args, "mirror_invert_y", False)),
            button_mode=str(getattr(args, "mirror_button_mode", "click") or "click"),
            keys_enabled=bool(getattr(args, "mirror_keys", False)),
            max_clicks_per_second=max(1, int(getattr(args, "mirror_max_clicks_per_second", 4) or 4)),
            max_button_commands_per_second=max(1, int(getattr(args, "mirror_max_button_commands_per_second", 8) or 8)),
            max_move_commands_per_second=max(1, int(getattr(args, "mirror_max_move_commands_per_second", 120) or 120)),
            max_total_commands_per_second=max(1, int(getattr(args, "mirror_max_total_commands_per_second", 150) or 150)),
            click_cooldown_ms=max(0, int(getattr(args, "mirror_click_cooldown_ms", 120) or 120)),
            same_button_cooldown_ms=max(0, int(getattr(args, "mirror_same_button_cooldown_ms", 80) or 80)),
            max_burst_commands=max(1, int(getattr(args, "mirror_max_burst_commands", 50) or 50)),
            panic_command_threshold=max(1, int(getattr(args, "mirror_panic_command_threshold", 100) or 100)),
            panic_window_ms=max(100, int(getattr(args, "mirror_panic_window_ms", 1000) or 1000)),
            arm_delay_ms=max(0, int(getattr(args, "mirror_arm_delay_ms", 500) or 500)),
            arm_mode=_normalized_arm_mode(getattr(args, "mirror_arm_mode", None), live_mirror=bool(getattr(args, "arduino_live_mirror", False))),
            persist_until_stop=bool(getattr(args, "mirror_persist_until_stop", False) or getattr(args, "mirror_keep_armed_while_recording", False) or _normalized_arm_mode(getattr(args, "mirror_arm_mode", None), live_mirror=bool(getattr(args, "arduino_live_mirror", False))) == "recording_persistent"),
            keep_armed_while_recording=bool(getattr(args, "mirror_keep_armed_while_recording", False) or getattr(args, "mirror_persist_until_stop", False) or _normalized_arm_mode(getattr(args, "mirror_arm_mode", None), live_mirror=bool(getattr(args, "arduino_live_mirror", False))) == "recording_persistent"),
            disarm_on_focus_lost=bool(getattr(args, "mirror_disarm_on_focus_lost", False)),
            test_duration_sec=max(0.0, float(getattr(args, "mirror_test_duration_sec", 0) or 0)),
            panic_stop_file=str(getattr(args, "mirror_panic_stop_file", "") or ""),
            arm_only_when_runelite_focused=bool(getattr(args, "mirror_arm_only_when_runelite_focused", False)),
            allow_ui_events=bool(getattr(args, "mirror_allow_ui_events", False)),
            window_title_allow=str(getattr(args, "mirror_window_title_allow", "RuneLite") or "RuneLite"),
            exclude_window_title=str(getattr(args, "mirror_exclude_window_title", "OSRS Telemetry Control") or "OSRS Telemetry Control"),
            region=str(getattr(args, "mirror_region", "client") or "client"),
            ignore_ui_clicks=bool(getattr(args, "mirror_ignore_ui_clicks", True)),
        )

    def movement_enabled(self) -> bool:
        if self.disable_movement or self.mirror_profile in {"observe_only", "click_only", "validation_menu_row"}:
            return False
        return self.mirror_profile in {"move_only", "full_live_mirror"}

    def button_enabled(self) -> bool:
        if self.disable_clicks or self.mirror_profile in {"observe_only", "move_only"}:
            return False
        return self.mirror_profile in {"click_only", "full_live_mirror", "validation_menu_row"}


def _normalized_arm_mode(value: Any, *, live_mirror: bool = False) -> str:
    text = str(value or "").strip()
    if text in ARM_MODES:
        return text
    return "recording_persistent" if live_mirror else "manual"


def _normalized_profile(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in MIRROR_PROFILES else "full_live_mirror"


def _normalized_click_policy(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in CLICK_POLICIES else "live_unsuppressed"


class ArduinoLiveMirror:
    def __init__(
        self,
        recording_dir: str | Path,
        *,
        recording_id: str,
        port: str | None = None,
        baud: int = arduino_input_bridge.DEFAULT_BAUD,
        settings: LiveMirrorSettings | None = None,
        command_client: Any | None = None,
        pretty: bool = True,
        sleep_func: Any = time.sleep,
        clock: Any = time.monotonic,
    ) -> None:
        self.recording_dir = Path(recording_dir)
        self.recording_id = recording_id
        self.port = port
        self.baud = int(baud or arduino_input_bridge.DEFAULT_BAUD)
        self.settings = settings or LiveMirrorSettings()
        self.pretty = bool(pretty)
        self.sleep_func = sleep_func
        self.clock = clock
        self.client = command_client or arduino_input_bridge.ArduinoCommandClient(
            self.recording_dir,
            recording_id=recording_id,
            port=port,
            baud=self.baud,
            pretty=pretty,
        )
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        self._state = "disarmed"
        self._arm_at: float | None = None
        self._disarm_at: float | None = None
        self._armed_at_monotonic: float | None = None
        self._disarmed_at_monotonic: float | None = None
        self._mirrored_event_keys: set[Any] = set()
        self._button_states: dict[str, dict[str, Any]] = defaultdict(lambda: {"state": "up"})
        self._feedback_until = 0.0
        self._button_feedback_until: dict[str, float] = defaultdict(float)
        self._pending_feedback_dx = 0
        self._pending_feedback_dy = 0
        self._pending_echo_commands: deque[dict[str, Any]] = deque()
        self._last_click_at: dict[str, float] = defaultdict(float)
        self._last_button_command_at: dict[str, float] = defaultdict(float)
        self._last_right_click_at: float | None = None
        self._last_plane: int | None = None
        self._command_times: deque[float] = deque()
        self._click_times: deque[float] = deque()
        self._button_command_times: deque[float] = deque()
        self._move_times: deque[float] = deque()
        self.status_payload: dict[str, Any] = {}
        self.records: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {
            "schema": LIVE_MIRROR_SUMMARY_SCHEMA,
            "recording_id": recording_id,
            "status": "not_started",
            "liveMirrorRequested": True,
            "liveMirrorReady": False,
            "liveMirrorActive": False,
            "liveMirrorVerified": False,
            "mirrorState": self._state,
            "mirrorProfile": self.settings.mirror_profile,
            "movementMirroringEnabled": self.settings.movement_enabled(),
            "clickMirroringEnabled": self.settings.button_enabled(),
            "mirrorClickPolicy": self.settings.mirror_click_policy,
            "clickPolicyUsed": self.settings.mirror_click_policy,
            "clickPolicyDowngraded": False,
            "clickPolicyDowngradeReason": None,
            "sourceSuppressionAvailable": False,
            "sourceSuppressionVerified": False,
            "mapOnlyClickCount": 0,
            "clickPolicyOffCount": 0,
            "liveUnsuppressedClickCount": 0,
            "liveClickWithoutSuppressionCount": 0,
            "duplicateRiskClickCount": 0,
            "arduinoPhysicalClickCount": 0,
            "liveClicksAutoDisabled": False,
            "liveClickCommandLimit": self.settings.max_live_clicks_per_recording,
            "mirrorPaused": False,
            "pauseReason": None,
            "armMode": self.settings.arm_mode,
            "recordingPersistent": self.settings.arm_mode == "recording_persistent",
            "testDurationSec": self.settings.test_duration_sec if self.settings.arm_mode == "test_window" else 0.0,
            "armedAtMonotonic": None,
            "disarmedAtMonotonic": None,
            "disarmReason": None,
            "activeWindows": [],
            "actionsAfterDisarmCount": 0,
            "clicksAfterDisarmCount": 0,
            "movementAfterDisarmCount": 0,
            "echoSuppressionEnabled": self.settings.echo_suppression,
            "echoSuppressedMoveCount": 0,
            "echoSuppressedClickCount": 0,
            "pendingCommandCount": 0,
            "unmatchedEchoCount": 0,
            "echoMatchedCommandCount": 0,
            "feedbackLoopSuspected": False,
            "queuedCommandMaxAgeMs": 0.0,
            "staleCommandsDropped": 0,
            "queueClearedOnMenuSelectionCount": 0,
            "queueClearedOnGameActionCount": 0,
            "queueClearedOnPlaneChangeCount": 0,
            "queueClearedOnTargetActionCount": 0,
            "mirrorAutoPaused": False,
            "autoPauseReason": None,
            "autoPauseAtMonotonic": None,
            "actionsBeforeAutoPause": 0,
            "actionsAfterAutoPause": 0,
            "startedAtUtc": None,
            "armedAtUtc": None,
            "disarmedAtUtc": None,
            "stoppedAtUtc": None,
            "processedInputEventCount": 0,
            "mirroredInputEventCount": 0,
            "ignoredInputEventCount": 0,
            "suppressedFeedbackEventCount": 0,
            "droppedCommandCount": 0,
            "throttledCommandCount": 0,
            "duplicateCommandCount": 0,
            "duplicateInputEventCount": 0,
            "panicStopCount": 0,
            "uiControlEventsDropped": 0,
            "foregroundFilteredEventsDropped": 0,
            "sameButtonCooldownDrops": 0,
            "feedbackSuppressedButtonEventCount": 0,
            "nonProbeActionCommandCount": 0,
            "movementCommandCount": 0,
            "clickCommandCount": 0,
            "keyboardCommandCount": 0,
            "ackCount": 0,
            "errorCount": 0,
            "maxCommandsPerSecondObserved": 0,
            "maxClickCommandsPerSecondObserved": 0,
            "maxMoveCommandsPerSecondObserved": 0,
            "droppedEventsByReason": {},
            "settings": self.settings.__dict__.copy(),
            "warnings": [],
        }

    def start(self, *, require_active: bool = False) -> dict[str, Any]:
        if self._started:
            return self.status_payload
        try:
            self.status_payload = self.client.connect()
            available = bool(self.status_payload.get("available") or self.status_payload.get("connected"))
            if not available and require_active:
                raise RuntimeError("; ".join(self.status_payload.get("warnings") or ["Arduino live mirror client unavailable"]))
            self._started = True
            self._state = "disarmed"
            self.stats["status"] = "PASS" if available else "WARN"
            self.stats["liveMirrorReady"] = bool(available)
            self.stats["liveMirrorActive"] = False
            self.stats["mirrorState"] = self._state
            self.stats["startedAtUtc"] = utc_now()
            self._thread = threading.Thread(target=self._worker, name="arduino-live-mirror", daemon=True)
            self._thread.start()
        except Exception as error:  # noqa: BLE001
            self.status_payload = {
                "schema": LIVE_MIRROR_STATUS_SCHEMA,
                "status": "FAIL",
                "available": False,
                "connected": False,
                "port": self.port,
                "baud": self.baud,
                "error": f"{type(error).__name__}: {error}",
                "updated_at_utc": utc_now(),
            }
            self.stats["status"] = "FAIL"
            self.stats["warnings"].append(self.status_payload["error"])
            if require_active:
                self.write_status()
                raise
        self.write_status()
        return self.status_payload

    def arm(self, *, delay_ms: int | None = None, duration_sec: float | None = None, mode: str | None = None) -> None:
        now = self.clock()
        arm_mode = _normalized_arm_mode(mode or self.settings.arm_mode, live_mirror=True)
        delay = self.settings.arm_delay_ms if delay_ms is None else max(0, int(delay_ms))
        raw_duration = self.settings.test_duration_sec if duration_sec is None else max(0.0, float(duration_sec or 0.0))
        duration = raw_duration if arm_mode == "test_window" else 0.0
        self._arm_at = now + (delay / 1000.0)
        self._disarm_at = self._arm_at + duration if duration else None
        self._armed_at_monotonic = None
        self._disarmed_at_monotonic = None
        self._state = "disarmed"
        self.stats["armMode"] = arm_mode
        self.stats["recordingPersistent"] = arm_mode == "recording_persistent"
        self.stats["testDurationSec"] = duration
        self.stats["armedAtMonotonic"] = None
        self.stats["disarmedAtMonotonic"] = None
        self.stats["disarmReason"] = None
        self.stats["mirrorState"] = self._state
        self.stats["mirrorPaused"] = False
        self.stats["pauseReason"] = f"{arm_mode} arming in {delay} ms" if delay else None
        self.write_status()

    def disarm(self, reason: str = "manual_disarm") -> None:
        now = self.clock()
        if self._state not in {"stopped", "panic_stopped"}:
            self._state = "disarmed"
        if self._armed_at_monotonic is not None:
            windows = list(self.stats.get("activeWindows") or [])
            if not windows or windows[-1].get("endMonotonic") is not None:
                windows.append({"startMonotonic": self._armed_at_monotonic, "endMonotonic": now, "disarmReason": reason})
            else:
                windows[-1]["endMonotonic"] = now
                windows[-1]["disarmReason"] = reason
            self.stats["activeWindows"] = windows[-10:]
        self._disarmed_at_monotonic = now
        self._arm_at = None
        self._disarm_at = None
        self.stats["mirrorState"] = self._state
        self.stats["mirrorPaused"] = self._state == "disarmed"
        self.stats["pauseReason"] = reason
        self.stats["disarmReason"] = reason
        self.stats["disarmedAtMonotonic"] = now
        self.stats["disarmedAtUtc"] = utc_now()
        self.write_status()

    def panic_stop(self, reason: str = "panic_stopped") -> None:
        now = self.clock()
        self._state = "panic_stopped"
        if self._armed_at_monotonic is not None:
            windows = list(self.stats.get("activeWindows") or [])
            if not windows or windows[-1].get("endMonotonic") is not None:
                windows.append({"startMonotonic": self._armed_at_monotonic, "endMonotonic": now, "disarmReason": reason})
            else:
                windows[-1]["endMonotonic"] = now
                windows[-1]["disarmReason"] = reason
            self.stats["activeWindows"] = windows[-10:]
        self._disarmed_at_monotonic = now
        self._arm_at = None
        self._disarm_at = None
        self.stats["panicStopCount"] += 1
        self.stats["mirrorState"] = self._state
        self.stats["mirrorPaused"] = True
        self.stats["pauseReason"] = reason
        self.stats["disarmReason"] = reason
        self.stats["disarmedAtMonotonic"] = now
        self.stats["warnings"].append(f"live mirror panic-stopped: {reason}")
        self.write_summary()
        self.write_status()

    def process_input_event(self, event: dict[str, Any]) -> None:
        if not self._started or self._stop.is_set():
            return
        if self._queue.qsize() >= self.settings.max_queue_size:
            self.stats["staleCommandsDropped"] += 1
            self._drop_event("mirror_queue_full", event)
            return
        self._queue.put(dict(event))

    def process_telemetry_snapshot(self, snapshot: dict[str, Any]) -> None:
        plane = self._extract_plane(snapshot)
        if plane is None:
            return
        if self._last_plane is None:
            self._last_plane = plane
            return
        if plane != self._last_plane:
            self._last_plane = plane
            if self.settings.clear_queue_on_plane_change:
                self._clear_pending_input("plane_change")
            if self.settings.auto_pause_after_plane_change and self._state in {"armed", "active"}:
                self._auto_pause("plane_change")

    def stop(self, *, drain_timeout: float = 2.0) -> dict[str, Any]:
        if self._state not in {"panic_stopped", "stopped"}:
            self.disarm("recording_stop")
        self._stop.set()
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, float(drain_timeout or 0.1)))
        try:
            self.client.close()
        except Exception as error:  # noqa: BLE001
            self.stats["warnings"].append(f"client close failed: {type(error).__name__}: {error}")
        self._state = "stopped" if self._state != "panic_stopped" else "panic_stopped"
        self.stats["mirrorState"] = self._state
        self.stats["stoppedAtUtc"] = utc_now()
        self.stats["status"] = "PASS" if self.stats.get("nonProbeActionCommandCount") and not self.stats.get("panicStopCount") else ("WARN" if self.stats.get("liveMirrorReady") else "FAIL")
        self._merge_safety_diagnostics()
        self.write_summary()
        self.write_status()
        return dict(self.stats)

    def write_status(self) -> None:
        payload = {
            "schema": LIVE_MIRROR_STATUS_SCHEMA,
            "recording_id": self.recording_id,
            "status": self.stats.get("status"),
            "mirrorState": self._state,
            "mirrorProfile": self.stats.get("mirrorProfile"),
            "movementMirroringEnabled": self.stats.get("movementMirroringEnabled"),
            "clickMirroringEnabled": self.stats.get("clickMirroringEnabled"),
            "mirrorClickPolicy": self.stats.get("mirrorClickPolicy"),
            "clickPolicyUsed": self.stats.get("clickPolicyUsed"),
            "clickPolicyDowngraded": self.stats.get("clickPolicyDowngraded"),
            "clickPolicyDowngradeReason": self.stats.get("clickPolicyDowngradeReason"),
            "mapOnlyClickCount": self.stats.get("mapOnlyClickCount"),
            "liveClickWithoutSuppressionCount": self.stats.get("liveClickWithoutSuppressionCount"),
            "duplicateRiskClickCount": self.stats.get("duplicateRiskClickCount"),
            "armMode": self.stats.get("armMode"),
            "armedAtMonotonic": self.stats.get("armedAtMonotonic"),
            "disarmedAtMonotonic": self.stats.get("disarmedAtMonotonic"),
            "disarmReason": self.stats.get("disarmReason"),
            "testDurationSec": self.stats.get("testDurationSec"),
            "recordingPersistent": self.stats.get("recordingPersistent"),
            "activeWindows": self.stats.get("activeWindows") or [],
            "actionsAfterDisarmCount": self.stats.get("actionsAfterDisarmCount"),
            "clicksAfterDisarmCount": self.stats.get("clicksAfterDisarmCount"),
            "movementAfterDisarmCount": self.stats.get("movementAfterDisarmCount"),
            "echoSuppressionEnabled": self.stats.get("echoSuppressionEnabled"),
            "echoSuppressedMoveCount": self.stats.get("echoSuppressedMoveCount"),
            "echoSuppressedClickCount": self.stats.get("echoSuppressedClickCount"),
            "pendingCommandCount": self.stats.get("pendingCommandCount"),
            "feedbackLoopSuspected": self.stats.get("feedbackLoopSuspected"),
            "staleCommandsDropped": self.stats.get("staleCommandsDropped"),
            "queueClearedOnMenuSelectionCount": self.stats.get("queueClearedOnMenuSelectionCount"),
            "queueClearedOnGameActionCount": self.stats.get("queueClearedOnGameActionCount"),
            "queueClearedOnPlaneChangeCount": self.stats.get("queueClearedOnPlaneChangeCount"),
            "mirrorAutoPaused": self.stats.get("mirrorAutoPaused"),
            "autoPauseReason": self.stats.get("autoPauseReason"),
            "autoPauseAtMonotonic": self.stats.get("autoPauseAtMonotonic"),
            "liveMirrorReady": self.stats.get("liveMirrorReady"),
            "liveMirrorActive": self.stats.get("liveMirrorActive"),
            "mirrorPaused": self.stats.get("mirrorPaused"),
            "pauseReason": self.stats.get("pauseReason"),
            "nonProbeActionCommandCount": self.stats.get("nonProbeActionCommandCount"),
            "movementCommandCount": self.stats.get("movementCommandCount"),
            "clickCommandCount": self.stats.get("clickCommandCount"),
            "droppedCommandCount": self.stats.get("droppedCommandCount"),
            "throttledCommandCount": self.stats.get("throttledCommandCount"),
            "panicStopCount": self.stats.get("panicStopCount"),
            "maxCommandsPerSecondObserved": self.stats.get("maxCommandsPerSecondObserved"),
            "maxClickCommandsPerSecondObserved": self.stats.get("maxClickCommandsPerSecondObserved"),
            "ackCount": self.stats.get("ackCount"),
            "port": self.status_payload.get("port") or self.port,
            "protocol": self.status_payload.get("protocol") or "arduino_hid.v1",
            "warnings": self.stats.get("warnings") or [],
            "updatedAtUtc": utc_now(),
        }
        atomic_write_json(self.recording_dir / "arduino_live_mirror_status.json", payload, pretty=self.pretty)

    def write_summary(self) -> None:
        self._merge_safety_diagnostics()
        atomic_write_json(self.recording_dir / "arduino_live_mirror_summary.json", self.stats, pretty=self.pretty)

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                self._refresh_state()
                continue
            if event is None:
                break
            try:
                self._process_event(event)
            except Exception as error:  # noqa: BLE001
                self.stats["errorCount"] += 1
                if self.stats["errorCount"] <= 10:
                    self.stats["warnings"].append(f"event mirror failed: {type(error).__name__}: {error}")
        self.write_summary()

    def _refresh_state(self) -> None:
        if self._state in {"panic_stopped", "stopped"}:
            return
        panic_file = self.settings.panic_stop_file
        if panic_file and Path(panic_file).exists():
            self.panic_stop("panic_stop_file")
            return
        now = self.clock()
        if self._arm_at is not None and now >= self._arm_at and self._state == "disarmed":
            self._state = "armed"
            self._armed_at_monotonic = now
            self.stats["mirrorState"] = self._state
            self.stats["mirrorPaused"] = False
            self.stats["pauseReason"] = None
            self.stats["armedAtMonotonic"] = now
            self.stats["armedAtUtc"] = utc_now()
            self.write_status()
        if self._disarm_at is not None and now >= self._disarm_at and self._state in {"armed", "active"}:
            self.disarm("test_window_elapsed")

    def _metadata(self, event: dict[str, Any], *, command_role: str, chunk_index: int | None = None, chunk_count: int | None = None, source_dx: int = 0, source_dy: int = 0, converted_dx: int = 0, converted_dy: int = 0) -> dict[str, Any]:
        metadata = {
            "probeCommand": False,
            "liveMirrorCommand": True,
            "mirrorCommandRole": command_role,
            "mirrorState": self._state,
            "sourceInputEventSeq": _event_seq(event),
            "sourceInputKind": event.get("kind"),
            "sourceInputElapsedSeconds": event.get("elapsed_seconds"),
            "sourceInputMonotonicTime": event.get("monotonic_time"),
            "elapsed_seconds": event.get("elapsed_seconds"),
            "sourceDx": source_dx,
            "sourceDy": source_dy,
            "convertedDx": converted_dx,
            "convertedDy": converted_dy,
            "sourceWindowTitle": event.get("foreground_window_title"),
            "sourceRegion": event.get("region"),
        }
        device = self._input_device_classification(event)
        if device:
            metadata["inputDeviceClassification"] = device
            metadata["rawInputAttributionAvailable"] = device != "unknown_click_source"
        if chunk_index is not None:
            metadata["chunkIndex"] = chunk_index
            metadata["chunkCount"] = chunk_count
        return metadata

    def _process_event(self, event: dict[str, Any]) -> None:
        self._refresh_state()
        self.stats["processedInputEventCount"] += 1
        kind = str(event.get("kind") or "")
        if kind in IGNORED_CAPTURE_KINDS:
            self.stats["ignoredInputEventCount"] += 1
            return
        if self.stats.get("mirrorAutoPaused") and kind not in IGNORED_CAPTURE_KINDS:
            self.stats["actionsAfterAutoPause"] += 1
        if kind in MOVE_INPUT_KINDS and self._is_stale_move_event(event):
            self.stats["staleCommandsDropped"] += 1
            self._drop_event("stale_queued_movement", event)
            return
        if self._state not in {"armed", "active"}:
            self._drop_event("mirror_disarmed", event)
            return
        drop_reason = self._event_filter_reason(event)
        if drop_reason:
            self._drop_event(drop_reason, event)
            return
        event_key = self._dedupe_key(event)
        if event_key in self._mirrored_event_keys:
            self.stats["duplicateInputEventCount"] += 1
            self._drop_event("duplicate_event", event, duplicate=True)
            return
        if self._is_feedback_event(event):
            self.stats["suppressedFeedbackEventCount"] += 1
            if kind in MOVE_INPUT_KINDS:
                self.stats["echoSuppressedMoveCount"] += 1
            if kind in BUTTON_INPUT_KINDS:
                self.stats["feedbackSuppressedButtonEventCount"] += 1
                self.stats["echoSuppressedClickCount"] += 1
            return
        if kind in MOVE_INPUT_KINDS:
            self._mirrored_event_keys.add(event_key)
            self._mirror_move(event)
        elif kind in BUTTON_INPUT_KINDS:
            self._mirror_button_event(event)
        elif kind in {"key_down", "key_up"} and self.settings.keys_enabled:
            self._mirrored_event_keys.add(event_key)
            self._mirror_key_event(event)
        else:
            self.stats["ignoredInputEventCount"] += 1

    def _is_stale_move_event(self, event: dict[str, Any]) -> bool:
        if self.settings.drop_move_older_than_ms <= 0:
            return False
        try:
            event_time = float(event.get("monotonic_time"))
        except (TypeError, ValueError):
            return False
        age_ms = max(0.0, (self.clock() - event_time) * 1000.0)
        self.stats["queuedCommandMaxAgeMs"] = max(float(self.stats.get("queuedCommandMaxAgeMs") or 0.0), round(age_ms, 3))
        return age_ms > float(self.settings.drop_move_older_than_ms)

    def _extract_plane(self, value: Any) -> int | None:
        if isinstance(value, dict):
            for key in ("plane", "z"):
                if key in value:
                    try:
                        return int(value[key])
                    except (TypeError, ValueError):
                        pass
            for child in value.values():
                plane = self._extract_plane(child)
                if plane is not None:
                    return plane
        elif isinstance(value, list):
            for child in value[:20]:
                plane = self._extract_plane(child)
                if plane is not None:
                    return plane
        return None

    def _clear_pending_input(self, reason: str) -> int:
        cleared = 0
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                break
            if queued is None:
                self._queue.put(None)
                break
            cleared += 1
        if cleared:
            self.stats["ignoredInputEventCount"] += cleared
            self.stats["droppedCommandCount"] += cleared
            drops = dict(self.stats.get("droppedEventsByReason") or {})
            drop_reason = f"queue_cleared_{reason}"
            drops[drop_reason] = int(drops.get(drop_reason) or 0) + cleared
            self.stats["droppedEventsByReason"] = drops
        if reason == "menu_selection":
            self.stats["queueClearedOnMenuSelectionCount"] += 1
        elif reason == "game_action":
            self.stats["queueClearedOnGameActionCount"] += 1
        elif reason == "plane_change":
            self.stats["queueClearedOnPlaneChangeCount"] += 1
        elif reason == "target_action":
            self.stats["queueClearedOnTargetActionCount"] += 1
        return cleared

    def _auto_pause(self, reason: str) -> None:
        if self.stats.get("mirrorAutoPaused"):
            return
        self.stats["mirrorAutoPaused"] = True
        self.stats["autoPauseReason"] = reason
        self.stats["autoPauseAtMonotonic"] = self.clock()
        self._clear_pending_input(reason)
        self.disarm(f"auto_pause_{reason}")

    def _dedupe_key(self, event: dict[str, Any]) -> tuple[Any, ...]:
        seq = _event_seq(event)
        if seq is not None:
            return ("seq", seq)
        point = _point(event)
        try:
            bucket = round(float(_event_time(event)) * 1000 / 20)
        except (TypeError, ValueError):
            bucket = round(self.clock() * 1000 / 20)
        return ("fallback", event.get("kind"), _button(event), bucket, point)

    def _event_filter_reason(self, event: dict[str, Any]) -> str | None:
        title = str(event.get("foreground_window_title") or event.get("windowTitle") or "")
        title_l = title.lower()
        exclude = str(self.settings.exclude_window_title or "").lower()
        allow = str(self.settings.window_title_allow or "").lower()
        if exclude and exclude in title_l and not self.settings.allow_ui_events:
            return "telemetry_ui_window"
        if self.settings.arm_only_when_runelite_focused and allow and allow not in title_l:
            if self.settings.disarm_on_focus_lost and self._state in {"armed", "active"}:
                self.disarm("focus_lost")
            return "foreground_window_not_allowed"
        kind = str(event.get("kind") or "")
        region = str(event.get("region") or "unknown").lower()
        if self.settings.region == "viewport" and region != "viewport":
            return "outside_allowed_region"
        if self.settings.region == "client":
            if region in {"external", "window_chrome"}:
                return "outside_allowed_region"
            if kind in BUTTON_INPUT_KINDS and self.settings.ignore_ui_clicks and region in UI_REGIONS:
                return "ui_control_event"
        if kind in BUTTON_INPUT_KINDS and self.settings.ignore_ui_clicks and region in {"unknown", ""} and not title:
            return "ui_control_event"
        return None

    def _drop_event(self, reason: str, event: dict[str, Any], *, duplicate: bool = False, throttled: bool = False) -> None:
        self.stats["ignoredInputEventCount"] += 1
        self.stats["droppedCommandCount"] += 1
        if reason == "mirror_disarmed":
            kind = str(event.get("kind") or "")
            if kind in MOVE_INPUT_KINDS or kind in BUTTON_INPUT_KINDS or kind in {"key_down", "key_up"}:
                self.stats["actionsAfterDisarmCount"] += 1
            if kind in BUTTON_INPUT_KINDS:
                self.stats["clicksAfterDisarmCount"] += 1
            if kind in MOVE_INPUT_KINDS:
                self.stats["movementAfterDisarmCount"] += 1
        if throttled:
            self.stats["throttledCommandCount"] += 1
        if duplicate:
            self.stats["duplicateCommandCount"] += 1
        if reason in {"telemetry_ui_window", "ui_control_event"}:
            self.stats["uiControlEventsDropped"] += 1
        if reason == "foreground_window_not_allowed":
            self.stats["foregroundFilteredEventsDropped"] += 1
        if reason == "same_button_cooldown":
            self.stats["sameButtonCooldownDrops"] += 1
        drops = dict(self.stats.get("droppedEventsByReason") or {})
        drops[reason] = int(drops.get(reason) or 0) + 1
        self.stats["droppedEventsByReason"] = drops

    def _is_feedback_event(self, event: dict[str, Any]) -> bool:
        now = self.clock()
        kind = str(event.get("kind") or "")
        button = _button(event)
        event_time = now
        try:
            if event.get("monotonic_time") is not None:
                event_time = float(event.get("monotonic_time"))
        except (TypeError, ValueError):
            event_time = now
        if self.settings.echo_suppression:
            while self._pending_echo_commands and float(self._pending_echo_commands[0].get("expiresAt") or 0.0) < event_time:
                self._pending_echo_commands.popleft()
                self.stats["unmatchedEchoCount"] += 1
            for pending in list(self._pending_echo_commands):
                if float(pending.get("sentAt") or 0.0) - 0.001 > event_time:
                    continue
                if float(pending.get("expiresAt") or 0.0) < event_time:
                    continue
                command = str(pending.get("command") or "")
                if kind in MOVE_INPUT_KINDS and command == "MOVE":
                    dx = int(round(float(event.get("dx") or 0)))
                    dy = int(round(float(event.get("dy") or 0)))
                    expected_dx = int(pending.get("dx") or 0)
                    expected_dy = int(pending.get("dy") or 0)
                    if self._movement_matches_echo(dx, dy, expected_dx, expected_dy):
                        self._pending_echo_commands.remove(pending)
                        self.stats["echoMatchedCommandCount"] += 1
                        self.stats["pendingCommandCount"] = len(self._pending_echo_commands)
                        return True
                if kind in BUTTON_INPUT_KINDS and command in BUTTON_KINDS and str(pending.get("button") or "left").lower() == button:
                    self._pending_echo_commands.remove(pending)
                    self.stats["echoMatchedCommandCount"] += 1
                    self.stats["pendingCommandCount"] = len(self._pending_echo_commands)
                    return True
        button_until = self._button_feedback_until.get(button, 0.0)
        if kind in BUTTON_INPUT_KINDS and button_until > 0 and now <= button_until:
            return True
        if now > self._feedback_until:
            return False
        if kind not in MOVE_INPUT_KINDS:
            return False
        dx = int(round(float(event.get("dx") or 0)))
        dy = int(round(float(event.get("dy") or 0)))
        if not dx and not dy:
            return False
        return abs(dx) <= max(abs(self._pending_feedback_dx), 1) + 3 and abs(dy) <= max(abs(self._pending_feedback_dy), 1) + 3

    def _movement_matches_echo(self, dx: int, dy: int, expected_dx: int, expected_dy: int) -> bool:
        if not dx and not dy:
            return False
        if not expected_dx and not expected_dy:
            return False
        max_error = max(0, int(self.settings.echo_max_error_px))
        distance_error = math.hypot(dx - expected_dx, dy - expected_dy)
        if distance_error <= max_error:
            return True
        dot = dx * expected_dx + dy * expected_dy
        if dot <= 0:
            return False
        observed_mag = math.hypot(dx, dy)
        expected_mag = math.hypot(expected_dx, expected_dy)
        if observed_mag <= 0 or expected_mag <= 0:
            return False
        return distance_error <= max(max_error, expected_mag + observed_mag)

    def _mirror_move(self, event: dict[str, Any]) -> None:
        if not self.settings.movement_enabled():
            self._drop_event("movement_disabled_by_profile", event)
            return
        source_dx = int(round(float(event.get("dx") or 0)))
        source_dy = int(round(float(event.get("dy") or 0)))
        converted_dx = int(round(source_dx * self.settings.scale_x * (-1 if self.settings.invert_x else 1)))
        converted_dy = int(round(source_dy * self.settings.scale_y * (-1 if self.settings.invert_y else 1)))
        if max(abs(converted_dx), abs(converted_dy)) < self.settings.move_min_px:
            self.stats["ignoredInputEventCount"] += 1
            return
        safe_step = min(max(1, self.settings.max_step_px), 20)
        chunks = vm_mouse_arduino_mapper.chunk_delta(converted_dx, converted_dy, max_step=safe_step)
        for index, chunk in enumerate(chunks, start=1):
            metadata = self._metadata(
                event,
                command_role="move",
                chunk_index=index,
                chunk_count=len(chunks),
                source_dx=source_dx,
                source_dy=source_dy,
                converted_dx=converted_dx,
                converted_dy=converted_dy,
            )
            if not self._allow_command("MOVE", event=event):
                continue
            record = self.client.send_move(int(chunk.get("dx") or 0), int(chunk.get("dy") or 0), metadata=metadata)
            self._record_command(record)
            self._pending_feedback_dx = int(chunk.get("dx") or 0)
            self._pending_feedback_dy = int(chunk.get("dy") or 0)
            self._feedback_until = self.clock() + max(0, self.settings.feedback_suppression_ms) / 1000.0
            if self.settings.send_interval_ms:
                self.sleep_func(self.settings.send_interval_ms / 1000.0)

    def _mirror_button_event(self, event: dict[str, Any]) -> None:
        if not self.settings.button_enabled():
            self._drop_event("clicks_disabled_by_profile", event)
            return
        kind = str(event.get("kind") or "")
        button = _button(event)
        state = self._button_states[button]
        event_key = self._dedupe_key(event)
        if self.settings.button_mode == "down_up":
            self._mirror_button_down_up(event, state, button, kind, event_key)
            return
        if kind == "mouse_down":
            if state.get("state") == "down":
                self._drop_event("duplicate_button_down", event, duplicate=True)
                return
            state.update({"state": "down", "down_point": _point(event), "dragging": False, "click_emitted": False})
            self._mirrored_event_keys.add(event_key)
            return
        if kind == "drag_start":
            state["dragging"] = True
            state["state"] = "dragging"
            self._mirrored_event_keys.add(event_key)
            return
        if kind == "drag_end":
            state["dragging"] = True
            state["state"] = "released"
            self._mirrored_event_keys.add(event_key)
            if self.settings.allow_drag_clicks:
                self._send_click(event, button)
            else:
                self._drop_event("drag_release_not_click", event)
            state.clear()
            state["state"] = "up"
            return
        if kind == "mouse_up":
            down_point = state.get("down_point")
            drag_distance = _distance(down_point or _point(event), _point(event))
            dragging = bool(state.get("dragging")) or drag_distance > max(1, self.settings.drag_threshold_px) or button == "middle"
            self._mirrored_event_keys.add(event_key)
            if dragging and not self.settings.allow_drag_clicks:
                self._drop_event("drag_release_not_click", event)
                state.clear()
                state["state"] = "up"
                return
            if not state.get("click_emitted"):
                self._send_click(event, button)
                state["click_emitted"] = True
            state.clear()
            state["state"] = "up"
            return
        if kind in {"click", "double_click"}:
            if state.get("click_emitted"):
                self._drop_event("duplicate_click_event", event, duplicate=True)
                self._mirrored_event_keys.add(event_key)
                return
            self._mirrored_event_keys.add(event_key)
            self._send_click(event, button)

    def _mirror_button_down_up(self, event: dict[str, Any], state: dict[str, Any], button: str, kind: str, event_key: tuple[Any, ...]) -> None:
        if kind == "mouse_down":
            if state.get("state") == "down":
                self._drop_event("duplicate_button_down", event, duplicate=True)
                return
            self._mirrored_event_keys.add(event_key)
            if self._allow_command("MOUSE_DOWN", button=button, event=event):
                self._record_command(self.client.send_mouse_down(button, metadata=self._metadata(event, command_role="mouse_down")))
            state["state"] = "down"
            state["down_point"] = _point(event)
            state["dragging"] = False
        elif kind == "drag_start":
            state["dragging"] = True
            self._mirrored_event_keys.add(event_key)
        elif kind == "mouse_up":
            self._mirrored_event_keys.add(event_key)
            if state.get("state") == "down" and self._allow_command("MOUSE_UP", button=button, event=event):
                self._record_command(self.client.send_mouse_up(button, metadata=self._metadata(event, command_role="mouse_up")))
            state.clear()
            state["state"] = "up"
        elif kind in {"click", "double_click"}:
            self._drop_event("click_event_ignored_in_down_up_mode", event)
            self._mirrored_event_keys.add(event_key)
        else:
            self.stats["ignoredInputEventCount"] += 1

    def _send_click(self, event: dict[str, Any], button: str) -> None:
        policy = self._effective_click_policy(event)
        source_suppressed = self._source_suppression_verified(event)
        self.stats["sourceSuppressionAvailable"] = bool(self.stats.get("sourceSuppressionAvailable") or self._raw_input_available(event))
        self.stats["sourceSuppressionVerified"] = bool(self.stats.get("sourceSuppressionVerified") or source_suppressed)
        if policy == "off":
            self._record_virtual_click(event, button, reason="click_policy_off", click_owner="os_click_only")
            self._after_click_command(event, button)
            self.write_summary()
            return
        if policy == "map_only":
            self._record_virtual_click(event, button, reason="click_policy_map_only", click_owner="conversion_trace_click_only")
            self._after_click_command(event, button)
            self.write_summary()
            return
        if policy == "live_requires_source_suppression" and not source_suppressed:
            self.stats["clickPolicyDowngraded"] = True
            self.stats["clickPolicyDowngradeReason"] = "source_suppression_not_verified"
            self._record_virtual_click(event, button, reason="click_policy_source_suppression_not_verified", click_owner="conversion_trace_click_only")
            self._after_click_command(event, button)
            self.write_summary()
            return
        if policy == "arduino_source_only" and self._input_device_classification(event) != "arduino_physical_click_source":
            self.stats["clickPolicyDowngraded"] = True
            self.stats["clickPolicyDowngradeReason"] = "arduino_physical_source_not_verified"
            self._record_virtual_click(event, button, reason="click_policy_arduino_source_not_verified", click_owner="unknown_click_source")
            self._after_click_command(event, button)
            self.write_summary()
            return
        limit = int(self.settings.max_live_clicks_per_recording or 0)
        if limit > 0 and int(self.stats.get("clickCommandCount") or 0) >= limit:
            self.stats["clickPolicyDowngraded"] = True
            self.stats["clickPolicyDowngradeReason"] = "max_live_clicks_per_recording_reached"
            self._record_virtual_click(event, button, reason="click_policy_live_click_limit_reached", click_owner="conversion_trace_click_only")
            self._after_click_command(event, button)
            self.write_summary()
            return
        if not self._allow_command("CLICK", button=button, event=event):
            return
        metadata = self._metadata(event, command_role="click")
        click_owner = "arduino_physical_click_source" if policy == "arduino_source_only" and source_suppressed else "arduino_live_click"
        metadata.update(
            {
                "clickPolicyUsed": policy,
                "clickOwner": click_owner,
                "sourceSuppressionVerified": source_suppressed,
                "duplicateClickLikely": bool(policy == "live_unsuppressed" and not source_suppressed),
            }
        )
        if policy == "live_unsuppressed" and not source_suppressed:
            self.stats["liveUnsuppressedClickCount"] += 1
            self.stats["liveClickWithoutSuppressionCount"] += 1
            self.stats["duplicateRiskClickCount"] += 1
            self.stats["warnings"].append("live_unsuppressed_click_duplicate_risk")
        elif click_owner == "arduino_physical_click_source":
            self.stats["arduinoPhysicalClickCount"] += 1
        self._record_command(self.client.send_click(button, metadata=metadata))
        self._after_click_command(event, button)
        if self.settings.auto_disable_live_clicks_after_first_game_action and button == "left":
            self.stats["liveClicksAutoDisabled"] = True
        feedback_seconds = max(0, self.settings.click_echo_window_ms if self.settings.echo_suppression else self.settings.feedback_suppression_ms) / 1000.0
        self._button_feedback_until[button] = self.clock() + feedback_seconds if feedback_seconds > 0 else 0.0

    def _effective_click_policy(self, event: dict[str, Any]) -> str:
        if self.stats.get("liveClicksAutoDisabled"):
            return "map_only"
        policy = _normalized_click_policy(self.settings.mirror_click_policy)
        if self.settings.require_click_source_suppression and policy == "live_unsuppressed":
            policy = "live_requires_source_suppression"
        self.stats["clickPolicyUsed"] = policy
        return policy

    def _record_virtual_click(self, event: dict[str, Any], button: str, *, reason: str, click_owner: str) -> None:
        now = self.clock()
        metadata = self._metadata(event, command_role="click_mapping")
        record = {
            "schema": arduino_input_bridge.ARDUINO_ACTION_COMMAND_SCHEMA,
            "kind": "command_dropped",
            "recording_id": self.recording_id,
            "command_id": f"map_{time.time_ns()}",
            "command": "CLICK",
            "command_kind": "CLICK",
            "monotonic_time": now,
            "sent_at_monotonic": now,
            "sent_at_utc": utc_now(),
            "port": self.port,
            "baud": self.baud,
            "protocol": "arduino_hid.v1",
            "payload": {"button": button},
            "button": button,
            "dropReason": reason,
            "reason": reason,
            "mapOnlyClick": reason == "click_policy_map_only",
            "clickOwner": click_owner,
            "clickPolicyUsed": self.stats.get("clickPolicyUsed") or self.settings.mirror_click_policy,
            "sourceSuppressionVerified": False,
            "duplicateClickLikely": False,
            "probeCommand": False,
            "liveMirrorCommand": False,
            "conversionTraceOnly": True,
            **metadata,
        }
        record["liveMirrorCommand"] = False
        self.records.append(record)
        writer = getattr(self.client, "writer", None)
        if writer is not None:
            writer.write(record)
        if reason == "click_policy_map_only":
            self.stats["mapOnlyClickCount"] += 1
        elif reason == "click_policy_off":
            self.stats["clickPolicyOffCount"] += 1
        else:
            self.stats["mapOnlyClickCount"] += 1
        self.stats["ignoredInputEventCount"] += 1
        self.stats["droppedCommandCount"] += 1
        drops = dict(self.stats.get("droppedEventsByReason") or {})
        drops[reason] = int(drops.get(reason) or 0) + 1
        self.stats["droppedEventsByReason"] = drops
        self.write_summary()

    def _raw_input_available(self, event: dict[str, Any]) -> bool:
        raw = event.get("rawInputDevice") if isinstance(event.get("rawInputDevice"), dict) else event.get("raw_input_device")
        return bool(isinstance(raw, dict) and raw.get("available"))

    def _input_device_classification(self, event: dict[str, Any]) -> str:
        raw = event.get("rawInputDevice") if isinstance(event.get("rawInputDevice"), dict) else event.get("raw_input_device")
        if not isinstance(raw, dict):
            return "unknown_click_source"
        text = " ".join(str(raw.get(key) or "") for key in ("deviceClass", "deviceName", "name", "manufacturer", "product", "path")).lower()
        if "arduino" in text or "leonardo" in text or "hidbridge" in text:
            return "arduino_physical_click_source"
        if raw.get("available"):
            return str(raw.get("deviceClass") or "os_click_only")
        return "unknown_click_source"

    def _source_suppression_verified(self, event: dict[str, Any]) -> bool:
        return self._input_device_classification(event) == "arduino_physical_click_source"

    def _after_click_command(self, event: dict[str, Any], button: str) -> None:
        now = self.clock()
        if button == "right":
            self._last_right_click_at = now
            return
        if button != "left":
            return
        menu_like = bool(self._last_right_click_at is not None and now - self._last_right_click_at <= 8.0)
        self.stats["actionsBeforeAutoPause"] += 1
        if menu_like and self.settings.clear_queue_on_menu_selection:
            self._clear_pending_input("menu_selection")
        elif self.settings.clear_queue_on_game_action:
            self._clear_pending_input("game_action")
        if menu_like and self.settings.auto_pause_after_menu_selection:
            self._auto_pause("menu_selection")
        elif self.settings.auto_pause_after_first_game_action:
            self._auto_pause("game_action")

    def _mirror_key_event(self, event: dict[str, Any]) -> None:
        key = str(event.get("key_name") or event.get("key") or "")
        if not key:
            self.stats["ignoredInputEventCount"] += 1
            return
        kind = str(event.get("kind") or "")
        command = "KEY_DOWN" if kind == "key_down" else "KEY_UP"
        if not self._allow_command(command, event=event):
            return
        if kind == "key_down":
            self._record_command(self.client.send_key_down(key, metadata=self._metadata(event, command_role="key_down")))
        else:
            self._record_command(self.client.send_key_up(key, metadata=self._metadata(event, command_role="key_up")))

    def _allow_command(self, command: str, *, button: str | None = None, event: dict[str, Any]) -> bool:
        self._refresh_state()
        if self._state == "panic_stopped":
            self._drop_event("panic_stopped", event)
            return False
        if self._state not in {"armed", "active"}:
            self._drop_event("mirror_disarmed", event)
            return False
        now = self.clock()
        window = max(0.1, self.settings.panic_window_ms / 1000.0)
        if _window_count(self._command_times, now, window) >= self.settings.panic_command_threshold:
            self.panic_stop("panic_command_threshold")
            self._drop_event("panic_stopped", event)
            return False
        if _window_count(self._command_times, now, window) >= self.settings.max_burst_commands:
            self._drop_event("rate_limited", event, throttled=True)
            return False
        if _window_count(self._command_times, now, 1.0) >= self.settings.max_total_commands_per_second:
            self._drop_event("rate_limited", event, throttled=True)
            return False
        if command == "MOVE" and _window_count(self._move_times, now, 1.0) >= self.settings.max_move_commands_per_second:
            self._drop_event("rate_limited", event, throttled=True)
            return False
        if command in BUTTON_KINDS:
            if _window_count(self._button_command_times, now, 1.0) >= self.settings.max_button_commands_per_second:
                self._drop_event("rate_limited", event, throttled=True)
                return False
            if command == "CLICK":
                if _window_count(self._click_times, now, 1.0) >= self.settings.max_clicks_per_second:
                    self._drop_event("rate_limited", event, throttled=True)
                    return False
                if button and now - self._last_click_at.get(button, 0.0) < self.settings.click_cooldown_ms / 1000.0:
                    self._drop_event("click_cooldown", event, throttled=True)
                    return False
            if command != "MOUSE_UP" and button and now - self._last_button_command_at.get(button, 0.0) < self.settings.same_button_cooldown_ms / 1000.0:
                self._drop_event("same_button_cooldown", event, throttled=True)
                return False
        return True

    def _record_command(self, record: dict[str, Any]) -> None:
        now = self.clock()
        self.records.append(dict(record))
        self._command_times.append(now)
        command = _command_name(record)
        if command in arduino_input_bridge.ARDUINO_MOVEMENT_COMMANDS:
            self._move_times.append(now)
            self.stats["movementCommandCount"] += 1
        elif command in arduino_input_bridge.ARDUINO_CLICK_COMMANDS:
            self._button_command_times.append(now)
            self.stats["clickCommandCount"] += 1
            button = str(record.get("button") or record.get("payload", {}).get("button") or "left").lower()
            self._last_button_command_at[button] = now
            if command == "CLICK":
                self._click_times.append(now)
                self._last_click_at[button] = now
        elif command in {"KEY_DOWN", "KEY_UP", "KEY_PRESS", "HOLD_KEYS"}:
            self.stats["keyboardCommandCount"] += 1
        if not record.get("probeCommand"):
            self.stats["nonProbeActionCommandCount"] += 1
        self.stats["mirroredInputEventCount"] += 1
        self.stats["liveMirrorActive"] = True
        if self._state == "armed":
            self._state = "active"
            self.stats["mirrorState"] = self._state
        if record.get("ack_received"):
            self.stats["ackCount"] += 1
        if record.get("error"):
            self.stats["errorCount"] += 1
        self._track_expected_echo(record, now)
        self.stats["maxCommandsPerSecondObserved"] = max(self.stats.get("maxCommandsPerSecondObserved") or 0, _window_count(self._command_times, now, 1.0))
        self.stats["maxClickCommandsPerSecondObserved"] = max(self.stats.get("maxClickCommandsPerSecondObserved") or 0, _window_count(self._click_times, now, 1.0))
        self.stats["maxMoveCommandsPerSecondObserved"] = max(self.stats.get("maxMoveCommandsPerSecondObserved") or 0, _window_count(self._move_times, now, 1.0))
        self.write_summary()

    def _track_expected_echo(self, record: dict[str, Any], now: float) -> None:
        if not self.settings.echo_suppression:
            return
        command = _command_name(record)
        if command == "MOVE":
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            self._pending_echo_commands.append(
                {
                    "command": "MOVE",
                    "sentAt": now,
                    "expiresAt": now + max(0, self.settings.echo_window_ms) / 1000.0,
                    "dx": int(record.get("dx") or payload.get("dx") or 0),
                    "dy": int(record.get("dy") or payload.get("dy") or 0),
                    "commandId": record.get("command_id"),
                }
            )
        elif command in BUTTON_KINDS:
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            self._pending_echo_commands.append(
                {
                    "command": command,
                    "sentAt": now,
                    "expiresAt": now + max(0, self.settings.click_echo_window_ms) / 1000.0,
                    "button": str(record.get("button") or payload.get("button") or "left").lower(),
                    "commandId": record.get("command_id"),
                }
            )
        while len(self._pending_echo_commands) > self.settings.max_queue_size:
            self._pending_echo_commands.popleft()
            self.stats["unmatchedEchoCount"] += 1
        self.stats["pendingCommandCount"] = len(self._pending_echo_commands)

    def _merge_safety_diagnostics(self) -> None:
        diagnostics = _rate_diagnostics(self.records)
        for key, value in diagnostics.items():
            if key == "droppedCommandCount":
                self.stats[key] = max(int(self.stats.get(key) or 0), int(value or 0))
            elif key == "droppedCommandsByReason":
                existing = dict(self.stats.get("droppedEventsByReason") or {})
                merged = {
                    str(reason): max(int(existing.get(reason) or 0), int(count or 0))
                    for reason, count in dict(value or {}).items()
                }
                for reason, count in existing.items():
                    merged.setdefault(str(reason), int(count or 0))
                self.stats["droppedEventsByReason"] = dict(sorted(merged.items()))
            elif key == "liveMirrorSafetyClassifications":
                current = set(self.stats.get("liveMirrorSafetyClassifications") or [])
                self.stats[key] = sorted(current.union(value or []))
            elif isinstance(value, int):
                self.stats[key] = max(int(self.stats.get(key) or 0), value)
            else:
                self.stats[key] = value


def build_summary_from_commands(commands: list[dict[str, Any]], *, status_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    non_probe = [record for record in commands if record.get("liveMirrorCommand") and not record.get("probeCommand") and record.get("kind") != "command_dropped"]
    movement = [record for record in non_probe if _command_name(record) in arduino_input_bridge.ARDUINO_MOVEMENT_COMMANDS]
    clicks = [record for record in non_probe if _command_name(record) in arduino_input_bridge.ARDUINO_CLICK_COMMANDS]
    map_only_clicks = [record for record in commands if record.get("mapOnlyClick") or record.get("dropReason") in {"click_policy_map_only", "click_policy_source_suppression_not_verified", "click_policy_arduino_source_not_verified", "click_policy_live_click_limit_reached"}]
    live_unsuppressed_clicks = [
        record
        for record in clicks
        if record.get("clickPolicyUsed") == "live_unsuppressed" or record.get("duplicateClickLikely")
    ]
    live_click_without_suppression = [
        record
        for record in clicks
        if not record.get("sourceSuppressionVerified") and record.get("clickOwner") != "arduino_physical_click_source"
    ]
    errors = [record for record in non_probe if record.get("error")]
    diagnostics = _rate_diagnostics(commands)
    status = "PASS" if non_probe and not errors and "live_mirror_click_storm" not in diagnostics.get("liveMirrorSafetyClassifications", []) else ("WARN" if non_probe else "WARN")
    return {
        "schema": LIVE_MIRROR_SUMMARY_SCHEMA,
        "status": status,
        "generatedAtUtc": utc_now(),
        "liveMirrorRequested": bool(non_probe),
        "liveMirrorActive": bool(non_probe),
        "liveMirrorVerified": False,
        "mirrorProfile": None,
        "mirrorClickPolicy": _last_nonempty([record.get("clickPolicyUsed") for record in commands]),
        "clickPolicyUsed": _last_nonempty([record.get("clickPolicyUsed") for record in commands]),
        "clickPolicyDowngraded": any(record.get("dropReason") in {"click_policy_source_suppression_not_verified", "click_policy_arduino_source_not_verified", "click_policy_live_click_limit_reached"} for record in commands),
        "clickPolicyDowngradeReason": _last_nonempty([record.get("dropReason") for record in map_only_clicks if record.get("dropReason") != "click_policy_map_only"]),
        "mapOnlyClickCount": len(map_only_clicks),
        "liveUnsuppressedClickCount": len(live_unsuppressed_clicks),
        "liveClickWithoutSuppressionCount": len(live_click_without_suppression),
        "duplicateRiskClickCount": len([record for record in clicks if record.get("duplicateClickLikely")]),
        "arduinoPhysicalClickCount": len([record for record in clicks if record.get("clickOwner") == "arduino_physical_click_source"]),
        "echoSuppressionEnabled": False,
        "echoSuppressedMoveCount": 0,
        "echoSuppressedClickCount": 0,
        "feedbackLoopSuspected": "live_mirror_feedback_suspected" in diagnostics.get("liveMirrorSafetyClassifications", []),
        "staleCommandsDropped": 0,
        "queueClearedOnMenuSelectionCount": 0,
        "queueClearedOnGameActionCount": 0,
        "queueClearedOnPlaneChangeCount": 0,
        "mirrorAutoPaused": False,
        "autoPauseReason": None,
        "nonProbeActionCommandCount": len(non_probe),
        "movementCommandCount": len(movement),
        "clickCommandCount": len(clicks),
        "keyboardCommandCount": sum(1 for record in non_probe if _command_name(record).startswith("KEY")),
        "ackCount": sum(1 for record in non_probe if record.get("ack_received")),
        "errorCount": len(errors),
        "port": _last_nonempty([record.get("port") for record in commands]) or (status_payload or {}).get("port"),
        "protocol": _last_nonempty([record.get("protocol") for record in commands]) or (status_payload or {}).get("protocol"),
        "warnings": [str(record.get("error")) for record in errors if record.get("error")],
        **diagnostics,
    }


def _last_nonempty(values: list[Any]) -> Any:
    for value in reversed(values):
        if value not in (None, "", [], {}):
            return value
    return None
