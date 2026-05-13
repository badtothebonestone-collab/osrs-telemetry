from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import mission_presets
import task_policy


CONTROL_SCHEMA = "runtime_control.v1"
CONTROL_RESULT_SCHEMA = "runtime_control_result.v1"

ALLOWED_FIELDS = {
    "missionPreset",
    "taskPolicy",
    "goalCount",
    "observeOnly",
    "resetBrainState",
    "brainEnabled",
    "overlayEnabled",
    "overlayMode",
    "overlayBackupCandidates",
}
OVERLAY_MODES = {"intent", "candidates", "debug"}
ACTION_LIKE_FIELD_FRAGMENTS = (
    "click",
    "walk",
    "move",
    "interact",
    "menu",
    "bank",
    "burn",
    "drop",
    "use",
    "key",
    "keyboard",
    "mouse",
    "action",
    "execute",
    "invoke",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class RuntimeControlState:
    activeTask: str = "woodcutting"
    activeMissionPreset: str | None = None
    taskPolicy: str = "woodcutting_bank"
    goalCount: int | None = 5
    observeOnly: bool = False
    brainEnabled: bool = True
    overlayEnabled: bool = True
    overlayMode: str = "intent"
    overlayBackupCandidates: int = 2
    resetBaselineRequested: bool = False
    lastUpdatedUtc: str = field(default_factory=utc_now)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONTROL_SCHEMA,
            "activeTask": self.activeTask,
            "activeMissionPreset": self.activeMissionPreset,
            "taskPolicy": self.taskPolicy,
            "goalCount": self.goalCount,
            "observeOnly": self.observeOnly,
            "brainEnabled": self.brainEnabled,
            "overlayEnabled": self.overlayEnabled,
            "overlayMode": self.overlayMode,
            "overlayBackupCandidates": self.overlayBackupCandidates,
            "resetBaselineRequested": self.resetBaselineRequested,
            "lastUpdatedUtc": self.lastUpdatedUtc,
            "warnings": list(self.warnings),
        }


@dataclass
class RuntimeControlCommand:
    payload: dict[str, Any]


@dataclass
class RuntimeControlResult:
    status: str
    acceptedFields: list[str] = field(default_factory=list)
    rejectedFields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    state: RuntimeControlState | None = None
    resetBrainState: bool = False
    noActionEmitted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CONTROL_RESULT_SCHEMA,
            "status": self.status,
            "acceptedFields": list(self.acceptedFields),
            "rejectedFields": list(self.rejectedFields),
            "warnings": list(self.warnings),
            "state": self.state.to_dict() if self.state is not None else None,
            "resetBrainState": self.resetBrainState,
            "noActionEmitted": self.noActionEmitted,
        }


def _field_looks_action_like(name: str) -> bool:
    lowered = str(name or "").lower()
    return any(fragment in lowered for fragment in ACTION_LIKE_FIELD_FRAGMENTS)


def _action_like_keys(value: Any, prefix: str = "") -> list[str]:
    if not isinstance(value, dict):
        return []
    rejected: list[str] = []
    for key, child in value.items():
        text = str(key)
        path = f"{prefix}.{text}" if prefix else text
        if _field_looks_action_like(text):
            rejected.append(path)
        rejected.extend(_action_like_keys(child, path))
    return rejected


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return None


def _coerce_int(value: Any, *, minimum: int = 0, maximum: int | None = None) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def validate_control_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(payload, dict):
        return {}, ["payload"], ["control payload must be a JSON object"]

    accepted: dict[str, Any] = {}
    rejected: list[str] = []
    warnings: list[str] = []

    if "missionPreset" in payload:
        preset_name = str(payload.get("missionPreset") or "").strip()
        try:
            preset_fields = mission_presets.runtime_control_fields_for_preset(preset_name)
        except (KeyError, TypeError, ValueError):
            rejected.append("missionPreset")
        else:
            accepted["missionPreset"] = preset_name
            accepted["activeMissionPreset"] = preset_name
            accepted.update(preset_fields)

    for field_name, value in payload.items():
        if field_name == "missionPreset":
            continue
        if field_name not in ALLOWED_FIELDS:
            rejected.append(str(field_name))
            continue
        if _field_looks_action_like(field_name):
            rejected.append(str(field_name))
            continue
        nested_action_fields = _action_like_keys(value, str(field_name))
        if nested_action_fields:
            rejected.extend(nested_action_fields)
            continue

        if field_name == "taskPolicy":
            policy_name = str(value or "").strip()
            if policy_name not in task_policy.policy_names():
                rejected.append(field_name)
            else:
                accepted[field_name] = policy_name
        elif field_name == "goalCount":
            if value is None or value == "":
                accepted[field_name] = None
            else:
                goal_count = _coerce_int(value, minimum=0)
                if goal_count is None:
                    rejected.append(field_name)
                else:
                    accepted[field_name] = goal_count
        elif field_name in {"observeOnly", "resetBrainState", "brainEnabled", "overlayEnabled"}:
            bool_value = _coerce_bool(value)
            if bool_value is None:
                rejected.append(field_name)
            else:
                accepted[field_name] = bool_value
        elif field_name == "overlayMode":
            overlay_mode = str(value or "").strip()
            if overlay_mode not in OVERLAY_MODES:
                rejected.append(field_name)
            else:
                accepted[field_name] = overlay_mode
        elif field_name == "overlayBackupCandidates":
            count = _coerce_int(value, minimum=0, maximum=25)
            if count is None:
                rejected.append(field_name)
            else:
                accepted[field_name] = count

    return accepted, rejected, warnings


def apply_control_command(state: RuntimeControlState, payload: dict[str, Any] | RuntimeControlCommand) -> RuntimeControlResult:
    command_payload = payload.payload if isinstance(payload, RuntimeControlCommand) else payload
    accepted, rejected, warnings = validate_control_payload(command_payload if isinstance(command_payload, dict) else {})
    if rejected:
        return RuntimeControlResult(
            status="FAIL",
            rejectedFields=list(dict.fromkeys(rejected)),
            warnings=warnings,
            state=state,
        )

    direct_control_fields = {"taskPolicy", "observeOnly", "brainEnabled", "overlayEnabled", "overlayMode", "overlayBackupCandidates"}
    if "missionPreset" not in accepted and any(field_name in accepted for field_name in direct_control_fields):
        state.activeMissionPreset = None

    for field_name, value in accepted.items():
        if field_name == "resetBrainState":
            if value:
                state.resetBaselineRequested = True
        elif field_name == "missionPreset":
            continue
        else:
            setattr(state, field_name, value)

    if accepted:
        state.lastUpdatedUtc = utc_now()
    if warnings:
        state.warnings = list(dict.fromkeys([*state.warnings, *warnings]))

    return RuntimeControlResult(
        status="PASS",
        acceptedFields=list(accepted.keys()),
        warnings=warnings,
        state=state,
        resetBrainState=bool(accepted.get("resetBrainState")),
    )
