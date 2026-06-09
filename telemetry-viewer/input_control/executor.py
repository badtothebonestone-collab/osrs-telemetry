from __future__ import annotations

import json
import random
import secrets
import time
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import client_tick_core
from . import camera_control
from .action_lifecycle import (
    ActionLifecycleState,
    expected_result_for_action,
    is_waiting_for_result,
    lifecycle_after_execution,
    lifecycle_state_for_proposal,
    proposal_action_id,
    verify_expected_result,
)
from .action_proposal import ActionProposal, build_action_proposal
from .input_geometry import CLICK_FAILURE_BUCKETS, resolve_screen_click_point, validate_screen_point_inside_geometry
from .backend_pyautogui import PyAutoGuiBackend
from .backend_pydirectinput import PyDirectInputBackend
from .backend_arduino_hid import DEFAULT_COMMAND_TIMEOUT_MS, ArduinoHIDBackend, check_arduino_monitor_status
from .human_input_controller import HumanInputController, HumanInputContext
from .input_integrity import build_firmware_safety, input_integrity_delta
from .mouse_movement import MouseMovementProfile, MousePoint, MouseTarget, plan_mouse_movement
from .visual_debug_bundle import VisualDebugBundleWriter
from live_readiness_core import build_readiness_report


SCHEMA = "input_control_execution_result.v1"
LOOP_SCHEMA = "input_control_execution_loop_result.v1"
PLUGIN_SNAPSHOT_REQUEST_SCHEMA = "plugin_snapshot_request.v1"


def _wall_time_millis() -> int:
    return int(time.time() * 1000)


@dataclass
class HoverConfirmationOptions:
    enabled: bool = False
    hover_only: bool = False
    snapshot_url: str = "http://127.0.0.1:8893"
    timeout_ms: int = 120
    poll_ms: int = 10
    tolerance_px: int = 3
    click_hold_ms: int = 0
    request_timeout_seconds: float = 0.2
    client_tick_debug: bool = False
    client_tick_tail: int = 0
    menu_entry_limit: int = 5
    require_clicked_menu_match: bool = False


NAVIGATION_ACTIONS = {"navigate_to_service", "return_to_resource_area"}
ROUTE_TRANSITION_ACTIONS = {"interact_service_route_object"}
MOVEMENT_SAFETY_BLOCK_REASONS = {
    "movement_safety_screen_point_unavailable",
    "movement_safety_region_unavailable",
    "screen_click_point_outside_movement_safety_region",
}
FATAL_NO_CLICK_BLOCK_REASONS = {*MOVEMENT_SAFETY_BLOCK_REASONS, "hover_movement_failed"}


HoverMenuMatchResult = client_tick_core.HoverMenuMatchResult


@dataclass
class ExecutionResult:
    status: str
    proposed_action: str
    dry_run: bool
    action_id: str | None = None
    executed: bool = False
    backend_name: str | None = None
    movement_profile: str | None = None
    proposal: dict[str, Any] | None = None
    movement_plan: dict[str, Any] | None = None
    click_point_resolution: dict[str, Any] | None = None
    commands: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    lifecycle_state: dict[str, Any] | None = None
    expected_result: dict[str, Any] | None = None
    observed_result: dict[str, Any] | None = None
    hover_confirmation: dict[str, Any] | None = None
    action_trace: dict[str, Any] | None = None
    verification_status: str | None = None
    next_allowed_at: str | None = None
    cooldown_remaining_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "proposedAction": self.proposed_action,
            "actionId": self.action_id,
            "dryRun": self.dry_run,
            "executed": self.executed,
            "backend": self.backend_name,
            "movementProfile": self.movement_profile,
            "proposal": self.proposal,
            "movementPlan": self.movement_plan,
            "clickPointResolution": self.click_point_resolution,
            "commands": list(self.commands),
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
            "verification": self.verification,
            "readiness": self.readiness,
            "lifecycleState": self.lifecycle_state,
            "expectedResult": self.expected_result,
            "observedResult": self.observed_result,
            "hoverConfirmation": self.hover_confirmation,
            "actionTrace": self.action_trace,
            "verificationStatus": self.verification_status,
            "nextAllowedAt": self.next_allowed_at,
            "cooldownRemainingMs": self.cooldown_remaining_ms,
        }


@dataclass
class LoopExecutionResult:
    status: str
    dry_run: bool
    action_results: list[ExecutionResult] = field(default_factory=list)
    lifecycle_state: dict[str, Any] | None = None
    loop_summary: dict[str, Any] = field(default_factory=dict)
    reason: str = "not_applicable"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    max_actions: int = 1
    max_runtime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LOOP_SCHEMA,
            "status": self.status,
            "dryRun": self.dry_run,
            "executedActionCount": sum(1 for result in self.action_results if result.executed),
            "actionResultCount": len(self.action_results),
            "maxActions": self.max_actions,
            "maxRuntimeSeconds": self.max_runtime_seconds,
            "reason": self.reason,
            "lifecycleState": self.lifecycle_state,
            "loopSummary": dict(self.loop_summary),
            "actionResults": [result.to_dict() for result in self.action_results],
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
        }


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def post_json(url: str, payload: dict[str, Any], timeout: float = 0.2) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    decoded = json.loads(body)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def daemon_context_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/context"


def daemon_action_summary_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/action-summary"


def action_context_request_payload(*, task: str = "woodcutting") -> dict[str, Any]:
    return {
        "schema": "context_request.v1",
        "task": task,
        "needs": [
            "baseline",
            "inventory",
            "activity",
            "candidates",
            "world_model_summary",
            "resource_object_census",
            "service_object_census",
            "route_object_census",
            "pathing_frontier",
            "knowledge_current_debug_context",
            "knowledge_resource_candidates",
            "knowledge_service_candidates",
            "knowledge_route_objects",
            "knowledge_path_frontier",
            "click_plan",
            "route_monitor",
            "bank_state",
            "woodcutting_loop_lifecycle",
            "interruption_lifecycle",
        ],
        "maxCandidates": 8,
        "maxEvents": 5,
        "responseMode": "compact",
    }


def fetch_action_context(daemon_url: str, timeout: float = 1.0, *, task: str = "woodcutting") -> dict[str, Any]:
    try:
        summary = fetch_json(daemon_action_summary_url(daemon_url), timeout=timeout)
        if isinstance(summary, dict) and summary.get("schema") == "live_core_action_summary.v1":
            proposal = summary.get("actionProposal") if isinstance(summary.get("actionProposal"), dict) else {}
            return {
                "schema": "context_response.v1",
                "status": summary.get("status") or "WARN",
                "latestTick": summary.get("latestTick"),
                "inventory": summary.get("inventory") if isinstance(summary.get("inventory"), dict) else {},
                "knowledgeCurrentDebugContext": {
                    "schema": "knowledge_fabric_current_debug_context.v1",
                    "source": "live_core_action_summary",
                    "data": {
                        "actionProposal": proposal,
                        "liveStatus": summary.get("liveStatus") if isinstance(summary.get("liveStatus"), dict) else {},
                        "readiness": summary.get("readiness") if isinstance(summary.get("readiness"), dict) else {},
                    },
                },
                "warnings": summary.get("warnings") if isinstance(summary.get("warnings"), list) else [],
                "missingCapabilities": summary.get("missingCapabilities") if isinstance(summary.get("missingCapabilities"), list) else [],
            }
    except Exception:
        pass
    return post_json(daemon_context_url(daemon_url), action_context_request_payload(task=task), timeout=timeout)


def plugin_snapshot_endpoint_url(snapshot_url: str) -> str:
    base = (snapshot_url or "http://127.0.0.1:8893").rstrip("/")
    return base if base.endswith("/snapshot") else base + "/snapshot"


def fetch_plugin_snapshot(
    snapshot_url: str,
    timeout: float = 0.2,
    *,
    client_tick_tail: int = 0,
    menu_entry_limit: int = 5,
    tile_projection_requests: list[dict[str, Any]] | None = None,
    extra_needs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    needs = ["baseline", "interaction_hot"]
    for need in extra_needs or ():
        normalized_need = str(need or "").strip()
        if normalized_need and normalized_need not in needs:
            needs.append(normalized_need)
    tail = max(0, int(client_tick_tail or 0))
    if tail > 0:
        needs.append("client_tick_tail")
    request = {
        "schema": PLUGIN_SNAPSHOT_REQUEST_SCHEMA,
        "needs": needs,
        "maxAgeTicks": 5,
        "responseMode": "compact",
        "includeCollisionWindow": False,
        "includeMenuEntries": True,
        "menuEntryLimit": max(0, int(menu_entry_limit or 0)),
    }
    if tile_projection_requests:
        request["tileProjectionRequests"] = list(tile_projection_requests[:16])
    if tail > 0:
        request["maxClientTickSamples"] = tail
        request["maxMenuSamples"] = tail
        request["maxClickedSamples"] = tail
    return post_json(plugin_snapshot_endpoint_url(snapshot_url), request, timeout=timeout)


def backend_from_name(name: str | None, **kwargs: Any):
    normalized = str(name or "pyautogui").strip().lower()
    if normalized == "arduino":
        return ArduinoHIDBackend(
            port=kwargs.get("arduino_port"),
            baud=int(kwargs.get("arduino_baud", 115200) or 115200),
            handshake_timeout_ms=int(kwargs.get("arduino_handshake_timeout_ms", 2000) or 2000),
            command_timeout_ms=int(kwargs.get("arduino_command_timeout_ms", DEFAULT_COMMAND_TIMEOUT_MS) or DEFAULT_COMMAND_TIMEOUT_MS),
            session_token=kwargs.get("arduino_session_token"),
            fail_closed=bool(kwargs.get("arduino_fail_closed", True)),
            vid=kwargs.get("arduino_vid"),
            pid=kwargs.get("arduino_pid"),
        )
    if normalized == "pydirectinput":
        return PyDirectInputBackend(**kwargs)
    return PyAutoGuiBackend(**kwargs)


def backend_from_options(options: Any):
    name = getattr(options, "backend", None) or "pyautogui"
    if str(name).strip().lower() == "arduino":
        return backend_from_name(
            name,
            arduino_port=getattr(options, "arduino_port", None),
            arduino_baud=getattr(options, "arduino_baud", 115200),
            arduino_handshake_timeout_ms=getattr(options, "arduino_handshake_timeout_ms", 2000),
            arduino_command_timeout_ms=getattr(options, "arduino_command_timeout_ms", DEFAULT_COMMAND_TIMEOUT_MS),
            arduino_session_token=getattr(options, "arduino_session_token", None),
            arduino_fail_closed=getattr(options, "arduino_fail_closed", True),
            arduino_vid=getattr(options, "arduino_vid", None),
            arduino_pid=getattr(options, "arduino_pid", None),
        )
    return backend_from_name(
        name,
        focus_runelite=bool(getattr(options, "focus_runelite", False)),
        window_title_filter=str(getattr(options, "window_title_filter", "RuneLite")),
    )


def run_camera_self_test(
    snapshot_url: str,
    options: Any,
    *,
    backend: Any,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
) -> dict[str, Any]:
    method_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    live_input_status: dict[str, Any] | None = None
    try:
        live_input_status = _start_live_input_session(options, backend)
    except Exception as error:  # noqa: BLE001
        status = _live_input_status(options, backend)
        status["status"] = "FAIL"
        status["blockReason"] = status.get("blockReason") or "live_input_blocked"
        status["startError"] = f"{type(error).__name__}: {error}"
        return {
            "schema": "camera_self_test.v1",
            "status": "FAIL",
            "selectedMethod": None,
            "methodResults": [],
            "humanInput": {
                "liveInputBackend": _backend_name(backend),
                "liveInputBackendRequired": _live_input_required_for_backend(options, backend),
                "softwareInputAllowed": _software_input_allowed(options),
                "directBackendBypassCount": 0,
            },
            "liveInput": status,
            "warnings": [str(status.get("blockReason") or error)],
        }
    input_controller = _input_controller_from_options(backend, options, sleep_func=sleep_func, monotonic_func=time.monotonic)
    hold_ms = max(120, int(getattr(options, "camera_sample_interval_ms", None) or getattr(options, "camera_probe_ms", 120) or 120) * 4)
    methods = camera_control.camera_method_sequence(getattr(options, "camera_method", "auto"))
    for method in methods:
        result: dict[str, Any] = {"method": method, "command": "yaw_right", "holdMillis": hold_ms}
        try:
            before_snapshot = snapshot_fetch_func(snapshot_url, timeout=0.35, menu_entry_limit=0)
            before_viewport = _camera_viewport_from_snapshot(before_snapshot)
            before_pose = _camera_pose_from_viewport(before_viewport)
            spec = camera_control.camera_input_spec(method=method, command="yaw_right")
            if spec.continuous_hover:
                with input_controller.hold_keys(spec.keys, context=HumanInputContext(reason="camera_self_test")):
                    sleep_func(hold_ms / 1000.0)
            else:
                input_controller.camera_drag_pulse(spec, duration_ms=hold_ms)
            after_snapshot = snapshot_fetch_func(snapshot_url, timeout=0.35, menu_entry_limit=0)
            after_viewport = _camera_viewport_from_snapshot(after_snapshot)
            after_pose = _camera_pose_from_viewport(after_viewport)
            yaw_delta = camera_control.camera_angle_delta(before_pose.get("cameraYaw"), after_pose.get("cameraYaw"))
            pitch_delta = None
            if before_pose.get("cameraPitch") is not None and after_pose.get("cameraPitch") is not None:
                pitch_delta = int(after_pose["cameraPitch"]) - int(before_pose["cameraPitch"])
            moved = bool(abs(int(yaw_delta or 0)) > 0 or abs(int(pitch_delta or 0)) > 0)
            result.update(
                {
                    "status": "PASS" if moved else "WARN",
                    "reason": "camera_delta_observed" if moved else "no_camera_delta",
                    "before": before_pose,
                    "after": after_pose,
                    "yawDelta": yaw_delta,
                    "pitchDelta": pitch_delta,
                }
            )
            if getattr(options, "camera_test_return", False):
                return_spec = camera_control.camera_input_spec(method=method, command="yaw_left")
                try:
                    if return_spec.continuous_hover:
                        with input_controller.hold_keys(return_spec.keys, context=HumanInputContext(reason="camera_self_test_return")):
                            sleep_func(hold_ms / 1000.0)
                    else:
                        input_controller.camera_drag_pulse(return_spec, duration_ms=hold_ms)
                    result["returnAttempted"] = True
                except Exception as error:  # noqa: BLE001
                    result["returnAttempted"] = True
                    result["returnWarning"] = f"{type(error).__name__}: {error}"
            method_results.append(result)
            if moved:
                break
        except Exception as error:  # noqa: BLE001
            result.update({"status": "FAIL", "reason": "camera_method_failed", "warning": f"{type(error).__name__}: {error}"})
            method_results.append(result)
            warnings.append(f"{method}: {result['warning']}")
    selected = next((item.get("method") for item in method_results if item.get("status") == "PASS"), None)
    try:
        _finish_live_input_session(backend, live_input_status, options=options)
    finally:
        payload = {
            "schema": "camera_self_test.v1",
            "status": "PASS" if selected else "WARN" if method_results else "FAIL",
            "selectedMethod": selected,
            "methodResults": method_results,
            "humanInput": input_controller.metrics(),
            "liveInput": live_input_status,
            "warnings": warnings,
        }
    output = Path("interaction_geometry") / "live" / "camera_calibration.json"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
        payload["calibrationPath"] = str(output)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"camera calibration write failed: {type(error).__name__}: {error}")
    return payload


def _movement_profile_from_options(options: Any) -> str | MouseMovementProfile:
    name = str(getattr(options, "movement_profile", "linear_debug"))
    if getattr(options, "seed", None) is not None:
        return MouseMovementProfile(name=name, seed=int(getattr(options, "seed")))
    return name


def _input_profile_from_options(options: Any | None) -> str:
    return str(getattr(options, "input_profile", "instant_debug") or "instant_debug")


def _live_input_operation_requested(options: Any | None) -> bool:
    if options is None:
        return False
    return bool(getattr(options, "execute", False) or getattr(options, "hover_only", False) or getattr(options, "camera_self_test", False))


def _software_input_allowed(options: Any | None) -> bool:
    if options is None:
        return False
    return bool(
        getattr(options, "allow_software_input", False)
        or getattr(options, "unsafe_allow_pyautogui_live", False)
        or getattr(options, "unsafe_allow_software_live", False)
    )


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", backend.__class__.__name__) or "unknown")


def _is_arduino_backend(backend: Any) -> bool:
    return bool(getattr(backend, "arduino_hid_backend", False) or _backend_name(backend).lower() == "arduino")


def _is_software_backend(backend: Any) -> bool:
    name = _backend_name(backend).lower()
    return bool(getattr(backend, "software_input_backend", False) or name in {"pyautogui", "pydirectinput"})


def _is_known_live_backend(backend: Any) -> bool:
    return bool(_is_arduino_backend(backend) or _is_software_backend(backend))


def _live_input_required_for_backend(options: Any | None, backend: Any) -> bool:
    return bool(_live_input_operation_requested(options) and _is_known_live_backend(backend))


def _arduino_monitor_status_source(options: Any | None) -> str | None:
    if options is None:
        return None
    return (
        getattr(options, "arduino_monitor_status", None)
        or getattr(options, "arduino_monitor_status_path", None)
        or getattr(options, "input_integrity_status_path", None)
    )


def _input_backend_status_path(options: Any | None) -> Path:
    raw = getattr(options, "input_integrity_backend_status_path", None) if options is not None else None
    return Path(raw) if raw else Path("interaction_geometry") / "live" / "arduino_backend_status.json"


def _write_input_backend_status(options: Any | None, backend: Any, *, armed: bool, direct_backend_bypass_count: int = 0) -> None:
    try:
        path = _input_backend_status_path(options)
        path.parent.mkdir(parents=True, exist_ok=True)
        status = backend.status() if callable(getattr(backend, "status", None)) else {}
        payload = {
            "schema": "arduino_backend_runtime_status.v1",
            "generatedAtMillis": _wall_time_millis(),
            "liveInputBackend": _backend_name(backend),
            "arduinoArmed": bool(armed),
            "directBackendBypassCount": int(direct_backend_bypass_count or 0),
            "backendStatus": status if isinstance(status, dict) else None,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _input_integrity_fail_on_injected(options: Any | None) -> bool:
    if options is None:
        return True
    return bool(getattr(options, "input_integrity_fail_on_injected", True))


def _input_integrity_fail_on_bypass(options: Any | None) -> bool:
    if options is None:
        return True
    return bool(getattr(options, "input_integrity_fail_on_bypass", True))


def _input_integrity_counts(status: dict[str, Any] | None) -> dict[str, int]:
    status = status if isinstance(status, dict) else {}
    flags = status.get("injectionFlags") if isinstance(status.get("injectionFlags"), dict) else {}
    mouse_injected = int(flags.get("mouseInjectedCount") or 0)
    keyboard_injected = int(flags.get("keyboardInjectedCount") or 0)
    mouse_lower = int(flags.get("mouseLowerIlInjectedCount") or 0)
    keyboard_lower = int(flags.get("keyboardLowerIlInjectedCount") or 0)
    return {
        "mouseInjectedCount": mouse_injected,
        "keyboardInjectedCount": keyboard_injected,
        "mouseLowerIlInjectedCount": mouse_lower,
        "keyboardLowerIlInjectedCount": keyboard_lower,
        "injectedEvents": int(status.get("injectedEvents") or mouse_injected + keyboard_injected),
        "lowerIlInjectedEvents": int(status.get("lowerIlInjectedEvents") or mouse_lower + keyboard_lower),
    }


def _input_integrity_phase_report(status: dict[str, Any]) -> dict[str, Any]:
    before = status.get("inputIntegrityStatusBefore") if isinstance(status.get("inputIntegrityStatusBefore"), dict) else status.get("inputIntegrityStatus")
    after = status.get("inputIntegrityStatusAfter") if isinstance(status.get("inputIntegrityStatusAfter"), dict) else {}
    before_counts = _input_integrity_counts(before if isinstance(before, dict) else {})
    after_counts = _input_integrity_counts(after if isinstance(after, dict) else {})
    delta = status.get("inputIntegrityDelta") if isinstance(status.get("inputIntegrityDelta"), dict) else {}
    injected_delta = int(delta.get("mouseInjectedCountDelta") or 0) + int(delta.get("keyboardInjectedCountDelta") or 0)
    lower_delta = int(delta.get("lowerIlInjectedCountDelta") or 0)
    direct_delta = int(delta.get("directBackendBypassCountDelta") or 0)
    hard_blocker = bool(injected_delta > 0 or lower_delta > 0 or direct_delta > 0)
    return {
        "schema": "input_integrity_phase_report.v1",
        "policy": "phase_aware_live_window_only",
        "operator_phase": {
            "operatorInjectedEvents": before_counts["injectedEvents"],
            "operatorLowerIlInjectedEvents": before_counts["lowerIlInjectedEvents"],
            "blocking": False,
            "classification": "operatorInjectedEvents" if before_counts["injectedEvents"] or before_counts["lowerIlInjectedEvents"] else "none",
        },
        "pre_live_phase": {
            "baselineEstablished": isinstance(before, dict) and bool(before),
            "monitorPassAtBaseline": before.get("monitorPass") if isinstance(before, dict) else None,
            "injectedEventsAtBaseline": before_counts["injectedEvents"],
            "lowerIlInjectedEventsAtBaseline": before_counts["lowerIlInjectedEvents"],
            "blocking": False,
            "rule": "pre-live injected totals are baseline counts; only live deltas block",
        },
        "live_action_phase": {
            "injectedEventsDelta": injected_delta,
            "lowerIlInjectedEventsDelta": lower_delta,
            "directBackendBypassCountDelta": direct_delta,
            "hardBlocker": hard_blocker,
            "blockingReason": "live_input_integrity_delta" if hard_blocker else None,
        },
        "post_live_phase": {
            "monitorPassAfter": after.get("monitorPass") if isinstance(after, dict) else None,
            "injectedEventsAfter": after_counts["injectedEvents"],
            "lowerIlInjectedEventsAfter": after_counts["lowerIlInjectedEvents"],
            "stopAllSent": bool(status.get("stopAllSent")),
            "disarmed": bool(status.get("arduinoDisarmed")),
        },
    }


def _read_input_integrity_status(
    options: Any | None,
    backend: Any,
    *,
    direct_backend_bypass_count: int = 0,
    require_armed: bool = False,
    fail_on_injected: bool | None = None,
) -> dict[str, Any]:
    return check_arduino_monitor_status(
        require_monitor=bool(getattr(options, "arduino_require_monitor", False)),
        status_path=_arduino_monitor_status_source(options),
        expected_vid=getattr(options, "arduino_vid", None),
        expected_pid=getattr(options, "arduino_pid", None),
        expected_com_port=getattr(options, "arduino_port", None),
        live_input_backend=_backend_name(backend),
        arduino_armed=bool(getattr(backend, "armed", False)),
        software_input_allowed=_software_input_allowed(options),
        direct_backend_bypass_count=direct_backend_bypass_count,
        fail_on_injected=_input_integrity_fail_on_injected(options) if fail_on_injected is None else bool(fail_on_injected),
        fail_on_bypass=_input_integrity_fail_on_bypass(options),
        require_armed=require_armed,
        max_event_age_ms=int(getattr(options, "arduino_monitor_max_age_ms", 3000) or 3000),
    )


def _live_input_status(options: Any | None, backend: Any) -> dict[str, Any]:
    requested = _live_input_operation_requested(options)
    backend_name = _backend_name(backend)
    required = _live_input_required_for_backend(options, backend)
    software_allowed = _software_input_allowed(options)
    status: dict[str, Any] = {
        "schema": "live_input_policy.v1",
        "liveInputBackend": backend_name,
        "liveInputBackendRequired": required,
        "softwareInputAllowed": software_allowed,
        "requestedLiveInput": requested,
        "backendIsArduino": _is_arduino_backend(backend),
        "backendIsSoftware": _is_software_backend(backend),
        "status": "PASS",
        "blockReason": None,
        "monitor": None,
        "arduino": backend.status() if callable(getattr(backend, "status", None)) else None,
    }
    if not requested or not required:
        return status
    if _is_software_backend(backend) and not software_allowed:
        status["status"] = "FAIL"
        status["blockReason"] = "software_input_blocked"
        return status
    if not _is_arduino_backend(backend) and not software_allowed:
        status["status"] = "FAIL"
        status["blockReason"] = "arduino_backend_required"
        return status
    if _is_arduino_backend(backend) and bool(getattr(options, "arduino_require_monitor", False)):
        monitor = _read_input_integrity_status(options, backend, direct_backend_bypass_count=0, require_armed=False, fail_on_injected=False)
        status["monitor"] = monitor
        status["inputIntegrityStatus"] = monitor.get("inputIntegrityStatus") if isinstance(monitor.get("inputIntegrityStatus"), dict) else dict(monitor)
        status["inputIntegrityPhasePolicy"] = "operator_or_pre_live_injected_counts_do_not_block; live_action_delta_blocks"
        if not bool(monitor.get("monitorPass")):
            status["status"] = "FAIL"
            status["blockReason"] = monitor.get("monitorBlockReason") or "arduino_monitor_failed"
    return status


def _start_live_input_session(options: Any | None, backend: Any) -> dict[str, Any]:
    status = _live_input_status(options, backend)
    if status.get("status") == "FAIL":
        raise RuntimeError(str(status.get("blockReason") or "live_input_blocked"))
    if _live_input_operation_requested(options) and _is_arduino_backend(backend):
        token = getattr(options, "arduino_session_token", None)
        if not token or str(token).strip().lower() == "auto":
            token = secrets.token_hex(8)
        try:
            arduino_status = backend.arm(str(token))
            status["arduino"] = arduino_status
            status["arduinoArmed"] = True
            status["arduinoSessionTokenHash"] = arduino_status.get("sessionTokenHash") if isinstance(arduino_status, dict) else None
            _write_input_backend_status(options, backend, armed=True, direct_backend_bypass_count=0)
            if bool(getattr(options, "arduino_require_monitor", False)):
                integrity_before = _read_input_integrity_status(options, backend, direct_backend_bypass_count=0, require_armed=True, fail_on_injected=False)
                status["monitor"] = integrity_before
                status["inputIntegrityStatusBefore"] = (
                    integrity_before.get("inputIntegrityStatus") if isinstance(integrity_before.get("inputIntegrityStatus"), dict) else dict(integrity_before)
                )
                status["inputIntegrityPhasePolicy"] = "pre_live_baseline_established; live_action_delta_blocks"
                if not bool(integrity_before.get("monitorPass")):
                    status["status"] = "FAIL"
                    status["blockReason"] = integrity_before.get("monitorBlockReason") or "input_integrity_failed"
                    try:
                        backend.stop_all()
                    except Exception:
                        pass
                    raise RuntimeError(str(status["blockReason"]))
        except Exception as error:  # noqa: BLE001
            status["status"] = "FAIL"
            status["blockReason"] = status.get("blockReason") or "arduino_arm_failed"
            status["arduinoArmError"] = f"{type(error).__name__}: {error}"
            try:
                backend.stop_all()
                status["stopAllSent"] = True
            except Exception as stop_error:  # noqa: BLE001
                status["stopAllError"] = f"{type(stop_error).__name__}: {stop_error}"
            raise RuntimeError(status["arduinoArmError"]) from error
    return status


def _ensure_live_input_session_for_action(options: Any | None, backend: Any, status: dict[str, Any] | None) -> None:
    if not isinstance(status, dict):
        return
    if not _live_input_operation_requested(options) or not _is_arduino_backend(backend):
        return
    if bool(getattr(backend, "armed", False)):
        return
    try:
        recovered = False
        ensure_armed = getattr(backend, "ensure_armed", None)
        if callable(ensure_armed):
            recovered = bool(ensure_armed())
        if not recovered:
            token = getattr(backend, "session_token", None) or getattr(options, "arduino_session_token", None)
            if token and str(token).strip().lower() != "auto":
                backend.arm(str(token))
                recovered = bool(getattr(backend, "armed", False))
        if not recovered:
            raise RuntimeError("Arduino live session is not armed and could not be re-armed")
        status["arduinoRearmedBeforeActionCount"] = int(status.get("arduinoRearmedBeforeActionCount") or 0) + 1
        if callable(getattr(backend, "status", None)):
            status["arduino"] = backend.status()
        _write_input_backend_status(options, backend, armed=True, direct_backend_bypass_count=0)
    except Exception as error:  # noqa: BLE001
        status["status"] = "FAIL"
        status["blockReason"] = "arduino_rearm_failed"
        status["arduinoRearmError"] = f"{type(error).__name__}: {error}"
        raise RuntimeError(status["arduinoRearmError"]) from error


def _finish_live_input_session(backend: Any, status: dict[str, Any] | None, *, options: Any | None = None) -> None:
    if not isinstance(status, dict) or not _is_arduino_backend(backend):
        return
    if status.get("arduinoArmed") or bool(getattr(backend, "armed", False)):
        try:
            arduino_status = backend.stop_all()
            status["arduino"] = arduino_status
            status["stopAllSent"] = True
        except Exception as error:  # noqa: BLE001
            status["stopAllError"] = f"{type(error).__name__}: {error}"
    try:
        arduino_status = backend.disarm()
        status["arduino"] = arduino_status
        status["arduinoDisarmed"] = True
        _write_input_backend_status(options, backend, armed=False, direct_backend_bypass_count=0)
    except Exception as error:  # noqa: BLE001
        status["arduinoDisarmError"] = f"{type(error).__name__}: {error}"
    try:
        firmware_status = backend.firmware_status()
        status["firmwareStatusAfter"] = firmware_status
        caps = status.get("arduino", {}).get("capabilities") if isinstance(status.get("arduino"), dict) else {}
        status["firmwareSafety"] = build_firmware_safety(
            {
                "status": "OK",
                "protocol": (status.get("arduino") or {}).get("protocol") if isinstance(status.get("arduino"), dict) else "arduino_hid.v1",
                "resetSafe": caps.get("resetSafe") if isinstance(caps, dict) else True,
                "stopAll": caps.get("stopAll") if isinstance(caps, dict) else True,
                "watchdog": caps.get("watchdog") if isinstance(caps, dict) else True,
                "watchdogMs": firmware_status.get("watchdogMs"),
                "armed": firmware_status.get("armed"),
                "keysDown": firmware_status.get("keysDown"),
                "mouseButtonsDown": firmware_status.get("mouseButtonsDown"),
            }
        )
    except Exception as error:  # noqa: BLE001
        status["firmwareStatusAfterError"] = f"{type(error).__name__}: {error}"
    try:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
            status["serialClosed"] = True
    except Exception as error:  # noqa: BLE001
        status["serialCloseError"] = f"{type(error).__name__}: {error}"


def _attach_live_input_status(
    result: ExecutionResult,
    status: dict[str, Any] | None,
    *,
    options: Any | None = None,
    backend: Any | None = None,
) -> None:
    if not isinstance(status, dict):
        return
    direct_count = _direct_backend_bypass_count(result)
    if backend is not None and _is_arduino_backend(backend) and bool(getattr(options, "arduino_require_monitor", False)):
        try:
            after_monitor = _read_input_integrity_status(options, backend, direct_backend_bypass_count=direct_count, require_armed=False, fail_on_injected=False)
            status["inputIntegrityStatusAfter"] = (
                after_monitor.get("inputIntegrityStatus") if isinstance(after_monitor.get("inputIntegrityStatus"), dict) else dict(after_monitor)
            )
            status["monitorAfter"] = after_monitor
            before = status.get("inputIntegrityStatusBefore") or status.get("inputIntegrityStatus")
            after = status.get("inputIntegrityStatusAfter")
            if isinstance(before, dict) and isinstance(after, dict):
                status["inputIntegrityDelta"] = input_integrity_delta(before, after)
        except Exception as error:  # noqa: BLE001
            status["inputIntegrityReadAfterError"] = f"{type(error).__name__}: {error}"
    phase_report = _input_integrity_phase_report(status)
    status["inputIntegrityPhaseReport"] = phase_report
    if isinstance(result.action_trace, dict):
        result.action_trace["liveInput"] = dict(status)
        before_status = status.get("inputIntegrityStatusBefore") or status.get("inputIntegrityStatus")
        after_status = status.get("inputIntegrityStatusAfter")
        delta = status.get("inputIntegrityDelta") if isinstance(status.get("inputIntegrityDelta"), dict) else {}
        if isinstance(before_status, dict):
            result.action_trace["inputIntegrityStatusBefore"] = dict(before_status)
        if isinstance(after_status, dict):
            result.action_trace["inputIntegrityStatusAfter"] = dict(after_status)
        if isinstance(delta, dict):
            result.action_trace.update(
                {
                    "mouseInjectedCountDelta": delta.get("mouseInjectedCountDelta", 0),
                    "keyboardInjectedCountDelta": delta.get("keyboardInjectedCountDelta", 0),
                    "lowerIlInjectedCountDelta": delta.get("lowerIlInjectedCountDelta", 0),
                    "directBackendBypassCountDelta": delta.get("directBackendBypassCountDelta", 0),
                }
            )
        result.action_trace["inputIntegrityPhaseReport"] = phase_report
        monitor = status.get("monitorAfter") if isinstance(status.get("monitorAfter"), dict) else status.get("monitor")
        if isinstance(monitor, dict):
            result.action_trace["monitorPass"] = monitor.get("monitorPass")
            result.action_trace["arduinoRawInputSeen"] = monitor.get("arduinoRawInputSeen")
            result.action_trace["arduinoVidPidMatched"] = monitor.get("expectedVidPidMatched")
        result.action_trace["liveInputBackend"] = status.get("liveInputBackend")
        result.action_trace["arduinoArmed"] = bool(status.get("arduinoArmed"))
        human = result.action_trace.setdefault("humanInput", {})
        if isinstance(human, dict):
            human["liveInputBackend"] = status.get("liveInputBackend")
            human["liveInputBackendRequired"] = status.get("liveInputBackendRequired")
            human["softwareInputAllowed"] = status.get("softwareInputAllowed")
    if result.readiness is not None:
        action_readiness = result.readiness.setdefault("actionReadiness", {})
        if isinstance(action_readiness, dict):
            action_readiness["liveInput"] = dict(status)
    delta = status.get("inputIntegrityDelta") if isinstance(status.get("inputIntegrityDelta"), dict) else {}
    injected_delta = int(delta.get("mouseInjectedCountDelta") or 0) + int(delta.get("keyboardInjectedCountDelta") or 0)
    lower_delta = int(delta.get("lowerIlInjectedCountDelta") or 0)
    if _live_input_operation_requested(options) and _input_integrity_fail_on_injected(options) and (injected_delta > 0 or lower_delta > 0):
        result.status = "FAIL"
        result.warnings.append("injected_input_detected")
        if "live_input.injected_input" not in result.missing_capabilities:
            result.missing_capabilities.append("live_input.injected_input")
        if isinstance(result.action_trace, dict):
            result.action_trace["finalClassification"] = "injected_input_detected"


def _direct_backend_bypass_count(result: ExecutionResult) -> int:
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    human = trace.get("humanInput") if isinstance(trace.get("humanInput"), dict) else {}
    try:
        return int(human.get("directBackendBypassCount") or 0)
    except (TypeError, ValueError):
        return 0


def _enforce_no_direct_backend_bypass(result: ExecutionResult, options: Any | None) -> None:
    if not _live_input_operation_requested(options):
        return
    count = _direct_backend_bypass_count(result)
    if count <= 0:
        return
    result.status = "FAIL"
    result.warnings.append(f"direct_backend_bypass_blocked: directBackendBypassCount={count}")
    if "live_input.direct_backend_bypass" not in result.missing_capabilities:
        result.missing_capabilities.append("live_input.direct_backend_bypass")
    if isinstance(result.action_trace, dict):
        result.action_trace["finalClassification"] = "direct_backend_bypass_blocked"
        result.action_trace.setdefault("liveInput", {})["directBackendBypassBlocked"] = True


def _live_input_blocked_result(options: Any, backend: Any, status: dict[str, Any], *, proposed_action: str = "none") -> ExecutionResult:
    reason = str(status.get("blockReason") or "live_input_blocked")
    action_readiness = {
        "status": "FAIL",
        "executionAllowed": False,
        "intent": None,
        "blockers": [reason],
        "warnings": [],
        "checks": {"liveInputBackend": dict(status)},
        "missingCapabilities": ["live_input.arduino"] if reason != "software_input_blocked" else [],
    }
    result = ExecutionResult(
        status="FAIL",
        proposed_action=proposed_action,
        dry_run=not bool(getattr(options, "execute", False)),
        backend_name=_backend_name(backend),
        movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
        warnings=[reason],
        missing_capabilities=list(action_readiness["missingCapabilities"]),
        readiness={
            "schema": "live_readiness.v2",
            "status": "FAIL",
            "ready": False,
            "currentIntent": None,
            "actionReadiness": action_readiness,
        },
        verification_status="FAIL",
        action_trace={
            "schema": "action_trace.v2",
            "liveInput": dict(status),
            "humanInput": {
                "liveInputBackend": status.get("liveInputBackend"),
                "liveInputBackendRequired": status.get("liveInputBackendRequired"),
                "softwareInputAllowed": status.get("softwareInputAllowed"),
                "directBackendBypassCount": 0,
                "backendCommandCount": 0,
                "backendBlockedCommandCount": 0,
            },
        },
    )
    return result


def _recovery_verified_loaded_scene(recovery: dict[str, Any] | None) -> bool:
    if not isinstance(recovery, dict):
        return False
    if recovery.get("loadedSceneVerified") is True or recovery.get("finalLoadedSceneVerified") is True:
        return True
    final_state = recovery.get("finalState") if isinstance(recovery.get("finalState"), dict) else {}
    if final_state.get("loadedSceneVerified") is True:
        return True
    proof = final_state.get("loadedSceneProof") if isinstance(final_state.get("loadedSceneProof"), dict) else {}
    return proof.get("loadedSceneVerified") is True


def _input_controller_from_options(
    backend: Any,
    options: Any | None,
    *,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> HumanInputController:
    return HumanInputController(
        backend,
        profile=_input_profile_from_options(options),
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
        seed=getattr(options, "seed", None),
        live_input_backend_required=_live_input_required_for_backend(options, backend),
        software_input_allowed=_software_input_allowed(options),
    )


def _cooldown_ms(options: Any) -> int:
    return max(0, int(getattr(options, "cooldown_ms", 0) or 0))


def _action_timeout_seconds(options: Any) -> float:
    timeout_ms = getattr(options, "result_timeout_ms", None)
    if timeout_ms is None:
        timeout_ms = getattr(options, "action_timeout_ms", 3000)
    return max(0.001, float(timeout_ms or 3000) / 1000.0)


def _poll_interval_seconds(options: Any) -> float:
    return max(0.01, float(getattr(options, "poll_interval_ms", 250) or 250) / 1000.0)


def _wait_for_ready_seconds(options: Any) -> float:
    return max(0.0, float(getattr(options, "wait_for_ready", 0.0) or 0.0))


def _optional_positive_int(options: Any, name: str) -> int | None:
    value = getattr(options, name, None)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "full", "complete"}:
            return True
        if text in {"false", "no", "0", "not_full", "incomplete"}:
            return False
    return None


def _hover_menu_sample(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    return client_tick_core.latest_hover_menu_sample(snapshot)


def _last_menu_option_clicked_sample(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    return client_tick_core.latest_menu_option_clicked_sample(snapshot)


def _proposal_target_id(proposal: ActionProposal) -> int | None:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    for key in ("objectId", "id", "rawId", "identifier"):
        value = _int_or_none(explanation.get(key))
        if value is not None:
            return value
    return None


def _canvas_click_point_for_hover(proposal: ActionProposal) -> dict[str, int] | None:
    if proposal.click_point_space == "canvas" and isinstance(proposal.suggested_click_point, dict):
        return {
            "x": int(round(float(proposal.suggested_click_point["x"]))),
            "y": int(round(float(proposal.suggested_click_point["y"]))),
        }
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    point = explanation.get("canvasAimPoint") or explanation.get("aimPoint")
    if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
        return {"x": int(round(float(point["x"]))), "y": int(round(float(point["y"])))}
    return None


def hover_menu_matches_target(
    sample: dict[str, Any] | None,
    proposal: ActionProposal,
    canvas_point: dict[str, Any],
    *,
    tolerance_px: int = 3,
    min_wall_time_millis: int | None = None,
) -> HoverMenuMatchResult:
    intent = client_tick_core.action_intent_from_proposal(
        proposal,
        tolerance_px=tolerance_px,
        freshness_millis=120,
    )
    result = client_tick_core.hover_sample_matches_intent(
        sample,
        intent,
        canvas_point,
        tolerance_px=tolerance_px,
        min_wall_time_millis=min_wall_time_millis,
    )
    if result.reason == "top_option_rejected":
        reason = "top_option_not_chop" if proposal.proposed_action == "select_resource_target" else "top_option_not_expected"
        return HoverMenuMatchResult(result.confirmed, reason, result.sample, result.details)
    return result


def _same_menu_option_sample(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    return client_tick_core.same_menu_option_sample(left, right)


def classify_last_menu_option_clicked(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    proposal: ActionProposal,
) -> str:
    intent = client_tick_core.action_intent_from_proposal(proposal)
    classification = client_tick_core.classify_clicked_menu(before, after, intent)
    if classification == "clicked_expected_action" and proposal.proposed_action == "select_resource_target" and intent.activity == "woodcutting":
        return "clicked_chop_tree"
    return classification


def _clicked_menu_matches_expected(classification: str | None) -> bool:
    return str(classification or "") in {"clicked_chop_tree", "clicked_expected_action"}


def _clicked_menu_mismatch_is_known(classification: str | None) -> bool:
    value = str(classification or "")
    return value.startswith("clicked_") and value not in {"clicked_expected_action", "clicked_chop_tree", "unknown_click_result"}


def _menu_text_key(value: Any) -> str:
    text = client_tick_core._clean_menu_text(value) if hasattr(client_tick_core, "_clean_menu_text") else str(value or "")
    return " ".join(text.replace("-", " ").lower().split())


def _route_transition_direct_expected_options(proposal: ActionProposal) -> list[str]:
    if proposal.proposed_action != "interact_service_route_object":
        return []
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    opener_keys = {_menu_text_key(value) for value in explanation.get("dialogueOpenerOptions") or []}
    direct: list[str] = []
    for value in explanation.get("expectedOptions") or []:
        key = _menu_text_key(value)
        if key and key not in opener_keys:
            direct.append(str(value))
    return direct


def _entry_matches_route_transition_direct_option(entry: dict[str, Any], proposal: ActionProposal) -> bool:
    option_key = _menu_text_key(entry.get("option"))
    if not option_key:
        return False
    expected_options = _route_transition_direct_expected_options(proposal)
    if not expected_options:
        return False
    if not any(option_key == _menu_text_key(expected) or _menu_text_key(expected) in option_key for expected in expected_options):
        return False
    expected_id = _proposal_target_id(proposal)
    entry_id = _int_or_none(entry.get("identifier"))
    if expected_id is not None and entry_id is not None and expected_id != entry_id:
        return False
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    expected_targets = list(explanation.get("expectedTargets") or [])
    if proposal.target_name:
        expected_targets.append(proposal.target_name)
    target_key = _menu_text_key(entry.get("target"))
    if expected_targets and not any(_menu_text_key(expected) in target_key for expected in expected_targets if _menu_text_key(expected)):
        return False
    return True


def _entry_matches_route_transition_dialogue_opener(entry: dict[str, Any], proposal: ActionProposal) -> bool:
    option_key = _menu_text_key(entry.get("option"))
    if not option_key:
        return False
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    opener_options = [_menu_text_key(value) for value in explanation.get("dialogueOpenerOptions") or []]
    opener_matches = False
    for opener in opener_options:
        if not opener:
            continue
        if option_key == opener:
            opener_matches = True
            break
        if opener == "climb":
            continue
        if opener in option_key:
            opener_matches = True
            break
    if not opener_matches:
        return False
    expected_id = _proposal_target_id(proposal)
    entry_id = _int_or_none(entry.get("identifier"))
    if expected_id is not None and entry_id is not None and expected_id != entry_id:
        return False
    expected_targets = list(explanation.get("expectedTargets") or [])
    if proposal.target_name:
        expected_targets.append(proposal.target_name)
    target_key = _menu_text_key(entry.get("target"))
    if expected_targets and not any(_menu_text_key(expected) in target_key for expected in expected_targets if _menu_text_key(expected)):
        return False
    return True


def _route_transition_direct_menu_entry(
    proposal: ActionProposal,
    confirmation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if proposal.proposed_action != "interact_service_route_object":
        return None
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    sample = confirmation.get("sample") if isinstance(confirmation.get("sample"), dict) else _confirmation_hover_sample(confirmation)
    if not isinstance(sample, dict):
        return None
    selected = client_tick_core.get_left_click_entry(sample)
    if isinstance(selected, dict) and _entry_matches_route_transition_direct_option(selected, proposal):
        return None
    expected_options = _route_transition_direct_expected_options(proposal)
    synthetic_direct_entry = None
    if expected_options and _route_transition_left_click_is_dialogue_opener(proposal, {"sample": sample}):
        synthetic_direct_entry = {
            "option": expected_options[0],
            "target": proposal.target_name or "",
            "identifier": _proposal_target_id(proposal),
            "syntheticEntry": True,
            "reason": "left_click_is_generic_dialogue_opener",
        }
    entries = _menu_entries_display_order(sample)
    if not entries:
        return synthetic_direct_entry
    for entry in entries:
        if _entry_matches_route_transition_direct_option(entry, proposal):
            return entry
    return synthetic_direct_entry


def _entry_matches_navigation_walk_here(entry: dict[str, Any], proposal: ActionProposal) -> bool:
    if not (_is_navigation_path_proposal(proposal) or proposal.proposed_action in NAVIGATION_ACTIONS):
        return False
    return client_tick_core.is_walk_here_entry(entry)


def _navigation_walk_here_menu_entry(
    proposal: ActionProposal,
    confirmation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not (_is_navigation_path_proposal(proposal) or proposal.proposed_action in NAVIGATION_ACTIONS):
        return None
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    sample = confirmation.get("sample") if isinstance(confirmation.get("sample"), dict) else _confirmation_hover_sample(confirmation)
    if not isinstance(sample, dict):
        return None
    selected = client_tick_core.get_left_click_entry(sample)
    if isinstance(selected, dict) and client_tick_core.is_walk_here_entry(selected):
        return None
    for entry in _menu_entries_display_order(sample):
        if _entry_matches_navigation_walk_here(entry, proposal):
            return entry
    return None


def _route_transition_left_click_is_dialogue_opener(
    proposal: ActionProposal,
    confirmation: dict[str, Any] | None,
) -> bool:
    if proposal.proposed_action != "interact_service_route_object":
        return False
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    sample = confirmation.get("sample") if isinstance(confirmation.get("sample"), dict) else _confirmation_hover_sample(confirmation)
    if not isinstance(sample, dict):
        return False
    selected = client_tick_core.get_left_click_entry(sample)
    if isinstance(selected, dict) and _entry_matches_route_transition_dialogue_opener(selected, proposal):
        return True
    raw_top = {
        "option": sample.get("topOption"),
        "target": sample.get("topTarget"),
        "type": sample.get("topType"),
        "identifier": sample.get("topIdentifier"),
        "param0": sample.get("topParam0"),
        "param1": sample.get("topParam1"),
    }
    return _entry_matches_route_transition_dialogue_opener(raw_top, proposal)


def _menu_entries_display_order(sample: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(sample, dict):
        return []
    entries = sample.get("entries")
    if not isinstance(entries, list):
        return []
    source = str(sample.get("sourceEvent") or sample.get("sampleSource") or "")
    order = str(sample.get("entriesDisplayOrder") or sample.get("entryOrder") or "").strip().lower()
    reverse_raw_order = False
    if order in {"top_to_bottom", "display_top_to_bottom", "display_order"}:
        reverse_raw_order = False
    elif order in {"client_order", "raw_client_order", "bottom_to_top"}:
        reverse_raw_order = True
    elif source == "MenuOpened":
        # TelemetryPlugin versions before entriesDisplayOrder emitted MenuOpened
        # entries in RuneLite client-array order, while PostMenuSort emits top-first.
        reverse_raw_order = True
    indexed = list(enumerate(entries))
    if reverse_raw_order:
        indexed = list(reversed(indexed))
    display_entries: list[dict[str, Any]] = []
    for display_index, (source_index, raw_entry) in enumerate(indexed):
        if not isinstance(raw_entry, dict):
            continue
        entry = dict(raw_entry)
        entry["sourceEntryIndex"] = source_index
        entry["displayEntryIndex"] = display_index
        entry["entryIndex"] = display_index
        entry["entriesDisplayOrder"] = "top_to_bottom"
        entry["entriesDisplayOrderSource"] = "normalized_menu_opened" if reverse_raw_order else (order or source or "as_emitted")
        display_entries.append(entry)
    return display_entries


def _menu_row_canvas_geometry(sample: dict[str, Any], row_index: int) -> dict[str, Any] | None:
    bounds = sample.get("menuBounds") if isinstance(sample, dict) else None
    entries = _menu_entries_display_order(sample)
    if not isinstance(bounds, dict) or not isinstance(entries, list):
        return None
    displayed_count = _int_or_none(sample.get("entryCount"))
    if displayed_count is None:
        displayed_count = _int_or_none(sample.get("menuEntryCount"))
    count = max(1, len(entries), int(displayed_count or 0))
    x = _int_or_none(bounds.get("x"))
    y = _int_or_none(bounds.get("y"))
    width = _int_or_none(bounds.get("width"))
    height = _int_or_none(bounds.get("height"))
    if x is None or y is None or width is None or height is None or width <= 0 or height <= 0:
        return None
    if row_index < 0 or row_index >= count:
        return None
    if height > 22:
        natural_header_height = height - (15.0 * count)
        header_height = min(24.0, max(16.0, natural_header_height))
        row_height = (height - header_height) / count
        options_top = y + header_height
    else:
        row_height = height / count
        options_top = y
    row_y = options_top + row_index * row_height + row_height / 2.0
    row_x = x + width / 2.0
    if row_x < x or row_x > x + width or row_y < y or row_y > y + height:
        return None
    return {
        "point": {"x": int(round(row_x)), "y": int(round(row_y))},
        "rowIndex": row_index,
        "displayEntryCount": count,
        "entryCount": displayed_count,
        "visibleEntryCount": len(entries),
        "menuBounds": dict(bounds),
        "headerHeight": header_height if height > 22 else 0.0,
        "rowHeight": row_height,
        "optionsTop": options_top,
        "entriesDisplayOrder": "top_to_bottom",
    }


def _menu_row_canvas_point(sample: dict[str, Any], row_index: int) -> dict[str, int] | None:
    geometry = _menu_row_canvas_geometry(sample, row_index)
    point = geometry.get("point") if isinstance(geometry, dict) else None
    return dict(point) if isinstance(point, dict) else None


def _screen_point_from_canvas(backend: Any, point: dict[str, int]) -> dict[str, int] | None:
    converter = getattr(backend, "canvas_to_screen_point", None)
    if not callable(converter):
        return None
    try:
        converted = converter(dict(point))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(converted, dict) or converted.get("x") is None or converted.get("y") is None:
        return None
    return {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))}


def _screen_point_from_canvas_for_proposal(
    proposal: ActionProposal,
    backend: Any,
    point: dict[str, int] | None,
) -> dict[str, int] | None:
    if point is None:
        return None
    resolution = resolve_screen_click_point(
        dict(point),
        click_point_space="canvas",
        input_geometry=proposal.input_geometry,
        source_canvas_size=(proposal.input_geometry or {}).get("sourceCanvasSize") if isinstance(proposal.input_geometry, dict) else None,
    )
    screen_point = resolution.get("screenClickPoint") if isinstance(resolution, dict) else None
    if isinstance(screen_point, dict) and screen_point.get("x") is not None and screen_point.get("y") is not None:
        return {"x": int(round(float(screen_point["x"]))), "y": int(round(float(screen_point["y"])))}
    return _screen_point_from_canvas(backend, point)


def _is_observed_menu_open_sample(sample: dict[str, Any], *, minimum_wall_time_millis: int | None = None) -> bool:
    if not isinstance(sample, dict):
        return False
    bounds = sample.get("menuBounds")
    if not isinstance(bounds, dict):
        return False
    width = _int_or_none(bounds.get("width"))
    height = _int_or_none(bounds.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return False
    entries = sample.get("entries")
    if not isinstance(entries, list) or not entries:
        return False
    if minimum_wall_time_millis is not None:
        sample_wall_time = _int_or_none(sample.get("wallTimeMillis"))
        if sample_wall_time is None or sample_wall_time < int(minimum_wall_time_millis):
            return False
    if sample.get("menuOpen") is True:
        return True
    source = str(sample.get("sourceEvent") or sample.get("sampleSource") or "")
    return source == "MenuOpened"


def _menu_open_poll_client_tick_tail(hover_options: HoverConfirmationOptions) -> int:
    return max(5, max(0, int(hover_options.client_tick_tail or 0)))


def _observed_menu_open_sample_from_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    minimum_wall_time_millis: int | None = None,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    latest = _hover_menu_sample(snapshot)
    if isinstance(latest, dict):
        candidates.append(latest)
    candidates.extend(client_tick_core.post_menu_sort_tail_samples(snapshot, include_latest=False))

    best: dict[str, Any] | None = None
    best_key: tuple[int, int, int] | None = None
    for index, sample in enumerate(candidates):
        if not _is_observed_menu_open_sample(sample, minimum_wall_time_millis=minimum_wall_time_millis):
            continue
        wall_time = _int_or_none(sample.get("wallTimeMillis"))
        client_tick = _int_or_none(sample.get("clientTick"))
        key = (wall_time if wall_time is not None else -1, client_tick if client_tick is not None else -1, index)
        if best_key is None or key >= best_key:
            best = sample
            best_key = key
    return best


def _poll_menu_open_sample(
    hover_options: HoverConfirmationOptions,
    *,
    minimum_wall_time_millis: int | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> dict[str, Any] | None:
    started = float(monotonic_func())
    timeout_seconds = max(0.25, hover_options.timeout_ms / 1000.0)
    poll_seconds = max(0.001, hover_options.poll_ms / 1000.0)
    latest: dict[str, Any] | None = None
    effective_client_tick_tail = _menu_open_poll_client_tick_tail(hover_options)
    while True:
        try:
            snapshot = snapshot_fetch_func(
                hover_options.snapshot_url,
                timeout=hover_options.request_timeout_seconds,
                client_tick_tail=effective_client_tick_tail,
                menu_entry_limit=max(8, hover_options.menu_entry_limit),
            )
            latest = _hover_menu_sample(snapshot)
            open_sample = _observed_menu_open_sample_from_snapshot(
                snapshot,
                minimum_wall_time_millis=minimum_wall_time_millis,
            )
            if isinstance(open_sample, dict):
                return open_sample
        except Exception:  # noqa: BLE001
            pass
        if float(monotonic_func()) - started >= timeout_seconds:
            return latest
        sleep_func(poll_seconds)


def _execute_route_transition_direct_menu_selection(
    proposal: ActionProposal,
    *,
    direct_entry: dict[str, Any],
    screen_point: dict[str, int],
    plan: Any,
    result: ExecutionResult,
    hover_options: HoverConfirmationOptions,
    input_controller: HumanInputController,
    backend: Any,
    before_click: dict[str, Any] | None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
    entry_matcher: Callable[[dict[str, Any], ActionProposal], bool] | None = None,
    event_source: str = "route_transition_direct_expected_option",
    open_reason: str = "route_transition_direct_expected_option",
    row_label: str = "route transition menu row",
) -> bool:
    matches_entry = entry_matcher or _entry_matches_route_transition_direct_option
    event: dict[str, Any] = {
        "schema": "right_click_menu_select.v1",
        "expectedEntry": dict(direct_entry),
        "source": event_source,
        "status": "started",
        "clientTickTailRequested": _menu_open_poll_client_tick_tail(hover_options),
        "menuEntryLimitRequested": max(8, hover_options.menu_entry_limit),
    }
    result.commands.append(
        {
            "type": "right_click_menu_open",
            "clickPoint": plan.click_point.to_dict(),
            "button": "right",
            "reason": open_reason,
        }
    )
    right_click_started_wall_ms = int(wall_time_millis_func())
    _click_confirmed_current_position(
        input_controller,
        button="right",
        hold_ms=max(50, int(hover_options.click_hold_ms or 0)),
        context=_human_context(proposal, "right_click_menu_open"),
    )
    menu_sample = _poll_menu_open_sample(
        hover_options,
        minimum_wall_time_millis=right_click_started_wall_ms,
        snapshot_fetch_func=snapshot_fetch_func,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    )
    event["menuOpenSample"] = menu_sample
    if not isinstance(menu_sample, dict) or not _is_observed_menu_open_sample(
        menu_sample,
        minimum_wall_time_millis=right_click_started_wall_ms,
    ):
        event["status"] = "FAIL"
        event["reason"] = "menu_open_not_observed"
        result.warnings.append("right-click menu selection failed: menu did not open")
        result.hover_confirmation["rightClickMenuSelection"] = event
        return False
    selected_entry: dict[str, Any] | None = None
    for entry in _menu_entries_display_order(menu_sample):
        if matches_entry(entry, proposal):
            selected_entry = entry
            break
    if selected_entry is None:
        event["status"] = "FAIL"
        event["reason"] = "expected_menu_row_missing"
        result.warnings.append("right-click menu selection failed: expected route option not present")
        result.hover_confirmation["rightClickMenuSelection"] = event
        return False
    row_index = int(selected_entry.get("entryIndex") or 0)
    row_geometry = _menu_row_canvas_geometry(menu_sample, row_index)
    canvas_row = dict(row_geometry.get("point")) if isinstance(row_geometry, dict) and isinstance(row_geometry.get("point"), dict) else None
    screen_row = _screen_point_from_canvas_for_proposal(proposal, backend, canvas_row)
    event["selectedEntry"] = dict(selected_entry)
    event["rowCanvasPoint"] = canvas_row
    event["rowCanvasGeometry"] = row_geometry
    event["rowScreenPoint"] = screen_row
    if screen_row is None:
        event["status"] = "FAIL"
        event["reason"] = "menu_row_geometry_unavailable"
        result.warnings.append("right-click menu selection failed: menu row geometry unavailable")
        result.hover_confirmation["rightClickMenuSelection"] = event
        return False
    start = MousePoint(*_backend_position(backend))
    row_plan = input_controller.plan_mouse_movement(
        start,
        MouseTarget(
            int(screen_row["x"]),
            int(screen_row["y"]),
            radius_px=6,
            width_px=max(24, _int_or_none((menu_sample.get("menuBounds") or {}).get("width")) or 24),
            height_px=14,
            label=row_label,
            source="right_click_menu",
        ),
        MouseMovementProfile(name="linear_debug", min_duration_ms=80, max_duration_ms=260, waypoint_count=12),
        context=_human_context(proposal, "right_click_menu_row"),
    )
    result.commands.append(
        {
            "type": "right_click_menu_select",
            "selectedEntry": dict(selected_entry),
            "rowCanvasPoint": canvas_row,
            "rowScreenPoint": screen_row,
            "pointCount": len(row_plan.points),
        }
    )
    input_controller.move_mouse(row_plan, context=_human_context(proposal, "right_click_menu_row"))
    _click_confirmed_current_position(
        input_controller,
        button="left",
        hold_ms=hover_options.click_hold_ms,
        context=_human_context(proposal, "right_click_menu_select"),
    )
    result.executed = True
    event["status"] = "PASS"
    event["selectedAtWallMillis"] = int(wall_time_millis_func())
    after_click = _poll_last_menu_option_clicked(
        hover_options,
        before_click,
        snapshot_fetch_func=snapshot_fetch_func,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
    )
    result.hover_confirmation["lastMenuOptionClickedAfter"] = after_click
    click_classification = classify_last_menu_option_clicked(before_click, after_click, proposal)
    result.hover_confirmation["clickClassification"] = click_classification
    event["actualClickedMenu"] = after_click
    event["clickClassification"] = click_classification
    direct_option_clicked = matches_entry(after_click or {}, proposal)
    event["directOptionClicked"] = bool(direct_option_clicked)
    if not direct_option_clicked:
        event["status"] = "FAIL"
        event["reason"] = "clicked_direct_menu_mismatch"
        result.warnings.append("right-click menu selection clicked a route option, but not the expected direct route option")
        result.hover_confirmation["rightClickMenuSelection"] = event
        if isinstance(result.action_trace, dict):
            result.action_trace["rightClickMenuSelection"] = event
        return False
    result.hover_confirmation["rightClickMenuSelection"] = event
    if isinstance(result.action_trace, dict):
        result.action_trace["rightClickMenuSelection"] = event
        client_tick = result.action_trace.setdefault("clientTick", {})
        client_tick["lastMenuOptionClickedBefore"] = before_click
        client_tick["lastMenuOptionClickedAfter"] = after_click
        client_tick["clickedMenuClassification"] = click_classification
        result.action_trace["clickTimestampWallMillis"] = event["selectedAtWallMillis"]
    return _clicked_menu_matches_expected(click_classification)


def _menu_mismatch_payload(
    proposal: ActionProposal,
    *,
    hover_before: dict[str, Any] | None,
    actual_clicked: dict[str, Any] | None,
    classification: str,
) -> dict[str, Any]:
    intent = _action_intent_type(proposal)
    possible_causes = ["hover_flip", "stale_hot_sample", "target_occlusion", "focus_issue"]
    return {
        "mismatchReason": f"clicked_menu_did_not_match_{intent}",
        "expectedIntent": intent,
        "classification": classification,
        "hoverBeforeClick": hover_before,
        "actualClickedMenu": actual_clicked,
        "possibleCauses": possible_causes,
    }


def _status_brain(status: dict[str, Any]) -> dict[str, Any]:
    return _dict(status.get("brain")) or status


def _status_context(status: dict[str, Any], context_name: str) -> dict[str, Any]:
    brain = _status_brain(status)
    value = brain.get(context_name)
    if isinstance(value, dict):
        return value
    value = status.get(context_name)
    return value if isinstance(value, dict) else {}


def _point_xy(value: Any) -> dict[str, int] | None:
    point = value if isinstance(value, dict) else {}
    x = _int_or_none(point.get("x"))
    y = _int_or_none(point.get("y"))
    if x is None:
        x = _int_or_none(point.get("canvasX"))
    if y is None:
        y = _int_or_none(point.get("canvasY"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _context_action_proposal_payload(context_response: dict[str, Any] | None) -> dict[str, Any]:
    response = context_response if isinstance(context_response, dict) else {}
    debug = _dict(response.get("knowledgeCurrentDebugContext"))
    data = _dict(debug.get("data"))
    for value in (
        data.get("actionProposal"),
        response.get("actionProposal"),
        _dict(response.get("currentDebugContext")).get("actionProposal"),
    ):
        if isinstance(value, dict) and value:
            return value
    return {}


def _proposal_from_context_payload(payload: dict[str, Any]) -> ActionProposal | None:
    if not isinstance(payload, dict) or not payload:
        return None
    click_point = _point_xy(payload.get("suggestedClickPoint"))
    resolved_point = _point_xy(payload.get("resolvedScreenClickPoint"))
    proposal = ActionProposal(
        proposed_action=str(payload.get("proposedAction") or payload.get("action") or "none"),
        target_kind=str(payload.get("targetKind") or "none"),
        target_name=str(payload.get("targetName")) if payload.get("targetName") is not None else None,
        target_tile=_dict(payload.get("targetTile")) or None,
        suggested_click_point=click_point,
        click_point_space=str(payload.get("clickPointSpace") or "screen"),
        resolved_screen_click_point=resolved_point,
        click_point_resolution=_dict(payload.get("clickPointResolution")) or None,
        input_geometry=_dict(payload.get("inputGeometry")) or None,
        suggested_world_tile=_dict(payload.get("suggestedWorldTile")) or None,
        key_action=_dict(payload.get("keyAction")) or None,
        target_explanation=_dict(payload.get("targetExplanation")) or None,
        reason=str(payload.get("reason") or "context_action_proposal"),
        confidence=float(payload.get("confidence")) if isinstance(payload.get("confidence"), (int, float)) else 0.0,
        required_context=_list_of_strings(payload.get("requiredContext")),
        missing_capabilities=_list_of_strings(payload.get("missingCapabilities")),
        warnings=_list_of_strings(payload.get("warnings")),
        status=str(payload.get("status") or "PASS"),
        source_tick=_int_or_none(payload.get("sourceTick")),
        action_target_source=str(payload.get("actionTargetSource")) if payload.get("actionTargetSource") is not None else None,
        actionability=str(payload.get("actionability")) if payload.get("actionability") is not None else None,
    )
    if isinstance(payload.get("selectedTarget"), dict):
        proposal.target_explanation = proposal.target_explanation or {}
        proposal.target_explanation.setdefault("selectedTarget", dict(payload.get("selectedTarget")))
    return proposal


def _inventory_context_from_context_response(context_response: dict[str, Any]) -> dict[str, Any]:
    inventory = _dict(context_response.get("inventory"))
    if not inventory:
        return {}
    context: dict[str, Any] = {}
    for key in ("freeSlots", "filledSlots", "inventoryFull", "known"):
        if key in inventory:
            context[key] = inventory.get(key)
    progress: dict[str, Any] = {}
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount", "displayedGoalProgress", "goalProgress"):
        value = _int_or_none(inventory.get(key))
        if value is not None:
            progress[key] = value
    item_count = None
    for key in ("normalLogs", "logs", "resourceCount", "logCount"):
        item_count = _int_or_none(inventory.get(key))
        if item_count is not None:
            break
    item_counts = inventory.get("itemCounts") if isinstance(inventory.get("itemCounts"), dict) else {}
    if item_count is None:
        for key in ("1511", 1511, "Logs", "logs"):
            item_count = _int_or_none(item_counts.get(key))
            if item_count is not None:
                break
    items = inventory.get("items") if isinstance(inventory.get("items"), list) else []
    if item_count is None and items:
        total = 0
        found = False
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = _int_or_none(item.get("itemId") if item.get("itemId") is not None else item.get("id"))
            name = str(item.get("name") or item.get("itemName") or "").lower()
            if item_id == 1511 or name == "logs":
                total += max(1, _int_or_none(item.get("quantity")) or 1)
                found = True
        if found:
            item_count = total
    if item_count is not None:
        progress.setdefault("currentHeldCount", item_count)
        progress.setdefault("currentHeldResourceCount", item_count)
        progress.setdefault("displayedGoalProgress", item_count)
    if progress:
        context["progress"] = progress
    return context


def _target_from_context_action_proposal(proposal_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal_payload, dict) or not proposal_payload:
        return {}
    explanation = _dict(proposal_payload.get("targetExplanation"))
    target = dict(explanation) if explanation else {}
    if not target:
        selected = _dict(proposal_payload.get("selectedTarget"))
        if selected:
            target.update(selected)
    name = proposal_payload.get("targetName") or target.get("targetName") or target.get("name")
    if name is not None:
        target.setdefault("targetName", name)
        target.setdefault("name", name)
    if proposal_payload.get("targetKind") is not None:
        target.setdefault("targetType", proposal_payload.get("targetKind"))
    if proposal_payload.get("sourceTick") is not None:
        target.setdefault("sourceTick", proposal_payload.get("sourceTick"))
        target.setdefault("tick", proposal_payload.get("sourceTick"))
    for source_key in ("suggestedClickPoint", "aimPoint", "canvasAimPoint", "rawAimPoint"):
        value = proposal_payload.get(source_key) if source_key == "suggestedClickPoint" else target.get(source_key)
        point = _point_xy(value)
        if point:
            target.setdefault("aimPoint", {"x": point["x"], "y": point["y"]})
            break
    safe = _dict(target.get("safeAimPoint"))
    if safe:
        target["safeAimPoint"] = safe
        if safe.get("status") == "PASS":
            target.setdefault("geometryAvailable", True)
            target.setdefault("onScreen", True)
    if proposal_payload.get("actionTargetSource") is not None:
        target.setdefault("actionTargetSource", proposal_payload.get("actionTargetSource"))
    if proposal_payload.get("actionability") is not None:
        target.setdefault("actionability", proposal_payload.get("actionability"))
    return target


def _merge_context_response_into_status(status: dict[str, Any], context_response: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(status if isinstance(status, dict) else {})
    context = context_response if isinstance(context_response, dict) else {}
    enriched["contextServiceResponse"] = context
    debug = _dict(context.get("knowledgeCurrentDebugContext"))
    debug_data = _dict(debug.get("data"))
    live_status = _dict(debug_data.get("liveStatus") or context.get("liveStatus"))
    if live_status:
        for key in ("sessionPath", "latestTick", "latestExportSeq", "inputSourceActive", "profile", "activeProfile"):
            if enriched.get(key) is None and live_status.get(key) is not None:
                enriched[key] = live_status.get(key)
    for key in ("latestTick", "latestExportSeq", "sourceAgeMs"):
        if enriched.get(key) is None and context.get(key) is not None:
            enriched[key] = context.get(key)
    brain = dict(enriched.get("brain")) if isinstance(enriched.get("brain"), dict) else {}
    inventory_context = _inventory_context_from_context_response(context)
    if inventory_context:
        brain["inventoryContext"] = inventory_context
        enriched["inventoryContext"] = inventory_context
    if isinstance(context.get("bankState"), dict):
        brain["bankUiContext"] = dict(context.get("bankState"))
        enriched["bankUiContext"] = dict(context.get("bankState"))
    if isinstance(context.get("routeMonitor"), dict):
        brain["routeMonitorContext"] = dict(context.get("routeMonitor"))
        enriched["routeMonitorContext"] = dict(context.get("routeMonitor"))
    proposal_payload = _context_action_proposal_payload(context)
    if proposal_payload:
        brain["contextActionProposal"] = proposal_payload
        enriched["contextActionProposal"] = proposal_payload
        if isinstance(proposal_payload.get("inputGeometry"), dict) and not enriched.get("inputGeometry"):
            enriched["inputGeometry"] = dict(proposal_payload.get("inputGeometry"))
        target = _target_from_context_action_proposal(proposal_payload)
        if target:
            generic = dict(brain.get("genericTaskState")) if isinstance(brain.get("genericTaskState"), dict) else {}
            generic.setdefault("phase", "target_selected")
            generic.setdefault("activeIntent", "select_target")
            generic.setdefault("activeIntentTarget", target)
            brain["genericTaskState"] = generic
            overlay = dict(brain.get("intentOverlayContext")) if isinstance(brain.get("intentOverlayContext"), dict) else {}
            overlay.setdefault("selectedMarker", target)
            overlay.setdefault("markers", [dict(target, markerType="selected_target")])
            brain["intentOverlayContext"] = overlay
    if brain:
        enriched["brain"] = brain
    return enriched


def _plugin_snapshot_payload(snapshot: dict[str, Any], need: str) -> dict[str, Any]:
    payloads = _dict(snapshot.get("payloads"))
    payload = _dict(payloads.get(need))
    if payload:
        return payload
    return _dict(snapshot.get(need))


def _plugin_snapshot_inventory_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    inventory_payload = _plugin_snapshot_payload(snapshot, "inventory")
    inventory = _dict(inventory_payload.get("inventory") or inventory_payload)
    if not inventory:
        return {}
    context: dict[str, Any] = {}
    for key in ("freeSlots", "filledSlots", "inventoryFull", "known", "slotCount", "occupiedSlots"):
        if inventory.get(key) is not None:
            context[key] = inventory.get(key)
    free_slots = _int_or_none(context.get("freeSlots"))
    if free_slots is not None:
        context.setdefault("inventoryFull", free_slots <= 0)
    totals = _dict(inventory.get("totalQuantityByItemId") or inventory.get("itemCounts"))
    normal_logs = _int_or_none(totals.get("1511") if totals.get("1511") is not None else totals.get(1511))
    oak_logs = _int_or_none(totals.get("1521") if totals.get("1521") is not None else totals.get(1521))
    if normal_logs is None or oak_logs is None:
        normal_total = 0
        oak_total = 0
        saw_normal = False
        saw_oak = False
        for item in inventory.get("items") if isinstance(inventory.get("items"), list) else []:
            if not isinstance(item, dict):
                continue
            item_id = _int_or_none(item.get("itemId") if item.get("itemId") is not None else item.get("id"))
            quantity = max(1, _int_or_none(item.get("quantity")) or 1)
            if item_id == 1511:
                normal_total += quantity
                saw_normal = True
            elif item_id == 1521:
                oak_total += quantity
                saw_oak = True
        if normal_logs is None and saw_normal:
            normal_logs = normal_total
        if oak_logs is None and saw_oak:
            oak_logs = oak_total
    woodcutting_logs = (normal_logs or 0) + (oak_logs or 0)
    progress: dict[str, Any] = {}
    if woodcutting_logs:
        progress["currentHeldCount"] = woodcutting_logs
        progress["currentHeldResourceCount"] = woodcutting_logs
        progress["displayedGoalProgress"] = woodcutting_logs
        progress["woodcuttingLogCount"] = woodcutting_logs
    if normal_logs is not None:
        progress["normalLogs"] = normal_logs
    if oak_logs is not None:
        progress["oakLogs"] = oak_logs
    if inventory.get("signature") is not None:
        progress["currentInventorySignature"] = inventory.get("signature")
    if progress:
        context["progress"] = progress
    context["source"] = "plugin_snapshot_inventory"
    return context


def _plugin_snapshot_player_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    baseline = _plugin_snapshot_payload(snapshot, "baseline")
    player = _dict(baseline.get("player"))
    if not player:
        return {}
    context = dict(player)
    world_x = _int_or_none(context.get("worldX"))
    world_y = _int_or_none(context.get("worldY"))
    plane = _int_or_none(context.get("plane"))
    if world_x is not None and world_y is not None:
        context["worldTile"] = {"worldX": world_x, "worldY": world_y, "plane": plane}
    context["source"] = "plugin_snapshot_baseline"
    return context


def _plugin_snapshot_activity_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    activity = _plugin_snapshot_payload(snapshot, "activity")
    if not activity:
        return {}
    context = dict(activity)
    context["source"] = "plugin_snapshot_activity"
    return context


def _plugin_snapshot_resource_candidates(snapshot: dict[str, Any], *, limit: int = 24) -> list[dict[str, Any]]:
    projection = _plugin_snapshot_payload(snapshot, "projection")
    candidates: list[dict[str, Any]] = []
    for value in projection.get("visibleObjectRefs") if isinstance(projection.get("visibleObjectRefs"), list) else []:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or value.get("targetName") or "").strip()
        actions = [str(item).strip() for item in value.get("actions") if str(item).strip()] if isinstance(value.get("actions"), list) else []
        action_text = " ".join(actions).lower()
        name_text = name.lower()
        if "chop down" not in action_text and "tree" not in name_text and "oak" not in name_text:
            continue
        candidate = dict(value)
        candidate["targetName"] = candidate.get("targetName") or name or "Tree"
        candidate["classId"] = candidate.get("classId") or ("oak_tree" if "oak" in name_text else "tree")
        candidate["targetType"] = candidate.get("targetType") or "sceneObject"
        candidate["actionTargetSource"] = candidate.get("actionTargetSource") or "plugin_snapshot_projection"
        candidate["sourceTick"] = candidate.get("sourceTick") or snapshot.get("latestTick")
        candidate["tick"] = candidate.get("tick") or snapshot.get("latestTick")
        if candidate.get("aimPoint") is not None and candidate.get("suggestedClickPoint") is None:
            candidate["suggestedClickPoint"] = candidate.get("aimPoint")
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def _plugin_snapshot_route_transition_candidates(snapshot: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    projection = _plugin_snapshot_payload(snapshot, "projection")
    candidates: list[dict[str, Any]] = []
    transition_names = ("ladder", "stair", "staircase", "stairs", "trapdoor", "door", "gate", "bank", "booth", "banker", "deposit")
    preferred_actions = ("climb", "bank", "deposit", "use", "open", "enter", "exit")
    negative_only_actions = {"close", "examine", "cancel"}
    for value in projection.get("visibleObjectRefs") if isinstance(projection.get("visibleObjectRefs"), list) else []:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or value.get("targetName") or "").strip()
        actions = [str(item).strip() for item in value.get("actions") if str(item).strip()] if isinstance(value.get("actions"), list) else []
        name_text = name.lower()
        action_text = " ".join(actions).lower()
        if not any(term in name_text for term in transition_names):
            continue
        actionable = [action for action in actions if action.strip().lower() not in negative_only_actions]
        if not any(term in action_text for term in preferred_actions):
            continue
        if not actionable:
            continue
        candidate = dict(value)
        candidate["targetName"] = candidate.get("targetName") or name or "Route transition"
        candidate["classId"] = candidate.get("classId") or (
            "bank_service" if any(term in name_text or term in action_text for term in ("bank", "booth", "banker", "deposit")) else "service_route_transition"
        )
        candidate["targetType"] = candidate.get("targetType") or "sceneObject"
        candidate["actions"] = actions
        candidate["expectedOptions"] = actionable
        candidate["expectedTargets"] = [name] if name else []
        candidate["routeStepType"] = "service_interact" if candidate["classId"] == "bank_service" else "interact_object"
        candidate["routeStepLabel"] = candidate["targetName"]
        candidate["actionTargetSource"] = candidate.get("actionTargetSource") or "plugin_snapshot_projection"
        candidate["source"] = candidate.get("source") or "plugin_snapshot_projection"
        candidate["sourceTick"] = candidate.get("sourceTick") or snapshot.get("latestTick")
        candidate["tick"] = candidate.get("tick") or snapshot.get("latestTick")
        candidate["verifiedLive"] = True
        candidate["routeRelevance"] = {
            "schema": "route_relevance.v1",
            "candidateName": candidate["targetName"],
            "candidateActions": actionable,
            "relevanceStatus": "PASS",
            "rejectionReason": None,
            "candidateWouldAdvanceRoute": True,
            "source": "plugin_snapshot_projection",
        }
        if candidate.get("aimPoint") is not None and candidate.get("suggestedClickPoint") is None:
            candidate["suggestedClickPoint"] = candidate.get("aimPoint")
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    candidates.sort(
        key=lambda item: (
            0 if any("climb" in str(action).lower() for action in item.get("actions") or []) else 1,
            0 if any(term in str(item.get("targetName") or "").lower() for term in ("ladder", "stair", "trapdoor")) else 1,
            str(item.get("targetName") or ""),
        )
    )
    return candidates


def _inventory_context_full(inventory_context: dict[str, Any], status: dict[str, Any]) -> bool:
    if _bool_or_none(inventory_context.get("inventoryFull")) is True:
        return True
    free_slots = _int_or_none(inventory_context.get("freeSlots"))
    if free_slots == 0:
        return True
    existing = _dict(_dict(status.get("brain")).get("inventoryContext") or status.get("inventoryContext"))
    if _bool_or_none(existing.get("inventoryFull")) is True:
        return True
    free_slots = _int_or_none(existing.get("freeSlots") if existing else status.get("inventoryFreeSlots"))
    return free_slots == 0


def _snapshot_tick_newer_or_equal(snapshot_tick: int | None, status_tick: int | None) -> bool:
    if snapshot_tick is None:
        return False
    if status_tick is None:
        return True
    return snapshot_tick >= status_tick


def _merge_plugin_snapshot_into_status(status: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(status, dict) or not isinstance(snapshot, dict):
        return status
    hot = snapshot.get("clientTickHot") if isinstance(snapshot.get("clientTickHot"), dict) else {}
    payloads = _dict(snapshot.get("payloads"))
    if not hot:
        hot = _dict(payloads.get("interaction_hot"))
    snapshot_tick = _int_or_none(snapshot.get("latestTick"))
    status_tick = _int_or_none(status.get("latestTick"))
    enriched = dict(status)
    if hot:
        enriched["clientTickHot"] = hot
        if hot.get("sessionPath") is not None:
            enriched["sessionPath"] = hot.get("sessionPath")
    if _snapshot_tick_newer_or_equal(snapshot_tick, status_tick):
        enriched["latestTick"] = snapshot_tick
    if snapshot.get("freshness") is not None:
        enriched["pluginSnapshotFreshness"] = snapshot.get("freshness")
    enriched["pluginSnapshotAgeMillis"] = snapshot.get("maxCacheAgeMillis") or _dict(snapshot.get("freshness")).get("maxCacheAgeMillis") or snapshot.get("snapshotAgeMillis")
    brain = dict(enriched.get("brain")) if isinstance(enriched.get("brain"), dict) else {}
    inventory_context = _plugin_snapshot_inventory_context(snapshot)
    if inventory_context:
        brain["inventoryContext"] = inventory_context
        enriched["inventoryContext"] = inventory_context
    player_context = _plugin_snapshot_player_context(snapshot)
    if player_context:
        brain["playerContext"] = player_context
        enriched["playerContext"] = player_context
        world_tile = _dict(player_context.get("worldTile"))
        if world_tile:
            enriched["playerLocation"] = dict(world_tile)
            enriched["playerLocationSource"] = "plugin_snapshot_baseline"
    activity_context = _plugin_snapshot_activity_context(snapshot)
    if activity_context:
        brain["activityContext"] = activity_context
        enriched["activityContext"] = activity_context
        animation = _int_or_none(activity_context.get("animation"))
        if animation is not None:
            enriched["playerAnimation"] = animation
            if animation == 879:
                enriched["latestEventSummary"] = "Player animation changed: 879"
        generic = dict(brain.get("genericTaskState")) if isinstance(brain.get("genericTaskState"), dict) else {}
        if generic.get("phase") == "blocked" and animation is not None:
            generic["phase"] = "target_selected" if animation == 879 else "observe"
            generic["activeIntent"] = "continue_current_target" if animation == 879 else "observe"
            generic["blockingConditions"] = []
            brain["genericTaskState"] = generic
    resource_candidates = _plugin_snapshot_resource_candidates(snapshot)
    if resource_candidates:
        existing_candidates = [item for item in brain.get("candidateTargets") if isinstance(item, dict)] if isinstance(brain.get("candidateTargets"), list) else []
        brain["candidateTargets"] = [*resource_candidates, *existing_candidates]
        brain["profileCandidates"] = brain["candidateTargets"]
        enriched["candidateTargets"] = brain["candidateTargets"]
        if not _dict(brain.get("genericTaskState")).get("activeIntentTarget"):
            generic = dict(brain.get("genericTaskState")) if isinstance(brain.get("genericTaskState"), dict) else {}
            generic.setdefault("phase", "target_selected")
            generic.setdefault("activeIntent", "select_target")
            generic["activeIntentTarget"] = dict(resource_candidates[0])
            brain["genericTaskState"] = generic
    route_candidates = _plugin_snapshot_route_transition_candidates(snapshot)
    if route_candidates and _inventory_context_full(inventory_context, enriched):
        route_context = dict(brain.get("serviceRouteContext")) if isinstance(brain.get("serviceRouteContext"), dict) else {}
        if not _dict(route_context.get("visibleInteractionTarget") or route_context.get("visibleServiceTarget") or route_context.get("selectedServiceObject")):
            selected_route_candidate = dict(route_candidates[0])
            step_type = str(selected_route_candidate.get("routeStepType") or "interact_object")
            route_context.update(
                {
                    "schema": route_context.get("schema") or "service_route_context.v1",
                    "routeId": route_context.get("routeId") or "plugin_snapshot_route_to_service",
                    "routeStepStatus": "plugin_snapshot_route_transition_visible",
                    "actionReady": True,
                    "currentStepIndex": route_context.get("currentStepIndex", 0),
                    "currentStep": {
                        "type": step_type,
                        "label": selected_route_candidate.get("targetName") or "Route transition",
                        "expectedOptions": list(selected_route_candidate.get("expectedOptions") or selected_route_candidate.get("actions") or []),
                        "expectedTargetContains": list(selected_route_candidate.get("expectedTargets") or [selected_route_candidate.get("targetName")]),
                    },
                    "visibleInteractionTarget": selected_route_candidate if step_type != "service_interact" else route_context.get("visibleInteractionTarget"),
                    "visibleServiceTarget": selected_route_candidate if step_type == "service_interact" else route_context.get("visibleServiceTarget"),
                    "pluginSnapshotRouteTransitionCandidates": route_candidates,
                    "source": "plugin_snapshot_projection",
                }
            )
            brain["serviceRouteContext"] = route_context
            enriched["serviceRouteContext"] = route_context
            service_context = dict(brain.get("serviceContext")) if isinstance(brain.get("serviceContext"), dict) else {}
            service_context.setdefault("serviceNeeded", True)
            service_context.setdefault("serviceRequired", True)
            service_context.setdefault("serviceReady", False)
            service_context["serviceRouteContext"] = route_context
            brain["serviceContext"] = service_context
            enriched["serviceContext"] = service_context
    if brain:
        enriched["brain"] = brain
    return enriched


def _context_fallback_task(options: Any) -> str:
    task = getattr(options, "task", None) or getattr(options, "eval_task", None) or "woodcutting"
    normalized = str(task or "woodcutting").strip().lower()
    if "woodcutting" in normalized:
        return "woodcutting"
    return normalized or "woodcutting"


def _proposal_needs_context_fallback(proposal: ActionProposal) -> bool:
    return proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable


def _context_navigation_proposal_is_stale(proposal: ActionProposal | None) -> bool:
    if proposal is None:
        return False
    if proposal.proposed_action not in NAVIGATION_ACTIONS and proposal.target_kind != "path_tile":
        return False
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    freshness = explanation.get("freshness") if isinstance(explanation.get("freshness"), dict) else {}
    if freshness.get("stale") is True:
        return True
    if str(freshness.get("status") or "").strip().lower() == "stale":
        return True
    if str(freshness.get("targetCandidateFreshness") or "").strip().lower() == "stale":
        return True
    age_ms = _int_or_none(freshness.get("daemonStatusAgeMillis"))
    if age_ms is not None and age_ms > 10_000:
        return True
    return False


def _status_without_context_action_proposal(status: dict[str, Any]) -> dict[str, Any]:
    fresh = deepcopy(status if isinstance(status, dict) else {})
    fresh.pop("contextActionProposal", None)
    brain = dict(fresh.get("brain")) if isinstance(fresh.get("brain"), dict) else {}
    brain.pop("contextActionProposal", None)
    brain.pop("intentOverlayContext", None)
    generic = dict(brain.get("genericTaskState")) if isinstance(brain.get("genericTaskState"), dict) else {}
    active_target = generic.get("activeIntentTarget")
    if isinstance(active_target, dict) and active_target.get("sourceTick") is not None:
        generic.pop("activeIntentTarget", None)
    if generic:
        brain["genericTaskState"] = generic
    if brain:
        fresh["brain"] = brain
    return fresh


def _maybe_context_action_proposal(
    daemon_url: str,
    options: Any,
    status: dict[str, Any],
    proposal: ActionProposal,
    *,
    timeout: float,
) -> tuple[dict[str, Any], ActionProposal, dict[str, Any] | None]:
    if not _proposal_needs_context_fallback(proposal):
        return status, proposal, None
    fallback: dict[str, Any] = {
        "schema": "context_action_fallback.v1",
        "status": "MISS",
        "originalAction": proposal.proposed_action,
        "originalReason": proposal.reason,
    }
    try:
        context_response = fetch_action_context(daemon_url, timeout=min(max(timeout, 0.2), 1.5), task=_context_fallback_task(options))
    except Exception as error:  # noqa: BLE001
        fallback.update(
            {
                "status": "FAIL",
                "reason": "context_unavailable",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        proposal.warnings.append(f"context action fallback unavailable: {fallback['error']}")
        if "context.action_proposal" not in proposal.missing_capabilities:
            proposal.missing_capabilities.append("context.action_proposal")
        proposal.target_explanation = proposal.target_explanation or {}
        proposal.target_explanation["contextActionFallback"] = fallback
        return status, proposal, fallback
    enriched_status = _merge_context_response_into_status(status, context_response)
    payload = _context_action_proposal_payload(context_response)
    context_proposal = _proposal_from_context_payload(payload)
    if context_proposal is None:
        fallback.update(
            {
                "status": "MISS",
                "reason": "context_action_proposal_missing",
                "contextStatus": context_response.get("status"),
            }
        )
        proposal.target_explanation = proposal.target_explanation or {}
        proposal.target_explanation["contextActionFallback"] = fallback
        return enriched_status, proposal, fallback
    if not context_proposal.executable:
        fresh_status = _status_without_context_action_proposal(enriched_status)
        fresh_status_proposal = build_action_proposal(fresh_status)
        if (
            _status_inventory_full(enriched_status) is True
            and fresh_status_proposal.proposed_action == "select_resource_target"
        ):
            fallback.update(
                {
                    "status": "WARN",
                    "reason": "context_action_proposal_rejected_inventory_full",
                    "contextStatus": context_response.get("status"),
                    "contextProposalAction": context_proposal.proposed_action,
                    "contextProposalExecutable": False,
                    "freshProposalAction": fresh_status_proposal.proposed_action,
                    "freshProposalExecutable": bool(fresh_status_proposal.executable),
                }
            )
            proposal.warnings.append(
                "fresh status/plugin state proposed resource collection while inventory is full; keeping route/bank blocker"
            )
            proposal.target_explanation = proposal.target_explanation or {}
            proposal.target_explanation["contextActionFallback"] = fallback
            return enriched_status, proposal, fallback
        if fresh_status_proposal.executable:
            fallback.update(
                {
                    "status": "WARN",
                    "reason": "context_action_proposal_rejected_non_executable",
                    "contextStatus": context_response.get("status"),
                    "contextProposalAction": context_proposal.proposed_action,
                    "contextProposalExecutable": False,
                    "freshProposalAction": fresh_status_proposal.proposed_action,
                    "freshProposalExecutable": True,
                }
            )
            fresh_status_proposal.target_explanation = fresh_status_proposal.target_explanation or {}
            fresh_status_proposal.target_explanation["contextActionFallback"] = fallback
            fresh_status_proposal.warnings = list(
                dict.fromkeys(
                    fresh_status_proposal.warnings
                    + ["non-executable context action fallback rejected; using fresh status/plugin state"]
                )
            )
            return enriched_status, fresh_status_proposal, fallback
    if _context_navigation_proposal_is_stale(context_proposal):
        fresh_status = _status_without_context_action_proposal(enriched_status)
        fresh_status_proposal = build_action_proposal(fresh_status)
        fallback.update(
            {
                "status": "WARN",
                "reason": "context_action_proposal_rejected_stale_navigation",
                "contextStatus": context_response.get("status"),
                "contextProposalAction": context_proposal.proposed_action,
                "contextProposalExecutable": bool(context_proposal.executable),
                "freshProposalAction": fresh_status_proposal.proposed_action,
                "freshProposalExecutable": bool(fresh_status_proposal.executable),
            }
        )
        selected = fresh_status_proposal if fresh_status_proposal.executable else proposal
        selected.target_explanation = selected.target_explanation or {}
        selected.target_explanation["contextActionFallback"] = fallback
        selected.warnings = list(
            dict.fromkeys(
                selected.warnings
                + ["stale context navigation fallback rejected; using fresh status/plugin state"]
            )
        )
        return enriched_status, selected, fallback
    if (
        _status_inventory_full(enriched_status) is True
        and context_proposal.proposed_action == "select_resource_target"
        and str(proposal.reason or "") == "inventory_full_route_context_missing"
    ):
        fallback.update(
            {
                "status": "WARN",
                "reason": "context_action_proposal_rejected_inventory_full",
                "contextStatus": context_response.get("status"),
                "contextProposalAction": context_proposal.proposed_action,
                "contextProposalExecutable": bool(context_proposal.executable),
            }
        )
        proposal.warnings.append(
            "context action fallback proposed resource collection while inventory is full; keeping route/bank blocker"
        )
        proposal.target_explanation = proposal.target_explanation or {}
        proposal.target_explanation["contextActionFallback"] = fallback
        return enriched_status, proposal, fallback
    fallback.update(
        {
            "status": "PASS" if context_proposal.executable else "WARN",
            "reason": "context_action_proposal_used" if context_proposal.executable else "context_action_proposal_not_executable",
            "contextStatus": context_response.get("status"),
            "contextProposalAction": context_proposal.proposed_action,
            "contextProposalExecutable": bool(context_proposal.executable),
        }
    )
    context_proposal.target_explanation = context_proposal.target_explanation or {}
    context_proposal.target_explanation["contextActionFallback"] = fallback
    context_proposal.warnings = list(dict.fromkeys(context_proposal.warnings + ["proposal sourced from context_service action fallback"]))
    return enriched_status, context_proposal, fallback


def _fetch_status_or_action_context(
    daemon_url: str,
    options: Any,
    *,
    fetch_json_func=fetch_json,
    timeout: float = 3.0,
    purpose: str = "status",
) -> dict[str, Any]:
    def attach_plugin_hot(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        try:
            snapshot = fetch_plugin_snapshot(
                str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
                timeout=min(max(timeout, 0.2), 1.0),
                extra_needs=["inventory", "activity", "projection"],
            )
        except Exception:
            return payload
        return _merge_plugin_snapshot_into_status(payload, snapshot)

    try:
        status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
        if isinstance(status, dict):
            try:
                context_response = fetch_action_context(
                    daemon_url,
                    timeout=min(max(timeout, 0.2), 1.5),
                    task=_context_fallback_task(options),
                )
                proposal_payload = _context_action_proposal_payload(context_response)
                context_proposal = _proposal_from_context_payload(proposal_payload)
                status_proposal = build_action_proposal(status)
                should_enrich = bool(
                context_proposal is not None
                and context_proposal.executable
                and not _context_navigation_proposal_is_stale(context_proposal)
                and (
                    not status.get("sessionPath")
                    or not _dict(status.get("inputGeometry")).get("inputGeometryAvailable")
                        or not status_proposal.executable
                    )
                )
                if should_enrich:
                    enriched = _merge_context_response_into_status(status, context_response)
                    fallback = dict(enriched.get("daemonStatusFallback") or {})
                    fallback.update(
                        {
                            "schema": "daemon_status_fallback.v1",
                            "status": "PASS",
                            "purpose": purpose,
                            "source": "action_summary_or_context",
                            "statusEndpointReason": "status_payload_missing_action_context",
                        }
                    )
                    enriched["daemonStatusFallback"] = fallback
                    return attach_plugin_hot(enriched)
            except Exception:
                pass
            return attach_plugin_hot(status)
    except Exception as status_error:  # noqa: BLE001
        try:
            context_response = fetch_action_context(
                daemon_url,
                timeout=min(max(timeout, 0.2), 1.5),
                task=_context_fallback_task(options),
            )
        except Exception as context_error:  # noqa: BLE001
            snapshot = fetch_plugin_snapshot(
                str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
                timeout=min(max(timeout, 0.2), 1.0),
                extra_needs=["inventory", "activity", "projection"],
            )
            enriched = _merge_plugin_snapshot_into_status(
                {
                    "schema": "plugin_snapshot_status_fallback.v1",
                    "status": "WARN",
                    "daemonStatusFallback": {
                        "schema": "daemon_status_fallback.v1",
                        "status": "WARN",
                        "purpose": purpose,
                        "source": "plugin_snapshot",
                        "statusEndpointError": f"{type(status_error).__name__}: {status_error}",
                        "contextEndpointError": f"{type(context_error).__name__}: {context_error}",
                    },
                },
                snapshot,
            )
            warnings = list(enriched.get("warnings") or [])
            warnings.append(
                f"daemon status and compact action context unavailable; using plugin snapshot: "
                f"{type(status_error).__name__}: {status_error}; {type(context_error).__name__}: {context_error}"
            )
            enriched["warnings"] = list(dict.fromkeys(str(item) for item in warnings if item is not None))
            return enriched
        enriched = _merge_context_response_into_status(
            {
                "schema": "context_status_fallback.v1",
                "status": "WARN",
                "daemonStatusFallback": {
                    "schema": "daemon_status_fallback.v1",
                    "status": "PASS",
                    "purpose": purpose,
                    "source": "action_summary_or_context",
                    "statusEndpointError": f"{type(status_error).__name__}: {status_error}",
                },
            },
            context_response,
        )
        warnings = list(enriched.get("warnings") or [])
        warnings.extend(str(item) for item in context_response.get("warnings") or [] if item is not None)
        warnings.append(f"daemon status endpoint unavailable; using compact action context: {type(status_error).__name__}: {status_error}")
        enriched["warnings"] = list(dict.fromkeys(str(item) for item in warnings if item is not None))
        return attach_plugin_hot(enriched)
    return {}


def _status_phase_intent(status: dict[str, Any]) -> tuple[str | None, str | None]:
    generic = _status_context(status, "genericTaskState")
    phase = generic.get("phase") or status.get("phase") or status.get("brainPhase")
    intent = generic.get("activeIntent") or status.get("activeIntent")
    return (str(phase) if phase is not None else None, str(intent) if intent is not None else None)


def _status_player_location(status: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, float | None]:
    status = status if isinstance(status, dict) else {}
    location = status.get("playerLocation") if isinstance(status.get("playerLocation"), dict) else {}
    if location.get("worldX") is not None and location.get("worldY") is not None:
        return (
            {"worldX": location.get("worldX"), "worldY": location.get("worldY"), "plane": location.get("plane")},
            str(status.get("playerLocationSource") or "daemon_player_location"),
            float(status.get("playerLocationConfidence")) if isinstance(status.get("playerLocationConfidence"), (int, float)) else None,
        )
    brain = _status_brain(status)
    player = brain.get("playerContext") if isinstance(brain.get("playerContext"), dict) else {}
    tile = player.get("worldTile") if isinstance(player.get("worldTile"), dict) else player.get("tile")
    if isinstance(tile, dict) and tile.get("worldX") is not None and tile.get("worldY") is not None:
        return (
            {"worldX": tile.get("worldX"), "worldY": tile.get("worldY"), "plane": tile.get("plane")},
            str(player.get("locationSource") or player.get("location_source") or "player_context"),
            float(player.get("locationConfidence")) if isinstance(player.get("locationConfidence"), (int, float)) else 1.0,
        )
    world_x = status.get("playerWorldX")
    world_y = status.get("playerWorldY")
    if world_x is None:
        world_x = player.get("worldX") if "worldX" in player else player.get("world_x")
    if world_y is None:
        world_y = player.get("worldY") if "worldY" in player else player.get("world_y")
    if world_x is not None and world_y is not None:
        return (
            {"worldX": world_x, "worldY": world_y, "plane": player.get("plane", status.get("playerPlane"))},
            str(player.get("locationSource") or player.get("location_source") or "player_context"),
            float(player.get("locationConfidence")) if isinstance(player.get("locationConfidence"), (int, float)) else 1.0,
        )
    pathing = _status_context(status, "pathingContext")
    for value in (
        pathing.get("playerTile"),
        pathing.get("currentPlayerTile"),
        pathing.get("collisionWindowCenterWorld"),
        status.get("pathingCollisionWindowCenterWorld"),
        status.get("collisionWindowCenterWorld"),
    ):
        if isinstance(value, dict) and value.get("worldX") is not None and value.get("worldY") is not None:
            return (
                {"worldX": value.get("worldX"), "worldY": value.get("worldY"), "plane": value.get("plane")},
                "collision_window_center_proxy",
                0.35,
            )
    return None, None, None


def _status_inventory_free_slots(status: dict[str, Any]) -> int | None:
    inventory = _status_context(status, "inventoryContext")
    for key in ("freeSlots", "inventoryFreeSlots"):
        value = _int_or_none(inventory.get(key))
        if value is not None:
            return value
        value = _int_or_none(status.get(key))
        if value is not None:
            return value
    return None


def _status_inventory_full(status: dict[str, Any]) -> bool | None:
    inventory = _status_context(status, "inventoryContext")
    value = _bool_or_none(inventory.get("inventoryFull"))
    free_slots = _status_inventory_free_slots(status)
    if free_slots == 0:
        return True
    if value is not None:
        return value
    value = _bool_or_none(status.get("inventoryFull"))
    if value is not None:
        return value
    phase, _intent = _status_phase_intent(status)
    if phase == "inventory_full":
        return True
    if free_slots is not None:
        return False
    return None


def _status_progress(status: dict[str, Any]) -> dict[str, Any]:
    inventory = _status_context(status, "inventoryContext")
    progress = inventory.get("progress")
    if isinstance(progress, dict):
        return progress
    progress = status.get("brainProgress")
    return progress if isinstance(progress, dict) else {}


def _status_resource_count(status: dict[str, Any]) -> int | None:
    progress = _status_progress(status)
    for key in ("currentHeldCount", "currentHeldResourceCount", "heldResourceCount"):
        value = _int_or_none(progress.get(key))
        if value is not None:
            return value
    for key in ("brainCurrentHeldCount", "inventoryMatchingResourceCount", "heldResourceCount"):
        value = _int_or_none(status.get(key))
        if value is not None:
            return value
    bank_operation = _status_context(status, "bankOperationContext")
    return _int_or_none(bank_operation.get("resourceItemsHeld"))


def _status_progress_count(status: dict[str, Any]) -> int | None:
    progress = _status_progress(status)
    for key in ("displayedGoalProgress", "goalProgress", "currentGoalProgress"):
        value = _int_or_none(progress.get(key))
        if value is not None:
            return value
    return _int_or_none(status.get("displayedGoalProgress"))


def _status_bank_open(status: dict[str, Any]) -> bool | None:
    bank_ui = _status_context(status, "bankUiContext")
    for value in (bank_ui.get("bankOpen"), status.get("bankOpen")):
        parsed = _bool_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _status_banking_complete(status: dict[str, Any]) -> bool | None:
    bank_operation = _status_context(status, "bankOperationContext")
    for value in (bank_operation.get("bankingComplete"), status.get("bankingComplete")):
        parsed = _bool_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _status_resource_target_available(status: dict[str, Any]) -> bool:
    phase, intent = _status_phase_intent(status)
    if phase in {"target_selected", "select_target", "continue_current_target"}:
        return True
    if intent in {"select_target", "select_resource_target", "continue_current_target"}:
        return True
    for context_name in ("returnToResourceContext", "resourceReturnContext"):
        context = _status_context(status, context_name)
        for key in ("resourceTargetAvailable", "returnResourceTargetAvailable"):
            if _bool_or_none(context.get(key)) is True:
                return True
    return _bool_or_none(status.get("returnResourceTargetAvailable")) is True


def _stage_is_service_route(stage: str | None, phase: str | None, intent: str | None) -> bool:
    values = {str(item or "") for item in (stage, phase, intent)}
    return bool(
        values.intersection(
            {
                "inventory_full",
                "needs_service",
                "navigate_to_service",
                "service_available",
                "service_open",
                "bank_operation_pending",
                "service_interaction_pending",
            }
        )
    )


def _stage_is_return_route(stage: str | None, phase: str | None, intent: str | None) -> bool:
    values = {str(item or "") for item in (stage, phase, intent)}
    return bool(values.intersection({"return_to_resource", "return_to_resource_area", "navigate_to_resource_area"}))


def _update_lifecycle_accounting(summary: dict[str, Any], status: dict[str, Any]) -> None:
    state = summary.setdefault("lifecycleAccountingState", {})
    phase, intent = _status_phase_intent(status)
    stage_value = status.get("currentCycleStage") or status.get("cycleStage") or phase
    stage = str(stage_value) if stage_value is not None else None
    tick = _status_tick(status)
    if tick is not None:
        summary["lastLifecycleSampleTick"] = tick
    bank_open = _status_bank_open(status)
    banking_complete = _status_banking_complete(status)
    inventory_full = _status_inventory_full(status)
    resource_count = _status_resource_count(status)
    progress_count = _status_progress_count(status)
    resource_target_available = _status_resource_target_available(status)
    last_stage = state.get("lastStage")
    last_inventory_full = state.get("lastInventoryFull")
    last_bank_open = state.get("lastBankOpen")
    last_banking_complete = state.get("lastBankingComplete")
    last_resource_target_available = state.get("lastResourceTargetAvailable")
    last_resource_count = state.get("lastResourceCount")
    last_progress_count = state.get("lastProgressCount")

    if stage == "collecting_resources" and last_stage != "collecting_resources":
        summary["collectionPhasesStarted"] = int(summary.get("collectionPhasesStarted") or 0) + 1

    inventory_full_event = inventory_full is True and last_inventory_full is not True
    if inventory_full_event:
        summary["inventoryFullEvents"] = int(summary.get("inventoryFullEvents") or 0) + 1
        summary["lifecycleCyclesStarted"] = int(summary.get("lifecycleCyclesStarted") or 0) + 1
        state.update(
            {
                "currentCycleActive": True,
                "serviceRouteActive": False,
                "serviceRouteCompleted": False,
                "serviceCompleteSeen": False,
                "returnRouteActive": False,
                "returnRouteCompleted": False,
                "resourceReacquired": False,
                "cycleCompleted": False,
            }
        )

    if _stage_is_service_route(stage, phase, intent) and state.get("serviceRouteActive") is not True:
        summary["serviceRoutesStarted"] = int(summary.get("serviceRoutesStarted") or 0) + 1
        state["serviceRouteActive"] = True

    if bank_open is True and last_bank_open is not True:
        summary["bankOpenEvents"] = int(summary.get("bankOpenEvents") or 0) + 1
        if state.get("serviceRouteActive") is True and state.get("serviceRouteCompleted") is not True:
            summary["serviceRoutesCompleted"] = int(summary.get("serviceRoutesCompleted") or 0) + 1
            state["serviceRouteCompleted"] = True

    if banking_complete is True and last_banking_complete is not True:
        summary["depositSuccesses"] = int(summary.get("depositSuccesses") or 0) + 1
        summary["serviceCompleteEvents"] = int(summary.get("serviceCompleteEvents") or 0) + 1
        state["serviceCompleteSeen"] = True
        state["postServiceResourceBaseline"] = resource_count
        state["postServiceProgressBaseline"] = progress_count

    if _stage_is_return_route(stage, phase, intent) and state.get("returnRouteActive") is not True:
        summary["returnRoutesStarted"] = int(summary.get("returnRoutesStarted") or 0) + 1
        state["returnRouteActive"] = True

    if (
        state.get("returnRouteActive") is True
        and resource_target_available
        and last_resource_target_available is not True
    ):
        summary["returnRoutesCompleted"] = int(summary.get("returnRoutesCompleted") or 0) + 1
        summary["resourceReacquisitions"] = int(summary.get("resourceReacquisitions") or 0) + 1
        state["returnRouteCompleted"] = True
        state["resourceReacquired"] = True

    resource_delta = 0
    if last_resource_count is not None and resource_count is not None and resource_count > int(last_resource_count):
        resource_delta = max(resource_delta, int(resource_count) - int(last_resource_count))
    if last_progress_count is not None and progress_count is not None and progress_count > int(last_progress_count):
        resource_delta = max(resource_delta, int(progress_count) - int(last_progress_count))
    if (
        resource_delta > 0
        and state.get("serviceCompleteSeen") is True
        and (state.get("returnRouteCompleted") is True or state.get("resourceReacquired") is True)
    ):
        summary["postServiceResourceCollections"] = int(summary.get("postServiceResourceCollections") or 0) + 1
        summary["postServiceLogsCollected"] = int(summary.get("postServiceLogsCollected") or 0) + resource_delta
        if state.get("cycleCompleted") is not True:
            summary["lifecycleCyclesCompleted"] = int(summary.get("lifecycleCyclesCompleted") or 0) + 1
            state["cycleCompleted"] = True

    state["lastStage"] = stage
    state["lastInventoryFull"] = inventory_full
    state["lastBankOpen"] = bank_open
    state["lastBankingComplete"] = banking_complete
    state["lastResourceTargetAvailable"] = resource_target_available
    state["lastResourceCount"] = resource_count
    state["lastProgressCount"] = progress_count


def _new_loop_summary() -> dict[str, Any]:
    return {
        "candidatesEvaluated": 0,
        "aimpointsEvaluated": 0,
        "hoverChecks": 0,
        "proposedActions": 0,
        "actionsAttempted": 0,
        "actionsExecuted": 0,
        "actualClicks": 0,
        "successfulActions": 0,
        "timeouts": 0,
        "consecutiveTimeouts": 0,
        "consecutiveNoProgress": 0,
        "inventoryChanges": 0,
        "inventoryProgressSuccesses": 0,
        "resourceProgressSuccesses": 0,
        "hoverConfirmSuccesses": 0,
        "hoverConfirmFailures": 0,
        "cancelHoverFailures": 0,
        "walkHereHoverFailures": 0,
        "staleHoverSamples": 0,
        "volatileHoverFailures": 0,
        "skippedUnsafeGeometry": 0,
        "skippedHoverMismatch": 0,
        "skippedStaleClientTick": 0,
        "targetsSuppressed": 0,
        "targetNoProgressSuppressions": 0,
        "targetMenuFlipSuppressions": 0,
        "suppressedTargets": [],
        "targetReacquireRounds": 0,
        "reacquireRoundsByBudget": {},
        "reacquireBudgetType": None,
        "reacquireAttemptsUsed": 0,
        "reacquireLimit": 0,
        "phaseScopedBudget": True,
        "budgetResetReason": None,
        "reacquireBudgetResets": 0,
        "stoppedByReacquireLimit": False,
        "candidateWasActionableBeforeLimit": False,
        "staleProposalDetected": False,
        "staleProposalSource": None,
        "reacquireAttempted": False,
        "reacquireResult": None,
        "freshTargetFound": False,
        "freshTargetSource": None,
        "reasonIfNoFreshTarget": None,
        "staleProposalReacquireAttempts": 0,
        "routeTransitionSuppressionOverrides": 0,
        "targetReacquireWaits": 0,
        "targetReacquireWaitMillis": 0,
        "waypointOccludedByObject": 0,
        "navigationAlternateAttempts": 0,
        "cameraAdjustments": 0,
        "navigationInProgressWaits": 0,
        "routeReplanSuppressedWhileMoving": 0,
        "routeOscillationDetections": 0,
        "routeBacktrackingDetections": 0,
        "routeBarrierDetections": 0,
        "navigationTraceEntries": 0,
        "navigationTraceOutputPath": None,
        "lastNavigationTrace": None,
        "navigationNoProgressWithoutBlockEvidence": 0,
        "edgeRouteClicksRejected": 0,
        "cameraReacquireOnEdgeCount": 0,
        "resourceProjectionRecoveryAttempts": 0,
        "resourceProjectionRecoverySuccesses": 0,
        "resourceProjectionRecoveryFailures": 0,
        "debugScreenshotBundlesCaptured": 0,
        "debugScreenshotCaptureFailures": 0,
        "debugScreenshotBundlesSkippedByLimit": 0,
        "debugScreenshotBundlePaths": [],
        "expectedMenuClicks": 0,
        "walkHereClicks": 0,
        "cancelClicks": 0,
        "menuFlipMismatchCount": 0,
        "volatileHoverSkips": 0,
        "verificationTimeouts": 0,
        "finalReconciledSuccesses": 0,
        "delayedProgressReconciliations": 0,
        "resourceTimeoutReconciledSuccesses": 0,
        "resourceTimeoutNoProgress": 0,
        "serviceObjectTimeoutExtendedWaits": 0,
        "unresolvedTimeouts": 0,
        "trueUnresolvedTimeouts": 0,
        "resolvedByRetry": 0,
        "resolvedByLateEvidence": 0,
        "pendingButSafe": 0,
        "routeTransitionAttempts": 0,
        "routeTransitionFirstTrySuccesses": 0,
        "routeTransitionPending": 0,
        "routeTransitionRetryRequired": 0,
        "routeTransitionRetrySuccesses": 0,
        "routeTargetHoverFailures": 0,
        "repeatedRouteTargetHoverFailures": 0,
        "routeTransitionTrueTimeouts": 0,
        "routeTransitionReconciledSuccesses": 0,
        "prematureTransitionRetriesPrevented": 0,
        "timeoutClassifications": {},
        "timeoutReasons": {},
        "timeoutActionTypes": {},
        "timeoutsByIntent": {},
        "retriesByIntent": {},
        "timeoutRecoveredBy": {},
        "evidenceAfterTimeout": [],
        "lifecycleCyclesStarted": 0,
        "lifecycleCyclesCompleted": 0,
        "collectionPhasesStarted": 0,
        "inventoryFullEvents": 0,
        "serviceRoutesStarted": 0,
        "serviceRoutesCompleted": 0,
        "bankOpenEvents": 0,
        "depositSuccesses": 0,
        "serviceCompleteEvents": 0,
        "returnRoutesStarted": 0,
        "returnRoutesCompleted": 0,
        "resourceReacquisitions": 0,
        "postServiceResourceCollections": 0,
        "postServiceLogsCollected": 0,
        "lastLifecycleSampleTick": None,
        "lifecycleAccountingState": {
            "lastStage": None,
            "lastInventoryFull": None,
            "lastBankOpen": None,
            "lastBankingComplete": None,
            "lastResourceTargetAvailable": None,
            "lastResourceCount": None,
            "currentCycleActive": False,
            "serviceRouteActive": False,
            "serviceRouteCompleted": False,
            "serviceCompleteSeen": False,
            "returnRouteActive": False,
            "returnRouteCompleted": False,
            "resourceReacquired": False,
            "cycleCompleted": False,
        },
        "hoverLatencyMinMillis": None,
        "hoverLatencyAvgMillis": None,
        "hoverLatencyMaxMillis": None,
        "pacingProfile": "instant_debug",
        "inputProfile": "instant_debug",
        "averageMouseMoveMs": None,
        "averageClickHoldMs": None,
        "averageReactionDelayMs": None,
        "cameraHoldMinMs": None,
        "cameraHoldAvgMs": None,
        "cameraHoldMaxMs": None,
        "cameraDirectionSwitches": 0,
        "directBackendBypassCount": 0,
        "instantActionsCount": 0,
        "pacingDelayCount": 0,
        "pacingDelayMinMillis": None,
        "pacingDelayAvgMillis": None,
        "pacingDelayMaxMillis": None,
        "pacingDelaysMillis": [],
        "finalReconcileMillis": 0,
        "finalReconcileResult": None,
        "inventoryFreeSlotsStart": None,
        "inventoryFreeSlotsEnd": None,
        "inventoryFullStart": None,
        "inventoryFullEnd": None,
        "resourceCountStart": None,
        "resourceCountEnd": None,
        "progressStart": None,
        "progressEnd": None,
        "finalLocation": None,
        "finalLocationSource": None,
        "finalLocationConfidence": None,
        "finalCycleStage": None,
        "finalPhase": None,
        "finalActiveIntent": None,
        "lastObservedSignals": [],
        "stopReason": "not_applicable",
    }


def _record_loop_status(summary: dict[str, Any], status: dict[str, Any] | None) -> None:
    if not isinstance(status, dict):
        return
    _update_lifecycle_accounting(summary, status)
    free_slots = _status_inventory_free_slots(status)
    inventory_full = _status_inventory_full(status)
    resource_count = _status_resource_count(status)
    progress_count = _status_progress_count(status)
    if summary.get("inventoryFreeSlotsStart") is None and free_slots is not None:
        summary["inventoryFreeSlotsStart"] = free_slots
    if summary.get("inventoryFullStart") is None and inventory_full is not None:
        summary["inventoryFullStart"] = inventory_full
    if summary.get("resourceCountStart") is None and resource_count is not None:
        summary["resourceCountStart"] = resource_count
    if summary.get("progressStart") is None and progress_count is not None:
        summary["progressStart"] = progress_count
    if free_slots is not None:
        summary["inventoryFreeSlotsEnd"] = free_slots
    if inventory_full is not None:
        summary["inventoryFullEnd"] = inventory_full
    if resource_count is not None:
        summary["resourceCountEnd"] = resource_count
    if progress_count is not None:
        summary["progressEnd"] = progress_count
    phase, intent = _status_phase_intent(status)
    location, location_source, location_confidence = _status_player_location(status)
    if location is not None:
        summary["finalLocation"] = location
        summary["finalLocationSource"] = location_source
        summary["finalLocationConfidence"] = location_confidence
    summary["finalCycleStage"] = status.get("currentCycleStage") or status.get("cycleStage") or summary.get("finalCycleStage")
    summary["finalPhase"] = phase or summary.get("finalPhase")
    summary["finalActiveIntent"] = intent or summary.get("finalActiveIntent")


def _loop_resource_progress_delta(summary: dict[str, Any]) -> dict[str, int | None]:
    free_start = _int_or_none(summary.get("inventoryFreeSlotsStart"))
    free_end = _int_or_none(summary.get("inventoryFreeSlotsEnd"))
    resource_start = _int_or_none(summary.get("resourceCountStart"))
    resource_end = _int_or_none(summary.get("resourceCountEnd"))
    progress_start = _int_or_none(summary.get("progressStart"))
    progress_end = _int_or_none(summary.get("progressEnd"))
    return {
        "inventoryFreeSlotsStart": free_start,
        "inventoryFreeSlotsEnd": free_end,
        "inventoryFreeSlotDelta": None if free_start is None or free_end is None else free_end - free_start,
        "resourceCountStart": resource_start,
        "resourceCountEnd": resource_end,
        "resourceCountDelta": None if resource_start is None or resource_end is None else resource_end - resource_start,
        "progressStart": progress_start,
        "progressEnd": progress_end,
        "progressDelta": None if progress_start is None or progress_end is None else progress_end - progress_start,
    }


def _loop_resource_progress_seen(summary: dict[str, Any]) -> bool:
    delta = _loop_resource_progress_delta(summary)
    free_delta = delta.get("inventoryFreeSlotDelta")
    resource_delta = delta.get("resourceCountDelta")
    progress_delta = delta.get("progressDelta")
    return (
        (isinstance(free_delta, int) and free_delta < 0)
        or (isinstance(resource_delta, int) and resource_delta > 0)
        or (isinstance(progress_delta, int) and progress_delta > 0)
    )


def _resource_progress_during_view_recovery_observation(
    observed: dict[str, Any],
    loop_summary: dict[str, Any],
) -> dict[str, Any]:
    progress = dict(observed)
    progress["previousObservedResult"] = observed.get("observedResult")
    progress["previousResultOutcome"] = observed.get("resultOutcome")
    progress["observedResult"] = "resource_progress_during_view_recovery"
    progress["resultOutcome"] = "progress"
    progress["resultComplete"] = True
    progress["nextActionAllowed"] = True
    progress["verificationStatus"] = "WARN"
    progress["resourceProgressClassification"] = "resource_progress_during_view_recovery"
    progress["resourceProjectionRecoveryClassification"] = "resource_progress_during_view_recovery"
    progress["resourceProgressDuringViewRecovery"] = True
    progress["resourceProgressDelta"] = _loop_resource_progress_delta(loop_summary)
    signals = list(progress.get("observedSignals") or [])
    for signal in ("inventory_changed", "inventory_free_slots_changed", "held_resource_count_increased", "resource_progress_increased"):
        if signal not in signals:
            signals.append(signal)
    progress["observedSignals"] = signals
    progress["warnings"] = list(progress.get("warnings") or []) + [
        "resource view recovery did not improve projection, but resource inventory progressed during the recovery window"
    ]
    return progress


def _observed_from_result(result: ExecutionResult) -> dict[str, Any]:
    if isinstance(result.observed_result, dict):
        return result.observed_result
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    observed = lifecycle.get("observedResult")
    return observed if isinstance(observed, dict) else {}


def _navigation_not_executed_observation(result: ExecutionResult) -> dict[str, Any] | None:
    if result.proposed_action not in NAVIGATION_ACTIONS or result.executed:
        return None
    observed = dict(_observed_from_result(result))
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    reason = str(
        observed.get("observedResult")
        or lifecycle.get("reason")
        or (result.warnings[-1] if result.warnings else "")
        or "navigation_action_not_executed"
    )
    observed["observedResult"] = reason
    observed.setdefault("resultOutcome", "blocked" if result.status == "FAIL" else "skipped")
    observed.setdefault("resultComplete", True)
    observed.setdefault("nextActionAllowed", True)
    observed.setdefault("verificationStatus", result.verification_status or ("FAIL" if result.status == "FAIL" else "WARN"))
    observed["navigationActionNotExecuted"] = True
    result.observed_result = observed
    result.verification_status = str(observed.get("verificationStatus") or result.verification_status or "UNKNOWN")
    return observed


def _navigation_not_executed_allows_retry(observed: dict[str, Any] | None) -> bool:
    observed = observed if isinstance(observed, dict) else {}
    return bool(
        observed.get("observedResult") in {"no_click_safety_skip", "hover_confirm_timeout"}
        and observed.get("resultOutcome") == "skipped"
        and observed.get("nextActionAllowed") is True
    )


def _result_has_click_command(result: ExecutionResult) -> bool:
    for command in result.commands:
        if not isinstance(command, dict):
            continue
        command_type = str(command.get("type") or "")
        if command_type == "pre_click_hover_confirm":
            continue
        if "click" in command_type:
            return True
    return False


def _action_intent_type_from_payload(proposal: dict[str, Any] | None, proposed_action: str | None = None) -> str:
    proposal = proposal if isinstance(proposal, dict) else {}
    action = str(proposed_action or proposal.get("proposedAction") or "")
    target_kind = str(proposal.get("targetKind") or "")
    if action == "select_resource_target":
        return "resource_object_action"
    if action == "resource_view_recovery":
        return "resource_view_recovery_action"
    if action == "service_view_recovery":
        return "service_view_recovery_action"
    if action in {"open_service", "deposit_inventory", "deposit_resources", "close_bank"}:
        return "service_object_action"
    if action == "interact_service_route_object":
        return "route_transition_action"
    if action == "interface_dialogue_choice":
        return "interface_dialogue_choice_action"
    if action in NAVIGATION_ACTIONS or target_kind == "path_tile":
        return "navigation_waypoint_action"
    if action == "camera_adjustment":
        return "camera_adjustment_action"
    return "unknown"


def _loop_counts(results: list[ExecutionResult]) -> dict[str, Any]:
    successful = 0
    timeouts = 0
    inventory_changes = 0
    resource_progress_successes = 0
    hover_successes = 0
    hover_failures = 0
    hover_checks = 0
    skipped_unsafe_geometry = 0
    skipped_hover_mismatch = 0
    skipped_stale_client_tick = 0
    cancel_hover_failures = 0
    walk_here_hover_failures = 0
    stale_hover_samples = 0
    volatile_hover_failures = 0
    expected_menu_clicks = 0
    walk_here_clicks = 0
    cancel_clicks = 0
    menu_flip_mismatches = 0
    volatile_hover_skips = 0
    final_reconciled_successes = 0
    delayed_progress_reconciliations = 0
    resource_timeout_reconciled_successes = 0
    resource_timeout_no_progress = 0
    service_object_timeout_extended_waits = 0
    unresolved_timeouts = 0
    route_transition_attempts = 0
    route_transition_first_try_successes = 0
    route_transition_pending = 0
    route_transition_retry_required = 0
    route_transition_retry_successes = 0
    route_target_hover_failures = 0
    repeated_route_target_hover_failures = 0
    route_transition_true_timeouts = 0
    route_transition_reconciled_successes = 0
    premature_transition_retries_prevented = 0
    resolved_by_retry = 0
    resolved_by_late_evidence = 0
    pending_but_safe = 0
    timeouts_by_intent: dict[str, int] = {}
    retries_by_intent: dict[str, int] = {}
    timeout_classifications: dict[str, int] = {}
    timeout_reasons: dict[str, int] = {}
    timeout_action_types: dict[str, int] = {}
    timeout_recovered_by: dict[str, int] = {}
    evidence_after_timeout: list[str] = []
    waypoint_occluded_by_object = 0
    navigation_alternate_attempts = 0
    camera_adjustments = 0
    navigation_in_progress_waits = 0
    route_replan_suppressed = 0
    route_oscillation_detections = 0
    route_backtracking_detections = 0
    route_barrier_detections = 0
    navigation_no_progress_without_block_evidence = 0
    edge_route_clicks_rejected = 0
    camera_reacquire_on_edge_count = 0
    resource_projection_recovery_attempts = 0
    resource_projection_recovery_successes = 0
    resource_projection_recovery_failures = 0
    latest_human_input: dict[str, Any] = {}
    latencies: list[int] = []
    last_signals: list[str] = []
    inventory_signals = {
        "inventory_changed",
        "inventory_free_slots_changed",
        "held_resource_count_increased",
        "resource_count_decreased",
        "banking_complete",
    }
    for result in results:
        hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
        if hover:
            hover_checks += 1
            if hover.get("confirmed") is True:
                hover_successes += 1
            else:
                hover_failures += 1
                reason = str(hover.get("reason") or "")
                latest_match = hover.get("latestMatch") if isinstance(hover.get("latestMatch"), dict) else {}
                latest_sample = hover.get("latestHoverMenu") if isinstance(hover.get("latestHoverMenu"), dict) else latest_match.get("sample")
                latest_class = client_tick_core.classify_menu_action(latest_sample if isinstance(latest_sample, dict) else {})
                if reason == "cancel_hover" or latest_match.get("reason") == "cancel_hover" or latest_class == "cancel_hover":
                    cancel_hover_failures += 1
                if reason == "top_option_rejected" or latest_class == "walk_here":
                    walk_here_hover_failures += 1
                if "stale" in reason or "fresh" in reason:
                    stale_hover_samples += 1
                missing = {str(item) for item in (result.missing_capabilities or [])}
                if not result.executed:
                    if "safe_aimpoint" in missing or "safe" in reason:
                        skipped_unsafe_geometry += 1
                    elif "stale" in reason or "fresh" in reason:
                        skipped_stale_client_tick += 1
                    else:
                        skipped_hover_mismatch += 1
            latency = _int_or_none(hover.get("latencyMillis"))
            if latency is not None:
                latencies.append(latency)
            click_classification = str(hover.get("clickClassification") or "")
            if click_classification in {"clicked_chop_tree", "clicked_expected_action"}:
                expected_menu_clicks += 1
            elif click_classification == "clicked_walk_here":
                walk_here_clicks += 1
            elif click_classification == "clicked_cancel":
                cancel_clicks += 1
        observed = _observed_from_result(result)
        proposal_payload = result.proposal if isinstance(result.proposal, dict) else {}
        target_explanation = proposal_payload.get("targetExplanation") if isinstance(proposal_payload.get("targetExplanation"), dict) else {}
        projection_status = target_explanation.get("routeProjectionStatus") if isinstance(target_explanation.get("routeProjectionStatus"), dict) else {}
        if projection_status.get("classification") == "edge_clipped" and not result.executed:
            edge_route_clicks_rejected += 1
        result_camera_reacquire_on_edge = False
        if isinstance(observed.get("navigationInProgress"), dict):
            navigation_in_progress_waits += 1
            route_replan_suppressed += 1
        if not result.executed and observed.get("skipReason") == "unsafe_geometry":
            skipped_unsafe_geometry += 1
        if not result.executed and observed.get("skipReason") == "volatile_hover_zone":
            volatile_hover_skips += 1
            volatile_hover_failures += 1
        outcome = str(observed.get("resultOutcome") or "")
        trace_for_counts = result.action_trace if isinstance(result.action_trace, dict) else {}
        action_intent_type = str(trace_for_counts.get("actionIntentType") or _action_intent_type_from_payload(result.proposal if isinstance(result.proposal, dict) else {}, result.proposed_action))
        route_transition_classification = str(observed.get("routeTransitionProgressClassification") or observed.get("observedResult") or "")
        if result.executed and result.proposed_action in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"}):
            route_transition_attempts += 1
            if route_transition_classification in {"return_transition_pending", "route_transition_pending", "return_transition_pathing_to_object", "route_transition_pathing_to_object"} or outcome == "still_waiting":
                route_transition_pending += 1
                pending_but_safe += 1
            if route_transition_classification in {"return_transition_retry_required", "route_transition_retry_required"} or outcome == "retry_required":
                route_transition_retry_required += 1
                retries_by_intent[action_intent_type] = retries_by_intent.get(action_intent_type, 0) + 1
            if route_transition_classification in {"return_transition_retry_success", "route_transition_retry_success"} or observed.get("retryOfActionId"):
                route_transition_retry_successes += 1
                resolved_by_retry += 1
            if route_transition_classification in {"return_transition_reconciled_success", "route_transition_reconciled_success"}:
                route_transition_reconciled_successes += 1
                resolved_by_late_evidence += 1
            if route_transition_classification == "retry_while_pending_detected":
                premature_transition_retries_prevented += 1
            if (
                outcome in {"success", "progress", "depleted"}
                and not observed.get("retryOfActionId")
                and route_transition_classification not in {
                    "return_transition_retry_success",
                    "route_transition_retry_success",
                    "return_transition_reconciled_success",
                    "route_transition_reconciled_success",
                }
            ):
                route_transition_first_try_successes += 1
        if result.proposed_action in ROUTE_TRANSITION_ACTIONS:
            final_classification = str(trace_for_counts.get("finalClassification") or "")
            observed_name = str(observed.get("observedResult") or "")
            if observed_name == "route_target_hover_not_confirmed" or final_classification == "route_target_hover_not_confirmed":
                route_target_hover_failures += 1
            if observed_name == "repeated_route_target_hover_failure" or final_classification == "repeated_route_target_hover_failure":
                route_target_hover_failures += 1
                repeated_route_target_hover_failures += 1
        resource_progress_classification = str(observed.get("resourceProgressClassification") or "")
        if observed.get("delayedProgressReconciliation") is True:
            delayed_progress_reconciliations += 1
            resolved_by_late_evidence += 1
            timeout_recovered_by["delayed_progress_reconciliation"] = timeout_recovered_by.get("delayed_progress_reconciliation", 0) + 1
            if "delayed_progress_reconciliation" not in evidence_after_timeout:
                evidence_after_timeout.append("delayed_progress_reconciliation")
        service_object_wait_extended = bool(
            observed.get("serviceObjectTimeoutExtendedWait") is True
            or trace_for_counts.get("serviceObjectTimeoutExtendedWait") is True
        )
        if service_object_wait_extended:
            service_object_timeout_extended_waits += 1
            pending_but_safe += 1
            if "service_object_timeout_extended_wait" not in evidence_after_timeout:
                evidence_after_timeout.append("service_object_timeout_extended_wait")
        if resource_progress_classification == "resource_timeout_reconciled_success":
            resource_timeout_reconciled_successes += 1
            timeout_classifications[resource_progress_classification] = timeout_classifications.get(resource_progress_classification, 0) + 1
            timeout_reasons[resource_progress_classification] = timeout_reasons.get(resource_progress_classification, 0) + 1
        if resource_progress_classification == "resource_timeout_no_progress":
            resource_timeout_no_progress += 1
        recovery_classification = str(observed.get("resourceProjectionRecoveryClassification") or "")
        if result.proposed_action == "resource_view_recovery" and result.executed:
            resource_projection_recovery_attempts += 1
            camera_adjustments += 1
            if recovery_classification in {"resource_camera_reacquire_success", "resource_projection_improved"}:
                resource_projection_recovery_successes += 1
            elif recovery_classification == "resource_projection_recovery_failed":
                resource_projection_recovery_failures += 1
        if result.proposed_action == "service_view_recovery" and result.executed:
            camera_adjustments += 1
        if outcome == "no_change_timeout" and result.executed:
            timeout_classification = resource_progress_classification or str(observed.get("observedResult") or "unclassified_timeout")
            timeout_classifications[timeout_classification] = timeout_classifications.get(timeout_classification, 0) + 1
            timeout_reasons[timeout_classification] = timeout_reasons.get(timeout_classification, 0) + 1
            timeout_action_types[result.proposed_action] = timeout_action_types.get(result.proposed_action, 0) + 1
            timeouts_by_intent[action_intent_type] = timeouts_by_intent.get(action_intent_type, 0) + 1
            if result.proposed_action in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"}):
                route_transition_true_timeouts += 1
            if observed.get("delayedProgressReconciliation") is not True and timeout_classification != "resource_timeout_reconciled_success":
                unresolved_timeouts += 1
        signals = [str(signal) for signal in observed.get("observedSignals") or []]
        if signals:
            last_signals = signals
        if outcome in {"success", "progress", "depleted"} and (signals or observed.get("observedResult")):
            successful += 1
        if outcome == "no_change_timeout" and result.executed:
            timeouts += 1
        if inventory_signals.intersection(signals) or observed.get("observedResult") in {
            "inventory_changed",
            "banking_complete",
            "resource_count_decreased",
        }:
            inventory_changes += 1
        if "resource_progress_increased" in signals or observed.get("observedResult") == "resource_progress_increased":
            resource_progress_successes += 1
        trace = result.action_trace if isinstance(result.action_trace, dict) else {}
        route_stability = trace.get("routeStability") if isinstance(trace.get("routeStability"), dict) else {}
        if route_stability.get("oscillationDetected") is True:
            route_oscillation_detections += 1
        if route_stability.get("backtrackingDetected") is True:
            route_backtracking_detections += 1
        route_no_progress = observed.get("routeNoProgress") if isinstance(observed.get("routeNoProgress"), dict) else {}
        route_no_progress_classification = str(route_no_progress.get("classification") or "")
        if route_no_progress and route_no_progress.get("barrierEvidence") is not True:
            navigation_no_progress_without_block_evidence += 1
        if (
            route_stability.get("barrierDetected") is True
            or route_no_progress.get("barrierEvidence") is True
            or route_no_progress_classification == "route_wrong_node_or_barrier"
            or "route_wrong_node_or_barrier" in signals
        ):
            route_barrier_detections += 1
        if trace.get("finalClassification") == "menu_flip_mismatch":
            menu_flip_mismatches += 1
        client_tick_trace = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
        if client_tick_trace.get("volatileHoverZone") is True and not result.executed:
            volatile_hover_skips += 1
            volatile_hover_failures += 1
        human_input = trace.get("humanInput") if isinstance(trace.get("humanInput"), dict) else {}
        if human_input:
            latest_human_input = human_input
        reacquisition = trace.get("reacquisition") if isinstance(trace.get("reacquisition"), dict) else {}
        if reacquisition.get("primaryWaypointFailure") == "waypoint_edge_projection":
            edge_route_clicks_rejected += 1
        if reacquisition.get("cameraTriggeredBy") == "edge_projection":
            result_camera_reacquire_on_edge = True
        if reacquisition.get("primaryWaypointFailure") == "waypoint_occluded_by_object":
            waypoint_occluded_by_object += 1
        for key in ("navigationAlternateWaypoints", "navigationAlternateWaypointsAfterCamera"):
            attempts = reacquisition.get(key)
            if isinstance(attempts, list):
                navigation_alternate_attempts += len(attempts)
        if isinstance(reacquisition.get("cameraAdjustment"), dict):
            camera_adjustments += 1
        for command in result.commands:
            if isinstance(command, dict) and command.get("reason") == "waypoint_edge_projection":
                result_camera_reacquire_on_edge = True
                break
        if result_camera_reacquire_on_edge:
            camera_reacquire_on_edge_count += 1
        camera_exposure_attempts = reacquisition.get("cameraExposureAttempts")
        if isinstance(camera_exposure_attempts, list):
            camera_adjustments += sum(
                1
                for attempt in camera_exposure_attempts
                if isinstance(attempt, dict) and attempt.get("cameraMoved") is True
            )
        timeline = trace.get("gameTickVerificationTimeline") if isinstance(trace.get("gameTickVerificationTimeline"), list) else []
        if any(isinstance(item, dict) and item.get("reconciled") is True for item in timeline):
            final_reconciled_successes += 1
    executed_count = sum(1 for result in results if result.executed)
    actual_clicks = sum(1 for result in results if result.executed and _result_has_click_command(result))
    consecutive_timeouts = 0
    consecutive_no_progress = 0
    for result in reversed(results):
        observed = _observed_from_result(result)
        outcome = str(observed.get("resultOutcome") or "")
        observed_result = str(observed.get("observedResult") or "")
        no_progress = (
            outcome == "no_change_timeout"
            or observed_result.endswith("_no_progress")
            or observed_result.endswith("_stuck")
            or bool(observed.get("routeNoProgress"))
        )
        if no_progress and result.executed:
            consecutive_no_progress += 1
            if outcome == "no_change_timeout":
                consecutive_timeouts += 1
            continue
        break
    counts = {
        "candidatesEvaluated": len(results),
        "aimpointsEvaluated": len(results),
        "hoverChecks": hover_checks,
        "proposedActions": len(results),
        "actualClicks": actual_clicks,
        "actionsAttempted": executed_count,
        "actionsExecuted": executed_count,
        "successfulActions": successful,
        "timeouts": timeouts,
        "consecutiveTimeouts": consecutive_timeouts,
        "consecutiveNoProgress": consecutive_no_progress,
        "verificationTimeouts": timeouts,
        "inventoryChanges": inventory_changes,
        "inventoryProgressSuccesses": inventory_changes,
        "resourceProgressSuccesses": resource_progress_successes,
        "hoverConfirmSuccesses": hover_successes,
        "hoverConfirmFailures": hover_failures,
        "cancelHoverFailures": cancel_hover_failures,
        "walkHereHoverFailures": walk_here_hover_failures,
        "staleHoverSamples": stale_hover_samples,
        "volatileHoverFailures": volatile_hover_failures,
        "skippedUnsafeGeometry": skipped_unsafe_geometry,
        "skippedHoverMismatch": skipped_hover_mismatch,
        "skippedStaleClientTick": skipped_stale_client_tick,
        "waypointOccludedByObject": waypoint_occluded_by_object,
        "navigationAlternateAttempts": navigation_alternate_attempts,
        "cameraAdjustments": camera_adjustments,
        "resourceProjectionRecoveryAttempts": resource_projection_recovery_attempts,
        "resourceProjectionRecoverySuccesses": resource_projection_recovery_successes,
        "resourceProjectionRecoveryFailures": resource_projection_recovery_failures,
        "navigationInProgressWaits": navigation_in_progress_waits,
        "routeReplanSuppressedWhileMoving": route_replan_suppressed,
        "routeOscillationDetections": route_oscillation_detections,
        "routeBacktrackingDetections": route_backtracking_detections,
        "routeBarrierDetections": route_barrier_detections,
        "navigationNoProgressWithoutBlockEvidence": navigation_no_progress_without_block_evidence,
        "edgeRouteClicksRejected": edge_route_clicks_rejected,
        "cameraReacquireOnEdgeCount": camera_reacquire_on_edge_count,
        "expectedMenuClicks": expected_menu_clicks,
        "walkHereClicks": walk_here_clicks,
        "cancelClicks": cancel_clicks,
        "menuFlipMismatchCount": menu_flip_mismatches,
        "volatileHoverSkips": volatile_hover_skips,
        "finalReconciledSuccesses": final_reconciled_successes,
        "delayedProgressReconciliations": delayed_progress_reconciliations,
        "resourceTimeoutReconciledSuccesses": resource_timeout_reconciled_successes,
        "resourceTimeoutNoProgress": resource_timeout_no_progress,
        "serviceObjectTimeoutExtendedWaits": service_object_timeout_extended_waits,
        "unresolvedTimeouts": unresolved_timeouts,
        "trueUnresolvedTimeouts": unresolved_timeouts,
        "resolvedByRetry": resolved_by_retry,
        "resolvedByLateEvidence": resolved_by_late_evidence,
        "pendingButSafe": pending_but_safe,
        "routeTransitionAttempts": route_transition_attempts,
        "routeTransitionFirstTrySuccesses": route_transition_first_try_successes,
        "routeTransitionPending": route_transition_pending,
        "routeTransitionRetryRequired": route_transition_retry_required,
        "routeTransitionRetrySuccesses": route_transition_retry_successes,
        "routeTargetHoverFailures": route_target_hover_failures,
        "repeatedRouteTargetHoverFailures": repeated_route_target_hover_failures,
        "routeTransitionTrueTimeouts": route_transition_true_timeouts,
        "routeTransitionReconciledSuccesses": route_transition_reconciled_successes,
        "prematureTransitionRetriesPrevented": premature_transition_retries_prevented,
        "timeoutClassifications": dict(timeout_classifications),
        "timeoutReasons": dict(timeout_reasons),
        "timeoutActionTypes": dict(timeout_action_types),
        "timeoutsByIntent": dict(timeouts_by_intent),
        "retriesByIntent": dict(retries_by_intent),
        "timeoutRecoveredBy": dict(timeout_recovered_by),
        "evidenceAfterTimeout": list(evidence_after_timeout),
        "hoverLatencyMinMillis": min(latencies) if latencies else None,
        "hoverLatencyAvgMillis": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "hoverLatencyMaxMillis": max(latencies) if latencies else None,
        "lastObservedSignals": last_signals,
    }
    if latest_human_input:
        counts.update(
            {
                "inputProfile": latest_human_input.get("profile"),
                "averageMouseMoveMs": latest_human_input.get("averageMouseMoveMs"),
                "averageClickHoldMs": latest_human_input.get("averageClickHoldMs"),
                "averageReactionDelayMs": latest_human_input.get("averageReactionDelayMs"),
                "cameraHoldMinMs": latest_human_input.get("cameraHoldMinMs"),
                "cameraHoldAvgMs": latest_human_input.get("cameraHoldAvgMs"),
                "cameraHoldMaxMs": latest_human_input.get("cameraHoldMaxMs"),
                "cameraDirectionSwitches": latest_human_input.get("cameraDirectionSwitches", 0),
                "directBackendBypassCount": latest_human_input.get("directBackendBypassCount", 0),
                "instantActionsCount": 1 if latest_human_input.get("profile") == "instant_debug" else 0,
            }
        )
    return counts


def _refresh_loop_summary(summary: dict[str, Any], results: list[ExecutionResult]) -> None:
    counts = _loop_counts(results)
    preserved_debug = {
        key: summary.get(key)
        for key in (
            "debugScreenshotBundlesCaptured",
            "debugScreenshotCaptureFailures",
            "debugScreenshotBundlesSkippedByLimit",
            "debugScreenshotBundlePaths",
            "staleProposalDetected",
            "staleProposalSource",
            "reacquireAttempted",
            "reacquireResult",
            "freshTargetFound",
            "freshTargetSource",
            "reasonIfNoFreshTarget",
            "staleProposalReacquireAttempts",
        )
    }
    summary.update(counts)
    for key, value in preserved_debug.items():
        if value is not None:
            summary[key] = value


def _update_debug_bundle_summary(summary: dict[str, Any], writer: VisualDebugBundleWriter | None) -> None:
    if writer is None:
        return
    metrics = writer.metrics()
    summary["debugScreenshotBundlesCaptured"] = metrics.get("captured", 0)
    summary["debugScreenshotCaptureFailures"] = metrics.get("captureFailures", 0)
    summary["debugScreenshotBundlesSkippedByLimit"] = metrics.get("skippedByLimit", 0)
    summary["debugScreenshotBundlePaths"] = list(metrics.get("bundlePaths") or [])


def _capture_debug_bundle(
    writer: VisualDebugBundleWriter | None,
    summary: dict[str, Any],
    reason: str,
    *,
    daemon_status: dict[str, Any] | None = None,
    proposal: ActionProposal | dict[str, Any] | None = None,
    result: ExecutionResult | None = None,
    readiness: dict[str, Any] | None = None,
    classification: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if writer is None:
        return None
    trace = result.action_trace if isinstance(result, ExecutionResult) and isinstance(result.action_trace, dict) else None
    if proposal is None and isinstance(result, ExecutionResult):
        proposal = result.proposal
    clicked_menu = None
    if isinstance(result, ExecutionResult) and isinstance(result.hover_confirmation, dict):
        clicked_menu = result.hover_confirmation.get("lastMenuOptionClickedAfter")
    event = writer.capture(
        reason,
        daemon_status=daemon_status,
        proposal=proposal,
        action_trace=trace,
        readiness=readiness,
        clicked_menu=clicked_menu if isinstance(clicked_menu, dict) else None,
        classification=classification,
        loop_summary=summary,
        extra=extra,
    )
    if event is not None and isinstance(result, ExecutionResult) and isinstance(result.action_trace, dict):
        result.action_trace.setdefault("visualDebugBundles", []).append(dict(event))
    _update_debug_bundle_summary(summary, writer)
    return event


def _route_edge_reject_result(result: ExecutionResult) -> bool:
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    projection = explanation.get("routeProjectionStatus") if isinstance(explanation.get("routeProjectionStatus"), dict) else {}
    if projection.get("classification") == "edge_clipped" and not result.executed:
        return True
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    reacquisition = trace.get("reacquisition") if isinstance(trace.get("reacquisition"), dict) else {}
    return reacquisition.get("primaryWaypointFailure") == "waypoint_edge_projection"


def _timeout_debug_reason(result: ExecutionResult) -> str | None:
    observed = _observed_from_result(result)
    if str(observed.get("resultOutcome") or "") != "no_change_timeout":
        return None
    if result.proposed_action == "select_resource_target":
        return "resource_timeout"
    if result.proposed_action in NAVIGATION_ACTIONS:
        return "route_no_progress_timeout"
    return "failure"


def _final_reconcile_ms(options: Any) -> int:
    return max(0, int(getattr(options, "final_reconcile_ms", 0) or 0))


def _final_reconcile_game_ticks(options: Any) -> int:
    return max(0, int(getattr(options, "final_reconcile_game_ticks", 0) or 0))


def _resource_reconcile_ms(options: Any) -> int:
    return max(0, int(getattr(options, "resource_reconcile_ms", 0) or 0))


def _resource_reconcile_game_ticks(options: Any) -> int:
    return max(
        0,
        int(getattr(options, "resource_reconcile_game_ticks", 0) or 0),
        int(getattr(options, "post_click_progress_tail_ticks", 0) or 0),
    )


def _nav_verify_ms(options: Any) -> int:
    return max(0, int(getattr(options, "nav_verify_ms", 0) or 0))


def _nav_verify_game_ticks(options: Any) -> int:
    return max(0, int(getattr(options, "nav_verify_game_ticks", 0) or 0))


def _transition_verify_ms(options: Any) -> int:
    explicit = max(0, int(getattr(options, "transition_verify_ms", 0) or 0))
    return explicit if explicit > 0 else _nav_verify_ms(options)


def _transition_verify_game_ticks(options: Any) -> int:
    explicit = max(0, int(getattr(options, "transition_verify_game_ticks", 0) or 0))
    return explicit if explicit > 0 else _nav_verify_game_ticks(options)


def _transition_pending_game_ticks(options: Any) -> int:
    return max(0, int(getattr(options, "transition_pending_game_ticks", 0) or 0))


def _transition_retry_after_stall_ticks(options: Any) -> int:
    return max(0, int(getattr(options, "transition_retry_after_stall_ticks", 0) or 0))


def _nav_progress_min_distance(options: Any) -> float:
    try:
        return max(0.0, float(getattr(options, "nav_progress_min_distance", 0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _action_timeout_millis_for_verification(action: str, options: Any) -> int:
    base_timeout_ms = int(_action_timeout_seconds(options) * 1000)
    if action in ROUTE_TRANSITION_ACTIONS:
        transition_timeout_ms = _transition_verify_ms(options)
        tick_timeout_ms = _transition_verify_game_ticks(options) * 700
        return max(base_timeout_ms, transition_timeout_ms, tick_timeout_ms)
    if action in NAVIGATION_ACTIONS:
        nav_timeout_ms = _nav_verify_ms(options)
        tick_timeout_ms = _nav_verify_game_ticks(options) * 700
        return max(base_timeout_ms, nav_timeout_ms, tick_timeout_ms)
    if action == "select_resource_target":
        tick_timeout_ms = _resource_reconcile_game_ticks(options) * 700
        return max(base_timeout_ms, _resource_reconcile_ms(options), tick_timeout_ms)
    return base_timeout_ms


def _action_timeout_ticks_for_verification(action: str, options: Any) -> int | None:
    if action in ROUTE_TRANSITION_ACTIONS:
        ticks = _transition_verify_game_ticks(options)
        return ticks if ticks > 0 else None
    if action in NAVIGATION_ACTIONS:
        return _nav_verify_game_ticks(options)
    if action == "select_resource_target":
        ticks = _resource_reconcile_game_ticks(options)
        return ticks if ticks > 0 else None
    return None


def _status_tick(status: dict[str, Any] | None) -> int | None:
    status = status if isinstance(status, dict) else {}
    for value in (status.get("latestTick"), _dict(status.get("brain")).get("latestTick")):
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                continue
    return None


def _nav_replan_while_moving(options: Any) -> bool:
    return bool(getattr(options, "nav_replan_while_moving", False))


def _nav_min_game_ticks_between_clicks(options: Any) -> int:
    return max(0, int(getattr(options, "nav_min_game_ticks_between_clicks", 3) or 0))


def _nav_stuck_game_ticks(options: Any) -> int:
    return max(0, int(getattr(options, "nav_stuck_game_ticks", 6) or 0))


def _nav_destination_arrival_distance(options: Any) -> int:
    return max(0, int(getattr(options, "nav_destination_arrival_distance", 1) or 0))


def _status_player_tile(status: dict[str, Any] | None) -> dict[str, int] | None:
    status = status if isinstance(status, dict) else {}
    for value in (
        _status_context(status, "playerContext").get("worldTile"),
        _status_context(status, "playerContext").get("tile"),
        _status_context(status, "player").get("worldTile"),
        _status_context(status, "player").get("tile"),
        status.get("playerWorldTile"),
        status.get("playerTile"),
    ):
        if isinstance(value, dict):
            world_x = _int_or_none(value.get("worldX") if value.get("worldX") is not None else value.get("x"))
            world_y = _int_or_none(value.get("worldY") if value.get("worldY") is not None else value.get("y"))
            plane = _int_or_none(value.get("plane"))
            if world_x is not None and world_y is not None:
                return {"worldX": world_x, "worldY": world_y, "plane": 0 if plane is None else plane}
    player = _status_context(status, "playerContext") or _status_context(status, "player")
    world_x = _int_or_none(player.get("worldX") if player.get("worldX") is not None else status.get("playerWorldX"))
    world_y = _int_or_none(player.get("worldY") if player.get("worldY") is not None else status.get("playerWorldY"))
    plane = _int_or_none(player.get("plane") if player.get("plane") is not None else status.get("playerPlane"))
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": 0 if plane is None else plane}


def _tile_distance(left: dict[str, Any] | None, right: dict[str, Any] | None) -> int | None:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    left_x = _int_or_none(left.get("worldX") if left.get("worldX") is not None else left.get("x"))
    left_y = _int_or_none(left.get("worldY") if left.get("worldY") is not None else left.get("y"))
    right_x = _int_or_none(right.get("worldX") if right.get("worldX") is not None else right.get("x"))
    right_y = _int_or_none(right.get("worldY") if right.get("worldY") is not None else right.get("y"))
    if left_x is None or left_y is None or right_x is None or right_y is None:
        return None
    return max(abs(left_x - right_x), abs(left_y - right_y))


def _status_movement_state(status: dict[str, Any] | None) -> str:
    status = status if isinstance(status, dict) else {}
    pathing = _status_context(status, "pathingContext")
    for value in (
        pathing.get("movementState"),
        status.get("pathMovementState"),
        _status_context(status, "activityContext").get("currentActivity"),
        _status_context(status, "activity").get("currentActivity"),
    ):
        if value is not None:
            return str(value).strip().lower()
    raw = _status_context(status, "activityContext").get("raw")
    activity = raw.get("activity") if isinstance(raw, dict) and isinstance(raw.get("activity"), dict) else {}
    if activity.get("isMoving") is True:
        return "moving"
    return "unknown"


def _navigation_trace_enabled(options: Any | None) -> bool:
    return bool(getattr(options, "nav_trace", False) or getattr(options, "nav_trace_console", False))


def _status_session_path(status: dict[str, Any] | None) -> str | None:
    status = status if isinstance(status, dict) else {}
    candidates: list[Any] = [
        status.get("sessionPath"),
        status.get("activeSessionPath"),
        status.get("daemonSessionPath"),
    ]
    readiness = status.get("readiness") if isinstance(status.get("readiness"), dict) else {}
    session = readiness.get("session") if isinstance(readiness.get("session"), dict) else {}
    candidates.extend(
        [
            session.get("daemonSessionPath"),
            session.get("latestLiveSessionPath"),
            session.get("latestSessionPath"),
            session.get("activeSessionPath"),
        ]
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _navigation_trace_output_path(options: Any | None, status: dict[str, Any] | None) -> Path:
    raw = str(getattr(options, "nav_trace_output", "") or "interaction_geometry/live/navigation_decisions.jsonl")
    output = Path(raw)
    if output.is_absolute():
        return output
    session_path = _status_session_path(status)
    if session_path:
        return Path(session_path) / output
    return output


def _compact_world_tile(tile: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(tile, dict):
        return None
    world_x = _int_or_none(tile.get("worldX") if tile.get("worldX") is not None else tile.get("x"))
    world_y = _int_or_none(tile.get("worldY") if tile.get("worldY") is not None else tile.get("y"))
    plane = _int_or_none(tile.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": 0 if plane is None else plane}


def _navigation_metric_value(status: dict[str, Any] | None, name: str) -> float | None:
    status = status if isinstance(status, dict) else {}
    pathing = _status_context(status, "pathingContext")
    service = _status_context(status, "serviceContext")
    nav = _status_context(status, "navigationIntentContext")
    aliases: dict[str, tuple[tuple[dict[str, Any], str], ...]] = {
        "destinationDistance": ((pathing, "distanceToDestination"), (status, "distanceToDestination")),
        "pathTargetDistance": ((pathing, "distanceToPathTarget"), (status, "distanceToPathTarget")),
        "serviceDistance": ((service, "distanceToServiceTarget"), (pathing, "distanceToServiceTarget"), (status, "distanceToServiceTarget")),
        "navigationIntentDistance": ((nav, "distanceTiles"), (status, "navigationIntentDistanceTiles")),
        "finalApproachDistance": ((service, "distanceToFinalApproach"), (pathing, "distanceToFinalApproach"), (status, "serviceDistanceToFinalApproach")),
    }
    for source, key in aliases.get(name, ()):
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _navigation_goal_tile(status: dict[str, Any] | None, proposal: ActionProposal | None = None) -> dict[str, int] | None:
    status = status if isinstance(status, dict) else {}
    pathing = _status_context(status, "pathingContext")
    service_route = _status_context(status, "serviceRouteContext")
    for value in (
        pathing.get("destinationTile"),
        pathing.get("pathTargetTile"),
        service_route.get("destinationTile"),
        service_route.get("pathTargetTile"),
        proposal.target_tile if proposal is not None else None,
        proposal.suggested_world_tile if proposal is not None else None,
    ):
        tile = _compact_world_tile(value if isinstance(value, dict) else None)
        if tile is not None:
            return tile
    return None


def _navigation_distance_to_goal(status: dict[str, Any] | None, proposal: ActionProposal | None = None) -> float | None:
    for name in ("destinationDistance", "pathTargetDistance", "serviceDistance", "navigationIntentDistance"):
        value = _navigation_metric_value(status, name)
        if value is not None:
            return value
    player = _status_player_tile(status)
    goal = _navigation_goal_tile(status, proposal)
    distance = _tile_distance(player, goal)
    return float(distance) if distance is not None else None


def _navigation_route_trace(status: dict[str, Any] | None) -> dict[str, Any]:
    status = status if isinstance(status, dict) else {}
    route = _status_context(status, "serviceRouteContext")
    pathing = _status_context(status, "pathingContext")
    return {
        "routeId": route.get("routeId") or status.get("serviceRouteId"),
        "currentNodeId": route.get("currentNodeId") or status.get("serviceRouteCurrentNodeId"),
        "currentStepIndex": route.get("currentStepIndex") if route.get("currentStepIndex") is not None else status.get("serviceRouteCurrentStepIndex"),
        "currentStepId": route.get("currentStepId") or route.get("stepId"),
        "currentStepKind": route.get("currentStepKind") or route.get("currentStepType") or route.get("nextEdgeType"),
        "routeStepStatus": route.get("routeStepStatus") or status.get("serviceRouteStepStatus"),
        "pathingNeeded": pathing.get("pathingNeeded"),
        "localReachability": pathing.get("localReachability"),
        "pathingReason": pathing.get("reason"),
    }


def _navigation_chosen_subgoal(proposal: ActionProposal | None) -> dict[str, Any] | None:
    if proposal is None:
        return None
    resolution = proposal.click_point_resolution if isinstance(proposal.click_point_resolution, dict) else {}
    resolved_screen = proposal.resolved_screen_click_point
    if not isinstance(resolved_screen, dict):
        resolved_screen = resolution.get("screenClickPoint") if isinstance(resolution.get("screenClickPoint"), dict) else None
    return {
        "proposedAction": proposal.proposed_action,
        "targetKind": proposal.target_kind,
        "targetName": proposal.target_name,
        "targetTile": _compact_world_tile(proposal.target_tile),
        "suggestedWorldTile": _compact_world_tile(proposal.suggested_world_tile),
        "suggestedClickPoint": dict(proposal.suggested_click_point) if isinstance(proposal.suggested_click_point, dict) else None,
        "resolvedScreenClickPoint": dict(resolved_screen) if isinstance(resolved_screen, dict) else None,
        "coordinate": {
            key: resolution.get(key)
            for key in (
                "coordinateResolver",
                "coordinateMethod",
                "coordinateSpace",
                "displayScaleApplied",
                "displayScaleReason",
                "screenPointBeforeScaling",
                "screenPointAfterScaling",
                "clickFailureBucket",
            )
            if resolution.get(key) is not None
        } if resolution else None,
        "actionTargetSource": proposal.action_target_source,
        "actionability": proposal.actionability,
        "executable": proposal.executable,
    }


def _navigation_pending_state(observed: dict[str, Any] | None, status: dict[str, Any] | None) -> dict[str, Any]:
    observed = observed if isinstance(observed, dict) else {}
    return {
        "movementState": _status_movement_state(status),
        "observedResult": observed.get("observedResult"),
        "resultOutcome": observed.get("resultOutcome"),
        "resultComplete": observed.get("resultComplete"),
        "nextActionAllowed": observed.get("nextActionAllowed"),
        "navigationInProgress": observed.get("navigationInProgress") if isinstance(observed.get("navigationInProgress"), dict) else None,
    }


def _navigation_trace_entry(
    *,
    decision: str,
    reason: str,
    status: dict[str, Any] | None = None,
    previous_status: dict[str, Any] | None = None,
    proposal: ActionProposal | None = None,
    observed: dict[str, Any] | None = None,
    recovery_mode: str | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    status = status if isinstance(status, dict) else {}
    previous_status = previous_status if isinstance(previous_status, dict) else None
    observed = observed if isinstance(observed, dict) else {}
    current_distance = _navigation_distance_to_goal(status, proposal)
    previous_distance = _navigation_distance_to_goal(previous_status, proposal) if previous_status is not None else None
    distance_delta = None
    if current_distance is not None and previous_distance is not None:
        distance_delta = round(current_distance - previous_distance, 3)
    distances = {
        "distanceToGoal": current_distance,
        "lastDistanceToGoal": previous_distance,
        "currentDistanceToGoal": current_distance,
        "distanceDelta": distance_delta,
        "distanceImproving": bool(distance_delta is not None and distance_delta < 0),
        "destinationDistance": _navigation_metric_value(status, "destinationDistance"),
        "pathTargetDistance": _navigation_metric_value(status, "pathTargetDistance"),
        "serviceDistance": _navigation_metric_value(status, "serviceDistance"),
        "navigationIntentDistance": _navigation_metric_value(status, "navigationIntentDistance"),
        "finalApproachDistance": _navigation_metric_value(status, "finalApproachDistance"),
    }
    clicked_waypoint = observed.get("clickedWaypointMovement") if isinstance(observed.get("clickedWaypointMovement"), dict) else None
    if clicked_waypoint:
        distances["clickedWaypointMovement"] = dict(clicked_waypoint)
    entry = {
        "schema": "navigation_decision_trace.v1",
        "wallTimeMillis": _wall_time_millis(),
        "tick": _status_tick(status) if _status_tick(status) is not None else (proposal.source_tick if proposal is not None else None),
        "playerWorldPosition": _status_player_tile(status),
        "destinationWorldPosition": _navigation_goal_tile(status, proposal),
        "routeStep": _navigation_route_trace(status),
        "distances": distances,
        "pending": _navigation_pending_state(observed, status),
        "chosenSubgoal": _navigation_chosen_subgoal(proposal),
        "recoveryMode": recovery_mode,
        "decision": str(decision or "unknown"),
        "reason": str(reason or "unspecified_navigation_decision"),
        "actionId": action_id,
    }
    return entry


def _navigation_trace_console_line(entry: dict[str, Any]) -> str:
    player = entry.get("playerWorldPosition") if isinstance(entry.get("playerWorldPosition"), dict) else {}
    goal = entry.get("destinationWorldPosition") if isinstance(entry.get("destinationWorldPosition"), dict) else {}
    distances = entry.get("distances") if isinstance(entry.get("distances"), dict) else {}
    route = entry.get("routeStep") if isinstance(entry.get("routeStep"), dict) else {}
    return (
        "NAV "
        f"tick={entry.get('tick')} "
        f"decision={entry.get('decision')} "
        f"reason={entry.get('reason')} "
        f"pos={player.get('worldX')},{player.get('worldY')},{player.get('plane')} "
        f"goal={goal.get('worldX')},{goal.get('worldY')},{goal.get('plane')} "
        f"dist={distances.get('currentDistanceToGoal')} "
        f"delta={distances.get('distanceDelta')} "
        f"step={route.get('currentNodeId') or route.get('currentStepId') or route.get('routeStepStatus')}"
    )


def _record_navigation_trace(
    *,
    options: Any | None,
    loop_summary: dict[str, Any] | None,
    decision: str,
    reason: str,
    status: dict[str, Any] | None = None,
    previous_status: dict[str, Any] | None = None,
    proposal: ActionProposal | None = None,
    observed: dict[str, Any] | None = None,
    result: ExecutionResult | None = None,
    recovery_mode: str | None = None,
) -> dict[str, Any] | None:
    if not _navigation_trace_enabled(options):
        return None
    action_id = result.action_id if isinstance(result, ExecutionResult) else None
    entry = _navigation_trace_entry(
        decision=decision,
        reason=reason,
        status=status,
        previous_status=previous_status,
        proposal=proposal,
        observed=observed,
        recovery_mode=recovery_mode,
        action_id=action_id,
    )
    if isinstance(result, ExecutionResult) and isinstance(result.action_trace, dict):
        result.action_trace.setdefault("navigationDecisionTrace", []).append(dict(entry))
    if isinstance(loop_summary, dict):
        loop_summary["navigationTraceEntries"] = int(loop_summary.get("navigationTraceEntries") or 0) + 1
        loop_summary["lastNavigationTrace"] = dict(entry)
    if bool(getattr(options, "nav_trace", False)):
        output = _navigation_trace_output_path(options, status)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=False) + "\n")
        if isinstance(loop_summary, dict):
            loop_summary["navigationTraceOutputPath"] = str(output)
    if bool(getattr(options, "nav_trace_console", False)):
        print(_navigation_trace_console_line(entry), flush=True)
    return entry


def _navigation_decision_from_observed(observed: dict[str, Any] | None) -> tuple[str, str, str | None]:
    observed = observed if isinstance(observed, dict) else {}
    result = str(observed.get("observedResult") or "")
    outcome = str(observed.get("resultOutcome") or "")
    signals = {str(signal) for signal in observed.get("observedSignals") or []}
    if isinstance(observed.get("navigationInProgress"), dict):
        lock = observed.get("navigationInProgress") or {}
        return "wait", str(lock.get("replanSuppressedReason") or result or "navigation_in_progress"), None
    if signals.intersection({"route_node_changed", "route_step_changed", "route_step_index_changed", "route_step_status_changed", "service_ready"}):
        return "advance", result or "route_step_progress", None
    if result in {"service_navigation_reached_node", "resource_return_reached_node", "bank_opened", "service_ready"}:
        return "advance", result, None
    if outcome in {"success", "progress", "depleted"}:
        return "wait", result or outcome, None
    route_no_progress = observed.get("routeNoProgress") if isinstance(observed.get("routeNoProgress"), dict) else {}
    if route_no_progress:
        if route_no_progress.get("barrierEvidence") is True:
            return "recover", str(route_no_progress.get("classification") or "route_no_progress"), str(route_no_progress.get("classification") or "route_no_progress")
        return "wait", str(route_no_progress.get("classification") or "navigation_no_progress_no_block_evidence"), None
    if outcome == "no_change_timeout":
        return "wait", result or "navigation_timeout_no_progress", None
    if outcome in {"skipped", "menu_mismatch", "blocked"}:
        return "fail", result or outcome, None
    return "wait", result or outcome or "navigation_observation", None


def _status_has_navigation_context(status: dict[str, Any] | None) -> bool:
    status = status if isinstance(status, dict) else {}
    pathing = _status_context(status, "pathingContext")
    route = _status_context(status, "serviceRouteContext")
    phase, intent = _status_phase_intent(status)
    return bool(
        pathing
        or route
        or str(intent or "").lower() in {"needs_service", "return_to_resource", "return_to_resource_area", "navigation"}
        or str(phase or "").lower() in {"inventory_full", "service_route", "return_to_resource"}
    )


def _status_route_object_actionable(status: dict[str, Any] | None) -> bool:
    status = status if isinstance(status, dict) else {}
    route = _status_context(status, "serviceRouteContext")
    if _bool_or_none(route.get("actionReady")) is True:
        return True
    if isinstance(route.get("visibleInteractionTarget"), dict) and route.get("visibleInteractionTarget"):
        return True
    return _bool_or_none(status.get("serviceRouteActionReady")) is True


def _navigation_motion_lock_observation(
    *,
    action: str,
    proposal: ActionProposal | None,
    status: dict[str, Any] | None,
    observed: dict[str, Any] | None,
    options: Any,
) -> dict[str, Any] | None:
    observed = observed if isinstance(observed, dict) else {}
    if action not in NAVIGATION_ACTIONS or _nav_replan_while_moving(options):
        return None
    if observed.get("resultOutcome") != "progress":
        return None
    signals = {str(signal) for signal in observed.get("observedSignals") or []}
    if signals.intersection({"service_ready", "route_object_reacquired", "resource_target_visible"}):
        return None
    result_name = str(observed.get("observedResult") or "")
    if result_name not in {"service_navigation_progress", "resource_return_progress"}:
        return None
    elapsed_ticks = observed.get("elapsedTicks") if isinstance(observed.get("elapsedTicks"), int) else None
    min_ticks = _nav_min_game_ticks_between_clicks(options)
    movement_state = _status_movement_state(status)
    movement_active = movement_state in {"moving", "recently_moved"}
    target_tile = proposal.target_tile if proposal is not None and isinstance(proposal.target_tile, dict) else None
    player_tile = _status_player_tile(status)
    arrival_distance = _tile_distance(player_tile, target_tile)
    arrived = arrival_distance is not None and arrival_distance <= _nav_destination_arrival_distance(options)
    if _status_route_object_actionable(status):
        return None
    should_hold = False
    reason = None
    if elapsed_ticks is not None and elapsed_ticks < min_ticks:
        should_hold = True
        reason = "min_game_ticks_between_route_clicks"
    if movement_active and not arrived:
        should_hold = True
        reason = "player_still_moving_to_clicked_waypoint"
    if not should_hold:
        return None
    locked = dict(observed)
    locked["verificationStatus"] = "WARN"
    locked["observedResult"] = "service_navigation_clicked_waiting" if action == "navigate_to_service" else "resource_return_clicked_waiting"
    locked["resultOutcome"] = "still_waiting"
    locked["resultComplete"] = False
    locked["nextActionAllowed"] = False
    signals = list(locked.get("observedSignals") or [])
    for signal in ("navigation_in_progress", "route_replan_suppressed_while_moving"):
        if signal not in signals:
            signals.append(signal)
    locked["observedSignals"] = signals
    lock = {
        "navigationInProgress": True,
        "replanSuppressedReason": reason,
        "movementState": movement_state,
        "elapsedTicks": elapsed_ticks,
        "minGameTicksBetweenClicks": min_ticks,
        "clickedWaypointTile": dict(target_tile) if isinstance(target_tile, dict) else None,
        "playerLocationAfter": dict(player_tile) if isinstance(player_tile, dict) else None,
        "distanceToClickedWaypoint": arrival_distance,
        "arrivalDistanceTiles": _nav_destination_arrival_distance(options),
        "routeObjectActionable": _status_route_object_actionable(status),
    }
    locked["navigationInProgress"] = lock
    warnings = list(locked.get("warnings") or [])
    warnings.append(f"navigation replan suppressed while movement is in progress: {reason}")
    locked["warnings"] = warnings
    return locked


def _observed_success_classification(observed: dict[str, Any] | None) -> str | None:
    observed = observed if isinstance(observed, dict) else {}
    result = str(observed.get("observedResult") or "")
    route_reconciled = str(observed.get("routeTransitionProgressClassification") or "")
    if route_reconciled in {"route_transition_reconciled_success", "return_transition_reconciled_success"}:
        return route_reconciled
    signals = {str(item) for item in (observed.get("observedSignals") or [])}
    if result in {"inventory_changed", "banking_complete"} or signals.intersection({"inventory_changed", "inventory_free_slots_changed", "held_resource_count_increased"}):
        return "inventory_changed_success"
    if result in {"resource_count_decreased", "resource_progress_increased"} or signals.intersection({"resource_count_decreased", "resource_progress_increased"}):
        return "resource_count_changed_success"
    if result in {"service_navigation_progress", "service_navigation_reached_node", "service_route_object_reacquired", "resource_return_progress", "resource_return_reached_node"}:
        return result
    if result == "route_transition_progress" or signals.intersection({"player_plane_changed", "player_position_changed", "route_step_changed", "service_ready"}):
        return "route_transition_progress"
    if str(observed.get("resultOutcome") or "") in {"progress", "depleted"}:
        return "animation_started_progress_pending"
    if result == "no_change_timeout":
        return "verification_timeout"
    return None


def _route_transition_reconciliation_classification(observed: dict[str, Any]) -> str:
    observed_result = str(observed.get("observedResult") or "")
    signals = {str(item) for item in (observed.get("observedSignals") or [])}
    if observed_result.startswith("return_transition_"):
        return "return_transition_reconciled_success"
    if "return_transition" in observed_result:
        return "return_transition_reconciled_success"
    if {"player_plane_changed", "player_position_changed", "route_step_changed"}.intersection(signals):
        return "route_transition_reconciled_success"
    return "route_transition_reconciled_success"


def _route_transition_is_return(result: ExecutionResult, observed: dict[str, Any] | None = None) -> bool:
    observed = observed if isinstance(observed, dict) else {}
    for value in (
        observed.get("observedResult"),
        observed.get("routeTransitionProgressClassification"),
    ):
        if str(value or "").startswith("return_transition_"):
            return True
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    if str(explanation.get("expectedPlaneChange") or "").strip() == "-1":
        return True
    route_id = str(explanation.get("routeId") or proposal.get("routeId") or "")
    return "return" in route_id.lower()


def _route_transition_click_confirmed(result: ExecutionResult) -> bool:
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    if str(hover.get("clickClassification") or "") == "clicked_expected_action":
        return True
    clicked = hover.get("lastMenuOptionClickedAfter")
    if not isinstance(clicked, dict):
        trace = result.action_trace if isinstance(result.action_trace, dict) else {}
        client_tick = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
        clicked = client_tick.get("lastMenuOptionClickedAfter")
    if not isinstance(clicked, dict):
        return False
    option = str(clicked.get("option") or "").strip().lower()
    target = str(clicked.get("target") or "").strip().lower()
    if option in {"", "walk here", "cancel"}:
        return False
    return any(token in option for token in ("climb", "open", "bank", "use", "deposit")) or any(
        token in target for token in ("stair", "ladder", "door", "gate", "bank", "booth")
    )


def _route_transition_classification_prefix(result: ExecutionResult, observed: dict[str, Any] | None = None) -> str:
    return "return_transition" if _route_transition_is_return(result, observed) else "route_transition"


def _route_node_from_status(status: dict[str, Any] | None) -> str | None:
    status = status if isinstance(status, dict) else {}
    for context_name in ("returnRouteContext", "serviceRouteContext"):
        context = _status_context(status, context_name)
        value = context.get("currentNodeId")
        if value not in (None, ""):
            return str(value)
    value = status.get("serviceRouteCurrentNodeId") or status.get("returnRouteCurrentNodeId")
    return str(value) if value not in (None, "") else None


def _route_id_from_status_or_result(status: dict[str, Any] | None, result: ExecutionResult | None = None) -> str | None:
    status = status if isinstance(status, dict) else {}
    for context_name in ("returnRouteContext", "serviceRouteContext"):
        context = _status_context(status, context_name)
        value = context.get("routeId") or context.get("returnRouteId")
        if value not in (None, ""):
            return str(value)
    if isinstance(result, ExecutionResult) and isinstance(result.proposal, dict):
        explanation = result.proposal.get("targetExplanation") if isinstance(result.proposal.get("targetExplanation"), dict) else {}
        value = explanation.get("routeId") or result.proposal.get("routeId")
        if value not in (None, ""):
            return str(value)
    return None


def _status_local_destination(status: dict[str, Any] | None) -> dict[str, Any] | None:
    status = status if isinstance(status, dict) else {}
    pathing = _status_context(status, "pathingContext")
    for key in ("localDestination", "currentLocalDestination", "destinationTile", "pathTargetTile", "nextWaypointTile"):
        value = pathing.get(key)
        if isinstance(value, dict):
            return dict(value)
    for key in ("localDestination", "currentLocalDestination", "destinationTile"):
        value = status.get(key)
        if isinstance(value, dict):
            return dict(value)
    return None


def _route_transition_ledger_entry(
    result: ExecutionResult,
    *,
    before_status: dict[str, Any] | None = None,
    after_status: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    retry_of_action_id: str | None = None,
    same_route_object_as_previous: bool | None = None,
) -> dict[str, Any]:
    observed = observed if isinstance(observed, dict) else {}
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    client_tick = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
    clicked_after = hover.get("lastMenuOptionClickedAfter") if isinstance(hover.get("lastMenuOptionClickedAfter"), dict) else client_tick.get("lastMenuOptionClickedAfter")
    clicked_before = hover.get("lastMenuOptionClickedBefore") if isinstance(hover.get("lastMenuOptionClickedBefore"), dict) else client_tick.get("lastMenuOptionClickedBefore")
    before_tile = _status_player_tile(before_status)
    after_tile = _status_player_tile(after_status)
    expected_actions = explanation.get("expectedOptions") if isinstance(explanation.get("expectedOptions"), list) else []
    world_location = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else None
    evidence = {
        "menuClickMatched": _route_transition_click_confirmed(result),
        "pathingStarted": "pathing_started" in set(observed.get("observedSignals") or []),
        "localDestinationChanged": "local_destination_changed" in set(observed.get("observedSignals") or []),
        "locationChanged": "player_position_changed" in set(observed.get("observedSignals") or []),
        "distanceToObjectDecreased": "distance_to_object_decreased" in set(observed.get("observedSignals") or []),
        "routeNodeAdvanced": "route_step_changed" in set(observed.get("observedSignals") or []),
        "planeChanged": "player_plane_changed" in set(observed.get("observedSignals") or []),
        "dialogueOpened": "route_transition_dialogue_opened" in set(observed.get("observedSignals") or []),
        "serviceStateAdvanced": "service_ready" in set(observed.get("observedSignals") or []),
    }
    return {
        "schema": "route_transition_action_ledger.v1",
        "actionId": result.action_id or trace.get("actionId"),
        "actionIntent": _route_transition_classification_prefix(result, observed) + "_action",
        "routeId": _route_id_from_status_or_result(before_status, result) or _route_id_from_status_or_result(after_status, result),
        "routeNodeBefore": _route_node_from_status(before_status),
        "routeNodeAfter": _route_node_from_status(after_status),
        "expectedAction": expected_actions[0] if expected_actions else None,
        "objectId": explanation.get("objectId") or explanation.get("id"),
        "objectHash": explanation.get("objectHash") or explanation.get("hash"),
        "objectName": explanation.get("name") or proposal.get("targetName") or result.proposed_action,
        "worldLocation": dict(world_location) if isinstance(world_location, dict) else None,
        "planeBefore": before_tile.get("plane") if isinstance(before_tile, dict) else None,
        "planeAfter": after_tile.get("plane") if isinstance(after_tile, dict) else None,
        "playerLocationBefore": dict(before_tile) if isinstance(before_tile, dict) else None,
        "playerLocationAfter": dict(after_tile) if isinstance(after_tile, dict) else None,
        "localDestinationBefore": _status_local_destination(before_status),
        "localDestinationAfter": _status_local_destination(after_status),
        "clickedMenuBefore": dict(clicked_before) if isinstance(clicked_before, dict) else None,
        "clickedMenuAfter": dict(clicked_after) if isinstance(clicked_after, dict) else None,
        "clickTimestamp": trace.get("clickTimestampWallMillis"),
        "clickGameTick": trace.get("gameTickBeforeAction"),
        "clickClientTick": client_tick.get("clientTickAtHover"),
        "verificationWindowTicks": observed.get("timeoutTicks"),
        "verificationWindowMs": observed.get("timeoutMillis"),
        "retryOfActionId": retry_of_action_id,
        "sameRouteObjectAsPrevious": same_route_object_as_previous,
        "evidence": evidence,
    }


def _route_transition_identity_from_result(result: ExecutionResult) -> tuple[Any, ...]:
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    explanation = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    world = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else {}
    return (
        explanation.get("objectId") or explanation.get("id"),
        explanation.get("objectHash") or explanation.get("hash"),
        explanation.get("name") or proposal.get("targetName"),
        world.get("worldX"),
        world.get("worldY"),
        world.get("plane"),
        explanation.get("routeId") or proposal.get("routeId"),
    )


def _same_route_transition_object(result: ExecutionResult, previous: dict[str, Any] | None) -> bool:
    previous = previous if isinstance(previous, dict) else {}
    previous_identity = previous.get("identity")
    if not isinstance(previous_identity, (list, tuple)):
        return False
    current = _route_transition_identity_from_result(result)
    # Match on route/object id or exact world location when either is available.
    if current[0] is not None and previous_identity[0] is not None and current[0] == previous_identity[0]:
        return True
    if current[3:6] == tuple(previous_identity[3:6]) and current[3] is not None and current[4] is not None:
        return True
    return False


def _attach_route_transition_ledger(
    result: ExecutionResult,
    *,
    before_status: dict[str, Any] | None,
    after_status: dict[str, Any] | None,
    observed: dict[str, Any],
    retry_of_action_id: str | None = None,
    same_route_object_as_previous: bool | None = None,
) -> dict[str, Any] | None:
    if result.proposed_action not in ROUTE_TRANSITION_ACTIONS and result.proposed_action != "interface_dialogue_choice":
        return None
    ledger = _route_transition_ledger_entry(
        result,
        before_status=before_status,
        after_status=after_status,
        observed=observed,
        retry_of_action_id=retry_of_action_id,
        same_route_object_as_previous=same_route_object_as_previous,
    )
    observed["routeTransitionLedgerEntry"] = ledger
    if retry_of_action_id:
        observed["retryOfActionId"] = retry_of_action_id
    if isinstance(result.action_trace, dict):
        result.action_trace["routeTransitionLedgerEntry"] = ledger
    return ledger


def _route_transition_retry_required_observation(result: ExecutionResult, observed: dict[str, Any]) -> dict[str, Any]:
    if not _route_transition_click_confirmed(result):
        return observed
    prefix = _route_transition_classification_prefix(result, observed)
    classification = f"{prefix}_retry_required"
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    retry = dict(observed)
    retry["previousObservedResult"] = observed.get("observedResult")
    retry["previousResultOutcome"] = observed.get("resultOutcome")
    retry["observedResult"] = classification
    retry["resultOutcome"] = "retry_required"
    retry["resultComplete"] = True
    retry["nextActionAllowed"] = True
    retry["verificationStatus"] = "WARN"
    retry["routeTransitionProgressClassification"] = classification
    retry["clickedMenuAfter"] = hover.get("lastMenuOptionClickedAfter")
    signals = list(retry.get("observedSignals") or [])
    if classification not in signals:
        signals.append(classification)
    retry["observedSignals"] = signals
    retry["warnings"] = list(retry.get("warnings") or []) + [
        "route transition click had no completion evidence before timeout; retry is required"
    ]
    return retry


def _annotate_delayed_reconciliation(result: ExecutionResult, observed: dict[str, Any], previous: dict[str, Any]) -> None:
    previous_observed_result = previous.get("previousObservedResult") or previous.get("observedResult")
    previous_result_outcome = previous.get("previousResultOutcome") or previous.get("resultOutcome")
    if result.proposed_action == "select_resource_target":
        observed["delayedProgressReconciliation"] = True
        if str(previous_result_outcome or "") == "no_change_timeout":
            observed["resourceProgressClassification"] = "resource_timeout_reconciled_success"
        elif str(observed.get("observedResult") or "") == "target_depleted":
            observed["resourceProgressClassification"] = "resource_target_depleted_success"
        else:
            observed["resourceProgressClassification"] = "resource_delayed_inventory_success"
    elif result.proposed_action in ROUTE_TRANSITION_ACTIONS or result.proposed_action == "interface_dialogue_choice":
        observed["delayedProgressReconciliation"] = True
        observed["routeTransitionProgressClassification"] = _route_transition_reconciliation_classification(observed)
    else:
        return
    if previous:
        observed["previousObservedResult"] = previous_observed_result
        observed["previousResultOutcome"] = previous_result_outcome


def _apply_reconciled_observation(result: ExecutionResult, observed: dict[str, Any], *, elapsed_ms: int) -> None:
    previous = _observed_from_result(result)
    previous_timed_out = str(previous.get("resultOutcome") or "") == "no_change_timeout"
    previous_lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    previous_timed_out = previous_timed_out or previous_lifecycle.get("currentState") == "timed_out"
    if previous_timed_out or result.proposed_action == "select_resource_target":
        _annotate_delayed_reconciliation(result, observed, previous)
    result.observed_result = observed
    result.verification_status = str(observed.get("verificationStatus") or result.verification_status or "UNKNOWN")
    if result.verification_status == "PASS":
        result.status = "PASS"
    classification = _observed_success_classification(observed)
    if classification:
        observed["actionResultClassification"] = classification
        _set_trace_final(result, classification)
    if isinstance(result.lifecycle_state, dict):
        lifecycle = dict(result.lifecycle_state)
        lifecycle["observedResult"] = observed
        lifecycle["observedSignals"] = list(observed.get("observedSignals") or [])
        lifecycle["resultComplete"] = bool(observed.get("resultComplete"))
        lifecycle["resultOutcome"] = observed.get("resultOutcome") or lifecycle.get("resultOutcome")
        lifecycle["elapsedMillis"] = elapsed_ms
        if observed.get("resultComplete") and observed.get("resultOutcome") in {"success", "progress", "depleted"}:
            lifecycle["currentState"] = "verified"
            lifecycle["reason"] = str(observed.get("resultOutcome") or observed.get("observedResult") or "expected_result_verified")
        result.lifecycle_state = lifecycle
    if isinstance(result.action_trace, dict):
        result.action_trace.setdefault("gameTickVerificationTimeline", []).append(
            {
                "elapsedMs": elapsed_ms,
                "verificationStatus": result.verification_status,
                "observedResult": result.observed_result,
                "reconciled": True,
            }
        )


def _update_result_classification_from_observed(result: ExecutionResult) -> None:
    observed = result.observed_result if isinstance(result.observed_result, dict) else {}
    classification = _observed_success_classification(observed)
    if not classification:
        return
    observed["actionResultClassification"] = classification
    result.observed_result = observed
    _set_trace_final(result, _trace_classification_from_observed(classification))


def _maybe_final_reconcile(
    *,
    daemon_url: str,
    options: Any,
    results: list[ExecutionResult],
    before_status: dict[str, Any] | None,
    loop_summary: dict[str, Any],
    fetch_json_func: Any,
    sleep_func: Any,
    monotonic_func: Any,
    timeout: float,
) -> None:
    reconcile_ms = _final_reconcile_ms(options)
    reconcile_ticks = _final_reconcile_game_ticks(options)
    if results and results[-1].proposed_action == "select_resource_target":
        reconcile_ms = max(reconcile_ms, _resource_reconcile_ms(options))
        reconcile_ticks = max(reconcile_ticks, _resource_reconcile_game_ticks(options))
    loop_summary["finalReconcileMillis"] = reconcile_ms
    loop_summary["finalReconcileGameTicks"] = reconcile_ticks
    if (reconcile_ms <= 0 and reconcile_ticks <= 0) or not results:
        return
    result = results[-1]
    if not result.executed:
        return
    if result.proposed_action in ROUTE_TRANSITION_ACTIONS:
        reconcile_ticks = max(reconcile_ticks, _nav_verify_game_ticks(options))
        loop_summary["finalReconcileGameTicks"] = reconcile_ticks
    observed = _observed_from_result(result)
    if observed.get("resultComplete") and observed.get("resultOutcome") in {"success", "progress", "depleted"}:
        return
    start = _safe_monotonic(monotonic_func)
    if start is None:
        start = 0.0
    tick_window_ms = reconcile_ticks * 700 if reconcile_ticks > 0 else 0
    wall_window_ms = max(reconcile_ms, tick_window_ms)
    deadline = start + wall_window_ms / 1000.0
    wait_started_tick = _status_tick(before_status)
    if wait_started_tick is None and isinstance(result.action_trace, dict):
        trace_tick = result.action_trace.get("gameTickBeforeAction")
        wait_started_tick = trace_tick if isinstance(trace_tick, int) else None
    result_proposal = result.proposal if isinstance(result.proposal, dict) else {}
    reconcile_proposal = None
    if result_proposal:
        reconcile_proposal = ActionProposal(
            proposed_action=str(result_proposal.get("proposedAction") or result.proposed_action or "none"),
            target_kind=str(result_proposal.get("targetKind") or "none"),
            target_tile=result_proposal.get("targetTile") if isinstance(result_proposal.get("targetTile"), dict) else None,
        )
    elapsed_ms = 0
    last_observed: dict[str, Any] | None = None
    while True:
        try:
            latest_status = _fetch_status_or_action_context(
                daemon_url,
                options,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
                purpose="final_reconcile",
            )
        except Exception:  # noqa: BLE001
            return
        sample_now = _safe_monotonic(monotonic_func)
        elapsed_ms = int(max(0.0, (sample_now if sample_now is not None else start) - start) * 1000)
        reconciled = verify_expected_result(
            result.proposed_action,
            before_status,
            latest_status,
            elapsed_ms=elapsed_ms,
            timeout_ms=reconcile_ms if reconcile_ticks <= 0 else None,
            wait_started_tick=wait_started_tick,
            timeout_ticks=reconcile_ticks if reconcile_ticks > 0 else None,
            progress_min_distance=_nav_progress_min_distance(options) if result.proposed_action in NAVIGATION_ACTIONS else None,
            proposal=reconcile_proposal,
        )
        last_observed = reconciled
        _record_loop_status(loop_summary, latest_status)
        loop_summary["finalReconcileElapsedTicks"] = reconciled.get("elapsedTicks")
        if reconciled.get("resultComplete") and reconciled.get("resultOutcome") in {"success", "progress", "depleted"}:
            _apply_reconciled_observation(result, reconciled, elapsed_ms=elapsed_ms)
            loop_summary["finalReconcileResult"] = reconciled.get("observedResult")
            _refresh_loop_summary(loop_summary, results)
            return
        now = _safe_monotonic(monotonic_func)
        elapsed_ticks = reconciled.get("elapsedTicks")
        ticks_elapsed = reconcile_ticks > 0 and isinstance(elapsed_ticks, int) and elapsed_ticks >= reconcile_ticks
        wall_elapsed = now is None or now >= deadline
        if ticks_elapsed or wall_elapsed:
            loop_summary["finalReconcileResult"] = reconciled.get("observedResult")
            if last_observed and isinstance(result.action_trace, dict):
                result.action_trace.setdefault("gameTickVerificationTimeline", []).append(
                    {
                        "elapsedMs": elapsed_ms,
                        "verificationStatus": last_observed.get("verificationStatus"),
                        "observedResult": last_observed,
                        "reconciled": False,
                    }
                )
            return
        sleep_func(min(_poll_interval_seconds(options), max(0.0, deadline - now)))


def _verify_action_after_execution(
    *,
    daemon_url: str,
    options: Any,
    action: str,
    proposal: ActionProposal | None,
    before_status: dict[str, Any],
    fetch_json_func: Any,
    sleep_func: Any,
    monotonic_func: Any,
    timeout: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int, list[dict[str, Any]]]:
    wait_ms = max(0, int(getattr(options, "after_action_wait_ms", 500) or 0))
    if wait_ms > 0:
        sleep_func(wait_ms / 1000.0)
    nav_action = action in NAVIGATION_ACTIONS
    path_to_interact_action = action in ROUTE_TRANSITION_ACTIONS
    timeout_ms = _action_timeout_millis_for_verification(action, options)
    timeout_ticks = _nav_verify_game_ticks(options) if (nav_action or path_to_interact_action) else 0
    progress_min_distance = _nav_progress_min_distance(options) if nav_action else None
    if not nav_action and not path_to_interact_action:
        try:
            after_status = _fetch_status_or_action_context(
                daemon_url,
                options,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
                purpose="post_action_verification",
            )
        except Exception:
            raise
        observed = verify_expected_result(
            action,
            before_status,
            after_status,
            elapsed_ms=wait_ms,
            timeout_ms=timeout_ms,
            wait_started_tick=_status_tick(before_status),
            progress_min_distance=progress_min_distance,
            proposal=proposal,
        )
        locked = _navigation_motion_lock_observation(
            action=action,
            proposal=proposal,
            status=after_status,
            observed=observed,
            options=options,
        )
        if locked is not None:
            observed = locked
        return after_status, observed, wait_ms, [
            {
                "elapsedMs": wait_ms,
                "verificationStatus": observed.get("verificationStatus"),
                "observedResult": observed,
            }
        ]

    start = _safe_monotonic(monotonic_func)
    if start is None:
        start = 0.0
    wait_started_tick = _status_tick(before_status)
    wall_window_ms = max(timeout_ms, timeout_ticks * 700)
    deadline = start + max(0, wall_window_ms - wait_ms) / 1000.0
    elapsed_ms = wait_ms
    timeline: list[dict[str, Any]] = []
    last_status: dict[str, Any] | None = None
    last_observed: dict[str, Any] | None = None
    while True:
        last_status = _fetch_status_or_action_context(
            daemon_url,
            options,
            fetch_json_func=fetch_json_func,
            timeout=timeout,
            purpose="post_action_navigation_verification",
        )
        now_value = _safe_monotonic(monotonic_func)
        elapsed_ms = wait_ms + int(max(0.0, (now_value if now_value is not None else start) - start) * 1000)
        observed = verify_expected_result(
            action,
            before_status,
            last_status,
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms if timeout_ms > 0 else None,
            wait_started_tick=wait_started_tick,
            timeout_ticks=timeout_ticks if timeout_ticks > 0 else None,
            progress_min_distance=progress_min_distance,
            proposal=proposal,
        )
        locked = _navigation_motion_lock_observation(
            action=action,
            proposal=proposal,
            status=last_status,
            observed=observed,
            options=options,
        )
        if locked is not None:
            observed = locked
        last_observed = observed
        timeline.append(
            {
                "elapsedMs": elapsed_ms,
                "verificationStatus": observed.get("verificationStatus"),
                "observedResult": observed,
            }
        )
        if observed.get("resultComplete") and observed.get("resultOutcome") in {"success", "progress", "depleted", "interrupted", "no_change_timeout"}:
            return last_status, observed, elapsed_ms, timeline
        now_value = _safe_monotonic(monotonic_func)
        elapsed_ticks = observed.get("elapsedTicks")
        ticks_elapsed = timeout_ticks > 0 and isinstance(elapsed_ticks, int) and elapsed_ticks >= timeout_ticks
        wall_elapsed = now_value is None or (wall_window_ms <= 0 or now_value >= deadline)
        if ticks_elapsed or wall_elapsed:
            return last_status, last_observed, elapsed_ms, timeline
        sleep_func(_poll_interval_seconds(options))


def _bounded_delay_ms(min_ms: int, max_ms: int, *, profile: str) -> int:
    lower = max(0, int(min_ms or 0))
    upper = max(lower, int(max_ms or lower))
    if upper <= 0:
        return 0
    if profile == "steady":
        return int(round((lower + upper) / 2.0))
    return int(round(random.uniform(lower, upper)))


def _pacing_delay_ms(options: Any, *, reason: str) -> int:
    profile = str(getattr(options, "pacing_profile", "instant_debug") or "instant_debug")
    if profile == "instant_debug":
        return 0
    min_ms = int(getattr(options, "target_switch_min_ms", 0) or 0)
    max_ms = int(getattr(options, "target_switch_max_ms", 0) or 0)
    if reason == "after_inventory_change":
        min_ms = max(min_ms, int(getattr(options, "post_resource_min_ms", 0) or 0))
        max_ms = max(max_ms, int(getattr(options, "post_resource_max_ms", 0) or 0))
    delay = _bounded_delay_ms(min_ms, max_ms, profile=profile)
    if profile == "natural":
        chance = max(0.0, min(1.0, float(getattr(options, "occasional_idle_chance", 0.0) or 0.0)))
        if chance > 0.0 and random.random() < chance:
            delay += _bounded_delay_ms(
                int(getattr(options, "occasional_idle_min_ms", 0) or 0),
                int(getattr(options, "occasional_idle_max_ms", 0) or 0),
                profile=profile,
            )
    return delay


def _record_pacing(summary: dict[str, Any], result: ExecutionResult, *, profile: str, delay_ms: int, reason: str) -> None:
    if delay_ms <= 0:
        return
    delays = summary.setdefault("pacingDelaysMillis", [])
    delays.append(delay_ms)
    summary["pacingProfile"] = profile
    summary["pacingDelayCount"] = len(delays)
    summary["pacingDelayMinMillis"] = min(delays)
    summary["pacingDelayAvgMillis"] = round(sum(delays) / len(delays), 3)
    summary["pacingDelayMaxMillis"] = max(delays)
    if isinstance(result.action_trace, dict):
        result.action_trace["pacing"] = {
            "pacingProfile": profile,
            "appliedDelayMs": delay_ms,
            "pacingReason": reason,
        }


def _apply_target_switch_pacing(
    options: Any,
    summary: dict[str, Any],
    result: ExecutionResult | None,
    sleep_func: Any,
    *,
    reason: str,
    input_controller: HumanInputController | None = None,
) -> None:
    if result is None or not result.executed:
        return
    profile = str(getattr(options, "pacing_profile", "instant_debug") or "instant_debug")
    delay_ms = _pacing_delay_ms(options, reason=reason)
    if delay_ms <= 0:
        return
    _record_pacing(summary, result, profile=profile, delay_ms=delay_ms, reason=reason)
    if input_controller is not None:
        input_controller.apply_fixed_delay(reason, delay_ms)
        if isinstance(result.action_trace, dict):
            _attach_human_input_trace(result, input_controller)
    else:
        sleep_func(delay_ms / 1000.0)


def _pacing_reason_from_observed(observed: dict[str, Any] | None) -> str:
    observed = observed if isinstance(observed, dict) else {}
    signals = {str(item) for item in (observed.get("observedSignals") or [])}
    if observed.get("observedResult") in {"inventory_changed", "resource_progress_increased", "resource_count_decreased"}:
        return "after_inventory_change"
    if signals.intersection({"inventory_changed", "inventory_free_slots_changed", "held_resource_count_increased", "resource_progress_increased"}):
        return "after_inventory_change"
    if observed.get("observedResult") == "target_depleted" or "target_depleted_recently" in signals:
        return "target_depleted"
    return "reacquire_target"


def _target_key_from_mapping(target: dict[str, Any] | None) -> str | None:
    target = target if isinstance(target, dict) else {}
    world = target.get("worldLocation") if isinstance(target.get("worldLocation"), dict) else target.get("world")
    world = world if isinstance(world, dict) else {}
    world_x = target.get("worldX", world.get("worldX"))
    world_y = target.get("worldY", world.get("worldY"))
    plane = target.get("plane", world.get("plane", 0))
    object_id = target.get("objectId", target.get("rawId", target.get("id")))
    class_id = target.get("classId") or target.get("targetClass")
    parts = [object_id, world_x, world_y, plane, class_id]
    if any(value is not None for value in parts):
        return ":".join(str(value) for value in parts)
    for key in ("targetKey", "objectKey", "candidateKey", "key", "markerId"):
        value = target.get(key)
        if value is not None:
            return str(value)
    return None


def _target_key_from_proposal(proposal: ActionProposal) -> str | None:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    if proposal.target_kind == "path_tile" and isinstance(proposal.target_tile, dict):
        target = {
            "id": _proposal_target_id(proposal),
            **proposal.target_tile,
            "classId": explanation.get("classId"),
        }
        key = _target_key_from_mapping(target)
        if key:
            return key
    key = _target_key_from_mapping(explanation)
    if key:
        return key
    target = {
        "id": _proposal_target_id(proposal),
        **(proposal.target_tile if isinstance(proposal.target_tile, dict) else {}),
        "classId": explanation.get("classId"),
    }
    return _target_key_from_mapping(target)


def _suppression_enabled(options: Any) -> bool:
    return int(getattr(options, "target_hover_failure_limit", 0) or 0) > 0 and int(getattr(options, "target_suppression_ms", 0) or 0) > 0


def _active_suppression_keys(cache: dict[str, dict[str, Any]], now_ms: int) -> set[str]:
    expired = [
        key
        for key, record in cache.items()
        if int(record.get("suppressionUntil", 0) or 0) > 0 and int(record.get("suppressionUntil", 0) or 0) <= now_ms
    ]
    for key in expired:
        cache.pop(key, None)
    return {key for key, record in cache.items() if int(record.get("suppressionUntil", 0) or 0) > now_ms}


def _status_with_suppressed_targets(status: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    if not keys:
        return status
    copied = dict(status)
    brain = dict(_dict(copied.get("brain")))
    values = sorted(keys)
    copied["suppressedResourceTargetKeys"] = values
    copied["suppressedActionTargetKeys"] = values
    copied["suppressedNavigationTargetKeys"] = values
    brain["suppressedResourceTargetKeys"] = values
    brain["suppressedActionTargetKeys"] = values
    brain["suppressedNavigationTargetKeys"] = values
    copied["brain"] = brain
    return copied


def _proposal_is_suppressed(proposal: ActionProposal, keys: set[str]) -> bool:
    key = _target_key_from_proposal(proposal)
    return bool(key and key in keys)


def _proposal_payload_reacquire_budget_type(proposal: dict[str, Any]) -> str:
    action = str(proposal.get("proposedAction") or "")
    target_kind = str(proposal.get("targetKind") or "")
    if action in ROUTE_TRANSITION_ACTIONS or action == "interface_dialogue_choice" or target_kind == "service_route_object":
        return "route_transition"
    if action == "select_resource_target":
        return "resource"
    if action == "resource_view_recovery":
        return "camera_recovery"
    if action == "open_service":
        return "service_object"
    if action in {"deposit_inventory", "deposit_resources", "close_bank"}:
        return "service_inventory"
    if action in NAVIGATION_ACTIONS or target_kind == "path_tile":
        return "navigation_waypoint"
    return "unknown"


def _proposal_reacquire_budget_type(proposal: ActionProposal) -> str:
    return _proposal_payload_reacquire_budget_type(proposal.to_dict())


def _proposal_has_actionable_safe_target(proposal: ActionProposal) -> bool:
    missing = {str(item) for item in (proposal.missing_capabilities or [])}
    if missing.intersection({"click_point", "screen_click_point", "canvas_hover_point", "safe_aimpoint"}):
        return False
    payload = proposal.to_dict()
    resolution = payload.get("clickPointResolution") if isinstance(payload.get("clickPointResolution"), dict) else {}
    if resolution and resolution.get("status") not in {None, "PASS"}:
        return False
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    safe = explanation.get("safeAimPoint") if isinstance(explanation.get("safeAimPoint"), dict) else {}
    if safe.get("actionable") is True or safe.get("status") == "PASS":
        return True
    return proposal.suggested_click_point is not None or proposal.executable


def _proposal_action_target_source(proposal: ActionProposal) -> str | None:
    if proposal.action_target_source:
        return str(proposal.action_target_source)
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    value = explanation.get("actionTargetSource") or explanation.get("targetSource") or explanation.get("source")
    return str(value) if value is not None else None


def _proposal_actionability(proposal: ActionProposal) -> str | None:
    if proposal.actionability:
        return str(proposal.actionability)
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    value = explanation.get("actionability")
    return str(value) if value is not None else None


def _proposal_is_stale_or_static(proposal: ActionProposal) -> bool:
    actionability = _proposal_actionability(proposal)
    source = _proposal_action_target_source(proposal)
    return (
        actionability in {"advisory_only", "stale", "blocked"}
        or str(actionability or "").startswith("blocked_")
        or source in {"static_route_prior", "route_context_goal"}
    )


def _proposal_fresh_executable_source(proposal: ActionProposal) -> str | None:
    if _proposal_is_stale_or_static(proposal) or not proposal.executable:
        return None
    source = _proposal_action_target_source(proposal)
    if source:
        return source
    if proposal.target_kind == "path_tile":
        return "live_projected_waypoint" if proposal.suggested_click_point else "local_frontier_waypoint"
    if proposal.target_kind == "service_route_object":
        return "live_route_object"
    if proposal.target_kind == "service":
        return "live_service_object"
    if proposal.target_kind == "resource":
        return "live_resource_candidate"
    return "unknown"


def _status_reacquire_scope_key(status: dict[str, Any]) -> tuple[Any, ...]:
    phase, intent = _status_phase_intent(status)
    tile = _status_player_tile(status)
    plane = tile.get("plane") if isinstance(tile, dict) else None
    service_route = _status_context(status, "serviceRouteContext")
    return_route = _status_context(status, "returnRouteContext")
    service_node = service_route.get("currentNodeId") or status.get("serviceRouteCurrentNodeId")
    return_node = return_route.get("currentNodeId") or status.get("returnRouteCurrentNodeId")
    return (phase, intent, plane, service_node, return_node)


def _maybe_reset_reacquire_budget_on_scope_change(
    cache: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    *,
    previous_scope: tuple[Any, ...] | None,
    current_scope: tuple[Any, ...] | None,
) -> tuple[Any, ...] | None:
    if previous_scope is not None and current_scope != previous_scope and cache:
        cleared = len(cache)
        cache.clear()
        summary["phaseScopedBudget"] = True
        summary["budgetResetReason"] = "reacquire_scope_changed"
        summary["reacquireBudgetResets"] = int(summary.get("reacquireBudgetResets") or 0) + 1
        summary["suppressionClearsOnScopeChange"] = int(summary.get("suppressionClearsOnScopeChange") or 0) + cleared
        summary["targetReacquireRounds"] = 0
        summary["reacquireRoundsByBudget"] = {}
        summary["reacquireAttemptsUsed"] = 0
    return current_scope


def _record_reacquire_budget_round(summary: dict[str, Any], budget_type: str, *, max_rounds: int) -> int:
    rounds = summary.setdefault("reacquireRoundsByBudget", {})
    if not isinstance(rounds, dict):
        rounds = {}
        summary["reacquireRoundsByBudget"] = rounds
    current = int(rounds.get(budget_type) or 0) + 1
    rounds[budget_type] = current
    summary["reacquireBudgetType"] = budget_type
    summary["reacquireAttemptsUsed"] = current
    summary["reacquireLimit"] = max(0, int(max_rounds or 0))
    summary["phaseScopedBudget"] = True
    return current


def _route_transition_suppression_override_allowed(
    proposal: ActionProposal,
    keys: set[str],
    summary: dict[str, Any],
    options: Any,
) -> bool:
    if _proposal_reacquire_budget_type(proposal) != "route_transition":
        return False
    if not _proposal_is_suppressed(proposal, keys):
        return False
    if not _proposal_has_actionable_safe_target(proposal):
        return False
    max_rounds = max(1, int(getattr(options, "max_candidate_reacquire_rounds", 0) or 1))
    return int(summary.get("routeTransitionSuppressionOverrides") or 0) < max_rounds


def _world_tile_key(tile: dict[str, Any] | None) -> tuple[int, int, int] | None:
    tile = tile if isinstance(tile, dict) else {}
    world_x = _int_or_none(tile.get("worldX") if tile.get("worldX") is not None else tile.get("x"))
    world_y = _int_or_none(tile.get("worldY") if tile.get("worldY") is not None else tile.get("y"))
    plane = _int_or_none(tile.get("plane"))
    if world_x is None or world_y is None:
        return None
    return (world_x, world_y, 0 if plane is None else plane)


def _navigation_progress_observed(result: ExecutionResult | None) -> bool:
    if result is None:
        return False
    observed = result.observed_result if isinstance(result.observed_result, dict) else {}
    outcome = str(observed.get("resultOutcome") or "")
    if outcome in {"progress", "success"}:
        return True
    signals = {str(signal) for signal in observed.get("observedSignals") or []}
    progress_signals = {
        "player_tile_changed",
        "player_position_changed",
        "destination_distance_decreased",
        "path_target_distance_decreased",
        "service_distance_decreased",
        "final_approach_distance_decreased",
        "resource_destination_distance_decreased",
        "clicked_waypoint_distance_decreased",
        "route_node_changed",
        "route_step_changed",
        "route_step_index_changed",
        "route_step_status_changed",
        "service_ready",
    }
    return bool(signals.intersection(progress_signals))


def _navigation_block_evidence(
    *,
    status: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    result: ExecutionResult | None = None,
) -> list[str]:
    evidence: list[str] = []
    status = status if isinstance(status, dict) else {}
    observed = observed if isinstance(observed, dict) else {}
    pathing = _status_context(status, "pathingContext")
    route = _status_context(status, "serviceRouteContext")
    for source_name, source in (("pathing", pathing), ("route", route), ("status", status)):
        if not isinstance(source, dict):
            continue
        for key in ("localReachability", "frontierStatus", "routeStepStatus", "approachQuality"):
            value = str(source.get(key) or "").strip().lower()
            if value in {
                "blocked",
                "path_blocked",
                "destination_blocked",
                "player_tile_blocked",
                "suspect_outside_wall",
                "invalid_no_side_access",
                "invalid_no_line_of_sight",
            }:
                evidence.append(f"{source_name}.{key}={value}")
        for key in ("wallLoopDetected", "wallHuggingDetected", "wallLoop", "blockedPathDetected", "pathBlocked"):
            if source.get(key) is True:
                evidence.append(f"{source_name}.{key}=true")
        reason = str(source.get("reason") or source.get("pathingReason") or "").strip().lower()
        if any(token in reason for token in ("blocked", "collision", "barrier", "wall_hug", "outside_wall")):
            evidence.append(f"{source_name}.reason={reason}")
        rejected = source.get("rejectedApproachTileReasons")
        if isinstance(rejected, dict) and rejected:
            evidence.append(f"{source_name}.rejectedApproachTileReasons")
    signals = {str(signal) for signal in observed.get("observedSignals") or []}
    for signal in sorted(signals):
        if signal in {"route_blocked", "route_wrong_node_or_barrier", "wall_loop_detected", "wall_hugging_detected"}:
            evidence.append(f"observedSignal={signal}")
    route_no_progress = observed.get("routeNoProgress") if isinstance(observed.get("routeNoProgress"), dict) else {}
    if route_no_progress.get("barrierEvidence") is True:
        evidence.append("observed.routeNoProgress.barrierEvidence=true")
    if isinstance(result, ExecutionResult) and isinstance(result.action_trace, dict):
        stability = result.action_trace.get("routeStability") if isinstance(result.action_trace.get("routeStability"), dict) else {}
        if stability.get("barrierDetected") is True:
            evidence.append("trace.routeStability.barrierDetected=true")
        for item in stability.get("blockEvidence") or []:
            evidence.append(f"trace.routeStability.{item}")
    return list(dict.fromkeys(evidence))


def _route_stability_issue(
    proposal: ActionProposal,
    recent_waypoints: list[tuple[int, int, int]],
    *,
    last_result: ExecutionResult | None = None,
    current_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if proposal.proposed_action not in NAVIGATION_ACTIONS or proposal.target_kind != "path_tile":
        return None
    key = _world_tile_key(proposal.target_tile)
    if key is None:
        return None
    if len(recent_waypoints) >= 2 and key == recent_waypoints[-2] and key != recent_waypoints[-1]:
        return {
            "classification": "route_oscillation_detected",
            "oscillationDetected": True,
            "backtrackingDetected": True,
            "reason": "proposed waypoint would repeat an A-B-A route cycle",
            "proposedWaypointTile": {"worldX": key[0], "worldY": key[1], "plane": key[2]},
            "recentClickedWaypointTiles": [
                {"worldX": item[0], "worldY": item[1], "plane": item[2]}
                for item in recent_waypoints[-4:]
            ],
        }
    if recent_waypoints and key == recent_waypoints[-1]:
        current_player_tile = _status_player_tile(current_status)
        distance_to_repeated_waypoint = _tile_distance(current_player_tile, proposal.target_tile)
        if distance_to_repeated_waypoint is not None and distance_to_repeated_waypoint <= 1:
            return {
                "classification": "route_waypoint_arrived_advance_required",
                "advanceRecommended": True,
                "repeatClickSuppressed": True,
                "barrierDetected": False,
                "noProgressDetected": False,
                "recoverySuppressed": True,
                "reason": "player is already inside the waypoint radius; route step should advance instead of clicking again",
                "distanceToWaypoint": distance_to_repeated_waypoint,
                "proposedWaypointTile": {"worldX": key[0], "worldY": key[1], "plane": key[2]},
                "playerWorldPosition": dict(current_player_tile) if isinstance(current_player_tile, dict) else None,
                "recentClickedWaypointTiles": [
                    {"worldX": item[0], "worldY": item[1], "plane": item[2]}
                    for item in recent_waypoints[-4:]
                ],
            }
        if _navigation_progress_observed(last_result):
            return None
        block_evidence = _navigation_block_evidence(status=current_status, result=last_result)
        if not block_evidence:
            return {
                "classification": "route_repeat_suppressed_no_block_evidence",
                "repeatClickSuppressed": True,
                "barrierDetected": False,
                "noProgressDetected": True,
                "recoverySuppressed": True,
                "reason": "proposed waypoint repeats the previous route click without obstacle evidence; wait or reacquire instead of wall-hug recovery",
                "proposedWaypointTile": {"worldX": key[0], "worldY": key[1], "plane": key[2]},
                "recentClickedWaypointTiles": [
                    {"worldX": item[0], "worldY": item[1], "plane": item[2]}
                    for item in recent_waypoints[-4:]
                ],
            }
        return {
            "classification": "route_wall_hugging_detected",
            "barrierDetected": True,
            "blockEvidence": block_evidence,
            "reason": "proposed waypoint repeats the previous route click",
            "proposedWaypointTile": {"worldX": key[0], "worldY": key[1], "plane": key[2]},
            "recentClickedWaypointTiles": [
                {"worldX": item[0], "worldY": item[1], "plane": item[2]}
                for item in recent_waypoints[-4:]
            ],
        }
    return None


def _transition_direction(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("_", "-")
    if "climb-up" in text or "climb up" in text or text in {"up", "go-up", "go up"}:
        return "up"
    if "climb-down" in text or "climb down" in text or text in {"down", "go-down", "go down"}:
        return "down"
    return None


def _opposite_transition_direction(a: str | None, b: str | None) -> bool:
    return {a, b} == {"up", "down"}


def _proposal_transition_option(proposal: ActionProposal) -> str | None:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    for key in ("expectedOptions", "actions"):
        values = explanation.get(key)
        if isinstance(values, list) and values:
            return str(values[0])
    return None


def _same_transition_location_without_plane(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    ax = _int_or_none(a.get("worldX") if a.get("worldX") is not None else a.get("x"))
    ay = _int_or_none(a.get("worldY") if a.get("worldY") is not None else a.get("y"))
    bx = _int_or_none(b.get("worldX") if b.get("worldX") is not None else b.get("x"))
    by = _int_or_none(b.get("worldY") if b.get("worldY") is not None else b.get("y"))
    return ax is not None and ay is not None and ax == bx and ay == by


def _route_transition_reverse_issue(
    proposal: ActionProposal,
    last_transition: dict[str, Any] | None,
    *,
    current_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if proposal.proposed_action not in ROUTE_TRANSITION_ACTIONS:
        return None
    previous = last_transition if isinstance(last_transition, dict) else {}
    if not previous:
        return None
    previous_direction = _transition_direction(
        previous.get("expectedAction") or _dict(previous.get("clickedMenuAfter")).get("option")
    )
    current_option = _proposal_transition_option(proposal)
    current_direction = _transition_direction(current_option)
    if not _opposite_transition_direction(previous_direction, current_direction):
        return None
    previous_before = _int_or_none(previous.get("planeBefore"))
    previous_after = _int_or_none(previous.get("planeAfter"))
    if previous_before is None or previous_after is None or previous_before == previous_after:
        return None
    player_tile = _status_player_tile(current_status)
    if isinstance(player_tile, dict) and _int_or_none(player_tile.get("plane")) != previous_after:
        return None
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    current_world = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else proposal.target_tile
    previous_world = previous.get("worldLocation") if isinstance(previous.get("worldLocation"), dict) else None
    current_name = str(explanation.get("name") or proposal.target_name or "").strip().lower()
    previous_name = str(previous.get("objectName") or "").strip().lower()
    current_route = explanation.get("routeId")
    previous_route = previous.get("routeId")
    same_location = _same_transition_location_without_plane(current_world, previous_world)
    same_named_route = bool(current_name and previous_name and current_name == previous_name and current_route and previous_route and current_route == previous_route)
    if not same_location and not same_named_route:
        return None
    return {
        "classification": "route_transition_reverse_oscillation_prevented",
        "oscillationDetected": True,
        "backtrackingDetected": True,
        "reason": "proposed route transition would immediately undo the previous plane change",
        "previousTransition": {
            "expectedAction": previous.get("expectedAction"),
            "objectName": previous.get("objectName"),
            "worldLocation": previous_world,
            "planeBefore": previous_before,
            "planeAfter": previous_after,
            "routeId": previous_route,
        },
        "proposedTransition": {
            "expectedAction": current_option,
            "objectName": explanation.get("name") or proposal.target_name,
            "worldLocation": dict(current_world) if isinstance(current_world, dict) else None,
            "routeId": current_route,
        },
        "playerWorldPosition": dict(player_tile) if isinstance(player_tile, dict) else None,
    }


def _route_transition_plane_mismatch_issue(
    proposal: ActionProposal,
    current_status: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if proposal.proposed_action not in ROUTE_TRANSITION_ACTIONS:
        return None
    player_tile = _status_player_tile(current_status)
    player_plane = _int_or_none(player_tile.get("plane")) if isinstance(player_tile, dict) else None
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    target_world = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else proposal.target_tile
    target_plane = _int_or_none(target_world.get("plane")) if isinstance(target_world, dict) else None
    if player_plane is None or target_plane is None or player_plane == target_plane:
        return None
    return {
        "classification": "route_transition_target_plane_mismatch",
        "staleRouteTargetDetected": True,
        "reason": "proposed route transition target is on a different plane than the current player",
        "proposedTransition": {
            "expectedAction": _proposal_transition_option(proposal),
            "objectName": explanation.get("name") or proposal.target_name,
            "objectId": _proposal_target_id(proposal),
            "worldLocation": dict(target_world),
            "routeId": explanation.get("routeId"),
            "routeStepIndex": explanation.get("routeStepIndex"),
        },
        "playerWorldPosition": dict(player_tile),
    }


def _executed_navigation_waypoint_key(proposal: ActionProposal, result: ExecutionResult) -> tuple[int, int, int] | None:
    if proposal.proposed_action not in NAVIGATION_ACTIONS:
        return None
    result_proposal = result.proposal if isinstance(result.proposal, dict) else {}
    result_tile = result_proposal.get("targetTile") if isinstance(result_proposal.get("targetTile"), dict) else None
    key = _world_tile_key(result_tile)
    if key is not None:
        return key
    return _world_tile_key(proposal.target_tile)


def _blocked_by_route_stability_result(
    proposal: ActionProposal,
    issue: dict[str, Any],
    *,
    options: Any,
) -> ExecutionResult:
    lifecycle = lifecycle_state_for_proposal(proposal)
    advance_recommended = issue.get("advanceRecommended") is True
    lifecycle.current_state = "waiting_for_context" if advance_recommended else "blocked"
    lifecycle.reason = str(issue.get("classification") or "route_stability_blocked")
    lifecycle.warnings = [str(issue.get("reason") or "route navigation stability guard refused this waypoint")]
    observed = {
        "observedResult": issue.get("classification") or "route_stability_blocked",
        "resultOutcome": "skipped",
        "resultComplete": True,
        "nextActionAllowed": advance_recommended,
        "verificationStatus": "SKIPPED",
        "skipReason": issue.get("classification") or "route_stability_blocked",
    }
    lifecycle.observed_result = observed
    result = ExecutionResult(
        status="WARN",
        proposed_action=proposal.proposed_action,
        dry_run=not bool(getattr(options, "execute", False)),
        action_id=proposal_action_id(proposal),
        backend_name=str(getattr(options, "backend", "unknown")),
        movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
        proposal=proposal.to_dict(),
        warnings=list(lifecycle.warnings),
        observed_result=observed,
        verification_status="SKIPPED",
        action_trace=_new_action_trace(proposal),
    )
    if isinstance(result.action_trace, dict):
        result.action_trace["routeStability"] = dict(issue)
        _set_trace_final(result, str(issue.get("classification") or "route_stability_blocked"))
    _apply_lifecycle(result, lifecycle)
    return result


def _mark_navigation_no_progress(
    result: ExecutionResult,
    proposal: ActionProposal,
    *,
    status: dict[str, Any] | None = None,
) -> bool:
    if result.proposed_action not in NAVIGATION_ACTIONS:
        return False
    observed = result.observed_result if isinstance(result.observed_result, dict) else {}
    observed_name = str(observed.get("observedResult") or "")
    outcome = str(observed.get("resultOutcome") or "")
    if observed_name not in {
        "service_navigation_no_progress",
        "resource_return_no_progress",
        "service_navigation_stuck",
        "resource_return_stuck",
    } and outcome != "no_change_timeout":
        return False
    block_evidence = _navigation_block_evidence(status=status, observed=observed, result=result)
    classification = "route_wrong_node_or_barrier" if block_evidence else "navigation_no_progress_no_block_evidence"
    signals = [str(signal) for signal in observed.get("observedSignals") or []]
    for signal in (["route_no_progress", "route_wrong_node_or_barrier"] if block_evidence else ["route_no_progress"]):
        if signal not in signals:
            signals.append(signal)
    observed["observedSignals"] = signals
    observed["routeNoProgress"] = {
        "classification": classification,
        "clickedWaypointTile": dict(proposal.target_tile) if isinstance(proposal.target_tile, dict) else None,
        "reason": observed_name or outcome or "navigation_no_progress",
        "barrierEvidence": bool(block_evidence),
        "blockEvidence": list(block_evidence),
    }
    warning = (
        "route navigation produced no movement/distance progress with obstacle evidence; suppressing further local replans around this waypoint"
        if block_evidence
        else "route navigation produced no movement/distance progress, but no obstacle evidence was present; waiting/reacquiring instead of wall-hug recovery"
    )
    warnings = list(observed.get("warnings") or [])
    if warning not in warnings:
        warnings.append(warning)
    observed["warnings"] = warnings
    result.observed_result = observed
    if warning not in result.warnings:
        result.warnings.append(warning)
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    route_stability = trace.setdefault("routeStability", {})
    route_stability.update(
        {
            "classification": classification,
            "barrierDetected": bool(block_evidence),
            "noProgressDetected": True,
            "clickedWaypointTile": dict(proposal.target_tile) if isinstance(proposal.target_tile, dict) else None,
            "observedResult": observed_name or outcome,
            "blockEvidence": list(block_evidence),
        }
    )
    result.action_trace = trace
    return True


def _hover_failure_category(result: ExecutionResult) -> str | None:
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    client_tick_trace = trace.get("clientTick") if isinstance(trace.get("clientTick"), dict) else {}
    if not result.executed:
        missing = {str(item) for item in (result.missing_capabilities or [])}
        lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
        reason = str(lifecycle.get("reason") or "")
        right_click_selection = hover.get("rightClickMenuSelection") if isinstance(hover.get("rightClickMenuSelection"), dict) else {}
        if (
            result.proposed_action in ROUTE_TRANSITION_ACTIONS
            and reason in {"right_click_menu_select_failed", "route_target_hover_not_confirmed"}
            and str(right_click_selection.get("reason") or "") == "menu_open_not_observed"
        ):
            return "route_target_hover_not_confirmed"
        if reason in FATAL_NO_CLICK_BLOCK_REASONS:
            if _resource_target_movement_safety_reacquirable(result, reason):
                return "unsafe_geometry"
            return None
        if missing.intersection({"click_point", "screen_click_point", "canvas_hover_point", "safe_aimpoint"}) or reason in {
            "click_point_unavailable",
            "screen_click_point_unavailable",
            "hover_canvas_point_unavailable",
        }:
            return "unsafe_geometry"
    if client_tick_trace.get("volatileHoverZone") is True and not result.executed:
        return "volatile_hover_zone"
    if not hover or hover.get("confirmed") is True or result.executed:
        return None
    reason = str(hover.get("reason") or "")
    latest_match = hover.get("latestMatch") if isinstance(hover.get("latestMatch"), dict) else {}
    latest_sample = hover.get("latestHoverMenu") if isinstance(hover.get("latestHoverMenu"), dict) else latest_match.get("sample")
    latest_class = client_tick_core.classify_menu_action(latest_sample if isinstance(latest_sample, dict) else {})
    if reason == "cancel_hover" or latest_match.get("reason") == "cancel_hover" or latest_class == "cancel_hover":
        return "cancel_hover"
    if reason == "top_option_rejected" or latest_class == "walk_here":
        return "walk_here_hover"
    if "stale" in reason or "fresh" in reason:
        return "stale_client_tick"
    if "position" in reason:
        return "position_mismatch"
    return "hover_mismatch"


def _resource_target_movement_safety_reacquirable(result: ExecutionResult, reason: str | None = None) -> bool:
    if result.executed or result.proposed_action != "select_resource_target":
        return False
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    resolved_reason = str(reason or lifecycle.get("reason") or "")
    return resolved_reason == "screen_click_point_outside_movement_safety_region"


def _no_click_safety_skip_observed(result: ExecutionResult) -> dict[str, Any] | None:
    if result.executed:
        return None
    missing = {str(item) for item in (result.missing_capabilities or [])}
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    reason = str(lifecycle.get("reason") or "")
    if reason in FATAL_NO_CLICK_BLOCK_REASONS:
        if _resource_target_movement_safety_reacquirable(result, reason):
            return {
                "observedResult": "no_click_safety_skip",
                "resultOutcome": "skipped",
                "resultComplete": True,
                "nextActionAllowed": True,
                "verificationStatus": "SKIPPED",
                "skipReason": "unsafe_geometry",
                "safetyReason": reason,
            }
        return {
            "observedResult": "no_click_safety_block",
            "resultOutcome": "blocked",
            "resultComplete": True,
            "nextActionAllowed": False,
            "verificationStatus": "FAIL",
            "skipReason": reason,
        }
    if missing.intersection({"click_point", "screen_click_point", "canvas_hover_point", "safe_aimpoint"}):
        skip_reason = "unsafe_geometry"
    elif reason in {"click_point_unavailable", "screen_click_point_unavailable", "hover_canvas_point_unavailable"}:
        skip_reason = "unsafe_geometry"
    elif "hover_menu.volatile" in missing or reason == "volatile_hover_zone":
        skip_reason = "volatile_hover_zone"
    else:
        return None
    return {
        "observedResult": "no_click_safety_skip",
        "resultOutcome": "skipped",
        "resultComplete": True,
        "nextActionAllowed": True,
        "verificationStatus": "SKIPPED",
        "skipReason": skip_reason,
    }


def _record_target_hover_failure(
    *,
    options: Any,
    cache: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    result: ExecutionResult,
    now_ms: int,
) -> dict[str, Any] | None:
    if not _suppression_enabled(options):
        return None
    category = _hover_failure_category(result)
    if category is None:
        return None
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    target = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    target_key = _target_key_from_mapping(target)
    if not target_key:
        return None
    budget_type = _proposal_payload_reacquire_budget_type(proposal)
    limit = max(1, int(getattr(options, "target_hover_failure_limit", 0) or 1))
    if category == "walk_here_hover" and budget_type == "resource":
        limit = 1
    if category == "route_target_hover_not_confirmed" and budget_type == "route_transition":
        limit = min(limit, 2)
    window_ms = max(1, int(getattr(options, "target_suppression_ms", 0) or 1))
    record = cache.setdefault(
        target_key,
        {
            "targetKey": target_key,
            "targetName": target.get("name") or proposal.get("targetName"),
            "worldLocation": target.get("worldLocation"),
            "hoverConfirmFailures": 0,
            "cancelHoverFailures": 0,
            "walkHereFailures": 0,
            "positionMismatchFailures": 0,
            "staleSampleFailures": 0,
            "volatileHoverFailures": 0,
            "unsafeGeometryFailures": 0,
            "lastFailureReason": None,
            "lastFailureTime": None,
            "suppressionUntil": 0,
            "reacquireBudgetType": budget_type,
        },
    )
    record["reacquireBudgetType"] = budget_type
    already_suppressed = int(record.get("suppressionUntil") or 0) > now_ms
    record["hoverConfirmFailures"] = int(record.get("hoverConfirmFailures") or 0) + 1
    if category == "cancel_hover":
        record["cancelHoverFailures"] = int(record.get("cancelHoverFailures") or 0) + 1
    elif category == "walk_here_hover":
        record["walkHereFailures"] = int(record.get("walkHereFailures") or 0) + 1
    elif category == "position_mismatch":
        record["positionMismatchFailures"] = int(record.get("positionMismatchFailures") or 0) + 1
    elif category == "stale_client_tick":
        record["staleSampleFailures"] = int(record.get("staleSampleFailures") or 0) + 1
    elif category == "volatile_hover_zone":
        record["volatileHoverFailures"] = int(record.get("volatileHoverFailures") or 0) + 1
    elif category == "unsafe_geometry":
        record["unsafeGeometryFailures"] = int(record.get("unsafeGeometryFailures") or 0) + 1
    elif category == "route_target_hover_not_confirmed":
        record["routeTargetHoverFailures"] = int(record.get("routeTargetHoverFailures") or 0) + 1
    record["lastFailureReason"] = "walk_here_hover_for_resource" if category == "walk_here_hover" and budget_type == "resource" else category
    record["lastFailureTime"] = now_ms
    attempted_point = _result_attempted_screen_point(result)
    if attempted_point is not None:
        attempted_points = record.setdefault("attemptedPoints", [])
        if not any(isinstance(item, dict) and item.get("x") == attempted_point.get("x") and item.get("y") == attempted_point.get("y") for item in attempted_points):
            attempted_points.append(dict(attempted_point))
    hover_menu = _hover_menu_summary_for_failure(result)
    if hover_menu is not None:
        observed_menus = record.setdefault("observedMenus", [])
        observed_menus.append(hover_menu)
        del observed_menus[:-5]
    suppressed = int(record.get("hoverConfirmFailures") or 0) >= limit
    if suppressed:
        record["suppressionUntil"] = now_ms + window_ms
        suppressed_targets = summary.setdefault("suppressedTargets", [])
        already_counted = any(isinstance(item, dict) and item.get("targetKey") == target_key for item in suppressed_targets)
        if not already_suppressed and not already_counted:
            summary["targetsSuppressed"] = int(summary.get("targetsSuppressed") or 0) + 1
            suppressed_targets.append(
                {
                    "targetKey": target_key,
                    "targetName": record.get("targetName"),
                    "worldLocation": record.get("worldLocation"),
                    "reason": record.get("lastFailureReason"),
                    "failureCount": record.get("hoverConfirmFailures"),
                    "suppressionUntil": record.get("suppressionUntil"),
                    "reacquireBudgetType": budget_type,
                    "attemptedPoints": list(record.get("attemptedPoints") or []),
                    "observedMenus": list(record.get("observedMenus") or []),
                }
            )
    event = {
        "targetKey": target_key,
        "reason": record.get("lastFailureReason"),
        "failureCount": record.get("hoverConfirmFailures"),
        "failureLimit": limit,
        "suppressed": bool(suppressed),
        "suppressionUntil": record.get("suppressionUntil") if suppressed else None,
        "reacquireBudgetType": budget_type,
        "attemptedPoints": list(record.get("attemptedPoints") or []),
        "observedMenus": list(record.get("observedMenus") or []),
    }
    if isinstance(result.action_trace, dict):
        result.action_trace["targetSuppression"] = event
    return event


def _result_attempted_screen_point(result: ExecutionResult) -> dict[str, int] | None:
    movement = result.movement_plan if isinstance(result.movement_plan, dict) else {}
    for key in ("clickPoint", "target"):
        point = movement.get(key)
        if isinstance(point, dict):
            x = _int_or_none(point.get("x"))
            y = _int_or_none(point.get("y"))
            if x is not None and y is not None:
                return {"x": x, "y": y}
    commands = result.commands if isinstance(result.commands, list) else []
    for command in commands:
        if not isinstance(command, dict):
            continue
        point = command.get("clickPoint")
        if isinstance(point, dict):
            x = _int_or_none(point.get("x"))
            y = _int_or_none(point.get("y"))
            if x is not None and y is not None:
                return {"x": x, "y": y}
    return None


def _hover_menu_summary_for_failure(result: ExecutionResult) -> dict[str, Any] | None:
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    selection = hover.get("rightClickMenuSelection") if isinstance(hover.get("rightClickMenuSelection"), dict) else {}
    sample = selection.get("menuOpenSample") if isinstance(selection.get("menuOpenSample"), dict) else hover.get("sample")
    if not isinstance(sample, dict):
        sample = _confirmation_hover_sample(hover)
    if not isinstance(sample, dict):
        return None
    entries = _menu_entries_display_order(sample)
    return {
        "sourceEvent": sample.get("sourceEvent") or sample.get("sampleSource"),
        "menuOpen": sample.get("menuOpen"),
        "topOption": sample.get("topOption"),
        "topTarget": sample.get("topTarget"),
        "topIdentifier": sample.get("topIdentifier"),
        "mouseCanvasX": sample.get("mouseCanvasX"),
        "mouseCanvasY": sample.get("mouseCanvasY"),
        "entries": [
            {
                "option": entry.get("option"),
                "target": entry.get("target"),
                "identifier": entry.get("identifier"),
                "entryIndex": entry.get("entryIndex"),
            }
            for entry in entries[:6]
        ],
    }


def _resource_no_progress_failure_category(result: ExecutionResult) -> str | None:
    if result.proposed_action != "select_resource_target" or not result.executed:
        return None
    observed = _observed_from_result(result)
    outcome = str(observed.get("resultOutcome") or "")
    classification = str(observed.get("resourceProgressClassification") or "")
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    lifecycle_reason = str(lifecycle.get("reason") or "")
    if (
        outcome == "no_change_timeout"
        or classification == "resource_timeout_no_progress"
        or lifecycle_reason == "resource_no_progress_target_reacquired"
    ):
        return "resource_no_progress"
    return None


def _record_target_no_progress_failure(
    *,
    options: Any,
    cache: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    result: ExecutionResult,
    now_ms: int,
) -> dict[str, Any] | None:
    if not _suppression_enabled(options):
        return None
    category = _resource_no_progress_failure_category(result)
    if category is None:
        return None
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    target = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    target_key = _target_key_from_mapping(target)
    if not target_key:
        return None
    limit = max(1, int(getattr(options, "target_hover_failure_limit", 0) or 1))
    window_ms = max(1, int(getattr(options, "target_suppression_ms", 0) or 1))
    record = cache.setdefault(
        target_key,
        {
            "targetKey": target_key,
            "targetName": target.get("name") or proposal.get("targetName"),
            "worldLocation": target.get("worldLocation"),
            "hoverConfirmFailures": 0,
            "resourceNoProgressFailures": 0,
            "lastFailureReason": None,
            "lastFailureTime": None,
            "suppressionUntil": 0,
        },
    )
    already_suppressed = int(record.get("suppressionUntil") or 0) > now_ms
    record["resourceNoProgressFailures"] = int(record.get("resourceNoProgressFailures") or 0) + 1
    record["lastFailureReason"] = category
    record["lastFailureTime"] = now_ms
    suppressed = int(record.get("resourceNoProgressFailures") or 0) >= limit
    if suppressed:
        record["suppressionUntil"] = now_ms + window_ms
        suppressed_targets = summary.setdefault("suppressedTargets", [])
        already_counted = any(isinstance(item, dict) and item.get("targetKey") == target_key for item in suppressed_targets)
        if not already_suppressed and not already_counted:
            summary["targetsSuppressed"] = int(summary.get("targetsSuppressed") or 0) + 1
            summary["targetNoProgressSuppressions"] = int(summary.get("targetNoProgressSuppressions") or 0) + 1
            suppressed_targets.append(
                {
                    "targetKey": target_key,
                    "targetName": record.get("targetName"),
                    "worldLocation": record.get("worldLocation"),
                    "reason": category,
                    "failureCount": record.get("resourceNoProgressFailures"),
                    "suppressionUntil": record.get("suppressionUntil"),
                }
            )
    event = {
        "targetKey": target_key,
        "reason": category,
        "failureCount": record.get("resourceNoProgressFailures"),
        "failureLimit": limit,
        "suppressed": bool(suppressed),
        "suppressionUntil": record.get("suppressionUntil") if suppressed else None,
    }
    if isinstance(result.action_trace, dict):
        result.action_trace["targetNoProgressSuppression"] = event
    return event


def _is_menu_flip_mismatch_result(result: ExecutionResult) -> bool:
    observed = _observed_from_result(result)
    if str(observed.get("actionResultClassification") or observed.get("observedResult") or "") == "menu_flip_mismatch":
        return True
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    return str(trace.get("finalClassification") or "") == "menu_flip_mismatch"


def _record_target_menu_flip_failure(
    *,
    options: Any,
    cache: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    result: ExecutionResult,
    now_ms: int,
) -> dict[str, Any] | None:
    if not _suppression_enabled(options):
        return None
    proposal = result.proposal if isinstance(result.proposal, dict) else {}
    target = proposal.get("targetExplanation") if isinstance(proposal.get("targetExplanation"), dict) else {}
    target_key = _target_key_from_mapping(target)
    if not target_key:
        return None
    window_ms = max(1, int(getattr(options, "target_suppression_ms", 0) or 1))
    budget_type = _proposal_payload_reacquire_budget_type(proposal)
    record = cache.setdefault(
        target_key,
        {
            "targetKey": target_key,
            "targetName": target.get("name") or proposal.get("targetName"),
            "worldLocation": target.get("worldLocation"),
            "hoverConfirmFailures": 0,
            "resourceNoProgressFailures": 0,
            "menuFlipFailures": 0,
            "lastFailureReason": None,
            "lastFailureTime": None,
            "suppressionUntil": 0,
            "reacquireBudgetType": budget_type,
        },
    )
    already_suppressed = int(record.get("suppressionUntil") or 0) > now_ms
    record["reacquireBudgetType"] = budget_type
    record["menuFlipFailures"] = int(record.get("menuFlipFailures") or 0) + 1
    record["lastFailureReason"] = "menu_flip_mismatch"
    record["lastFailureTime"] = now_ms
    record["suppressionUntil"] = now_ms + window_ms
    suppressed_targets = summary.setdefault("suppressedTargets", [])
    already_counted = any(isinstance(item, dict) and item.get("targetKey") == target_key for item in suppressed_targets)
    if not already_suppressed and not already_counted:
        summary["targetsSuppressed"] = int(summary.get("targetsSuppressed") or 0) + 1
        summary["targetMenuFlipSuppressions"] = int(summary.get("targetMenuFlipSuppressions") or 0) + 1
        suppressed_targets.append(
            {
                "targetKey": target_key,
                "targetName": record.get("targetName"),
                "worldLocation": record.get("worldLocation"),
                "reason": "menu_flip_mismatch",
                "failureCount": record.get("menuFlipFailures"),
                "suppressionUntil": record.get("suppressionUntil"),
                "reacquireBudgetType": budget_type,
            }
        )
    event = {
        "targetKey": target_key,
        "reason": "menu_flip_mismatch",
        "failureCount": record.get("menuFlipFailures"),
        "failureLimit": 1,
        "suppressed": True,
        "suppressionUntil": record.get("suppressionUntil"),
        "reacquireBudgetType": budget_type,
    }
    if isinstance(result.action_trace, dict):
        result.action_trace["targetMenuFlipSuppression"] = event
    return event


def _clear_suppression_on_progress_if_needed(
    options: Any,
    cache: dict[str, dict[str, Any]],
    summary: dict[str, Any],
    observed: dict[str, Any] | None,
) -> None:
    if not cache or not bool(getattr(options, "clear_suppression_on_progress", True)):
        return
    if _observed_success_classification(observed):
        cleared = len(cache)
        cache.clear()
        summary["suppressionClearsOnProgress"] = int(summary.get("suppressionClearsOnProgress") or 0) + cleared


def _record_reacquire_wait(options: Any, summary: dict[str, Any], sleep_func: Any, *, reason: str) -> None:
    wait_ms = max(0, int(getattr(options, "suppressed_target_wait_ms", 0) or 0))
    if reason == "no_safe_target":
        wait_ms = max(wait_ms, int(getattr(options, "no_safe_target_wait_ms", 0) or 0))
    summary["targetReacquireWaits"] = int(summary.get("targetReacquireWaits") or 0) + 1
    summary["targetReacquireWaitMillis"] = int(summary.get("targetReacquireWaitMillis") or 0) + wait_ms
    if wait_ms > 0:
        sleep_func(wait_ms / 1000.0)


def _loop_stop_reason(options: Any, summary: dict[str, Any]) -> str | None:
    lifecycle_limit = _optional_positive_int(options, "stop_after_lifecycle_cycles")
    if lifecycle_limit is not None and int(summary.get("lifecycleCyclesCompleted") or 0) >= lifecycle_limit:
        return "lifecycle_cycle_limit_reached"
    service_limit = _optional_positive_int(options, "stop_after_service_cycles")
    if service_limit is not None and int(summary.get("serviceCompleteEvents") or 0) >= service_limit:
        return "service_cycle_limit_reached"
    post_service_logs_limit = _optional_positive_int(options, "stop_after_post_service_logs")
    if post_service_logs_limit is not None and int(summary.get("postServiceLogsCollected") or 0) >= post_service_logs_limit:
        return "post_service_log_limit_reached"
    consecutive_no_progress_limit = _optional_positive_int(options, "max_consecutive_no_progress")
    if consecutive_no_progress_limit is not None and int(summary.get("consecutiveNoProgress") or 0) >= consecutive_no_progress_limit:
        return "max_consecutive_no_progress_reached"
    consecutive_timeout_limit = _optional_positive_int(options, "max_consecutive_timeouts")
    if consecutive_timeout_limit is not None and int(summary.get("consecutiveTimeouts") or 0) >= consecutive_timeout_limit:
        return "max_consecutive_timeouts_reached"
    if bool(getattr(options, "stop_when_inventory_full", False)) and summary.get("inventoryFullEnd") is True:
        return "inventory_full"
    inventory_limit = _optional_positive_int(options, "stop_after_inventory_changes")
    if inventory_limit is not None and int(summary.get("inventoryChanges") or 0) >= inventory_limit:
        return "inventory_change_limit_reached"
    success_limit = _optional_positive_int(options, "max_successful_actions")
    if success_limit is not None and int(summary.get("successfulActions") or 0) >= success_limit:
        return "successful_action_limit_reached"
    timeout_limit = _optional_positive_int(options, "max_timeouts")
    if timeout_limit is not None and int(summary.get("timeouts") or 0) >= timeout_limit:
        return "max_timeouts_reached"
    return None


GOAL_REACHED_STOP_REASONS = {
    "lifecycle_cycle_limit_reached",
    "service_cycle_limit_reached",
    "post_service_log_limit_reached",
    "inventory_full",
    "inventory_change_limit_reached",
    "successful_action_limit_reached",
}


def _recoverable_failure_after_goal(result: ExecutionResult) -> bool:
    if result.status != "FAIL":
        return False
    observed = _observed_from_result(result)
    observed_result = str(observed.get("observedResult") or "")
    outcome = str(observed.get("resultOutcome") or "")
    classification = str(observed.get("resourceProgressClassification") or "")
    lifecycle = result.lifecycle_state if isinstance(result.lifecycle_state, dict) else {}
    lifecycle_reason = str(lifecycle.get("reason") or "")
    if observed_result == "no_click_safety_skip" and outcome == "skipped":
        return True
    if result.proposed_action == "select_resource_target":
        return (
            outcome == "no_change_timeout"
            or classification == "resource_timeout_no_progress"
            or lifecycle_reason == "resource_no_progress_target_reacquired"
        )
    return False


def _goal_reached_with_only_recoverable_failures(reason: str, results: list[ExecutionResult], summary: dict[str, Any]) -> int:
    if reason not in GOAL_REACHED_STOP_REASONS:
        return 0
    if not (
        int(summary.get("inventoryChanges") or 0) > 0
        or int(summary.get("resourceProgressSuccesses") or 0) > 0
        or int(summary.get("postServiceLogsCollected") or 0) > 0
        or int(summary.get("lifecycleCyclesCompleted") or 0) > 0
        or summary.get("inventoryFullEnd") is True
    ):
        return 0
    failures = [result for result in results if result.status == "FAIL"]
    if not failures:
        return 0
    if all(_recoverable_failure_after_goal(result) for result in failures):
        return len(failures)
    return 0


def _resource_timeout_wait_extension_allowed(options: Any, summary: dict[str, Any]) -> bool:
    timeout_limit = _optional_positive_int(options, "max_consecutive_timeouts")
    if timeout_limit is None:
        return False
    return int(summary.get("consecutiveTimeouts") or 0) < timeout_limit


def _service_object_timeout_wait_extension_allowed(result: ExecutionResult | None) -> bool:
    if result is None or result.proposed_action != "open_service" or not result.executed:
        return False
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    observed = _observed_from_result(result)
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    classifications = {
        str(hover.get("clickClassification") or ""),
        str(observed.get("menuClickClassification") or ""),
        str(observed.get("actionResultClassification") or ""),
        str(trace.get("clickClassification") or ""),
        str(trace.get("finalClassification") or ""),
    }
    return bool(classifications.intersection({"clicked_expected_action", "clicked_open_service"}))


def _service_object_timeout_pending_observation(observed: dict[str, Any]) -> dict[str, Any]:
    pending = dict(observed)
    pending["serviceObjectTimeoutExtendedWait"] = True
    pending["verificationStatus"] = "WARN"
    pending["observedResult"] = "service_object_click_confirmed_waiting"
    pending["resultOutcome"] = "still_waiting"
    pending["resultComplete"] = False
    pending["nextActionAllowed"] = False
    pending["previousObservedResult"] = observed.get("observedResult")
    pending["previousResultOutcome"] = observed.get("resultOutcome")
    pending["warnings"] = list(pending.get("warnings") or []) + [
        "service object click timed out while the expected Bank action was confirmed; continuing bounded observation"
    ]
    return pending


def _status_from_lifecycle(lifecycle: ActionLifecycleState) -> str:
    if lifecycle.current_state == "timed_out":
        return "FAIL"
    if lifecycle.current_state in {"blocked", "waiting_for_result"}:
        return "WARN"
    return "PASS"


def _readiness_missing_capabilities(readiness: dict[str, Any]) -> list[str]:
    action_readiness = readiness.get("actionReadiness")
    if isinstance(action_readiness, dict) and isinstance(action_readiness.get("missingCapabilities"), list):
        values = [str(item) for item in action_readiness.get("missingCapabilities") or []]
        if values:
            return values
    missing = readiness.get("missingCapabilities")
    if not isinstance(missing, list):
        return ["live_readiness"]
    values = [str(item) for item in missing]
    return values or ["live_readiness"]


def _readiness_warnings(readiness: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    action_readiness = readiness.get("actionReadiness")
    if isinstance(action_readiness, dict):
        action_blockers = action_readiness.get("blockers")
        if isinstance(action_blockers, list):
            for blocker in action_blockers:
                if isinstance(blocker, dict):
                    code = blocker.get("code") or "action_readiness_blocker"
                    message = blocker.get("message") or "action readiness failed"
                    messages.append(f"action readiness failed: {code}: {message}")
                else:
                    messages.append(f"action readiness failed: {blocker}")
        action_warnings = action_readiness.get("warnings")
        if isinstance(action_warnings, list):
            messages.extend(str(item) for item in action_warnings)
    blockers = readiness.get("blockers")
    if isinstance(blockers, list):
        for blocker in blockers:
            if isinstance(blocker, dict):
                code = blocker.get("code") or "readiness_blocker"
                message = blocker.get("message") or "pre-action readiness failed"
                messages.append(f"pre-action readiness failed: {code}: {message}")
            else:
                messages.append(f"pre-action readiness failed: {blocker}")
    warnings = readiness.get("warnings")
    if isinstance(warnings, list):
        messages.extend(str(item) for item in warnings)
    return messages or ["pre-action readiness failed"]


def _readiness_allows_execution(readiness: dict[str, Any]) -> bool:
    action_readiness = readiness.get("actionReadiness")
    if isinstance(action_readiness, dict) and action_readiness.get("executionAllowed") is not None:
        return bool(action_readiness.get("executionAllowed"))
    return readiness.get("readinessPassed") is True


def _status_with_navigation_option_overrides(status: dict[str, Any], options: Any | None) -> dict[str, Any]:
    if options is None or not isinstance(status, dict):
        return status
    keys = {
        "route_waypoint_lookahead_tiles": "routeWaypointLookaheadTiles",
        "route_waypoint_max_horizon_tiles": "routeWaypointMaxHorizonTiles",
        "min_route_progress_tiles": "minRouteProgressTiles",
        "max_route_waypoint_distance": "maxRouteWaypointDistance",
        "prefer_long_visible_waypoint": "preferLongVisibleWaypoint",
        "route_waypoint_distance_mode": "routeWaypointDistanceMode",
    }
    overrides: dict[str, Any] = {}
    for option_name, payload_name in keys.items():
        if hasattr(options, option_name):
            overrides[payload_name] = getattr(options, option_name)
    if not overrides:
        return status
    updated = dict(status)
    brain = dict(updated.get("brain")) if isinstance(updated.get("brain"), dict) else {}
    pathing = dict(brain.get("pathingContext")) if isinstance(brain.get("pathingContext"), dict) else dict(updated.get("pathingContext") or {})
    pathing.update({key: value for key, value in overrides.items() if value is not None})
    if brain:
        brain["pathingContext"] = pathing
        updated["brain"] = brain
    else:
        updated["pathingContext"] = pathing
    return updated


def _blocked_by_readiness_result(
    proposal: ActionProposal,
    *,
    readiness: dict[str, Any],
    options: Any,
) -> ExecutionResult:
    lifecycle = lifecycle_state_for_proposal(proposal)
    lifecycle.current_state = "blocked"
    lifecycle.reason = "pre_action_readiness_failed"
    lifecycle.warnings = _readiness_warnings(readiness)
    result = ExecutionResult(
        status="FAIL",
        proposed_action=proposal.proposed_action,
        dry_run=not bool(getattr(options, "execute", False)),
        action_id=proposal_action_id(proposal),
        backend_name=str(getattr(options, "backend", "unknown")),
        movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
        proposal=proposal.to_dict(),
        click_point_resolution=proposal.click_point_resolution,
        readiness=readiness,
        warnings=_readiness_warnings(readiness),
        missing_capabilities=_readiness_missing_capabilities(readiness),
        expected_result=expected_result_for_action(proposal.proposed_action),
        verification_status="FAIL",
    )
    _apply_lifecycle(result, lifecycle)
    return result


def _blocked_by_liveness_recovery_result(options: Any, backend: Any, recovery: dict[str, Any]) -> ExecutionResult:
    reason = str(recovery.get("blocker") or recovery.get("status") or "liveness_recovery_failed")
    result = ExecutionResult(
        status="FAIL",
        proposed_action="none",
        dry_run=not bool(getattr(options, "execute", False)),
        backend_name=str(getattr(options, "backend", getattr(backend, "name", "unknown"))),
        movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
        warnings=[f"liveness recovery did not produce a loaded scene: {reason}"],
        missing_capabilities=["loaded_scene"],
        verification_status="FAIL",
        readiness={
            "schema": "live_readiness.v2",
            "status": "FAIL",
            "livenessRecoveryLastResult": recovery,
            "livenessRecoveryRecommended": True,
            "actionReadiness": {
                "status": "FAIL",
                "executionAllowed": False,
                "intent": "unknown",
                "blockers": [{"code": reason, "message": "loaded scene recovery failed before action execution"}],
                "warnings": [],
            },
        },
    )
    lifecycle = ActionLifecycleState(current_state="blocked", reason=reason, warnings=result.warnings)
    _apply_lifecycle(result, lifecycle)
    return result


def _blocked_by_no_executable_result(
    proposal: ActionProposal,
    *,
    status: dict[str, Any],
    options: Any,
    reason: str | None = None,
) -> ExecutionResult:
    blocker = str(reason or proposal.reason or "no_executable_action")
    observed = {
        "schema": "action_observation.v1",
        "observedResult": blocker,
        "resultOutcome": "blocked",
        "resultComplete": False,
        "nextActionAllowed": False,
        "verificationStatus": "BLOCKED",
        "sourceTick": proposal.source_tick or status.get("latestTick"),
    }
    lifecycle = ActionLifecycleState(
        current_state="blocked",
        last_action=proposal.proposed_action,
        last_action_tick=proposal.source_tick or status.get("latestTick"),
        expected_result=expected_result_for_action(proposal.proposed_action),
        observed_result=observed,
        attempts=1,
        max_attempts=max(1, int(getattr(options, "max_actions", 1) or 1)),
        reason=blocker,
        warnings=list(proposal.warnings),
    )
    result = ExecutionResult(
        status="FAIL",
        proposed_action=proposal.proposed_action,
        dry_run=not bool(getattr(options, "execute", False)),
        action_id=proposal_action_id(proposal),
        backend_name=str(getattr(options, "backend", "unknown")),
        movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
        proposal=proposal.to_dict(),
        click_point_resolution=proposal.click_point_resolution,
        warnings=list(proposal.warnings),
        missing_capabilities=list(proposal.missing_capabilities),
        expected_result=lifecycle.expected_result,
        observed_result=observed,
        verification_status="BLOCKED",
    )
    result.action_trace = _new_action_trace(proposal)
    result.action_trace["finalClassification"] = blocker
    _apply_lifecycle(result, lifecycle)
    return result


def _proposal_specific_blocker(proposal: ActionProposal, default: str = "no_executable_action") -> str:
    target_explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    fallback = target_explanation.get("contextActionFallback")
    if isinstance(fallback, dict):
        original = str(fallback.get("originalReason") or "").strip()
        if original and original not in {"none", "wait_for_context", "no_executable_action"}:
            return original
    blocker = str(target_explanation.get("blocker") or "").strip()
    if blocker:
        return blocker
    reason = str(proposal.reason or "").strip()
    return reason or default


def _readiness_gate_required(options: Any, proposal: ActionProposal) -> bool:
    if getattr(options, "require_live_readiness", None) is False:
        return False
    has_readiness_option = hasattr(options, "wait_for_ready") or hasattr(options, "require_live_readiness")
    return has_readiness_option and (bool(getattr(options, "execute", False)) or bool(getattr(options, "hover_only", False))) and proposal.executable


def _maybe_auto_recover_loaded_scene(
    daemon_url: str,
    options: Any,
    *,
    status: dict[str, Any],
    fetch_json_func=fetch_json,
    timeout: float = 3.0,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not bool(getattr(options, "auto_recover_loaded_scene", False)):
        return status, None
    try:
        import liveness_recovery_core

        hint = liveness_recovery_core.liveness_hint_from_daemon_status(status)
    except Exception:  # noqa: BLE001
        return status, None
    if not hint.get("livenessRecoveryRecommended"):
        return status, None
    recovery = liveness_recovery_core.ensure_loaded_scene(
        daemon_url=daemon_url,
        snapshot_url=str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
        backend="arduino",
        arduino_port=getattr(options, "arduino_port", None),
        max_total_ms=int(max(1.0, float(getattr(options, "liveness_max_total_seconds", 120.0) or 120.0)) * 1000.0),
        max_attempts_per_state=max(1, int(getattr(options, "liveness_max_attempts_per_state", 2) or 2)),
        allow_jagex_launcher=bool(getattr(options, "allow_jagex_launcher_automation", False)),
        allow_credentials=False,
    )
    if recovery.get("status") in {"loaded_scene_ready", "recovered_loaded_scene"}:
        try:
            refreshed = _fetch_status_or_action_context(
                daemon_url,
                options,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
                purpose="post_liveness_recovery",
            )
        except Exception as error:  # noqa: BLE001
            failed = dict(recovery)
            failed["status"] = "daemon_rebind_failed"
            failed["blocker"] = f"daemon status unavailable after liveness recovery: {type(error).__name__}: {error}"
            failed["warnings"] = [
                *list(failed.get("warnings") or []),
                str(failed["blocker"]),
            ]
            return status, failed
        refreshed["livenessRecoveryLastResult"] = recovery
        return refreshed, recovery
    return status, recovery


def _wait_until_ready(
    daemon_url: str,
    options: Any,
    *,
    status: dict[str, Any],
    proposal: ActionProposal,
    fetch_json_func=fetch_json,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> tuple[dict[str, Any], ActionProposal, dict[str, Any]]:
    wait_seconds = _wait_for_ready_seconds(options)
    timeout = float(getattr(options, "timeout", 3.0))
    started_value = _safe_monotonic(monotonic_func)
    started = float(started_value if started_value is not None else 0.0)
    current_status = status
    current_proposal = proposal
    last_readiness: dict[str, Any] = {}
    while True:
        readiness_status = _status_with_navigation_option_overrides(current_status, options)
        last_readiness = build_readiness_report(
            daemon_url=daemon_url,
            timeout=timeout,
            daemon_status=readiness_status,
            sessions_dir=getattr(options, "sessions_dir", None),
            proposed_action=current_proposal.proposed_action,
        )
        if _readiness_allows_execution(last_readiness):
            return current_status, current_proposal, last_readiness
        if wait_seconds <= 0.0 or float(monotonic_func()) - started >= wait_seconds:
            return current_status, current_proposal, last_readiness
        sleep_func(_poll_interval_seconds(options))
        try:
            current_status = _fetch_status_or_action_context(
                daemon_url,
                options,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
                purpose="readiness_wait",
            )
            current_proposal = build_action_proposal(_status_with_navigation_option_overrides(current_status, options))
            current_status, current_proposal, _fallback = _maybe_context_action_proposal(
                daemon_url,
                options,
                current_status,
                current_proposal,
                timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001
            last_readiness = {
                "schema": "live_readiness.v2",
                "status": "FAIL",
                "proposedAction": current_proposal.proposed_action,
                "currentIntent": _action_intent_type(current_proposal),
                "actionReadiness": {
                    "status": "FAIL",
                    "executionAllowed": False,
                    "intent": _action_intent_type(current_proposal),
                    "blockers": [
                        {
                            "code": "daemon_status_unavailable",
                            "message": f"daemon status unavailable while waiting for readiness: {type(error).__name__}: {error}",
                        }
                    ],
                    "warnings": [],
                    "missingCapabilities": ["daemon.status"],
                    "checks": {"daemonReachable": False},
                    "checksSkippedAsNotApplicable": [],
                },
                "readinessPassed": False,
                "blockers": [
                    {
                        "code": "daemon_status_unavailable",
                        "message": f"daemon status unavailable while waiting for readiness: {type(error).__name__}: {error}",
                    }
                ],
                "warnings": [],
                "missingCapabilities": ["daemon.status"],
            }
            if wait_seconds <= 0.0 or float(monotonic_func()) - started >= wait_seconds:
                return current_status, current_proposal, last_readiness


def _apply_lifecycle(
    result: ExecutionResult,
    lifecycle: ActionLifecycleState,
    *,
    cooldown_remaining_ms: int = 0,
) -> None:
    result.lifecycle_state = lifecycle.to_dict()
    result.expected_result = lifecycle.expected_result
    result.observed_result = lifecycle.observed_result
    result.verification_status = (
        str(lifecycle.observed_result.get("verificationStatus"))
        if isinstance(lifecycle.observed_result, dict) and lifecycle.observed_result.get("verificationStatus") is not None
        else None
    )
    result.next_allowed_at = lifecycle.cooldown_until_utc
    result.cooldown_remaining_ms = max(0, int(cooldown_remaining_ms))
    bucket = _classify_click_failure_bucket(result, lifecycle)
    if bucket:
        if isinstance(result.observed_result, dict):
            result.observed_result["clickFailureBucket"] = bucket
        if isinstance(result.action_trace, dict):
            result.action_trace["clickFailureBucket"] = bucket


def _jsonish_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str).lower()
    except Exception:  # noqa: BLE001
        return str(value).lower()


def _resolution_failure_bucket(resolution: dict[str, Any] | None) -> str | None:
    resolution = resolution if isinstance(resolution, dict) else {}
    bucket = str(resolution.get("clickFailureBucket") or "")
    if bucket in CLICK_FAILURE_BUCKETS:
        return bucket
    if str(resolution.get("status") or "").upper() == "FAIL":
        return "coordinate_transform_error"
    text = _jsonish_text([resolution.get("warnings"), resolution.get("missingCapabilities"), resolution.get("method")])
    if any(
        token in text
        for token in (
            "canvas coordinate conversion failed",
            "coordinate validation failed",
            "resolved screen click point outside",
            "screen_click_point_outside_movement_safety_region",
        )
    ):
        return "coordinate_transform_error"
    return None


def _classify_click_failure_bucket(result: ExecutionResult, lifecycle: ActionLifecycleState) -> str | None:
    coordinate_bucket = _resolution_failure_bucket(result.click_point_resolution)
    if coordinate_bucket:
        return coordinate_bucket
    observed = lifecycle.observed_result if isinstance(lifecycle.observed_result, dict) else result.observed_result
    trace = result.action_trace if isinstance(result.action_trace, dict) else {}
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    final_classification = str(trace.get("finalClassification") or "").lower()
    observed_result = str((observed or {}).get("observedResult") if isinstance(observed, dict) else "").lower()
    result_outcome = str(
        ((observed or {}).get("resultOutcome") if isinstance(observed, dict) else None)
        or lifecycle.result_outcome
        or ""
    ).lower()
    text = _jsonish_text(
        {
            "status": result.status,
            "warnings": result.warnings,
            "missingCapabilities": result.missing_capabilities,
            "lifecycleReason": lifecycle.reason,
            "lifecycleWarnings": lifecycle.warnings,
            "observed": observed,
            "hover": hover,
            "finalClassification": final_classification,
            "mouseMove": trace.get("mouseMove"),
            "clientTick": trace.get("clientTick"),
        }
    )
    stale_like = any(token in text for token in ("stale", "freshness", "not_fresh", "stale_static_route_target"))
    coordinate_like = any(
        token in text
        for token in (
            "screen_click_point_outside_movement_safety_region",
            "resolved screen click point outside",
            "target_outside_allowed_region",
            "cursor_start_outside_allowed_region",
        )
    )
    arduino_like = any(
        token in text
        for token in (
            "hover movement failed",
            "movement_feedback_mismatch",
            "move_chunk_feedback_mismatch",
            "cursor_left_allowed_region",
            "move_chunk_no_rawinput_no_cursor",
            "move_chunk_rawinput_seen_cursor_no_move",
        )
    )
    aimpoint_like = any(
        token in text
        for token in (
            "hover_position_mismatch",
            "hover_confirm_timeout",
            "hover_mismatch_skipped",
            "menu_flip_mismatch",
            "clicked_direct_menu_mismatch",
            "hover_confirmed_but_clicked_walk_here",
            "clicked_menu_did_not_match",
            "clicked_cancel",
        )
    )
    failure_like = (
        str(result.status or "").upper() == "FAIL"
        or result_outcome in {"blocked", "menu_mismatch", "no_change_timeout", "skipped", "interrupted"}
        or observed_result in {"no_click_safety_block", "no_click_safety_skip"}
        or bool(stale_like or coordinate_like or arduino_like or aimpoint_like or final_classification)
    )
    if not failure_like:
        return None
    if coordinate_like:
        return "coordinate_transform_error"
    if stale_like:
        return "game_state_stale"
    if arduino_like:
        return "arduino_movement_error"
    if aimpoint_like:
        return "target_aimpoint_error"
    if result.status == "FAIL" and any(item in {"screen_click_point", "click_point", "canvas_hover_point"} for item in result.missing_capabilities):
        return "coordinate_transform_error"
    return None


def _sync_lifecycle_observation(lifecycle: ActionLifecycleState, observed: dict[str, Any]) -> None:
    lifecycle.observed_result = observed
    lifecycle.observed_signals = [str(signal) for signal in observed.get("observedSignals") or []]
    lifecycle.result_complete = bool(observed.get("resultComplete"))
    lifecycle.result_outcome = str(observed.get("resultOutcome") or "unknown")
    lifecycle.elapsed_ticks = observed.get("elapsedTicks") if isinstance(observed.get("elapsedTicks"), int) else None
    lifecycle.elapsed_millis = observed.get("elapsedMillis") if isinstance(observed.get("elapsedMillis"), int) else None
    lifecycle.timeout_ticks = observed.get("timeoutTicks") if isinstance(observed.get("timeoutTicks"), int) else None
    lifecycle.timeout_millis = observed.get("timeoutMillis") if isinstance(observed.get("timeoutMillis"), int) else None
    lifecycle.next_action_allowed = bool(observed.get("nextActionAllowed"))


def _backend_position(backend: Any) -> tuple[int, int]:
    if hasattr(backend, "current_position"):
        try:
            return tuple(backend.current_position())  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return (0, 0)
    return (0, 0)


def _target_from_click(point: dict[str, Any]) -> MouseTarget:
    return MouseTarget(x=int(point["x"]), y=int(point["y"]), radius_px=4, label="action target", source="action_proposal")


def _path_tile_projection_request(proposal: ActionProposal) -> dict[str, Any] | None:
    if proposal.suggested_click_point or proposal.target_kind != "path_tile":
        return None
    if proposal.proposed_action not in {"navigate_to_service", "return_to_resource_area"}:
        return None
    tile = proposal.target_tile if isinstance(proposal.target_tile, dict) else {}
    world_x = _int_or_none(tile.get("worldX"))
    world_y = _int_or_none(tile.get("worldY"))
    plane = _int_or_none(tile.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {
        "label": proposal.target_name or proposal.proposed_action,
        "worldX": world_x,
        "worldY": world_y,
        "plane": 0 if plane is None else plane,
        "source": "action_proposal.path_tile",
    }


def _tile_projection_request_from_tile(proposal: ActionProposal, tile: dict[str, Any], *, label: str | None = None) -> dict[str, Any] | None:
    world_x = _int_or_none(tile.get("worldX"))
    world_y = _int_or_none(tile.get("worldY"))
    plane = _int_or_none(tile.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {
        "label": label or proposal.target_name or proposal.proposed_action,
        "worldX": world_x,
        "worldY": world_y,
        "plane": 0 if plane is None else plane,
        "source": "action_proposal.path_tile",
    }


def _tile_projection_payload(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    direct = snapshot.get("tileProjections")
    if isinstance(direct, dict):
        return direct
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    value = payloads.get("tile_projection") or payloads.get("tileProjections")
    return value if isinstance(value, dict) else {}


def _matching_tile_projection(snapshot: dict[str, Any] | None, request: dict[str, Any]) -> dict[str, Any] | None:
    payload = _tile_projection_payload(snapshot)
    tiles = payload.get("tiles") if isinstance(payload.get("tiles"), list) else []
    request_x = _int_or_none(request.get("worldX"))
    request_y = _int_or_none(request.get("worldY"))
    request_plane = _int_or_none(request.get("plane"))
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        if _int_or_none(tile.get("worldX")) != request_x or _int_or_none(tile.get("worldY")) != request_y:
            continue
        if request_plane is not None and _int_or_none(tile.get("plane")) != request_plane:
            continue
        return tile
    return None


def _bounds_from_projection(projection: dict[str, Any] | None) -> dict[str, int] | None:
    projection = projection if isinstance(projection, dict) else {}
    bounds = projection.get("canvasTileBounds") or projection.get("bounds")
    if not isinstance(bounds, dict):
        return None
    x = _int_or_none(bounds.get("x"))
    y = _int_or_none(bounds.get("y"))
    w = _int_or_none(bounds.get("w") if bounds.get("w") is not None else bounds.get("width"))
    h = _int_or_none(bounds.get("h") if bounds.get("h") is not None else bounds.get("height"))
    if x is None or y is None or w is None or h is None:
        return None
    return {"x": x, "y": y, "w": max(0, w), "h": max(0, h)}


def _camera_viewport_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    baseline = payloads.get("baseline") if isinstance(payloads.get("baseline"), dict) else snapshot.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    for value in (
        snapshot.get("cameraViewport"),
        baseline.get("cameraViewport"),
        _tile_projection_payload(snapshot).get("cameraViewport"),
    ):
        if isinstance(value, dict):
            return dict(value)
    return None


def _viewport_from_projection(projection: dict[str, Any] | None) -> dict[str, Any]:
    projection = projection if isinstance(projection, dict) else {}
    viewport = projection.get("cameraViewport") if isinstance(projection.get("cameraViewport"), dict) else {}
    canvas_width = _int_or_none(viewport.get("canvasWidth") if viewport else projection.get("canvasWidth")) or 765
    canvas_height = _int_or_none(viewport.get("canvasHeight") if viewport else projection.get("canvasHeight")) or 503
    viewport_x = _int_or_none(viewport.get("viewportXOffset") if viewport else projection.get("viewportXOffset")) or 0
    viewport_y = _int_or_none(viewport.get("viewportYOffset") if viewport else projection.get("viewportYOffset")) or 0
    viewport_width = _int_or_none(viewport.get("viewportWidth") if viewport else projection.get("viewportWidth")) or canvas_width
    viewport_height = _int_or_none(viewport.get("viewportHeight") if viewport else projection.get("viewportHeight")) or canvas_height
    return {
        "canvasWidth": canvas_width,
        "canvasHeight": canvas_height,
        "viewportXOffset": viewport_x,
        "viewportYOffset": viewport_y,
        "viewportWidth": viewport_width,
        "viewportHeight": viewport_height,
    }


def _distance_to_viewport_edge(canvas_point: dict[str, Any] | None, projection: dict[str, Any] | None) -> int | None:
    if not isinstance(canvas_point, dict):
        return None
    x = _int_or_none(canvas_point.get("x") if canvas_point.get("x") is not None else canvas_point.get("canvasX"))
    y = _int_or_none(canvas_point.get("y") if canvas_point.get("y") is not None else canvas_point.get("canvasY"))
    if x is None or y is None:
        return None
    viewport = _viewport_from_projection(projection)
    left = int(viewport["viewportXOffset"])
    top = int(viewport["viewportYOffset"])
    right = left + int(viewport["viewportWidth"])
    bottom = top + int(viewport["viewportHeight"])
    return min(x - left, y - top, right - x, bottom - y)


def _distance_to_canvas_edge(canvas_point: dict[str, Any] | None, projection: dict[str, Any] | None) -> int | None:
    if not isinstance(canvas_point, dict):
        return None
    x = _int_or_none(canvas_point.get("x") if canvas_point.get("x") is not None else canvas_point.get("canvasX"))
    y = _int_or_none(canvas_point.get("y") if canvas_point.get("y") is not None else canvas_point.get("canvasY"))
    if x is None or y is None:
        return None
    viewport = _viewport_from_projection(projection)
    return min(x, y, int(viewport["canvasWidth"]) - x, int(viewport["canvasHeight"]) - y)


def _rect_area(rect: dict[str, int] | None) -> int:
    if not isinstance(rect, dict):
        return 0
    return max(0, int(rect.get("w") or 0)) * max(0, int(rect.get("h") or 0))


def _intersect_rect(first: dict[str, int], second: dict[str, int]) -> dict[str, int] | None:
    left = max(int(first["x"]), int(second["x"]))
    top = max(int(first["y"]), int(second["y"]))
    right = min(int(first["x"]) + int(first["w"]), int(second["x"]) + int(second["w"]))
    bottom = min(int(first["y"]) + int(first["h"]), int(second["y"]) + int(second["h"]))
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


def _visible_area_metrics(bounds: dict[str, int] | None, projection: dict[str, Any] | None) -> tuple[int | None, float | None]:
    if not isinstance(bounds, dict) or _rect_area(bounds) <= 0:
        return None, None
    viewport = _viewport_from_projection(projection)
    canvas_rect = {"x": 0, "y": 0, "w": int(viewport["canvasWidth"]), "h": int(viewport["canvasHeight"])}
    viewport_rect = {
        "x": int(viewport["viewportXOffset"]),
        "y": int(viewport["viewportYOffset"]),
        "w": int(viewport["viewportWidth"]),
        "h": int(viewport["viewportHeight"]),
    }
    clipped = _intersect_rect(bounds, canvas_rect)
    if clipped is not None:
        clipped = _intersect_rect(clipped, viewport_rect)
    clipped_area = _rect_area(clipped)
    total_area = _rect_area(bounds)
    ratio = round(clipped_area / total_area, 4) if total_area > 0 else None
    return clipped_area, ratio


def _projection_ui_blocked(projection: dict[str, Any] | None) -> bool:
    projection = projection if isinstance(projection, dict) else {}
    for key in ("uiBlocked", "blockedByUi", "blockedByUI", "insideUi", "menuBlocked"):
        if _bool_or_none(projection.get(key)) is True:
            return True
    return False


def _reject_edge_route_clicks(options: Any | None) -> bool:
    return bool(getattr(options, "reject_edge_route_clicks", False) if options is not None else False)


def _camera_reacquire_on_edge_projection(options: Any | None) -> bool:
    return bool(getattr(options, "camera_reacquire_on_edge_projection", False) if options is not None else False)


def _route_click_edge_margin_px(options: Any | None) -> int:
    value = getattr(options, "route_click_edge_margin_px", 12) if options is not None else 12
    if value is None:
        value = 12
    return max(0, int(value or 0))


def _route_min_visible_area_ratio(options: Any | None) -> float:
    value = getattr(options, "route_min_visible_area_ratio", 0.45) if options is not None else 0.45
    if value is None:
        value = 0.45
    return max(0.0, min(1.0, float(value or 0.0)))


def _mouse_matches_canvas_point(hover_sample: dict[str, Any] | None, canvas_point: dict[str, Any] | None, *, tolerance_px: int) -> tuple[bool, int | None, int | None]:
    if not isinstance(hover_sample, dict) or not isinstance(canvas_point, dict):
        return False, None, None
    mouse_x = _int_or_none(hover_sample.get("mouseCanvasX"))
    mouse_y = _int_or_none(hover_sample.get("mouseCanvasY"))
    point_x = _int_or_none(canvas_point.get("x") if canvas_point.get("x") is not None else canvas_point.get("canvasX"))
    point_y = _int_or_none(canvas_point.get("y") if canvas_point.get("y") is not None else canvas_point.get("canvasY"))
    if mouse_x is None or mouse_y is None or point_x is None or point_y is None:
        return False, None, None
    dx = abs(mouse_x - point_x)
    dy = abs(mouse_y - point_y)
    return dx <= max(0, int(tolerance_px)) and dy <= max(0, int(tolerance_px)), dx, dy


def _camera_pose_from_viewport(viewport: dict[str, Any] | None) -> dict[str, int | None]:
    viewport = viewport if isinstance(viewport, dict) else {}
    return {
        "cameraYaw": _int_or_none(viewport.get("cameraYaw")),
        "cameraPitch": _int_or_none(viewport.get("cameraPitch")),
    }


def _canvas_point_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> float | None:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    before_x = _int_or_none(before.get("x") if before.get("x") is not None else before.get("canvasX"))
    before_y = _int_or_none(before.get("y") if before.get("y") is not None else before.get("canvasY"))
    after_x = _int_or_none(after.get("x") if after.get("x") is not None else after.get("canvasX"))
    after_y = _int_or_none(after.get("y") if after.get("y") is not None else after.get("canvasY"))
    if before_x is None or before_y is None or after_x is None or after_y is None:
        return None
    return round((((after_x - before_x) ** 2 + (after_y - before_y) ** 2) ** 0.5), 3)


def camera_exposure_score(
    *,
    hover_sample: dict[str, Any] | None,
    canvas_point: dict[str, Any] | None,
    projection: dict[str, Any] | None,
    tolerance_px: int = 3,
    target_world_tile: dict[str, Any] | None = None,
    previous_canvas_point: dict[str, Any] | None = None,
    previous_camera_viewport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    projection = projection if isinstance(projection, dict) else {}
    hover_sample = hover_sample if isinstance(hover_sample, dict) else {}
    point = canvas_point if isinstance(canvas_point, dict) else {}
    camera_viewport = projection.get("cameraViewport") if isinstance(projection.get("cameraViewport"), dict) else {}
    pose_before = _camera_pose_from_viewport(previous_camera_viewport)
    pose_after = _camera_pose_from_viewport(camera_viewport)
    yaw_delta = camera_control.camera_angle_delta(pose_before.get("cameraYaw"), pose_after.get("cameraYaw"))
    pitch_delta = None
    if pose_before.get("cameraPitch") is not None and pose_after.get("cameraPitch") is not None:
        pitch_delta = int(pose_after["cameraPitch"]) - int(pose_before["cameraPitch"])
    projection_delta = _canvas_point_delta(previous_canvas_point, point)
    mouse_matches, dx, dy = _mouse_matches_canvas_point(hover_sample, point, tolerance_px=tolerance_px)
    distance_to_edge = _distance_to_viewport_edge(point, projection)
    menu_class = client_tick_core.classify_menu_action(hover_sample)
    option = hover_sample.get("topOption") or hover_sample.get("option")
    target = hover_sample.get("topTarget") or hover_sample.get("target")
    geometry_available = projection.get("geometryAvailable") is not False
    on_screen = projection.get("onScreen") is not False
    bounds = _bounds_from_projection(projection)
    classification = "ambiguous"
    score = 0
    if not projection:
        classification = "no_projection"
        score = -100
    elif not geometry_available:
        classification = "no_projection"
        score = -90
    elif not on_screen:
        classification = "offscreen"
        score = -85
    elif distance_to_edge is not None and distance_to_edge < 3:
        classification = "edge_blocked"
        score = -35
    if classification == "ambiguous":
        if menu_class == "walk_here" and mouse_matches:
            classification = "exposed_walk_here"
            score = 100
        elif menu_class == "object_action":
            classification = "occluded_by_object"
            score = -50
        elif menu_class == "cancel_hover":
            classification = "ambiguous"
            score = -20
        elif not mouse_matches:
            classification = "ambiguous"
            score = -10
    movement_reference_available = isinstance(previous_canvas_point, dict) or isinstance(previous_camera_viewport, dict)
    no_camera_or_projection_delta = (
        movement_reference_available
        and abs(int(yaw_delta or 0)) == 0
        and abs(int(pitch_delta or 0)) == 0
        and (projection_delta is None or float(projection_delta) < 1.0)
    )
    if classification == "ambiguous" and no_camera_or_projection_delta:
        classification = "no_camera_delta"
        score = min(score, -25)
    if distance_to_edge is not None:
        score += max(-15, min(15, int(distance_to_edge) // 8))
    if bounds and bounds["w"] > 0 and bounds["h"] > 0:
        score += min(10, max(0, (bounds["w"] * bounds["h"]) // 200))
    if menu_class == "object_action":
        score -= 10
    return {
        "schema": "camera_exposure_score.v1",
        "classification": classification,
        "score": score,
        "targetWorldTile": dict(target_world_tile) if isinstance(target_world_tile, dict) else None,
        "waypointCanvasPoint": dict(point) if point else None,
        "projectionAvailable": bool(projection),
        "projectionDeltaPx": projection_delta,
        "mousePositionMatchesProjection": mouse_matches,
        "mouseDeltaX": dx,
        "mouseDeltaY": dy,
        "hoverOption": option,
        "hoverTarget": target,
        "hoverMenuClass": menu_class,
        "hoverMatchesWalkHere": menu_class == "walk_here" and mouse_matches,
        "blockingHoverOption": option if menu_class == "object_action" else None,
        "blockingHoverTarget": target if menu_class == "object_action" else None,
        "distanceToViewportEdgePx": distance_to_edge,
        "waypointTileBounds": bounds,
        "onScreen": on_screen,
        "geometryAvailable": geometry_available,
        "cameraYaw": pose_after.get("cameraYaw"),
        "cameraPitch": pose_after.get("cameraPitch"),
        "yawDelta": yaw_delta,
        "pitchDelta": pitch_delta,
    }


def next_camera_direction_from_exposure(
    attempts: list[dict[str, Any]] | None,
    *,
    preferred_direction: str = "right",
    min_score_improvement: int = 1,
) -> str:
    preferred = "left" if str(preferred_direction).lower() == "left" else "right"
    attempts = attempts if isinstance(attempts, list) else []
    if not attempts:
        return preferred
    last = attempts[-1] if isinstance(attempts[-1], dict) else {}
    action = str(last.get("cameraAction") or "")
    direction = "left" if "left" in action else "right" if "right" in action else preferred
    before = last.get("exposureScoreBefore") if isinstance(last.get("exposureScoreBefore"), dict) else {}
    after = last.get("exposureScoreAfter") if isinstance(last.get("exposureScoreAfter"), dict) else {}
    before_score = int(before.get("score") or 0)
    after_score = int(after.get("score") or 0)
    if after_score >= before_score + max(0, int(min_score_improvement)):
        return direction
    return "left" if direction == "right" else "right"


def _aim_point_from_tile_projection(tile: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(tile, dict):
        return None
    for key in ("safeAimPoint", "aimPoint", "canvasCenter"):
        value = tile.get(key)
        if not isinstance(value, dict):
            continue
        x = _int_or_none(value.get("x"))
        y = _int_or_none(value.get("y"))
        if x is None:
            x = _int_or_none(value.get("canvasX"))
        if y is None:
            y = _int_or_none(value.get("canvasY"))
        if x is not None and y is not None:
            return {"x": x, "y": y}
    bounds = tile.get("canvasTileBounds") or tile.get("bounds")
    if isinstance(bounds, dict):
        x = _int_or_none(bounds.get("x"))
        y = _int_or_none(bounds.get("y"))
        w = _int_or_none(bounds.get("w") if bounds.get("w") is not None else bounds.get("width"))
        h = _int_or_none(bounds.get("h") if bounds.get("h") is not None else bounds.get("height"))
        if x is not None and y is not None and w is not None and h is not None:
            return {"x": x + max(1, w) // 2, "y": y + max(1, h) // 2}
    return None


def _tile_projection_actionability_warning(tile: dict[str, Any] | None) -> str | None:
    if not isinstance(tile, dict):
        return "tile projection missing"
    bounds = tile.get("canvasTileBounds") or tile.get("bounds")
    width = height = None
    if isinstance(bounds, dict):
        width = _int_or_none(bounds.get("w") if bounds.get("w") is not None else bounds.get("width"))
        height = _int_or_none(bounds.get("h") if bounds.get("h") is not None else bounds.get("height"))
    polygon = tile.get("canvasTilePolygon") or tile.get("tilePolygon")
    if isinstance(polygon, list) and polygon:
        xs: list[int] = []
        ys: list[int] = []
        for point in polygon:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                x = _int_or_none(point[0])
                y = _int_or_none(point[1])
            elif isinstance(point, dict):
                x = _int_or_none(point.get("x") if point.get("x") is not None else point.get("canvasX"))
                y = _int_or_none(point.get("y") if point.get("y") is not None else point.get("canvasY"))
            else:
                continue
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        if len(xs) < 3 or max(xs) <= min(xs) or max(ys) <= min(ys):
            return "tile projection returned degenerate canvas polygon"
    if width is not None and height is not None and (width < 3 or height < 3):
        return "tile projection canvas bounds are too small to click safely"
    point = _aim_point_from_tile_projection(tile)
    if point and point.get("x") == 0 and point.get("y") == 0:
        return "tile projection returned the canvas origin as an aim point"
    return None


def route_projection_status(
    tile: dict[str, Any] | None,
    hover_sample: dict[str, Any] | None = None,
    *,
    reject_edge_route_clicks: bool = False,
    edge_margin_px: int = 0,
    min_visible_area_ratio: float = 0.0,
) -> dict[str, Any]:
    tile = tile if isinstance(tile, dict) else {}
    point = _aim_point_from_tile_projection(tile)
    bounds = _bounds_from_projection(tile)
    viewport = _viewport_from_projection(tile)
    hover_sample = hover_sample if isinstance(hover_sample, dict) else {}
    hover_class = client_tick_core.classify_menu_action(hover_sample) if hover_sample else None
    rejection = _tile_projection_actionability_warning(tile)
    distance_to_viewport_edge = _distance_to_viewport_edge(point, tile)
    distance_to_canvas_edge = _distance_to_canvas_edge(point, tile)
    clipped_area_px, clipped_area_ratio = _visible_area_metrics(bounds, tile)
    edge_margin = max(0, int(edge_margin_px or 0))
    min_ratio = max(0.0, min(1.0, float(min_visible_area_ratio or 0.0)))
    ui_blocked = _projection_ui_blocked(tile)
    in_canvas = None
    in_viewport = None
    if isinstance(point, dict):
        x = _int_or_none(point.get("x") if point.get("x") is not None else point.get("canvasX"))
        y = _int_or_none(point.get("y") if point.get("y") is not None else point.get("canvasY"))
        if x is not None and y is not None:
            in_canvas = 0 <= x <= int(viewport["canvasWidth"]) and 0 <= y <= int(viewport["canvasHeight"])
            left = int(viewport["viewportXOffset"])
            top = int(viewport["viewportYOffset"])
            right = left + int(viewport["viewportWidth"])
            bottom = top + int(viewport["viewportHeight"])
            in_viewport = left <= x <= right and top <= y <= bottom
    degenerate = bool(rejection and ("degenerate" in rejection or "canvas origin" in rejection))
    tiny = bool(rejection and "too small" in rejection)
    offscreen = tile.get("onScreen") is False or in_viewport is False
    object_occluded = hover_class == "object_action"
    edge_too_close = (
        bool(reject_edge_route_clicks)
        and edge_margin > 0
        and distance_to_viewport_edge is not None
        and distance_to_viewport_edge <= edge_margin
    )
    canvas_edge_too_close = (
        bool(reject_edge_route_clicks)
        and edge_margin > 0
        and distance_to_canvas_edge is not None
        and distance_to_canvas_edge <= edge_margin
    )
    visible_ratio_too_low = (
        bool(reject_edge_route_clicks)
        and min_ratio > 0
        and clipped_area_ratio is not None
        and clipped_area_ratio < min_ratio
    )
    edge_clipped = edge_too_close or canvas_edge_too_close or visible_ratio_too_low
    if ui_blocked and not rejection:
        rejection = "route tile aim point is blocked by UI"
    if edge_clipped and not rejection:
        if edge_too_close:
            rejection = f"route tile aim point is too close to viewport edge ({distance_to_viewport_edge}px <= {edge_margin}px)"
        elif canvas_edge_too_close:
            rejection = f"route tile aim point is too close to canvas edge ({distance_to_canvas_edge}px <= {edge_margin}px)"
        else:
            rejection = f"route tile visible area ratio {clipped_area_ratio} is below minimum {min_ratio}"
    actionable = (
        bool(tile)
        and not rejection
        and not ui_blocked
        and not edge_clipped
        and tile.get("onScreen") is not False
        and tile.get("geometryAvailable") is not False
        and in_canvas is not False
        and in_viewport is not False
    )
    if not tile:
        classification = "no_projection"
        rejection = rejection or "tile projection missing"
    elif tile.get("geometryAvailable") is False:
        classification = "no_projection"
        rejection = rejection or str(tile.get("reason") or tile.get("geometryWarning") or "tile projection geometry unavailable")
    elif degenerate:
        classification = "degenerate"
    elif tiny:
        classification = "tiny_projection"
    elif offscreen:
        classification = "offscreen"
        rejection = rejection or str(tile.get("reason") or tile.get("geometryWarning") or "tile projection is outside viewport")
    elif ui_blocked:
        classification = "not_actionable"
    elif edge_clipped:
        classification = "edge_clipped"
    elif object_occluded:
        classification = "occluded"
        rejection = rejection or "hover menu is object action over route tile"
    elif actionable:
        classification = "visible"
    else:
        classification = "not_actionable"
        rejection = rejection or "tile projection is not actionable"
    return {
        "schema": "route_projection_status.v1",
        "worldTile": {
            "worldX": _int_or_none(tile.get("worldX")),
            "worldY": _int_or_none(tile.get("worldY")),
            "plane": _int_or_none(tile.get("plane")),
        },
        "canvasPoint": dict(point) if isinstance(point, dict) else None,
        "canvasTileBounds": dict(bounds) if isinstance(bounds, dict) else None,
        "inCanvas": in_canvas,
        "inViewport": in_viewport,
        "degenerateProjection": degenerate,
        "tinyProjection": tiny,
        "offscreen": bool(offscreen),
        "uiBlocked": ui_blocked,
        "edgeClipped": bool(edge_clipped),
        "edgeMarginPx": edge_margin if reject_edge_route_clicks else None,
        "minVisibleAreaRatio": min_ratio if reject_edge_route_clicks else None,
        "distanceToViewportEdgePx": distance_to_viewport_edge,
        "distanceToCanvasEdgePx": distance_to_canvas_edge,
        "clippedVisibleAreaPx": clipped_area_px,
        "clippedVisibleAreaRatio": clipped_area_ratio,
        "projectedVisibleAreaPx": clipped_area_px,
        "projectedVisibleAreaRatio": clipped_area_ratio,
        "partiallyOffscreen": bool(clipped_area_ratio is not None and clipped_area_ratio < 1.0),
        "objectOccluded": object_occluded,
        "hoverOption": hover_sample.get("topOption") or hover_sample.get("option"),
        "hoverTarget": hover_sample.get("topTarget") or hover_sample.get("target"),
        "projectionSource": tile.get("source") or tile.get("projectionSource") or "plugin_tile_projection",
        "actionableByCanvas": actionable,
        "actionableByMinimap": False,
        "classification": classification,
        "rejectionReason": None if actionable else rejection,
    }


def _movement_safety_config(backend: Any) -> dict[str, Any] | None:
    getter = getattr(backend, "movement_safety", None)
    safety = None
    if callable(getter):
        try:
            safety = getter()
        except Exception:  # noqa: BLE001
            safety = None
    if safety is None:
        safety = getattr(backend, "_movement_safety", None)
    if not isinstance(safety, dict) or not safety.get("enabled"):
        return None
    return dict(safety)


def _movement_safety_rect(region: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(region, dict):
        return None
    x = region.get("x", region.get("left"))
    y = region.get("y", region.get("top"))
    width = region.get("width")
    height = region.get("height")
    right = region.get("right")
    bottom = region.get("bottom")
    try:
        left_i = int(round(float(x)))
        top_i = int(round(float(y)))
        if width is not None and height is not None:
            width_i = int(round(float(width)))
            height_i = int(round(float(height)))
            right_i = left_i + width_i
            bottom_i = top_i + height_i
        elif right is not None and bottom is not None:
            right_i = int(round(float(right)))
            bottom_i = int(round(float(bottom)))
            width_i = right_i - left_i
            height_i = bottom_i - top_i
        else:
            return None
    except (TypeError, ValueError):
        return None
    if width_i <= 0 or height_i <= 0:
        return None
    return {"x": left_i, "y": top_i, "width": width_i, "height": height_i, "right": right_i, "bottom": bottom_i}


def _movement_safety_point(point: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(point, dict):
        return None
    try:
        return {"x": int(round(float(point.get("x")))), "y": int(round(float(point.get("y"))))}
    except (TypeError, ValueError):
        return None


def _movement_safety_preflight(screen_point: dict[str, Any] | None, backend: Any) -> dict[str, Any] | None:
    safety = _movement_safety_config(backend)
    if safety is None:
        return None
    point = _movement_safety_point(screen_point)
    rect = _movement_safety_rect(safety.get("allowedRegion"))
    margin = max(0, int(safety.get("marginPx") or 0))
    preflight = {
        "schema": "movement_safety_preflight.v1",
        "status": "PASS",
        "reason": None,
        "screenPoint": dict(point) if point else None,
        "allowedRegion": dict(rect) if rect else None,
        "marginPx": margin,
    }
    if point is None:
        preflight["status"] = "FAIL"
        preflight["reason"] = "movement_safety_screen_point_unavailable"
        return preflight
    if rect is None:
        preflight["status"] = "FAIL"
        preflight["reason"] = "movement_safety_region_unavailable"
        return preflight
    inside = (
        int(rect["x"]) + margin <= int(point["x"]) <= int(rect["right"]) - margin
        and int(rect["y"]) + margin <= int(point["y"]) <= int(rect["bottom"]) - margin
    )
    preflight["targetInsideAllowedRegion"] = bool(inside)
    if not inside:
        preflight["status"] = "FAIL"
        preflight["reason"] = "screen_click_point_outside_movement_safety_region"
    return preflight


def _movement_safety_preflight_failed(preflight: dict[str, Any] | None) -> bool:
    return isinstance(preflight, dict) and str(preflight.get("status") or "").upper() == "FAIL"


def _movement_safety_preflight_warning(preflight: dict[str, Any] | None) -> str:
    preflight = preflight if isinstance(preflight, dict) else {}
    reason = str(preflight.get("reason") or "movement_safety_preflight_failed")
    point = preflight.get("screenPoint")
    region = preflight.get("allowedRegion")
    if reason == "movement_safety_region_unavailable":
        return f"{reason}: configured live movement safety region is unavailable"
    if reason == "movement_safety_screen_point_unavailable":
        return f"{reason}: screen point is unavailable for live movement safety validation"
    return f"{reason}: screen point {point or 'unknown'} is outside configured live movement safety region {region or 'unknown'}"


def _can_camera_reacquire_movement_safety_failure(
    proposal: ActionProposal,
    preflight: dict[str, Any] | None,
    navigation_options: Any | None,
) -> str | None:
    if not _is_navigation_path_proposal(proposal) or not _movement_safety_preflight_failed(preflight):
        return None
    if str((preflight or {}).get("reason") or "") != "screen_click_point_outside_movement_safety_region":
        return None
    if not _camera_reacquire_on_edge_projection(navigation_options):
        return None
    if not _camera_reacquire_waypoint_enabled(navigation_options):
        return None
    return "screen_point_outside_movement_safety_region"


def _apply_path_tile_projection(
    proposal: ActionProposal,
    projection: dict[str, Any],
    *,
    backend: Any,
    navigation_options: Any | None = None,
) -> tuple[ActionProposal, list[str]]:
    warnings: list[str] = []
    projection_status = route_projection_status(
        projection,
        reject_edge_route_clicks=_reject_edge_route_clicks(navigation_options),
        edge_margin_px=_route_click_edge_margin_px(navigation_options),
        min_visible_area_ratio=_route_min_visible_area_ratio(navigation_options),
    )
    if not isinstance(proposal.target_explanation, dict):
        proposal.target_explanation = {}
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation["tileProjection"] = dict(projection)
        proposal.target_explanation["routeProjectionStatus"] = projection_status
    if str(projection.get("status") or "PASS").upper() == "FAIL":
        reason = projection.get("reason") or projection.get("warning") or "tile projection failed"
        return proposal, [f"path tile projection failed: {reason}"]
    if projection.get("onScreen") is False or projection.get("geometryAvailable") is False:
        reason = projection.get("reason") or projection.get("geometryWarning") or "tile projection is not visible"
        return proposal, [f"path tile projection not actionable: {reason}"]
    actionability_warning = _tile_projection_actionability_warning(projection)
    if actionability_warning:
        return proposal, [f"path tile projection not actionable: {actionability_warning}"]
    if projection_status.get("actionableByCanvas") is not True:
        reason = projection_status.get("rejectionReason") or projection_status.get("classification") or "tile projection is not actionable"
        return proposal, [f"path tile projection not actionable: {reason}"]
    point = _aim_point_from_tile_projection(projection)
    if not point:
        return proposal, ["path tile projection did not include a canvas aim point"]

    proposal.suggested_click_point = point
    proposal.click_point_space = "canvas"
    proposal.action_target_source = "live_projected_waypoint"
    proposal.actionability = "needs_hover_confirmation"
    proposal.missing_capabilities = [item for item in proposal.missing_capabilities if item not in {"click_point", "screen_click_point"}]
    proposal.warnings = [warning for warning in proposal.warnings if "missing click point" not in str(warning)]
    if isinstance(proposal.target_explanation, dict):
        proposal.target_explanation.setdefault("advisoryTargetSource", proposal.target_explanation.get("targetSource"))
        proposal.target_explanation["actionTargetSource"] = proposal.action_target_source
        proposal.target_explanation["actionability"] = proposal.actionability
        proposal.target_explanation["tileProjection"] = dict(projection)
        proposal.target_explanation["routeProjectionStatus"] = projection_status
        proposal.target_explanation["aimPoint"] = {"canvasX": point["x"], "canvasY": point["y"], "source": "plugin_tile_projection"}
    projected_tile = {
        "worldX": _int_or_none(projection.get("worldX")),
        "worldY": _int_or_none(projection.get("worldY")),
        "plane": _int_or_none(projection.get("plane")),
    }
    if projected_tile["worldX"] is not None and projected_tile["worldY"] is not None:
        proposal.target_tile = projected_tile
        proposal.suggested_world_tile = projected_tile
    resolution = resolve_screen_click_point(
        point,
        click_point_space="canvas",
        input_geometry=proposal.input_geometry,
    )
    if isinstance(resolution, dict) and resolution.get("status") == "PASS" and isinstance(resolution.get("screenClickPoint"), dict):
        resolution = {
            "status": "PASS",
            "method": "plugin_tile_projection",
            "coordinateMethod": resolution.get("method"),
            "coordinateResolver": resolution.get("coordinateResolver"),
            "screenClickPoint": resolution.get("screenClickPoint"),
            "coordinateSpace": resolution.get("coordinateSpace"),
            "scaleX": resolution.get("scaleX"),
            "scaleY": resolution.get("scaleY"),
            "screenPointBeforeScaling": resolution.get("screenPointBeforeScaling"),
            "screenPointAfterScaling": resolution.get("screenPointAfterScaling"),
            "windowBoundsSource": resolution.get("windowBoundsSource"),
            "canvasBoundsSource": resolution.get("canvasBoundsSource"),
            "displayScale": resolution.get("displayScale"),
            "displayScaleApplied": resolution.get("displayScaleApplied"),
            "displayScaleReason": resolution.get("displayScaleReason"),
            "warnings": [],
            "missingCapabilities": [],
            "tileProjection": dict(projection),
        }
    elif isinstance(resolution, dict) and resolution.get("status") == "FAIL":
        resolution["method"] = "plugin_tile_projection"
        resolution["tileProjection"] = dict(projection)
        warnings.extend(str(item) for item in (resolution.get("warnings") or ["path tile projection coordinate validation failed"]))
        proposal.click_point_resolution = resolution
        proposal.status = "FAIL"
        if "screen_click_point" not in proposal.missing_capabilities:
            proposal.missing_capabilities.append("screen_click_point")
        return proposal, warnings
    else:
        try:
            resolution = {
                "status": "PASS",
                "method": "plugin_tile_projection",
                "coordinateMethod": "backend_fallback_window_geometry",
                "coordinateResolver": "backend.canvas_to_screen_point",
                "screenClickPoint": backend.canvas_to_screen_point(point),
                "displayScaleApplied": False,
                "displayScaleReason": "dynamic_input_geometry_unavailable_backend_fallback",
                "warnings": ["dynamic input geometry unavailable; used backend fallback window geometry"],
                "missingCapabilities": [],
                "tileProjection": dict(projection),
            }
        except Exception as error:  # noqa: BLE001
            warnings.append(f"path tile screen conversion failed: {type(error).__name__}: {error}")
            resolution = None
    if isinstance(resolution, dict) and isinstance(resolution.get("screenClickPoint"), dict):
        movement_preflight = _movement_safety_preflight(resolution.get("screenClickPoint"), backend)
        if _movement_safety_preflight_failed(movement_preflight):
            warning = _movement_safety_preflight_warning(movement_preflight)
            warnings.append(warning)
            resolution = dict(resolution)
            resolution["status"] = "FAIL"
            resolution["movementSafetyPreflight"] = dict(movement_preflight or {})
            resolution["warnings"] = list(dict.fromkeys([*(resolution.get("warnings") or []), warning]))
            resolution["missingCapabilities"] = list(dict.fromkeys([*(resolution.get("missingCapabilities") or []), "screen_click_point"]))
            proposal.click_point_resolution = resolution
            proposal.status = "FAIL"
            if "screen_click_point" not in proposal.missing_capabilities:
                proposal.missing_capabilities.append("screen_click_point")
            return proposal, warnings
        if isinstance(movement_preflight, dict):
            resolution = dict(resolution)
            resolution["movementSafetyPreflight"] = dict(movement_preflight)
        proposal.click_point_resolution = resolution
        proposal.resolved_screen_click_point = {
            "x": int(round(float(resolution["screenClickPoint"]["x"]))),
            "y": int(round(float(resolution["screenClickPoint"]["y"]))),
        }
    else:
        proposal.click_point_resolution = {
            "status": "PASS",
            "method": "plugin_tile_projection",
            "screenClickPoint": None,
            "warnings": list(warnings),
            "missingCapabilities": [],
            "tileProjection": dict(projection),
        }
    if not proposal.missing_capabilities and not warnings:
        proposal.status = "PASS"
    elif warnings:
        proposal.status = "WARN"
    return proposal, warnings


def _resolve_path_tile_projection(
    proposal: ActionProposal,
    *,
    backend: Any,
    snapshot_url: str,
    navigation_options: Any | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
) -> tuple[ActionProposal, list[str]]:
    request = _path_tile_projection_request(proposal)
    if request is None:
        return proposal, []
    try:
        snapshot = snapshot_fetch_func(
            snapshot_url,
            timeout=0.35,
            tile_projection_requests=[request],
        )
    except Exception as error:  # noqa: BLE001
        return proposal, [f"path tile projection unavailable: {type(error).__name__}: {error}"]

    projection = _matching_tile_projection(snapshot, request)
    if not isinstance(projection, dict):
        return proposal, ["path tile projection missing from plugin snapshot response"]
    primary_candidate, primary_warnings = _apply_path_tile_projection(proposal, projection, backend=backend, navigation_options=navigation_options)
    if not primary_warnings:
        return primary_candidate, []
    if not _is_navigation_path_proposal(proposal):
        return proposal, primary_warnings

    alternate_requests = _navigation_alternate_tile_requests(
        proposal,
        snapshot,
        max_requests=_max_waypoint_alternates(navigation_options),
        navigation_options=navigation_options,
    )
    if not alternate_requests:
        return proposal, primary_warnings
    try:
        alternate_snapshot = snapshot_fetch_func(
            snapshot_url,
            timeout=0.35,
            tile_projection_requests=alternate_requests,
        )
    except Exception as error:  # noqa: BLE001
        return proposal, primary_warnings + [f"structured alternate tile projections unavailable: {type(error).__name__}: {error}"]

    attempt_warnings: list[str] = []
    for alternate_request in alternate_requests:
        alternate_projection = _matching_tile_projection(alternate_snapshot, alternate_request)
        if not isinstance(alternate_projection, dict):
            attempt_warnings.append(f"alternate {alternate_request.get('worldX')},{alternate_request.get('worldY')} projection missing")
            continue
        candidate = deepcopy(proposal)
        candidate.target_tile = {
            "worldX": _int_or_none(alternate_request.get("worldX")),
            "worldY": _int_or_none(alternate_request.get("worldY")),
            "plane": _int_or_none(alternate_request.get("plane")),
        }
        candidate.suggested_world_tile = dict(candidate.target_tile)
        candidate.suggested_click_point = None
        candidate.resolved_screen_click_point = None
        candidate.click_point_resolution = None
        candidate, alternate_warnings = _apply_path_tile_projection(candidate, alternate_projection, backend=backend, navigation_options=navigation_options)
        if alternate_warnings:
            attempt_warnings.extend(alternate_warnings)
            continue
        if isinstance(candidate.target_explanation, dict):
            candidate.target_explanation.setdefault("navigationReacquisition", {})["selectedAlternateWaypoint"] = dict(candidate.target_tile or {})
            candidate.target_explanation["navigationReacquisition"]["reason"] = "primary_waypoint_projection_not_actionable"
        warning = (
            "primary path tile projection not actionable; selected structured alternate "
            f"{candidate.target_tile.get('worldX')},{candidate.target_tile.get('worldY')}"
        )
        return candidate, primary_warnings + [warning]
    return proposal, primary_warnings + attempt_warnings[:3]


def _snapshot_player_tile(snapshot: dict[str, Any] | None) -> dict[str, int] | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    payloads = snapshot.get("payloads") if isinstance(snapshot.get("payloads"), dict) else {}
    baseline = payloads.get("baseline") if isinstance(payloads.get("baseline"), dict) else snapshot.get("baseline")
    baseline = baseline if isinstance(baseline, dict) else {}
    player = baseline.get("player") if isinstance(baseline.get("player"), dict) else {}
    world_x = _int_or_none(player.get("worldX"))
    world_y = _int_or_none(player.get("worldY"))
    plane = _int_or_none(player.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": 0 if plane is None else plane}


def _navigation_destination_tile(proposal: ActionProposal) -> dict[str, int] | None:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    world = explanation.get("destinationTile") if isinstance(explanation.get("destinationTile"), dict) else None
    if not world:
        world = explanation.get("worldLocation") if isinstance(explanation.get("worldLocation"), dict) else explanation.get("world")
    world = world if isinstance(world, dict) else {}
    world_x = _int_or_none(world.get("worldX"))
    world_y = _int_or_none(world.get("worldY"))
    plane = _int_or_none(world.get("plane"))
    if world_x is None or world_y is None:
        tile = proposal.target_tile if isinstance(proposal.target_tile, dict) else {}
        world_x = _int_or_none(tile.get("worldX"))
        world_y = _int_or_none(tile.get("worldY"))
        plane = _int_or_none(tile.get("plane"))
    if world_x is None or world_y is None:
        return None
    return {"worldX": world_x, "worldY": world_y, "plane": 0 if plane is None else plane}


def _suppressed_route_target_keys_from_explanation(explanation: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("suppressedTargetKeysAtSelection", "suppressedActionTargetKeys", "suppressedNavigationTargetKeys"):
        values = explanation.get(field)
        if isinstance(values, list):
            keys.update(str(value) for value in values if value is not None)
    reacquisition = explanation.get("reacquisition")
    if isinstance(reacquisition, dict):
        values = reacquisition.get("suppressedTargetKeys")
        if isinstance(values, list):
            keys.update(str(value) for value in values if value is not None)
    selection = explanation.get("routeWaypointSelection")
    if isinstance(selection, dict):
        values = selection.get("suppressedTargetKeys")
        if isinstance(values, list):
            keys.update(str(value) for value in values if value is not None)
    return keys


def _route_tile_target_keys(tile: dict[str, Any], explanation: dict[str, Any]) -> set[str]:
    object_id = explanation.get("objectId", explanation.get("rawId", explanation.get("id")))
    class_id = explanation.get("classId") or explanation.get("targetClass")
    keys: set[str] = set()
    id_values: list[Any] = []
    for value in (object_id, None):
        if value not in id_values:
            id_values.append(value)
    class_values: list[Any] = []
    for value in (class_id, None):
        if value not in class_values:
            class_values.append(value)
    for target_id in id_values:
        for target_class in class_values:
            key = _target_key_from_mapping(
                {
                    "id": target_id,
                    "worldX": tile.get("worldX"),
                    "worldY": tile.get("worldY"),
                    "plane": tile.get("plane", 0),
                    "classId": target_class,
                }
            )
            if key:
                keys.add(key)
    return keys


def _navigation_alternate_tile_requests(
    proposal: ActionProposal,
    snapshot: dict[str, Any] | None,
    *,
    max_requests: int = 12,
    navigation_options: Any | None = None,
) -> list[dict[str, Any]]:
    if proposal.target_kind != "path_tile" or proposal.proposed_action not in {"navigate_to_service", "return_to_resource_area"}:
        return []
    if max_requests <= 0:
        return []
    destination = _navigation_destination_tile(proposal)
    primary = proposal.target_tile if isinstance(proposal.target_tile, dict) else {}
    if destination is None:
        return []
    player = _snapshot_player_tile(snapshot)
    if player is not None and player.get("plane") != destination.get("plane"):
        return []
    fallback_plane = _int_or_none(primary.get("plane"))
    if fallback_plane is None:
        fallback_plane = _int_or_none(destination.get("plane"))
    reference_plane = _int_or_none(player.get("plane")) if player is not None else fallback_plane
    if reference_plane is None:
        reference_plane = 0
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    suppressed_route_keys = _suppressed_route_target_keys_from_explanation(explanation)
    structured_tiles: list[dict[str, int]] = []
    for key in ("predictedPathTiles", "localScoutPath", "availableWaypointTiles"):
        values = explanation.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            tile = item if isinstance(item, dict) else {}
            world_x = _int_or_none(tile.get("worldX") if tile.get("worldX") is not None else tile.get("x"))
            world_y = _int_or_none(tile.get("worldY") if tile.get("worldY") is not None else tile.get("y"))
            plane = _int_or_none(tile.get("plane"))
            if world_x is None or world_y is None:
                continue
            structured_tiles.append({"worldX": world_x, "worldY": world_y, "plane": reference_plane if plane is None else plane})
    primary_key = (
        _int_or_none(primary.get("worldX")),
        _int_or_none(primary.get("worldY")),
        _int_or_none(primary.get("plane")),
    )
    candidates: list[tuple[int, dict[str, int], tuple[int, int], int]] = []
    for index, tile in enumerate(structured_tiles):
        key = (tile["worldX"], tile["worldY"], tile["plane"])
        if key == primary_key:
            continue
        if player is not None and tile.get("plane") != player.get("plane"):
            continue
        if player is None and tile.get("plane") != reference_plane:
            continue
        if suppressed_route_keys and _route_tile_target_keys(tile, explanation).intersection(suppressed_route_keys):
            continue
        score = (
            max(abs(destination["worldX"] - tile["worldX"]), abs(destination["worldY"] - tile["worldY"])),
            abs(destination["worldX"] - tile["worldX"]) + abs(destination["worldY"] - tile["worldY"]),
        )
        if player is not None:
            step_distance = max(abs(tile["worldX"] - player["worldX"]), abs(tile["worldY"] - player["worldY"]))
        else:
            step_distance = index + 1
        if step_distance <= 0:
            continue
        max_distance = int(getattr(navigation_options, "max_route_waypoint_distance", 0) or 0)
        if max_distance > 0 and step_distance > max_distance:
            continue
        candidates.append((step_distance, tile, score, index))
    if not candidates:
        return []

    min_progress = max(1, int(getattr(navigation_options, "min_route_progress_tiles", 3) or 3))
    lookahead = max(min_progress, int(getattr(navigation_options, "route_waypoint_lookahead_tiles", 12) or 12))
    max_horizon = max(lookahead, int(getattr(navigation_options, "route_waypoint_max_horizon_tiles", 25) or 25))
    max_distance = int(getattr(navigation_options, "max_route_waypoint_distance", 0) or 0)
    if max_distance > 0:
        max_horizon = min(max_horizon, max_distance)
    distance_targets: list[int] = []
    if player is None:
        distance_targets.append(1)
    for value in (min_progress, 6, max(min_progress, lookahead // 2), lookahead, max_horizon):
        value = max(1, int(value))
        if value not in distance_targets:
            distance_targets.append(value)

    ordered_candidates: list[tuple[int, dict[str, int], tuple[int, int], int]] = []
    seen_candidate_tiles: set[tuple[int, int, int]] = set()

    def add_candidate(item: tuple[int, dict[str, int], tuple[int, int], int]) -> None:
        tile = item[1]
        key = (tile["worldX"], tile["worldY"], tile["plane"])
        if key in seen_candidate_tiles:
            return
        seen_candidate_tiles.add(key)
        ordered_candidates.append(item)

    reachable_candidates = [item for item in candidates if item[0] <= max_horizon]
    for target_distance in distance_targets:
        pool = reachable_candidates or candidates
        at_or_beyond = [item for item in pool if item[0] >= target_distance]
        selected_pool = at_or_beyond if at_or_beyond else pool
        selected = min(
            selected_pool,
            key=lambda item: (
                abs(item[0] - target_distance),
                item[2][0],
                item[2][1],
                item[3],
                item[1]["worldX"] * 256 + item[1]["worldY"],
            ),
        )
        add_candidate(selected)

    for item in sorted(
        candidates,
        key=lambda item: (
            item[2][0],
            item[2][1],
            abs(item[0] - lookahead),
            item[3],
            item[1]["worldX"] * 256 + item[1]["worldY"],
        ),
    ):
        add_candidate(item)
        if len(ordered_candidates) >= max_requests:
            break

    requests: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for _step_distance, tile, _score, _index in ordered_candidates:
        key = (tile["worldX"], tile["worldY"], tile["plane"])
        if key in seen:
            continue
        seen.add(key)
        request = _tile_projection_request_from_tile(
            proposal,
            tile,
            label=f"{proposal.target_name or proposal.proposed_action} alternate",
        )
        if request is not None:
            requests.append(request)
        if len(requests) >= max_requests:
            break
    return requests


def _screen_click_point(proposal: ActionProposal, backend: Any) -> tuple[dict[str, int] | None, list[str], dict[str, Any] | None]:
    if not proposal.suggested_click_point:
        return None, [], None
    if isinstance(proposal.click_point_resolution, dict) and proposal.click_point_resolution.get("status") == "FAIL":
        warnings = [str(item) for item in proposal.click_point_resolution.get("warnings") or []]
        return None, warnings or ["resolved click point failed validation"], proposal.click_point_resolution
    if isinstance(proposal.resolved_screen_click_point, dict):
        point = proposal.resolved_screen_click_point
        return {"x": int(round(float(point["x"]))), "y": int(round(float(point["y"])))}, [], proposal.click_point_resolution
    point = dict(proposal.suggested_click_point)
    if proposal.click_point_space == "canvas":
        resolution = resolve_screen_click_point(
            point,
            click_point_space="canvas",
            input_geometry=proposal.input_geometry,
            source_canvas_size=(proposal.input_geometry or {}).get("sourceCanvasSize") if isinstance(proposal.input_geometry, dict) else None,
        )
        if isinstance(resolution, dict):
            screen_point = resolution.get("screenClickPoint")
            if resolution.get("status") == "PASS" and isinstance(screen_point, dict):
                return {
                    "x": int(round(float(screen_point["x"]))),
                    "y": int(round(float(screen_point["y"]))),
                }, [], resolution
            if resolution.get("status") == "FAIL":
                warnings = [str(item) for item in resolution.get("warnings") or []]
                return None, warnings or ["resolved click point failed validation"], resolution
        converter = getattr(backend, "canvas_to_screen_point", None)
        if callable(converter):
            try:
                converted = converter(point)
                if isinstance(converted, dict) and converted.get("x") is not None and converted.get("y") is not None:
                    resolution = {
                        "status": "PASS",
                        "method": "backend_fallback_window_geometry",
                        "coordinateResolver": "backend.canvas_to_screen_point",
                        "screenClickPoint": {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))},
                        "coordinateSpace": "physical_pyautogui",
                        "scaleX": 1.0,
                        "scaleY": 1.0,
                        "screenPointBeforeScaling": {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))},
                        "screenPointAfterScaling": {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))},
                        "windowBoundsSource": "backend.canvas_to_screen_point",
                        "canvasBoundsSource": "backend.canvas_client_geometry",
                        "displayScaleApplied": False,
                        "displayScaleReason": "dynamic_input_geometry_unavailable_backend_fallback",
                        "warnings": ["dynamic input geometry unavailable; used backend fallback window geometry"],
                        "missingCapabilities": [],
                    }
                    return dict(resolution["screenClickPoint"]), list(resolution["warnings"]), resolution
            except Exception as error:  # noqa: BLE001
                return None, [f"canvas coordinate conversion failed: {type(error).__name__}: {error}"], None
        return None, ["canvas click point requires backend window coordinate conversion"], None
    resolution = {
        "status": "PASS",
        "method": "screen_direct",
        "coordinateResolver": "executor.screen_direct",
        "screenClickPoint": {"x": int(point["x"]), "y": int(point["y"])},
        "coordinateSpace": "physical_pyautogui",
        "scaleX": 1.0,
        "scaleY": 1.0,
        "screenPointBeforeScaling": {"x": int(point["x"]), "y": int(point["y"])},
        "screenPointAfterScaling": {"x": int(point["x"]), "y": int(point["y"])},
        "windowBoundsSource": "screen_direct",
        "canvasBoundsSource": "none",
        "displayScaleApplied": False,
        "displayScaleReason": "screen_direct_already_physical",
        "warnings": [],
        "missingCapabilities": [],
    }
    return dict(resolution["screenClickPoint"]), [], resolution


def hover_options_from_options(options: Any) -> HoverConfirmationOptions | None:
    enabled = bool(getattr(options, "hover_confirm_target", False) or getattr(options, "hover_only", False))
    if not enabled:
        return None
    return HoverConfirmationOptions(
        enabled=True,
        hover_only=bool(getattr(options, "hover_only", False)),
        snapshot_url=str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
        timeout_ms=max(0, int(getattr(options, "hover_confirm_timeout_ms", 120) or 0)),
        poll_ms=max(1, int(getattr(options, "hover_poll_ms", 10) or 10)),
        tolerance_px=max(0, int(getattr(options, "hover_position_tolerance", 3) or 0)),
        click_hold_ms=max(0, int(getattr(options, "click_hold_ms", 0) or 0)),
        client_tick_debug=bool(getattr(options, "client_tick_debug", False)),
        client_tick_tail=max(0, int(getattr(options, "client_tick_tail", 0) or 0)),
        menu_entry_limit=max(0, int(getattr(options, "menu_entry_limit", 5) or 5)),
        require_clicked_menu_match=bool(getattr(options, "require_clicked_menu_match", False)),
    )


def _backend_move(backend: Any, plan: Any) -> None:
    mover = getattr(backend, "move", None)
    if callable(mover):
        mover(plan)
        return
    raise RuntimeError(f"backend does not support hover-only movement: {getattr(backend, 'name', backend.__class__.__name__)}")


def _backend_click_at(backend: Any, x: int, y: int, *, button: str = "left", hold_ms: int = 0) -> None:
    clicker = getattr(backend, "click_at", None)
    if callable(clicker):
        clicker(int(x), int(y), button=button, hold_ms=max(0, int(hold_ms)))
        return
    raise RuntimeError(f"backend does not support separated hover-confirm click: {getattr(backend, 'name', backend.__class__.__name__)}")


def _click_confirmed_current_position(
    input_controller: HumanInputController,
    *,
    button: str = "left",
    hold_ms: int = 0,
    context: Any | None = None,
) -> None:
    click_current = getattr(input_controller, "click_current_position", None)
    if callable(click_current):
        click_current(button=button, hold_ms=hold_ms, context=context)
        return
    hold = max(0, int(hold_ms or 0))
    with input_controller.hold_mouse_button(button=button, context=context):
        if hold > 0:
            input_controller.sleep_func(hold / 1000.0)


def _confirm_hover_menu(
    proposal: ActionProposal,
    hover_options: HoverConfirmationOptions,
    canvas_point: dict[str, int],
    *,
    move_started_wall_millis: int,
    max_requests: int | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> dict[str, Any]:
    started = float(monotonic_func())
    timeout_seconds = max(0.0, hover_options.timeout_ms / 1000.0)
    poll_seconds = max(0.001, hover_options.poll_ms / 1000.0)
    latest_match = HoverMenuMatchResult(False, "hover_menu_missing")
    latest_snapshot: dict[str, Any] | None = None
    request_count = 0
    last_error: str | None = None
    accepted_samples: list[dict[str, Any]] = []
    rejected_samples: list[dict[str, Any]] = []
    effective_client_tick_tail = hover_options.client_tick_tail
    if _is_navigation_path_proposal(proposal) and effective_client_tick_tail <= 0:
        effective_client_tick_tail = 5
    intent = client_tick_core.action_intent_from_proposal(
        proposal,
        tolerance_px=hover_options.tolerance_px,
        freshness_millis=120,
    )
    route_transition_relaxed_tolerance = max(hover_options.tolerance_px, 8)
    while True:
        request_count += 1
        try:
            latest_snapshot = snapshot_fetch_func(
                hover_options.snapshot_url,
                timeout=hover_options.request_timeout_seconds,
                client_tick_tail=effective_client_tick_tail,
                menu_entry_limit=hover_options.menu_entry_limit,
            )
            sample = _hover_menu_sample(latest_snapshot)
            latest_match = hover_menu_matches_target(
                sample,
                proposal,
                canvas_point,
                tolerance_px=hover_options.tolerance_px,
                min_wall_time_millis=move_started_wall_millis,
            )
            if latest_match.reason == "hover_menu_stale" and _is_navigation_path_proposal(proposal):
                stationary_match = hover_menu_matches_target(
                    sample,
                    proposal,
                    canvas_point,
                    tolerance_px=hover_options.tolerance_px,
                    min_wall_time_millis=None,
                )
                if stationary_match.confirmed:
                    latest_match = HoverMenuMatchResult(
                        True,
                        "stationary_navigation_hover_confirmed",
                        stationary_match.sample,
                        {
                            **stationary_match.details,
                            "staleSampleAccepted": True,
                            "staleSampleReason": "mouse_already_at_navigation_waypoint",
                            "minimumWallTimeMillis": move_started_wall_millis,
                        },
                    )
            elif latest_match.reason == "hover_menu_stale" and proposal.proposed_action in ROUTE_TRANSITION_ACTIONS:
                stationary_match = hover_menu_matches_target(
                    sample,
                    proposal,
                    canvas_point,
                    tolerance_px=hover_options.tolerance_px,
                    min_wall_time_millis=None,
                )
                if stationary_match.confirmed:
                    latest_match = HoverMenuMatchResult(
                        True,
                        "stationary_route_hover_confirmed",
                        stationary_match.sample,
                        {
                            **stationary_match.details,
                            "staleSampleAccepted": True,
                            "staleSampleReason": "mouse_already_at_route_transition_point",
                            "minimumWallTimeMillis": move_started_wall_millis,
                        },
                    )
                elif stationary_match.reason == "mouse_position_outside_tolerance":
                    relaxed_match = hover_menu_matches_target(
                        sample,
                        proposal,
                        canvas_point,
                        tolerance_px=route_transition_relaxed_tolerance,
                        min_wall_time_millis=None,
                    )
                    if relaxed_match.confirmed:
                        latest_match = HoverMenuMatchResult(
                            True,
                            "stationary_route_hover_confirmed",
                            relaxed_match.sample,
                            {
                                **relaxed_match.details,
                                "staleSampleAccepted": True,
                                "staleSampleReason": "mouse_already_near_route_transition_point",
                                "minimumWallTimeMillis": move_started_wall_millis,
                                "positionToleranceRelaxed": True,
                                "requestedTolerancePx": hover_options.tolerance_px,
                                "relaxedTolerancePx": route_transition_relaxed_tolerance,
                            },
                        )
            elif latest_match.reason == "mouse_position_outside_tolerance" and proposal.proposed_action in ROUTE_TRANSITION_ACTIONS:
                relaxed_match = hover_menu_matches_target(
                    sample,
                    proposal,
                    canvas_point,
                    tolerance_px=route_transition_relaxed_tolerance,
                    min_wall_time_millis=move_started_wall_millis,
                )
                if relaxed_match.confirmed:
                    latest_match = HoverMenuMatchResult(
                        True,
                        "route_hover_confirmed_position_relaxed",
                        relaxed_match.sample,
                        {
                            **relaxed_match.details,
                            "positionToleranceRelaxed": True,
                            "requestedTolerancePx": hover_options.tolerance_px,
                            "relaxedTolerancePx": route_transition_relaxed_tolerance,
                        },
                    )
            menu_volatility = (
                client_tick_core.menu_tail_volatility(
                    latest_snapshot,
                    canvas_point,
                    intent,
                    tolerance_px=hover_options.tolerance_px,
                )
                if _is_navigation_path_proposal(proposal)
                else None
            )
            if latest_match.confirmed:
                if latest_match.sample:
                    accepted_samples.append(dict(latest_match.sample))
                now_value = _safe_monotonic(monotonic_func)
                latency_ms = int(round(((now_value if now_value is not None else started) - started) * 1000))
                return {
                    "status": "PASS",
                    "confirmed": True,
                    "reason": latest_match.reason,
                    "latencyMillis": max(0, latency_ms),
                    "requestCount": request_count,
                    "expectedCanvasPoint": dict(canvas_point),
                    "positionTolerancePx": hover_options.tolerance_px,
                    "sample": latest_match.sample,
                    "matchDetails": latest_match.details,
                    "clientTickHot": client_tick_core.compact_hot_explanation(latest_snapshot),
                    "menuTailVolatility": menu_volatility,
                    "hoverConfirmationSamples": accepted_samples[-5:],
                    "rejectedHoverSamples": rejected_samples[-10:],
                    "lastMenuOptionClickedBefore": _last_menu_option_clicked_sample(latest_snapshot),
                }
            if latest_match.sample:
                rejected = latest_match.to_dict()
                rejected["requestNumber"] = request_count
                rejected_samples.append(rejected)
        except Exception as error:  # noqa: BLE001
            last_error = f"{type(error).__name__}: {error}"
            latest_match = HoverMenuMatchResult(False, "plugin_snapshot_unavailable", None, {"error": last_error})
        if max_requests is not None and max_requests > 0 and request_count >= max_requests:
            return {
                "status": "FAIL",
                "confirmed": False,
                "reason": "hover_confirm_request_limit",
                "latencyMillis": int(round(((_safe_monotonic(monotonic_func) or started) - started) * 1000)),
                "requestCount": request_count,
                "expectedCanvasPoint": dict(canvas_point),
                "positionTolerancePx": hover_options.tolerance_px,
                "latestMatch": latest_match.to_dict(),
                "latestHoverMenu": latest_match.sample,
                "clientTickHot": client_tick_core.compact_hot_explanation(latest_snapshot),
                "menuTailVolatility": (
                    client_tick_core.menu_tail_volatility(
                        latest_snapshot,
                        canvas_point,
                        intent,
                        tolerance_px=hover_options.tolerance_px,
                    )
                    if _is_navigation_path_proposal(proposal)
                    else None
                ),
                "hoverConfirmationSamples": accepted_samples[-5:],
                "rejectedHoverSamples": rejected_samples[-10:],
                "lastError": last_error,
                "lastMenuOptionClickedBefore": _last_menu_option_clicked_sample(latest_snapshot),
            }
        now_value = _safe_monotonic(monotonic_func)
        elapsed = (now_value - started) if now_value is not None else timeout_seconds
        if elapsed >= timeout_seconds:
            latency_ms = int(round(elapsed * 1000))
            return {
                "status": "FAIL",
                "confirmed": False,
                "reason": "hover_confirm_timeout",
                "latencyMillis": max(0, latency_ms),
                "requestCount": request_count,
                "expectedCanvasPoint": dict(canvas_point),
                "positionTolerancePx": hover_options.tolerance_px,
                "latestMatch": latest_match.to_dict(),
                "latestHoverMenu": latest_match.sample,
                "clientTickHot": client_tick_core.compact_hot_explanation(latest_snapshot),
                "menuTailVolatility": (
                    client_tick_core.menu_tail_volatility(
                        latest_snapshot,
                        canvas_point,
                        intent,
                        tolerance_px=hover_options.tolerance_px,
                    )
                    if _is_navigation_path_proposal(proposal)
                    else None
                ),
                "hoverConfirmationSamples": accepted_samples[-5:],
                "rejectedHoverSamples": rejected_samples[-10:],
                "lastError": last_error,
                "lastMenuOptionClickedBefore": _last_menu_option_clicked_sample(latest_snapshot),
            }
        sleep_func(poll_seconds)


def _is_navigation_path_proposal(proposal: ActionProposal) -> bool:
    return proposal.target_kind == "path_tile" and proposal.proposed_action in NAVIGATION_ACTIONS


def _is_service_object_proposal(proposal: ActionProposal) -> bool:
    return proposal.target_kind == "service" and proposal.proposed_action == "open_service"


def _is_resource_object_proposal(proposal: ActionProposal) -> bool:
    return proposal.target_kind == "resource" and proposal.proposed_action == "select_resource_target"


def _supports_structured_alternate_aimpoints(proposal: ActionProposal) -> bool:
    if _is_service_object_proposal(proposal):
        return True
    return proposal.target_kind == "service_route_object" and proposal.proposed_action == "interact_service_route_object"


def _service_alternate_aimpoints(proposal: ActionProposal, current_canvas_point: dict[str, Any] | None) -> list[dict[str, int]]:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    safe = explanation.get("safeAimPoint") if isinstance(explanation.get("safeAimPoint"), dict) else {}
    samples = safe.get("sampledAimpoints") if isinstance(safe.get("sampledAimpoints"), list) else []
    current_x = _int_or_none((current_canvas_point or {}).get("x"))
    current_y = _int_or_none((current_canvas_point or {}).get("y"))
    points: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        x = _int_or_none(sample.get("x", sample.get("canvasX")))
        y = _int_or_none(sample.get("y", sample.get("canvasY")))
        if x is None or y is None:
            continue
        key = (x, y)
        if key in seen or (current_x == x and current_y == y):
            continue
        seen.add(key)
        points.append({"x": x, "y": y})
    return points[:8]


def _proposal_with_service_aimpoint(proposal: ActionProposal, canvas_point: dict[str, int], screen_point: dict[str, int]) -> ActionProposal:
    candidate = deepcopy(proposal)
    candidate.suggested_click_point = dict(canvas_point)
    candidate.click_point_space = "canvas"
    candidate.resolved_screen_click_point = dict(screen_point)
    candidate.click_point_resolution = {
        "status": "PASS",
        "method": "service_alternate_safe_aimpoint",
        "screenClickPoint": dict(screen_point),
        "warnings": [],
        "missingCapabilities": [],
    }
    if isinstance(candidate.target_explanation, dict):
        safe = candidate.target_explanation.get("safeAimPoint")
        if isinstance(safe, dict):
            updated_safe = dict(safe)
            updated_safe["acceptedAimpoint"] = dict(canvas_point)
            updated_safe["canvasX"] = canvas_point["x"]
            updated_safe["canvasY"] = canvas_point["y"]
            updated_safe["source"] = "serviceAlternateSafeAimpoint"
            candidate.target_explanation["safeAimPoint"] = updated_safe
        candidate.target_explanation["serviceAlternateAimpoint"] = dict(canvas_point)
    return candidate


def _resource_required_level_from_name(name: Any) -> int | None:
    key = _menu_text_key(name)
    if not key:
        return None
    if "oak" in key:
        return 15
    if "willow" in key:
        return 30
    if "yew" in key:
        return 60
    if "magic" in key:
        return 75
    if "tree" in key:
        return 1
    return None


def _resource_level_met(required: int | None, player_level_known: bool | None, player_level: int | None) -> bool:
    if required is None:
        return False
    if required <= 1:
        return True
    if player_level_known is False or player_level is None:
        return False
    return player_level >= required


def _resource_hover_entry_summary(entry: dict[str, Any], *, player_level_known: bool | None, player_level: int | None) -> dict[str, Any]:
    target = entry.get("target") or entry.get("topTarget")
    required = _resource_required_level_from_name(target)
    met = _resource_level_met(required, player_level_known, player_level)
    return {
        "option": entry.get("option") or entry.get("topOption"),
        "target": target,
        "identifier": entry.get("identifier") if entry.get("identifier") is not None else entry.get("topIdentifier"),
        "type": entry.get("type") or entry.get("topType"),
        "entryIndex": entry.get("entryIndex"),
        "requiredSkill": "woodcutting" if required is not None else None,
        "requiredLevel": required,
        "playerLevelKnown": bool(player_level_known),
        "playerLevel": player_level,
        "levelRequirementMet": met,
        "visibleButNotExecutable": bool(required is not None and not met),
        "futureEligibleWhenLevelMet": bool(required is not None and not met),
    }


def _resource_target_ambiguity(
    proposal: ActionProposal,
    confirmation: dict[str, Any] | None,
    *,
    rejection_reason: str | None = None,
) -> dict[str, Any] | None:
    if not _is_resource_object_proposal(proposal):
        return None
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    sample = _confirmation_hover_sample(confirmation)
    latest_match = (confirmation or {}).get("latestMatch") if isinstance((confirmation or {}).get("latestMatch"), dict) else {}
    match_details = latest_match.get("details") if isinstance(latest_match.get("details"), dict) else {}
    candidate_name = proposal.target_name or explanation.get("name")
    required = _int_or_none(explanation.get("requiredLevel")) or _resource_required_level_from_name(candidate_name)
    player_level_known = explanation.get("playerLevelKnown")
    player_level = _int_or_none(explanation.get("playerLevel"))
    if player_level_known is None:
        player_level_known = player_level is not None
    level_met = _resource_level_met(required, bool(player_level_known), player_level)
    left_entry = client_tick_core.get_left_click_entry(sample)
    entries = client_tick_core.get_actionable_entries(sample)
    expected_lower = client_tick_core.expected_entries_not_top(
        sample,
        client_tick_core.action_intent_from_proposal(proposal),
    )
    top_summary = _resource_hover_entry_summary(left_entry or {}, player_level_known=bool(player_level_known), player_level=player_level) if left_entry else None
    entry_summaries = [
        _resource_hover_entry_summary(entry, player_level_known=bool(player_level_known), player_level=player_level)
        for entry in entries
    ]
    top_target = _menu_text_key((left_entry or {}).get("target") if isinstance(left_entry, dict) else sample.get("topTarget"))
    candidate_key = _menu_text_key(candidate_name)
    expected_present_not_top = bool(expected_lower or match_details.get("expectedEntryPresentButNotTop"))
    higher_level_top = bool(
        top_summary
        and top_summary.get("requiredLevel") is not None
        and required is not None
        and int(top_summary["requiredLevel"]) > int(required)
    )
    non_exec_top = bool(top_summary and top_summary.get("visibleButNotExecutable"))
    if (confirmation or {}).get("confirmed") is True:
        ambiguity_status = "clear"
        reason = None
    elif higher_level_top and ("oak" in top_target or non_exec_top):
        ambiguity_status = "ambiguous_overlap_with_higher_level_target"
        reason = "top hover is a higher-level resource than the selected candidate"
    elif expected_present_not_top:
        ambiguity_status = "ambiguous_expected_entry_not_top"
        reason = "expected resource entry is present below another left-click target"
    elif top_target and candidate_key and candidate_key not in top_target:
        ambiguity_status = "ambiguous_top_hover_mismatch"
        reason = "top hover target does not match selected resource candidate"
    elif entries and len(entries) > 1:
        ambiguity_status = "ambiguous_object_stack"
        reason = "multiple object actions are stacked at this aimpoint"
    else:
        ambiguity_status = "unsafe"
        reason = rejection_reason or str((confirmation or {}).get("reason") or "hover confirmation failed")
    return {
        "schema": "resource_target_ambiguity.v1",
        "candidateId": explanation.get("objectId") or explanation.get("id"),
        "candidateHash": explanation.get("hash"),
        "candidateName": candidate_name,
        "candidateWorldLocation": explanation.get("worldLocation") or explanation.get("world"),
        "expectedAction": "Chop down",
        "expectedTargetClass": "basic_tree" if required in (None, 1) else candidate_name,
        "requiredLevel": required,
        "playerLevelKnown": bool(player_level_known),
        "playerLevel": player_level,
        "levelRequirementMet": level_met,
        "topHoverOption": (left_entry or {}).get("option") if isinstance(left_entry, dict) else sample.get("topOption"),
        "topHoverTarget": (left_entry or {}).get("target") if isinstance(left_entry, dict) else sample.get("topTarget"),
        "lowerHoverEntries": entry_summaries[1:] if len(entry_summaries) > 1 else [],
        "expectedEntryPresentButNotTop": expected_present_not_top,
        "lowerMenuWouldWorkPotentially": expected_present_not_top,
        "rightClickResourceSelectionDeferred": expected_present_not_top,
        "overlappingCandidates": entry_summaries,
        "overlappingExecutableCandidates": [entry for entry in entry_summaries if entry.get("levelRequirementMet")],
        "overlappingNonExecutableCandidates": [entry for entry in entry_summaries if entry.get("visibleButNotExecutable")],
        "overlappingHigherLevelCandidates": [
            entry for entry in entry_summaries if required is not None and entry.get("requiredLevel") is not None and int(entry["requiredLevel"]) > int(required)
        ],
        "ambiguityStatus": ambiguity_status,
        "overlapPenaltyFromNonExecutableTarget": bool(non_exec_top or (higher_level_top and not level_met)),
        "rejectionReason": reason,
    }


def _resource_alternate_aimpoints(proposal: ActionProposal, current_canvas_point: dict[str, Any] | None) -> list[dict[str, int]]:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    safe = explanation.get("safeAimPoint") if isinstance(explanation.get("safeAimPoint"), dict) else {}
    samples = safe.get("sampledAimpoints") if isinstance(safe.get("sampledAimpoints"), list) else []
    current_x = _int_or_none((current_canvas_point or {}).get("x"))
    current_y = _int_or_none((current_canvas_point or {}).get("y"))
    points: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        x = _int_or_none(sample.get("x", sample.get("canvasX")))
        y = _int_or_none(sample.get("y", sample.get("canvasY")))
        if x is None or y is None:
            continue
        key = (x, y)
        if key in seen or (current_x == x and current_y == y):
            continue
        seen.add(key)
        points.append({"x": x, "y": y})
    return points[:7]


def _resource_ambiguity_recoverable(ambiguity: dict[str, Any] | None) -> bool:
    if not isinstance(ambiguity, dict):
        return False
    return str(ambiguity.get("ambiguityStatus") or "") in {
        "ambiguous_top_hover_mismatch",
        "ambiguous_overlap_with_higher_level_target",
        "ambiguous_expected_entry_not_top",
        "ambiguous_object_stack",
    }


def _proposal_with_resource_aimpoint(
    proposal: ActionProposal,
    canvas_point: dict[str, int],
    screen_point: dict[str, int],
    *,
    source: str = "alternate_hull_sample",
    method: str = "resource_alternate_hull_sample",
) -> ActionProposal:
    candidate = deepcopy(proposal)
    candidate.suggested_click_point = dict(canvas_point)
    candidate.click_point_space = "canvas"
    candidate.resolved_screen_click_point = dict(screen_point)
    candidate.click_point_resolution = {
        "status": "PASS",
        "method": method,
        "screenClickPoint": dict(screen_point),
        "warnings": [],
        "missingCapabilities": [],
    }
    if isinstance(candidate.target_explanation, dict):
        safe = candidate.target_explanation.get("safeAimPoint")
        if isinstance(safe, dict):
            updated_safe = dict(safe)
            updated_safe["acceptedAimpoint"] = dict(canvas_point)
            updated_safe["canvasX"] = canvas_point["x"]
            updated_safe["canvasY"] = canvas_point["y"]
            updated_safe["source"] = source
            candidate.target_explanation["safeAimPoint"] = updated_safe
        candidate.target_explanation["selectedAimpointSource"] = source
    return candidate


def _hover_sample_canvas_point(sample: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(sample, dict):
        return None
    x = _int_or_none(sample.get("mouseCanvasX"))
    y = _int_or_none(sample.get("mouseCanvasY"))
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def _try_resource_observed_hover_retarget(
    proposal: ActionProposal,
    confirmation: dict[str, Any] | None,
    *,
    backend: Any,
    hover_options: HoverConfirmationOptions,
) -> dict[str, Any] | None:
    if not _is_resource_object_proposal(proposal) or not isinstance(confirmation, dict):
        return None
    latest_match = confirmation.get("latestMatch") if isinstance(confirmation.get("latestMatch"), dict) else {}
    if latest_match.get("reason") != "mouse_position_outside_tolerance":
        return None
    sample = _confirmation_hover_sample(confirmation)
    observed_point = _hover_sample_canvas_point(sample)
    if observed_point is None:
        return {"accepted": False, "reason": "observed_hover_point_missing"}
    intent = client_tick_core.action_intent_from_proposal(proposal, tolerance_px=hover_options.tolerance_px)
    selected_entry = client_tick_core.get_left_click_entry(sample)
    if not client_tick_core.menu_entry_matches_intent(selected_entry, intent):
        return {
            "accepted": False,
            "reason": "observed_hover_top_not_expected",
            "observedCanvasPoint": dict(observed_point),
            "topOption": (selected_entry or {}).get("option"),
            "topTarget": (selected_entry or {}).get("target"),
        }
    screen_point = _screen_point_from_canvas_for_proposal(proposal, backend, observed_point)
    if not isinstance(screen_point, dict):
        return {"accepted": False, "reason": "observed_hover_screen_point_unavailable", "observedCanvasPoint": dict(observed_point)}
    candidate = _proposal_with_resource_aimpoint(
        proposal,
        observed_point,
        screen_point,
        source="hover_observed_same_target",
        method="resource_observed_hover_retarget",
    )
    match = hover_menu_matches_target(
        sample,
        candidate,
        observed_point,
        tolerance_px=hover_options.tolerance_px,
        min_wall_time_millis=None,
    )
    if not match.confirmed:
        return {
            "accepted": False,
            "reason": match.reason,
            "observedCanvasPoint": dict(observed_point),
            "latestMatch": match.to_dict(),
        }
    retarget_trace = {
        "schema": "resource_hover_observed_retarget.v1",
        "originalCanvasPoint": dict(confirmation.get("expectedCanvasPoint") or _canvas_click_point_for_hover(proposal) or {}),
        "observedCanvasPoint": dict(observed_point),
        "observedScreenPoint": dict(screen_point),
        "positionMismatch": dict((latest_match.get("details") or {}) if isinstance(latest_match.get("details"), dict) else {}),
        "topOption": (selected_entry or {}).get("option"),
        "topTarget": (selected_entry or {}).get("target"),
        "selectedAimpointSource": "hover_observed_same_target",
    }
    retarget_confirmation = {
        "status": "PASS",
        "confirmed": True,
        "reason": "resource_hover_confirmed_at_observed_point",
        "latencyMillis": confirmation.get("latencyMillis"),
        "requestCount": confirmation.get("requestCount"),
        "expectedCanvasPoint": dict(observed_point),
        "originalExpectedCanvasPoint": dict(retarget_trace["originalCanvasPoint"]),
        "positionTolerancePx": hover_options.tolerance_px,
        "sample": sample,
        "matchDetails": dict(match.details),
        "latestMatch": match.to_dict(),
        "clientTickHot": confirmation.get("clientTickHot"),
        "hoverConfirmationSamples": [dict(sample)] if isinstance(sample, dict) else [],
        "rejectedHoverSamples": list(confirmation.get("rejectedHoverSamples") or []),
        "lastMenuOptionClickedBefore": confirmation.get("lastMenuOptionClickedBefore"),
        "resourceHoverRetarget": retarget_trace,
    }
    if isinstance(candidate.target_explanation, dict):
        candidate.target_explanation["hoverConfirmedTopExpected"] = True
        candidate.target_explanation["resourceHoverRetarget"] = dict(retarget_trace)
        candidate.target_explanation["resourceTargetAmbiguity"] = _resource_target_ambiguity(candidate, retarget_confirmation) or {
            "schema": "resource_target_ambiguity.v1",
            "ambiguityStatus": "clear",
        }
    return {
        "accepted": True,
        "reason": "resource_hover_confirmed_at_observed_point",
        "proposal": candidate,
        "screenPoint": screen_point,
        "canvasPoint": observed_point,
        "confirmation": retarget_confirmation,
    }


def _max_waypoint_alternates(options: Any | None) -> int:
    if options is None:
        return 12
    return max(0, int(getattr(options, "max_waypoint_alternates", 12) or 0))


def _max_hover_checks_per_waypoint(options: Any | None) -> int | None:
    if options is None:
        return None
    value = int(getattr(options, "max_hover_checks_per_waypoint", 0) or 0)
    return value if value > 0 else None


def _max_navigation_reacquire_rounds(options: Any | None) -> int:
    if options is None:
        return 1
    return max(0, int(getattr(options, "max_navigation_reacquire_rounds", 1) or 0))


def _navigation_hover_failure_reason(proposal: ActionProposal, confirmation: dict[str, Any] | None) -> str:
    if not _is_navigation_path_proposal(proposal):
        return str((confirmation or {}).get("reason") or "hover_mismatch")
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    latest_match = confirmation.get("latestMatch") if isinstance(confirmation.get("latestMatch"), dict) else {}
    latest_details = latest_match.get("details") if isinstance(latest_match.get("details"), dict) else {}
    structured_reason = str(latest_details.get("mismatchReason") or "")
    raw_reason = str(latest_match.get("reason") or confirmation.get("reason") or "")
    if structured_reason in {"hover_position_mismatch", "stale_hover_sample"}:
        return structured_reason
    reason_map = {
        "hover_menu_stale": "stale_hover_sample",
        "mouse_position_outside_tolerance": "hover_position_mismatch",
        "top_option_not_expected": "hover_option_mismatch",
        "top_option_not_chop": "hover_option_mismatch",
        "top_option_rejected": "hover_option_mismatch",
        "top_target_not_expected": "hover_target_mismatch",
        "top_type_not_allowed": "wrong_intent_matcher",
    }
    if raw_reason in {"hover_menu_stale", "mouse_position_outside_tolerance"}:
        return reason_map[raw_reason]
    samples: list[dict[str, Any]] = []
    for sample in (
        confirmation.get("latestHoverMenu"),
        latest_match.get("sample"),
        confirmation.get("sample"),
    ):
        if isinstance(sample, dict):
            samples.append(sample)
    for key in ("rejectedHoverSamples", "hoverConfirmationSamples"):
        for item in confirmation.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("sample"), dict):
                samples.append(item["sample"])
            elif isinstance(item, dict):
                samples.append(item)
    classes = [client_tick_core.classify_menu_action(sample) for sample in samples]
    if "object_action" in classes:
        return "waypoint_occluded_by_object"
    if "walk_here" in classes:
        return "walk_here_hover_mismatch"
    if "cancel_hover" in classes:
        return "cancel_hover"
    if structured_reason:
        return structured_reason
    if raw_reason in reason_map:
        return reason_map[raw_reason]
    return str(confirmation.get("reason") or latest_match.get("reason") or "hover_mismatch")


def _navigation_projection_failure_reason(
    proposal: ActionProposal,
    projection_warnings: list[str] | None,
    navigation_options: Any | None,
) -> str | None:
    if not _is_navigation_path_proposal(proposal):
        return None
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    status = explanation.get("routeProjectionStatus") if isinstance(explanation.get("routeProjectionStatus"), dict) else {}
    classification = str(status.get("classification") or "")
    rejection = str(status.get("rejectionReason") or " ".join(str(item) for item in (projection_warnings or [])))
    if classification == "edge_clipped" or "viewport edge" in rejection or "canvas edge" in rejection or "visible area ratio" in rejection:
        return "waypoint_edge_projection"
    if classification in {"offscreen", "degenerate", "tiny_projection", "no_projection", "not_actionable"}:
        return f"waypoint_{classification}"
    return None


def _can_camera_reacquire_projection_failure(
    proposal: ActionProposal,
    projection_warnings: list[str] | None,
    navigation_options: Any | None,
) -> str | None:
    reason = _navigation_projection_failure_reason(proposal, projection_warnings, navigation_options)
    if reason != "waypoint_edge_projection":
        return None
    if not _camera_reacquire_on_edge_projection(navigation_options):
        return None
    if not _camera_reacquire_waypoint_enabled(navigation_options):
        return None
    return reason


def _camera_trigger_from_navigation_failure(reason: str | None) -> str | None:
    value = str(reason or "")
    if value == "waypoint_edge_projection":
        return "edge_projection"
    if value == "screen_point_outside_movement_safety_region":
        return "movement_safety_region_edge"
    if value == "waypoint_occluded_by_object":
        return "object_occlusion"
    if value == "waypoint_offscreen":
        return "offscreen_projection"
    if value == "waypoint_tiny_projection":
        return "tiny_projection"
    if value in {"waypoint_degenerate", "waypoint_no_projection", "waypoint_not_actionable"}:
        return "poor_projection"
    return None


def _navigation_volatile_hover_zone(proposal: ActionProposal, confirmation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not _is_navigation_path_proposal(proposal) or not isinstance(confirmation, dict):
        return None
    volatility = confirmation.get("menuTailVolatility")
    if isinstance(volatility, dict) and volatility.get("volatileHoverZone") is True:
        return volatility
    return None


def _hover_menu_label(confirmation: dict[str, Any] | None) -> str:
    sample = _confirmation_hover_sample(confirmation)
    option = sample.get("topOption") or sample.get("option") or "unknown"
    target = sample.get("topTarget") or sample.get("target") or ""
    return f"{option} {target}".strip()


def _confirmation_hover_sample(confirmation: dict[str, Any] | None) -> dict[str, Any]:
    confirmation = confirmation if isinstance(confirmation, dict) else {}
    latest_match = confirmation.get("latestMatch") if isinstance(confirmation.get("latestMatch"), dict) else {}
    sample = confirmation.get("latestHoverMenu") if isinstance(confirmation.get("latestHoverMenu"), dict) else latest_match.get("sample")
    if not isinstance(sample, dict):
        sample = confirmation.get("sample") if isinstance(confirmation.get("sample"), dict) else {}
    return sample


def _camera_adjustment_direction(options: Any | None) -> str:
    raw = str(getattr(options, "camera_adjust_direction", "auto") if options is not None else "auto" or "auto").lower()
    if raw in {"left", "right"}:
        return raw
    # Deterministic default; this is a bounded reacquisition nudge, not pathing logic.
    return "right"


def _camera_reacquire_waypoint_enabled(options: Any | None) -> bool:
    return bool(getattr(options, "camera_reacquire_waypoint", False) if options is not None else False)


def _camera_max_nudges(options: Any | None) -> int:
    if options is None:
        return 0
    value = getattr(options, "camera_max_nudges", None)
    if value is None:
        value = getattr(options, "max_camera_adjustments_per_route_step", 0)
    return max(0, int(value or 0))


def _camera_probe_ms(options: Any | None) -> int:
    if options is None:
        return 120
    value = getattr(options, "camera_sample_interval_ms", None)
    if value is not None:
        return max(1, int(value or 1))
    value = getattr(options, "camera_probe_ms", None)
    if value is None:
        value = getattr(options, "camera_reacquire_ms", 120)
    return max(1, int(value or 1))


def _camera_reacquire_timeout_ms(options: Any | None) -> int:
    if options is None:
        return 0
    value = getattr(options, "camera_exposure_max_ms", None)
    if value is None or int(value or 0) <= 0:
        value = getattr(options, "camera_reacquire_timeout_ms", 0)
    return max(0, int(value or 0))


def _camera_method(options: Any | None) -> str:
    if options is None:
        return "auto"
    return camera_control.normalize_camera_method(getattr(options, "camera_method", "auto"))


def _camera_max_direction_switches(options: Any | None) -> int:
    if options is None:
        return 2
    value = getattr(options, "camera_max_direction_switches", None)
    if value is None:
        value = getattr(options, "camera_max_nudges", 2)
    return max(0, int(value or 0))


def _camera_allow_diagonal(options: Any | None) -> bool:
    return bool(getattr(options, "camera_allow_diagonal", False) if options is not None else False)


def _camera_min_projection_delta_px(options: Any | None) -> float:
    if options is None:
        return 2.0
    return max(0.0, float(getattr(options, "camera_min_projection_delta_px", 2) or 0))


def _camera_min_score_improvement(options: Any | None) -> int:
    if options is None:
        return 1
    return max(0, int(getattr(options, "camera_min_score_improvement", 1) or 0))


def _camera_follow_target(options: Any | None) -> bool:
    if options is None:
        return True
    return bool(getattr(options, "camera_follow_target", True))


def _camera_allow_pitch_adjust(options: Any | None) -> bool:
    return bool(getattr(options, "camera_allow_pitch_adjust", False) if options is not None else False)


def _proposal_projection(proposal: ActionProposal) -> dict[str, Any] | None:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    projection = explanation.get("tileProjection") if isinstance(explanation.get("tileProjection"), dict) else None
    if isinstance(projection, dict):
        return projection
    resolution = proposal.click_point_resolution if isinstance(proposal.click_point_resolution, dict) else {}
    projection = resolution.get("tileProjection") if isinstance(resolution.get("tileProjection"), dict) else None
    return projection


def _project_navigation_tile(
    proposal: ActionProposal,
    *,
    backend: Any,
    snapshot_url: str,
    navigation_options: Any | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    menu_entry_limit: int = 5,
) -> tuple[ActionProposal | None, dict[str, int] | None, dict[str, int] | None, dict[str, Any] | None, list[str], dict[str, Any] | None]:
    tile = proposal.target_tile if isinstance(proposal.target_tile, dict) else {}
    request = _tile_projection_request_from_tile(
        proposal,
        tile,
        label=f"{proposal.target_name or proposal.proposed_action} camera-follow",
    )
    if request is None:
        return None, None, None, None, ["navigation camera reacquire target tile missing"], None
    try:
        snapshot = snapshot_fetch_func(
            snapshot_url,
            timeout=0.35,
            tile_projection_requests=[request],
            menu_entry_limit=menu_entry_limit,
        )
    except Exception as error:  # noqa: BLE001
        return None, None, None, None, [f"navigation camera tile projection unavailable: {type(error).__name__}: {error}"], None
    projection = _matching_tile_projection(snapshot, request)
    if not isinstance(projection, dict):
        return None, None, None, None, ["navigation camera tile projection missing from plugin snapshot response"], snapshot
    viewport = _camera_viewport_from_snapshot(snapshot)
    if viewport is not None:
        projection = dict(projection)
        projection["cameraViewport"] = viewport
    candidate = deepcopy(proposal)
    candidate.suggested_click_point = None
    candidate.resolved_screen_click_point = None
    candidate.click_point_resolution = None
    candidate, projection_warnings = _apply_path_tile_projection(candidate, projection, backend=backend, navigation_options=navigation_options)
    if projection_warnings:
        return candidate, None, None, projection, projection_warnings, snapshot
    screen_point, coordinate_warnings, click_resolution = _screen_click_point(candidate, backend)
    if click_resolution:
        candidate.click_point_resolution = click_resolution
    if coordinate_warnings or not screen_point:
        return candidate, None, None, projection, coordinate_warnings or ["navigation camera screen point unavailable"], snapshot
    canvas_point = _canvas_click_point_for_hover(candidate)
    if not canvas_point:
        return candidate, screen_point, None, projection, ["navigation camera canvas point unavailable"], snapshot
    return candidate, screen_point, canvas_point, projection, [], snapshot


def _try_navigation_camera_guided_reacquire(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile,
    hover_options: HoverConfirmationOptions,
    snapshot_url: str,
    navigation_options: Any | None,
    input_controller: HumanInputController,
    primary_confirmation: dict[str, Any] | None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
) -> dict[str, Any] | None:
    if not _is_navigation_path_proposal(proposal) or not _camera_reacquire_waypoint_enabled(navigation_options):
        return None
    max_directions = _camera_max_direction_switches(navigation_options)
    if max_directions <= 0:
        return {"accepted": False, "attempts": [], "warning": "camera reacquire requested with zero direction switches"}
    tile = proposal.target_tile if isinstance(proposal.target_tile, dict) else {}
    target_world_tile = {
        "worldX": _int_or_none(tile.get("worldX")),
        "worldY": _int_or_none(tile.get("worldY")),
        "plane": _int_or_none(tile.get("plane")),
    }
    if target_world_tile["worldX"] is None or target_world_tile["worldY"] is None:
        return {"accepted": False, "attempts": [], "warning": "navigation camera reacquire target world tile missing"}
    attempts: list[dict[str, Any]] = []
    current_canvas = _canvas_click_point_for_hover(proposal)
    current_score = camera_exposure_score(
        hover_sample=_confirmation_hover_sample(primary_confirmation),
        canvas_point=current_canvas,
        projection=_proposal_projection(proposal),
        tolerance_px=hover_options.tolerance_px,
        target_world_tile=target_world_tile,
    )
    timeout_ms = _camera_reacquire_timeout_ms(navigation_options)
    deadline = float(monotonic_func()) + (timeout_ms / 1000.0) if timeout_ms > 0 else None
    sample_interval_ms = _camera_probe_ms(navigation_options)
    min_projection_delta_px = _camera_min_projection_delta_px(navigation_options)
    min_score_improvement = _camera_min_score_improvement(navigation_options)
    preferred_direction = _camera_adjustment_direction(navigation_options)
    base_commands = ["yaw_right", "yaw_left"] if preferred_direction == "right" else ["yaw_left", "yaw_right"]
    if _camera_allow_diagonal(navigation_options) and _camera_allow_pitch_adjust(navigation_options):
        base_commands = [base_commands[0] + "_pitch_up", base_commands[0], base_commands[1] + "_pitch_up", base_commands[1]]
    elif _camera_allow_pitch_adjust(navigation_options):
        base_commands = [base_commands[0], base_commands[1], "pitch_up"]
    commands = list(base_commands)
    methods = camera_control.camera_method_sequence(_camera_method(navigation_options))
    current_viewport = None
    current_projection = _proposal_projection(proposal)
    if isinstance(current_projection, dict):
        current_viewport = current_projection.get("cameraViewport") if isinstance(current_projection.get("cameraViewport"), dict) else None

    for method in methods:
        for attempt_index, command in enumerate(commands, start=1):
            if len(attempts) >= max_directions:
                return {"accepted": False, "attempts": attempts}
            if deadline is not None and float(monotonic_func()) >= deadline:
                return {"accepted": False, "attempts": attempts, "warning": "camera reacquire timeout elapsed"}
            spec = camera_control.camera_input_spec(method=method, command=command)
            attempt: dict[str, Any] = {
                "schema": "camera_exposure_attempt.v2",
                "attemptIndex": len(attempts) + 1,
                "directionIndex": attempt_index,
                "cameraMethod": spec.method,
                "cameraCommand": spec.command,
                "cameraAction": spec.command,
                "cameraKeys": list(spec.keys),
                "targetWorldTile": dict(target_world_tile),
                "projectedCanvasBefore": dict(current_canvas) if isinstance(current_canvas, dict) else None,
                "cameraViewportBefore": dict(current_viewport) if isinstance(current_viewport, dict) else None,
                "exposureScoreBefore": dict(current_score),
                "samples": [],
                "cameraMoved": False,
                "accepted": False,
            }
            start_time = float(monotonic_func())
            best_score = int(current_score.get("score") or 0)
            best_sample_score = best_score
            samples_without_improvement = 0

            def sample_after_camera() -> tuple[bool, dict[str, Any] | None]:
                nonlocal current_score, current_canvas, current_viewport, current_projection, best_sample_score, samples_without_improvement
                candidate, screen_point, canvas_point, projection, warnings, snapshot = _project_navigation_tile(
                    proposal,
                    backend=backend,
                    snapshot_url=snapshot_url,
                    navigation_options=navigation_options,
                    snapshot_fetch_func=snapshot_fetch_func,
                    menu_entry_limit=hover_options.menu_entry_limit,
                )
                camera_viewport = _camera_viewport_from_snapshot(snapshot)
                if isinstance(projection, dict):
                    current_projection = projection
                if isinstance(camera_viewport, dict):
                    current_viewport = camera_viewport
                sample: dict[str, Any] = {
                    "sampleIndex": len(attempt["samples"]) + 1,
                    "wallTimeMillis": int(wall_time_millis_func()),
                    "cameraViewport": dict(camera_viewport) if isinstance(camera_viewport, dict) else None,
                    "projection": dict(projection) if isinstance(projection, dict) else None,
                    "projectedCanvasPoint": dict(canvas_point) if isinstance(canvas_point, dict) else None,
                    "warnings": list(warnings),
                }
                if warnings or candidate is None or screen_point is None or canvas_point is None:
                    score = camera_exposure_score(
                        hover_sample=None,
                        canvas_point=canvas_point,
                        projection=projection,
                        tolerance_px=hover_options.tolerance_px,
                        target_world_tile=target_world_tile,
                        previous_canvas_point=current_canvas,
                        previous_camera_viewport=attempt.get("cameraViewportBefore"),
                    )
                    sample["exposureScore"] = dict(score)
                    sample["reason"] = "projection_not_actionable"
                    attempt["samples"].append(sample)
                    current_score = score
                    return False, None
                start = MousePoint(*_backend_position(backend))
                plan = input_controller.plan_mouse_movement(
                    start,
                    _target_from_click(screen_point),
                    movement_profile,
                    context=_human_context(candidate, "camera_follow_target"),
                )
                sample["screenPoint"] = dict(screen_point)
                sample["canvasPoint"] = dict(canvas_point)
                sample["movementPlan"] = plan.to_dict(include_points=False)
                if plan.validation_status == "FAIL":
                    score = camera_exposure_score(
                        hover_sample=None,
                        canvas_point=canvas_point,
                        projection=projection,
                        tolerance_px=hover_options.tolerance_px,
                        target_world_tile=target_world_tile,
                        previous_canvas_point=current_canvas,
                        previous_camera_viewport=attempt.get("cameraViewportBefore"),
                    )
                    sample["exposureScore"] = dict(score)
                    sample["reason"] = "movement_plan_invalid"
                    sample["warnings"] = list(plan.warnings)
                    attempt["samples"].append(sample)
                    current_score = score
                    return False, None
                move_started_wall_millis = int(wall_time_millis_func())
                try:
                    if _camera_follow_target(navigation_options):
                        input_controller.move_mouse(plan, context=_human_context(candidate, "camera_follow_target"))
                except Exception as error:  # noqa: BLE001
                    sample["reason"] = "hover_movement_failed"
                    sample["warnings"] = [f"{type(error).__name__}: {error}"]
                    attempt["samples"].append(sample)
                    raise
                confirmation = _confirm_hover_menu(
                    candidate,
                    hover_options,
                    canvas_point,
                    move_started_wall_millis=move_started_wall_millis,
                    max_requests=1,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                )
                hover_sample = _confirmation_hover_sample(confirmation)
                score = camera_exposure_score(
                    hover_sample=hover_sample,
                    canvas_point=canvas_point,
                    projection=projection,
                    tolerance_px=hover_options.tolerance_px,
                    target_world_tile=target_world_tile,
                    previous_canvas_point=current_canvas,
                    previous_camera_viewport=attempt.get("cameraViewportBefore"),
                )
                projection_delta = score.get("projectionDeltaPx")
                camera_moved = bool(
                    abs(int(score.get("yawDelta") or 0)) > 0
                    or abs(int(score.get("pitchDelta") or 0)) > 0
                    or (projection_delta is not None and float(projection_delta) >= min_projection_delta_px)
                )
                attempt["cameraMoved"] = bool(attempt.get("cameraMoved") or camera_moved)
                sample["cameraMoved"] = camera_moved
                sample["hoverAfterCamera"] = {
                    "status": confirmation.get("status"),
                    "confirmed": confirmation.get("confirmed"),
                    "reason": confirmation.get("reason"),
                    "latencyMillis": confirmation.get("latencyMillis"),
                    "topOption": hover_sample.get("topOption"),
                    "topTarget": hover_sample.get("topTarget"),
                }
                sample["projectionFollowErrorPx"] = {
                    "dx": score.get("mouseDeltaX"),
                    "dy": score.get("mouseDeltaY"),
                }
                sample["exposureScore"] = dict(score)
                attempt["samples"].append(sample)
                current_score = score
                current_canvas = canvas_point
                if isinstance(camera_viewport, dict):
                    current_viewport = camera_viewport
                if int(score.get("score") or 0) >= best_sample_score + min_score_improvement:
                    best_sample_score = int(score.get("score") or 0)
                    samples_without_improvement = 0
                else:
                    samples_without_improvement += 1
                if confirmation.get("confirmed") is True:
                    attempt["accepted"] = True
                    attempt["reason"] = "exposed_walk_here"
                    attempt["exposureScoreAfter"] = dict(score)
                    attempt["projectedCanvasAfter"] = dict(canvas_point)
                    attempt["cameraViewportAfter"] = dict(camera_viewport) if isinstance(camera_viewport, dict) else None
                    attempts.append(attempt)
                    if isinstance(candidate.target_explanation, dict):
                        camera = candidate.target_explanation.setdefault("cameraReacquisition", {})
                        camera["targetWorldTile"] = dict(target_world_tile)
                        camera["acceptedAimPoint"] = dict(canvas_point)
                        camera["reason"] = "waypoint_exposed_by_camera"
                    return True, {
                        "proposal": candidate,
                        "screenPoint": screen_point,
                        "canvasPoint": canvas_point,
                        "movementPlan": plan,
                        "confirmation": confirmation,
                        "moveStartedWallMillis": move_started_wall_millis,
                    }
                return False, None

            try:
                if spec.continuous_hover:
                    with input_controller.hold_keys(spec.keys, context=HumanInputContext(reason="camera_expose_waypoint", action_intent_type="navigation_waypoint_action")):
                        while True:
                            sleep_func(sample_interval_ms / 1000.0)
                            accepted, payload = sample_after_camera()
                            if accepted and payload is not None:
                                return {"accepted": True, "attempts": attempts, **payload}
                            elapsed_ms = (float(monotonic_func()) - start_time) * 1000.0
                            if not attempt.get("cameraMoved") and elapsed_ms >= max(60, sample_interval_ms * 2):
                                attempt["reason"] = "no_camera_delta"
                                break
                            if attempt.get("cameraMoved") and samples_without_improvement >= 3:
                                attempt["reason"] = "worsening" if best_sample_score < best_score + min_score_improvement else "no_exposure_improvement"
                                break
                            if deadline is not None and float(monotonic_func()) >= deadline:
                                attempt["reason"] = "timeout"
                                break
                else:
                    input_controller.camera_drag_pulse(spec, duration_ms=sample_interval_ms)
                    accepted, payload = sample_after_camera()
                    if accepted and payload is not None:
                        return {"accepted": True, "attempts": attempts, **payload}
                    if not attempt.get("cameraMoved"):
                        attempt["reason"] = "no_camera_delta"
            except Exception as error:  # noqa: BLE001
                attempt["reason"] = "camera_reacquire_failed"
                attempt["warning"] = f"{type(error).__name__}: {error}"
                attempts.append(attempt)
                return {"accepted": False, "attempts": attempts, "warning": attempt["warning"]}
            if "exposureScoreAfter" not in attempt:
                attempt["exposureScoreAfter"] = dict(current_score)
            if "projectedCanvasAfter" not in attempt:
                attempt["projectedCanvasAfter"] = dict(current_canvas) if isinstance(current_canvas, dict) else None
            if "cameraViewportAfter" not in attempt:
                attempt["cameraViewportAfter"] = dict(current_viewport) if isinstance(current_viewport, dict) else None
            if "reason" not in attempt:
                attempt["reason"] = "camera_reacquire_failed"
            attempts.append(attempt)
            if deadline is not None and float(monotonic_func()) >= deadline:
                return {"accepted": False, "attempts": attempts, "warning": "camera reacquire timeout elapsed"}
    return {"accepted": False, "attempts": attempts}


def _try_navigation_camera_adjustment(
    proposal: ActionProposal,
    *,
    backend: Any,
    navigation_options: Any | None,
    sleep_func: Any,
    input_controller: HumanInputController | None = None,
) -> dict[str, Any] | None:
    if not _is_navigation_path_proposal(proposal):
        return None
    max_adjustments = max(0, int(getattr(navigation_options, "max_camera_adjustments_per_route_step", 0) or 0))
    if max_adjustments <= 0:
        return None
    direction = _camera_adjustment_direction(navigation_options)
    duration_ms = max(0, int(getattr(navigation_options, "camera_adjust_ms", 0) or 0))
    reacquire_ms = max(0, int(getattr(navigation_options, "camera_reacquire_ms", 0) or 0))
    event = {
        "cameraAdjustmentAttempted": True,
        "cameraAdjustmentDirection": direction,
        "cameraAdjustmentDurationMs": duration_ms,
        "reason": "waypoint_occluded_by_object",
        "boundedMaxAdjustments": max_adjustments,
    }
    presser = getattr(backend, "press", None)
    if not callable(presser):
        event["status"] = "SKIPPED"
        event["warning"] = "backend does not support camera key adjustment"
        return event
    key = "left" if direction == "left" else "right"
    try:
        if input_controller is not None:
            with input_controller.hold_keys((key,), context=HumanInputContext(reason="legacy_camera_adjustment", action_intent_type="navigation_waypoint_action")):
                if duration_ms > 0:
                    sleep_func(duration_ms / 1000.0)
        else:
            presser(key)
            if duration_ms > 0:
                sleep_func(duration_ms / 1000.0)
        if reacquire_ms > 0:
            sleep_func(reacquire_ms / 1000.0)
        event["status"] = "PASS"
    except Exception as error:  # noqa: BLE001
        event["status"] = "FAIL"
        event["warning"] = f"camera adjustment failed: {type(error).__name__}: {error}"
    return event


def _try_navigation_alternate_hover(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile,
    hover_options: HoverConfirmationOptions,
    snapshot_url: str,
    navigation_options: Any | None = None,
    input_controller: HumanInputController | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
) -> dict[str, Any] | None:
    if not _is_navigation_path_proposal(proposal):
        return None
    attempts: list[dict[str, Any]] = []
    try:
        seed_snapshot = snapshot_fetch_func(
            snapshot_url,
            timeout=hover_options.request_timeout_seconds,
            menu_entry_limit=hover_options.menu_entry_limit,
        )
    except Exception as error:  # noqa: BLE001
        return {
            "accepted": False,
            "attempts": attempts,
            "warning": f"navigation alternate seed snapshot unavailable: {type(error).__name__}: {error}",
        }
    requests = _navigation_alternate_tile_requests(
        proposal,
        seed_snapshot,
        max_requests=_max_waypoint_alternates(navigation_options),
        navigation_options=navigation_options,
    )
    if not requests:
        return {"accepted": False, "attempts": attempts}
    try:
        projection_snapshot = snapshot_fetch_func(
            snapshot_url,
            timeout=0.35,
            tile_projection_requests=requests,
            menu_entry_limit=hover_options.menu_entry_limit,
        )
    except Exception as error:  # noqa: BLE001
        return {
            "accepted": False,
            "attempts": attempts,
            "warning": f"navigation alternate tile projections unavailable: {type(error).__name__}: {error}",
        }

    for request in requests:
        projection = _matching_tile_projection(projection_snapshot, request)
        attempt: dict[str, Any] = {"request": dict(request), "accepted": False}
        if not isinstance(projection, dict):
            attempt["reason"] = "projection_missing"
            attempts.append(attempt)
            continue
        candidate = deepcopy(proposal)
        candidate.target_tile = {
            "worldX": _int_or_none(request.get("worldX")),
            "worldY": _int_or_none(request.get("worldY")),
            "plane": _int_or_none(request.get("plane")),
        }
        candidate.suggested_world_tile = candidate.target_tile
        candidate.suggested_click_point = None
        candidate.resolved_screen_click_point = None
        candidate.click_point_resolution = None
        candidate, projection_warnings = _apply_path_tile_projection(candidate, projection, backend=backend, navigation_options=navigation_options)
        attempt["projection"] = dict(projection)
        if projection_warnings:
            attempt["reason"] = "projection_not_actionable"
            attempt["warnings"] = list(projection_warnings)
            attempts.append(attempt)
            continue
        screen_point, coordinate_warnings, click_resolution = _screen_click_point(candidate, backend)
        if click_resolution:
            candidate.click_point_resolution = click_resolution
        if coordinate_warnings or not screen_point:
            attempt["reason"] = "screen_point_unavailable"
            attempt["warnings"] = list(coordinate_warnings)
            attempts.append(attempt)
            continue
        canvas_point = _canvas_click_point_for_hover(candidate)
        if not canvas_point:
            attempt["reason"] = "canvas_point_unavailable"
            attempts.append(attempt)
            continue
        start = MousePoint(*_backend_position(backend))
        if input_controller is None:
            input_controller = HumanInputController(backend, profile="instant_debug", sleep_func=sleep_func, monotonic_func=monotonic_func)
        plan = input_controller.plan_mouse_movement(
            start,
            _target_from_click(screen_point),
            movement_profile,
            context=_human_context(candidate, "navigation_alternate_waypoint"),
        )
        attempt["screenPoint"] = dict(screen_point)
        attempt["canvasPoint"] = dict(canvas_point)
        attempt["movementPlan"] = plan.to_dict(include_points=False)
        if plan.validation_status == "FAIL":
            attempt["reason"] = "movement_plan_invalid"
            attempt["warnings"] = list(plan.warnings)
            attempts.append(attempt)
            continue
        move_started_wall_millis = int(wall_time_millis_func())
        try:
            input_controller.move_mouse(plan, context=_human_context(candidate, "navigation_alternate_waypoint"))
        except Exception as error:  # noqa: BLE001
            attempt["reason"] = "hover_movement_failed"
            attempt["warnings"] = [f"{type(error).__name__}: {error}"]
            attempts.append(attempt)
            continue
        confirmation = _confirm_hover_menu(
            candidate,
            hover_options,
            canvas_point,
            move_started_wall_millis=move_started_wall_millis,
            max_requests=_max_hover_checks_per_waypoint(navigation_options),
            snapshot_fetch_func=snapshot_fetch_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
        )
        hover_sample = _confirmation_hover_sample(confirmation)
        attempt["hoverConfirmation"] = {
            "status": confirmation.get("status"),
            "confirmed": confirmation.get("confirmed"),
            "reason": confirmation.get("reason"),
            "latencyMillis": confirmation.get("latencyMillis"),
            "topOption": hover_sample.get("topOption"),
            "topTarget": hover_sample.get("topTarget"),
        }
        if confirmation.get("confirmed") is True:
            volatility = _navigation_volatile_hover_zone(candidate, confirmation)
            if volatility is not None:
                attempt["reason"] = "volatile_hover_zone"
                attempt["volatileHoverZone"] = True
                attempt["volatileReasons"] = list(volatility.get("volatileReasons") or [])
                attempt["recentMenuTail"] = list(volatility.get("recentMenuTail") or [])
                attempts.append(attempt)
                continue
            attempt["accepted"] = True
            attempts.append(attempt)
            if isinstance(candidate.target_explanation, dict):
                candidate.target_explanation.setdefault("navigationReacquisition", {})["selectedAlternateWaypoint"] = dict(candidate.target_tile or {})
                candidate.target_explanation["navigationReacquisition"]["reason"] = "primary_waypoint_hover_mismatch"
            return {
                "accepted": True,
                "attempts": attempts,
                "proposal": candidate,
                "screenPoint": screen_point,
                "canvasPoint": canvas_point,
                "movementPlan": plan,
                "confirmation": confirmation,
                "moveStartedWallMillis": move_started_wall_millis,
            }
        attempt["reason"] = _navigation_hover_failure_reason(candidate, confirmation)
        attempts.append(attempt)
    return {"accepted": False, "attempts": attempts}


def _try_service_alternate_hover(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile,
    hover_options: HoverConfirmationOptions,
    input_controller: HumanInputController | None = None,
    current_canvas_point: dict[str, Any] | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
) -> dict[str, Any] | None:
    if not _supports_structured_alternate_aimpoints(proposal):
        return None
    attempts: list[dict[str, Any]] = []
    for canvas_point in _service_alternate_aimpoints(proposal, current_canvas_point):
        screen_point = _screen_point_from_canvas_for_proposal(proposal, backend, canvas_point)
        attempt: dict[str, Any] = {"canvasPoint": dict(canvas_point), "accepted": False}
        if not isinstance(screen_point, dict):
            attempt["reason"] = "screen_point_unavailable"
            attempts.append(attempt)
            continue
        start = MousePoint(*_backend_position(backend))
        if input_controller is None:
            input_controller = HumanInputController(backend, profile="instant_debug", sleep_func=sleep_func, monotonic_func=monotonic_func)
        candidate = _proposal_with_service_aimpoint(proposal, canvas_point, screen_point)
        plan = input_controller.plan_mouse_movement(
            start,
            _target_from_click(screen_point),
            movement_profile,
            context=_human_context(candidate, "service_alternate_aimpoint"),
        )
        attempt["screenPoint"] = dict(screen_point)
        attempt["movementPlan"] = plan.to_dict(include_points=False)
        if plan.validation_status == "FAIL":
            attempt["reason"] = "movement_plan_invalid"
            attempt["warnings"] = list(plan.warnings)
            attempts.append(attempt)
            continue
        move_started_wall_millis = int(wall_time_millis_func())
        try:
            input_controller.move_mouse(plan, context=_human_context(candidate, "service_alternate_aimpoint"))
        except Exception as error:  # noqa: BLE001
            attempt["reason"] = "hover_movement_failed"
            attempt["warnings"] = [f"{type(error).__name__}: {error}"]
            attempts.append(attempt)
            continue
        confirmation = _confirm_hover_menu(
            candidate,
            hover_options,
            canvas_point,
            move_started_wall_millis=move_started_wall_millis,
            max_requests=1,
            snapshot_fetch_func=snapshot_fetch_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
        )
        hover_sample = _confirmation_hover_sample(confirmation)
        attempt["hoverConfirmation"] = {
            "status": confirmation.get("status"),
            "confirmed": confirmation.get("confirmed"),
            "reason": confirmation.get("reason"),
            "latencyMillis": confirmation.get("latencyMillis"),
            "topOption": hover_sample.get("topOption"),
            "topTarget": hover_sample.get("topTarget"),
        }
        if confirmation.get("confirmed") is True:
            attempt["accepted"] = True
            attempts.append(attempt)
            return {
                "accepted": True,
                "attempts": attempts,
                "proposal": candidate,
                "screenPoint": screen_point,
                "canvasPoint": canvas_point,
                "movementPlan": plan,
                "confirmation": confirmation,
                "moveStartedWallMillis": move_started_wall_millis,
            }
        attempt["reason"] = confirmation.get("reason")
        attempts.append(attempt)
    return {"accepted": False, "attempts": attempts, "warning": "service object alternate aimpoints did not hover-confirm expected action"}


def _try_resource_alternate_hover(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile,
    hover_options: HoverConfirmationOptions,
    input_controller: HumanInputController | None = None,
    current_canvas_point: dict[str, Any] | None = None,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
) -> dict[str, Any] | None:
    if not _is_resource_object_proposal(proposal):
        return None
    attempts: list[dict[str, Any]] = []
    for canvas_point in _resource_alternate_aimpoints(proposal, current_canvas_point):
        screen_point = _screen_point_from_canvas_for_proposal(proposal, backend, canvas_point)
        attempt: dict[str, Any] = {"canvasPoint": dict(canvas_point), "accepted": False}
        if not isinstance(screen_point, dict):
            attempt["reason"] = "screen_point_unavailable"
            attempts.append(attempt)
            continue
        start = MousePoint(*_backend_position(backend))
        if input_controller is None:
            input_controller = HumanInputController(backend, profile="instant_debug", sleep_func=sleep_func, monotonic_func=monotonic_func)
        candidate = _proposal_with_resource_aimpoint(proposal, canvas_point, screen_point)
        plan = input_controller.plan_mouse_movement(
            start,
            _target_from_click(screen_point),
            movement_profile,
            context=_human_context(candidate, "resource_alternate_aimpoint"),
        )
        attempt["screenPoint"] = dict(screen_point)
        attempt["movementPlan"] = plan.to_dict(include_points=False)
        if plan.validation_status == "FAIL":
            attempt["reason"] = "movement_plan_invalid"
            attempt["warnings"] = list(plan.warnings)
            attempts.append(attempt)
            continue
        move_started_wall_millis = int(wall_time_millis_func())
        try:
            input_controller.move_mouse(plan, context=_human_context(candidate, "resource_alternate_aimpoint"))
        except Exception as error:  # noqa: BLE001
            attempt["reason"] = "hover_movement_failed"
            attempt["warnings"] = [f"{type(error).__name__}: {error}"]
            attempts.append(attempt)
            continue
        confirmation = _confirm_hover_menu(
            candidate,
            hover_options,
            canvas_point,
            move_started_wall_millis=move_started_wall_millis,
            snapshot_fetch_func=snapshot_fetch_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
        )
        hover_sample = _confirmation_hover_sample(confirmation)
        ambiguity = _resource_target_ambiguity(candidate, confirmation, rejection_reason=confirmation.get("reason"))
        attempt["hoverConfirmation"] = {
            "status": confirmation.get("status"),
            "confirmed": confirmation.get("confirmed"),
            "reason": confirmation.get("reason"),
            "latencyMillis": confirmation.get("latencyMillis"),
            "topOption": hover_sample.get("topOption") or hover_sample.get("option"),
            "topTarget": hover_sample.get("topTarget") or hover_sample.get("target"),
        }
        if ambiguity:
            attempt["resourceTargetAmbiguity"] = ambiguity
        if confirmation.get("confirmed") is True:
            attempt["accepted"] = True
            attempts.append(attempt)
            if isinstance(candidate.target_explanation, dict):
                candidate.target_explanation["aimpointSamplesTried"] = len(attempts)
                candidate.target_explanation["aimpointSampleResults"] = list(attempts)
                candidate.target_explanation["hoverConfirmedTopExpected"] = True
                candidate.target_explanation["resourceTargetAmbiguity"] = ambiguity or {"schema": "resource_target_ambiguity.v1", "ambiguityStatus": "clear"}
            return {
                "accepted": True,
                "attempts": attempts,
                "proposal": candidate,
                "screenPoint": screen_point,
                "canvasPoint": canvas_point,
                "movementPlan": plan,
                "confirmation": confirmation,
                "moveStartedWallMillis": move_started_wall_millis,
            }
        attempt["reason"] = confirmation.get("reason")
        attempts.append(attempt)
    return {
        "accepted": False,
        "attempts": attempts,
        "warning": "resource alternate aimpoints did not hover-confirm an unambiguous expected target",
        "reason": "no_clear_low_level_resource_target" if attempts else "no_alternate_resource_aimpoints",
    }


def _poll_last_menu_option_clicked(
    hover_options: HoverConfirmationOptions,
    before: dict[str, Any] | None,
    *,
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> dict[str, Any] | None:
    started = float(monotonic_func())
    timeout_seconds = max(0.0, hover_options.timeout_ms / 1000.0)
    poll_seconds = max(0.001, hover_options.poll_ms / 1000.0)
    latest: dict[str, Any] | None = None
    while True:
        try:
            snapshot = snapshot_fetch_func(
                hover_options.snapshot_url,
                timeout=hover_options.request_timeout_seconds,
                client_tick_tail=hover_options.client_tick_tail,
                menu_entry_limit=hover_options.menu_entry_limit,
            )
            latest = _last_menu_option_clicked_sample(snapshot)
            if latest is not None and not _same_menu_option_sample(before, latest):
                return latest
        except Exception:  # noqa: BLE001
            pass
        if float(monotonic_func()) - started >= timeout_seconds:
            return latest
        sleep_func(poll_seconds)


def _menu_action_result_classification(result: ExecutionResult) -> str | None:
    hover = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else {}
    if not hover:
        return None
    observed = result.observed_result if isinstance(result.observed_result, dict) else {}
    observed_classification = _observed_success_classification(observed)
    if observed_classification in {"inventory_changed_success", "resource_count_changed_success", "animation_started_progress_pending"}:
        return observed_classification
    click_classification = str(hover.get("clickClassification") or "")
    if not hover.get("confirmed"):
        return "hover_confirm_timeout" if hover.get("reason") == "hover_confirm_timeout" else None
    if click_classification == "clicked_walk_here":
        return "hover_confirmed_but_clicked_walk_here"
    if click_classification == "clicked_cancel":
        return "clicked_cancel"
    if click_classification == "clicked_chop_tree":
        return "chop_clicked_but_no_progress_yet"
    if click_classification == "clicked_expected_action" and result.proposed_action == "interact_service_route_object":
        return "route_transition_click_confirmed"
    if _clicked_menu_mismatch_is_known(click_classification):
        return "menu_flip_mismatch"
    return click_classification or "unknown_click_result"


def _action_intent_type(proposal: ActionProposal) -> str:
    if proposal.proposed_action == "select_resource_target":
        return "resource_object_action"
    if proposal.proposed_action == "resource_view_recovery":
        return "resource_view_recovery_action"
    if proposal.proposed_action in {"open_service", "deposit_inventory", "deposit_resources", "close_bank"}:
        return "service_object_action"
    if proposal.proposed_action == "interact_service_route_object":
        return "route_transition_action"
    if proposal.proposed_action == "interface_dialogue_choice":
        return "interface_dialogue_choice_action"
    if proposal.proposed_action in NAVIGATION_ACTIONS or proposal.target_kind == "path_tile":
        return "navigation_waypoint_action"
    if proposal.proposed_action == "camera_adjustment":
        return "camera_adjustment_action"
    return "unknown"


def _new_action_trace(proposal: ActionProposal) -> dict[str, Any]:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    return {
        "actionTraceSchema": "action_trace.v2",
        "selectedTarget": dict(explanation),
        "proposedAction": proposal.proposed_action,
        "actionIntentType": _action_intent_type(proposal),
        "gameTickBeforeAction": proposal.source_tick,
        "clientTickBeforeAction": None,
        "mouseMove": {},
        "intendedPoint": {},
        "clientTick": {
            "hoverConfirmationSamples": [],
            "rejectedHoverSamples": [],
            "acceptedHoverSample": None,
            "lastMenuOptionClickedBefore": None,
            "lastMenuOptionClickedAfter": None,
            "clickedMenuClassification": None,
        },
        "dialogue": {},
        "humanInput": {},
        "cameraInput": {},
        "targetSuppression": {},
        "reacquisition": {},
        "gameTickVerificationTimeline": [],
        "finalClassification": None,
    }


def _coordinate_trace_fields(click_resolution: dict[str, Any] | None) -> dict[str, Any]:
    resolution = click_resolution if isinstance(click_resolution, dict) else {}
    fields: dict[str, Any] = {}
    for key in (
        "coordinateSpace",
        "scaleX",
        "scaleY",
        "screenPointBeforeScaling",
        "screenPointAfterScaling",
        "windowBoundsSource",
        "canvasBoundsSource",
        "displayScale",
        "displayScaleApplied",
        "displayScaleReason",
        "coordinateMethod",
        "coordinateResolver",
        "clickFailureBucket",
    ):
        if resolution.get(key) is not None:
            fields[key] = resolution.get(key)
    if fields:
        fields["clickPointResolution"] = dict(resolution)
    return fields


def _set_trace_intended_point(
    result: ExecutionResult,
    proposal: ActionProposal,
    *,
    canvas_point: dict[str, Any] | None,
    screen_point: dict[str, Any],
    click_resolution: dict[str, Any] | None = None,
) -> None:
    if not isinstance(result.action_trace, dict):
        return
    payload = {
        "canvas": dict(canvas_point) if isinstance(canvas_point, dict) else None,
        "screen": dict(screen_point),
        "clickPointSpace": proposal.click_point_space,
    }
    payload.update(_coordinate_trace_fields(click_resolution or proposal.click_point_resolution))
    result.action_trace["intendedPoint"] = payload


def _attach_human_input_trace(result: ExecutionResult, input_controller: HumanInputController | None) -> None:
    if input_controller is None or not isinstance(result.action_trace, dict):
        return
    metrics = input_controller.metrics()
    result.action_trace["humanInput"] = dict(metrics)
    result.action_trace["cameraInput"] = {
        "profile": metrics.get("profile"),
        "movementGenerator": metrics.get("movementGenerator"),
        "cameraHoldMinMs": metrics.get("cameraHoldMinMs"),
        "cameraHoldAvgMs": metrics.get("cameraHoldAvgMs"),
        "cameraHoldMaxMs": metrics.get("cameraHoldMaxMs"),
        "cameraDirectionSwitches": metrics.get("cameraDirectionSwitches", 0),
        "directBackendBypassCount": metrics.get("directBackendBypassCount", 0),
    }


def human_click_profile_handoff(profile: dict[str, Any] | None, *, activity: str | None = None) -> dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    task_profile = {}
    if activity:
        task_profile = _dict(_dict(profile.get("taskProfiles")).get(activity) or profile.get("taskProfile"))
    landing = _dict(profile.get("landing"))
    clicks = _dict(profile.get("clicks"))
    camera = _dict(profile.get("camera"))
    return {
        "schema": "human_click_profile_executor_handoff.v1",
        "status": "PASS" if profile and profile.get("status") != "FAIL" else "WARN",
        "activity": activity,
        "recordingCount": profile.get("recordingCount"),
        "clickLanding": {
            "medianAimDistancePx": landing.get("medianAimDistancePx") or _dict(task_profile.get("clickLandingDistancePx")).get("median"),
            "p75AimDistancePx": landing.get("p75AimDistancePx") or _dict(task_profile.get("clickLandingDistancePx")).get("p75"),
            "p90AimDistancePx": landing.get("p90AimDistancePx") or _dict(task_profile.get("clickLandingDistancePx")).get("p90"),
            "aimDistanceBucketsPx": landing.get("aimDistanceBucketsPx"),
            "targetRelativeClickCount": clicks.get("targetRelativeClicks") or task_profile.get("targetRelativeClickCount"),
            "strongOrMediumTargetRate": task_profile.get("strongOrMediumTargetRate"),
        },
        "menuBehavior": {
            "menuRowSelectionCount": clicks.get("menuRowSelectionCount") or task_profile.get("menuRowSelectionCount"),
            "rightClickMenuOpenCount": clicks.get("rightClickMenuOpenCount") or task_profile.get("rightClickMenuOpenCount"),
        },
        "cameraBehavior": {
            "cameraBeforeClickCount": camera.get("cameraBeforeClickCount"),
            "cameraBeforeClickFrequency": task_profile.get("cameraBeforeClickFrequency"),
            "middleMouseDragCount": camera.get("middleMouseDragCount"),
            "medianCameraToClickMs": camera.get("medianCameraToClickMs"),
        },
        "imperfectSuccessfulClickCount": profile.get("imperfectSuccessfulClickCount") or task_profile.get("imperfectSuccessfulClickCount"),
        "warnings": profile.get("warnings") or [],
        "missingCapabilities": profile.get("missingCapabilities") or [],
        "rule": "Advisory only; live execution must still use target/readiness/hover proof before clicking.",
    }


def get_human_click_profile_executor_handoff(profile: dict[str, Any] | None, *, activity: str | None = None) -> dict[str, Any]:
    return human_click_profile_handoff(profile, activity=activity)


def build_click_plan_from_handoff(
    handoff: dict[str, Any] | None,
    *,
    target: dict[str, Any] | None = None,
    action: str | None = None,
    activity: str | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from input_control import click_planner

    handoff = handoff if isinstance(handoff, dict) else {}
    profile = {
        "schema": "human_click_profile_compact.v1",
        "status": handoff.get("status") or "WARN",
        "recordingCount": handoff.get("recordingCount"),
        "landing": {
            "medianAimDistancePx": _dict(handoff.get("clickLanding")).get("medianAimDistancePx"),
            "p75AimDistancePx": _dict(handoff.get("clickLanding")).get("p75AimDistancePx"),
            "p90AimDistancePx": _dict(handoff.get("clickLanding")).get("p90AimDistancePx"),
            "aimDistanceBucketsPx": _dict(handoff.get("clickLanding")).get("aimDistanceBucketsPx"),
        },
        "taskProfile": {
            "cameraBeforeClickFrequency": _dict(handoff.get("cameraBehavior")).get("cameraBeforeClickFrequency"),
            "menuRowSelectionCount": _dict(handoff.get("menuBehavior")).get("menuRowSelectionCount"),
            "rightClickMenuOpenCount": _dict(handoff.get("menuBehavior")).get("rightClickMenuOpenCount"),
            "strongOrMediumTargetRate": _dict(handoff.get("clickLanding")).get("strongOrMediumTargetRate"),
        },
        "camera": {
            "middleMouseDragCount": _dict(handoff.get("cameraBehavior")).get("middleMouseDragCount"),
            "medianCameraToClickMs": _dict(handoff.get("cameraBehavior")).get("medianCameraToClickMs"),
        },
        "warnings": handoff.get("warnings") or [],
        "missingCapabilities": handoff.get("missingCapabilities") or [],
    }
    source = {
        "readiness": readiness or {},
        "humanClickProfile": profile,
    }
    return click_planner.build_click_plan(
        source,
        target=target or {},
        action=action,
        activity=activity or handoff.get("activity"),
        human_profile=profile,
    )


def compare_center_click_vs_profile_click(plan: dict[str, Any] | None) -> dict[str, Any]:
    from input_control import click_planner

    return click_planner.compare_center_click_vs_profile_click(plan if isinstance(plan, dict) else {})


def _attach_readiness_trace(result: ExecutionResult, readiness: dict[str, Any] | None) -> None:
    if not isinstance(readiness, dict) or not isinstance(result.action_trace, dict):
        return
    action_readiness = readiness.get("actionReadiness") if isinstance(readiness.get("actionReadiness"), dict) else {}
    result.action_trace["currentIntent"] = readiness.get("currentIntent")
    result.action_trace["actionReadiness"] = dict(action_readiness)
    result.action_trace["readiness"] = {
        "overallStatus": readiness.get("status"),
        "currentIntent": readiness.get("currentIntent"),
        "actionReadinessStatus": action_readiness.get("status") if isinstance(action_readiness, dict) else None,
        "executionAllowed": action_readiness.get("executionAllowed") if isinstance(action_readiness, dict) else None,
    }


def _human_context(proposal: ActionProposal, reason: str) -> HumanInputContext:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    safe = explanation.get("safeAimPoint") if isinstance(explanation.get("safeAimPoint"), dict) else {}
    width = _int_or_none(safe.get("safeHullWidthPx") or safe.get("targetWidthPx") or explanation.get("targetWidthPx"))
    radius = _int_or_none(safe.get("radiusPx") or explanation.get("radiusPx"))
    return HumanInputContext(
        reason=reason,
        action_intent_type=_action_intent_type(proposal),
        target_width_px=width,
        safe_hull_width_px=width,
        safe_radius_px=radius,
    )


def _set_trace_final(result: ExecutionResult, classification: str) -> None:
    if not isinstance(result.action_trace, dict):
        return
    result.action_trace["finalClassification"] = classification


def _resource_camera_trigger(proposal: ActionProposal) -> str:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    if explanation.get("cameraTriggeredBy"):
        return str(explanation["cameraTriggeredBy"])
    status = explanation.get("resourceProjectionStatus") if isinstance(explanation.get("resourceProjectionStatus"), dict) else {}
    classification = str(status.get("classification") or "")
    mapping = {
        "projection_sentinel": "resource_projection_sentinel",
        "edge_clipped": "resource_edge_projection",
        "offscreen": "resource_offscreen_projection",
        "tiny_projection": "resource_tiny_projection",
        "degenerate_projection": "resource_degenerate_projection",
    }
    return mapping.get(classification, "resource_no_safe_aimpoint")


def _is_resource_view_recovery(proposal: ActionProposal, proposal_payload: dict[str, Any] | None = None) -> bool:
    payload = proposal_payload if isinstance(proposal_payload, dict) else proposal.to_dict()
    explanation = payload.get("targetExplanation") if isinstance(payload.get("targetExplanation"), dict) else {}
    recovery_action = str(explanation.get("recoveryAction") or "")
    if recovery_action == "camera_reacquire_resource_view":
        return True
    return str(payload.get("reason") or proposal.reason or "") == "resource_view_recovery_needed"


def _resource_recovery_hold_ms(proposal: ActionProposal, navigation_options: Any | None) -> int:
    action = proposal.key_action if isinstance(proposal.key_action, dict) else {}
    value = _int_or_none(action.get("durationMs"))
    if value is None and navigation_options is not None:
        value = _int_or_none(getattr(navigation_options, "resource_recovery_camera_hold_ms", None))
    if value is None:
        value = max(160, _camera_probe_ms(navigation_options) * 4)
    return max(80, min(900, int(value)))


def _service_camera_trigger(proposal: ActionProposal) -> str:
    explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
    if explanation.get("cameraTriggeredBy"):
        return str(explanation["cameraTriggeredBy"])
    exposure = explanation.get("serviceTargetExposure") if isinstance(explanation.get("serviceTargetExposure"), dict) else {}
    return str(exposure.get("cameraExposureReason") or "service_screen_click_point_unavailable")


def _service_recovery_hold_ms(proposal: ActionProposal, navigation_options: Any | None) -> int:
    action = proposal.key_action if isinstance(proposal.key_action, dict) else {}
    value = _int_or_none(action.get("durationMs"))
    if value is None:
        value = max(160, _camera_probe_ms(navigation_options) * 5)
    return max(100, min(1000, int(value)))


def _trace_classification_from_observed(classification: str | None) -> str:
    value = str(classification or "")
    mapping = {
        "clicked_chop_tree": "clicked_expected_action",
        "chop_clicked_but_no_progress_yet": "clicked_expected_action_no_progress_yet",
        "hover_confirmed_but_clicked_walk_here": "clicked_walk_here",
        "clicked_cancel": "clicked_cancel",
        "inventory_changed_success": "inventory_changed_success",
        "success_inventory_changed": "inventory_changed_success",
        "resource_count_changed_success": "resource_count_changed_success",
        "service_navigation_progress": "service_navigation_progress",
        "service_navigation_reached_node": "service_navigation_reached_node",
        "service_route_object_reacquired": "service_route_object_reacquired",
        "route_transition_click_confirmed": "route_transition_click_confirmed",
        "route_transition_progress": "route_transition_progress",
        "return_transition_plane_changed": "return_transition_plane_changed",
        "return_transition_pathing_to_object": "return_transition_pathing_to_object",
        "route_transition_reconciled_success": "route_transition_reconciled_success",
        "return_transition_reconciled_success": "return_transition_reconciled_success",
        "route_transition_dialogue_opened": "route_transition_dialogue_opened",
        "route_transition_dialogue_choice_selected": "route_transition_dialogue_choice_selected",
        "resource_return_progress": "resource_return_progress",
        "resource_return_reached_node": "resource_return_reached_node",
        "animation_started_progress_pending": "animation_started_progress_pending",
        "hover_confirm_timeout": "hover_confirm_timeout",
    }
    return mapping.get(value, value or "unknown")


def _update_trace_from_hover(result: ExecutionResult, confirmation: dict[str, Any]) -> None:
    if not isinstance(result.action_trace, dict):
        return
    client_tick = result.action_trace.setdefault("clientTick", {})
    if isinstance(confirmation.get("sample"), dict):
        client_tick["acceptedHoverSample"] = confirmation.get("sample")
        client_tick["clientTickAtHover"] = confirmation["sample"].get("clientTick")
    if isinstance(confirmation.get("latestMatch"), dict):
        latest_match = confirmation.get("latestMatch") or {}
        if isinstance(latest_match.get("sample"), dict):
            client_tick["latestRejectedHoverSample"] = latest_match.get("sample")
    client_tick["hoverConfirmation"] = confirmation
    client_tick["hoverConfirmationSamples"] = list(confirmation.get("hoverConfirmationSamples") or [])
    client_tick["rejectedHoverSamples"] = list(confirmation.get("rejectedHoverSamples") or [])
    client_tick["lastMenuOptionClickedBefore"] = confirmation.get("lastMenuOptionClickedBefore")
    match_details = {}
    if isinstance(confirmation.get("matchDetails"), dict):
        match_details = confirmation.get("matchDetails") or {}
    elif isinstance(confirmation.get("latestMatch"), dict) and isinstance(confirmation["latestMatch"].get("details"), dict):
        match_details = confirmation["latestMatch"].get("details") or {}
    client_tick["rawTopEntry"] = match_details.get("rawTopEntry")
    client_tick["selectedLeftClickEntry"] = match_details.get("selectedMenuEntry")
    client_tick["menuSelectionReason"] = match_details.get("menuSelectionReason")
    client_tick["menuOpen"] = match_details.get("menuOpen")
    volatility = confirmation.get("menuTailVolatility") if isinstance(confirmation.get("menuTailVolatility"), dict) else {}
    if volatility:
        client_tick["menuTailVolatility"] = dict(volatility)
        client_tick["recentMenuTail"] = list(volatility.get("recentMenuTail") or [])
        client_tick["volatileHoverZone"] = bool(volatility.get("volatileHoverZone"))
        client_tick["volatileReasons"] = list(volatility.get("volatileReasons") or [])
    result.action_trace["clientTickBeforeAction"] = client_tick.get("clientTickAtHover")


def execute_action(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile = "linear_debug",
    dry_run: bool = True,
    hover_options: HoverConfirmationOptions | None = None,
    navigation_options: Any | None = None,
    snapshot_url: str = "http://127.0.0.1:8893",
    snapshot_fetch_func=fetch_plugin_snapshot,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
    wall_time_millis_func=_wall_time_millis,
    input_controller: HumanInputController | None = None,
) -> ExecutionResult:
    if hover_options is not None:
        snapshot_url = hover_options.snapshot_url
    input_controller = input_controller or HumanInputController(
        backend,
        profile=_input_profile_from_options(navigation_options),
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
        seed=getattr(navigation_options, "seed", None),
    )
    blocked_actionability = str(proposal.actionability or "")
    if blocked_actionability in {"advisory_only", "stale", "blocked"} or blocked_actionability.startswith("blocked_"):
        backend_name = getattr(backend, "name", backend.__class__.__name__)
        reason_map = {
            "advisory_only": "static_target_not_executable",
            "stale": "stale_target",
            "blocked": "action_target_blocked",
        }
        block_reason = reason_map.get(blocked_actionability, "action_target_blocked")
        result = ExecutionResult(
            status="FAIL",
            proposed_action=proposal.proposed_action,
            dry_run=dry_run,
            action_id=proposal_action_id(proposal),
            backend_name=backend_name,
            movement_profile=movement_profile.name if isinstance(movement_profile, MouseMovementProfile) else str(movement_profile),
            proposal=proposal.to_dict(),
            click_point_resolution=proposal.click_point_resolution,
            expected_result=expected_result_for_action(proposal.proposed_action),
            warnings=[*proposal.warnings, f"{block_reason}: {blocked_actionability}"],
            missing_capabilities=list(dict.fromkeys([*proposal.missing_capabilities, "action.live_target"])),
            verification_status="FAIL",
        )
        result.action_trace = _new_action_trace(proposal)
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = block_reason
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    projection_warnings: list[str] = []
    if _path_tile_projection_request(proposal) is not None:
        proposal, projection_warnings = _resolve_path_tile_projection(
            proposal,
            backend=backend,
            snapshot_url=snapshot_url,
            navigation_options=navigation_options,
            snapshot_fetch_func=snapshot_fetch_func,
        )
    warnings = list(proposal.warnings)
    warnings.extend(projection_warnings)
    missing = list(proposal.missing_capabilities)
    backend_name = getattr(backend, "name", backend.__class__.__name__)
    result = ExecutionResult(
        status="PASS",
        proposed_action=proposal.proposed_action,
        dry_run=dry_run,
        action_id=proposal_action_id(proposal),
        backend_name=backend_name,
        movement_profile=movement_profile.name if isinstance(movement_profile, MouseMovementProfile) else str(movement_profile),
        proposal=proposal.to_dict(),
        click_point_resolution=proposal.click_point_resolution,
        expected_result=expected_result_for_action(proposal.proposed_action),
        warnings=warnings,
        missing_capabilities=missing,
    )
    result.action_trace = _new_action_trace(proposal)
    _attach_human_input_trace(result, input_controller)
    hover_options = hover_options if hover_options and hover_options.enabled else None
    camera_reacquired_before_hover: dict[str, Any] | None = None
    lifecycle = lifecycle_state_for_proposal(proposal)
    if proposal.proposed_action in {"none", "wait_for_context"}:
        result.status = "WARN"
        result.warnings.append(proposal.reason)
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    if proposal.proposed_action == "resource_view_recovery":
        action = proposal.key_action if isinstance(proposal.key_action, dict) else {}
        command = str(action.get("command") or "yaw_right_pitch_up")
        method = str(action.get("method") or _camera_method(navigation_options))
        duration_ms = _resource_recovery_hold_ms(proposal, navigation_options)
        spec = camera_control.camera_input_spec(method=method, command=command)
        trigger = _resource_camera_trigger(proposal)
        result.commands.append(
            {
                "type": "resource_camera_reacquire",
                "cameraMethod": spec.method,
                "cameraCommand": spec.command,
                "cameraKeys": list(spec.keys),
                "durationMs": duration_ms,
                "cameraTriggeredBy": trigger,
            }
        )
        if isinstance(result.action_trace, dict):
            reacquisition = result.action_trace.setdefault("reacquisition", {})
            reacquisition["cameraTriggeredBy"] = trigger
            reacquisition["resourceProjectionRecovery"] = True
            is_resource_view_recovery = _is_resource_view_recovery(proposal)
            view_score = _dict((proposal.target_explanation or {}).get("resourceViewScore")) if isinstance(proposal.target_explanation, dict) else {}
            if is_resource_view_recovery and view_score:
                reacquisition["resourceViewScoreBefore"] = dict(view_score)
                reacquisition["resourceCameraTriggeredBy"] = trigger
                result.action_trace["resourceViewScoreBefore"] = dict(view_score)
            reacquisition["projectionBefore"] = dict(
                _dict((proposal.target_explanation or {}).get("resourceProjectionStatus"))
            ) if isinstance(proposal.target_explanation, dict) else {}
            result.action_trace["cameraInput"].update(
                {
                    "method": spec.method,
                    "keys": list(spec.keys),
                    "command": spec.command,
                    "plannedHoldDurationMs": duration_ms,
                    "releaseReason": "bounded_resource_view_recovery" if is_resource_view_recovery else "bounded_resource_projection_recovery",
                }
            )
        if not dry_run:
            try:
                if spec.continuous_hover:
                    with input_controller.hold_keys(
                        spec.keys,
                        context=HumanInputContext(reason="resource_projection_recovery", action_intent_type="resource_view_recovery_action"),
                    ):
                        sleep_func(duration_ms / 1000.0)
                else:
                    input_controller.camera_drag_pulse(spec, duration_ms=duration_ms)
                result.executed = True
            except Exception as error:  # noqa: BLE001
                result.status = "FAIL"
                result.warnings.append(f"resource projection recovery failed: {type(error).__name__}: {error}")
                result.missing_capabilities.append("camera.controller")
        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
        if result.status != "FAIL":
            result.observed_result = {
                "observedResult": "resource_projection_recovery_started" if result.executed else "resource_projection_recovery_planned",
                "resultOutcome": "still_waiting" if result.executed else "dry_run",
                "resultComplete": False if result.executed else True,
                "nextActionAllowed": False if result.executed else True,
                "verificationStatus": "PASS" if result.executed else "DRY_RUN",
            }
            result.verification_status = str(result.observed_result["verificationStatus"])
            _set_trace_final(result, "resource_projection_recovery_started" if result.executed else "resource_projection_recovery_planned")
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    if proposal.proposed_action == "service_view_recovery":
        action = proposal.key_action if isinstance(proposal.key_action, dict) else {}
        command = str(action.get("command") or "yaw_right_pitch_up")
        method = str(action.get("method") or _camera_method(navigation_options))
        duration_ms = _service_recovery_hold_ms(proposal, navigation_options)
        spec = camera_control.camera_input_spec(method=method, command=command)
        trigger = _service_camera_trigger(proposal)
        exposure = _dict((proposal.target_explanation or {}).get("serviceTargetExposure")) if isinstance(proposal.target_explanation, dict) else {}
        target_view = _dict((proposal.target_explanation or {}).get("targetViewState")) if isinstance(proposal.target_explanation, dict) else {}
        if not target_view:
            target_view = _dict(exposure.get("targetViewState"))
        motor = _dict(exposure.get("cameraMotorPlan"))
        result.commands.append(
            {
                "type": "service_camera_reacquire",
                "cameraMethod": spec.method,
                "cameraCommand": spec.command,
                "cameraKeys": list(spec.keys),
                "durationMs": duration_ms,
                "cameraTriggeredBy": trigger,
                "nonClick": True,
            }
        )
        if isinstance(result.action_trace, dict):
            reacquisition = result.action_trace.setdefault("reacquisition", {})
            reacquisition["cameraTriggeredBy"] = trigger
            reacquisition["serviceViewRecovery"] = True
            reacquisition["serviceTargetExposureBefore"] = dict(exposure)
            if target_view:
                reacquisition["targetViewStateBefore"] = dict(target_view)
            reacquisition["serviceCameraTriggeredBy"] = trigger
            result.action_trace["serviceTargetExposure"] = dict(exposure)
            if target_view:
                result.action_trace["targetViewState"] = dict(target_view)
            result.action_trace["cameraInput"].update(
                {
                    "method": spec.method,
                    "keys": list(spec.keys),
                    "command": spec.command,
                    "plannedHoldDurationMs": duration_ms,
                    "releaseReason": "bounded_service_view_recovery",
                    "cameraDirectionChosen": motor.get("cameraDirectionChosen") or spec.command,
                    "cameraDirectionReason": motor.get("cameraDirectionReason"),
                    "targetBearing": motor.get("targetBearing") or target_view.get("targetBearing"),
                    "yawErrorBefore": motor.get("yawErrorBefore") or target_view.get("yawErrorToTarget"),
                    "cameraResponseCalibration": motor.get("cameraResponseCalibration") or target_view.get("cameraResponseCalibration"),
                    "controlLaw": motor.get("controlLaw"),
                    "serviceViewScoreBefore": exposure.get("viewQualityClassification"),
                }
            )
        if not dry_run:
            try:
                if spec.continuous_hover:
                    with input_controller.hold_keys(
                        spec.keys,
                        context=HumanInputContext(reason="service_view_recovery", action_intent_type="service_view_recovery_action"),
                    ):
                        sleep_func(duration_ms / 1000.0)
                else:
                    input_controller.camera_drag_pulse(spec, duration_ms=duration_ms)
                result.executed = True
            except Exception as error:  # noqa: BLE001
                result.status = "FAIL"
                result.warnings.append(f"service view recovery failed: {type(error).__name__}: {error}")
                result.missing_capabilities.append("camera.controller")
        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
        if result.status != "FAIL":
            result.observed_result = {
                "observedResult": "service_view_recovery_started" if result.executed else "service_view_recovery_planned",
                "resultOutcome": "still_waiting" if result.executed else "dry_run",
                "resultComplete": False if result.executed else True,
                "nextActionAllowed": False if result.executed else True,
                "verificationStatus": "PASS" if result.executed else "DRY_RUN",
                "serviceViewRecoveryClassification": "service_view_recovery_started" if result.executed else "service_view_recovery_planned",
            }
            result.verification_status = str(result.observed_result["verificationStatus"])
            _set_trace_final(result, "service_view_recovery_started" if result.executed else "service_view_recovery_planned")
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    if proposal.key_action:
        key = proposal.key_action.get("key")
        command = {"type": "key_press", "key": key}
        if proposal.proposed_action == "interface_dialogue_choice":
            explanation = proposal.target_explanation if isinstance(proposal.target_explanation, dict) else {}
            command["dialoguePrompt"] = explanation.get("dialoguePrompt")
            command["selectedDialogueOption"] = explanation.get("selectedDialogueOption")
            command["selectionMethod"] = explanation.get("selectionMethod")
            if isinstance(result.action_trace, dict):
                result.action_trace["dialogue"] = {
                    "dialoguePrompt": explanation.get("dialoguePrompt"),
                    "dialogueOptions": list(explanation.get("dialogueOptions") or []),
                    "expectedDialogueOption": explanation.get("expectedDialogueOption"),
                    "selectedDialogueOption": explanation.get("selectedDialogueOption"),
                    "selectionMethod": explanation.get("selectionMethod"),
                    "keyPressed": key,
                    "dialogueBefore": {
                        "promptText": explanation.get("dialoguePrompt"),
                        "options": list(explanation.get("dialogueOptions") or []),
                    },
                }
        result.commands.append(command)
        if not dry_run and key:
            input_controller.press_key(key, context=_human_context(proposal, "key_action"))
            result.executed = True
        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    screen_point, coordinate_warnings, click_resolution = _screen_click_point(proposal, backend)
    if click_resolution:
        result.click_point_resolution = click_resolution
    movement_safety_preflight = _movement_safety_preflight(screen_point, backend) if screen_point else None
    projection_camera_reason = _can_camera_reacquire_projection_failure(proposal, projection_warnings, navigation_options)
    movement_safety_camera_reason = _can_camera_reacquire_movement_safety_failure(
        proposal,
        movement_safety_preflight,
        navigation_options,
    )
    camera_reacquire_reason = projection_camera_reason or movement_safety_camera_reason
    if hover_options is not None and camera_reacquire_reason and (
        coordinate_warnings or not screen_point or _movement_safety_preflight_failed(movement_safety_preflight)
    ):
        camera_reacquire = _try_navigation_camera_guided_reacquire(
            proposal,
            backend=backend,
            movement_profile=movement_profile,
            hover_options=hover_options,
            snapshot_url=snapshot_url,
            navigation_options=navigation_options,
            input_controller=input_controller,
            primary_confirmation={
                "reason": camera_reacquire_reason,
                "movementSafetyPreflight": dict(movement_safety_preflight) if isinstance(movement_safety_preflight, dict) else None,
            },
            snapshot_fetch_func=snapshot_fetch_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
            wall_time_millis_func=wall_time_millis_func,
        )
        if isinstance(camera_reacquire, dict):
            if isinstance(result.action_trace, dict):
                reacquisition = result.action_trace.setdefault("reacquisition", {})
                reacquisition["primaryWaypointFailure"] = camera_reacquire_reason
                if isinstance(movement_safety_preflight, dict):
                    reacquisition["movementSafetyPreflightBefore"] = dict(movement_safety_preflight)
                trigger = _camera_trigger_from_navigation_failure(camera_reacquire_reason)
                if trigger:
                    reacquisition["cameraTriggeredBy"] = trigger
                projection_before = _proposal_projection(proposal)
                if isinstance(projection_before, dict):
                    status_before = proposal.target_explanation.get("routeProjectionStatus") if isinstance(proposal.target_explanation, dict) else None
                    status_before = status_before if isinstance(status_before, dict) else {}
                    reacquisition["projectionBefore"] = dict(projection_before)
                    reacquisition["edgeDistanceBefore"] = status_before.get("distanceToViewportEdgePx")
                    reacquisition["visibleAreaRatioBefore"] = status_before.get("clippedVisibleAreaRatio")
                reacquisition["cameraExposureAttempts"] = list(camera_reacquire.get("attempts") or [])
                reacquisition["waypointReacquiredByCamera"] = bool(camera_reacquire.get("accepted") is True)
            if camera_reacquire.get("warning"):
                result.warnings.append(str(camera_reacquire.get("warning")))
            if camera_reacquire.get("accepted") is True:
                camera_reacquired_before_hover = camera_reacquire
                proposal = camera_reacquire["proposal"]
                screen_point = dict(camera_reacquire["screenPoint"])
                coordinate_warnings = []
                click_resolution = proposal.click_point_resolution
                movement_safety_preflight = _movement_safety_preflight(screen_point, backend) if screen_point else None
                result.proposal = proposal.to_dict()
                result.click_point_resolution = proposal.click_point_resolution
                if isinstance(result.action_trace, dict):
                    reacquisition = result.action_trace.setdefault("reacquisition", {})
                    projection_after = _proposal_projection(proposal)
                    status_after = proposal.target_explanation.get("routeProjectionStatus") if isinstance(proposal.target_explanation, dict) else None
                    status_after = status_after if isinstance(status_after, dict) else {}
                    if isinstance(projection_after, dict):
                        reacquisition["projectionAfter"] = dict(projection_after)
                    reacquisition["edgeDistanceAfter"] = status_after.get("distanceToViewportEdgePx")
                    reacquisition["visibleAreaRatioAfter"] = status_after.get("clippedVisibleAreaRatio")
                    before_edge = reacquisition.get("edgeDistanceBefore")
                    after_edge = reacquisition.get("edgeDistanceAfter")
                    before_ratio = reacquisition.get("visibleAreaRatioBefore")
                    after_ratio = reacquisition.get("visibleAreaRatioAfter")
                    reacquisition["cameraImprovedProjection"] = (
                        (isinstance(before_edge, (int, float)) and isinstance(after_edge, (int, float)) and after_edge > before_edge)
                        or (isinstance(before_ratio, (int, float)) and isinstance(after_ratio, (int, float)) and after_ratio > before_ratio)
                    )
                result.movement_plan = camera_reacquire["movementPlan"].to_dict(include_points=False)
                result.hover_confirmation = camera_reacquire["confirmation"]
                result.commands.append(
                    {
                        "type": "navigation_reacquire_camera_waypoint",
                        "reason": camera_reacquire_reason,
                        "attemptCount": len(camera_reacquire.get("attempts") or []),
                        "selectedTargetTile": dict(proposal.target_tile or {}),
                    }
                )
                result.commands.append(
                    {
                        "type": "move",
                        "pointCount": len(camera_reacquire["movementPlan"].points),
                        "clickPoint": camera_reacquire["movementPlan"].click_point.to_dict(),
                    }
                )
                result.commands.append(
                    {
                        "type": "hover_confirm",
                        "snapshotUrl": hover_options.snapshot_url,
                        "timeoutMs": hover_options.timeout_ms,
                        "pollMs": hover_options.poll_ms,
                        "positionTolerancePx": hover_options.tolerance_px,
                        "expectedCanvasPoint": dict(camera_reacquire["canvasPoint"]),
                    }
                )
                if isinstance(result.action_trace, dict):
                    _set_trace_intended_point(
                        result,
                        proposal,
                        canvas_point=camera_reacquire["canvasPoint"],
                        screen_point=screen_point,
                    )
                    result.action_trace.setdefault("reacquisition", {})["waypointReacquiredAfterCamera"] = True
                _update_trace_from_hover(result, result.hover_confirmation)
    if coordinate_warnings:
        if not click_resolution or click_resolution.get("status") == "FAIL":
            result.status = "FAIL"
        result.warnings.extend(coordinate_warnings)
        if result.status == "FAIL" and "screen_click_point" not in result.missing_capabilities:
            result.missing_capabilities.append("screen_click_point")
        if result.status == "FAIL":
            lifecycle = lifecycle_state_for_proposal(proposal)
            lifecycle.current_state = "blocked"
            lifecycle.reason = "screen_click_point_unavailable"
            _apply_lifecycle(result, lifecycle)
            _attach_human_input_trace(result, input_controller)
            return result
    if not screen_point:
        result.status = "FAIL"
        if "click_point" not in result.missing_capabilities:
            result.missing_capabilities.append("click_point")
        result.warnings.append("no click point available; execution blocked")
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = "click_point_unavailable"
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    geometry_validation = validate_screen_point_inside_geometry(
        screen_point,
        proposal.input_geometry if isinstance(proposal.input_geometry, dict) and proposal.input_geometry else click_resolution,
    )
    if isinstance(result.click_point_resolution, dict):
        result.click_point_resolution = dict(result.click_point_resolution)
        result.click_point_resolution["inputGeometryValidation"] = geometry_validation
    if isinstance(result.action_trace, dict):
        result.action_trace["inputGeometryStatus"] = geometry_validation.get("inputGeometryStatus")
        result.action_trace["inputGeometryValidation"] = geometry_validation
    if not dry_run and geometry_validation.get("status") != "PASS":
        reason = str(geometry_validation.get("reason") or "input_geometry_invalid")
        result.status = "FAIL"
        result.warnings.append(f"input geometry safety blocked click: {reason}")
        for capability in ("input.geometry", "screen_click_point"):
            if capability not in result.missing_capabilities:
                result.missing_capabilities.append(capability)
        observed = {
            "observedResult": "no_click_safety_block",
            "resultOutcome": "blocked",
            "resultComplete": True,
            "nextActionAllowed": False,
            "verificationStatus": "FAIL",
            "skipReason": reason,
            "inputGeometryStatus": geometry_validation.get("inputGeometryStatus"),
            "targetScreenPoint": geometry_validation.get("targetScreenPoint"),
            "canvasRect": geometry_validation.get("canvasRect"),
            "clientRect": geometry_validation.get("clientRect"),
        }
        result.observed_result = observed
        result.verification_status = "FAIL"
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = reason
        lifecycle.observed_result = observed
        lifecycle.result_complete = True
        lifecycle.result_outcome = "blocked"
        lifecycle.next_action_allowed = False
        _apply_lifecycle(result, lifecycle)
        _set_trace_final(result, reason)
        _attach_human_input_trace(result, input_controller)
        return result
    if _movement_safety_preflight_failed(movement_safety_preflight):
        warning = _movement_safety_preflight_warning(movement_safety_preflight)
        result.status = "FAIL"
        if warning not in result.warnings:
            result.warnings.append(warning)
        if "screen_click_point" not in result.missing_capabilities:
            result.missing_capabilities.append("screen_click_point")
        if isinstance(result.action_trace, dict):
            result.action_trace["movementSafetyPreflight"] = dict(movement_safety_preflight or {})
            _set_trace_final(result, "blocked_movement_safety_region")
        if isinstance(result.click_point_resolution, dict):
            result.click_point_resolution = dict(result.click_point_resolution)
            result.click_point_resolution["movementSafetyPreflight"] = dict(movement_safety_preflight or {})
        result.observed_result = {
            "observedResult": "no_click_safety_block",
            "resultOutcome": "blocked",
            "resultComplete": True,
            "nextActionAllowed": False,
            "verificationStatus": "FAIL",
            "skipReason": str((movement_safety_preflight or {}).get("reason") or "movement_safety_preflight_failed"),
            "movementSafetyPreflight": dict(movement_safety_preflight or {}),
        }
        result.verification_status = "FAIL"
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = str((movement_safety_preflight or {}).get("reason") or "movement_safety_preflight_failed")
        lifecycle.observed_result = result.observed_result
        lifecycle.result_complete = True
        lifecycle.result_outcome = "blocked"
        lifecycle.next_action_allowed = False
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    start = MousePoint(*_backend_position(backend))
    if camera_reacquired_before_hover is not None:
        plan = camera_reacquired_before_hover["movementPlan"]
    else:
        plan = input_controller.plan_mouse_movement(
            start,
            _target_from_click(screen_point),
            movement_profile,
            context=_human_context(proposal, "primary_action_move"),
        )
        result.movement_plan = plan.to_dict(include_points=False)
    if isinstance(result.action_trace, dict):
        _set_trace_intended_point(
            result,
            proposal,
            canvas_point=_canvas_click_point_for_hover(proposal),
            screen_point=screen_point,
            click_resolution=click_resolution,
        )
    if plan.validation_status == "FAIL":
        result.status = "FAIL"
        result.warnings.extend(plan.warnings)
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = "movement_plan_invalid"
        _apply_lifecycle(result, lifecycle)
        _attach_human_input_trace(result, input_controller)
        return result
    if hover_options is not None:
        canvas_point = _canvas_click_point_for_hover(proposal)
        if not canvas_point:
            result.status = "FAIL"
            result.missing_capabilities.append("canvas_hover_point")
            result.warnings.append("hover confirmation requires a canvas aim point")
            lifecycle = lifecycle_state_for_proposal(proposal)
            lifecycle.current_state = "blocked"
            lifecycle.reason = "hover_canvas_point_unavailable"
            _apply_lifecycle(result, lifecycle)
            _attach_human_input_trace(result, input_controller)
            return result
        current_x, current_y = _backend_position(backend)
        already_at_screen_point = (
            abs(int(current_x) - int(screen_point["x"])) <= 3
            and abs(int(current_y) - int(screen_point["y"])) <= 3
        )
        pre_hover_started_wall_millis = int(wall_time_millis_func())
        pre_move_confirmation = (
            _confirm_hover_menu(
                proposal,
                hover_options,
                canvas_point,
                move_started_wall_millis=pre_hover_started_wall_millis,
                max_requests=1,
                snapshot_fetch_func=snapshot_fetch_func,
                sleep_func=sleep_func,
                monotonic_func=monotonic_func,
            )
            if already_at_screen_point
            else {}
        )
        confirmation: dict[str, Any] | None = None
        if pre_move_confirmation.get("confirmed") is True:
            result.commands.append(
                {
                    "type": "hover_confirm",
                    "snapshotUrl": hover_options.snapshot_url,
                    "timeoutMs": hover_options.timeout_ms,
                    "pollMs": hover_options.poll_ms,
                    "positionTolerancePx": hover_options.tolerance_px,
                    "expectedCanvasPoint": dict(canvas_point),
                    "preMove": True,
                }
            )
            confirmation = pre_move_confirmation
            result.hover_confirmation = confirmation
            _update_trace_from_hover(result, confirmation)
        elif camera_reacquired_before_hover is not None:
            move_started_wall_millis = int(camera_reacquired_before_hover.get("moveStartedWallMillis") or wall_time_millis_func())
            confirmation = result.hover_confirmation if isinstance(result.hover_confirmation, dict) else camera_reacquired_before_hover["confirmation"]
        else:
            result.commands.append(
                {
                    "type": "move",
                    "pointCount": len(plan.points),
                    "clickPoint": plan.click_point.to_dict(),
                }
            )
            move_started_wall_millis = int(wall_time_millis_func())
            if isinstance(result.action_trace, dict):
                result.action_trace["mouseMove"]["startWallTimeMillis"] = move_started_wall_millis
                result.action_trace["mouseMove"]["startScreenPoint"] = start.to_dict()
                result.action_trace["mouseMove"]["plannedEndScreenPoint"] = plan.click_point.to_dict()
            try:
                input_controller.move_mouse(plan, context=_human_context(proposal, "hover_move"))
                if isinstance(result.action_trace, dict):
                    result.action_trace["mouseMove"]["endWallTimeMillis"] = int(wall_time_millis_func())
                    _attach_human_input_trace(result, input_controller)
            except Exception as error:  # noqa: BLE001
                fallback_confirmation = _confirm_hover_menu(
                    proposal,
                    hover_options,
                    canvas_point,
                    move_started_wall_millis=move_started_wall_millis,
                    max_requests=1,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                )
                if fallback_confirmation.get("confirmed") is True:
                    result.warnings.append(
                        f"hover movement reported {type(error).__name__} after reaching target; using confirmed hover proof"
                    )
                    result.commands.append(
                        {
                            "type": "hover_confirm",
                            "snapshotUrl": hover_options.snapshot_url,
                            "timeoutMs": hover_options.timeout_ms,
                            "pollMs": hover_options.poll_ms,
                            "positionTolerancePx": hover_options.tolerance_px,
                            "expectedCanvasPoint": dict(canvas_point),
                            "postMoveFailure": True,
                        }
                    )
                    confirmation = fallback_confirmation
                    result.hover_confirmation = confirmation
                    _update_trace_from_hover(result, confirmation)
                else:
                    result.status = "FAIL"
                    result.warnings.append(f"hover movement failed: {type(error).__name__}: {error}")
                    result.missing_capabilities.append("hover_movement")
                    lifecycle = lifecycle_state_for_proposal(proposal)
                    lifecycle.current_state = "blocked"
                    lifecycle.reason = "hover_movement_failed"
                    _apply_lifecycle(result, lifecycle)
                    _attach_human_input_trace(result, input_controller)
                    return result
            if confirmation is None:
                result.commands.append(
                    {
                        "type": "hover_confirm",
                        "snapshotUrl": hover_options.snapshot_url,
                        "timeoutMs": hover_options.timeout_ms,
                        "pollMs": hover_options.poll_ms,
                        "positionTolerancePx": hover_options.tolerance_px,
                        "expectedCanvasPoint": dict(canvas_point),
                    }
                )
                confirmation = _confirm_hover_menu(
                    proposal,
                    hover_options,
                    canvas_point,
                    move_started_wall_millis=move_started_wall_millis,
                    max_requests=_max_hover_checks_per_waypoint(navigation_options) if _is_navigation_path_proposal(proposal) else None,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                )
                result.hover_confirmation = confirmation
                _update_trace_from_hover(result, confirmation)
        if confirmation is None:
            result.status = "FAIL"
            result.warnings.append("hover confirmation did not run")
            result.missing_capabilities.append("hover_confirmation")
            lifecycle = lifecycle_state_for_proposal(proposal)
            lifecycle.current_state = "blocked"
            lifecycle.reason = "hover_confirmation_missing"
            _apply_lifecycle(result, lifecycle)
            _attach_human_input_trace(result, input_controller)
            return result
        if confirmation.get("confirmed") is not True:
            resource_ambiguity = _resource_target_ambiguity(proposal, confirmation, rejection_reason=confirmation.get("reason"))
            if isinstance(resource_ambiguity, dict):
                if isinstance(result.action_trace, dict):
                    result.action_trace["resourceTargetAmbiguity"] = dict(resource_ambiguity)
                    selected = result.action_trace.get("selectedTarget")
                    if isinstance(selected, dict):
                        selected["resourceTargetAmbiguity"] = dict(resource_ambiguity)
                if isinstance(result.proposal, dict):
                    explanation = result.proposal.get("targetExplanation")
                    if isinstance(explanation, dict):
                        explanation["resourceTargetAmbiguity"] = dict(resource_ambiguity)
            resource_observed = _try_resource_observed_hover_retarget(
                proposal,
                confirmation,
                backend=backend,
                hover_options=hover_options,
            )
            if isinstance(result.action_trace, dict) and isinstance(resource_observed, dict):
                reacquisition = result.action_trace.setdefault("reacquisition", {})
                reacquisition["resourceObservedHoverRetarget"] = {
                    key: value
                    for key, value in resource_observed.items()
                    if key not in {"proposal", "confirmation"}
                }
            if isinstance(resource_observed, dict) and resource_observed.get("accepted") is True:
                proposal = resource_observed["proposal"]
                screen_point = dict(resource_observed["screenPoint"])
                canvas_point = dict(resource_observed["canvasPoint"])
                confirmation = resource_observed["confirmation"]
                plan = input_controller.plan_mouse_movement(
                    MousePoint(*_backend_position(backend)),
                    _target_from_click(screen_point),
                    movement_profile,
                    context=_human_context(proposal, "resource_observed_hover_retarget"),
                )
                result.proposal = proposal.to_dict()
                result.click_point_resolution = proposal.click_point_resolution
                result.movement_plan = plan.to_dict(include_points=False)
                result.hover_confirmation = confirmation
                result.commands.append(
                    {
                        "type": "resource_retarget_observed_hover",
                        "reason": resource_observed.get("reason"),
                        "selectedCanvasPoint": dict(canvas_point),
                        "selectedScreenPoint": dict(screen_point),
                    }
                )
                if isinstance(result.action_trace, dict):
                    _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                    reacquisition = result.action_trace.setdefault("reacquisition", {})
                    reacquisition["resourceAimpointReacquired"] = True
                    reacquisition["resourceRetargetedToObservedHover"] = True
                    if isinstance(proposal.target_explanation, dict):
                        result.action_trace["resourceTargetAmbiguity"] = proposal.target_explanation.get("resourceTargetAmbiguity")
                        selected = result.action_trace.get("selectedTarget")
                        if isinstance(selected, dict):
                            selected["resourceTargetAmbiguity"] = proposal.target_explanation.get("resourceTargetAmbiguity")
                            selected["resourceHoverRetarget"] = proposal.target_explanation.get("resourceHoverRetarget")
                            selected["selectedAimpointSource"] = proposal.target_explanation.get("selectedAimpointSource")
                            selected["hoverConfirmedTopExpected"] = proposal.target_explanation.get("hoverConfirmedTopExpected")
                _update_trace_from_hover(result, confirmation)
            service_alternate = _try_service_alternate_hover(
                proposal,
                backend=backend,
                movement_profile=movement_profile,
                hover_options=hover_options,
                input_controller=input_controller,
                current_canvas_point=canvas_point,
                snapshot_fetch_func=snapshot_fetch_func,
                sleep_func=sleep_func,
                monotonic_func=monotonic_func,
                wall_time_millis_func=wall_time_millis_func,
            ) if confirmation.get("confirmed") is not True else None
            if isinstance(result.action_trace, dict) and isinstance(service_alternate, dict):
                reacquisition = result.action_trace.setdefault("reacquisition", {})
                if proposal.proposed_action == "interact_service_route_object":
                    reacquisition["routeObjectAlternateAimpoints"] = list(service_alternate.get("attempts") or [])
                else:
                    reacquisition["serviceAlternateAimpoints"] = list(service_alternate.get("attempts") or [])
            if isinstance(service_alternate, dict) and service_alternate.get("warning"):
                result.warnings.append(str(service_alternate.get("warning")))
            if isinstance(service_alternate, dict) and service_alternate.get("accepted") is True:
                reacquire_type = "route_object_reacquire_alternate_aimpoint" if proposal.proposed_action == "interact_service_route_object" else "service_reacquire_alternate_aimpoint"
                reacquire_reason = "primary_route_object_hover_mismatch" if proposal.proposed_action == "interact_service_route_object" else "primary_service_hover_mismatch"
                proposal = service_alternate["proposal"]
                screen_point = dict(service_alternate["screenPoint"])
                canvas_point = dict(service_alternate["canvasPoint"])
                plan = service_alternate["movementPlan"]
                confirmation = service_alternate["confirmation"]
                result.proposal = proposal.to_dict()
                result.click_point_resolution = proposal.click_point_resolution
                result.movement_plan = plan.to_dict(include_points=False)
                result.hover_confirmation = confirmation
                result.commands.append(
                    {
                        "type": reacquire_type,
                        "reason": reacquire_reason,
                        "attemptCount": len(service_alternate.get("attempts") or []),
                        "selectedCanvasPoint": dict(canvas_point),
                    }
                )
                result.commands.append(
                    {
                        "type": "move",
                        "pointCount": len(plan.points),
                        "clickPoint": plan.click_point.to_dict(),
                    }
                )
                result.commands.append(
                    {
                        "type": "hover_confirm",
                        "snapshotUrl": hover_options.snapshot_url,
                        "timeoutMs": hover_options.timeout_ms,
                        "pollMs": hover_options.poll_ms,
                        "positionTolerancePx": hover_options.tolerance_px,
                        "expectedCanvasPoint": dict(canvas_point),
                    }
                )
                if isinstance(result.action_trace, dict):
                    _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                    reacquisition = result.action_trace.setdefault("reacquisition", {})
                    if proposal.proposed_action == "interact_service_route_object":
                        reacquisition["routeObjectAimpointReacquired"] = True
                    else:
                        reacquisition["serviceAimpointReacquired"] = True
                _update_trace_from_hover(result, confirmation)
            resource_alternate = (
                _try_resource_alternate_hover(
                    proposal,
                    backend=backend,
                    movement_profile=movement_profile,
                    hover_options=hover_options,
                    input_controller=input_controller,
                    current_canvas_point=canvas_point,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                    wall_time_millis_func=wall_time_millis_func,
                )
                if confirmation.get("confirmed") is not True and _resource_ambiguity_recoverable(resource_ambiguity)
                else None
            )
            if isinstance(result.action_trace, dict) and isinstance(resource_alternate, dict):
                reacquisition = result.action_trace.setdefault("reacquisition", {})
                reacquisition["resourceAlternateAimpoints"] = list(resource_alternate.get("attempts") or [])
                reacquisition["resourceViewRecoveryResult"] = resource_alternate.get("reason")
            if isinstance(resource_alternate, dict) and resource_alternate.get("warning"):
                result.warnings.append(str(resource_alternate.get("warning")))
            if isinstance(resource_alternate, dict) and resource_alternate.get("accepted") is True:
                proposal = resource_alternate["proposal"]
                screen_point = dict(resource_alternate["screenPoint"])
                canvas_point = dict(resource_alternate["canvasPoint"])
                plan = resource_alternate["movementPlan"]
                confirmation = resource_alternate["confirmation"]
                result.proposal = proposal.to_dict()
                result.click_point_resolution = proposal.click_point_resolution
                result.movement_plan = plan.to_dict(include_points=False)
                result.hover_confirmation = confirmation
                result.commands.append(
                    {
                        "type": "resource_reacquire_alternate_aimpoint",
                        "reason": "primary_resource_hover_mismatch",
                        "attemptCount": len(resource_alternate.get("attempts") or []),
                        "selectedCanvasPoint": dict(canvas_point),
                    }
                )
                result.commands.append(
                    {
                        "type": "move",
                        "pointCount": len(plan.points),
                        "clickPoint": plan.click_point.to_dict(),
                    }
                )
                result.commands.append(
                    {
                        "type": "hover_confirm",
                        "snapshotUrl": hover_options.snapshot_url,
                        "timeoutMs": hover_options.timeout_ms,
                        "pollMs": hover_options.poll_ms,
                        "positionTolerancePx": hover_options.tolerance_px,
                        "expectedCanvasPoint": dict(canvas_point),
                    }
                )
                if isinstance(result.action_trace, dict):
                    _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                    reacquisition = result.action_trace.setdefault("reacquisition", {})
                    reacquisition["resourceAimpointReacquired"] = True
                    if isinstance(proposal.target_explanation, dict):
                        result.action_trace["resourceTargetAmbiguity"] = proposal.target_explanation.get("resourceTargetAmbiguity")
                _update_trace_from_hover(result, confirmation)
            alternate = (
                _try_navigation_alternate_hover(
                    proposal,
                    backend=backend,
                    movement_profile=movement_profile,
                    hover_options=hover_options,
                    snapshot_url=snapshot_url,
                    navigation_options=navigation_options,
                    input_controller=input_controller,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                    wall_time_millis_func=wall_time_millis_func,
                )
                if _max_navigation_reacquire_rounds(navigation_options) >= 1
                else {"accepted": False, "attempts": []}
            ) if confirmation.get("confirmed") is not True else {"accepted": False, "attempts": []}
            if isinstance(result.action_trace, dict) and isinstance(alternate, dict):
                result.action_trace.setdefault("reacquisition", {})["navigationAlternateWaypoints"] = list(alternate.get("attempts") or [])
            if isinstance(alternate, dict) and alternate.get("warning"):
                result.warnings.append(str(alternate.get("warning")))
            if isinstance(alternate, dict) and alternate.get("accepted") is True:
                proposal = alternate["proposal"]
                screen_point = dict(alternate["screenPoint"])
                canvas_point = dict(alternate["canvasPoint"])
                plan = alternate["movementPlan"]
                confirmation = alternate["confirmation"]
                result.proposal = proposal.to_dict()
                result.click_point_resolution = proposal.click_point_resolution
                result.movement_plan = plan.to_dict(include_points=False)
                result.hover_confirmation = confirmation
                result.commands.append(
                    {
                        "type": "navigation_reacquire_alternate_waypoint",
                        "reason": "primary_waypoint_hover_mismatch",
                        "attemptCount": len(alternate.get("attempts") or []),
                        "selectedTargetTile": dict(proposal.target_tile or {}),
                    }
                )
                result.commands.append(
                    {
                        "type": "move",
                        "pointCount": len(plan.points),
                        "clickPoint": plan.click_point.to_dict(),
                    }
                )
                result.commands.append(
                    {
                        "type": "hover_confirm",
                        "snapshotUrl": hover_options.snapshot_url,
                        "timeoutMs": hover_options.timeout_ms,
                        "pollMs": hover_options.poll_ms,
                        "positionTolerancePx": hover_options.tolerance_px,
                        "expectedCanvasPoint": dict(canvas_point),
                    }
                )
                if isinstance(result.action_trace, dict):
                    _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                _update_trace_from_hover(result, confirmation)
            else:
                failure_reason = _navigation_hover_failure_reason(proposal, confirmation)
                if isinstance(result.action_trace, dict) and _is_navigation_path_proposal(proposal):
                    reacquisition = result.action_trace.setdefault("reacquisition", {})
                    reacquisition["primaryWaypointFailure"] = failure_reason
                    reacquisition["primaryWaypointBlockingMenu"] = _hover_menu_label(confirmation)
                if _is_navigation_path_proposal(proposal) and failure_reason == "waypoint_occluded_by_object":
                    result.warnings.append(f"navigation waypoint occluded by hover menu: {_hover_menu_label(confirmation)}")
                    camera_reacquire = _try_navigation_camera_guided_reacquire(
                        proposal,
                        backend=backend,
                        movement_profile=movement_profile,
                        hover_options=hover_options,
                        snapshot_url=snapshot_url,
                        navigation_options=navigation_options,
                        input_controller=input_controller,
                        primary_confirmation=confirmation,
                        snapshot_fetch_func=snapshot_fetch_func,
                        sleep_func=sleep_func,
                        monotonic_func=monotonic_func,
                        wall_time_millis_func=wall_time_millis_func,
                    )
                    if isinstance(camera_reacquire, dict):
                        if isinstance(result.action_trace, dict):
                            reacquisition = result.action_trace.setdefault("reacquisition", {})
                            trigger = _camera_trigger_from_navigation_failure("waypoint_occluded_by_object")
                            if trigger:
                                reacquisition["cameraTriggeredBy"] = trigger
                            projection_before = _proposal_projection(proposal)
                            if isinstance(projection_before, dict):
                                reacquisition["projectionBefore"] = dict(projection_before)
                            reacquisition["cameraExposureAttempts"] = list(camera_reacquire.get("attempts") or [])
                            reacquisition["waypointReacquiredByCamera"] = bool(camera_reacquire.get("accepted") is True)
                        if camera_reacquire.get("warning"):
                            result.warnings.append(str(camera_reacquire.get("warning")))
                        if camera_reacquire.get("accepted") is True:
                            proposal = camera_reacquire["proposal"]
                            screen_point = dict(camera_reacquire["screenPoint"])
                            canvas_point = dict(camera_reacquire["canvasPoint"])
                            plan = camera_reacquire["movementPlan"]
                            confirmation = camera_reacquire["confirmation"]
                            result.proposal = proposal.to_dict()
                            result.click_point_resolution = proposal.click_point_resolution
                            if isinstance(result.action_trace, dict):
                                projection_after = _proposal_projection(proposal)
                                if isinstance(projection_after, dict):
                                    result.action_trace.setdefault("reacquisition", {})["projectionAfter"] = dict(projection_after)
                            result.movement_plan = plan.to_dict(include_points=False)
                            result.hover_confirmation = confirmation
                            result.commands.append(
                                {
                                    "type": "navigation_reacquire_camera_waypoint",
                                    "reason": "waypoint_occluded_by_object",
                                    "attemptCount": len(camera_reacquire.get("attempts") or []),
                                    "selectedTargetTile": dict(proposal.target_tile or {}),
                                }
                            )
                            result.commands.append(
                                {
                                    "type": "move",
                                    "pointCount": len(plan.points),
                                    "clickPoint": plan.click_point.to_dict(),
                                }
                            )
                            result.commands.append(
                                {
                                    "type": "hover_confirm",
                                    "snapshotUrl": hover_options.snapshot_url,
                                    "timeoutMs": hover_options.timeout_ms,
                                    "pollMs": hover_options.poll_ms,
                                    "positionTolerancePx": hover_options.tolerance_px,
                                    "expectedCanvasPoint": dict(canvas_point),
                                }
                            )
                            if isinstance(result.action_trace, dict):
                                _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                                result.action_trace.setdefault("reacquisition", {})["waypointReacquiredAfterCamera"] = True
                            _update_trace_from_hover(result, confirmation)
                    if confirmation.get("confirmed") is not True and not _camera_reacquire_waypoint_enabled(navigation_options):
                        camera_event = _try_navigation_camera_adjustment(
                            proposal,
                            backend=backend,
                            navigation_options=navigation_options,
                            sleep_func=sleep_func,
                            input_controller=input_controller,
                        )
                        if camera_event:
                            result.commands.append({"type": "camera_adjustment", **camera_event})
                            if isinstance(result.action_trace, dict):
                                result.action_trace.setdefault("reacquisition", {})["cameraAdjustment"] = dict(camera_event)
                            if camera_event.get("status") == "PASS" and _max_navigation_reacquire_rounds(navigation_options) >= 2:
                                alternate = _try_navigation_alternate_hover(
                                    proposal,
                                    backend=backend,
                                    movement_profile=movement_profile,
                                    hover_options=hover_options,
                                    snapshot_url=snapshot_url,
                                    navigation_options=navigation_options,
                                    input_controller=input_controller,
                                    snapshot_fetch_func=snapshot_fetch_func,
                                    sleep_func=sleep_func,
                                    monotonic_func=monotonic_func,
                                    wall_time_millis_func=wall_time_millis_func,
                                )
                                if isinstance(result.action_trace, dict) and isinstance(alternate, dict):
                                    result.action_trace.setdefault("reacquisition", {})["navigationAlternateWaypointsAfterCamera"] = list(alternate.get("attempts") or [])
                                if isinstance(alternate, dict) and alternate.get("accepted") is True:
                                    proposal = alternate["proposal"]
                                    screen_point = dict(alternate["screenPoint"])
                                    canvas_point = dict(alternate["canvasPoint"])
                                    plan = alternate["movementPlan"]
                                    confirmation = alternate["confirmation"]
                                    result.proposal = proposal.to_dict()
                                    result.click_point_resolution = proposal.click_point_resolution
                                    result.movement_plan = plan.to_dict(include_points=False)
                                    result.hover_confirmation = confirmation
                                    result.commands.append(
                                        {
                                            "type": "navigation_reacquire_after_camera",
                                            "reason": "waypoint_occluded_by_object",
                                            "attemptCount": len(alternate.get("attempts") or []),
                                            "selectedTargetTile": dict(proposal.target_tile or {}),
                                        }
                                    )
                                    if isinstance(result.action_trace, dict):
                                        _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                                        result.action_trace.setdefault("reacquisition", {})["waypointReacquiredAfterCamera"] = True
                                    _update_trace_from_hover(result, confirmation)
            if confirmation.get("confirmed") is not True:
                result.status = "FAIL"
                reason = _navigation_hover_failure_reason(proposal, confirmation)
                result.warnings.append(f"hover confirmation failed: {reason}; top menu={_hover_menu_label(confirmation)}")
                if "hover_menu" not in result.missing_capabilities:
                    result.missing_capabilities.append("hover_menu")
                lifecycle = lifecycle_state_for_proposal(proposal)
                lifecycle.current_state = "blocked"
                lifecycle.reason = "hover_confirm_timeout"
                _apply_lifecycle(result, lifecycle)
                _set_trace_final(result, "hover_confirm_timeout")
                _attach_human_input_trace(result, input_controller)
                return result
        if confirmation.get("confirmed") is not True:
            result.status = "FAIL"
            result.warnings.append(f"hover confirmation failed: {confirmation.get('reason') or 'unknown'}")
            if "hover_menu" not in result.missing_capabilities:
                result.missing_capabilities.append("hover_menu")
            lifecycle = lifecycle_state_for_proposal(proposal)
            lifecycle.current_state = "blocked"
            lifecycle.reason = "hover_confirm_timeout"
            _apply_lifecycle(result, lifecycle)
            _set_trace_final(result, "hover_confirm_timeout")
            _attach_human_input_trace(result, input_controller)
            return result
        if hover_options.hover_only:
            lifecycle = lifecycle_after_execution(proposal, executed=False, dry_run=True)
            lifecycle.reason = "hover_only_confirmed"
            _apply_lifecycle(result, lifecycle)
            _set_trace_final(result, "hover_confirmed_click")
            _attach_human_input_trace(result, input_controller)
            return result
        if not dry_run:
            pre_click_started_wall_millis = int(wall_time_millis_func())
            pre_click_confirmation = _confirm_hover_menu(
                proposal,
                hover_options,
                canvas_point,
                move_started_wall_millis=pre_click_started_wall_millis,
                max_requests=_max_hover_checks_per_waypoint(navigation_options) if _is_navigation_path_proposal(proposal) else None,
                snapshot_fetch_func=snapshot_fetch_func,
                sleep_func=sleep_func,
                monotonic_func=monotonic_func,
            )
            result.commands.append(
                {
                    "type": "pre_click_hover_confirm",
                    "snapshotUrl": hover_options.snapshot_url,
                    "timeoutMs": hover_options.timeout_ms,
                    "pollMs": hover_options.poll_ms,
                    "positionTolerancePx": hover_options.tolerance_px,
                    "expectedCanvasPoint": dict(canvas_point),
                    "status": pre_click_confirmation.get("status"),
                    "reason": pre_click_confirmation.get("reason"),
                }
            )
            if isinstance(result.hover_confirmation, dict):
                result.hover_confirmation["preClickConfirmation"] = pre_click_confirmation
            if pre_click_confirmation.get("confirmed") is not True:
                navigation_walk_entry = _navigation_walk_here_menu_entry(proposal, pre_click_confirmation)
                if navigation_walk_entry is not None:
                    if isinstance(result.action_trace, dict):
                        result.action_trace["navigationWalkHereMenuCandidate"] = dict(navigation_walk_entry)
                    before_click = pre_click_confirmation.get("lastMenuOptionClickedBefore") if isinstance(pre_click_confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                    if before_click is None:
                        before_click = confirmation.get("lastMenuOptionClickedBefore") if isinstance(confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                    try:
                        walk_selected = _execute_route_transition_direct_menu_selection(
                            proposal,
                            direct_entry=navigation_walk_entry,
                            screen_point=screen_point,
                            plan=plan,
                            result=result,
                            hover_options=hover_options,
                            input_controller=input_controller,
                            backend=backend,
                            before_click=before_click,
                            snapshot_fetch_func=snapshot_fetch_func,
                            sleep_func=sleep_func,
                            monotonic_func=monotonic_func,
                            wall_time_millis_func=wall_time_millis_func,
                            entry_matcher=_entry_matches_navigation_walk_here,
                            event_source="navigation_walk_here_lower_menu_entry",
                            open_reason="navigation_walk_here_lower_menu_entry",
                            row_label="navigation walk here menu row",
                        )
                    except Exception as error:  # noqa: BLE001
                        result.status = "FAIL"
                        result.warnings.append(f"right-click navigation Walk here menu selection failed: {type(error).__name__}: {error}")
                        lifecycle = lifecycle_state_for_proposal(proposal)
                        lifecycle.current_state = "blocked"
                        lifecycle.reason = "right_click_walk_here_menu_select_failed"
                        _apply_lifecycle(result, lifecycle)
                        _set_trace_final(result, "right_click_menu_select_failed")
                        _attach_human_input_trace(result, input_controller)
                        return result
                    if walk_selected:
                        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
                        _apply_lifecycle(result, lifecycle)
                        _set_trace_final(result, "right_click_walk_here_selected")
                        _attach_human_input_trace(result, input_controller)
                        return result
                    result.status = "FAIL"
                    lifecycle = lifecycle_state_for_proposal(proposal)
                    lifecycle.current_state = "blocked"
                    lifecycle.reason = "right_click_walk_here_menu_select_failed"
                    _apply_lifecycle(result, lifecycle)
                    _set_trace_final(result, "right_click_menu_select_failed")
                    _attach_human_input_trace(result, input_controller)
                    return result
                result.status = "FAIL"
                result.warnings.append(f"pre-click hover confirmation failed: {pre_click_confirmation.get('reason') or 'unknown'}")
                if "hover_menu" not in result.missing_capabilities:
                    result.missing_capabilities.append("hover_menu")
                lifecycle = lifecycle_state_for_proposal(proposal)
                lifecycle.current_state = "blocked"
                lifecycle.reason = "pre_click_hover_confirm_failed"
                _apply_lifecycle(result, lifecycle)
                _set_trace_final(result, "hover_mismatch_skipped")
                _attach_human_input_trace(result, input_controller)
                return result
            _update_trace_from_hover(result, pre_click_confirmation)
            volatility = _navigation_volatile_hover_zone(proposal, pre_click_confirmation)
            if volatility is not None:
                volatile_alternate = (
                    _try_navigation_alternate_hover(
                        proposal,
                        backend=backend,
                        movement_profile=movement_profile,
                        hover_options=hover_options,
                        snapshot_url=snapshot_url,
                        navigation_options=navigation_options,
                        input_controller=input_controller,
                        snapshot_fetch_func=snapshot_fetch_func,
                        sleep_func=sleep_func,
                        monotonic_func=monotonic_func,
                        wall_time_millis_func=wall_time_millis_func,
                    )
                    if _is_navigation_path_proposal(proposal) and _max_navigation_reacquire_rounds(navigation_options) >= 1
                    else {"accepted": False, "attempts": []}
                )
                if isinstance(result.action_trace, dict) and isinstance(volatile_alternate, dict):
                    result.action_trace.setdefault("reacquisition", {})["navigationAlternateWaypointsAfterVolatileHover"] = list(
                        volatile_alternate.get("attempts") or []
                    )
                if isinstance(volatile_alternate, dict) and volatile_alternate.get("accepted") is True:
                    proposal = volatile_alternate["proposal"]
                    screen_point = dict(volatile_alternate["screenPoint"])
                    canvas_point = dict(volatile_alternate["canvasPoint"])
                    plan = volatile_alternate["movementPlan"]
                    confirmation = volatile_alternate["confirmation"]
                    result.proposal = proposal.to_dict()
                    result.click_point_resolution = proposal.click_point_resolution
                    result.movement_plan = plan.to_dict(include_points=False)
                    result.hover_confirmation = confirmation
                    result.commands.append(
                        {
                            "type": "navigation_reacquire_volatile_waypoint",
                            "reason": "volatile_hover_zone",
                            "attemptCount": len(volatile_alternate.get("attempts") or []),
                            "selectedTargetTile": dict(proposal.target_tile or {}),
                        }
                    )
                    result.commands.append(
                        {
                            "type": "move",
                            "pointCount": len(plan.points),
                            "clickPoint": plan.click_point.to_dict(),
                        }
                    )
                    result.commands.append(
                        {
                            "type": "hover_confirm",
                            "snapshotUrl": hover_options.snapshot_url,
                            "timeoutMs": hover_options.timeout_ms,
                            "pollMs": hover_options.poll_ms,
                            "positionTolerancePx": hover_options.tolerance_px,
                            "expectedCanvasPoint": dict(canvas_point),
                        }
                    )
                    if isinstance(result.action_trace, dict):
                        _set_trace_intended_point(result, proposal, canvas_point=canvas_point, screen_point=screen_point)
                        result.action_trace.setdefault("reacquisition", {})["waypointReacquiredAfterVolatileHover"] = True
                    _update_trace_from_hover(result, confirmation)
                    pre_click_started_wall_millis = int(wall_time_millis_func())
                    pre_click_confirmation = _confirm_hover_menu(
                        proposal,
                        hover_options,
                        canvas_point,
                        move_started_wall_millis=pre_click_started_wall_millis,
                        max_requests=_max_hover_checks_per_waypoint(navigation_options),
                        snapshot_fetch_func=snapshot_fetch_func,
                        sleep_func=sleep_func,
                        monotonic_func=monotonic_func,
                    )
                    result.commands.append(
                        {
                            "type": "pre_click_hover_confirm",
                            "snapshotUrl": hover_options.snapshot_url,
                            "timeoutMs": hover_options.timeout_ms,
                            "pollMs": hover_options.poll_ms,
                            "positionTolerancePx": hover_options.tolerance_px,
                            "expectedCanvasPoint": dict(canvas_point),
                            "status": pre_click_confirmation.get("status"),
                            "reason": pre_click_confirmation.get("reason"),
                        }
                    )
                    if isinstance(result.hover_confirmation, dict):
                        result.hover_confirmation["preClickConfirmationAfterVolatileReacquire"] = pre_click_confirmation
                    if pre_click_confirmation.get("confirmed") is not True:
                        result.status = "FAIL"
                        result.warnings.append(f"pre-click hover confirmation failed after volatile waypoint reacquire: {pre_click_confirmation.get('reason') or 'unknown'}")
                        if "hover_menu" not in result.missing_capabilities:
                            result.missing_capabilities.append("hover_menu")
                        lifecycle = lifecycle_state_for_proposal(proposal)
                        lifecycle.current_state = "blocked"
                        lifecycle.reason = "pre_click_hover_confirm_failed"
                        _apply_lifecycle(result, lifecycle)
                        _set_trace_final(result, "hover_mismatch_skipped")
                        _attach_human_input_trace(result, input_controller)
                        return result
                    _update_trace_from_hover(result, pre_click_confirmation)
                    volatility = _navigation_volatile_hover_zone(proposal, pre_click_confirmation)
                if volatility is not None:
                    result.status = "FAIL"
                    reasons = ", ".join(str(reason) for reason in volatility.get("volatileReasons") or []) or "recent conflicting menu samples"
                    result.warnings.append(f"volatile navigation hover zone; skipping click before mouse-down: {reasons}")
                    if "hover_menu.volatile" not in result.missing_capabilities:
                        result.missing_capabilities.append("hover_menu.volatile")
                    lifecycle = lifecycle_state_for_proposal(proposal)
                    lifecycle.current_state = "blocked"
                    lifecycle.reason = "volatile_hover_zone"
                    _apply_lifecycle(result, lifecycle)
                    _set_trace_final(result, "hover_mismatch_skipped")
                    _attach_human_input_trace(result, input_controller)
                    return result
            direct_menu_entry = _route_transition_direct_menu_entry(proposal, pre_click_confirmation)
            if direct_menu_entry is not None:
                if isinstance(result.action_trace, dict):
                    result.action_trace["routeTransitionDirectMenuCandidate"] = dict(direct_menu_entry)
                before_click = pre_click_confirmation.get("lastMenuOptionClickedBefore") if isinstance(pre_click_confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                if before_click is None:
                    before_click = confirmation.get("lastMenuOptionClickedBefore") if isinstance(confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                try:
                    direct_selected = _execute_route_transition_direct_menu_selection(
                        proposal,
                        direct_entry=direct_menu_entry,
                        screen_point=screen_point,
                        plan=plan,
                        result=result,
                        hover_options=hover_options,
                        input_controller=input_controller,
                        backend=backend,
                        before_click=before_click,
                        snapshot_fetch_func=snapshot_fetch_func,
                        sleep_func=sleep_func,
                        monotonic_func=monotonic_func,
                        wall_time_millis_func=wall_time_millis_func,
                    )
                except Exception as error:  # noqa: BLE001
                    result.status = "FAIL"
                    result.warnings.append(f"right-click route transition menu selection failed: {type(error).__name__}: {error}")
                    lifecycle = lifecycle_state_for_proposal(proposal)
                    lifecycle.current_state = "blocked"
                    lifecycle.reason = "right_click_menu_select_failed"
                    _apply_lifecycle(result, lifecycle)
                    _set_trace_final(result, "right_click_menu_select_failed")
                    _attach_human_input_trace(result, input_controller)
                    return result
                if not direct_selected:
                    direct_selection = result.hover_confirmation.get("rightClickMenuSelection") if isinstance(result.hover_confirmation, dict) else {}
                    direct_selection_reason = str(direct_selection.get("reason") or "") if isinstance(direct_selection, dict) else ""
                    if direct_selection_reason == "clicked_direct_menu_mismatch":
                        result.status = "FAIL"
                        lifecycle = lifecycle_state_for_proposal(proposal)
                        lifecycle.current_state = "blocked"
                        lifecycle.reason = "clicked_direct_menu_mismatch"
                        _apply_lifecycle(result, lifecycle)
                        _set_trace_final(result, "clicked_direct_menu_mismatch")
                        _attach_human_input_trace(result, input_controller)
                        return result
                    if direct_menu_entry.get("syntheticEntry") is True and _route_transition_left_click_is_dialogue_opener(proposal, pre_click_confirmation):
                        result.commands.append(
                            {
                                "type": "route_transition_dialogue_opener_fallback",
                                "reason": "right_click_menu_select_failed",
                                "button": "left",
                            }
                        )
                        if isinstance(result.hover_confirmation, dict):
                            result.hover_confirmation["rightClickMenuSelectionFallback"] = "left_click_dialogue_opener"
                        if isinstance(result.action_trace, dict):
                            result.action_trace["rightClickMenuSelectionFallback"] = "left_click_dialogue_opener"
                    else:
                        result.status = "FAIL"
                        observed = result.observed_result if isinstance(result.observed_result, dict) else {}
                        observed.update(
                            {
                                "observedResult": "route_target_hover_not_confirmed"
                                if direct_selection_reason == "menu_open_not_observed"
                                else "right_click_menu_select_failed",
                                "resultOutcome": "blocked",
                                "resultComplete": True,
                                "nextActionAllowed": True,
                                "verificationStatus": "FAIL",
                                "routeTargetHoverFailure": direct_selection_reason == "menu_open_not_observed",
                                "rightClickMenuSelection": direct_selection if isinstance(direct_selection, dict) else {},
                            }
                        )
                        result.observed_result = observed
                        result.verification_status = "FAIL"
                        lifecycle = lifecycle_state_for_proposal(proposal)
                        lifecycle.current_state = "blocked"
                        lifecycle.reason = (
                            "route_target_hover_not_confirmed"
                            if direct_selection_reason == "menu_open_not_observed"
                            else "right_click_menu_select_failed"
                        )
                        lifecycle.observed_result = observed
                        lifecycle.result_complete = True
                        lifecycle.result_outcome = "blocked"
                        lifecycle.next_action_allowed = True
                        _apply_lifecycle(result, lifecycle)
                        _set_trace_final(
                            result,
                            "route_target_hover_not_confirmed"
                            if direct_selection_reason == "menu_open_not_observed"
                            else "right_click_menu_select_failed",
                        )
                        _attach_human_input_trace(result, input_controller)
                        return result
                lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
                if direct_selected:
                    _apply_lifecycle(result, lifecycle)
                    _set_trace_final(result, "right_click_menu_selected_expected_action")
                    _attach_human_input_trace(result, input_controller)
                    return result
            result.commands.append(
                {
                    "type": "click",
                    "clickPoint": plan.click_point.to_dict(),
                    "button": "left",
                    "clickHoldMs": hover_options.click_hold_ms,
                }
            )
            try:
                _click_confirmed_current_position(
                    input_controller,
                    button="left",
                    hold_ms=hover_options.click_hold_ms,
                    context=_human_context(proposal, "hover_confirmed_click"),
                )
                result.executed = True
                if isinstance(result.action_trace, dict):
                    result.action_trace["clickTimestampWallMillis"] = int(wall_time_millis_func())
                    _attach_human_input_trace(result, input_controller)
                before_click = pre_click_confirmation.get("lastMenuOptionClickedBefore") if isinstance(pre_click_confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                if before_click is None:
                    before_click = confirmation.get("lastMenuOptionClickedBefore") if isinstance(confirmation.get("lastMenuOptionClickedBefore"), dict) else None
                after_click = _poll_last_menu_option_clicked(
                    hover_options,
                    before_click,
                    snapshot_fetch_func=snapshot_fetch_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                )
                result.hover_confirmation["lastMenuOptionClickedAfter"] = after_click
                click_classification = classify_last_menu_option_clicked(before_click, after_click, proposal)
                result.hover_confirmation["clickClassification"] = click_classification
                if isinstance(result.action_trace, dict):
                    client_tick = result.action_trace.setdefault("clientTick", {})
                    client_tick["lastMenuOptionClickedBefore"] = before_click
                    client_tick["lastMenuOptionClickedAfter"] = after_click
                    client_tick["clickedMenuClassification"] = click_classification
                    generic_click = client_tick_core.classify_clicked_menu(
                        before_click,
                        after_click,
                        client_tick_core.action_intent_from_proposal(proposal, tolerance_px=hover_options.tolerance_px),
                    )
                    client_tick["clickedMenuGenericClassification"] = generic_click
                mismatch_is_blocking = (
                    hover_options.require_clicked_menu_match and not _clicked_menu_matches_expected(click_classification)
                ) or _clicked_menu_mismatch_is_known(click_classification)
                if mismatch_is_blocking:
                    mismatch_payload = _menu_mismatch_payload(
                        proposal,
                        hover_before=pre_click_confirmation.get("sample") if isinstance(pre_click_confirmation.get("sample"), dict) else confirmation.get("sample"),
                        actual_clicked=after_click,
                        classification=click_classification,
                    )
                    if isinstance(result.action_trace, dict):
                        client_tick = result.action_trace.setdefault("clientTick", {})
                        client_tick["menuMismatch"] = mismatch_payload
                    result.status = "FAIL"
                    result.warnings.append(f"clicked menu did not match expected action: {click_classification}")
                    lifecycle = lifecycle_state_for_proposal(proposal)
                    lifecycle.current_state = "blocked"
                    lifecycle.reason = "clicked_menu_mismatch"
                    _apply_lifecycle(result, lifecycle)
                    observed = result.observed_result if isinstance(result.observed_result, dict) else {}
                    observed["menuClickClassification"] = click_classification
                    observed["actionResultClassification"] = "menu_flip_mismatch"
                    result.observed_result = observed
                    _set_trace_final(result, "menu_flip_mismatch")
                    _attach_human_input_trace(result, input_controller)
                    return result
            except Exception as error:  # noqa: BLE001
                result.status = "FAIL"
                result.warnings.append(f"hover-confirmed click failed: {type(error).__name__}: {error}")
                lifecycle = lifecycle_state_for_proposal(proposal)
                lifecycle.current_state = "blocked"
                lifecycle.reason = "click_failed"
                _apply_lifecycle(result, lifecycle)
                _set_trace_final(result, "unknown")
                _attach_human_input_trace(result, input_controller)
                return result
        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
        _apply_lifecycle(result, lifecycle)
        if result.hover_confirmation and result.hover_confirmation.get("clickClassification") == "clicked_walk_here":
            _set_trace_final(result, "clicked_walk_here")
        elif result.hover_confirmation and result.hover_confirmation.get("clickClassification") in {"clicked_chop_tree", "clicked_expected_action"}:
            _set_trace_final(result, "clicked_expected_action")
        else:
            _set_trace_final(result, "hover_confirmed_click")
        _attach_human_input_trace(result, input_controller)
        return result
    result.commands.append(
        {
            "type": "move_and_click",
            "pointCount": len(plan.points),
            "clickPoint": plan.click_point.to_dict(),
            "button": "left",
        }
    )
    if not dry_run:
        input_controller.move_and_click(plan, button="left", context=_human_context(proposal, "move_and_click"))
        result.executed = True
    lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
    _apply_lifecycle(result, lifecycle)
    _set_trace_final(result, "unknown")
    _attach_human_input_trace(result, input_controller)
    return result


def execute_next_action(
    daemon_url: str,
    options: Any,
    *,
    fetch_json_func=fetch_json,
    backend: Any | None = None,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> ExecutionResult:
    timeout = float(getattr(options, "timeout", 3.0))
    if backend is None:
        backend = backend_from_options(options)
    preflight_live_input_status = _live_input_status(options, backend)
    if preflight_live_input_status.get("status") == "FAIL":
        return _live_input_blocked_result(options, backend, preflight_live_input_status)
    try:
        status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
    except Exception as error:  # noqa: BLE001
        if bool(getattr(options, "auto_recover_loaded_scene", False)):
            status, recovery = _maybe_auto_recover_loaded_scene(
                daemon_url,
                options,
                status={},
                fetch_json_func=fetch_json_func,
                timeout=timeout,
            )
            if recovery is not None and (
                recovery.get("status") in {"loaded_scene_ready", "recovered_loaded_scene"}
                or _recovery_verified_loaded_scene(recovery)
            ):
                pass
            elif recovery is not None:
                return _blocked_by_liveness_recovery_result(options, backend, recovery)
            else:
                recovery = {
                    "schema": "liveness_recovery_result.v1",
                    "status": "daemon_rebind_failed",
                    "blocker": f"daemon status unavailable before auto recovery: {type(error).__name__}: {error}",
                }
                return _blocked_by_liveness_recovery_result(options, backend, recovery)
        else:
            return ExecutionResult(
                status="FAIL",
                proposed_action="none",
                dry_run=not bool(getattr(options, "execute", False)),
                backend_name=str(getattr(options, "backend", "unknown")),
                movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
                warnings=[f"daemon status unavailable: {type(error).__name__}: {error}"],
                missing_capabilities=["daemon.status"],
            )
    else:
        recovery = None
        status, recovery = _maybe_auto_recover_loaded_scene(
            daemon_url,
            options,
            status=status,
            fetch_json_func=fetch_json_func,
            timeout=timeout,
        )
    if recovery is not None and recovery.get("status") not in {"loaded_scene_ready", "recovered_loaded_scene"} and not _recovery_verified_loaded_scene(recovery):
        return _blocked_by_liveness_recovery_result(options, backend, recovery)
    if is_waiting_for_result(status):
        source_tick = status.get("latestTick") if isinstance(status.get("latestTick"), int) else None
        waiting_proposal = ActionProposal(
            status="WARN",
            proposed_action="wait_for_resource_result",
            target_kind="none",
            reason="already_waiting_for_result",
            confidence=1.0,
            warnings=["already waiting for previous action result"],
            source_tick=source_tick,
        )
        lifecycle = ActionLifecycleState(
            current_state="waiting_for_result",
            last_action=None,
            last_action_tick=source_tick,
            expected_result=expected_result_for_action("select_resource_target"),
            attempts=0,
            max_attempts=1,
            reason="client_processing_previous_action",
            warnings=["already waiting for previous action result"],
        )
        result = ExecutionResult(
            status="WARN",
            proposed_action=waiting_proposal.proposed_action,
            dry_run=not bool(getattr(options, "execute", False)),
            action_id=proposal_action_id(waiting_proposal),
            backend_name=str(getattr(options, "backend", "unknown")),
            movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
            proposal=waiting_proposal.to_dict(),
            warnings=["already waiting for previous action result"],
            expected_result=lifecycle.expected_result,
        )
        result.action_trace = _new_action_trace(waiting_proposal)
        _apply_lifecycle(result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
        if _status_has_navigation_context(status):
            _record_navigation_trace(
                options=options,
                loop_summary=None,
                decision="wait",
                reason="client_processing_previous_action",
                status=status,
                proposal=waiting_proposal,
                observed={
                    "observedResult": "already_waiting_for_result",
                    "resultOutcome": "still_waiting",
                    "resultComplete": False,
                    "nextActionAllowed": False,
                },
                result=result,
            )
        return result
    proposal = build_action_proposal(_status_with_navigation_option_overrides(status, options))
    status, proposal, _context_fallback = _maybe_context_action_proposal(
        daemon_url,
        options,
        status,
        proposal,
        timeout=timeout,
    )
    if proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
        return _blocked_by_no_executable_result(
            proposal,
            status=status,
            options=options,
            reason=proposal.reason or "no_executable_action",
        )
    readiness: dict[str, Any] | None = None
    if _readiness_gate_required(options, proposal):
        status, proposal, readiness = _wait_until_ready(
            daemon_url,
            options,
            status=status,
            proposal=proposal,
            fetch_json_func=fetch_json_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
        )
        if not _readiness_allows_execution(readiness):
            return _blocked_by_readiness_result(proposal, readiness=readiness, options=options)
    live_input_status: dict[str, Any] | None = None
    try:
        live_input_status = _start_live_input_session(options, backend)
    except Exception:  # noqa: BLE001
        status = _live_input_status(options, backend)
        if status.get("status") != "FAIL":
            status["status"] = "FAIL"
            status["blockReason"] = status.get("blockReason") or "arduino_arm_failed"
        return _live_input_blocked_result(options, backend, status, proposed_action=proposal.proposed_action)
    result: ExecutionResult
    try:
        try:
            _ensure_live_input_session_for_action(options, backend, live_input_status)
        except Exception:  # noqa: BLE001
            status = _live_input_status(options, backend)
            status.update(live_input_status if isinstance(live_input_status, dict) else {})
            status["status"] = "FAIL"
            status["blockReason"] = status.get("blockReason") or "arduino_rearm_failed"
            return _live_input_blocked_result(options, backend, status, proposed_action=proposal.proposed_action)
        input_controller = _input_controller_from_options(backend, options, sleep_func=sleep_func, monotonic_func=monotonic_func)
        if proposal.proposed_action in NAVIGATION_ACTIONS or proposal.target_kind == "path_tile":
            _record_navigation_trace(
                options=options,
                loop_summary=None,
                decision="click",
                reason=proposal.reason or "navigation_waypoint_executable",
                status=status,
                proposal=proposal,
                observed={
                    "observedResult": "navigation_click_selected",
                    "resultOutcome": "pending",
                    "resultComplete": False,
                    "nextActionAllowed": False,
                },
            )
        result = execute_action(
            proposal,
            backend=backend,
            movement_profile=_movement_profile_from_options(options),
            dry_run=not bool(getattr(options, "execute", False)),
            hover_options=hover_options_from_options(options),
            navigation_options=options,
            snapshot_url=str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
            snapshot_fetch_func=fetch_plugin_snapshot,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
            input_controller=input_controller,
        )
    finally:
        _finish_live_input_session(backend, live_input_status, options=options)
    result.readiness = readiness
    if recovery is not None:
        if result.readiness is None:
            result.readiness = {}
        result.readiness["livenessRecoveryLastResult"] = recovery
        result.readiness["livenessRecoveryRecommended"] = False
    _attach_readiness_trace(result, readiness)
    _attach_live_input_status(result, live_input_status, options=options, backend=backend)
    _enforce_no_direct_backend_bypass(result, options)
    if bool(getattr(options, "verify_after_action", False)) and not result.executed:
        observed = result.observed_result if isinstance(result.observed_result, dict) else {}
        if not observed:
            observed = {
                "observedResult": "no_click_safety_skip" if result.status != "PASS" else "dry_run_not_executed",
                "resultOutcome": "skipped",
                "resultComplete": True,
                "nextActionAllowed": True,
                "verificationStatus": "SKIPPED",
            }
        result.observed_result = observed
        result.verification_status = str(observed.get("verificationStatus") or "SKIPPED")
    elif bool(getattr(options, "verify_after_action", False)):
        try:
            after_status, observed, elapsed_ms, timeline = _verify_action_after_execution(
                daemon_url=daemon_url,
                options=options,
                action=result.proposed_action,
                proposal=proposal,
                before_status=status,
                fetch_json_func=fetch_json_func,
                sleep_func=sleep_func,
                monotonic_func=monotonic_func,
                timeout=timeout,
            )
            result.verification = after_status
            if result.hover_confirmation:
                observed["menuClickClassification"] = result.hover_confirmation.get("clickClassification")
            result.observed_result = observed
            action_classification = _menu_action_result_classification(result)
            if action_classification:
                result.observed_result["actionResultClassification"] = action_classification
                _set_trace_final(result, _trace_classification_from_observed(action_classification))
            result.verification_status = str(observed.get("verificationStatus") or "UNKNOWN")
            if isinstance(result.action_trace, dict):
                result.action_trace["gameTickVerificationTimeline"].extend(timeline)
            if result.executed:
                lifecycle = lifecycle_after_execution(
                    proposal,
                    executed=True,
                    dry_run=False,
                    before_status=status,
                    after_status=result.verification,
                    cooldown_ms=_cooldown_ms(options),
                    elapsed_ms=elapsed_ms,
                    timeout_ms=_action_timeout_millis_for_verification(result.proposed_action, options),
                    timeout_ticks=_action_timeout_ticks_for_verification(result.proposed_action, options),
                    progress_min_distance=_nav_progress_min_distance(options) if result.proposed_action in NAVIGATION_ACTIONS else None,
                )
                if result.hover_confirmation:
                    lifecycle_observed = lifecycle.observed_result if isinstance(lifecycle.observed_result, dict) else {}
                    lifecycle_observed["menuClickClassification"] = result.hover_confirmation.get("clickClassification")
                    result.observed_result = lifecycle_observed
                    action_classification = _menu_action_result_classification(result)
                    if action_classification:
                        lifecycle_observed["actionResultClassification"] = action_classification
                        _set_trace_final(result, _trace_classification_from_observed(action_classification))
                    lifecycle.observed_result = lifecycle_observed
                if isinstance(lifecycle.observed_result, dict) and isinstance(lifecycle.observed_result.get("navigationInProgress"), dict):
                    lifecycle.current_state = "waiting_for_result"
                    lifecycle.reason = "navigation_in_progress"
                    lifecycle.wait_reason = "route_replan_suppressed_while_moving"
                    lifecycle.result_complete = False
                    lifecycle.result_outcome = "still_waiting"
                    lifecycle.next_action_allowed = False
                _apply_lifecycle(result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
                _update_result_classification_from_observed(result)
            if result.proposed_action in NAVIGATION_ACTIONS:
                decision, trace_reason, recovery_mode = _navigation_decision_from_observed(result.observed_result)
                _record_navigation_trace(
                    options=options,
                    loop_summary=None,
                    decision=decision,
                    reason=trace_reason,
                    status=result.verification if isinstance(result.verification, dict) else status,
                    previous_status=status,
                    proposal=proposal,
                    observed=result.observed_result,
                    result=result,
                    recovery_mode=recovery_mode,
                )
        except Exception as error:  # noqa: BLE001
            result.warnings.append(f"verification failed: {type(error).__name__}: {error}")
            if result.status == "PASS":
                result.status = "WARN"
    return result


def _safe_monotonic(monotonic_func: Any) -> float | None:
    try:
        return float(monotonic_func())
    except StopIteration:
        return None


def execute_action_loop(
    daemon_url: str,
    options: Any,
    *,
    fetch_json_func=fetch_json,
    snapshot_fetch_func=fetch_plugin_snapshot,
    backend: Any | None = None,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> LoopExecutionResult:
    max_actions = max(0, int(getattr(options, "max_actions", 1) or 1))
    max_total_actions = max(0, int(getattr(options, "max_total_actions", 0) or 0))
    max_runtime_seconds = max(0.0, float(getattr(options, "max_runtime_seconds", 0.0) or 0.0))
    timeout = float(getattr(options, "timeout", 3.0))
    dry_run = not bool(getattr(options, "execute", False))
    backend = backend or backend_from_options(options)
    recovery: dict[str, Any] | None = None
    if bool(getattr(options, "auto_recover_loaded_scene", False)):
        try:
            pre_status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
            _updated_status, recovery = _maybe_auto_recover_loaded_scene(
                daemon_url,
                options,
                status=pre_status,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001
            pre_status = {}
            _updated_status, recovery = _maybe_auto_recover_loaded_scene(
                daemon_url,
                options,
                status=pre_status,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
            )
            if recovery is None:
                recovery = {
                    "schema": "liveness_recovery_result.v1",
                    "status": "daemon_rebind_failed",
                    "blocker": f"daemon status unavailable before auto recovery: {type(error).__name__}: {error}",
                }
        if recovery is not None and recovery.get("status") not in {"loaded_scene_ready", "recovered_loaded_scene"} and not _recovery_verified_loaded_scene(recovery):
            summary = _new_loop_summary()
            summary["livenessRecoveryLastResult"] = recovery
            return LoopExecutionResult(
                status="FAIL",
                dry_run=dry_run,
                action_results=[],
                lifecycle_state=ActionLifecycleState(current_state="blocked", reason=str(recovery.get("blocker") or recovery.get("status") or "liveness_recovery_failed")).to_dict(),
                loop_summary=summary,
                reason=str(recovery.get("blocker") or recovery.get("status") or "liveness_recovery_failed"),
                warnings=["liveness recovery did not produce a loaded scene"],
                missing_capabilities=["loaded_scene"],
                max_actions=max_actions,
                max_runtime_seconds=max_runtime_seconds,
            )
    live_input_status: dict[str, Any] | None = None
    try:
        live_input_status = _start_live_input_session(options, backend)
    except Exception:  # noqa: BLE001
        status = _live_input_status(options, backend)
        if status.get("status") != "FAIL":
            status["status"] = "FAIL"
            status["blockReason"] = status.get("blockReason") or "arduino_arm_failed"
        summary = _new_loop_summary()
        summary["liveInput"] = dict(status)
        summary["inputProfile"] = _input_profile_from_options(options)
        return LoopExecutionResult(
            status="FAIL",
            dry_run=dry_run,
            action_results=[],
            lifecycle_state=ActionLifecycleState(current_state="blocked", reason=str(status.get("blockReason") or "live_input_blocked")).to_dict(),
            loop_summary=summary,
            reason=str(status.get("blockReason") or "live_input_blocked"),
            warnings=[str(status.get("blockReason") or "live_input_blocked")],
            missing_capabilities=["live_input.arduino"],
            max_actions=max_actions,
            max_runtime_seconds=max_runtime_seconds,
        )
    input_controller = _input_controller_from_options(backend, options, sleep_func=sleep_func, monotonic_func=monotonic_func)
    started = _safe_monotonic(monotonic_func)
    if started is None:
        started = 0.0
    results: list[ExecutionResult] = []
    warnings: list[str] = []
    lifecycle = ActionLifecycleState(current_state="idle", max_attempts=max_actions)
    wait_started: float | None = None
    wait_before_status: dict[str, Any] | None = None
    loop_summary = _new_loop_summary()
    screenshot_func = getattr(options, "visual_debug_screenshot_func", None)
    if callable(screenshot_func):
        debug_bundles = VisualDebugBundleWriter.from_options(options, backend=backend, screenshot_func=screenshot_func)
    else:
        debug_bundles = VisualDebugBundleWriter.from_options(options, backend=backend)
    _update_debug_bundle_summary(loop_summary, debug_bundles)
    reason = "not_applicable"
    status_value = "PASS"
    loop_summary["pacingProfile"] = str(getattr(options, "pacing_profile", "instant_debug") or "instant_debug")
    loop_summary["inputProfile"] = _input_profile_from_options(options)
    loop_summary["liveInput"] = dict(live_input_status) if isinstance(live_input_status, dict) else None
    if recovery is not None:
        loop_summary["livenessRecoveryLastResult"] = recovery
    loop_summary["finalReconcileMillis"] = _final_reconcile_ms(options)
    suppression_cache: dict[str, dict[str, Any]] = {}
    last_reacquire_scope_key: tuple[Any, ...] | None = None
    recent_navigation_waypoints: list[tuple[int, int, int]] = []
    last_route_transition_retry: dict[str, Any] | None = None
    last_successful_route_transition: dict[str, Any] | None = None

    while len([result for result in results if result.executed]) < max_actions:
        if max_total_actions > 0 and len(results) >= max_total_actions:
            reason = "max_total_actions_reached"
            break
        now = _safe_monotonic(monotonic_func)
        if now is None or max_runtime_seconds <= 0.0 or (now - started) >= max_runtime_seconds:
            reason = "max_runtime_reached"
            break
        if lifecycle.current_state == "waiting_for_result":
            try:
                latest_status = _fetch_status_or_action_context(
                    daemon_url,
                    options,
                    fetch_json_func=fetch_json_func,
                    timeout=timeout,
                    purpose="cooldown_poll",
                )
            except Exception as error:  # noqa: BLE001
                warnings.append(f"daemon status unavailable during cooldown: {type(error).__name__}: {error}")
                status_value = "WARN"
                reason = "daemon_unavailable"
                break
            _record_loop_status(loop_summary, latest_status)
            if not lifecycle.last_action:
                if is_waiting_for_result(latest_status):
                    sleep_func(_poll_interval_seconds(options))
                    continue
                lifecycle.current_state = "verified"
                lifecycle.reason = "external_wait_cleared"
                lifecycle.next_action_allowed = True
                continue
            elapsed = now - (wait_started if wait_started is not None else started)
            timeout_seconds = _action_timeout_seconds(options)
            latest_proposal = results[-1].proposal if results and isinstance(results[-1].proposal, dict) else {}
            lock_proposal = None
            if latest_proposal:
                lock_proposal = ActionProposal(
                    proposed_action=str(latest_proposal.get("proposedAction") or lifecycle.last_action or "none"),
                    target_kind=str(latest_proposal.get("targetKind") or "none"),
                    target_tile=latest_proposal.get("targetTile") if isinstance(latest_proposal.get("targetTile"), dict) else None,
                )
            observed = verify_expected_result(
                lifecycle.last_action or "none",
                wait_before_status,
                latest_status,
                elapsed_ms=int(max(0.0, elapsed) * 1000),
                timeout_ms=_action_timeout_millis_for_verification(lifecycle.last_action or "none", options),
                wait_started_tick=lifecycle.wait_started_tick,
                timeout_ticks=_action_timeout_ticks_for_verification(lifecycle.last_action or "none", options),
                progress_min_distance=_nav_progress_min_distance(options) if (lifecycle.last_action or "") in NAVIGATION_ACTIONS else None,
                proposal=lock_proposal,
            )
            locked = _navigation_motion_lock_observation(
                action=lifecycle.last_action or "none",
                proposal=lock_proposal,
                status=latest_status,
                observed=observed,
                options=options,
            )
            if locked is not None:
                observed = locked
                lock = locked.get("navigationInProgress") if isinstance(locked.get("navigationInProgress"), dict) else {}
                _record_navigation_trace(
                    options=options,
                    loop_summary=loop_summary,
                    decision="wait",
                    reason=str(lock.get("replanSuppressedReason") or "navigation_in_progress"),
                    status=latest_status,
                    previous_status=wait_before_status,
                    proposal=lock_proposal,
                    observed=locked,
                    result=results[-1] if results else None,
                )
            if (lifecycle.last_action or "") == "resource_view_recovery" and _loop_resource_progress_seen(loop_summary):
                observed = _resource_progress_during_view_recovery_observation(observed, loop_summary)
            _sync_lifecycle_observation(lifecycle, observed)
            previous_wait_observed: dict[str, Any] = {}
            if results:
                latest_result = results[-1]
                previous_wait_observed = _observed_from_result(latest_result)
                previous_wait_timed_out = (
                    str(previous_wait_observed.get("resultOutcome") or "") == "no_change_timeout"
                    or str(previous_wait_observed.get("resourceProgressClassification") or "") == "resource_timeout_no_progress"
                    or previous_wait_observed.get("resourceTimeoutExtendedWait") is True
                    or str(previous_wait_observed.get("previousResultOutcome") or "") == "no_change_timeout"
                )
                if (
                    previous_wait_timed_out
                    and (lifecycle.last_action or "") == "select_resource_target"
                    and observed.get("resultComplete")
                    and observed.get("resultOutcome") in {"success", "progress", "depleted"}
                ):
                    _annotate_delayed_reconciliation(latest_result, observed, previous_wait_observed)
                    if previous_wait_observed.get("resourceNoProgressTargetReacquired") is True:
                        observed["resourceNoProgressTargetReacquired"] = True
                    lifecycle.observed_result = observed
                    lifecycle.observed_signals = list(observed.get("observedSignals") or [])
                elif (
                    previous_wait_timed_out
                    and (lifecycle.last_action or "") in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"})
                    and observed.get("resultComplete")
                    and observed.get("resultOutcome") in {"success", "progress", "depleted"}
                ):
                    _annotate_delayed_reconciliation(latest_result, observed, previous_wait_observed)
                    lifecycle.observed_result = observed
                    lifecycle.observed_signals = list(observed.get("observedSignals") or [])
                if (lifecycle.last_action or "") in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"}):
                    transition_ledger = _attach_route_transition_ledger(
                        latest_result,
                        before_status=wait_before_status,
                        after_status=latest_status,
                        observed=observed,
                        retry_of_action_id=observed.get("retryOfActionId") if isinstance(observed.get("retryOfActionId"), str) else None,
                    )
                    if (
                        transition_ledger is not None
                        and (lifecycle.last_action or "") in ROUTE_TRANSITION_ACTIONS
                        and observed.get("resultOutcome") in {"success", "progress", "depleted"}
                    ):
                        last_successful_route_transition = transition_ledger
                latest_result.observed_result = observed
                latest_result.verification_status = str(observed.get("verificationStatus") or "UNKNOWN")
                if observed.get("resourceProgressDuringViewRecovery") is True:
                    latest_result.status = "WARN"
                    latest_result.verification_status = "WARN"
                    _set_trace_final(latest_result, "resource_progress_during_view_recovery")
                _apply_lifecycle(latest_result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
            _refresh_loop_summary(loop_summary, results)
            if observed.get("resultComplete") and observed.get("resultOutcome") in {"success", "progress", "depleted"}:
                lifecycle.current_state = "verified"
                lifecycle.reason = str(observed.get("resultOutcome") or observed.get("observedResult") or "expected_result_verified")
                _clear_suppression_on_progress_if_needed(options, suppression_cache, loop_summary, observed)
                if results:
                    _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                    _update_result_classification_from_observed(results[-1])
                    if (lifecycle.last_action or "") in NAVIGATION_ACTIONS:
                        decision, trace_reason, recovery_mode = _navigation_decision_from_observed(observed)
                        _record_navigation_trace(
                            options=options,
                            loop_summary=loop_summary,
                            decision=decision,
                            reason=trace_reason,
                            status=latest_status,
                            previous_status=wait_before_status,
                            proposal=lock_proposal,
                            observed=observed,
                            result=results[-1],
                            recovery_mode=recovery_mode,
                        )
                _refresh_loop_summary(loop_summary, results)
                if observed.get("delayedProgressReconciliation") is True and status_value == "WARN":
                    status_value = "PASS"
                stop_reason = _loop_stop_reason(options, loop_summary)
                if stop_reason:
                    reason = stop_reason
                    break
                if sum(1 for result in results if result.executed) < max_actions:
                    _apply_target_switch_pacing(
                        options,
                        loop_summary,
                        results[-1] if results else None,
                        sleep_func,
                        reason=_pacing_reason_from_observed(observed),
                        input_controller=input_controller,
                    )
            elif observed.get("resultOutcome") == "no_change_timeout":
                route_transition_timed_out = (lifecycle.last_action or "") in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"})
                if route_transition_timed_out and results:
                    retry_observed = _route_transition_retry_required_observation(results[-1], observed)
                    if retry_observed is not observed and retry_observed.get("resultOutcome") == "retry_required":
                        _attach_route_transition_ledger(
                            results[-1],
                            before_status=wait_before_status,
                            after_status=latest_status,
                            observed=retry_observed,
                        )
                        lifecycle.current_state = "verified"
                        lifecycle.reason = str(retry_observed.get("observedResult") or "route_transition_retry_required")
                        lifecycle.observed_result = retry_observed
                        lifecycle.observed_signals = list(retry_observed.get("observedSignals") or [])
                        lifecycle.result_complete = True
                        lifecycle.result_outcome = "retry_required"
                        lifecycle.next_action_allowed = True
                        results[-1].observed_result = retry_observed
                        results[-1].verification_status = "WARN"
                        results[-1].status = "WARN" if results[-1].status == "PASS" else results[-1].status
                        _set_trace_final(results[-1], str(retry_observed.get("routeTransitionProgressClassification") or retry_observed.get("observedResult") or "route_transition_retry_required"))
                        _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                        last_route_transition_retry = {
                            "actionId": results[-1].action_id,
                            "identity": _route_transition_identity_from_result(results[-1]),
                            "classification": retry_observed.get("routeTransitionProgressClassification"),
                        }
                        _refresh_loop_summary(loop_summary, results)
                        _capture_debug_bundle(
                            debug_bundles,
                            loop_summary,
                            str(retry_observed.get("observedResult") or "return_transition_retry_required"),
                            daemon_status=latest_status,
                            proposal=results[-1].proposal,
                            result=results[-1],
                            readiness=results[-1].readiness,
                            classification=str(retry_observed.get("routeTransitionProgressClassification") or retry_observed.get("observedResult") or "route_transition_retry_required"),
                        )
                        status_value = "WARN" if status_value == "PASS" else status_value
                        stop_reason = _loop_stop_reason(options, loop_summary)
                        if stop_reason:
                            reason = stop_reason
                            break
                        continue
                resource_target_reacquired = (
                    (lifecycle.last_action or "") == "select_resource_target"
                    and _status_inventory_full(latest_status) is not True
                    and _status_resource_target_available(latest_status)
                )
                projection_recovery_failed = (lifecycle.last_action or "") == "resource_view_recovery"
                service_view_recovery_failed = (lifecycle.last_action or "") == "service_view_recovery"
                service_object_pending_after_timeout = bool(
                    (lifecycle.last_action or "") == "open_service"
                    and results
                    and _service_object_timeout_wait_extension_allowed(results[-1])
                )
                lifecycle.current_state = "verified" if resource_target_reacquired else "timed_out"
                if resource_target_reacquired:
                    lifecycle.reason = "resource_no_progress_target_reacquired"
                elif projection_recovery_failed:
                    lifecycle.reason = "resource_projection_recovery_failed"
                elif service_view_recovery_failed:
                    lifecycle.reason = "service_view_recovery_failed"
                else:
                    lifecycle.reason = "action_timeout"
                if results:
                    if lock_proposal is not None:
                        route_no_progress_marked = _mark_navigation_no_progress(results[-1], lock_proposal, status=latest_status)
                        lifecycle.observed_result = results[-1].observed_result
                        lifecycle.observed_signals = list(_observed_from_result(results[-1]).get("observedSignals") or [])
                        if route_no_progress_marked:
                            decision, trace_reason, recovery_mode = _navigation_decision_from_observed(_observed_from_result(results[-1]))
                            _record_navigation_trace(
                                options=options,
                                loop_summary=loop_summary,
                                decision=decision,
                                reason=trace_reason,
                                status=latest_status,
                                previous_status=wait_before_status,
                                proposal=lock_proposal,
                                observed=_observed_from_result(results[-1]),
                                result=results[-1],
                                recovery_mode=recovery_mode,
                            )
                    if resource_target_reacquired:
                        continued = dict(observed)
                        continued["resourceNoProgressContinued"] = True
                        continued["resourceProgressClassification"] = continued.get("resourceProgressClassification") or "resource_timeout_no_progress"
                        continued["nextActionAllowed"] = True
                        continued["warnings"] = list(continued.get("warnings") or []) + [
                            "resource click produced no progress before timeout; continuing because a fresh resource target is available"
                        ]
                        lifecycle.observed_result = continued
                        lifecycle.observed_signals = list(continued.get("observedSignals") or [])
                        lifecycle.next_action_allowed = True
                    _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                _refresh_loop_summary(loop_summary, results)
                resource_pending_after_timeout = (
                    (lifecycle.last_action or "") == "select_resource_target"
                    and (
                        "wait_for_result_state" in (observed.get("observedSignals") or [])
                        or resource_target_reacquired
                    )
                    and _resource_timeout_wait_extension_allowed(options, loop_summary)
                )
                if resource_pending_after_timeout:
                    pending = dict(observed)
                    pending["resourceTimeoutExtendedWait"] = True
                    pending["verificationStatus"] = "WARN"
                    pending["observedResult"] = "resource_click_confirmed_waiting"
                    pending["resultOutcome"] = "still_waiting"
                    pending["resultComplete"] = False
                    pending["nextActionAllowed"] = False
                    pending["resourceProgressClassification"] = "resource_click_confirmed_waiting"
                    pending["previousObservedResult"] = observed.get("observedResult")
                    pending["previousResultOutcome"] = observed.get("resultOutcome")
                    pending["warnings"] = list(pending.get("warnings") or []) + [
                        "resource action timed out while late progress is still plausible; continuing bounded observation"
                    ]
                    if resource_target_reacquired:
                        pending["resourceNoProgressTargetReacquired"] = True
                    lifecycle.current_state = "waiting_for_result"
                    lifecycle.reason = "resource_timeout_waiting_for_late_evidence"
                    lifecycle.observed_result = pending
                    lifecycle.observed_signals = list(pending.get("observedSignals") or [])
                    lifecycle.result_complete = False
                    lifecycle.result_outcome = "still_waiting"
                    lifecycle.next_action_allowed = False
                    if results:
                        results[-1].observed_result = pending
                        results[-1].verification_status = "WARN"
                        _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                    _refresh_loop_summary(loop_summary, results)
                    status_value = "WARN" if status_value == "PASS" else status_value
                    sleep_func(_poll_interval_seconds(options))
                    continue
                if service_object_pending_after_timeout:
                    pending = _service_object_timeout_pending_observation(observed)
                    lifecycle.current_state = "waiting_for_result"
                    lifecycle.reason = "service_object_timeout_waiting_for_bank_ui"
                    lifecycle.observed_result = pending
                    lifecycle.observed_signals = list(pending.get("observedSignals") or [])
                    lifecycle.result_complete = False
                    lifecycle.result_outcome = "still_waiting"
                    lifecycle.next_action_allowed = False
                    if results:
                        results[-1].observed_result = pending
                        results[-1].verification_status = "WARN"
                        if results[-1].status == "PASS":
                            results[-1].status = "WARN"
                        if isinstance(results[-1].action_trace, dict):
                            results[-1].action_trace["serviceObjectTimeoutExtendedWait"] = True
                        _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                    _refresh_loop_summary(loop_summary, results)
                    status_value = "WARN" if status_value == "PASS" else status_value
                    sleep_func(_poll_interval_seconds(options))
                    continue
                if results:
                    timeout_reason = _timeout_debug_reason(results[-1])
                    if timeout_reason is not None:
                        _capture_debug_bundle(
                            debug_bundles,
                            loop_summary,
                            timeout_reason,
                            daemon_status=latest_status,
                            proposal=results[-1].proposal,
                            result=results[-1],
                            readiness=results[-1].readiness,
                            classification=str(_observed_from_result(results[-1]).get("resourceProgressClassification") or timeout_reason),
                        )
                if resource_target_reacquired:
                    status_value = "WARN" if status_value == "PASS" else status_value
                    stop_reason = _loop_stop_reason(options, loop_summary)
                    if stop_reason:
                        reason = stop_reason
                        break
                    if sum(1 for result in results if result.executed) < max_actions:
                        _apply_target_switch_pacing(
                            options,
                            loop_summary,
                            results[-1] if results else None,
                            sleep_func,
                            reason="after_hover_mismatch",
                            input_controller=input_controller,
                    )
                    continue
                if (lifecycle.last_action or "") in NAVIGATION_ACTIONS:
                    stop_reason = _loop_stop_reason(options, loop_summary)
                    if stop_reason:
                        status_value = "FAIL"
                        reason = stop_reason
                        if results:
                            _capture_debug_bundle(
                                debug_bundles,
                                loop_summary,
                                "failure",
                                daemon_status=latest_status,
                                proposal=results[-1].proposal,
                                result=results[-1],
                                readiness=results[-1].readiness,
                                classification=reason,
                            )
                        break
                    status_value = "WARN" if status_value == "PASS" else status_value
                    sleep_func(_poll_interval_seconds(options))
                    continue
                status_value = "FAIL"
                reason = (
                    "route_wrong_node_or_barrier"
                    if (lifecycle.last_action or "") in NAVIGATION_ACTIONS
                    else "resource_projection_recovery_failed"
                    if projection_recovery_failed
                    else (_loop_stop_reason(options, loop_summary) or "action_timeout")
                )
                if results:
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "failure",
                        daemon_status=latest_status,
                        proposal=results[-1].proposal,
                        result=results[-1],
                        readiness=results[-1].readiness,
                        classification=reason,
                    )
                break
            elif observed.get("resultOutcome") == "interrupted":
                lifecycle.current_state = "blocked"
                lifecycle.reason = "interrupted"
                if results:
                    _apply_lifecycle(results[-1], lifecycle, cooldown_remaining_ms=0)
                status_value = "FAIL"
                reason = "action_interrupted"
                break
            else:
                sleep_func(_poll_interval_seconds(options))
                continue

        try:
            before_status = _fetch_status_or_action_context(
                daemon_url,
                options,
                fetch_json_func=fetch_json_func,
                timeout=timeout,
                purpose="action_selection",
            )
        except Exception as error:  # noqa: BLE001
            warnings.append(f"daemon status unavailable: {type(error).__name__}: {error}")
            status_value = "FAIL"
            reason = "daemon_unavailable"
            break
        _record_loop_status(loop_summary, before_status)
        _refresh_loop_summary(loop_summary, results)
        stop_reason = _loop_stop_reason(options, loop_summary)
        if stop_reason:
            reason = stop_reason
            break
        if is_waiting_for_result(before_status) and not lifecycle.next_action_allowed:
            lifecycle = ActionLifecycleState(
                current_state="waiting_for_result",
                last_action=None,
                last_action_tick=before_status.get("latestTick") if isinstance(before_status.get("latestTick"), int) else None,
                expected_result=expected_result_for_action("select_resource_target"),
                expected_signal=expected_result_for_action("select_resource_target").get("expectedSignal"),
                attempts=len(results),
                max_attempts=max_actions,
                reason="client_processing_previous_action",
                warnings=["already waiting for previous action result"],
            )
            status_value = "WARN" if status_value == "PASS" else status_value
            reason = "already_waiting_for_result"
            if _status_has_navigation_context(before_status):
                _record_navigation_trace(
                    options=options,
                    loop_summary=loop_summary,
                    decision="wait",
                    reason="client_processing_previous_action",
                    status=before_status,
                    observed={
                        "observedResult": "already_waiting_for_result",
                        "resultOutcome": "still_waiting",
                        "resultComplete": False,
                        "nextActionAllowed": False,
                    },
                )
            sleep_func(_poll_interval_seconds(options))
            continue
        current_reacquire_scope_key = _status_reacquire_scope_key(before_status)
        last_reacquire_scope_key = _maybe_reset_reacquire_budget_on_scope_change(
            suppression_cache,
            loop_summary,
            previous_scope=last_reacquire_scope_key,
            current_scope=current_reacquire_scope_key,
        )
        active_suppression_keys = _active_suppression_keys(suppression_cache, int(now * 1000))
        status_for_proposal = _status_with_navigation_option_overrides(
            _status_with_suppressed_targets(before_status, active_suppression_keys),
            options,
        )
        proposal = build_action_proposal(status_for_proposal)
        before_status, proposal, context_fallback = _maybe_context_action_proposal(
            daemon_url,
            options,
            before_status,
            proposal,
            timeout=timeout,
        )
        if context_fallback is not None:
            loop_summary["contextActionFallback"] = context_fallback
        budget_type = _proposal_reacquire_budget_type(proposal)
        loop_summary["reacquireBudgetType"] = budget_type
        fresh_source = _proposal_fresh_executable_source(proposal)
        if loop_summary.get("staleProposalDetected") and not loop_summary.get("freshTargetFound") and fresh_source:
            loop_summary["freshTargetFound"] = True
            loop_summary["freshTargetSource"] = fresh_source
            loop_summary["reacquireResult"] = "live_target_reacquired"
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "live_target_reacquired",
                daemon_status=before_status,
                proposal=proposal,
                result=None,
                readiness=None,
                classification="live_target_reacquired",
            )
        if _proposal_is_stale_or_static(proposal):
            source = _proposal_action_target_source(proposal)
            actionability = _proposal_actionability(proposal)
            loop_summary["staleProposalDetected"] = True
            loop_summary["staleProposalSource"] = source
            loop_summary["reacquireAttempted"] = True
            loop_summary["reacquireResult"] = "pending"
            loop_summary["freshTargetFound"] = False
            loop_summary["freshTargetSource"] = None
            loop_summary["reasonIfNoFreshTarget"] = actionability or source or "stale_or_static_target"
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "stale_static_route_target",
                daemon_status=before_status,
                proposal=proposal,
                result=None,
                readiness=None,
                classification="stale_static_route_target",
                extra={
                    "staleProposalDetected": True,
                    "staleProposalSource": source,
                    "actionability": actionability,
                },
            )
            attempts = int(loop_summary.get("staleProposalReacquireAttempts") or 0)
            max_rounds = max(1, _max_navigation_reacquire_rounds(options))
            if attempts < max_rounds:
                loop_summary["staleProposalReacquireAttempts"] = attempts + 1
                _record_reacquire_wait(options, loop_summary, sleep_func, reason="stale_static_route_target")
                continue
            loop_summary["reacquireResult"] = "failed"
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "stale_proposal_reacquire_failed",
                daemon_status=before_status,
                proposal=proposal,
                result=None,
                readiness=None,
                classification="stale_proposal_reacquire_failed",
                extra={
                    "freshTargetFound": False,
                    "reasonIfNoFreshTarget": loop_summary.get("reasonIfNoFreshTarget"),
                },
            )
        if active_suppression_keys:
            loop_summary["targetReacquireRounds"] = int(loop_summary.get("targetReacquireRounds") or 0) + 1
            max_rounds = int(getattr(options, "max_candidate_reacquire_rounds", 0) or 0)
            budget_rounds = _record_reacquire_budget_round(loop_summary, budget_type, max_rounds=max_rounds)
            if isinstance(proposal.target_explanation, dict):
                proposal.target_explanation.setdefault(
                    "reacquisition",
                    {
                        "suppressedTargetKeys": sorted(active_suppression_keys),
                        "selectedTargetKey": _target_key_from_proposal(proposal),
                        "reacquireBudgetType": budget_type,
                        "reacquireAttemptsUsed": budget_rounds,
                        "reacquireLimit": max_rounds,
                        "phaseScopedBudget": True,
                    },
                )
            if _proposal_is_suppressed(proposal, active_suppression_keys):
                candidate_actionable = _proposal_has_actionable_safe_target(proposal)
                loop_summary["candidateWasActionableBeforeLimit"] = bool(candidate_actionable)
                if _route_transition_suppression_override_allowed(proposal, active_suppression_keys, loop_summary, options):
                    selected_key = _target_key_from_proposal(proposal)
                    if selected_key:
                        suppression_cache.pop(selected_key, None)
                        active_suppression_keys = set(active_suppression_keys)
                        active_suppression_keys.discard(selected_key)
                    loop_summary["routeTransitionSuppressionOverrides"] = int(loop_summary.get("routeTransitionSuppressionOverrides") or 0) + 1
                    loop_summary["budgetResetReason"] = "route_transition_actionable_override"
                    loop_summary["phaseScopedBudget"] = True
                else:
                    _record_reacquire_wait(options, loop_summary, sleep_func, reason="all_candidates_suppressed")
                    if max_rounds > 0 and budget_rounds >= max_rounds:
                        loop_summary["stoppedByReacquireLimit"] = True
                        reason = "return_transition_reacquire_limit_blocked" if budget_type == "route_transition" else "candidate_reacquire_round_limit"
                        status_value = "WARN" if status_value == "PASS" else status_value
                        _capture_debug_bundle(
                            debug_bundles,
                            loop_summary,
                            reason,
                            daemon_status=before_status,
                            proposal=proposal.to_dict(),
                            result=None,
                            readiness=None,
                            classification=reason,
                        )
                        break
                    continue
            elif proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
                _record_reacquire_wait(options, loop_summary, sleep_func, reason="all_candidates_suppressed")
                if max_rounds > 0 and budget_rounds >= max_rounds:
                    loop_summary["stoppedByReacquireLimit"] = True
                    reason = "return_transition_reacquire_limit_blocked" if budget_type == "route_transition" else "candidate_reacquire_round_limit"
                    status_value = "WARN" if status_value == "PASS" else status_value
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        reason,
                        daemon_status=before_status,
                        proposal=proposal.to_dict(),
                        result=None,
                        readiness=None,
                        classification=reason,
                    )
                    break
                continue
        if proposal.proposed_action == "wait_for_context" and not proposal.executable and _status_has_navigation_context(before_status):
            max_rounds = max(0, _max_navigation_reacquire_rounds(options))
            attempts = int(loop_summary.get("contextWaitReacquireAttempts") or 0)
            loop_summary["contextWaitReacquireLimit"] = max_rounds
            specific_blocker = _proposal_specific_blocker(proposal, "wait_for_context")
            if attempts < max_rounds:
                loop_summary["contextWaitReacquireAttempts"] = attempts + 1
                loop_summary["reacquireAttempted"] = True
                loop_summary["reacquireResult"] = "waiting_for_context"
                loop_summary["reasonIfNoFreshTarget"] = specific_blocker
                _record_navigation_trace(
                    options=options,
                    loop_summary=loop_summary,
                    decision="wait",
                    reason=specific_blocker,
                    status=before_status,
                    proposal=proposal,
                    observed={
                        "observedResult": specific_blocker,
                        "resultOutcome": "still_waiting",
                        "resultComplete": False,
                        "nextActionAllowed": False,
                        "contextWaitReacquireAttempt": attempts + 1,
                        "contextWaitReacquireLimit": max_rounds,
                    },
                )
                _record_reacquire_wait(options, loop_summary, sleep_func, reason="wait_for_context")
                continue
        if proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
            specific_blocker = _proposal_specific_blocker(proposal)
            action_result = _blocked_by_no_executable_result(
                proposal,
                status=before_status,
                options=options,
                reason=specific_blocker,
            )
            results.append(action_result)
            _refresh_loop_summary(loop_summary, results)
            lifecycle = ActionLifecycleState(
                current_state="blocked",
                last_action=proposal.proposed_action,
                last_action_tick=proposal.source_tick,
                expected_result=expected_result_for_action(proposal.proposed_action),
                observed_result=action_result.observed_result,
                attempts=len(results),
                max_attempts=max_actions,
                reason=specific_blocker,
                warnings=list(action_result.warnings),
            )
            _apply_lifecycle(action_result, lifecycle)
            status_value = "FAIL"
            reason = specific_blocker
            if proposal.proposed_action in NAVIGATION_ACTIONS or proposal.target_kind == "path_tile" or _status_has_navigation_context(before_status):
                _record_navigation_trace(
                    options=options,
                    loop_summary=loop_summary,
                    decision="wait" if proposal.proposed_action in {"none", "wait_for_context"} else "fail",
                    reason=specific_blocker or "navigation_context_unavailable",
                    status=before_status,
                    proposal=proposal,
                    observed={
                        "observedResult": specific_blocker or "navigation_context_unavailable",
                        "resultOutcome": "still_waiting" if proposal.proposed_action in {"none", "wait_for_context"} else "skipped",
                        "resultComplete": proposal.proposed_action not in {"none", "wait_for_context"},
                        "nextActionAllowed": False,
                    },
                )
            break
        readiness: dict[str, Any] | None = None
        if _readiness_gate_required(options, proposal):
            before_status, proposal, readiness = _wait_until_ready(
                daemon_url,
                options,
                status=before_status,
                proposal=proposal,
                fetch_json_func=fetch_json_func,
                sleep_func=sleep_func,
                monotonic_func=monotonic_func,
            )
            if not _readiness_allows_execution(readiness):
                action_result = _blocked_by_readiness_result(proposal, readiness=readiness, options=options)
                results.append(action_result)
                _refresh_loop_summary(loop_summary, results)
                status_value = "FAIL"
                lifecycle = ActionLifecycleState(
                    current_state="blocked",
                    last_action=proposal.proposed_action,
                    last_action_tick=proposal.source_tick,
                    expected_result=expected_result_for_action(proposal.proposed_action),
                    observed_result=action_result.observed_result,
                    attempts=len(results),
                    max_attempts=max_actions,
                    reason="pre_action_readiness_failed",
                    warnings=list(action_result.warnings),
                )
                _apply_lifecycle(action_result, lifecycle)
                reason = "pre_action_readiness_failed"
                break
        route_transition_reverse_issue = _route_transition_plane_mismatch_issue(
            proposal,
            current_status=before_status,
        ) or _route_transition_reverse_issue(
            proposal,
            last_successful_route_transition,
            current_status=before_status,
        )
        if route_transition_reverse_issue is not None:
            action_result = _blocked_by_route_stability_result(proposal, route_transition_reverse_issue, options=options)
            action_result.readiness = readiness
            _attach_readiness_trace(action_result, readiness)
            results.append(action_result)
            _refresh_loop_summary(loop_summary, results)
            route_stability_classification = str(route_transition_reverse_issue.get("classification") or "route_transition_reverse_blocked")
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                route_stability_classification,
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification=route_stability_classification,
                extra={"routeStabilityIssue": dict(route_transition_reverse_issue)},
            )
            status_value = "WARN" if status_value == "PASS" else status_value
            reason = route_stability_classification
            break
        route_stability_issue = _route_stability_issue(
            proposal,
            recent_navigation_waypoints,
            last_result=results[-1] if results else None,
            current_status=before_status,
        )
        if route_stability_issue is not None:
            action_result = _blocked_by_route_stability_result(proposal, route_stability_issue, options=options)
            action_result.readiness = readiness
            _attach_readiness_trace(action_result, readiness)
            _record_navigation_trace(
                options=options,
                loop_summary=loop_summary,
                decision="advance" if route_stability_issue.get("advanceRecommended") is True else "fail",
                reason=str(route_stability_issue.get("reason") or route_stability_issue.get("classification") or "route_stability_blocked"),
                status=before_status,
                proposal=proposal,
                observed=action_result.observed_result,
                result=action_result,
                recovery_mode=str(route_stability_issue.get("classification")) if route_stability_issue.get("barrierDetected") is True else None,
            )
            results.append(action_result)
            _refresh_loop_summary(loop_summary, results)
            route_stability_classification = str(route_stability_issue.get("classification") or "route_stability_blocked")
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                route_stability_classification,
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification=route_stability_classification,
                extra={"routeStabilityIssue": dict(route_stability_issue)},
            )
            if route_stability_issue.get("advanceRecommended") is True and route_stability_issue.get("barrierDetected") is not True:
                advance_waits = int(loop_summary.get("routeWaypointAdvanceWaits") or 0) + 1
                loop_summary["routeWaypointAdvanceWaits"] = advance_waits
                max_advance_waits = max(1, _max_navigation_reacquire_rounds(options))
                loop_summary["routeWaypointAdvanceWaitLimit"] = max_advance_waits
                if advance_waits <= max_advance_waits:
                    _record_reacquire_wait(options, loop_summary, sleep_func, reason=route_stability_classification)
                    continue
                stale_issue = dict(route_stability_issue)
                stale_issue["classification"] = "route_waypoint_arrived_but_route_state_stale"
                stale_issue["reason"] = "player is already at the repeated route waypoint, but route context did not advance after bounded reobserve attempts"
                action_result.observed_result = {
                    **(action_result.observed_result if isinstance(action_result.observed_result, dict) else {}),
                    "observedResult": stale_issue["classification"],
                    "skipReason": stale_issue["classification"],
                    "nextActionAllowed": False,
                }
                action_result.warnings = [stale_issue["reason"]]
                if isinstance(action_result.action_trace, dict):
                    action_result.action_trace["routeStability"] = stale_issue
                    _set_trace_final(action_result, stale_issue["classification"])
                route_stability_classification = stale_issue["classification"]
                _refresh_loop_summary(loop_summary, results)
                reason = route_stability_classification
            status_value = "WARN" if status_value == "PASS" else status_value
            reason = route_stability_classification
            break
        if proposal.proposed_action == "resource_view_recovery":
            proposal_payload = proposal.to_dict()
            explanation = proposal_payload.get("targetExplanation") if isinstance(proposal_payload.get("targetExplanation"), dict) else {}
            resource_view_recovery = _is_resource_view_recovery(proposal, proposal_payload)
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "resource_camera_reacquire_start" if resource_view_recovery else "resource_projection_recovery_start",
                daemon_status=before_status,
                proposal=proposal,
                readiness=readiness,
                classification="resource_camera_reacquire_started" if resource_view_recovery else "resource_projection_recovery_started",
            )
        if proposal.proposed_action == "service_view_recovery":
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "service_view_recovery_start",
                daemon_status=before_status,
                proposal=proposal,
                readiness=readiness,
                classification="service_view_recovery_started",
            )
        if proposal.proposed_action in NAVIGATION_ACTIONS or proposal.target_kind == "path_tile":
            _record_navigation_trace(
                options=options,
                loop_summary=loop_summary,
                decision="click",
                reason=proposal.reason or "navigation_waypoint_executable",
                status=before_status,
                proposal=proposal,
                observed={
                    "observedResult": "navigation_click_selected",
                    "resultOutcome": "pending",
                    "resultComplete": False,
                    "nextActionAllowed": False,
                },
            )
        try:
            _ensure_live_input_session_for_action(options, backend, live_input_status)
        except Exception:  # noqa: BLE001
            status = _live_input_status(options, backend)
            status.update(live_input_status if isinstance(live_input_status, dict) else {})
            status["status"] = "FAIL"
            status["blockReason"] = status.get("blockReason") or "arduino_rearm_failed"
            action_result = _live_input_blocked_result(options, backend, status, proposed_action=proposal.proposed_action)
            action_result.proposal = proposal.to_dict()
            action_result.readiness = readiness
            _attach_readiness_trace(action_result, readiness)
            results.append(action_result)
            _refresh_loop_summary(loop_summary, results)
            status_value = "FAIL"
            reason = str(status.get("blockReason") or "arduino_rearm_failed")
            break
        action_result = execute_action(
            proposal,
            backend=backend,
            movement_profile=_movement_profile_from_options(options),
            dry_run=dry_run,
            hover_options=hover_options_from_options(options),
            navigation_options=options,
            snapshot_url=str(getattr(options, "snapshot_url", "http://127.0.0.1:8893")),
            snapshot_fetch_func=snapshot_fetch_func,
            sleep_func=sleep_func,
            monotonic_func=monotonic_func,
            input_controller=input_controller,
        )
        action_result.action_id = action_result.action_id or f"action-{len(results) + 1}"
        if isinstance(action_result.action_trace, dict):
            action_result.action_trace["actionId"] = action_result.action_id
        action_result.readiness = readiness
        _attach_readiness_trace(action_result, readiness)
        _attach_live_input_status(action_result, live_input_status, options=options, backend=backend)
        _enforce_no_direct_backend_bypass(action_result, options)
        if action_result.executed and action_result.proposed_action in NAVIGATION_ACTIONS:
            waypoint_key = _executed_navigation_waypoint_key(proposal, action_result)
            if waypoint_key is not None:
                recent_navigation_waypoints.append(waypoint_key)
                del recent_navigation_waypoints[:-6]
            if isinstance(action_result.action_trace, dict):
                action_result.action_trace.setdefault("routeStability", {})["recentClickedWaypointTiles"] = [
                    {"worldX": item[0], "worldY": item[1], "plane": item[2]}
                    for item in recent_navigation_waypoints
                ]
        suppression_event = _record_target_hover_failure(
            options=options,
            cache=suppression_cache,
            summary=loop_summary,
            result=action_result,
            now_ms=int(now * 1000),
        )
        if suppression_event and not action_result.executed:
            action_result.status = "WARN"
            if suppression_event.get("reason") == "unsafe_geometry":
                skip_label = "unsafe geometry"
            elif suppression_event.get("reason") == "route_target_hover_not_confirmed":
                skip_label = "route target hover/menu confirmation"
            else:
                skip_label = "hover mismatch"
            action_result.warnings.append(
                f"{skip_label} skipped without click"
                + ("; target suppressed for reacquisition" if suppression_event.get("suppressed") else "")
            )
            if (
                suppression_event.get("suppressed") is True
                and suppression_event.get("reacquireBudgetType") == "route_transition"
                and suppression_event.get("reason") == "route_target_hover_not_confirmed"
            ):
                observed = action_result.observed_result if isinstance(action_result.observed_result, dict) else {}
                observed.update(
                    {
                        "observedResult": "repeated_route_target_hover_failure",
                        "resultOutcome": "blocked",
                        "resultComplete": True,
                        "nextActionAllowed": False,
                        "verificationStatus": "FAIL",
                        "skipReason": "repeated_route_target_hover_failure",
                        "targetSuppression": dict(suppression_event),
                        "attemptedPoints": list(suppression_event.get("attemptedPoints") or []),
                        "observedMenus": list(suppression_event.get("observedMenus") or []),
                    }
                )
                action_result.status = "FAIL"
                action_result.observed_result = observed
                action_result.verification_status = "FAIL"
                lifecycle = lifecycle_state_for_proposal(proposal)
                lifecycle.current_state = "blocked"
                lifecycle.reason = "repeated_route_target_hover_failure"
                lifecycle.observed_result = observed
                lifecycle.result_complete = True
                lifecycle.result_outcome = "blocked"
                lifecycle.next_action_allowed = False
                lifecycle.warnings = list(action_result.warnings)
                _apply_lifecycle(action_result, lifecycle)
                _set_trace_final(action_result, "repeated_route_target_hover_failure")
        safety_skip = _no_click_safety_skip_observed(action_result)
        fatal_safety_block = False
        if safety_skip is not None:
            fatal_safety_block = safety_skip.get("nextActionAllowed") is False
            action_result.status = "FAIL" if fatal_safety_block else "WARN"
            observed_before = action_result.observed_result if isinstance(action_result.observed_result, dict) else {}
            merged_skip = dict(observed_before)
            merged_skip.update(safety_skip)
            action_result.observed_result = merged_skip
            action_result.verification_status = str(safety_skip.get("verificationStatus") or ("FAIL" if fatal_safety_block else "SKIPPED"))
            if isinstance(action_result.action_trace, dict):
                action_result.action_trace.setdefault("safetySkip", dict(merged_skip))
                if fatal_safety_block:
                    _set_trace_final(action_result, "blocked_movement_safety_region")
                else:
                    _set_trace_final(action_result, "hover_mismatch_skipped" if safety_skip.get("skipReason") == "volatile_hover_zone" else "skipped_unsafe_geometry")
        results.append(action_result)
        _refresh_loop_summary(loop_summary, results)
        if fatal_safety_block:
            status_value = "FAIL"
            reason = str(safety_skip.get("skipReason") or safety_skip.get("observedResult") or "no_click_safety_block")
            break
        if (
            action_result.status == "FAIL"
            and isinstance(action_result.observed_result, dict)
            and action_result.observed_result.get("observedResult") == "repeated_route_target_hover_failure"
        ):
            status_value = "FAIL"
            reason = "repeated_route_target_hover_failure"
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "repeated_route_target_hover_failure",
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification="repeated_route_target_hover_failure",
            )
            break
        if _route_edge_reject_result(action_result):
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "route_waypoint_edge_rejected",
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification="route_waypoint_edge_rejected",
            )
        timeout_reason = _timeout_debug_reason(action_result)
        if timeout_reason is not None:
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                timeout_reason,
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification=timeout_reason,
            )
        navigation_not_executed = _navigation_not_executed_observation(action_result) if not dry_run else None
        if navigation_not_executed is not None:
            decision, trace_reason, recovery_mode = _navigation_decision_from_observed(navigation_not_executed)
            _record_navigation_trace(
                options=options,
                loop_summary=loop_summary,
                decision=decision,
                reason=trace_reason,
                status=before_status,
                proposal=proposal,
                observed=navigation_not_executed,
                result=action_result,
                recovery_mode=recovery_mode,
            )
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "navigation_action_not_executed",
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification=trace_reason,
            )
            _refresh_loop_summary(loop_summary, results)
            if _navigation_not_executed_allows_retry(navigation_not_executed):
                status_value = "WARN" if status_value == "PASS" else status_value
                reason = str(navigation_not_executed.get("observedResult") or "navigation_action_not_executed")
                continue
            status_value = "FAIL" if action_result.status == "FAIL" else "WARN"
            reason = str(navigation_not_executed.get("observedResult") or "navigation_action_not_executed")
            break
        if action_result.status == "FAIL":
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "failure",
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification=action_result.action_trace.get("finalClassification") if isinstance(action_result.action_trace, dict) else None,
            )
        if _is_menu_flip_mismatch_result(action_result):
            menu_flip_suppression = _record_target_menu_flip_failure(
                options=options,
                cache=suppression_cache,
                summary=loop_summary,
                result=action_result,
                now_ms=int(now * 1000),
            )
            observed = dict(_observed_from_result(action_result))
            observed["observedResult"] = "menu_flip_mismatch"
            observed["resultOutcome"] = "menu_mismatch"
            observed["resultComplete"] = True
            observed["nextActionAllowed"] = True
            observed["verificationStatus"] = "FAIL"
            observed["actionResultClassification"] = "menu_flip_mismatch"
            if menu_flip_suppression is not None:
                observed["targetSuppressedForReacquire"] = bool(menu_flip_suppression.get("suppressed"))
                observed["targetSuppression"] = dict(menu_flip_suppression)
            action_result.status = "WARN"
            action_result.observed_result = observed
            action_result.verification_status = "FAIL"
            if "clicked menu did not match expected action; target suppressed for reacquisition" not in action_result.warnings:
                action_result.warnings.append("clicked menu did not match expected action; target suppressed for reacquisition")
            _set_trace_final(action_result, "menu_flip_mismatch")
            lifecycle = ActionLifecycleState(
                current_state="verified",
                last_action=proposal.proposed_action,
                last_action_tick=proposal.source_tick,
                expected_result=expected_result_for_action(proposal.proposed_action),
                observed_result=observed,
                observed_signals=[],
                attempts=len(results),
                max_attempts=max_actions,
                reason="menu_flip_mismatch",
                warnings=list(action_result.warnings),
            )
            lifecycle.result_complete = True
            lifecycle.result_outcome = "menu_mismatch"
            lifecycle.next_action_allowed = True
            _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=0)
            _refresh_loop_summary(loop_summary, results)
            _capture_debug_bundle(
                debug_bundles,
                loop_summary,
                "hover_intent_mismatch",
                daemon_status=before_status,
                proposal=proposal,
                result=action_result,
                readiness=readiness,
                classification="menu_flip_mismatch",
            )
            if bool(getattr(options, "stop_on_fail", False)):
                status_value = "FAIL"
                reason = "menu_flip_mismatch"
                break
            status_value = "WARN" if status_value == "PASS" else status_value
            continue
        if action_result.status == "FAIL":
            status_value = "FAIL"
            lifecycle = ActionLifecycleState(
                current_state="blocked",
                last_action=proposal.proposed_action,
                last_action_tick=proposal.source_tick,
                expected_result=expected_result_for_action(proposal.proposed_action),
                observed_result=action_result.observed_result,
                attempts=len(results),
                max_attempts=max_actions,
                reason="execution_failed",
                warnings=list(action_result.warnings),
            )
            _apply_lifecycle(action_result, lifecycle)
            reason = "execution_failed"
            if bool(getattr(options, "stop_on_fail", False)):
                break
        if bool(getattr(options, "verify_after_action", False)) and not action_result.executed:
            observed = action_result.observed_result if isinstance(action_result.observed_result, dict) else {}
            if not observed:
                observed = {
                    "observedResult": "no_click_safety_skip",
                    "resultOutcome": "skipped",
                    "resultComplete": True,
                    "nextActionAllowed": True,
                    "verificationStatus": "SKIPPED",
                }
            action_result.observed_result = observed
            action_result.verification_status = str(observed.get("verificationStatus") or "SKIPPED")
            _refresh_loop_summary(loop_summary, results)
        elif bool(getattr(options, "verify_after_action", False)):
            try:
                after_status, observed_after, elapsed_ms, timeline = _verify_action_after_execution(
                    daemon_url=daemon_url,
                    options=options,
                    action=action_result.proposed_action,
                    proposal=proposal,
                    before_status=before_status,
                    fetch_json_func=fetch_json_func,
                    sleep_func=sleep_func,
                    monotonic_func=monotonic_func,
                    timeout=timeout,
                )
            except Exception as error:  # noqa: BLE001
                action_result.warnings.append(f"verification failed: {type(error).__name__}: {error}")
                if action_result.status == "PASS":
                    action_result.status = "WARN"
                after_status = None
            if after_status is not None:
                _record_loop_status(loop_summary, after_status)
                lifecycle = lifecycle_after_execution(
                    proposal,
                    executed=action_result.executed,
                    dry_run=dry_run,
                    before_status=before_status,
                    after_status=after_status,
                    cooldown_ms=_cooldown_ms(options),
                    elapsed_ms=elapsed_ms,
                    timeout_ms=_action_timeout_millis_for_verification(action_result.proposed_action, options),
                    timeout_ticks=_action_timeout_ticks_for_verification(action_result.proposed_action, options),
                    progress_min_distance=_nav_progress_min_distance(options) if action_result.proposed_action in NAVIGATION_ACTIONS else None,
                    attempts=len(results),
                    max_attempts=max_actions,
                )
                if isinstance(observed_after, dict):
                    lifecycle.observed_result = observed_after
                    lifecycle.observed_signals = list(observed_after.get("observedSignals") or [])
                    lifecycle.result_complete = bool(observed_after.get("resultComplete"))
                    lifecycle.result_outcome = str(observed_after.get("resultOutcome") or lifecycle.result_outcome)
                    lifecycle.next_action_allowed = bool(observed_after.get("nextActionAllowed"))
                    if action_result.proposed_action in ROUTE_TRANSITION_ACTIONS.union({"interface_dialogue_choice"}):
                        retry_of_action_id = None
                        same_route_object = None
                        if (
                            lifecycle.result_outcome in {"success", "progress", "depleted"}
                            and _same_route_transition_object(action_result, last_route_transition_retry)
                        ):
                            retry_of_action_id = str(last_route_transition_retry.get("actionId"))
                            same_route_object = True
                            prefix = _route_transition_classification_prefix(action_result, observed_after)
                            observed_after["routeTransitionProgressClassification"] = f"{prefix}_retry_success"
                            observed_after["retryOfActionId"] = retry_of_action_id
                            last_route_transition_retry = None
                        transition_ledger = _attach_route_transition_ledger(
                            action_result,
                            before_status=before_status,
                            after_status=after_status,
                            observed=observed_after,
                            retry_of_action_id=retry_of_action_id,
                            same_route_object_as_previous=same_route_object,
                        )
                        if (
                            transition_ledger is not None
                            and action_result.proposed_action in ROUTE_TRANSITION_ACTIONS
                            and lifecycle.result_outcome in {"success", "progress", "depleted"}
                        ):
                            last_successful_route_transition = transition_ledger
                    if isinstance(observed_after.get("navigationInProgress"), dict):
                        lifecycle.current_state = "waiting_for_result"
                        lifecycle.reason = "navigation_in_progress"
                        lifecycle.wait_reason = "route_replan_suppressed_while_moving"
                        lifecycle.result_complete = False
                        lifecycle.result_outcome = "still_waiting"
                        lifecycle.next_action_allowed = False
                if action_result.hover_confirmation:
                    observed = lifecycle.observed_result if isinstance(lifecycle.observed_result, dict) else {}
                    observed["menuClickClassification"] = action_result.hover_confirmation.get("clickClassification")
                    lifecycle.observed_result = observed
                    action_result.observed_result = observed
                    action_classification = _menu_action_result_classification(action_result)
                    if action_classification:
                        observed["actionResultClassification"] = action_classification
                        lifecycle.observed_result = observed
                        _set_trace_final(action_result, _trace_classification_from_observed(action_classification))
                    if isinstance(action_result.action_trace, dict):
                        action_result.action_trace["gameTickVerificationTimeline"].extend(timeline)
                route_no_progress = _mark_navigation_no_progress(action_result, proposal, status=after_status)
                if route_no_progress:
                    lifecycle.observed_result = action_result.observed_result
                    lifecycle.observed_signals = list(_observed_from_result(action_result).get("observedSignals") or [])
                if (
                    action_result.proposed_action == "open_service"
                    and str(_observed_from_result(action_result).get("resultOutcome") or "") == "no_change_timeout"
                    and _service_object_timeout_wait_extension_allowed(action_result)
                ):
                    pending = _service_object_timeout_pending_observation(_observed_from_result(action_result))
                    action_result.observed_result = pending
                    action_result.verification_status = "WARN"
                    if action_result.status == "PASS":
                        action_result.status = "WARN"
                    if isinstance(action_result.action_trace, dict):
                        action_result.action_trace["serviceObjectTimeoutExtendedWait"] = True
                    lifecycle.current_state = "waiting_for_result"
                    lifecycle.reason = "service_object_timeout_waiting_for_bank_ui"
                    lifecycle.observed_result = pending
                    lifecycle.observed_signals = list(pending.get("observedSignals") or [])
                    lifecycle.result_complete = False
                    lifecycle.result_outcome = "still_waiting"
                    lifecycle.next_action_allowed = False
                    _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=0)
                if action_result.proposed_action in NAVIGATION_ACTIONS:
                    decision, trace_reason, recovery_mode = _navigation_decision_from_observed(_observed_from_result(action_result))
                    _record_navigation_trace(
                        options=options,
                        loop_summary=loop_summary,
                        decision=decision,
                        reason=trace_reason,
                        status=after_status,
                        previous_status=before_status,
                        proposal=proposal,
                        observed=_observed_from_result(action_result),
                        result=action_result,
                        recovery_mode=recovery_mode,
                    )
                _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
                _update_result_classification_from_observed(action_result)
                no_progress_suppression = _record_target_no_progress_failure(
                    options=options,
                    cache=suppression_cache,
                    summary=loop_summary,
                    result=action_result,
                    now_ms=int(now * 1000),
                )
                if no_progress_suppression and no_progress_suppression.get("suppressed"):
                    warning = "resource target produced repeated no-progress; target suppressed for reacquisition"
                    if warning not in action_result.warnings:
                        action_result.warnings.append(warning)
                _clear_suppression_on_progress_if_needed(options, suppression_cache, loop_summary, action_result.observed_result)
                wait_before_status = before_status
                wait_started = now
                _refresh_loop_summary(loop_summary, results)
                if action_result.proposed_action == "resource_view_recovery" and _loop_resource_progress_seen(loop_summary):
                    observed = _resource_progress_during_view_recovery_observation(
                        _observed_from_result(action_result),
                        loop_summary,
                    )
                    action_result.status = "WARN"
                    action_result.verification_status = "WARN"
                    action_result.observed_result = observed
                    lifecycle.current_state = "verified"
                    lifecycle.reason = "resource_progress_during_view_recovery"
                    lifecycle.observed_result = observed
                    lifecycle.observed_signals = list(observed.get("observedSignals") or [])
                    lifecycle.result_complete = True
                    lifecycle.result_outcome = "progress"
                    lifecycle.next_action_allowed = True
                    _set_trace_final(action_result, "resource_progress_during_view_recovery")
                    _clear_suppression_on_progress_if_needed(options, suppression_cache, loop_summary, observed)
                    _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=0)
                    _refresh_loop_summary(loop_summary, results)
                    status_value = "WARN" if status_value == "PASS" else status_value
                if action_result.proposed_action == "resource_view_recovery":
                    observed = _observed_from_result(action_result)
                    proposal_payload = action_result.proposal if isinstance(action_result.proposal, dict) else proposal.to_dict()
                    explanation = proposal_payload.get("targetExplanation") if isinstance(proposal_payload.get("targetExplanation"), dict) else {}
                    resource_view_recovery = _is_resource_view_recovery(proposal, proposal_payload)
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "resource_camera_reacquire_end" if resource_view_recovery else "resource_projection_recovery_end",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification=str(
                            observed.get("resourceProjectionRecoveryClassification")
                            or observed.get("observedResult")
                            or ("resource_camera_reacquire_end" if resource_view_recovery else "resource_projection_recovery_end")
                        ),
                    )
                if action_result.proposed_action == "service_view_recovery":
                    observed = _observed_from_result(action_result)
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "service_view_recovery_success"
                        if str(observed.get("serviceViewRecoveryClassification") or "").endswith("success")
                        else "service_view_recovery_failed"
                        if str(observed.get("serviceViewRecoveryClassification") or "") == "service_view_recovery_failed"
                        else "service_view_recovery_end",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification=str(
                            observed.get("serviceViewRecoveryClassification")
                            or observed.get("observedResult")
                            or "service_view_recovery_end"
                        ),
                    )
                if action_result.proposed_action == "resource_view_recovery" and lifecycle.current_state == "timed_out":
                    if _loop_resource_progress_seen(loop_summary):
                        observed = _resource_progress_during_view_recovery_observation(
                            _observed_from_result(action_result),
                            loop_summary,
                        )
                        action_result.status = "WARN"
                        action_result.verification_status = "WARN"
                        action_result.observed_result = observed
                        lifecycle.current_state = "verified"
                        lifecycle.reason = "resource_progress_during_view_recovery"
                        lifecycle.observed_result = observed
                        lifecycle.observed_signals = list(observed.get("observedSignals") or [])
                        lifecycle.result_complete = True
                        lifecycle.result_outcome = "progress"
                        lifecycle.next_action_allowed = True
                        _set_trace_final(action_result, "resource_progress_during_view_recovery")
                        _clear_suppression_on_progress_if_needed(options, suppression_cache, loop_summary, observed)
                        _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=0)
                        _refresh_loop_summary(loop_summary, results)
                        status_value = "WARN" if status_value == "PASS" else status_value
                        stop_reason = _loop_stop_reason(options, loop_summary)
                        if stop_reason:
                            reason = stop_reason
                            break
                        continue
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "failure",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification="resource_projection_recovery_failed",
                    )
                    status_value = "FAIL"
                    reason = "resource_projection_recovery_failed"
                    break
                if route_no_progress:
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "route_no_progress_timeout",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification="route_wrong_node_or_barrier",
                    )
                    stop_reason = _loop_stop_reason(options, loop_summary)
                    if stop_reason:
                        status_value = "FAIL"
                        reason = stop_reason
                        break
                    status_value = "WARN" if status_value == "PASS" else status_value
                    sleep_func(_poll_interval_seconds(options))
                    continue
                observed_signals = {str(item) for item in (lifecycle.observed_signals or [])}
                if "service_ready" in observed_signals:
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "service_anchor_reached",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification="service_anchor_reached",
                    )
                if "route_object_reacquired" in observed_signals:
                    _capture_debug_bundle(
                        debug_bundles,
                        loop_summary,
                        "route_object_reacquired",
                        daemon_status=after_status,
                        proposal=proposal,
                        result=action_result,
                        readiness=readiness,
                        classification="route_object_reacquired",
                    )
        else:
            expected = expected_result_for_action(proposal.proposed_action)
            lifecycle = ActionLifecycleState(
                current_state="waiting_for_result" if action_result.executed else "proposed",
                last_action=proposal.proposed_action,
                last_action_tick=proposal.source_tick,
                wait_started_tick=proposal.source_tick if action_result.executed else None,
                wait_started_utc=None,
                wait_reason="awaiting_expected_result" if action_result.executed else None,
                expected_result=expected,
                expected_signal=expected.get("expectedSignal"),
                attempts=len(results),
                max_attempts=max_actions,
                reason="awaiting_expected_result" if action_result.executed else "dry_run",
            )
            _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
            wait_before_status = before_status
            wait_started = now
            _refresh_loop_summary(loop_summary, results)
        if action_result.status == "WARN" and bool(getattr(options, "stop_on_warn", False)):
            status_value = "WARN"
            reason = "stop_on_warn"
            break
        if action_result.status == "FAIL" and bool(getattr(options, "stop_on_fail", False)):
            status_value = "FAIL"
            reason = "stop_on_fail"
            break
        stop_reason = _loop_stop_reason(options, loop_summary)
        if stop_reason:
            reason = stop_reason
            break
        if not dry_run and lifecycle.current_state == "verified" and sum(1 for result in results if result.executed) < max_actions:
            _apply_target_switch_pacing(
                options,
                loop_summary,
                action_result,
                sleep_func,
                reason=_pacing_reason_from_observed(action_result.observed_result),
                input_controller=input_controller,
            )
        if dry_run:
            reason = "dry_run_complete"
            break
        if lifecycle.current_state == "waiting_for_result":
            continue

    executed_count = sum(1 for result in results if result.executed)
    if reason == "not_applicable":
        reason = "max_actions_reached" if executed_count >= max_actions else "loop_complete"
    _maybe_final_reconcile(
        daemon_url=daemon_url,
        options=options,
        results=results,
        before_status=wait_before_status,
        loop_summary=loop_summary,
        fetch_json_func=fetch_json_func,
        sleep_func=sleep_func,
        monotonic_func=monotonic_func,
        timeout=timeout,
    )
    _refresh_loop_summary(loop_summary, results)
    post_reconcile_stop_reason = _loop_stop_reason(options, loop_summary)
    if post_reconcile_stop_reason and reason in {"action_timeout", "not_applicable", "loop_complete", "max_actions_reached"}:
        reason = post_reconcile_stop_reason
    if results:
        last_lifecycle = results[-1].lifecycle_state if isinstance(results[-1].lifecycle_state, dict) else {}
        if last_lifecycle.get("currentState") == "verified" and lifecycle.current_state == "timed_out":
            lifecycle.current_state = "verified"
            lifecycle.reason = str(last_lifecycle.get("reason") or "final_reconciled_success")
            lifecycle.result_complete = bool(last_lifecycle.get("resultComplete"))
            lifecycle.result_outcome = str(last_lifecycle.get("resultOutcome") or lifecycle.result_outcome)
            lifecycle.observed_result = last_lifecycle.get("observedResult") if isinstance(last_lifecycle.get("observedResult"), dict) else lifecycle.observed_result
            lifecycle.observed_signals = list(last_lifecycle.get("observedSignals") or lifecycle.observed_signals)
            lifecycle.next_action_allowed = bool(last_lifecycle.get("nextActionAllowed"))
            status_value = "PASS"
    loop_summary["stopReason"] = reason
    recoverable_failures_after_goal = _goal_reached_with_only_recoverable_failures(reason, results, loop_summary)
    if recoverable_failures_after_goal:
        loop_summary["recoverableFailuresAfterGoal"] = recoverable_failures_after_goal
        loop_summary["goalReachedWithRecoverableFailures"] = True
    if lifecycle.current_state == "timed_out":
        status_value = "FAIL"
    elif any(result.status == "FAIL" for result in results) and not recoverable_failures_after_goal:
        status_value = "FAIL"
    elif recoverable_failures_after_goal and status_value == "PASS":
        status_value = "WARN"
    elif status_value == "PASS" and any(result.status == "WARN" for result in results):
        status_value = "WARN"
    final_status_for_bundle: dict[str, Any] | None = None
    if debug_bundles.reason_enabled("final_summary"):
        try:
            final_status_for_bundle = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
            _record_loop_status(loop_summary, final_status_for_bundle)
        except Exception:  # noqa: BLE001
            final_status_for_bundle = None
        _capture_debug_bundle(
            debug_bundles,
            loop_summary,
            "final_summary",
            daemon_status=final_status_for_bundle,
            proposal=results[-1].proposal if results else None,
            result=results[-1] if results else None,
            readiness=results[-1].readiness if results else None,
            classification=reason,
        )
    _finish_live_input_session(backend, live_input_status, options=options)
    if isinstance(live_input_status, dict):
        loop_summary["liveInput"] = dict(live_input_status)
    _update_debug_bundle_summary(loop_summary, debug_bundles)
    return LoopExecutionResult(
        status=status_value,
        dry_run=dry_run,
        action_results=results,
        lifecycle_state=lifecycle.to_dict(),
        loop_summary=loop_summary,
        reason=reason,
        warnings=warnings,
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
    )
