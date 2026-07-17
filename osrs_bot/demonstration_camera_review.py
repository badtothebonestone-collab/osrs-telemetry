from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


CAMERA_REVIEW_FINE_HOLD_MAX_MILLIS = 220
CAMERA_REVIEW_COARSE_HOLD_MIN_MILLIS = 300
MAX_CAMERA_REVIEW_HOLDS = 16
MAX_CAMERA_REVIEW_CHORDS = 16

_YAW_KEY_CONTROLS = frozenset({"A", "D", "LEFT", "RIGHT"})
_PITCH_KEY_CONTROLS = frozenset({"W", "S", "UP", "DOWN"})


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = int(value)
    return numeric if float(value) == float(numeric) else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    return _mapping(event.get("payload"))


def _event_time_millis(event: Mapping[str, object]) -> int | None:
    payload = _payload(event)
    pointer = _mapping(payload.get("pointer"))
    monotonic = _integer(
        payload.get("monotonicTimeNanos", pointer.get("monotonicTimeNanos"))
    )
    if monotonic is not None:
        return monotonic // 1_000_000
    return _integer(payload.get("wallTimeMillis", pointer.get("wallTimeMillis")))


def _control_axis(payload: Mapping[str, object]) -> str:
    input_kind = _text(payload.get("inputKind"))
    control = _text(payload.get("control")).upper()
    if input_kind == "key":
        if control in _YAW_KEY_CONTROLS:
            return "yaw"
        if control in _PITCH_KEY_CONTROLS:
            return "pitch"
        return "unknown"
    if input_kind != "middle_drag":
        return "unknown"
    delta_x = _integer(payload.get("totalDeltaX", payload.get("deltaX"))) or 0
    delta_y = _integer(payload.get("totalDeltaY", payload.get("deltaY"))) or 0
    if delta_x and delta_y:
        return "yaw_pitch"
    if delta_x:
        return "yaw"
    if delta_y:
        return "pitch"
    return "unknown"


def _association_status(episode: Mapping[str, object]) -> str:
    classification = _text(
        episode.get("intentClassification", episode.get("classification"))
    )
    if classification in {"action_linked", "action_linked_candidate"}:
        return classification
    if (
        classification == "exploratory_or_unassociated"
        or _integer(episode.get("clickEventSequence")) is None
    ):
        return "exploratory_or_unassociated"
    if classification in {"cancelled_or_ineffective", "ambiguous"}:
        return classification
    return "ambiguous_or_ineffective"


def _hold_classification(duration_millis: int, *, cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    if duration_millis >= CAMERA_REVIEW_COARSE_HOLD_MIN_MILLIS:
        return "coarse"
    if duration_millis <= CAMERA_REVIEW_FINE_HOLD_MAX_MILLIS:
        return "fine"
    return "intermediate"


def _control_intervals(
    events: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], int, int]:
    ordered = sorted(
        (
            (timestamp, _integer(event.get("recorderSequence")), event)
            for event in events
            if (timestamp := _event_time_millis(event)) is not None
        ),
        key=lambda value: (value[0], value[1] if value[1] is not None else -1),
    )
    open_controls: dict[tuple[str, str], tuple[int, int | None]] = {}
    intervals: list[dict[str, object]] = []
    for timestamp, sequence, event in ordered:
        payload = _payload(event)
        input_kind = _text(payload.get("inputKind"))
        control = _text(payload.get("control")).upper()
        phase = _text(payload.get("phase"))
        token = (input_kind, control)
        if phase == "press":
            open_controls[token] = (timestamp, sequence)
            continue
        if phase not in {"release", "cancel"}:
            continue
        authoritative_duration = _integer(payload.get("holdDurationMillis"))
        if authoritative_duration is not None and authoritative_duration < 0:
            authoritative_duration = None
        opened = open_controls.pop(token, None)
        if opened is not None:
            start_time, press_sequence = opened
        elif authoritative_duration is not None:
            start_time = max(0, timestamp - authoritative_duration)
            press_sequence = None
        else:
            continue
        observed_duration = max(0, timestamp - start_time)
        duration = (
            authoritative_duration
            if authoritative_duration is not None
            else observed_duration
        )
        cancelled = phase == "cancel"
        intervals.append(
            {
                "inputKind": input_kind or "unsupported",
                "control": control or None,
                "axis": _control_axis(payload),
                "startTimeMillis": start_time,
                "endTimeMillis": timestamp,
                "durationMillis": duration,
                "durationSource": (
                    "terminal_hold_duration"
                    if authoritative_duration is not None
                    else "observed_transition_interval"
                ),
                "pressEventSequence": press_sequence,
                "terminalEventSequence": sequence,
                "terminalPhase": phase,
                "cancelled": cancelled,
                "classification": _hold_classification(
                    duration,
                    cancelled=cancelled,
                ),
            }
        )
    intervals.sort(
        key=lambda value: (
            int(value["startTimeMillis"]),
            _integer(value.get("terminalEventSequence")) or -1,
        )
    )
    return intervals[:MAX_CAMERA_REVIEW_HOLDS], len(intervals), len(open_controls)


def _relative_hold(
    interval: Mapping[str, object],
    *,
    episode_start: int,
) -> dict[str, object]:
    return {
        key: value
        for key, value in interval.items()
        if key not in {"startTimeMillis", "endTimeMillis"}
    } | {
        "startOffsetMillis": max(
            0,
            int(interval["startTimeMillis"]) - episode_start,
        ),
        "endOffsetMillis": max(
            0,
            int(interval["endTimeMillis"]) - episode_start,
        ),
    }


def _yaw_pitch_chords(
    intervals: Sequence[Mapping[str, object]],
    *,
    episode_start: int,
) -> list[dict[str, object]]:
    yaw = [
        interval
        for interval in intervals
        if interval.get("inputKind") == "key"
        and interval.get("axis") == "yaw"
        and interval.get("cancelled") is False
    ]
    pitch = [
        interval
        for interval in intervals
        if interval.get("inputKind") == "key"
        and interval.get("axis") == "pitch"
        and interval.get("cancelled") is False
    ]
    chords: list[dict[str, object]] = []
    for yaw_hold in yaw:
        for pitch_hold in pitch:
            start = max(
                int(yaw_hold["startTimeMillis"]),
                int(pitch_hold["startTimeMillis"]),
            )
            end = min(
                int(yaw_hold["endTimeMillis"]),
                int(pitch_hold["endTimeMillis"]),
            )
            if end <= start:
                continue
            chords.append(
                {
                    "startOffsetMillis": max(0, start - episode_start),
                    "endOffsetMillis": max(0, end - episode_start),
                    "durationMillis": end - start,
                    "yawControl": yaw_hold.get("control"),
                    "pitchControl": pitch_hold.get("control"),
                    "yawTerminalEventSequence": yaw_hold.get(
                        "terminalEventSequence"
                    ),
                    "pitchTerminalEventSequence": pitch_hold.get(
                        "terminalEventSequence"
                    ),
                    "classification": "overlapping_yaw_pitch_key_chord",
                }
            )
    chords.sort(
        key=lambda value: (
            int(value["startOffsetMillis"]),
            int(value["endOffsetMillis"]),
        )
    )
    return chords[:MAX_CAMERA_REVIEW_CHORDS]


def _pattern_classification(
    coarse: Sequence[Mapping[str, object]],
    fine: Sequence[Mapping[str, object]],
    intermediate: Sequence[Mapping[str, object]],
) -> str:
    if coarse and fine:
        first_coarse = min(int(value["startOffsetMillis"]) for value in coarse)
        first_fine = min(int(value["startOffsetMillis"]) for value in fine)
        return "coarse_then_fine" if first_coarse <= first_fine else "mixed_order"
    if coarse:
        return "coarse_only"
    if fine:
        return "fine_only"
    if intermediate:
        return "intermediate_only"
    return "no_completed_holds"


def camera_control_pattern_review(
    events: Sequence[Mapping[str, object]],
    episode: Mapping[str, object],
) -> dict[str, object]:
    """Classify bounded manual camera controls without granting input authority."""

    intervals, observed_count, incomplete_count = _control_intervals(events)
    episode_start = min(
        (int(value["startTimeMillis"]) for value in intervals),
        default=0,
    )
    relative = [
        _relative_hold(interval, episode_start=episode_start)
        for interval in intervals
    ]
    coarse = [value for value in relative if value["classification"] == "coarse"]
    fine = [value for value in relative if value["classification"] == "fine"]
    intermediate = [
        value for value in relative if value["classification"] == "intermediate"
    ]
    cancelled = [
        value for value in relative if value["classification"] == "cancelled"
    ]
    chords = _yaw_pitch_chords(intervals, episode_start=episode_start)
    pose_delta = _mapping(episode.get("cameraPoseDelta"))
    observed_pose_axes = [
        axis
        for axis in ("yaw", "pitch")
        if isinstance(pose_delta.get(axis), (int, float))
        and not isinstance(pose_delta.get(axis), bool)
        and float(pose_delta[axis]) != 0.0
    ]
    association = _association_status(episode)
    return {
        "schema": "camera_control_pattern_review.v1",
        "reviewOnly": True,
        "automaticConfigurationAllowed": False,
        "thresholds": {
            "fineHoldMaximumMillis": CAMERA_REVIEW_FINE_HOLD_MAX_MILLIS,
            "coarseHoldMinimumMillis": CAMERA_REVIEW_COARSE_HOLD_MIN_MILLIS,
        },
        "patternClassification": _pattern_classification(
            coarse,
            fine,
            intermediate,
        ),
        "associationStatus": association,
        "exploratoryOrUnassociated": (
            association == "exploratory_or_unassociated"
        ),
        "observedPoseAxes": observed_pose_axes,
        "coarseHoldCount": len(coarse),
        "fineHoldCount": len(fine),
        "intermediateHoldCount": len(intermediate),
        "cancelledHoldCount": len(cancelled),
        "incompleteControlCount": incomplete_count,
        "observedCompletedHoldCount": observed_count,
        "retainedCompletedHoldCount": len(relative),
        "holdsTruncated": observed_count > len(relative),
        "coarseHolds": coarse,
        "fineHolds": fine,
        "intermediateHolds": intermediate,
        "cancelledHolds": cancelled,
        "yawPitchChordCount": len(chords),
        "yawPitchChordIntervals": chords,
        "chordDefinition": (
            "overlap between distinct whitelisted keyboard yaw and pitch holds"
        ),
        "inputIdentityPolicy": (
            "uses recorded whitelisted inputKind/control only; does not infer wheel identity"
        ),
    }


def enrich_camera_review_episodes(
    events: Sequence[Mapping[str, object]],
    episodes: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    """Add ephemeral v1 control-pattern facts to already-derived episodes."""

    events_by_sequence = {
        sequence: event
        for event in events
        if event.get("kind") == "camera_input"
        and (sequence := _integer(event.get("recorderSequence"))) is not None
    }
    enriched: list[dict[str, Any]] = []
    for episode in episodes:
        sequences = episode.get("cameraInputEventSequences")
        selected = [
            events_by_sequence[sequence]
            for value in sequences
            if (sequence := _integer(value)) in events_by_sequence
        ] if isinstance(sequences, Sequence) and not isinstance(
            sequences, (str, bytes, bytearray)
        ) else []
        row = dict(episode)
        row["cameraControlPattern"] = camera_control_pattern_review(
            selected,
            episode,
        )
        enriched.append(row)
    return enriched
