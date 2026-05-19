from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

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
from .backend_pyautogui import PyAutoGuiBackend
from .backend_pydirectinput import PyDirectInputBackend
from .mouse_movement import MouseMovementProfile, MousePoint, MouseTarget, plan_mouse_movement


SCHEMA = "input_control_execution_result.v1"
LOOP_SCHEMA = "input_control_execution_loop_result.v1"


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
    lifecycle_state: dict[str, Any] | None = None
    expected_result: dict[str, Any] | None = None
    observed_result: dict[str, Any] | None = None
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
            "lifecycleState": self.lifecycle_state,
            "expectedResult": self.expected_result,
            "observedResult": self.observed_result,
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
            "actionResults": [result.to_dict() for result in self.action_results],
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
        }


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    decoded = json.loads(payload)
    return decoded if isinstance(decoded, dict) else {}


def daemon_status_url(daemon_url: str) -> str:
    return daemon_url.rstrip("/") + "/status"


def backend_from_name(name: str, **kwargs: Any):
    if name == "pydirectinput":
        return PyDirectInputBackend(**kwargs)
    return PyAutoGuiBackend(**kwargs)


def _movement_profile_from_options(options: Any) -> str | MouseMovementProfile:
    name = str(getattr(options, "movement_profile", "linear_debug"))
    if getattr(options, "seed", None) is not None:
        return MouseMovementProfile(name=name, seed=int(getattr(options, "seed")))
    return name


def _cooldown_ms(options: Any) -> int:
    return max(0, int(getattr(options, "cooldown_ms", 0) or 0))


def _action_timeout_seconds(options: Any) -> float:
    return max(0.001, float(getattr(options, "action_timeout_ms", 3000) or 3000) / 1000.0)


def _status_from_lifecycle(lifecycle: ActionLifecycleState) -> str:
    if lifecycle.current_state == "timed_out":
        return "FAIL"
    if lifecycle.current_state in {"blocked", "waiting_for_result"}:
        return "WARN"
    return "PASS"


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


def _backend_position(backend: Any) -> tuple[int, int]:
    if hasattr(backend, "current_position"):
        try:
            return tuple(backend.current_position())  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return (0, 0)
    return (0, 0)


def _target_from_click(point: dict[str, Any]) -> MouseTarget:
    return MouseTarget(x=int(point["x"]), y=int(point["y"]), radius_px=4, label="action target", source="action_proposal")


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
        converter = getattr(backend, "canvas_to_screen_point", None)
        if callable(converter):
            try:
                converted = converter(point)
                if isinstance(converted, dict) and converted.get("x") is not None and converted.get("y") is not None:
                    resolution = {
                        "status": "PASS",
                        "method": "backend_fallback_window_geometry",
                        "screenClickPoint": {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))},
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
        "screenClickPoint": {"x": int(point["x"]), "y": int(point["y"])},
        "warnings": [],
        "missingCapabilities": [],
    }
    return dict(resolution["screenClickPoint"]), [], resolution


def execute_action(
    proposal: ActionProposal,
    *,
    backend: Any,
    movement_profile: str | MouseMovementProfile = "linear_debug",
    dry_run: bool = True,
) -> ExecutionResult:
    warnings = list(proposal.warnings)
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
    lifecycle = lifecycle_state_for_proposal(proposal)
    if proposal.proposed_action in {"none", "wait_for_context"}:
        result.status = "WARN"
        result.warnings.append(proposal.reason)
        _apply_lifecycle(result, lifecycle)
        return result
    if proposal.key_action:
        key = proposal.key_action.get("key")
        command = {"type": "key_press", "key": key}
        result.commands.append(command)
        if not dry_run and key:
            backend.press(key)
            result.executed = True
        lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
        _apply_lifecycle(result, lifecycle)
        return result
    screen_point, coordinate_warnings, click_resolution = _screen_click_point(proposal, backend)
    if click_resolution:
        result.click_point_resolution = click_resolution
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
        return result
    start = MousePoint(*_backend_position(backend))
    plan = plan_mouse_movement(start, _target_from_click(screen_point), movement_profile)
    result.movement_plan = plan.to_dict(include_points=False)
    result.commands.append(
        {
            "type": "move_and_click",
            "pointCount": len(plan.points),
            "clickPoint": plan.click_point.to_dict(),
            "button": "left",
        }
    )
    if plan.validation_status == "FAIL":
        result.status = "FAIL"
        result.warnings.extend(plan.warnings)
        lifecycle = lifecycle_state_for_proposal(proposal)
        lifecycle.current_state = "blocked"
        lifecycle.reason = "movement_plan_invalid"
        _apply_lifecycle(result, lifecycle)
        return result
    if not dry_run:
        backend.move_and_click(plan, button="left")
        result.executed = True
    lifecycle = lifecycle_after_execution(proposal, executed=result.executed, dry_run=dry_run)
    _apply_lifecycle(result, lifecycle)
    return result


def execute_next_action(
    daemon_url: str,
    options: Any,
    *,
    fetch_json_func=fetch_json,
    backend: Any | None = None,
) -> ExecutionResult:
    timeout = float(getattr(options, "timeout", 3.0))
    try:
        status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
    except Exception as error:  # noqa: BLE001
        return ExecutionResult(
            status="FAIL",
            proposed_action="none",
            dry_run=not bool(getattr(options, "execute", False)),
            backend_name=str(getattr(options, "backend", "unknown")),
            movement_profile=str(getattr(options, "movement_profile", "linear_debug")),
            warnings=[f"daemon status unavailable: {type(error).__name__}: {error}"],
            missing_capabilities=["daemon.status"],
        )
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
        _apply_lifecycle(result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
        return result
    proposal = build_action_proposal(status)
    backend = backend or backend_from_name(
        getattr(options, "backend", "pyautogui"),
        focus_runelite=bool(getattr(options, "focus_runelite", False)),
        window_title_filter=str(getattr(options, "window_title_filter", "RuneLite")),
    )
    result = execute_action(
        proposal,
        backend=backend,
        movement_profile=_movement_profile_from_options(options),
        dry_run=not bool(getattr(options, "execute", False)),
    )
    if bool(getattr(options, "verify_after_action", False)):
        wait_ms = int(getattr(options, "after_action_wait_ms", 500))
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        try:
            result.verification = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
            observed = verify_expected_result(result.proposed_action, status, result.verification)
            result.observed_result = observed
            result.verification_status = str(observed.get("verificationStatus") or "UNKNOWN")
            if result.executed:
                lifecycle = lifecycle_after_execution(
                    proposal,
                    executed=True,
                    dry_run=False,
                    before_status=status,
                    after_status=result.verification,
                    cooldown_ms=_cooldown_ms(options),
                )
                _apply_lifecycle(result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
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
    backend: Any | None = None,
    sleep_func=time.sleep,
    monotonic_func=time.monotonic,
) -> LoopExecutionResult:
    max_actions = max(0, int(getattr(options, "max_actions", 1) or 1))
    max_runtime_seconds = max(0.0, float(getattr(options, "max_runtime_seconds", 0.0) or 0.0))
    timeout = float(getattr(options, "timeout", 3.0))
    dry_run = not bool(getattr(options, "execute", False))
    backend = backend or backend_from_name(
        getattr(options, "backend", "pyautogui"),
        focus_runelite=bool(getattr(options, "focus_runelite", False)),
        window_title_filter=str(getattr(options, "window_title_filter", "RuneLite")),
    )
    started = _safe_monotonic(monotonic_func)
    if started is None:
        started = 0.0
    results: list[ExecutionResult] = []
    warnings: list[str] = []
    lifecycle = ActionLifecycleState(current_state="idle", max_attempts=max_actions)
    wait_started: float | None = None
    wait_before_status: dict[str, Any] | None = None
    reason = "not_applicable"
    status_value = "PASS"

    while len([result for result in results if result.executed]) < max_actions:
        now = _safe_monotonic(monotonic_func)
        if now is None or max_runtime_seconds <= 0.0 or (now - started) >= max_runtime_seconds:
            reason = "max_runtime_reached"
            break
        if lifecycle.current_state == "waiting_for_result":
            try:
                latest_status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
            except Exception as error:  # noqa: BLE001
                warnings.append(f"daemon status unavailable during cooldown: {type(error).__name__}: {error}")
                status_value = "WARN"
                reason = "daemon_unavailable"
                break
            observed = verify_expected_result(lifecycle.last_action or "none", wait_before_status, latest_status)
            lifecycle.observed_result = observed
            if lifecycle.last_action == "select_resource_target" and is_waiting_for_result(latest_status):
                elapsed = now - (wait_started if wait_started is not None else started)
                if elapsed >= _action_timeout_seconds(options):
                    lifecycle.current_state = "timed_out"
                    lifecycle.reason = "action_timeout"
                    status_value = "FAIL"
                    reason = "action_timeout"
                    break
                sleep_func(max(0.0, min(0.25, _cooldown_ms(options) / 1000.0 if _cooldown_ms(options) else 0.05)))
                continue
            if observed.get("verificationStatus") == "PASS":
                lifecycle.current_state = "verified"
                lifecycle.reason = str(observed.get("observedResult") or "expected_result_verified")
            else:
                elapsed = now - (wait_started if wait_started is not None else started)
                if elapsed >= _action_timeout_seconds(options):
                    lifecycle.current_state = "timed_out"
                    lifecycle.reason = "action_timeout"
                    status_value = "FAIL"
                    reason = "action_timeout"
                    break
                sleep_func(max(0.0, min(0.25, _cooldown_ms(options) / 1000.0 if _cooldown_ms(options) else 0.05)))
                continue

        try:
            before_status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
        except Exception as error:  # noqa: BLE001
            warnings.append(f"daemon status unavailable: {type(error).__name__}: {error}")
            status_value = "FAIL"
            reason = "daemon_unavailable"
            break
        if is_waiting_for_result(before_status):
            lifecycle = ActionLifecycleState(
                current_state="waiting_for_result",
                last_action=None,
                last_action_tick=before_status.get("latestTick") if isinstance(before_status.get("latestTick"), int) else None,
                expected_result=expected_result_for_action("select_resource_target"),
                attempts=len(results),
                max_attempts=max_actions,
                reason="client_processing_previous_action",
                warnings=["already waiting for previous action result"],
            )
            status_value = "WARN" if status_value == "PASS" else status_value
            reason = "already_waiting_for_result"
            sleep_func(max(0.0, min(0.25, _cooldown_ms(options) / 1000.0 if _cooldown_ms(options) else 0.05)))
            continue
        proposal = build_action_proposal(before_status)
        if proposal.proposed_action in {"none", "wait_for_context"} or not proposal.executable:
            lifecycle = lifecycle_state_for_proposal(proposal, max_attempts=max_actions)
            status_value = "WARN" if status_value == "PASS" else status_value
            reason = proposal.reason
            sleep_func(max(0.0, min(0.25, _cooldown_ms(options) / 1000.0 if _cooldown_ms(options) else 0.05)))
            continue
        action_result = execute_action(
            proposal,
            backend=backend,
            movement_profile=_movement_profile_from_options(options),
            dry_run=dry_run,
        )
        results.append(action_result)
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
        if bool(getattr(options, "verify_after_action", False)):
            wait_ms = int(getattr(options, "after_action_wait_ms", 500))
            if wait_ms > 0:
                sleep_func(wait_ms / 1000.0)
            try:
                after_status = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
            except Exception as error:  # noqa: BLE001
                action_result.warnings.append(f"verification failed: {type(error).__name__}: {error}")
                if action_result.status == "PASS":
                    action_result.status = "WARN"
                after_status = None
            if after_status is not None:
                lifecycle = lifecycle_after_execution(
                    proposal,
                    executed=action_result.executed,
                    dry_run=dry_run,
                    before_status=before_status,
                    after_status=after_status,
                    cooldown_ms=_cooldown_ms(options),
                    attempts=len(results),
                    max_attempts=max_actions,
                )
                _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
                wait_before_status = before_status
                wait_started = now
        else:
            lifecycle = ActionLifecycleState(
                current_state="waiting_for_result" if action_result.executed else "proposed",
                last_action=proposal.proposed_action,
                last_action_tick=proposal.source_tick,
                expected_result=expected_result_for_action(proposal.proposed_action),
                attempts=len(results),
                max_attempts=max_actions,
                reason="awaiting_expected_result" if action_result.executed else "dry_run",
            )
            _apply_lifecycle(action_result, lifecycle, cooldown_remaining_ms=_cooldown_ms(options))
            wait_before_status = before_status
            wait_started = now
        if action_result.status == "WARN" and bool(getattr(options, "stop_on_warn", False)):
            status_value = "WARN"
            reason = "stop_on_warn"
            break
        if action_result.status == "FAIL" and bool(getattr(options, "stop_on_fail", False)):
            status_value = "FAIL"
            reason = "stop_on_fail"
            break
        if dry_run:
            reason = "dry_run_complete"
            break
        if lifecycle.current_state == "waiting_for_result":
            continue

    executed_count = sum(1 for result in results if result.executed)
    if reason == "not_applicable":
        reason = "max_actions_reached" if executed_count >= max_actions else "loop_complete"
    if lifecycle.current_state == "timed_out":
        status_value = "FAIL"
    elif any(result.status == "FAIL" for result in results):
        status_value = "FAIL"
    elif status_value == "PASS" and any(result.status == "WARN" for result in results):
        status_value = "WARN"
    return LoopExecutionResult(
        status=status_value,
        dry_run=dry_run,
        action_results=results,
        lifecycle_state=lifecycle.to_dict(),
        reason=reason,
        warnings=warnings,
        max_actions=max_actions,
        max_runtime_seconds=max_runtime_seconds,
    )
