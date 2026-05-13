from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class AnalyzerContractFields:
    @property
    def missingCapabilities(self) -> list[str]:
        return self.missing_capabilities

    @property
    def sourceTick(self) -> int | None:
        return self.source_tick

    @property
    def retainedFromPrevious(self) -> bool:
        return self.retained_from_previous

    @property
    def timingMillis(self) -> float | None:
        return self.timing_millis

    def contract_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "warnings": list(self.warnings),
            "missingCapabilities": list(self.missing_capabilities),
            "sourceTick": self.source_tick,
            "retainedFromPrevious": self.retained_from_previous,
            "timingMillis": self.timing_millis,
        }


@dataclass
class AnalyzerWarning:
    message: str
    code: str | None = None
    severity: str = "WARN"
    capability: str | None = None


@dataclass
class AnalyzerTiming:
    timing_millis: float | None = None
    source_tick: int | None = None


@dataclass
class CapabilityStatus:
    name: str
    status: str = "unavailable"
    reason: str | None = None
    optional: bool = False


@dataclass
class MissingCapability:
    name: str
    reason: str | None = None
    optional: bool = False


@dataclass
class AnalyzerResult(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None


@dataclass
class LiveInputSnapshot:
    source: str | None = None
    session_path: str | None = None
    latest_tick: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveSourceStatus:
    input_source_active: str | None = None
    fallback_reason: str | None = None
    fresh: bool | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerContext:
    world_x: int | None = None
    world_y: int | None = None
    plane: int | None = None
    scene_x: int | None = None
    scene_y: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventoryContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    inventory: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    progress_result: Any = None
    matched_slots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TargetContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    raw_best_target: dict[str, Any] | None = None
    nearest_target: dict[str, Any] | None = None
    top_candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_count: int = 0


@dataclass
class NavigationContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    collision_known: bool | None = None
    collision_window_available: bool | None = None
    reachable_count: int = 0
    blocked_count: int = 0
    unknown_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivityContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    current_activity: str = "unknown"
    recent_task_signals: list[str] = field(default_factory=list)
    liveness: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentOverlayContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    overlay: dict[str, Any] = field(default_factory=dict)
    markers: list[dict[str, Any]] = field(default_factory=list)
    selected_marker: dict[str, Any] | None = None
    backup_markers: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BrainContext(AnalyzerContractFields):
    status: str = "PASS"
    warnings: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    source_tick: int | None = None
    retained_from_previous: bool = False
    timing_millis: float | None = None
    decision: dict[str, Any] = field(default_factory=dict)
    updated_state: dict[str, Any] = field(default_factory=dict)
    status_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveAnalysisResult:
    input_snapshot: LiveInputSnapshot | None = None
    source_status: LiveSourceStatus | None = None
    player: PlayerContext | None = None
    inventory: InventoryContext | None = None
    targets: TargetContext | None = None
    navigation: NavigationContext | None = None
    activity: ActivityContext | None = None
    intent_overlay: IntentOverlayContext | None = None
    brain: BrainContext | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
