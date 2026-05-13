from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any


DEFAULT_CONSECUTIVE_BETTER_TICKS = 2
DEFAULT_SWITCH_MARGIN_SCORE = 10.0
DEFAULT_SWITCH_MARGIN_DISTANCE_TILES = 2.0
DEFAULT_STALE_TIMEOUT_TICKS = 2
DEFAULT_MAX_CANDIDATES_CONSIDERED = 10
DEFAULT_TRANSIENT_MISSING_GRACE_TICKS = 2
DEFAULT_TRANSIENT_MISSING_GRACE_MILLIS = 1500
DEFAULT_SWITCH_AUDIT_LIMIT = 10

PRIORITY_DIAGNOSTIC = 10
PRIORITY_BACKUP = 30
PRIORITY_SELECTED_TARGET = 50
PRIORITY_TASK_TRANSITION = 70
PRIORITY_REQUIRED_TRANSITION = 80
PRIORITY_EMERGENCY = 100

INVALID_LIVENESS_DEPLETED = {"depleted", "target_depleted"}
INVALID_LIVENESS_STALE = {"stale", "despawned", "recently_unavailable", "unavailable", "recently unavailable"}
INVALID_REACHABILITY = {"blocked", "unreachable", "ui_blocked", "ui-blocked"}


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    if number.is_integer():
        return int(number)
    return None


def safe_get(mapping: dict, path: str, default: Any = None) -> Any:
    value: Any = mapping
    for part in path.split("."):
        if not isinstance(value, dict):
            return default
        value = value.get(part)
    return value if value is not None else default


def target_type_for(candidate: dict) -> str:
    target_type = candidate.get("targetType")
    if target_type:
        return str(target_type)
    if candidate.get("slot") is not None and candidate.get("itemId") is not None:
        return "inventorySlot"
    if candidate.get("npcId") is not None:
        return "npc"
    if candidate.get("uiTargetId") is not None:
        return "ui"
    if candidate.get("itemId") is not None and candidate.get("worldX") is not None:
        return "groundItem"
    if candidate.get("worldX") is not None and candidate.get("worldY") is not None:
        return "sceneObject"
    return "tile"


def reachability_for(candidate: dict) -> str | None:
    navigation = candidate.get("navigation") if isinstance(candidate.get("navigation"), dict) else {}
    value = navigation.get("directReachability") or candidate.get("directReachability") or candidate.get("reachability")
    return str(value) if value is not None else None


def liveness_for(candidate: dict) -> str | None:
    value = candidate.get("targetLiveState") or candidate.get("liveness") or candidate.get("liveState")
    return str(value) if value is not None else None


def aim_point_for(candidate: dict) -> dict | None:
    value = candidate.get("aimPoint")
    return dict(value) if isinstance(value, dict) else None


def normalize_explicit_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    for prefix in ("objectKey:", "candidateKey:", "targetKey:"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix):]
    return text


def build_target_key(candidate: dict, target_type: str) -> str:
    for key in ("targetKey", "objectKey", "candidateKey"):
        normalized = normalize_explicit_key(candidate.get(key))
        if normalized:
            return normalized
    normalized_key = normalize_explicit_key(candidate.get("key"))
    if normalized_key:
        return normalized_key
    if candidate.get("hash") is not None:
        return f"hash:{candidate.get('hash')}"
    parts = [
        candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
        candidate.get("worldX"),
        candidate.get("worldY"),
        candidate.get("plane"),
        candidate.get("classId"),
    ]
    if not any(part is not None for part in parts):
        parts = [
            target_type,
            candidate.get("classId"),
            candidate.get("sceneX"),
            candidate.get("sceneY"),
            candidate.get("slot"),
        ]
    return "|".join(str(part) for part in parts if part is not None)


def target_identity_keys_for_candidate(candidate: dict, target_type: str | None = None) -> set[tuple]:
    if not isinstance(candidate, dict):
        return set()
    target_type = target_type or target_type_for(candidate)
    keys: set[tuple] = set()
    for key_name in ("objectKey", "targetKey", "candidateKey", "key"):
        normalized = normalize_explicit_key(candidate.get(key_name))
        if normalized:
            if key_name == "key" and not str(candidate.get(key_name)).startswith(("objectKey:", "targetKey:", "candidateKey:")):
                continue
            keys.add(("explicit", normalized))
    if candidate.get("hash") is not None:
        keys.add(("hash", str(candidate.get("hash"))))
    raw_id = candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id")
    kind = candidate.get("kind") or candidate.get("layer")
    if raw_id is not None and candidate.get("worldX") is not None and candidate.get("worldY") is not None:
        keys.add(("world", str(target_type), str(kind or ""), str(raw_id), str(candidate.get("worldX")), str(candidate.get("worldY")), str(candidate.get("plane"))))
    if raw_id is not None and candidate.get("sceneX") is not None and candidate.get("sceneY") is not None:
        keys.add(("scene", str(target_type), str(kind or ""), str(raw_id), str(candidate.get("sceneX")), str(candidate.get("sceneY")), str(candidate.get("plane"))))
    return keys


def explicit_identity_values(candidate: dict) -> set[str]:
    values: set[str] = set()
    for key_name in ("objectKey", "targetKey", "candidateKey", "key"):
        normalized = normalize_explicit_key(candidate.get(key_name))
        if not normalized:
            continue
        if key_name == "key" and not str(candidate.get(key_name)).startswith(("objectKey:", "targetKey:", "candidateKey:")):
            continue
        values.add(normalized)
    return values


def world_identity_value(candidate: dict, target_type: str | None = None) -> tuple | None:
    target_type = target_type or target_type_for(candidate)
    raw_id = candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id")
    if raw_id is None or candidate.get("worldX") is None or candidate.get("worldY") is None:
        return None
    return (
        "world",
        str(target_type),
        str(candidate.get("kind") or candidate.get("layer") or ""),
        str(raw_id),
        str(candidate.get("worldX")),
        str(candidate.get("worldY")),
        str(candidate.get("plane")),
    )


def scene_identity_value(candidate: dict, target_type: str | None = None) -> tuple | None:
    target_type = target_type or target_type_for(candidate)
    raw_id = candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id")
    if raw_id is None or candidate.get("sceneX") is None or candidate.get("sceneY") is None:
        return None
    return (
        "scene",
        str(target_type),
        str(candidate.get("kind") or candidate.get("layer") or ""),
        str(raw_id),
        str(candidate.get("sceneX")),
        str(candidate.get("sceneY")),
        str(candidate.get("plane")),
    )


def same_candidate_identity(left: dict, right: dict) -> bool:
    left_explicit = explicit_identity_values(left)
    right_explicit = explicit_identity_values(right)
    if left_explicit and right_explicit:
        return bool(left_explicit.intersection(right_explicit))
    if left.get("hash") is not None and right.get("hash") is not None:
        return str(left.get("hash")) == str(right.get("hash"))
    left_world = world_identity_value(left)
    right_world = world_identity_value(right)
    if left_world is not None and right_world is not None:
        return left_world == right_world
    left_scene = scene_identity_value(left)
    right_scene = scene_identity_value(right)
    if left_scene is not None and right_scene is not None:
        return left_scene == right_scene
    return False


@dataclass
class IntentTarget:
    targetKey: str
    raw: dict = field(default_factory=dict)
    markerType: str = "selected_target"
    targetType: str = "sceneObject"
    classId: str | None = None
    id: Any = None
    hash: Any = None
    worldX: int | None = None
    worldY: int | None = None
    plane: int | None = None
    sceneX: int | None = None
    sceneY: int | None = None
    aimPoint: dict | None = None
    reachability: str | None = None
    liveness: str | None = None
    qualityScore: float | None = None
    distanceTiles: float | None = None
    task: str | None = None
    profile: str | None = None
    tick: int | None = None
    priority: int = PRIORITY_SELECTED_TARGET
    interruptLevel: int | None = None
    present: bool = True

    @classmethod
    def from_candidate(
        cls,
        candidate: dict | None,
        *,
        task: str | None = None,
        profile: str | None = None,
        tick: int | None = None,
        marker_type: str = "selected_target",
        priority: int = PRIORITY_SELECTED_TARGET,
    ) -> "IntentTarget | None":
        if not isinstance(candidate, dict) or not candidate:
            return None
        target_type = target_type_for(candidate)
        key = build_target_key(candidate, target_type)
        if not key:
            return None
        quality = (
            as_float(candidate.get("qualityScore"))
            or as_float(candidate.get("score"))
            or as_float(candidate.get("candidateScore"))
            or as_float(candidate.get("rankScore"))
        )
        candidate_tick = (
            as_int(candidate.get("lastSeenTick"))
            or as_int(candidate.get("lastUpdatedTick"))
            or as_int(candidate.get("tick"))
            or tick
        )
        return cls(
            targetKey=key,
            raw=dict(candidate),
            markerType=marker_type,
            targetType=target_type,
            classId=str(candidate.get("classId")) if candidate.get("classId") is not None else None,
            id=candidate.get("rawId") if candidate.get("rawId") is not None else candidate.get("id"),
            hash=candidate.get("hash"),
            worldX=as_int(candidate.get("worldX")),
            worldY=as_int(candidate.get("worldY")),
            plane=as_int(candidate.get("plane")),
            sceneX=as_int(candidate.get("sceneX")),
            sceneY=as_int(candidate.get("sceneY")),
            aimPoint=aim_point_for(candidate),
            reachability=reachability_for(candidate),
            liveness=liveness_for(candidate),
            qualityScore=quality,
            distanceTiles=as_float(candidate.get("distanceTiles")),
            task=task,
            profile=profile,
            tick=candidate_tick,
            priority=as_int(candidate.get("priority")) or priority,
            interruptLevel=as_int(candidate.get("interruptLevel")),
            present=bool(candidate.get("present", True)),
        )

    def with_raw(self, candidate: dict, *, tick: int | None = None) -> "IntentTarget":
        fresh = IntentTarget.from_candidate(
            candidate,
            task=self.task,
            profile=self.profile,
            tick=tick,
            marker_type=self.markerType,
            priority=self.priority,
        )
        return fresh if fresh is not None else self

    def identity_keys(self) -> set[tuple]:
        keys = target_identity_keys_for_candidate(self.raw, self.targetType)
        if self.targetKey:
            keys.add(("explicit", self.targetKey))
        return keys


@dataclass
class IntentState:
    selectedTarget: IntentTarget | None = None
    selectedTargetKey: str | None = None
    rawBestTargetKey: str | None = None
    activeTask: str | None = None
    activeProfile: str | None = None
    activeIntent: str | None = None
    stableForTicks: int = 0
    pendingBetterTargetKey: str | None = None
    pendingBetterTicks: int = 0
    currentMissingTicks: int = 0
    currentMissingSinceMillis: int | None = None
    backupTargetKeys: list[str] = field(default_factory=list)
    switchAudit: list[dict] = field(default_factory=list)
    lastTick: int | None = None


@dataclass
class IntentResult:
    state: IntentState
    selectedTarget: IntentTarget | None
    rawBestTarget: IntentTarget | None
    previousTargetKey: str | None
    selectedTargetKey: str | None
    rawBestTargetKey: str | None
    stableForTicks: int
    candidateWasRetained: bool
    candidateWasSwitched: bool
    switchReason: str
    hardSwitch: bool = False
    softSwitch: bool = False
    interruptReason: str | None = None
    candidatesConsidered: int = 0
    retainedDueToGrace: bool = False
    currentMissingTicks: int = 0
    currentTargetMissingThisTick: bool = False
    currentTargetExplicitlyInvalid: bool = False
    currentInvalidReason: str | None = None
    switchAuditTail: list[dict] = field(default_factory=list)

    def to_status_fields(self) -> dict:
        return {
            "selectedTargetKey": self.selectedTargetKey,
            "rawBestTargetKey": self.rawBestTargetKey,
            "previousTargetKey": self.previousTargetKey,
            "stableForTicks": self.stableForTicks,
            "candidateWasRetained": self.candidateWasRetained,
            "candidateWasSwitched": self.candidateWasSwitched,
            "switchReason": self.switchReason,
            "hardSwitch": self.hardSwitch,
            "softSwitch": self.softSwitch,
            "interruptReason": self.interruptReason,
            "candidatesConsidered": self.candidatesConsidered,
            "retainedDueToGrace": self.retainedDueToGrace,
            "currentMissingTicks": self.currentMissingTicks,
            "currentTargetMissingThisTick": self.currentTargetMissingThisTick,
            "currentTargetExplicitlyInvalid": self.currentTargetExplicitlyInvalid,
            "currentInvalidReason": self.currentInvalidReason,
            "switchAuditTail": self.switchAuditTail,
        }


class IntentStabilizer:
    def __init__(
        self,
        *,
        consecutive_better_ticks: int = DEFAULT_CONSECUTIVE_BETTER_TICKS,
        switch_margin_score: float = DEFAULT_SWITCH_MARGIN_SCORE,
        switch_margin_distance_tiles: float = DEFAULT_SWITCH_MARGIN_DISTANCE_TILES,
        stale_timeout_ticks: int = DEFAULT_STALE_TIMEOUT_TICKS,
        max_candidates_considered: int = DEFAULT_MAX_CANDIDATES_CONSIDERED,
        transient_missing_grace_ticks: int = DEFAULT_TRANSIENT_MISSING_GRACE_TICKS,
        transient_missing_grace_millis: int = DEFAULT_TRANSIENT_MISSING_GRACE_MILLIS,
        switch_audit_limit: int = DEFAULT_SWITCH_AUDIT_LIMIT,
    ):
        self.state = IntentState()
        self.consecutive_better_ticks = max(1, int(consecutive_better_ticks))
        self.switch_margin_score = float(switch_margin_score)
        self.switch_margin_distance_tiles = float(switch_margin_distance_tiles)
        self.stale_timeout_ticks = max(0, int(stale_timeout_ticks))
        self.max_candidates_considered = max(1, int(max_candidates_considered))
        self.transient_missing_grace_ticks = max(0, int(transient_missing_grace_ticks))
        self.transient_missing_grace_millis = max(0, int(transient_missing_grace_millis))
        self.switch_audit_limit = max(1, int(switch_audit_limit))

    def choose(self, candidates: list[dict], context: dict) -> IntentResult:
        return choose_stable_intent(self.state, candidates, context, stabilizer=self)


def priority_for_context(context: dict) -> int:
    explicit = as_int(context.get("intentPriority") or context.get("priority"))
    if explicit is not None:
        return explicit
    intent = str(context.get("activeIntent") or context.get("intent") or "")
    if any(token in intent for token in ("emergency", "threat", "escape")):
        return PRIORITY_EMERGENCY
    if any(token in intent for token in ("bank", "full", "goal_complete", "transition")):
        return PRIORITY_TASK_TRANSITION
    return PRIORITY_SELECTED_TARGET


def latest_tick_for(context: dict) -> int | None:
    return as_int(context.get("latestTick") or context.get("tick") or safe_get(context, "status.latestTick") or safe_get(context, "status.lastProcessedTick"))


def candidate_list(candidates: Any, context: dict, max_candidates: int, previous_key: str | None = None) -> list[dict]:
    rows = [dict(row) for row in candidates if isinstance(row, dict)] if isinstance(candidates, list) else []
    raw = context.get("rawBestTarget") or safe_get(context, "brain.currentContextSummary.bestTarget")
    if isinstance(raw, dict) and raw:
        raw_key = build_target_key(raw, target_type_for(raw))
        matching = next((row for row in rows if build_target_key(row, target_type_for(row)) == raw_key or same_candidate_identity(row, raw)), None)
        if matching is not None:
            enriched = dict(matching)
            enriched.update({key: value for key, value in raw.items() if value is not None})
            raw = enriched
        rows = [raw] + [row for row in rows if build_target_key(row, target_type_for(row)) != raw_key and not same_candidate_identity(row, raw)]
    limited = rows[:max_candidates]
    if previous_key and not any(build_target_key(row, target_type_for(row)) == previous_key for row in limited):
        for row in rows[max_candidates:]:
            if build_target_key(row, target_type_for(row)) == previous_key:
                if len(limited) >= max_candidates:
                    limited[-1] = row
                else:
                    limited.append(row)
                break
    return limited


def target_map_for(targets: list[IntentTarget]) -> dict[str, IntentTarget]:
    return {target.targetKey: target for target in targets if target.targetKey}


def same_target_identity(left: IntentTarget | None, right: IntentTarget | None) -> bool:
    if left is None or right is None:
        return False
    if left.targetKey and right.targetKey and left.targetKey == right.targetKey:
        return True
    return same_candidate_identity(left.raw, right.raw)


def find_matching_target(targets: list[IntentTarget], previous: IntentTarget | None) -> IntentTarget | None:
    if previous is None:
        return None
    for target in targets:
        if same_target_identity(target, previous):
            return target
    return None


def current_millis_for(context: dict) -> int:
    explicit = as_int(context.get("nowMillis") or context.get("currentMillis") or context.get("timestampMillis"))
    if explicit is not None:
        return explicit
    return int(time.time() * 1000)


def invalid_reason_for(
    target: IntentTarget | None,
    *,
    current_tick: int | None,
    require_reachability: bool,
    require_aim_point: bool,
    stale_timeout_ticks: int = DEFAULT_STALE_TIMEOUT_TICKS,
) -> str | None:
    if target is None:
        return "current_target_missing"
    if target.present is False:
        return "current_target_missing"
    liveness = str(target.liveness or "").lower()
    if any(token in liveness for token in INVALID_LIVENESS_DEPLETED):
        return "current_target_depleted"
    if any(token in liveness for token in INVALID_LIVENESS_STALE):
        return "current_target_stale"
    reachability = str(target.reachability or "").lower()
    if require_reachability and any(token in reachability for token in INVALID_REACHABILITY):
        return "current_target_unreachable"
    if require_aim_point and not target.aimPoint:
        return "current_target_missing"
    if current_tick is not None and target.tick is not None and int(current_tick) - int(target.tick) > stale_timeout_ticks:
        return "current_target_stale"
    return None


def score_margin_reason(raw: IntentTarget, previous: IntentTarget, *, score_margin: float, distance_margin: float) -> str | None:
    raw_score = raw.qualityScore
    previous_score = previous.qualityScore
    if raw_score is not None and previous_score is not None and raw_score - previous_score >= score_margin:
        return "better_candidate_score_margin"
    raw_distance = raw.distanceTiles
    previous_distance = previous.distanceTiles
    if raw_distance is not None and previous_distance is not None and previous_distance - raw_distance >= distance_margin:
        return "better_candidate_distance_margin"
    return None


def publish_result(
    state: IntentState,
    *,
    selected: IntentTarget | None,
    raw_best: IntentTarget | None,
    previous_key: str | None,
    reason: str,
    hard: bool = False,
    soft: bool = False,
    retained: bool = False,
    switched: bool = False,
    interrupt_reason: str | None = None,
    candidates_considered: int = 0,
    retained_due_to_grace: bool = False,
    current_missing_this_tick: bool = False,
    current_explicitly_invalid: bool = False,
    current_invalid_reason: str | None = None,
    better_candidate_persisted_ticks: int = 0,
    switch_audit_limit: int = DEFAULT_SWITCH_AUDIT_LIMIT,
) -> IntentResult:
    state.selectedTarget = selected
    state.selectedTargetKey = selected.targetKey if selected else None
    state.rawBestTargetKey = raw_best.targetKey if raw_best else None
    audit_entry = {
        "tick": state.lastTick,
        "previousTargetKey": previous_key,
        "rawBestTargetKey": state.rawBestTargetKey,
        "selectedTargetKey": state.selectedTargetKey,
        "switchReason": reason,
        "hardSwitch": hard,
        "retainedDueToGrace": retained_due_to_grace,
        "currentTargetMissingThisTick": current_missing_this_tick,
        "currentTargetExplicitlyInvalid": current_explicitly_invalid,
        "betterCandidatePersistedTicks": better_candidate_persisted_ticks,
        "candidateCount": candidates_considered,
        "activeTask": state.activeTask,
        "profile": state.activeProfile,
    }
    if current_invalid_reason:
        audit_entry["currentInvalidReason"] = current_invalid_reason
    if interrupt_reason:
        audit_entry["interruptReason"] = interrupt_reason
    state.switchAudit.append(audit_entry)
    if len(state.switchAudit) > switch_audit_limit:
        del state.switchAudit[: len(state.switchAudit) - switch_audit_limit]
    audit_tail = [dict(entry) for entry in state.switchAudit[-switch_audit_limit:]]
    return IntentResult(
        state=state,
        selectedTarget=selected,
        rawBestTarget=raw_best,
        previousTargetKey=previous_key,
        selectedTargetKey=state.selectedTargetKey,
        rawBestTargetKey=state.rawBestTargetKey,
        stableForTicks=state.stableForTicks,
        candidateWasRetained=retained,
        candidateWasSwitched=switched,
        switchReason=reason,
        hardSwitch=hard,
        softSwitch=soft,
        interruptReason=interrupt_reason,
        candidatesConsidered=candidates_considered,
        retainedDueToGrace=retained_due_to_grace,
        currentMissingTicks=state.currentMissingTicks,
        currentTargetMissingThisTick=current_missing_this_tick,
        currentTargetExplicitlyInvalid=current_explicitly_invalid,
        currentInvalidReason=current_invalid_reason,
        switchAuditTail=audit_tail,
    )


def choose_stable_intent(previous_state: IntentState, candidates: list[dict], context: dict, *, stabilizer: IntentStabilizer | None = None) -> IntentResult:
    max_candidates = stabilizer.max_candidates_considered if stabilizer else DEFAULT_MAX_CANDIDATES_CONSIDERED
    consecutive_better_ticks = stabilizer.consecutive_better_ticks if stabilizer else DEFAULT_CONSECUTIVE_BETTER_TICKS
    stale_timeout_ticks = stabilizer.stale_timeout_ticks if stabilizer else DEFAULT_STALE_TIMEOUT_TICKS
    score_margin = stabilizer.switch_margin_score if stabilizer else DEFAULT_SWITCH_MARGIN_SCORE
    distance_margin = stabilizer.switch_margin_distance_tiles if stabilizer else DEFAULT_SWITCH_MARGIN_DISTANCE_TILES
    transient_missing_grace_ticks = stabilizer.transient_missing_grace_ticks if stabilizer else DEFAULT_TRANSIENT_MISSING_GRACE_TICKS
    transient_missing_grace_millis = stabilizer.transient_missing_grace_millis if stabilizer else DEFAULT_TRANSIENT_MISSING_GRACE_MILLIS
    switch_audit_limit = stabilizer.switch_audit_limit if stabilizer else DEFAULT_SWITCH_AUDIT_LIMIT
    task = str(context.get("activeTask") or context.get("task") or context.get("profile") or "")
    profile = str(context.get("profile") or task or "")
    intent = str(context.get("activeIntent") or context.get("intent") or context.get("phase") or "observe")
    current_tick = latest_tick_for(context)
    now_millis = current_millis_for(context)
    priority = priority_for_context(context)
    previous_key = previous_state.selectedTargetKey
    rows = candidate_list(candidates, context, max_candidates, previous_key=previous_key)
    targets = [
        target
        for target in (IntentTarget.from_candidate(row, task=task, profile=profile, tick=current_tick, priority=priority) for row in rows)
        if target is not None
    ]
    targets_by_key = target_map_for(targets)
    raw_best = targets[0] if targets else None

    interrupt_target = IntentTarget.from_candidate(
        context.get("interruptTarget"),
        task=task,
        profile=profile,
        tick=current_tick,
        priority=as_int(context.get("interruptPriority")) or PRIORITY_EMERGENCY,
    )
    interrupt_reason = context.get("interruptReason")
    if context.get("interrupt") and interrupt_target is not None:
        raw_best = interrupt_target
        if interrupt_target.targetKey not in targets_by_key:
            targets_by_key[interrupt_target.targetKey] = interrupt_target
        targets = [interrupt_target] + [target for target in targets if target.targetKey != interrupt_target.targetKey]

    selected_previous = targets_by_key.get(previous_key or "")
    previous_present_this_tick = selected_previous is not None
    if selected_previous is None:
        identity_match = find_matching_target(targets, previous_state.selectedTarget)
        if identity_match is not None:
            selected_previous = identity_match
            previous_present_this_tick = True
        else:
            selected_previous = previous_state.selectedTarget

    previous_task = previous_state.activeTask or (previous_state.selectedTarget.task if previous_state.selectedTarget else None)
    previous_profile = previous_state.activeProfile or (previous_state.selectedTarget.profile if previous_state.selectedTarget else None)
    previous_intent = previous_state.activeIntent
    previous_state.lastTick = current_tick
    previous_state.activeTask = task
    previous_state.activeProfile = profile
    previous_state.activeIntent = intent

    def reset_missing_state() -> None:
        previous_state.currentMissingTicks = 0
        previous_state.currentMissingSinceMillis = None

    def hard_switch(
        selected: IntentTarget | None,
        reason: str,
        interrupt: str | None = None,
        *,
        current_missing: bool = False,
        current_invalid: bool = False,
        current_invalid_reason: str | None = None,
    ) -> IntentResult:
        previous_state.pendingBetterTargetKey = None
        previous_state.pendingBetterTicks = 0
        reset_missing_state()
        previous_state.stableForTicks = 1 if selected else 0
        return publish_result(
            previous_state,
            selected=selected,
            raw_best=raw_best,
            previous_key=previous_key,
            reason=reason,
            hard=True,
            switched=bool(previous_key and selected and not same_target_identity(selected, previous_state.selectedTarget)),
            interrupt_reason=interrupt,
            candidates_considered=len(targets),
            current_missing_this_tick=current_missing,
            current_explicitly_invalid=current_invalid,
            current_invalid_reason=current_invalid_reason,
            switch_audit_limit=switch_audit_limit,
        )

    def retain_missing_current(reason: str) -> IntentResult:
        previous_state.currentMissingTicks += 1
        if previous_state.currentMissingSinceMillis is None:
            previous_state.currentMissingSinceMillis = now_millis
        if current_tick is not None:
            within_grace = previous_state.currentMissingTicks <= transient_missing_grace_ticks
        else:
            within_grace = (now_millis - previous_state.currentMissingSinceMillis) <= transient_missing_grace_millis
        if within_grace and selected_previous is not None:
            previous_state.pendingBetterTargetKey = None
            previous_state.pendingBetterTicks = 0
            previous_state.stableForTicks += 1
            return publish_result(
                previous_state,
                selected=selected_previous,
                raw_best=raw_best,
                previous_key=previous_key,
                reason=reason,
                retained=True,
                retained_due_to_grace=True,
                current_missing_this_tick=True,
                candidates_considered=len(targets),
                switch_audit_limit=switch_audit_limit,
            )
        return hard_switch(raw_best, "current_target_missing", current_missing=True)

    if previous_state.selectedTarget is None or previous_key is None:
        reset_missing_state()
        previous_state.stableForTicks = 1 if raw_best else 0
        return publish_result(
            previous_state,
            selected=raw_best,
            raw_best=raw_best,
            previous_key=None,
            reason="initial_selection" if raw_best else "current_target_missing",
            hard=True,
            switched=bool(raw_best),
            candidates_considered=len(targets),
            switch_audit_limit=switch_audit_limit,
        )

    if previous_task is not None and task != previous_task:
        return hard_switch(raw_best, "task_changed")

    if previous_profile is not None and profile != previous_profile:
        return hard_switch(raw_best, "task_changed")

    if previous_intent is not None and intent != previous_intent:
        return hard_switch(raw_best, "intent_changed")

    if context.get("forceSwitch"):
        return hard_switch(raw_best, "force_switch")

    if context.get("interrupt"):
        return hard_switch(raw_best, "interrupt", str(interrupt_reason or "interrupt"))

    if raw_best is None:
        if selected_previous is None:
            return hard_switch(None, "current_target_missing", current_missing=True)
        explicit_invalid = invalid_reason_for(
            selected_previous,
            current_tick=None,
            require_reachability=bool(context.get("requireReachability", True)),
            require_aim_point=bool(context.get("requireAimPoint", False)),
            stale_timeout_ticks=stale_timeout_ticks,
        )
        if explicit_invalid:
            return hard_switch(None, explicit_invalid, current_missing=True, current_invalid=True, current_invalid_reason=explicit_invalid)
        return retain_missing_current("retained_current_target_transient_missing")

    if raw_best.priority > (selected_previous.priority if selected_previous else PRIORITY_SELECTED_TARGET):
        return hard_switch(raw_best, "higher_priority_intent")

    current_missing_this_tick = selected_previous is not None and not previous_present_this_tick
    invalid_reason = invalid_reason_for(
        selected_previous,
        current_tick=None if current_missing_this_tick else current_tick,
        require_reachability=bool(context.get("requireReachability", True)),
        require_aim_point=bool(context.get("requireAimPoint", False)),
        stale_timeout_ticks=stale_timeout_ticks,
    )
    if current_missing_this_tick and invalid_reason is None:
        return retain_missing_current("retained_current_target_transient_missing")
    if invalid_reason:
        return hard_switch(raw_best, invalid_reason, current_missing=current_missing_this_tick, current_invalid=True, current_invalid_reason=invalid_reason)

    reset_missing_state()

    if selected_previous and selected_previous.aimPoint is None and raw_best.aimPoint is not None and not same_target_identity(raw_best, selected_previous):
        return hard_switch(raw_best, "current_target_missing")

    if selected_previous and same_target_identity(raw_best, selected_previous):
        previous_state.pendingBetterTargetKey = None
        previous_state.pendingBetterTicks = 0
        previous_state.stableForTicks += 1
        selected = selected_previous if previous_present_this_tick else raw_best
        return publish_result(
            previous_state,
            selected=selected,
            raw_best=raw_best,
            previous_key=previous_key,
            reason="retained_current_target",
            retained=True,
            candidates_considered=len(targets),
            switch_audit_limit=switch_audit_limit,
        )

    margin_reason = score_margin_reason(raw_best, selected_previous, score_margin=score_margin, distance_margin=distance_margin)
    if margin_reason:
        previous_state.pendingBetterTargetKey = None
        previous_state.pendingBetterTicks = 0
        previous_state.stableForTicks = 1
        return publish_result(
            previous_state,
            selected=raw_best,
            raw_best=raw_best,
            previous_key=previous_key,
            reason=margin_reason,
            soft=True,
            switched=True,
            candidates_considered=len(targets),
            switch_audit_limit=switch_audit_limit,
        )

    if previous_state.pendingBetterTargetKey == raw_best.targetKey:
        previous_state.pendingBetterTicks += 1
    else:
        previous_state.pendingBetterTargetKey = raw_best.targetKey
        previous_state.pendingBetterTicks = 1

    if previous_state.pendingBetterTicks >= consecutive_better_ticks:
        previous_state.pendingBetterTargetKey = None
        previous_state.pendingBetterTicks = 0
        previous_state.stableForTicks = 1
        return publish_result(
            previous_state,
            selected=raw_best,
            raw_best=raw_best,
            previous_key=previous_key,
            reason="better_candidate_persisted",
            soft=True,
            switched=True,
            candidates_considered=len(targets),
            better_candidate_persisted_ticks=consecutive_better_ticks,
            switch_audit_limit=switch_audit_limit,
        )

    previous_state.stableForTicks += 1
    return publish_result(
        previous_state,
        selected=selected_previous,
        raw_best=raw_best,
        previous_key=previous_key,
        reason="retained_current_target",
        retained=True,
        candidates_considered=len(targets),
        better_candidate_persisted_ticks=previous_state.pendingBetterTicks,
        switch_audit_limit=switch_audit_limit,
    )
