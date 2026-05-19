from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .action_proposal import ActionProposal, build_action_proposal
from .backend_pyautogui import PyAutoGuiBackend
from .backend_pydirectinput import PyDirectInputBackend
from .mouse_movement import MouseMovementProfile, MousePoint, MouseTarget, plan_mouse_movement


SCHEMA = "input_control_execution_result.v1"


@dataclass
class ExecutionResult:
    status: str
    proposed_action: str
    dry_run: bool
    executed: bool = False
    backend_name: str | None = None
    movement_profile: str | None = None
    proposal: dict[str, Any] | None = None
    movement_plan: dict[str, Any] | None = None
    commands: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "status": self.status,
            "proposedAction": self.proposed_action,
            "dryRun": self.dry_run,
            "executed": self.executed,
            "backend": self.backend_name,
            "movementProfile": self.movement_profile,
            "proposal": self.proposal,
            "movementPlan": self.movement_plan,
            "commands": list(self.commands),
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
            "verification": self.verification,
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


def _backend_position(backend: Any) -> tuple[int, int]:
    if hasattr(backend, "current_position"):
        try:
            return tuple(backend.current_position())  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return (0, 0)
    return (0, 0)


def _target_from_click(point: dict[str, Any]) -> MouseTarget:
    return MouseTarget(x=int(point["x"]), y=int(point["y"]), radius_px=4, label="action target", source="action_proposal")


def _screen_click_point(proposal: ActionProposal, backend: Any) -> tuple[dict[str, int] | None, list[str]]:
    if not proposal.suggested_click_point:
        return None, []
    point = dict(proposal.suggested_click_point)
    if proposal.click_point_space == "canvas":
        converter = getattr(backend, "canvas_to_screen_point", None)
        if callable(converter):
            try:
                converted = converter(point)
                if isinstance(converted, dict) and converted.get("x") is not None and converted.get("y") is not None:
                    return {"x": int(round(float(converted["x"]))), "y": int(round(float(converted["y"])))}, []
            except Exception as error:  # noqa: BLE001
                return None, [f"canvas coordinate conversion failed: {type(error).__name__}: {error}"]
        return None, ["canvas click point requires backend window coordinate conversion"]
    return {"x": int(point["x"]), "y": int(point["y"])}, []


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
        backend_name=backend_name,
        movement_profile=movement_profile.name if isinstance(movement_profile, MouseMovementProfile) else str(movement_profile),
        proposal=proposal.to_dict(),
        warnings=warnings,
        missing_capabilities=missing,
    )
    if proposal.proposed_action in {"none", "wait_for_context"}:
        result.status = "WARN"
        result.warnings.append(proposal.reason)
        return result
    if proposal.key_action:
        key = proposal.key_action.get("key")
        command = {"type": "key_press", "key": key}
        result.commands.append(command)
        if not dry_run and key:
            backend.press(key)
            result.executed = True
        return result
    screen_point, coordinate_warnings = _screen_click_point(proposal, backend)
    if coordinate_warnings:
        result.status = "FAIL"
        result.warnings.extend(coordinate_warnings)
        if "screen_click_point" not in result.missing_capabilities:
            result.missing_capabilities.append("screen_click_point")
        return result
    if not screen_point:
        result.status = "FAIL"
        if "click_point" not in result.missing_capabilities:
            result.missing_capabilities.append("click_point")
        result.warnings.append("no click point available; execution blocked")
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
        return result
    if not dry_run:
        backend.move_and_click(plan, button="left")
        result.executed = True
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
    proposal = build_action_proposal(status)
    backend = backend or backend_from_name(
        getattr(options, "backend", "pyautogui"),
        focus_runelite=bool(getattr(options, "focus_runelite", False)),
        window_title_filter=str(getattr(options, "window_title_filter", "RuneLite")),
    )
    result = execute_action(
        proposal,
        backend=backend,
        movement_profile=(
            MouseMovementProfile(name=str(getattr(options, "movement_profile", "linear_debug")), seed=int(getattr(options, "seed")))
            if getattr(options, "seed", None) is not None
            else str(getattr(options, "movement_profile", "linear_debug"))
        ),
        dry_run=not bool(getattr(options, "execute", False)),
    )
    if bool(getattr(options, "verify_after_action", False)):
        wait_ms = int(getattr(options, "after_action_wait_ms", 500))
        if wait_ms > 0:
            time.sleep(wait_ms / 1000.0)
        try:
            result.verification = fetch_json_func(daemon_status_url(daemon_url), timeout=timeout)
        except Exception as error:  # noqa: BLE001
            result.warnings.append(f"verification failed: {type(error).__name__}: {error}")
            if result.status == "PASS":
                result.status = "WARN"
    return result
