from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


YAW_KEYS = frozenset({"left", "right"})
PITCH_KEYS = frozenset({"up", "down"})
CAMERA_KEYS = YAW_KEYS | PITCH_KEYS
YAW_MODULUS = 16_384

_COMPLETE_CLEANUP_FIELDS = (
    "safe",
    "stopAllAcknowledged",
    "disarmAcknowledged",
    "statusAcknowledged",
    "firmwareDisarmed",
    "zeroHeldKeys",
    "zeroHeldMouseButtons",
    "zeroUnresolvedCommands",
    "ledgerClosed",
    "backendClosed",
)


class RetainedCameraTraceError(ValueError):
    """Raised when the compact retained trace loses required proof facts."""


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RetainedCameraTraceError(f"{field} must be an object")
    return value


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RetainedCameraTraceError(f"{field} must be an array")
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetainedCameraTraceError(f"{field} must be an integer")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetainedCameraTraceError(f"{field} must be non-empty text")
    return value.strip()


def _true(value: object, *, field: str) -> bool:
    if value is not True:
        raise RetainedCameraTraceError(f"{field} must be true")
    return True


def _signed_yaw_delta(before: int, after: int) -> int:
    return ((after - before + (YAW_MODULUS // 2)) % YAW_MODULUS) - (
        YAW_MODULUS // 2
    )


def _crossed_zero(before: int, after: int) -> bool:
    return (before < 0 < after) or (after < 0 < before)


def _camera_actions(
    trace: Mapping[str, object],
) -> tuple[list[Mapping[str, object]], list[tuple[int | None, int]]]:
    actions: list[Mapping[str, object]] = []
    burst_counts: list[tuple[int | None, int]] = []
    for expected_burst, value in enumerate(
        _sequence(trace.get("bursts"), field="bursts"),
        start=1,
    ):
        burst = _mapping(value, field=f"bursts[{expected_burst - 1}]")
        actual_burst = _integer(
            burst.get("burst"),
            field=f"bursts[{expected_burst - 1}].burst",
        )
        if actual_burst != expected_burst:
            raise RetainedCameraTraceError("burst identifiers must be sequential")
        before_interaction_value = burst.get("beforeInteraction")
        before_interaction = (
            None
            if before_interaction_value is None
            else _integer(
                before_interaction_value,
                field=f"bursts[{expected_burst - 1}].beforeInteraction",
            )
        )
        burst_actions = [
            _mapping(action, field=f"bursts[{expected_burst - 1}].actions")
            for action in _sequence(
                burst.get("actions"),
                field=f"bursts[{expected_burst - 1}].actions",
            )
        ]
        if not burst_actions:
            raise RetainedCameraTraceError("retained bursts must not be empty")
        actions.extend(burst_actions)
        burst_counts.append((before_interaction, len(burst_actions)))
    return actions, burst_counts


def analyze_retained_camera_trace(
    trace: Mapping[str, object],
) -> dict[str, Any]:
    """Recompute camera facts from the compact retained signed evidence.

    The result describes what the old run recorded. It deliberately does not
    predict interactions or gameplay outcomes under a different controller.
    """

    if trace.get("schema") != "retained_camera_trace.v1":
        raise RetainedCameraTraceError("unsupported retained camera trace schema")

    universal = _mapping(
        trace.get("universalCameraEvidence"),
        field="universalCameraEvidence",
    )
    if universal.get("wireCommand") != "KEY_PRESS":
        raise RetainedCameraTraceError("camera evidence must use typed KEY_PRESS")
    for field in (
        "wireAccepted",
        "wireAcknowledged",
        "fresh",
        "wallClockFresh",
        "coherent",
    ):
        _true(universal.get(field), field=f"universalCameraEvidence.{field}")

    interaction_count = _integer(
        trace.get("interactionDecisionCount"),
        field="interactionDecisionCount",
    )
    if interaction_count < 0:
        raise RetainedCameraTraceError("interactionDecisionCount must be non-negative")

    actions, burst_counts = _camera_actions(trace)
    key_counts: Counter[str] = Counter()
    reversal_count = 0
    proven_overshoot_reversals = 0
    target_switches = 0
    pitch_no_effects = 0
    redundant_pitch_limit_attempts = 0
    pitch_limits: set[tuple[str, int]] = set()
    pose_changed_count = 0
    geometry_changed_count = 0

    action_index = 0
    bursts = _sequence(trace.get("bursts"), field="bursts")
    for burst_index, value in enumerate(bursts):
        burst = _mapping(value, field=f"bursts[{burst_index}]")
        previous_action: Mapping[str, object] | None = None
        previous_yaw: Mapping[str, object] | None = None
        for raw_action in _sequence(
            burst.get("actions"),
            field=f"bursts[{burst_index}].actions",
        ):
            action = _mapping(
                raw_action,
                field=f"bursts[{burst_index}].actions[{action_index}]",
            )
            action_index += 1
            source_tick = _integer(
                action.get("sourceTick"), field=f"actions[{action_index}].sourceTick"
            )
            observed_tick = _integer(
                action.get("observedTick"),
                field=f"actions[{action_index}].observedTick",
            )
            if observed_tick <= source_tick:
                raise RetainedCameraTraceError(
                    "camera verification must use a newer source tick"
                )
            target = _text(
                action.get("target"), field=f"actions[{action_index}].target"
            )
            key = _text(action.get("key"), field=f"actions[{action_index}].key")
            if key not in CAMERA_KEYS:
                raise RetainedCameraTraceError(f"unsupported camera key {key!r}")
            hold_millis = _integer(
                action.get("holdMillis"),
                field=f"actions[{action_index}].holdMillis",
            )
            if not 1 <= hold_millis <= 250:
                raise RetainedCameraTraceError(
                    "retained camera hold must remain within 1..250 ms"
                )
            yaw_error = _integer(
                action.get("yawErrorUnits"),
                field=f"actions[{action_index}].yawErrorUnits",
            )
            before_yaw = _integer(
                action.get("beforeYaw"), field=f"actions[{action_index}].beforeYaw"
            )
            after_yaw = _integer(
                action.get("afterYaw"), field=f"actions[{action_index}].afterYaw"
            )
            yaw_delta = _integer(
                action.get("yawDelta"), field=f"actions[{action_index}].yawDelta"
            )
            before_pitch = _integer(
                action.get("beforePitch"),
                field=f"actions[{action_index}].beforePitch",
            )
            after_pitch = _integer(
                action.get("afterPitch"),
                field=f"actions[{action_index}].afterPitch",
            )
            pitch_delta = _integer(
                action.get("pitchDelta"),
                field=f"actions[{action_index}].pitchDelta",
            )
            if yaw_delta != _signed_yaw_delta(before_yaw, after_yaw):
                raise RetainedCameraTraceError("yaw delta is not modularly coherent")
            if pitch_delta != after_pitch - before_pitch:
                raise RetainedCameraTraceError("pitch delta is not coherent")
            geometry_changed = action.get("geometryChanged")
            if not isinstance(geometry_changed, bool):
                raise RetainedCameraTraceError("geometryChanged must be boolean")

            status = _text(
                action.get("verificationStatus"),
                field=f"actions[{action_index}].verificationStatus",
            )
            if status == "pose_changed":
                pose_changed_count += 1
                if not geometry_changed:
                    raise RetainedCameraTraceError(
                        "pose-changed verification requires a changed geometry frame"
                    )
                geometry_changed_count += 1
                if key == "right" and yaw_delta <= 0:
                    raise RetainedCameraTraceError(
                        "RIGHT verification must report positive modular yaw"
                    )
                if key == "left" and yaw_delta >= 0:
                    raise RetainedCameraTraceError(
                        "LEFT verification must report negative modular yaw"
                    )
                if key == "up" and pitch_delta <= 0:
                    raise RetainedCameraTraceError(
                        "UP verification must report positive pitch"
                    )
                if key == "down" and pitch_delta >= 0:
                    raise RetainedCameraTraceError(
                        "DOWN verification must report negative pitch"
                    )
            elif status == "no_effect_at_deadline":
                if geometry_changed or yaw_delta or pitch_delta:
                    raise RetainedCameraTraceError(
                        "no-effect verification must retain unchanged pose and geometry"
                    )
                if key in PITCH_KEYS:
                    pitch_no_effects += 1
                    limit = (key, before_pitch)
                    if limit in pitch_limits:
                        redundant_pitch_limit_attempts += 1
                    pitch_limits.add(limit)
            else:
                raise RetainedCameraTraceError(
                    f"unsupported verification status {status!r}"
                )

            key_counts[key] += 1
            if previous_action is not None and previous_action.get("target") != target:
                target_switches += 1
            if key in YAW_KEYS:
                if previous_yaw is not None and previous_yaw.get("key") != key:
                    reversal_count += 1
                    previous_error = _integer(
                        previous_yaw.get("yawErrorUnits"),
                        field="previous yawErrorUnits",
                    )
                    if (
                        previous_yaw.get("target") == target
                        and _crossed_zero(previous_error, yaw_error)
                    ):
                        proven_overshoot_reversals += 1
                previous_yaw = action
            previous_action = action

    actions_before_interactions = [0] * interaction_count
    trailing_actions = 0
    for before_interaction, count in burst_counts:
        if before_interaction is None:
            trailing_actions += count
            continue
        if not 1 <= before_interaction <= interaction_count:
            raise RetainedCameraTraceError(
                "beforeInteraction lies outside the recorded interaction decisions"
            )
        index = before_interaction - 1
        if actions_before_interactions[index]:
            raise RetainedCameraTraceError(
                "multiple retained bursts precede one interaction decision"
            )
        actions_before_interactions[index] = count

    cleanup = _mapping(trace.get("cleanup"), field="cleanup")
    cleanup_complete = all(
        _true(cleanup.get(field), field=f"cleanup.{field}")
        for field in _COMPLETE_CLEANUP_FIELDS
    )

    return {
        "schema": "retained_camera_replay_analysis.v1",
        "proofRun": _text(trace.get("proofRun"), field="proofRun"),
        "cameraActions": len(actions),
        "keyCounts": dict(sorted(key_counts.items())),
        "burstCount": len(burst_counts),
        "actionsBeforeInteractions": actions_before_interactions,
        "trailingActionsWithoutInteraction": trailing_actions,
        "maximumBurst": max((count for _, count in burst_counts), default=0),
        "yawDirectionReversals": reversal_count,
        "provenOvershootReversals": proven_overshoot_reversals,
        "unjustifiedDirectionReversals": (
            reversal_count - proven_overshoot_reversals
        ),
        "targetSwitchesWithinBursts": target_switches,
        "pitchNoEffectAttempts": pitch_no_effects,
        "redundantPitchLimitAttempts": redundant_pitch_limit_attempts,
        "poseChangedVerifications": pose_changed_count,
        "geometryChangedVerifications": geometry_changed_count,
        "wireAcknowledgedActions": len(actions),
        "freshCoherentSamples": len(actions),
        "allWireAcknowledged": True,
        "allSamplesFresh": True,
        "allSamplesWallClockFresh": True,
        "allSamplesCoherent": True,
        "allSuccessfulPoseVerificationsChangedGeometry": (
            pose_changed_count == geometry_changed_count
        ),
        "cleanupComplete": cleanup_complete,
        "counterfactualInteractionOutcomePredicted": False,
    }


def target_locked_policy_envelope(
    analysis: Mapping[str, object],
    *,
    coarse_action_budget: int = 1,
    fine_action_budget: int = 1,
) -> dict[str, Any]:
    """Bound the same episodes under lock + coarse/fine policy constraints.

    This is an action-budget envelope, not a simulation of alternate gameplay.
    It makes no claim that an interaction would occur after a retained burst.
    """

    if analysis.get("schema") != "retained_camera_replay_analysis.v1":
        raise RetainedCameraTraceError("unsupported replay analysis schema")
    if coarse_action_budget < 0 or fine_action_budget < 0:
        raise RetainedCameraTraceError("camera action budgets must be non-negative")
    episode_count = _integer(analysis.get("burstCount"), field="burstCount")
    old_actions = _integer(analysis.get("cameraActions"), field="cameraActions")
    per_episode = coarse_action_budget + fine_action_budget
    upper_bound = min(old_actions, episode_count * per_episode)
    minimum_reduction = old_actions - upper_bound
    return {
        "schema": "target_locked_camera_policy_envelope.v1",
        "episodeCount": episode_count,
        "coarseActionBudget": coarse_action_budget,
        "fineActionBudget": fine_action_budget,
        "maxActionsPerLockedEpisode": per_episode,
        "modeledCameraActionUpperBound": upper_bound,
        "minimumCameraActionReduction": minimum_reduction,
        "lockedTargetSwitches": 0,
        "redundantPitchLimitAttempts": 0,
        "unjustifiedDirectionReversals": 0,
        "reversalRequiresFreshOvershootProof": True,
        "counterfactualInteractionOutcomePredicted": False,
        "comparisonOnly": True,
    }
