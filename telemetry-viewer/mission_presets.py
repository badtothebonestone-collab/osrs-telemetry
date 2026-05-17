from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MISSION_PRESETS_SCHEMA = "mission_presets.v1"


@dataclass(frozen=True)
class MissionPreset:
    name: str
    activeTask: str
    taskPolicy: str
    goalCount: int | None
    observeOnly: bool
    brainEnabled: bool
    overlayMode: str
    overlayBackupCandidates: int
    description: str
    noActionEmitted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MISSION_PRESETS_SCHEMA,
            "name": self.name,
            "activeTask": self.activeTask,
            "taskPolicy": self.taskPolicy,
            "goalCount": self.goalCount,
            "observeOnly": self.observeOnly,
            "brainEnabled": self.brainEnabled,
            "overlayMode": self.overlayMode,
            "overlayBackupCandidates": self.overlayBackupCandidates,
            "description": self.description,
            "noActionEmitted": self.noActionEmitted,
        }

    def runtime_control_fields(self, *, goal_count: int | str | None = None) -> dict[str, Any]:
        fields = {
            "activeTask": self.activeTask,
            "taskPolicy": self.taskPolicy,
            "goalCount": self.goalCount,
            "observeOnly": self.observeOnly,
            "brainEnabled": self.brainEnabled,
            "overlayMode": self.overlayMode,
            "overlayBackupCandidates": self.overlayBackupCandidates,
        }
        if goal_count not in (None, ""):
            fields["goalCount"] = max(0, int(goal_count))
        return fields


_PRESET_ORDER = (
    "woodcut_bank",
    "woodcut_deposit",
    "woodcut_firemake",
    "woodcut_drop",
    "observe_only",
    "combat_default",
)


PRESETS: dict[str, MissionPreset] = {
    "woodcut_bank": MissionPreset(
        name="woodcut_bank",
        activeTask="woodcutting",
        taskPolicy="woodcutting_bank",
        goalCount=5,
        observeOnly=False,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Woodcutting resources with service context when inventory is full.",
    ),
    "woodcut_deposit": MissionPreset(
        name="woodcut_deposit",
        activeTask="woodcutting",
        taskPolicy="woodcutting_deposit",
        goalCount=5,
        observeOnly=False,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Woodcutting resources with deposit-only service context when inventory is full.",
    ),
    "woodcut_firemake": MissionPreset(
        name="woodcut_firemake",
        activeTask="woodcutting",
        taskPolicy="woodcutting_firemake",
        goalCount=5,
        observeOnly=False,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Woodcutting resources with read-only process-inventory firemaking context.",
    ),
    "woodcut_drop": MissionPreset(
        name="woodcut_drop",
        activeTask="woodcutting",
        taskPolicy="woodcutting_drop",
        goalCount=5,
        observeOnly=False,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Woodcutting resources with read-only process-inventory drop context.",
    ),
    "observe_only": MissionPreset(
        name="observe_only",
        activeTask="observe",
        taskPolicy="observe_only",
        goalCount=None,
        observeOnly=True,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Observe context without a task transition policy.",
    ),
    "combat_default": MissionPreset(
        name="combat_default",
        activeTask="combat",
        taskPolicy="combat_default",
        goalCount=None,
        observeOnly=False,
        brainEnabled=True,
        overlayMode="intent",
        overlayBackupCandidates=2,
        description="Combat-oriented context where full inventory can be expected.",
    ),
}


def preset_names() -> list[str]:
    return [name for name in _PRESET_ORDER if name in PRESETS]


def resolve_mission_preset(name: str) -> MissionPreset:
    preset_name = str(name or "").strip()
    if preset_name not in PRESETS:
        raise KeyError(preset_name)
    return PRESETS[preset_name]


def runtime_control_fields_for_preset(name: str, *, goal_count: int | str | None = None) -> dict[str, Any]:
    return resolve_mission_preset(name).runtime_control_fields(goal_count=goal_count)
