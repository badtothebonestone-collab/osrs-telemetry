from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

from .demonstration_camera_review import enrich_camera_review_episodes
from .model import CAMERA_YAW_UNITS, Observation, ScreenBounds, ScreenPoint
from .observation import (
    DemonstrationEvidenceSnapshot,
    ObservationClient,
    RESPONSE_SCHEMA,
    SENSOR_FRAME_SCHEMA,
)
from .screen_capture import (
    CaptureMetadata,
    bounded_region_around,
    capture_canvas_region,
)


EVENT_SCHEMA = "osrs_demo_event.v1"
MANIFEST_SCHEMA = "osrs_demo_manifest.v1"
SUMMARY_SCHEMA = "osrs_demo_summary.v1"
HASH_SCHEMA = "osrs_demo_hashes.v1"
MAX_DURATION_SECONDS = 600.0
MAX_EVENTS = 50_000
MAX_RECORDING_EVENTS = MAX_EVENTS - 64
MAX_SCREENSHOTS = 32
WORLD_MODEL_GAP_GRACE_SECONDS = 5.0
MAX_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_FILES = 128
MAX_TOTAL_ARTIFACT_BYTES = 256 * 1024 * 1024
EVENT_FINALIZATION_RESERVE_BYTES = 256 * 1024
POINTER_INTERVAL_MILLIS = 50
CAMERA_INTENT_LOOKBACK_MILLIS = 2_500
EXACT_OBJECT_CAMERA_INTENT_LOOKBACK_MILLIS = 4_000
CAMERA_INPUT_JOIN_MILLIS = 400
CAMERA_INPUT_MAX_SPAN_MILLIS = 30_000
CONTEXT_MENU_TIMING_LOOKBACK_MILLIS = 5_000
CONTEXT_MENU_TIMING_JOIN_MILLIS = 250
MANUAL_WALK_QUICK_FOLLOWUP_MILLIS = 1_500
MAX_DERIVED_CAMERA_EPISODES = 256
MAX_DERIVED_MANUAL_ROUTE_TARGETS = 256
_CAMERA_INPUT_KINDS = frozenset({"key", "middle_drag"})
_CAMERA_INPUT_PHASES = frozenset(
    {"press", "repeat", "drag", "release", "cancel"}
)
_CAMERA_INPUT_CONTROLS = frozenset(
    {"LEFT", "RIGHT", "UP", "DOWN", "A", "S", "D", "W", "MIDDLE"}
)
_TARGET_ACTIVATION_KINDS = frozenset(
    {"object_geometry", "context_menu_row", "unverified"}
)
_TRANSIENT_WORLD_MODEL_CAPABILITIES = frozenset(
    {"scene_object_census", "actor_census", "collision_window"}
)
_TRANSIENT_WORLD_MODEL_WARNINGS = frozenset({"world_model_provenance_mismatch"})
_TRANSIENT_INTERACTION_CAPABILITIES = frozenset({"interaction_hot"})
_TRANSIENT_INTERACTION_WARNINGS = frozenset(
    {"menu_evidence_provenance_mismatch_or_stale"}
)
_TRANSIENT_CAPABILITIES = (
    _TRANSIENT_WORLD_MODEL_CAPABILITIES | _TRANSIENT_INTERACTION_CAPABILITIES
)
_TRANSIENT_WARNINGS = (
    _TRANSIENT_WORLD_MODEL_WARNINGS | _TRANSIENT_INTERACTION_WARNINGS
)
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_TAG = re.compile(r"<[^>]*>")
_UNSET = object()


class DemonstrationError(RuntimeError):
    pass


class DemonstrationLimitReached(DemonstrationError):
    pass


def _transient_handoff_kinds(
    evidence: DemonstrationEvidenceSnapshot,
) -> frozenset[str]:
    """Validate a bounded dynamic-payload provenance handoff.

    The endpoint can atomically refresh the world-model payload family, the
    interaction-hot capability, or both at once while its baseline identity
    remains fresh, coherent, logged in, and playable.  Only those complete,
    known capability families are accepted here; unrelated or partial losses
    remain terminal.
    """
    observation = evidence.observation
    raw = evidence.payload()
    payloads = raw.get("payloads")
    raw_missing = raw.get("missingCapabilities")
    raw_warnings = raw.get("warnings")
    if (
        not isinstance(raw_missing, list)
        or any(not isinstance(value, str) for value in raw_missing)
        or not isinstance(raw_warnings, list)
        or any(not isinstance(value, str) for value in raw_warnings)
    ):
        return frozenset()
    missing = frozenset(observation.missing_capabilities)
    warnings = frozenset(observation.warnings)
    if (
        observation.status != "WARN"
        or observation.game_state != "LOGGED_IN"
        or observation.location is None
        or observation.tick < 0
        or not observation.fresh
        or not observation.cache_wall_clock_fresh
        or not observation.source_coherent
        or not observation.scene_playable
        or not observation.timestamp_not_future
        or raw.get("status") != "WARN"
        or frozenset(raw_missing) != missing
        or frozenset(raw_warnings) != warnings
        or len(raw_missing) != len(missing)
        or len(raw_warnings) != len(warnings)
        or not isinstance(payloads, Mapping)
        or not missing
        or not missing.issubset(_TRANSIENT_CAPABILITIES)
        or not warnings.issubset(_TRANSIENT_WARNINGS)
    ):
        return frozenset()

    kinds: set[str] = set()
    expected_missing: set[str] = set()
    expected_warnings: set[str] = set()
    if missing.intersection(_TRANSIENT_WORLD_MODEL_CAPABILITIES):
        kinds.add("world_model")
        expected_missing.update(_TRANSIENT_WORLD_MODEL_CAPABILITIES)
        expected_warnings.update(_TRANSIENT_WORLD_MODEL_WARNINGS)
        if _TRANSIENT_WORLD_MODEL_CAPABILITIES.intersection(payloads):
            return frozenset()
    if missing.intersection(_TRANSIENT_INTERACTION_CAPABILITIES):
        kinds.add("interaction_hot")
        expected_missing.update(_TRANSIENT_INTERACTION_CAPABILITIES)
        expected_warnings.update(_TRANSIENT_INTERACTION_WARNINGS)
        interaction_hot = payloads.get("interaction_hot")
        root_interaction_hot = raw.get("clientTickHot")
        if (
            not isinstance(interaction_hot, Mapping)
            or not isinstance(root_interaction_hot, Mapping)
            or not isinstance(payloads.get("client_tick_tail"), Mapping)
            or interaction_hot.get("schema") != "client_tick_hot.v1"
            or interaction_hot.get("sessionId") != observation.session_id
            or _integer_or_none(interaction_hot.get("clientProcessId"))
            != observation.client_process_id
            or dict(interaction_hot) != dict(root_interaction_hot)
        ):
            return frozenset()
    if missing != frozenset(expected_missing) or warnings != frozenset(
        expected_warnings
    ):
        return frozenset()
    return frozenset(kinds)


def _is_transient_world_model_unavailable(
    evidence: DemonstrationEvidenceSnapshot,
) -> bool:
    """Recognize only the live-proven additive world-model handoff gap."""
    return _transient_handoff_kinds(evidence) == frozenset({"world_model"})


def _is_transient_interaction_hot_unavailable(
    evidence: DemonstrationEvidenceSnapshot,
) -> bool:
    """Allow a brief menu-provenance handoff while retaining the bound hot tail."""
    return _transient_handoff_kinds(evidence) == frozenset({"interaction_hot"})


def _transient_gap_code(
    evidence: DemonstrationEvidenceSnapshot,
) -> str | None:
    kinds = _transient_handoff_kinds(evidence)
    if kinds == frozenset({"world_model"}):
        return "demonstration_world_model_provenance_unavailable"
    if kinds == frozenset({"interaction_hot"}):
        return "demonstration_interaction_hot_provenance_unavailable"
    if kinds == frozenset({"world_model", "interaction_hot"}):
        return "demonstration_dynamic_provenance_handoff"
    return None


@dataclass(frozen=True, slots=True)
class InspectionResult:
    valid: bool
    status: str
    semantic_summary: tuple[str, ...] = ()
    route_points: tuple[dict[str, int], ...] = ()
    interacted_entities: tuple[dict[str, Any], ...] = ()
    selected_menu_options: tuple[str, ...] = ()
    state_changes: tuple[dict[str, Any], ...] = ()
    ambiguities: tuple[str, ...] = ()
    coverage_gaps: tuple[str, ...] = ()
    candidate_suggestions: tuple[dict[str, Any], ...] = ()
    camera_intent_episodes: tuple[dict[str, Any], ...] = ()
    camera_review_episodes: tuple[dict[str, Any], ...] = ()
    timing_profiles: tuple[dict[str, Any], ...] = ()
    timing_review_profiles: tuple[dict[str, Any], ...] = ()
    manual_route_targets: tuple[dict[str, Any], ...] = ()
    manual_route_review_targets: tuple[dict[str, Any], ...] = ()
    errors: tuple[str, ...] = ()
    intent_evidence_included: bool = False
    manual_route_evidence_included: bool = False
    stop_reason: str | None = None
    requested_duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": SUMMARY_SCHEMA,
            "valid": self.valid,
            "status": self.status,
            "semanticSummary": list(self.semantic_summary),
            "routePoints": list(self.route_points),
            "interactedEntities": list(self.interacted_entities),
            "selectedMenuOptions": list(self.selected_menu_options),
            "stateChanges": list(self.state_changes),
            "ambiguities": list(self.ambiguities),
            "coverageGaps": list(self.coverage_gaps),
            "candidateSuggestions": list(self.candidate_suggestions),
            "errors": list(self.errors),
        }
        # Keep finalized v1 artifacts byte-for-byte derivable. New recordings
        # opt into these additive review-only fields through their manifest.
        if (
            self.intent_evidence_included
            or self.camera_intent_episodes
            or self.timing_profiles
        ):
            payload["cameraIntentEpisodes"] = list(self.camera_intent_episodes)
            payload["timingProfiles"] = list(self.timing_profiles)
        if self.manual_route_evidence_included:
            payload["manualRouteTargets"] = list(self.manual_route_targets)
        return payload


class DemonstrationRecorder:
    """Append-only read-only evidence recorder; it owns no input surface."""

    def __init__(
        self,
        name: str,
        *,
        output_root: Path,
        annotations: Iterable[str] = (),
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
        capture: Callable[..., tuple[Any, CaptureMetadata]] = capture_canvas_region,
        screenshots_enabled: bool = True,
        requested_duration_seconds: float | None = None,
    ) -> None:
        self.name = _safe_name(name)
        self.output_root = Path(output_root)
        cleaned_annotations: list[str] = []
        for value in annotations:
            if not isinstance(value, str) or not value.strip():
                continue
            if len(cleaned_annotations) >= 32:
                raise DemonstrationError("at most 32 annotations are allowed")
            cleaned_annotations.append(_clean_text(value))
        self.annotations = tuple(cleaned_annotations)
        self._now = now
        if not callable(monotonic):
            raise DemonstrationError("monotonic must be callable")
        self._monotonic = monotonic
        self._capture = capture
        self._screenshots_enabled = bool(screenshots_enabled)
        if requested_duration_seconds is not None and (
            isinstance(requested_duration_seconds, bool)
            or not isinstance(requested_duration_seconds, (int, float))
            or not math.isfinite(float(requested_duration_seconds))
            or not 0 < float(requested_duration_seconds) <= MAX_DURATION_SECONDS
        ):
            raise DemonstrationError(
                "requested_duration_seconds must be greater than 0 and at most "
                f"{MAX_DURATION_SECONDS:g}"
            )
        self._requested_duration_seconds = (
            None
            if requested_duration_seconds is None
            else float(requested_duration_seconds)
        )
        self._artifact_path: Path | None = None
        self._events_handle: Any | None = None
        self._event_sequence = 0
        self._event_bytes = 0
        self._seen_hot_sequences: set[int] = set()
        self._start_hot_sequence = 0
        self._last_hot_watermark = 0
        self._last_pointer_wall_time: int | None = None
        self._reported_missing_pointer_time = False
        self._drop_counts = (0, 0, 0, 0)
        self._last_camera_pose_key: str | None = None
        self._session_id: str | None = None
        self._process_id: int | None = None
        self._first_tick: int | None = None
        self._last_tick: int | None = None
        self._last_seen_tick: int | None = None
        self._consecutive_world_model_gaps = 0
        self._world_model_gap_started_at: float | None = None
        self._last_observation: dict[str, Any] | None = None
        self._last_observation_event_sequence: int | None = None
        self._pending_clicks: list[dict[str, Any]] = []
        self._screenshots: list[str] = []
        self._manifest: dict[str, Any] = {}
        self._started = False
        self._finalized = False

    @property
    def artifact_path(self) -> Path:
        if self._artifact_path is None:
            raise DemonstrationError("recording has not started")
        return self._artifact_path

    def start(self, evidence: DemonstrationEvidenceSnapshot) -> None:
        if self._started:
            raise DemonstrationError("recording already started")
        observation = evidence.observation
        _require_loaded_identity(observation)
        _validate_evidence_binding(evidence)
        self._session_id = observation.session_id
        self._process_id = observation.client_process_id
        self._first_tick = observation.tick
        self._last_tick = observation.tick
        self._last_seen_tick = observation.tick
        self._artifact_path = _new_artifact_path(
            self.output_root, self.name, self._now()
        )
        self._artifact_path.mkdir(parents=True, exist_ok=False)
        (self._artifact_path / "screenshots").mkdir()
        self._events_handle = (self._artifact_path / "events.jsonl").open(
            "x", encoding="utf-8", newline="\n"
        )
        payload = evidence.payload()
        hot = _hot_payload(payload)
        initial_sequences = _hot_sequences(hot)
        self._seen_hot_sequences.update(initial_sequences)
        watermark = _integer_or_none(hot.get("latestEventSequence")) or 0
        self._start_hot_sequence = watermark
        self._last_hot_watermark = watermark
        self._drop_counts = _drop_counts(hot)
        self._manifest = _manifest_base(
            self.name,
            evidence,
            created_at=self._now(),
            annotations=self.annotations,
        )
        if self._requested_duration_seconds is not None:
            self._manifest["requestedDurationSeconds"] = (
                self._requested_duration_seconds
            )
        self._started = True
        start_payload = {
            "name": self.name,
            "readOnly": True,
            "rawReplayAllowed": False,
            "reviewRequired": True,
        }
        if self._requested_duration_seconds is not None:
            start_payload["requestedDurationSeconds"] = (
                self._requested_duration_seconds
            )
        self._append(
            "recording_started",
            _source(observation),
            start_payload,
        )
        for annotation in self.annotations:
            self._append("annotation", _source(observation), {"text": annotation})
        self._record_observation(evidence)
        self._capture_evidence("recording_start", observation, None)

    def add(self, evidence: DemonstrationEvidenceSnapshot) -> bool:
        if not self._started or self._finalized:
            raise DemonstrationError("recording is not active")
        observation = evidence.observation
        if (
            observation.session_id != self._session_id
            or observation.client_process_id != self._process_id
        ):
            self._append(
                "coverage_gap",
                _source(observation),
                {
                    "code": "session_or_process_changed",
                    "recordingStopped": True,
                },
            )
            return False
        if not observation.loaded_scene:
            transient_gap_code = _transient_gap_code(evidence)
            if transient_gap_code is not None:
                if (
                    self._last_seen_tick is not None
                    and observation.tick < self._last_seen_tick
                ):
                    self._append(
                        "coverage_gap",
                        _source(observation),
                        {"code": "source_tick_regressed", "recordingStopped": True},
                    )
                    return False
                _, watermark = _validate_evidence_envelope_and_hot(evidence)
                if watermark < self._last_hot_watermark:
                    self._append(
                        "coverage_gap",
                        _source(observation),
                        {"code": "hot_event_sequence_reset", "recordingStopped": True},
                    )
                    return False
                self._last_seen_tick = observation.tick
                self._record_hot_events(
                    evidence.payload(),
                    observation,
                    previous_watermark=self._last_hot_watermark,
                )
                self._last_hot_watermark = watermark
                self._consecutive_world_model_gaps += 1
                gap_now = self._monotonic()
                if self._world_model_gap_started_at is None:
                    self._world_model_gap_started_at = gap_now
                gap_elapsed = gap_now - self._world_model_gap_started_at
                self._append(
                    "coverage_gap",
                    _source(observation),
                    {
                        "code": transient_gap_code,
                        "missingCapabilities": list(observation.missing_capabilities),
                        "warnings": list(observation.warnings),
                        "consecutivePolls": self._consecutive_world_model_gaps,
                        "elapsedMillis": (
                            max(0, round(gap_elapsed * 1000))
                            if math.isfinite(gap_elapsed) and gap_elapsed >= 0
                            else None
                        ),
                        "recordingStopped": False,
                    },
                )
                return True
            self._append(
                "coverage_gap",
                _source(observation),
                {
                    "code": "loaded_scene_lost",
                    "recordingStopped": True,
                    "status": observation.status,
                    "gameState": observation.game_state,
                    "fresh": observation.fresh,
                    "cacheWallClockFresh": observation.cache_wall_clock_fresh,
                    "sourceCoherent": observation.source_coherent,
                    "scenePlayable": observation.scene_playable,
                    "timestampNotFuture": observation.timestamp_not_future,
                    "missingCapabilities": list(observation.missing_capabilities),
                    "warnings": list(observation.warnings),
                    "payloadKeys": sorted(
                        str(key)
                        for key in (
                            evidence.payload().get("payloads", {}).keys()
                            if isinstance(
                                evidence.payload().get("payloads"), Mapping
                            )
                            else ()
                        )
                    )[:64],
                },
            )
            return False
        if (
            self._last_seen_tick is not None
            and observation.tick < self._last_seen_tick
        ):
            self._append(
                "coverage_gap",
                _source(observation),
                {"code": "source_tick_regressed", "recordingStopped": True},
            )
            return False
        watermark = _validate_evidence_binding(evidence)

        payload = evidence.payload()
        if watermark < self._last_hot_watermark:
            self._append(
                "coverage_gap",
                _source(observation),
                {"code": "hot_event_sequence_reset", "recordingStopped": True},
            )
            return False
        self._last_seen_tick = observation.tick
        self._consecutive_world_model_gaps = 0
        self._world_model_gap_started_at = None
        self._record_hot_events(
            payload,
            observation,
            previous_watermark=self._last_hot_watermark,
        )
        self._last_hot_watermark = watermark
        if observation.tick != self._last_tick:
            self._record_observation(evidence)
        self._last_tick = observation.tick
        return True

    def finish(self, reason: str = "operator_or_duration_stop") -> Path:
        if not self._started:
            raise DemonstrationError("recording has not started")
        if self._finalized:
            return self.artifact_path
        source = {
            "sessionId": self._session_id,
            "pid": self._process_id,
            "sourceTick": self._last_tick,
            "clientTick": None,
            "plane": (
                self._last_observation.get("player", {}).get("plane")
                if self._last_observation
                else None
            ),
            "eventSequence": None,
        }
        if self._pending_clicks:
            self._append(
                "coverage_gap",
                source,
                {
                    "code": "missing_after_observation",
                    "clickEventSequences": [
                        pending["eventSequence"]
                        for pending in self._pending_clicks[:64]
                    ],
                    "clickCount": len(self._pending_clicks),
                },
                terminal=True,
            )
        self._pending_clicks.clear()
        self._append(
            "recording_stopped",
            source,
            {"reason": _clean_text(reason)},
            terminal=True,
        )
        assert self._events_handle is not None
        self._events_handle.close()
        self._events_handle = None
        self._manifest.update(
            finalizedAtUtc=self._now().astimezone(timezone.utc).isoformat(),
            firstSourceTick=self._first_tick,
            lastSourceTick=self._last_tick,
            eventCount=self._event_sequence,
            screenshotCount=len(self._screenshots),
            stopReason=_clean_text(reason),
        )
        _write_json(self.artifact_path / "manifest.json", self._manifest)
        events = _read_events_unverified(self.artifact_path / "events.jsonl")
        derived = _derive_summary(events, self._manifest)
        _write_json(self.artifact_path / "summary.json", derived.to_dict())
        (self.artifact_path / "timeline.md").write_text(
            _timeline_markdown(self._manifest, events, derived),
            encoding="utf-8",
        )
        _write_hashes(self.artifact_path, self._now())
        self._finalized = True
        return self.artifact_path

    def _append(
        self,
        kind: str,
        source: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        terminal: bool = False,
    ) -> int:
        event_limit = MAX_EVENTS if terminal else MAX_RECORDING_EVENTS
        if self._event_sequence >= event_limit:
            raise DemonstrationLimitReached("demonstration event limit reached")
        if self._events_handle is None:
            raise DemonstrationError("event stream is not open")
        next_sequence = self._event_sequence + 1
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        event = {
            "schema": EVENT_SCHEMA,
            "recorderSequence": next_sequence,
            "kind": kind,
            "recordedAtUtc": now.astimezone(timezone.utc).isoformat(),
            "recordedAtLocal": now.astimezone().isoformat(),
            "source": dict(source),
            "payload": dict(payload),
        }
        encoded = (
            json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        )
        encoded_bytes = len(encoded.encode("utf-8"))
        byte_limit = (
            MAX_ARTIFACT_FILE_BYTES
            if terminal
            else MAX_ARTIFACT_FILE_BYTES - EVENT_FINALIZATION_RESERVE_BYTES
        )
        if self._event_bytes + encoded_bytes > byte_limit:
            raise DemonstrationLimitReached("demonstration event byte limit reached")
        self._events_handle.write(encoded)
        self._events_handle.flush()
        self._event_sequence = next_sequence
        self._event_bytes += encoded_bytes
        return self._event_sequence

    def _record_observation(self, evidence: DemonstrationEvidenceSnapshot) -> None:
        observation = evidence.observation
        payload = _observation_payload(evidence)
        event_sequence = self._append(
            "observation",
            _source(
                observation,
                client_tick=_integer_or_none(payload.get("clientTick")),
            ),
            payload,
        )
        had_previous = self._last_observation is not None
        self._last_observation = payload
        self._last_observation_event_sequence = event_sequence
        self._first_tick = (
            observation.tick if self._first_tick is None else self._first_tick
        )
        self._last_tick = observation.tick
        if had_previous:
            eligible = [
                pending
                for pending in self._pending_clicks
                if observation.tick > pending["beforeTick"]
            ]
            self._pending_clicks = [
                pending
                for pending in self._pending_clicks
                if observation.tick <= pending["beforeTick"]
            ]
            if len(eligible) == 1:
                pending = eligible[0]
                self._append(
                    "interaction_outcome",
                    _source(observation),
                    {
                        "clickEventSequence": pending["eventSequence"],
                        "beforeObservationSequence": pending[
                            "beforeObservationSequence"
                        ],
                        "afterObservationSequence": event_sequence,
                        "beforeTick": pending["beforeTick"],
                        "afterTick": observation.tick,
                        "changes": _state_changes(pending["before"], payload),
                        "attributionAmbiguous": False,
                    },
                )
            elif len(eligible) > 1:
                click_sequences = [
                    pending["eventSequence"] for pending in eligible
                ]
                self._append(
                    "coverage_gap",
                    _source(observation),
                    {
                        "code": "multiple_clicks_before_after_observation",
                        "clickEventSequences": click_sequences,
                    },
                )
                first = eligible[0]
                self._append(
                    "interaction_outcome",
                    _source(observation),
                    {
                        "clickEventSequence": None,
                        "clickEventSequences": click_sequences,
                        "beforeObservationSequences": [
                            pending["beforeObservationSequence"]
                            for pending in eligible
                        ],
                        "afterObservationSequence": event_sequence,
                        "beforeTick": min(
                            pending["beforeTick"] for pending in eligible
                        ),
                        "afterTick": observation.tick,
                        "changes": _state_changes(first["before"], payload),
                        "attributionAmbiguous": True,
                    },
                )

    def _record_hot_events(
        self,
        raw_payload: Mapping[str, Any],
        observation: Observation,
        *,
        previous_watermark: int,
    ) -> None:
        hot = _hot_payload(raw_payload)
        current_drops = _drop_counts(hot)
        self._drop_counts = current_drops

        merged: list[tuple[int, str, Mapping[str, Any]]] = []
        for lane, key in (
            ("client_tick", "clientTickTail"),
            ("post_menu_sort", "postMenuSortTail"),
            ("clicked", "clickedTail"),
            ("camera_input", "cameraInputTail"),
        ):
            values = hot.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                sequence = _integer_or_none(value.get("eventSequence"))
                if sequence is None:
                    self._append(
                        "coverage_gap",
                        _source(observation),
                        {"code": "hot_event_sequence_missing", "lane": lane},
                    )
                    continue
                merged.append((sequence, lane, value))

        watermark = _integer_or_none(hot.get("latestEventSequence"))
        visible_sequences = {sequence for sequence, _, _ in merged}
        if watermark is not None and watermark > previous_watermark:
            visible_new = sorted(
                sequence
                for sequence in visible_sequences
                if previous_watermark < sequence <= watermark
            )
            missing_count = watermark - previous_watermark - len(visible_new)
            if missing_count > 0:
                sample_missing: list[int] = []
                first_missing: int | None = None
                last_missing: int | None = None
                cursor = previous_watermark + 1
                for boundary in (*visible_new, watermark + 1):
                    if boundary > cursor:
                        gap_end = boundary - 1
                        first_missing = cursor if first_missing is None else first_missing
                        last_missing = gap_end
                        if len(sample_missing) < 64:
                            sample_missing.extend(
                                range(cursor, min(gap_end + 1, cursor + 64 - len(sample_missing)))
                            )
                    cursor = boundary + 1
                self._append(
                    "coverage_gap",
                    _source(observation),
                    {
                        "code": "hot_event_sequence_gap",
                        "firstMissingEventSequence": first_missing,
                        "lastMissingEventSequence": last_missing,
                        "missingEventCount": missing_count,
                        "sampleEventSequences": sample_missing,
                        "ringEvictionCounters": {
                            lane: count
                            for lane, count in zip(
                                (
                                    "client_tick",
                                    "post_menu_sort",
                                    "clicked",
                                    "camera_input",
                                ),
                                current_drops,
                                strict=True,
                            )
                        },
                    },
                )

        for sequence, lane, sample in sorted(merged, key=lambda item: item[0]):
            if sequence in self._seen_hot_sequences:
                continue
            self._seen_hot_sequences.add(sequence)
            if sequence <= self._start_hot_sequence:
                continue
            sample_source = _source(
                observation,
                client_tick=_integer_or_none(sample.get("clientTick")),
                source_tick=_integer_or_none(sample.get("gameTickAtSample")),
                event_sequence=sequence,
            )
            if lane == "client_tick":
                wall_time = _integer_or_none(sample.get("wallTimeMillis"))
                if wall_time is None:
                    if not self._reported_missing_pointer_time:
                        self._append(
                            "coverage_gap",
                            sample_source,
                            {"code": "pointer_sample_time_missing"},
                        )
                        self._reported_missing_pointer_time = True
                    continue
                camera_pose = _camera_pose_payload(sample.get("cameraPose"))
                camera_pose_key = (
                    json.dumps(camera_pose, sort_keys=True, separators=(",", ":"))
                    if camera_pose is not None
                    else None
                )
                pointer_too_soon = (
                    self._last_pointer_wall_time is not None
                    and wall_time >= self._last_pointer_wall_time
                    and wall_time - self._last_pointer_wall_time
                    < POINTER_INTERVAL_MILLIS
                )
                if pointer_too_soon and camera_pose_key == self._last_camera_pose_key:
                    continue
                self._last_pointer_wall_time = wall_time
                self._last_camera_pose_key = camera_pose_key
                self._append(
                    "pointer_sample",
                    sample_source,
                    _pointer_payload(sample),
                )
            elif lane == "post_menu_sort":
                self._append(
                    "hover_menu",
                    sample_source,
                    _hover_payload(sample),
                )
            elif lane == "clicked":
                click_payload = _clicked_payload(
                    sample,
                    self._last_observation,
                    self._last_tick,
                )
                click_event = self._append(
                    "menu_option_clicked",
                    sample_source,
                    click_payload,
                )
                if self._last_observation is not None:
                    self._pending_clicks.append(
                        {
                            "eventSequence": click_event,
                            "beforeObservationSequence": self._last_observation_event_sequence,
                            "beforeTick": self._last_tick,
                            "before": self._last_observation,
                        }
                    )
                self._capture_evidence(
                    "menu_option_clicked",
                    observation,
                    _sample_point(sample, observation.canvas_bounds),
                    related_event_sequence=click_event,
                )
            else:
                self._append(
                    "camera_input",
                    sample_source,
                    _camera_input_payload(sample),
                )

    def _capture_evidence(
        self,
        reason: str,
        observation: Observation,
        point: ScreenPoint | None,
        *,
        related_event_sequence: int | None = None,
    ) -> None:
        if (
            not self._screenshots_enabled
            or len(self._screenshots) >= MAX_SCREENSHOTS
            or not observation.loaded_scene
            or observation.widgets.bank_pin_open
            or observation.canvas_bounds is None
        ):
            return
        canvas = observation.canvas_bounds
        center = point if point is not None and canvas.contains(point) else canvas.center
        region = bounded_region_around(canvas, center)
        try:
            image, metadata = self._capture(canvas, region)
        except Exception as error:  # screenshots are additive, never control evidence
            self._append(
                "coverage_gap",
                _source(observation),
                {
                    "code": "screenshot_capture_failed",
                    "error": f"{type(error).__name__}: {error}",
                },
            )
            return
        filename = f"{self._event_sequence + 1:08d}_{_safe_name(reason)}.png"
        relative_path = PurePosixPath("screenshots") / filename
        screenshot_path = self.artifact_path / Path(relative_path)
        try:
            image.save(screenshot_path, format="PNG")
        except Exception as error:
            try:
                screenshot_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._append(
                "coverage_gap",
                _source(observation),
                {
                    "code": "screenshot_save_failed",
                    "error": f"{type(error).__name__}: {error}",
                },
            )
            return
        self._screenshots.append(str(relative_path))
        try:
            self._append(
                "screenshot",
                _source(observation),
                {
                    "path": str(relative_path),
                    "reason": reason,
                    "relatedEventSequence": related_event_sequence,
                    "method": metadata.method,
                    "canvasBounds": _bounds_payload(metadata.canvas_bounds),
                    "capturedBounds": _bounds_payload(metadata.captured_bounds),
                    "canvasRelativeBounds": _bounds_payload(metadata.relative_bounds),
                    "temporalRelation": "captured_after_observation_or_event",
                },
            )
        except BaseException:
            self._screenshots.pop()
            try:
                screenshot_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _observation_payload(evidence: DemonstrationEvidenceSnapshot) -> dict[str, Any]:
    observation = evidence.observation
    raw = evidence.payload()
    payloads = raw.get("payloads") if isinstance(raw.get("payloads"), Mapping) else {}
    baseline = payloads.get("baseline") if isinstance(payloads.get("baseline"), Mapping) else {}
    raw_player = baseline.get("player") if isinstance(baseline.get("player"), Mapping) else {}
    raw_camera = (
        baseline.get("cameraViewport")
        if isinstance(baseline.get("cameraViewport"), Mapping)
        else {}
    )
    raw_input_geometry = (
        baseline.get("inputGeometry")
        if isinstance(baseline.get("inputGeometry"), Mapping)
        else {}
    )
    location = observation.location
    player = {
        "world": (
            None
            if location is None
            else {"x": location.x, "y": location.y, "plane": location.plane}
        ),
        "plane": observation.plane,
        "scene": _coordinate_pair(raw_player, "sceneX", "sceneY"),
        "local": _coordinate_pair(raw_player, "localX", "localY"),
    }
    objects = []
    for item in observation.nearby_objects[:64]:
        objects.append(
            {
                "key": item.key,
                "objectId": item.object_id,
                "name": _clean_text(item.name),
                "kind": item.kind,
                "actions": [_clean_text(action) for action in item.actions],
                "world": (
                    None
                    if item.location is None
                    else {
                        "x": item.location.x,
                        "y": item.location.y,
                        "plane": item.location.plane,
                    }
                ),
                "scene": (
                    None
                    if item.scene_x is None or item.scene_y is None
                    else {"x": item.scene_x, "y": item.scene_y}
                ),
                "distance": item.distance,
                "projection": {
                    "available": item.geometry.available,
                    "onScreen": item.geometry.on_screen,
                    "visible": item.geometry.visible,
                    "actionable": item.geometry.actionable,
                    "canvasPoint": _point_payload(item.geometry.canvas_point),
                    "screenPoint": _point_payload(item.geometry.screen_point),
                    "screenBounds": _bounds_payload(item.geometry.screen_bounds),
                    "geometrySource": item.geometry.geometry_source,
                    "screenPolygon": [
                        _point_payload(point) for point in item.geometry.screen_polygon
                    ],
                    "visibleAreaRatio": item.geometry.visible_area_ratio,
                    "edgeDistancePx": item.geometry.edge_distance_px,
                    "geometryFrameId": observation.geometry_frame_id,
                },
            }
        )
    actor_payload = _dynamic_payload(raw, "actor_census")
    collision_payload = _dynamic_payload(raw, "collision_window")
    scene_payload = _dynamic_payload(raw, "scene_object_census")
    actors = _npc_actors(actor_payload)
    collision = _collision_cells(collision_payload)
    hot = _hot_payload(raw)
    return {
        "sourceSchema": raw.get("schema"),
        "sensorFrameSchema": (
            raw.get("sensorFrame", {}).get("schema")
            if isinstance(raw.get("sensorFrame"), Mapping)
            else None
        ),
        "frameId": observation.frame_id,
        "geometryFrameId": observation.geometry_frame_id,
        "clientTick": _client_tick_from_payload(raw),
        "cameraPose": _camera_pose_payload(
            {
                **raw_camera,
                "cameraYaw": raw_camera.get("cameraYaw", observation.camera_yaw),
                "cameraPitch": raw_camera.get(
                    "cameraPitch", observation.camera_pitch
                ),
            }
        ),
        "viewGeometry": {
            "clientWindowBounds": _bounds_payload(observation.client_window_bounds),
            "canvasScreenBounds": _bounds_payload(observation.canvas_bounds),
            "viewportScreenBounds": _bounds_payload(observation.viewport_bounds),
            "inputGeometry": _input_geometry_payload(raw_input_geometry),
        },
        "player": player,
        "inventory": {
            "known": observation.inventory.known,
            "slotCount": observation.inventory.slot_count,
            "occupiedSlots": observation.inventory.occupied_slots,
            "freeSlots": observation.inventory.free_slots,
            "items": [
                {
                    "slot": item.slot,
                    "itemId": item.item_id,
                    "quantity": item.quantity,
                    "name": _clean_text(item.name or "") or None,
                }
                for item in observation.inventory.items
            ],
        },
        "interfaces": {
            "bankKnown": observation.widgets.bank_known,
            "bankOpen": observation.widgets.bank_open,
            "bankReadable": observation.widgets.bank_readable,
            "bankPinOpen": observation.widgets.bank_pin_open,
            "dialogueActive": observation.widgets.dialogue_active,
            "dialogueType": observation.widgets.dialogue_type,
            "dialoguePrompt": _clean_text(observation.widgets.dialogue_prompt),
            "dialogueOptions": [
                {
                    "index": option.index,
                    "key": option.key,
                    "text": _clean_text(option.text),
                    "visible": option.visible,
                }
                for option in observation.widgets.dialogue_options[:16]
            ],
            "depositInventoryWidget": _widget_payload(
                observation.widgets.deposit_inventory,
                observation.geometry_frame_id,
            ),
            "closeBankWidget": _widget_payload(
                observation.widgets.close_bank,
                observation.geometry_frame_id,
            ),
        },
        "nearbyObjects": objects,
        "sceneObjectCensusMeta": {
            "schema": scene_payload.get("schema"),
            "count": _integer_or_none(scene_payload.get("count")),
            "returned": _integer_or_none(scene_payload.get("returned")),
            "capHit": bool(scene_payload.get("capHit", False)),
            "objectCensusCapHit": bool(
                scene_payload.get("objectCensusCapHit", False)
            ),
        },
        "nearbyNpcs": actors,
        "actorCensusMeta": {
            "schema": actor_payload.get("schema"),
            "radiusTiles": _integer_or_none(actor_payload.get("radiusTiles")),
            "count": _integer_or_none(actor_payload.get("count")),
            "returned": _integer_or_none(actor_payload.get("returned")),
            "capHit": bool(actor_payload.get("capHit", False)),
        },
        "collisionCells": collision,
        "collisionWindowMeta": {
            "schema": collision_payload.get("schema"),
            "collisionAvailable": bool(
                collision_payload.get("collisionAvailable", False)
            ),
            "radiusTiles": _integer_or_none(
                collision_payload.get("radiusTiles")
            ),
            "cellCount": _integer_or_none(collision_payload.get("cellCount")),
            "cellCapHit": bool(collision_payload.get("cellCapHit", False)),
            "collisionHash": _clean_text(
                collision_payload.get("collisionHash", "")
            )
            or None,
        },
        "menuEntries": [
            _menu_entry_payload(
                {
                    "option": item.option,
                    "target": item.target,
                    "type": item.entry_type,
                    "identifier": item.identifier,
                    "param0": item.param0,
                    "param1": item.param1,
                }
            )
            for item in observation.menus[:16]
        ],
        "mouse": _pointer_payload(hot.get("mouse", {}) if isinstance(hot.get("mouse"), Mapping) else {}),
        "warnings": list(observation.warnings),
        "missingCapabilities": list(observation.missing_capabilities),
    }


def _hot_payload(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    payloads = raw.get("payloads")
    if isinstance(payloads, Mapping):
        candidate = payloads.get("client_tick_tail")
        if isinstance(candidate, Mapping):
            return candidate
    candidate = raw.get("clientTickHot")
    return candidate if isinstance(candidate, Mapping) else {}


def _dynamic_payload(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    payloads = raw.get("payloads")
    if isinstance(payloads, Mapping) and isinstance(payloads.get(name), Mapping):
        return payloads[name]
    world = raw.get("worldModel")
    if isinstance(world, Mapping):
        world_payloads = world.get("payloads")
        if isinstance(world_payloads, Mapping) and isinstance(
            world_payloads.get(name), Mapping
        ):
            return world_payloads[name]
    return {}


def _npc_actors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("actors", payload.get("npcs", []))
    if not isinstance(values, list):
        return []
    output = []
    for raw in values[:64]:
        if not isinstance(raw, Mapping):
            continue
        actor_type = str(raw.get("type", raw.get("actorType", "NPC"))).upper()
        if actor_type != "NPC":
            continue
        actions = raw.get("actions")
        action_values = actions if isinstance(actions, list) else []
        output.append(
            {
                "type": "NPC",
                "index": _integer_or_none(raw.get("index")),
                "id": _integer_or_none(raw.get("id")),
                "name": _clean_text(raw.get("name")),
                "actions": [
                    _clean_text(value)
                    for value in action_values
                    if isinstance(value, str)
                ][:16],
                "world": _world_from_mapping(raw),
                "scene": _coordinate_pair(raw, "sceneX", "sceneY"),
                "local": _coordinate_pair(raw, "localX", "localY"),
                "distance": _integer_or_none(
                    raw.get("distanceToPlayer", raw.get("distance"))
                ),
            }
        )
    return output


def _collision_cells(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cells", payload.get("tiles", []))
    if not isinstance(values, list):
        return []
    output = []
    for raw in values[:512]:
        if not isinstance(raw, Mapping):
            continue
        output.append(
            {
                "world": _world_from_mapping(raw),
                "scene": _coordinate_pair(raw, "sceneX", "sceneY"),
                "flags": _integer_or_none(raw.get("flags")),
                "blocked": bool(
                    raw.get("blockedMovement", raw.get("blocked", False))
                ),
            }
        )
    return output


def _hot_sequences(hot: Mapping[str, Any]) -> set[int]:
    output: set[int] = set()
    for key in (
        "clientTickTail",
        "postMenuSortTail",
        "clickedTail",
        "cameraInputTail",
    ):
        values = hot.get(key, [])
        if isinstance(values, list):
            for sample in values:
                if isinstance(sample, Mapping):
                    sequence = _integer_or_none(sample.get("eventSequence"))
                    if sequence is not None:
                        output.add(sequence)
    return output


def _drop_counts(hot: Mapping[str, Any]) -> tuple[int, int, int, int]:
    latency = hot.get("latency")
    latency = latency if isinstance(latency, Mapping) else {}
    return tuple(
        max(0, _integer_or_none(latency.get(name)) or 0)
        for name in (
            "droppedClientTickSamples",
            "droppedPostMenuSortSamples",
            "droppedClickedSamples",
            "droppedCameraInputSamples",
        )
    )  # type: ignore[return-value]


def _camera_pose_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    output: dict[str, Any] = {
        "schema": (
            _clean_text(value.get("schema"), maximum=64)
            or "camera_pose.v1"
        )
    }
    for name in (
        "cameraX",
        "cameraY",
        "cameraZ",
        "cameraYaw",
        "cameraPitch",
        "cameraYawTarget",
        "cameraPitchTarget",
        "zoom3d",
        "viewportXOffset",
        "viewportYOffset",
        "viewportWidth",
        "viewportHeight",
        "canvasWidth",
        "canvasHeight",
    ):
        output[name] = _integer_or_none(value.get(name))
    pose_frame_id = _clean_text(value.get("poseFrameId"), maximum=256)
    geometry_frame_id = _clean_text(value.get("geometryFrameId"), maximum=256)
    output["poseFrameId"] = pose_frame_id or None
    output["geometryFrameId"] = geometry_frame_id or None
    if all(
        output.get(name) is None
        for name in ("cameraX", "cameraY", "cameraZ", "cameraYaw", "cameraPitch")
    ):
        return None
    return output


def _input_geometry_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    output: dict[str, Any] = {
        "schema": _clean_text(value.get("schema"), maximum=64) or None,
        "geometryAvailable": bool(value.get("geometryAvailable", False)),
        "coordinateSpace": _clean_text(
            value.get("coordinateSpace"), maximum=64
        )
        or None,
        "isCanvasShowing": bool(value.get("isCanvasShowing", False)),
        "isClientFocused": bool(value.get("isClientFocused", False)),
    }
    for name in (
        "sourceTick",
        "canvasWidth",
        "canvasHeight",
        "sourceCanvasWidth",
        "sourceCanvasHeight",
        "canvasScreenX",
        "canvasScreenY",
        "clientWindowScreenX",
        "clientWindowScreenY",
        "clientWindowWidth",
        "clientWindowHeight",
        "clientProcessId",
    ):
        output[name] = _integer_or_none(value.get(name))
    return output


def _pointer_payload(sample: Mapping[str, Any]) -> dict[str, Any]:
    nested = sample.get("mouse") if isinstance(sample.get("mouse"), Mapping) else {}
    return {
        "canvasX": _integer_or_none(sample.get("mouseCanvasX", nested.get("canvasX"))),
        "canvasY": _integer_or_none(sample.get("mouseCanvasY", nested.get("canvasY"))),
        "isInCanvas": bool(sample.get("isInCanvas", nested.get("isInCanvas", False))),
        "wallTimeMillis": _integer_or_none(sample.get("wallTimeMillis")),
        "monotonicTimeNanos": _integer_or_none(sample.get("monotonicTimeNanos")),
        "observableButton": _clean_text(
            sample.get("mouseButton", sample.get("button", ""))
        )
        or None,
        "observableKey": _clean_text(
            sample.get("key", sample.get("keyText", ""))
        )
        or None,
        "cameraPose": _camera_pose_payload(sample.get("cameraPose")),
    }


def _camera_input_payload(sample: Mapping[str, Any]) -> dict[str, Any]:
    input_kind = _clean_text(sample.get("inputKind"), maximum=32)
    phase = _clean_text(sample.get("phase"), maximum=32)
    control = _clean_text(sample.get("control"), maximum=32).upper()
    if input_kind not in _CAMERA_INPUT_KINDS:
        input_kind = "unsupported"
    if phase not in _CAMERA_INPUT_PHASES:
        phase = "unsupported"
    if control not in _CAMERA_INPUT_CONTROLS:
        control = "UNSUPPORTED"
    payload: dict[str, Any] = {
        "schema": _clean_text(sample.get("schema"), maximum=64) or None,
        "controlEvidence": "candidate_camera_control",
        "inputKind": input_kind,
        "phase": phase,
        "control": control,
        "clientTick": _integer_or_none(sample.get("clientTick")),
        "gameTickAtSample": _integer_or_none(sample.get("gameTickAtSample")),
        "wallTimeMillis": _integer_or_none(sample.get("wallTimeMillis")),
        "monotonicTimeNanos": _integer_or_none(sample.get("monotonicTimeNanos")),
        "awtEventWhenMillis": _integer_or_none(
            sample.get("awtEventWhenMillis")
        ),
        "cameraPose": _camera_pose_payload(
            sample.get("cameraPose", sample.get("poseBefore"))
        ),
        "poseFrameId": _clean_text(sample.get("poseFrameId"), maximum=256)
        or None,
        "holdDurationMillis": _integer_or_none(sample.get("holdDurationMillis")),
        "pathDistancePixels": (
            round(float(sample["pathDistancePixels"]), 3)
            if isinstance(sample.get("pathDistancePixels"), (int, float))
            and not isinstance(sample.get("pathDistancePixels"), bool)
            and math.isfinite(float(sample["pathDistancePixels"]))
            else None
        ),
        "totalDeltaX": _integer_or_none(sample.get("totalDeltaX")),
        "totalDeltaY": _integer_or_none(sample.get("totalDeltaY")),
        "dragSampleCount": _integer_or_none(sample.get("dragSampleCount")),
    }
    if input_kind == "middle_drag":
        for name in ("canvasX", "canvasY", "deltaX", "deltaY"):
            payload[name] = _integer_or_none(sample.get(name))
    return payload


def _hover_payload(sample: Mapping[str, Any]) -> dict[str, Any]:
    entries = sample.get("entries", [])
    values = entries if isinstance(entries, list) else []
    sanitized = [
        _menu_entry_payload(item)
        for item in values[:16]
        if isinstance(item, Mapping)
    ]
    entry_count = _integer_or_none(sample.get("entryCount"))
    return {
        "menuOpen": bool(sample.get("menuOpen", False)),
        "pointer": _pointer_payload(sample),
        "entries": sanitized,
        "entryCount": entry_count,
        "recordedEntryCount": len(sanitized),
        "entryCapHit": entry_count is not None and entry_count > len(sanitized),
        "hoveredTarget": sanitized[0] if sanitized else None,
    }


def _clicked_payload(
    sample: Mapping[str, Any],
    before_observation: Mapping[str, Any] | None,
    before_source_tick: int | None,
) -> dict[str, Any]:
    raw_consumed = sample.get("consumed", _UNSET)
    consumed = raw_consumed if isinstance(raw_consumed, bool) else None
    payload = {
        **_menu_entry_payload(sample),
        "itemId": _integer_or_none(sample.get("itemId")),
        "consumed": consumed,
        "clientTick": _integer_or_none(sample.get("clientTick")),
        "gameTickAtSample": _integer_or_none(sample.get("gameTickAtSample")),
        "monotonicTimeNanos": _integer_or_none(sample.get("monotonicTimeNanos")),
        "pointer": _pointer_payload(sample),
        "cameraPose": _camera_pose_payload(sample.get("cameraPose")),
        "geometryFrameId": _clean_text(
            sample.get("geometryFrameId"), maximum=256
        )
        or None,
        "clickEvidenceId": _clean_text(
            sample.get("clickEvidenceId"), maximum=256
        )
        or None,
        "resolvedTarget": _resolved_target_payload(sample.get("resolvedTarget")),
    }
    payload["entityEvidence"] = _clicked_entity_evidence(
        payload, before_observation, before_source_tick
    )
    return payload


def _clicked_entity_evidence(
    click: Mapping[str, Any],
    before_observation: Mapping[str, Any] | None,
    before_source_tick: int | None,
) -> dict[str, Any] | None:
    entry_type = _clean_text(click.get("type")).upper()
    menu_identifier = _integer_or_none(click.get("identifier"))
    resolved = (
        click.get("resolvedTarget")
        if isinstance(click.get("resolvedTarget"), Mapping)
        else {}
    )
    resolved_object = (
        resolved.get("object")
        if isinstance(resolved.get("object"), Mapping)
        else {}
    )
    if (
        resolved.get("resolution") == "exact"
        and resolved.get("actionFamily") == "tile_object"
        and resolved_object
    ):
        stable_id = _integer_or_none(resolved_object.get("id"))
        if stable_id is not None and stable_id > 0:
            return {
                "kind": _clean_text(resolved_object.get("kind")) or "GAME_OBJECT",
                "stableEntityId": stable_id,
                "objectKey": _clean_text(
                    resolved_object.get("objectKey"), maximum=256
                )
                or None,
                "world": _resolved_world_payload(resolved_object),
                "scene": (
                    dict(resolved_object["scene"])
                    if isinstance(resolved_object.get("scene"), Mapping)
                    else _coordinate_pair(resolved_object, "sceneX", "sceneY")
                ),
                "source": "runelite_exact_click_target",
                "censusMatch": True,
                "resolution": "exact",
                "geometryFrameId": _clean_text(
                    click.get("geometryFrameId"), maximum=256
                )
                or None,
            }
    if menu_identifier is None or menu_identifier <= 0:
        return None
    if "GAME_OBJECT" in entry_type:
        values = (
            before_observation.get("nearbyObjects", [])
            if isinstance(before_observation, Mapping)
            else []
        )
        values = values if isinstance(values, list) else []
        same_tick = (
            before_source_tick is not None
            and _integer_or_none(click.get("gameTickAtSample")) == before_source_tick
        )
        scene_x = _integer_or_none(click.get("param0"))
        scene_y = _integer_or_none(click.get("param1"))
        identity_matches = [
            value
            for value in values
            if isinstance(value, Mapping)
            and _integer_or_none(value.get("objectId")) == menu_identifier
        ]
        exact_matches = [
            value
            for value in identity_matches
            if same_tick
            and scene_x is not None
            and scene_y is not None
            and isinstance(value.get("scene"), Mapping)
            and _integer_or_none(value["scene"].get("x")) == scene_x
            and _integer_or_none(value["scene"].get("y")) == scene_y
        ]
        if not exact_matches and same_tick:
            click_screen_point = _click_screen_point(click, before_observation)
            click_geometry_frame_id = _clean_text(
                click.get("geometryFrameId"), maximum=256
            )
            if click_screen_point is not None:
                exact_matches = [
                    value
                    for value in identity_matches
                    if _object_projection_contains_click(
                        value,
                        click_screen_point,
                        click_geometry_frame_id or None,
                    )
                ]
        if len(exact_matches) == 1:
            match = exact_matches[0]
            return {
                "kind": "GAME_OBJECT",
                "stableEntityId": menu_identifier,
                "objectKey": _clean_text(match.get("key"), maximum=256) or None,
                "name": _clean_text(match.get("name")) or None,
                "world": (
                    dict(match["world"])
                    if isinstance(match.get("world"), Mapping)
                    else None
                ),
                "scene": dict(match["scene"]),
                "projection": (
                    dict(match["projection"])
                    if isinstance(match.get("projection"), Mapping)
                    else None
                ),
                "source": (
                    "same_tick_object_id_and_scene_correlation"
                    if scene_x is not None
                    and scene_y is not None
                    and isinstance(match.get("scene"), Mapping)
                    and _integer_or_none(match["scene"].get("x")) == scene_x
                    and _integer_or_none(match["scene"].get("y")) == scene_y
                    else "same_tick_object_id_and_clickbox_containment"
                ),
                "censusMatch": True,
                "resolution": "exact",
                "geometryFrameId": (
                    match.get("projection", {}).get("geometryFrameId")
                    if isinstance(match.get("projection"), Mapping)
                    else None
                ),
            }
        return {
            "kind": "GAME_OBJECT",
            "stableEntityId": menu_identifier,
            "source": "runelite_game_object_menu_identifier",
            "censusMatch": bool(identity_matches),
            "resolution": "ambiguous",
        }
    if "NPC" in entry_type and "PLAYER" not in entry_type:
        if (
            before_source_tick is None
            or _integer_or_none(click.get("gameTickAtSample"))
            != before_source_tick
        ):
            return None
        values = (
            before_observation.get("nearbyNpcs", [])
            if isinstance(before_observation, Mapping)
            else []
        )
        if not isinstance(values, list):
            return None
        matches = [
            value
            for value in values
            if isinstance(value, Mapping)
            and _integer_or_none(value.get("index")) == menu_identifier
            and (_integer_or_none(value.get("id")) or 0) > 0
        ]
        if len(matches) != 1:
            return None
        match = matches[0]
        return {
            "kind": "NPC",
            "stableEntityId": _integer_or_none(match.get("id")),
            "npcIndex": menu_identifier,
            "name": _clean_text(match.get("name")) or None,
            "source": "actor_census_index_correlation",
            "censusMatch": True,
        }
    return None


def _click_screen_point(
    click: Mapping[str, Any], before_observation: Mapping[str, Any] | None
) -> tuple[int, int] | None:
    if not isinstance(before_observation, Mapping):
        return None
    pointer = click.get("pointer") if isinstance(click.get("pointer"), Mapping) else {}
    canvas_x = _integer_or_none(pointer.get("canvasX"))
    canvas_y = _integer_or_none(pointer.get("canvasY"))
    view = (
        before_observation.get("viewGeometry")
        if isinstance(before_observation.get("viewGeometry"), Mapping)
        else {}
    )
    geometry = (
        view.get("inputGeometry")
        if isinstance(view.get("inputGeometry"), Mapping)
        else {}
    )
    source_width = _integer_or_none(geometry.get("sourceCanvasWidth"))
    source_height = _integer_or_none(geometry.get("sourceCanvasHeight"))
    canvas_width = _integer_or_none(geometry.get("canvasWidth"))
    canvas_height = _integer_or_none(geometry.get("canvasHeight"))
    origin_x = _integer_or_none(geometry.get("canvasScreenX"))
    origin_y = _integer_or_none(geometry.get("canvasScreenY"))
    if (
        None in (
            canvas_x,
            canvas_y,
            source_width,
            source_height,
            canvas_width,
            canvas_height,
            origin_x,
            origin_y,
        )
        or source_width <= 0
        or source_height <= 0
        or canvas_width <= 0
        or canvas_height <= 0
        or not (0 <= canvas_x < source_width and 0 <= canvas_y < source_height)
    ):
        return None
    scale_x = canvas_width / source_width
    scale_y = canvas_height / source_height
    return (
        origin_x + math.ceil(canvas_x * scale_x - 0.5),
        origin_y + math.ceil(canvas_y * scale_y - 0.5),
    )


def _object_projection_contains_click(
    value: Mapping[str, Any],
    point: tuple[int, int],
    geometry_frame_id: str | None,
) -> bool:
    projection = (
        value.get("projection") if isinstance(value.get("projection"), Mapping) else {}
    )
    projection_frame_id = _clean_text(
        projection.get("geometryFrameId"), maximum=256
    )
    if (
        geometry_frame_id
        and projection_frame_id
        and geometry_frame_id != projection_frame_id
    ):
        return False
    if projection.get("available") is not True or projection.get("onScreen") is not True:
        return False
    raw_polygon = projection.get("screenPolygon")
    if not isinstance(raw_polygon, list) or len(raw_polygon) < 3:
        return False
    polygon: list[tuple[int, int]] = []
    for raw in raw_polygon[:256]:
        if not isinstance(raw, Mapping):
            return False
        x = _integer_or_none(raw.get("x"))
        y = _integer_or_none(raw.get("y"))
        if x is None or y is None:
            return False
        polygon.append((x, y))
    return _point_in_polygon(point, polygon)


def _point_in_polygon(
    point: tuple[int, int], polygon: list[tuple[int, int]]
) -> bool:
    px, py = point
    inside = False
    previous_x, previous_y = polygon[-1]
    for current_x, current_y in polygon:
        cross = (px - previous_x) * (current_y - previous_y) - (
            py - previous_y
        ) * (current_x - previous_x)
        if (
            cross == 0
            and min(previous_x, current_x) <= px <= max(previous_x, current_x)
            and min(previous_y, current_y) <= py <= max(previous_y, current_y)
        ):
            return True
        if (current_y > py) != (previous_y > py):
            intersection_x = (previous_x - current_x) * (py - current_y) / (
                previous_y - current_y
            ) + current_x
            if px < intersection_x:
                inside = not inside
        previous_x, previous_y = current_x, current_y
    return inside


def _resolved_world_payload(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    if isinstance(value.get("world"), Mapping):
        nested = _world_from_mapping(value["world"])
        if nested is not None:
            return nested
    x = _integer_or_none(value.get("worldX", value.get("x")))
    y = _integer_or_none(value.get("worldY", value.get("y")))
    plane = _integer_or_none(value.get("plane"))
    if x is None or y is None or plane is None:
        return None
    return {"x": x, "y": y, "plane": plane}


def _resolved_polygon_payload(value: object) -> list[dict[str, int]]:
    if not isinstance(value, list):
        return []
    points: list[dict[str, int]] = []
    for raw in value[:256]:
        if isinstance(raw, Mapping):
            x = _integer_or_none(raw.get("x"))
            y = _integer_or_none(raw.get("y"))
        elif isinstance(raw, list) and len(raw) == 2:
            x = _integer_or_none(raw[0])
            y = _integer_or_none(raw[1])
        else:
            continue
        if x is not None and y is not None:
            points.append({"x": x, "y": y})
    return points


def _resolved_bounds_payload(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    x = _integer_or_none(value.get("x"))
    y = _integer_or_none(value.get("y"))
    width = _integer_or_none(value.get("width", value.get("w")))
    height = _integer_or_none(value.get("height", value.get("h")))
    if None in (x, y, width, height) or width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _resolved_target_payload(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    activation_kind = _clean_text(value.get("activationKind"), maximum=32)
    output: dict[str, Any] = {
        "schema": _clean_text(value.get("schema"), maximum=64) or None,
        "resolution": _clean_text(value.get("resolution"), maximum=32)
        or "unresolved",
        "confidence": _clean_text(value.get("confidence"), maximum=32) or None,
        "source": _clean_text(value.get("source"), maximum=64) or None,
        "actionFamily": _clean_text(value.get("actionFamily"), maximum=32)
        or "other",
        # The menu-selection point is not object aim geometry. Missing legacy
        # declarations remain explicitly unverified instead of being inferred
        # from the final pointer coordinate.
        "activationKind": activation_kind or "unverified",
        "worldViewId": _integer_or_none(value.get("worldViewId")),
        "candidateCount": _integer_or_none(value.get("candidateCount")),
        "ambiguityReasons": [
            _clean_text(reason, maximum=128)
            for reason in (
                value.get("ambiguityReasons")
                if isinstance(value.get("ambiguityReasons"), list)
                else []
            )[:16]
            if _clean_text(reason, maximum=128)
        ],
    }
    for name in ("worldTile", "localDestinationTile"):
        raw_world = value.get(name)
        if isinstance(raw_world, Mapping):
            output[name] = _resolved_world_payload(raw_world)
    for name in ("menuParamTile", "selectedSceneTile"):
        raw_tile = value.get(name)
        if isinstance(raw_tile, Mapping):
            world = _resolved_world_payload(raw_tile)
            output[name] = world if world is not None else {
                "x": _integer_or_none(raw_tile.get("x", raw_tile.get("sceneX"))),
                "y": _integer_or_none(raw_tile.get("y", raw_tile.get("sceneY"))),
            }
    tile = value.get("tile") if isinstance(value.get("tile"), Mapping) else None
    if tile is not None:
        output["tile"] = {
            "scene": _coordinate_pair(tile, "sceneX", "sceneY"),
            "world": _resolved_world_payload(tile),
            "source": _clean_text(tile.get("source"), maximum=64) or None,
            "selectedSceneTileMatch": bool(
                tile.get("selectedSceneTileMatch", False)
            ),
        }
    raw_object = (
        value.get("object") if isinstance(value.get("object"), Mapping) else None
    )
    if raw_object is not None:
        output["object"] = {
            "objectKey": _clean_text(raw_object.get("objectKey"), maximum=256)
            or None,
            "id": _integer_or_none(raw_object.get("id")),
            "hash": _integer_or_none(raw_object.get("hash")),
            "kind": _clean_text(raw_object.get("kind"), maximum=64) or None,
            "world": _resolved_world_payload(raw_object),
            "scene": _coordinate_pair(raw_object, "sceneX", "sceneY"),
            "local": _coordinate_pair(raw_object, "localX", "localY"),
            "orientation": _integer_or_none(raw_object.get("orientation")),
        }
    geometry = (
        value.get("geometry")
        if isinstance(value.get("geometry"), Mapping)
        else None
    )
    if geometry is not None:
        output["geometry"] = {
            "geometryFrameId": _clean_text(
                geometry.get("geometryFrameId"), maximum=256
            )
            or None,
            "source": _clean_text(geometry.get("source"), maximum=64) or None,
            "polygon": _resolved_polygon_payload(geometry.get("polygon")),
            "bounds": _resolved_bounds_payload(geometry.get("bounds")),
            "clickInside": (
                bool(geometry.get("clickInside"))
                if geometry.get("clickInside") is not None
                else None
            ),
        }
    return output


def _menu_entry_payload(sample: Mapping[str, Any]) -> dict[str, Any]:
    entry_type = _clean_text(sample.get("type", sample.get("entryType", "")))
    target = _clean_text(sample.get("target", ""))
    if "PLAYER" in entry_type.upper():
        target = "<player-redacted>"
    return {
        "option": _clean_text(sample.get("option", "")),
        "target": target,
        "type": entry_type,
        "identifier": _integer_or_none(sample.get("identifier", sample.get("id"))),
        "param0": _integer_or_none(sample.get("param0")),
        "param1": _integer_or_none(sample.get("param1")),
    }


def _safe_name(value: object) -> str:
    if not isinstance(value, str):
        raise DemonstrationError("demonstration name must be text")
    if any(part in value for part in ("/", "\\", "..")):
        raise DemonstrationError("demonstration name must not contain a path")
    normalized = value.strip().lower().replace(" ", "-")
    if not _SAFE_NAME.fullmatch(normalized):
        raise DemonstrationError(
            "demonstration name must be 1-64 lowercase letters, digits, '-' or '_'"
        )
    return normalized


def _new_artifact_path(root: Path, name: str, now: datetime) -> Path:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = Path(root)
    candidate = root / f"{stamp}_{name}"
    suffix = 1
    while candidate.exists():
        candidate = root / f"{stamp}_{name}_{suffix:02d}"
        suffix += 1
    return candidate


def _require_loaded_identity(observation: Observation) -> None:
    if not observation.loaded_scene:
        raise DemonstrationError("a coherent loaded scene is required to record")
    if not isinstance(observation.session_id, str) or not observation.session_id:
        raise DemonstrationError("recording requires a RuneLite session ID")
    if (
        not isinstance(observation.client_process_id, int)
        or isinstance(observation.client_process_id, bool)
        or observation.client_process_id <= 0
    ):
        raise DemonstrationError("recording requires a RuneLite process ID")


def _validate_camera_pose_shape(value: object, label: str) -> None:
    if not isinstance(value, Mapping):
        raise DemonstrationError(f"{label} is not an object")
    if value.get("schema") != "camera_pose.v1":
        raise DemonstrationError(f"{label} has an unsupported schema")
    for name in (
        "cameraX",
        "cameraY",
        "cameraZ",
        "cameraYaw",
        "cameraPitch",
        "cameraYawTarget",
        "cameraPitchTarget",
        "zoom3d",
        "viewportXOffset",
        "viewportYOffset",
        "viewportWidth",
        "viewportHeight",
        "canvasWidth",
        "canvasHeight",
    ):
        if value.get(name) is not None and _integer_or_none(value.get(name)) is None:
            raise DemonstrationError(f"{label}.{name} is not an integer")
    for name in ("cameraYaw", "cameraYawTarget"):
        candidate = _integer_or_none(value.get(name))
        if candidate is not None and not 0 <= candidate < CAMERA_YAW_UNITS:
            raise DemonstrationError(f"{label}.{name} is outside the RuneLite range")
    for name in (
        "cameraPitch",
        "cameraPitchTarget",
        "zoom3d",
        "viewportWidth",
        "viewportHeight",
        "canvasWidth",
        "canvasHeight",
    ):
        candidate = _integer_or_none(value.get(name))
        if candidate is not None and candidate < 0:
            raise DemonstrationError(f"{label}.{name} is negative")


def _validate_nonnegative_time_fields(sample: Mapping[str, Any], label: str) -> None:
    for name in ("wallTimeMillis", "monotonicTimeNanos"):
        candidate = _integer_or_none(sample.get(name))
        if candidate is None or candidate < 0:
            raise DemonstrationError(f"{label} lacks a valid {name}")
    awt_when = sample.get("awtEventWhenMillis")
    if awt_when is not None and (
        (candidate := _integer_or_none(awt_when)) is None or candidate < 0
    ):
        raise DemonstrationError(f"{label} has an invalid awtEventWhenMillis")


def _validate_evidence_envelope_and_hot(
    evidence: DemonstrationEvidenceSnapshot,
) -> tuple[Mapping[str, Any], int]:
    observation = evidence.observation
    raw = evidence.payload()
    if raw.get("schema") != RESPONSE_SCHEMA:
        raise DemonstrationError("demonstration evidence has the wrong response schema")
    request = evidence.request()
    needs = request.get("needs")
    if (
        not isinstance(needs, list)
        or any(not isinstance(value, str) for value in needs)
        or not {
        "scene_object_census",
        "client_tick_tail",
        "actor_census",
        "collision_window",
        }.issubset(set(needs))
    ):
        raise DemonstrationError("demonstration evidence request lacks required needs")

    hot = _hot_payload(raw)
    if hot.get("schema") != "client_tick_hot.v1":
        raise DemonstrationError("client-tick evidence is missing or unsupported")
    if hot.get("sessionId") != observation.session_id or _integer_or_none(
        hot.get("clientProcessId")
    ) != observation.client_process_id:
        raise DemonstrationError("client-tick evidence is not bound to the loaded client")
    latest_sequence = _integer_or_none(hot.get("latestEventSequence"))
    if latest_sequence is None or latest_sequence < 0:
        raise DemonstrationError("client-tick evidence lacks its sequence watermark")
    seen_sequences: set[int] = set()
    for key, expected_lane in (
        ("clientTickTail", "client_tick"),
        ("postMenuSortTail", "post_menu_sort"),
        ("clickedTail", "menu_option_clicked"),
        ("cameraInputTail", "camera_input"),
    ):
        values = hot.get(key)
        if not isinstance(values, list):
            raise DemonstrationError(f"client-tick evidence lacks {key}")
        for sample in values:
            if not isinstance(sample, Mapping):
                raise DemonstrationError(f"{key} contains a non-object sample")
            sequence = _integer_or_none(sample.get("eventSequence"))
            if sequence is None or sequence <= 0 or sequence > latest_sequence:
                raise DemonstrationError(f"{key} contains an invalid event sequence")
            if sequence in seen_sequences:
                raise DemonstrationError("client-tick tails reuse an event sequence")
            seen_sequences.add(sequence)
            if sample.get("eventLane") != expected_lane:
                raise DemonstrationError(f"{key} contains the wrong event lane")
            if sample.get("cameraPose") is not None:
                _validate_camera_pose_shape(sample.get("cameraPose"), f"{key} cameraPose")
            if expected_lane == "camera_input":
                if sample.get("schema") != "plugin_camera_input.v1":
                    raise DemonstrationError(
                        "cameraInputTail contains an unsupported sample schema"
                    )
                if sample.get("inputKind") not in _CAMERA_INPUT_KINDS:
                    raise DemonstrationError(
                        "cameraInputTail contains a non-whitelisted input kind"
                    )
                if sample.get("phase") not in _CAMERA_INPUT_PHASES:
                    raise DemonstrationError(
                        "cameraInputTail contains an unsupported input phase"
                    )
                if str(sample.get("control", "")).upper() not in _CAMERA_INPUT_CONTROLS:
                    raise DemonstrationError(
                        "cameraInputTail contains a non-whitelisted control"
                    )
                _validate_nonnegative_time_fields(sample, "cameraInputTail sample")
                if sample.get("phase") in {"release", "cancel"}:
                    hold = _integer_or_none(sample.get("holdDurationMillis"))
                    if hold is None or hold < 0:
                        raise DemonstrationError(
                            "cameraInputTail terminal sample lacks a valid hold duration"
                        )
                    path = sample.get("pathDistancePixels")
                    if path is not None and (
                        not isinstance(path, (int, float))
                        or isinstance(path, bool)
                        or not math.isfinite(float(path))
                        or float(path) < 0
                    ):
                        raise DemonstrationError(
                            "cameraInputTail terminal sample has an invalid path distance"
                        )
            elif expected_lane == "menu_option_clicked":
                _validate_nonnegative_time_fields(sample, "clickedTail sample")
                if not isinstance(sample.get("consumed"), bool):
                    raise DemonstrationError(
                        "clickedTail sample lacks explicit consumed evidence"
                    )
                resolved = sample.get("resolvedTarget")
                if isinstance(resolved, Mapping) and resolved.get("schema") != (
                    "plugin_click_target.v1"
                ):
                    raise DemonstrationError(
                        "clickedTail sample has an unsupported resolved target"
                    )
            elif expected_lane == "client_tick":
                _validate_nonnegative_time_fields(sample, "clientTickTail sample")
            if sample.get("sessionId") != observation.session_id or _integer_or_none(
                sample.get("clientProcessId")
            ) != observation.client_process_id:
                raise DemonstrationError(f"{key} contains evidence from another client")

    return raw, latest_sequence


def _validate_evidence_binding(evidence: DemonstrationEvidenceSnapshot) -> int:
    observation = evidence.observation
    raw, latest_sequence = _validate_evidence_envelope_and_hot(evidence)
    for name, schema in (
        ("scene_object_census", "scene_object_census.v1"),
        ("actor_census", "world_model_actor_census.v1"),
        ("collision_window", "world_model_collision_window.v1"),
    ):
        payload = _dynamic_payload(raw, name)
        if payload.get("schema") != schema:
            raise DemonstrationError(f"{name} evidence is missing or unsupported")
        if (
            _integer_or_none(payload.get("sourceTick")) != observation.tick
            or payload.get("sessionId") != observation.session_id
            or _integer_or_none(payload.get("clientProcessId"))
            != observation.client_process_id
            or payload.get("geometryFrameId") != observation.geometry_frame_id
        ):
            raise DemonstrationError(f"{name} evidence is not atomic-frame bound")
        _parse_timestamp(payload.get("capturedAtUtc"), f"{name} capture time")
    return latest_sequence


def _widget_payload(
    widget: Any, geometry_frame_id: str | None
) -> dict[str, Any] | None:
    if widget is None:
        return None
    return {
        "name": _clean_text(widget.name),
        "visible": bool(widget.visible),
        "screenPoint": _point_payload(widget.screen_point),
        "screenBounds": _bounds_payload(widget.screen_bounds),
        "geometryFrameId": geometry_frame_id,
    }


def _source(
    observation: Observation,
    *,
    client_tick: object = _UNSET,
    source_tick: object = _UNSET,
    event_sequence: int | None = None,
) -> dict[str, Any]:
    return {
        "sessionId": observation.session_id,
        "pid": observation.client_process_id,
        "sourceTick": observation.tick if source_tick is _UNSET else source_tick,
        "clientTick": (
            observation.menu_client_tick if client_tick is _UNSET else client_tick
        ),
        "plane": observation.plane,
        "eventSequence": event_sequence,
    }


def _client_tick_from_payload(raw: Mapping[str, Any]) -> int | None:
    hot = _hot_payload(raw)
    candidates: list[Any] = [hot.get("clientTick")]
    for name in ("actor_census", "collision_window"):
        candidates.append(_dynamic_payload(raw, name).get("clientTick"))
    payloads = raw.get("payloads")
    if isinstance(payloads, Mapping):
        baseline = payloads.get("baseline")
        if isinstance(baseline, Mapping):
            candidates.append(baseline.get("clientTick"))
    for candidate in candidates:
        value = _integer_or_none(candidate)
        if value is not None:
            return value
    return None


def _clean_text(value: object, *, maximum: int = 512) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = unescape(_TAG.sub("", text))
    text = "".join(
        character
        for character in text
        if character in "\t\n\r" or ord(character) >= 32
    )
    text = " ".join(text.split())
    return text[:maximum]


def _integer_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if int(value) != value:
        return None
    return int(value)


def _coordinate_pair(
    raw: Mapping[str, Any], x_key: str, y_key: str
) -> dict[str, int] | None:
    x = _integer_or_none(raw.get(x_key))
    y = _integer_or_none(raw.get(y_key))
    if x is None or y is None or x < 0 or y < 0:
        return None
    return {"x": x, "y": y}


def _world_from_mapping(raw: Mapping[str, Any]) -> dict[str, int] | None:
    nested = raw.get("world")
    source = nested if isinstance(nested, Mapping) else raw
    x = _integer_or_none(source.get("x", source.get("worldX")))
    y = _integer_or_none(source.get("y", source.get("worldY")))
    plane = _integer_or_none(source.get("plane"))
    if x is None or y is None or plane is None or min(x, y, plane) < 0:
        return None
    return {"x": x, "y": y, "plane": plane}


def _point_payload(point: ScreenPoint | None) -> dict[str, int] | None:
    return None if point is None else {"x": point.x, "y": point.y}


def _bounds_payload(bounds: ScreenBounds | None) -> dict[str, int] | None:
    if bounds is None:
        return None
    return {
        "x": bounds.x,
        "y": bounds.y,
        "width": bounds.width,
        "height": bounds.height,
    }


def _sample_point(
    sample: Mapping[str, Any], canvas: ScreenBounds | None
) -> ScreenPoint | None:
    if canvas is None:
        return None
    nested = sample.get("mouse") if isinstance(sample.get("mouse"), Mapping) else {}
    x = _integer_or_none(sample.get("mouseCanvasX", nested.get("canvasX")))
    y = _integer_or_none(sample.get("mouseCanvasY", nested.get("canvasY")))
    if x is None or y is None or not (0 <= x < canvas.width and 0 <= y < canvas.height):
        return None
    point = ScreenPoint(canvas.x + x, canvas.y + y)
    return point if canvas.contains(point) else None


def _state_changes(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    before_player = before.get("player") if isinstance(before.get("player"), Mapping) else {}
    after_player = after.get("player") if isinstance(after.get("player"), Mapping) else {}
    for field, key in (("player.world", "world"), ("player.plane", "plane")):
        if before_player.get(key) != after_player.get(key):
            changes.append(
                {"field": field, "before": before_player.get(key), "after": after_player.get(key)}
            )

    def quantities(value: object) -> dict[str, int]:
        inventory = value if isinstance(value, Mapping) else {}
        items = inventory.get("items", [])
        output: dict[str, int] = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                item_id = _integer_or_none(item.get("itemId"))
                quantity = _integer_or_none(item.get("quantity"))
                if item_id is not None and quantity is not None:
                    output[str(item_id)] = output.get(str(item_id), 0) + quantity
        return output

    before_inventory = before.get("inventory")
    after_inventory = after.get("inventory")
    before_quantities = quantities(before_inventory)
    after_quantities = quantities(after_inventory)
    if before_quantities != after_quantities:
        changes.append(
            {
                "field": "inventory.quantitiesByItemId",
                "before": before_quantities,
                "after": after_quantities,
            }
        )
    for field in ("occupiedSlots", "freeSlots"):
        old = before_inventory.get(field) if isinstance(before_inventory, Mapping) else None
        new = after_inventory.get(field) if isinstance(after_inventory, Mapping) else None
        if old != new:
            changes.append(
                {"field": f"inventory.{field}", "before": old, "after": new}
            )

    before_interfaces = (
        before.get("interfaces") if isinstance(before.get("interfaces"), Mapping) else {}
    )
    after_interfaces = (
        after.get("interfaces") if isinstance(after.get("interfaces"), Mapping) else {}
    )
    for key in ("bankOpen", "bankReadable", "bankPinOpen", "dialogueActive", "dialogueType"):
        if before_interfaces.get(key) != after_interfaces.get(key):
            changes.append(
                {
                    "field": f"interfaces.{key}",
                    "before": before_interfaces.get(key),
                    "after": after_interfaces.get(key),
                }
            )
    return changes


def _manifest_base(
    name: str,
    evidence: DemonstrationEvidenceSnapshot,
    *,
    created_at: datetime,
    annotations: tuple[str, ...],
) -> dict[str, Any]:
    observation = evidence.observation
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    git = _git_metadata()
    request = _scrub_secrets(evidence.request())
    return {
        "schema": MANIFEST_SCHEMA,
        "name": name,
        "createdAtUtc": created_at.astimezone(timezone.utc).isoformat(),
        "fetchedAtUtc": evidence.fetched_at_utc.astimezone(timezone.utc).isoformat(),
        "readOnly": True,
        "injectsInput": False,
        "rawReplayAllowed": False,
        "automaticActivationAllowed": False,
        "reviewRequired": True,
        "notProofOfGeneralCorrectness": True,
        "git": git,
        "dependencies": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "pillow": _dependency_version("Pillow"),
            "pyserial": _dependency_version("pyserial"),
            "runelite": _runelite_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "schemas": {
            "manifest": MANIFEST_SCHEMA,
            "event": EVENT_SCHEMA,
            "summary": SUMMARY_SCHEMA,
            "hashes": HASH_SCHEMA,
            "snapshotResponse": RESPONSE_SCHEMA,
            "sensorFrame": SENSOR_FRAME_SCHEMA,
            "clientTickHot": "client_tick_hot.v1",
            "cameraPose": "camera_pose.v1",
            "cameraInput": "plugin_camera_input.v1",
            "clickTarget": "plugin_click_target.v1",
            "sceneObjectCensus": "scene_object_census.v1",
            "actorCensus": "world_model_actor_census.v1",
            "collisionWindow": "world_model_collision_window.v1",
        },
        "sessionProvenance": {
            "sessionId": observation.session_id,
            "pid": observation.client_process_id,
            "firstSourceTick": observation.tick,
            "firstClientTick": _client_tick_from_payload(evidence.payload()),
            "firstPlane": observation.plane,
            "frameId": observation.frame_id,
            "geometryFrameId": observation.geometry_frame_id,
        },
        "request": request,
        "evidenceCoverage": {
            "atomicObservations": True,
            "playerWorldAndScene": True,
            "inventoryAndInterfaces": True,
            "nearbyObjectCensusRequested": True,
            "nearbyNpcCensusRequested": True,
            "collisionWindowRequested": True,
            "boundedPointerPath": True,
            "cameraPoseAtClientTick": True,
            "whitelistedCameraInput": True,
            "cameraInputWhitelistOnly": True,
            "cameraIntentSemanticsV2": True,
            "cameraIntentSemanticsV3": True,
            "cameraIntentSemanticsV4": True,
            "cameraKeyTransitions": True,
            "middleMouseCameraTransitions": True,
            "exactClickTargetResolution": True,
            "contextMenuActivationSemanticsV1": True,
            "contextMenuTimingSemanticsV1": True,
            "cameraIntentDerivation": True,
            "manualRouteIntentSemanticsV1": True,
            "manualRouteIntentSemanticsV2": True,
            "hoverMenus": True,
            "semanticMenuOptionClicked": True,
            "beforeAfterObservations": True,
            "boundedScreenshotsAvailable": True,
            "globalRawInputHooks": False,
            "rawMouseButtonTransitions": False,
            "rawKeyboardEvents": False,
            "rawInputUnavailableReason": (
                "only whitelisted camera keys and middle-button camera gestures are "
                "captured; global raw mouse-button and keyboard hooks remain disabled"
            ),
        },
        "annotations": [_clean_text(value) for value in annotations],
    }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git_metadata() -> dict[str, Any]:
    root = _repository_root()

    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip()

    commit = run("rev-parse", "HEAD") or "unknown"
    status = run("status", "--porcelain=v1", "--untracked-files=all")
    status = "" if status is None else status
    return {
        "commit": commit,
        "dirty": bool(status),
        "statusFingerprintSha256": hashlib.sha256(
            status.encode("utf-8")
        ).hexdigest(),
    }


def _dependency_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _runelite_version() -> str:
    try:
        source = (_repository_root() / "build.gradle").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r"runeLiteVersion\s*=\s*['\"]([^'\"]+)['\"]", source)
    return match.group(1) if match else "unknown"


def _scrub_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "auth")):
                continue
            output[text_key] = _scrub_secrets(item)
        return output
    if isinstance(value, list):
        return [_scrub_secrets(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _clean_text(value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _write_hashes(root: Path, now: datetime) -> None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    entries: list[dict[str, Any]] = []
    total_size = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise DemonstrationError("demonstration artifacts may not contain symlinks")
        if path.is_dir() or path.name == "hashes.json":
            continue
        if len(entries) >= MAX_ARTIFACT_FILES:
            raise DemonstrationError("demonstration artifact has too many files")
        relative = path.relative_to(root).as_posix()
        _validated_relative_path(relative)
        size = path.stat().st_size
        if size > MAX_ARTIFACT_FILE_BYTES:
            raise DemonstrationError(f"artifact file exceeds size limit: {relative}")
        total_size += size
        if total_size > MAX_TOTAL_ARTIFACT_BYTES:
            raise DemonstrationError("demonstration artifact exceeds total size limit")
        entries.append(
            {"path": relative, "sizeBytes": size, "sha256": _sha256_file(path)}
        )
    _write_json(
        root / "hashes.json",
        {
            "schema": HASH_SCHEMA,
            "algorithm": "sha256",
            "generatedAtUtc": now.astimezone(timezone.utc).isoformat(),
            "files": entries,
        },
    )


def _validated_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DemonstrationError("artifact hash path is not a safe relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(value) > 240
        or any(
            part in {"", ".", ".."}
            or ":" in part
            or part.endswith((".", " "))
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part)
            for part in path.parts
        )
    ):
        raise DemonstrationError("artifact hash path is not a safe relative path")
    return path


def _load_json_bounded(path: Path) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise DemonstrationError(f"missing or unsafe artifact file: {path.name}")
    if path.stat().st_size > MAX_ARTIFACT_FILE_BYTES:
        raise DemonstrationError(f"artifact file exceeds size limit: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise DemonstrationError(f"invalid JSON in {path.name}") from error
    if not isinstance(value, Mapping):
        raise DemonstrationError(f"{path.name} must contain a JSON object")
    return value


def _read_events_unverified(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise DemonstrationError("events.jsonl is missing or unsafe")
    if path.stat().st_size > MAX_ARTIFACT_FILE_BYTES:
        raise DemonstrationError("events.jsonl exceeds the size limit")
    output: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                if len(output) >= MAX_EVENTS:
                    raise DemonstrationError("events.jsonl exceeds the event limit")
                try:
                    event = json.loads(
                        line,
                        parse_constant=lambda constant: (_ for _ in ()).throw(
                            ValueError(constant)
                        ),
                    )
                except (json.JSONDecodeError, ValueError) as error:
                    raise DemonstrationError(
                        f"events.jsonl line {line_number} is invalid JSON"
                    ) from error
                if not isinstance(event, dict):
                    raise DemonstrationError(
                        f"events.jsonl line {line_number} is not an object"
                    )
                output.append(event)
    except (OSError, UnicodeDecodeError) as error:
        raise DemonstrationError("events.jsonl could not be read") from error
    return output


def _event_wall_time_millis(event: Mapping[str, Any]) -> int | None:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    direct = _integer_or_none(payload.get("wallTimeMillis"))
    if direct is not None:
        return direct
    pointer = payload.get("pointer")
    return (
        _integer_or_none(pointer.get("wallTimeMillis"))
        if isinstance(pointer, Mapping)
        else None
    )


def _event_time_millis(
    event: Mapping[str, Any], *, prefer_monotonic: bool
) -> int | None:
    if prefer_monotonic:
        payload = (
            event.get("payload")
            if isinstance(event.get("payload"), Mapping)
            else {}
        )
        direct = _integer_or_none(payload.get("monotonicTimeNanos"))
        if direct is None:
            pointer = payload.get("pointer")
            direct = (
                _integer_or_none(pointer.get("monotonicTimeNanos"))
                if isinstance(pointer, Mapping)
                else None
            )
        if direct is not None:
            return direct // 1_000_000
    return _event_wall_time_millis(event)


def _event_camera_pose(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    if isinstance(payload.get("cameraPose"), Mapping):
        return payload["cameraPose"]
    pointer = payload.get("pointer")
    if isinstance(pointer, Mapping) and isinstance(pointer.get("cameraPose"), Mapping):
        return pointer["cameraPose"]
    return None


def _same_event_client(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_source = left.get("source") if isinstance(left.get("source"), Mapping) else {}
    right_source = right.get("source") if isinstance(right.get("source"), Mapping) else {}
    return (
        left_source.get("sessionId") == right_source.get("sessionId")
        and _integer_or_none(left_source.get("pid"))
        == _integer_or_none(right_source.get("pid"))
    )


def _is_semantic_click(
    event: Mapping[str, Any], *, require_non_consumed: bool = True
) -> bool:
    if event.get("kind") != "menu_option_clicked":
        return False
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    option = _clean_text(payload.get("option"))
    return bool(
        option
        and option.casefold() != "cancel"
        and (
            payload.get("consumed") is False
            if require_non_consumed
            else True
        )
    )


def _is_exact_camera_intent_click(event: Mapping[str, Any]) -> bool:
    """Return whether a click can authoritatively terminate camera intent."""
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    target = _intent_target(payload)
    return bool(
        target
        and target.get("resolution") == "exact"
        and target.get("actionFamily") in {"walk_tile", "tile_object"}
    )


def _pose_signature(pose: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    if pose is None:
        return None
    return tuple(
        _integer_or_none(pose.get(name))
        for name in (
            "cameraX",
            "cameraY",
            "cameraZ",
            "cameraYaw",
            "cameraPitch",
            "zoom3d",
        )
    )


def _pose_for_time(
    pose_events: list[tuple[int, Mapping[str, Any]]],
    time_millis: int,
) -> Mapping[str, Any] | None:
    candidates = [pose for timestamp, pose in pose_events if timestamp <= time_millis]
    return candidates[-1] if candidates else None


def _pose_delta(
    before: Mapping[str, Any] | None, after: Mapping[str, Any] | None
) -> dict[str, int | None]:
    if before is None or after is None:
        return {"yaw": None, "pitch": None, "zoom3d": None}
    before_yaw = _integer_or_none(before.get("cameraYaw"))
    after_yaw = _integer_or_none(after.get("cameraYaw"))
    before_pitch = _integer_or_none(before.get("cameraPitch"))
    after_pitch = _integer_or_none(after.get("cameraPitch"))
    before_zoom = _integer_or_none(before.get("zoom3d"))
    after_zoom = _integer_or_none(after.get("zoom3d"))
    return {
        "yaw": (
            (
                after_yaw
                - before_yaw
                + CAMERA_YAW_UNITS // 2
            )
            % CAMERA_YAW_UNITS
            - CAMERA_YAW_UNITS // 2
            if before_yaw is not None and after_yaw is not None
            else None
        ),
        "pitch": (
            after_pitch - before_pitch
            if before_pitch is not None and after_pitch is not None
            else None
        ),
        "zoom3d": (
            after_zoom - before_zoom
            if before_zoom is not None and after_zoom is not None
            else None
        ),
    }


def _intent_target(
    payload: Mapping[str, Any], *, include_activation_kind: bool = False
) -> dict[str, Any] | None:
    resolved = payload.get("resolvedTarget")
    entity = (
        payload.get("entityEvidence")
        if isinstance(payload.get("entityEvidence"), Mapping)
        else {}
    )
    if (
        (not isinstance(resolved, Mapping) or resolved.get("resolution") != "exact")
        and entity.get("resolution") == "exact"
    ):
        projection = (
            entity.get("projection")
            if isinstance(entity.get("projection"), Mapping)
            else {}
        )
        target = {
            "resolution": "exact",
            "actionFamily": "tile_object",
            "object": {
                "objectKey": entity.get("objectKey"),
                "id": _integer_or_none(entity.get("stableEntityId")),
                "kind": entity.get("kind"),
                "world": (
                    dict(entity["world"])
                    if isinstance(entity.get("world"), Mapping)
                    else None
                ),
            },
            "geometry": {
                "geometryFrameId": entity.get("geometryFrameId"),
                "source": projection.get("geometrySource"),
                "clickInside": None,
            },
        }
        if include_activation_kind:
            target["activationKind"] = "unverified"
        return target
    if not isinstance(resolved, Mapping):
        return None
    source_resolution = _clean_text(resolved.get("resolution")) or "unresolved"
    confidence = _clean_text(resolved.get("confidence")) or None
    exact = source_resolution == "exact" or (
        source_resolution == "resolved" and confidence == "exact"
    )
    target: dict[str, Any] = {
        "resolution": "exact" if exact else source_resolution,
        "sourceResolution": source_resolution,
        "confidence": confidence,
        "source": _clean_text(resolved.get("source")) or None,
        "actionFamily": _clean_text(resolved.get("actionFamily")) or "other",
    }
    if include_activation_kind:
        target["activationKind"] = (
            _clean_text(resolved.get("activationKind")) or "unverified"
        )
    tile = resolved.get("tile") if isinstance(resolved.get("tile"), Mapping) else {}
    if isinstance(tile.get("world"), Mapping):
        target["world"] = dict(tile["world"])
    if isinstance(resolved.get("worldTile"), Mapping):
        target["world"] = dict(resolved["worldTile"])
    for name in ("menuParamTile", "selectedSceneTile", "localDestinationTile"):
        if isinstance(resolved.get(name), Mapping):
            target[name] = dict(resolved[name])
    raw_object = (
        resolved.get("object")
        if isinstance(resolved.get("object"), Mapping)
        else {}
    )
    if raw_object:
        target["object"] = {
            "objectKey": raw_object.get("objectKey"),
            "id": _integer_or_none(raw_object.get("id")),
            "kind": raw_object.get("kind"),
            "world": (
                dict(raw_object["world"])
                if isinstance(raw_object.get("world"), Mapping)
                else None
            ),
        }
    geometry = (
        resolved.get("geometry")
        if isinstance(resolved.get("geometry"), Mapping)
        else {}
    )
    if geometry:
        target["geometry"] = {
            "geometryFrameId": geometry.get("geometryFrameId"),
            "source": geometry.get("source"),
            "clickInside": geometry.get("clickInside"),
        }
    reasons = resolved.get("ambiguityReasons")
    if isinstance(reasons, list) and reasons:
        target["ambiguityReasons"] = list(reasons[:16])
    if target["resolution"] != "exact":
        target["semanticFallback"] = {
            "option": _clean_text(payload.get("option")) or None,
            "target": _clean_text(payload.get("target")) or None,
            "identifier": _integer_or_none(payload.get("identifier")),
            "type": _clean_text(payload.get("type")) or None,
        }
    return target


def _input_method(events: list[dict[str, Any]]) -> str:
    kinds = {
        event["payload"].get("inputKind")
        for event in events
        if isinstance(event.get("payload"), Mapping)
    }
    if kinds == {"key"}:
        return "keyboard"
    if kinds == {"middle_drag"}:
        return "middle_drag"
    return "mixed" if kinds else "pose_only"


def _camera_input_groups(
    inputs: list[dict[str, Any]],
    event_time: Callable[[Mapping[str, Any]], int | None],
    *,
    preserve_open_controls: bool = False,
) -> list[list[dict[str, Any]]]:
    """Group bounded camera controls, optionally preserving open transitions."""

    ordered = sorted(
        inputs,
        key=lambda event: (
            event_time(event) if event_time(event) is not None else -1,
            _integer_or_none(event.get("recorderSequence")) or -1,
        ),
    )
    groups: list[list[dict[str, Any]]] = []
    open_controls: set[tuple[str, str]] = set()
    for event in ordered:
        timestamp = event_time(event)
        if timestamp is None:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        token = (
            _clean_text(payload.get("inputKind")),
            _clean_text(payload.get("control")),
        )
        phase = _clean_text(payload.get("phase"))
        if groups:
            first_time = event_time(groups[-1][0])
            previous_time = event_time(groups[-1][-1])
            connected_terminal = phase in {"repeat", "drag", "release", "cancel"} and (
                token in open_controls
            )
            connected = bool(
                _same_event_client(groups[-1][-1], event)
                and first_time is not None
                and previous_time is not None
                and timestamp - first_time <= CAMERA_INPUT_MAX_SPAN_MILLIS
                and (
                    timestamp - previous_time <= CAMERA_INPUT_JOIN_MILLIS
                    or connected_terminal
                    # A held key can be quiet for longer than the ordinary
                    # join window while another camera control begins. Keep
                    # the episode open so the later terminal event cannot be
                    # orphaned into a false ineffective episode.
                    or (preserve_open_controls and bool(open_controls))
                )
            )
        else:
            connected = False
        if not connected:
            groups.append([])
            open_controls = set()
        groups[-1].append(event)
        if phase == "press":
            open_controls.add(token)
        elif phase in {"release", "cancel"}:
            open_controls.discard(token)
    return groups


def _camera_group_facts(
    group: list[dict[str, Any]],
    event_time: Callable[[Mapping[str, Any]], int | None],
) -> dict[str, Any]:
    times = [
        timestamp
        for event in group
        if (timestamp := event_time(event)) is not None
    ]
    terminal_payloads = [
        event["payload"]
        for event in group
        if isinstance(event.get("payload"), Mapping)
        and event["payload"].get("phase") in {"release", "cancel"}
    ]
    durations = [
        value
        for payload in terminal_payloads
        if (value := _integer_or_none(payload.get("holdDurationMillis"))) is not None
        and value >= 0
    ]
    distances = [
        float(value)
        for payload in terminal_payloads
        if isinstance((value := payload.get("pathDistancePixels")), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    ]
    return {
        "times": times,
        "startTime": min(times),
        "endTime": max(times),
        "maxControlHoldMillis": max(durations) if durations else None,
        "maxDragPathPixels": round(max(distances), 2) if distances else 0.0,
        "episodeInputSpanMillis": max(times) - min(times),
        "cancelled": any(
            isinstance(event.get("payload"), Mapping)
            and event["payload"].get("phase") == "cancel"
            for event in group
        ),
    }


def _camera_click_association(
    event: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str] | None:
    """Return target, association class, and confidence for review-only linkage."""

    if not _is_semantic_click(event, require_non_consumed=True):
        return None
    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    target = _intent_target(payload, include_activation_kind=True)
    if not isinstance(target, Mapping):
        return None
    family = target.get("actionFamily")
    if family == "tile_object":
        if target.get("resolution") == "exact":
            return dict(target), "action_linked", "high"
        return None
    if family != "walk_tile":
        return None
    if target.get("resolution") == "exact":
        return dict(target), "action_linked", "high"
    if (
        target.get("sourceResolution") == "resolved"
        and target.get("confidence") == "high"
    ):
        return dict(target), "action_linked_candidate", "medium"
    return None


def _derive_camera_intent_episodes_v3(
    events: list[dict[str, Any]],
    *,
    association_semantics_v4: bool = False,
) -> list[dict[str, Any]]:
    """Derive observed camera episodes first, then bounded action associations."""

    event_time = lambda event: _event_time_millis(  # noqa: E731
        event, prefer_monotonic=True
    )
    inputs = [
        event
        for event in events
        if event.get("kind") == "camera_input" and event_time(event) is not None
    ]
    groups = _camera_input_groups(
        inputs,
        event_time,
        preserve_open_controls=association_semantics_v4,
    )
    poses = sorted(
        [
            (timestamp, pose)
            for event in events
            if (timestamp := event_time(event)) is not None
            and (pose := _event_camera_pose(event)) is not None
        ],
        key=lambda value: value[0],
    )
    outcomes: dict[int, Mapping[str, Any]] = {}
    for event in events:
        if event.get("kind") != "interaction_outcome":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        sequence = _integer_or_none(payload.get("clickEventSequence"))
        if sequence is not None:
            outcomes[sequence] = event

    semantic_clicks = sorted(
        [
            event
            for event in events
            if _is_semantic_click(event, require_non_consumed=True)
            and event_time(event) is not None
        ],
        key=lambda event: (
            event_time(event) or -1,
            _integer_or_none(event.get("recorderSequence")) or -1,
        ),
    )
    used_groups: set[int] = set()
    episodes: list[dict[str, Any]] = []
    previous_semantic_click_time: int | None = None
    for click in semantic_clicks:
        click_time = event_time(click)
        association = _camera_click_association(click)
        if click_time is None or association is None:
            previous_semantic_click_time = click_time
            continue
        target, association_class, association_confidence = association
        association_lookback = (
            EXACT_OBJECT_CAMERA_INTENT_LOOKBACK_MILLIS
            if association_semantics_v4
            and target.get("actionFamily") == "tile_object"
            and target.get("resolution") == "exact"
            else CAMERA_INTENT_LOOKBACK_MILLIS
        )
        eligible: list[int] = []
        for index, group in enumerate(groups):
            if index in used_groups or not _same_event_client(group[-1], click):
                continue
            facts = _camera_group_facts(group, event_time)
            if (
                facts["endTime"] <= click_time
                and click_time - facts["endTime"] <= association_lookback
                and (
                    previous_semantic_click_time is None
                    or facts["startTime"] > previous_semantic_click_time
                )
            ):
                eligible.append(index)
        previous_semantic_click_time = click_time
        if not eligible:
            continue
        relevant_inputs = [event for index in eligible for event in groups[index]]
        used_groups.update(eligible)
        facts = [_camera_group_facts(groups[index], event_time) for index in eligible]
        start_time = min(value["startTime"] for value in facts)
        last_input_time = max(value["endTime"] for value in facts)
        start_pose = _event_camera_pose(relevant_inputs[0]) or _pose_for_time(
            poses, start_time
        )
        end_pose = _event_camera_pose(click) or _pose_for_time(poses, click_time)
        delta = _pose_delta(start_pose, end_pose)
        pose_effect = any(value not in (None, 0) for value in delta.values())
        cancelled = any(value["cancelled"] for value in facts)
        ambiguity_reasons: list[str] = []
        if cancelled:
            intent_classification = "cancelled_or_ineffective"
            confidence = "low"
            inference = "cancelled_camera_input_preceded_candidate_action"
            ambiguity_reasons.append("camera input episode was cancelled")
        elif not pose_effect:
            intent_classification = "cancelled_or_ineffective"
            confidence = "low"
            inference = (
                "camera_input_preceded_candidate_action_no_observed_pose_change"
            )
            ambiguity_reasons.append("no observed camera pose change")
        else:
            intent_classification = association_class
            confidence = association_confidence
            inference = (
                "observed_camera_positioning_preceded_exact_action"
                if association_class == "action_linked"
                else "observed_camera_positioning_preceded_high_confidence_walk_candidate"
            )
        click_sequence = _integer_or_none(click.get("recorderSequence"))
        outcome = outcomes.get(click_sequence or -1)
        outcome_payload = (
            outcome.get("payload")
            if isinstance(outcome, Mapping)
            and isinstance(outcome.get("payload"), Mapping)
            else {}
        )
        episodes.append(
            {
                "classification": intent_classification,
                "intentClassification": intent_classification,
                "observedInputMethod": _input_method(relevant_inputs),
                "inference": inference,
                "confidence": confidence,
                "associationConfidence": (
                    association_confidence if pose_effect and not cancelled else "none"
                ),
                "cameraInputEventSequences": [
                    _integer_or_none(event.get("recorderSequence"))
                    for event in relevant_inputs
                ],
                "clickEventSequence": click_sequence,
                "outcomeEventSequence": (
                    _integer_or_none(outcome.get("recorderSequence"))
                    if isinstance(outcome, Mapping)
                    else None
                ),
                "startCameraPose": dict(start_pose) if start_pose else None,
                "clickCameraPose": dict(end_pose) if end_pose else None,
                "cameraPoseDelta": delta,
                "lastCameraInputToClickMillis": max(
                    0, click_time - last_input_time
                ),
                "target": target,
                "observedOutcome": {
                    "changes": list(outcome_payload.get("changes", []))
                    if isinstance(outcome_payload.get("changes"), list)
                    else [],
                    "attributionAmbiguous": bool(
                        outcome_payload.get("attributionAmbiguous", False)
                    ),
                }
                if outcome
                else None,
                "ambiguityReasons": ambiguity_reasons,
                "maxControlHoldMillis": max(
                    (
                        value["maxControlHoldMillis"]
                        for value in facts
                        if value["maxControlHoldMillis"] is not None
                    ),
                    default=None,
                ),
                "maxDragPathPixels": max(
                    value["maxDragPathPixels"] for value in facts
                ),
                "episodeInputSpanMillis": last_input_time - start_time,
                "effectiveCameraChangeObserved": pose_effect,
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        )

    for index, group in enumerate(groups):
        if index in used_groups or len(episodes) >= MAX_DERIVED_CAMERA_EPISODES:
            continue
        facts = _camera_group_facts(group, event_time)
        start_pose = _event_camera_pose(group[0]) or _pose_for_time(
            poses, facts["startTime"]
        )
        end_pose = _event_camera_pose(group[-1]) or _pose_for_time(
            poses, facts["endTime"]
        )
        delta = _pose_delta(start_pose, end_pose)
        pose_effect = any(value not in (None, 0) for value in delta.values())
        ambiguity_reasons: list[str] = []
        if facts["cancelled"]:
            intent_classification = "cancelled_or_ineffective"
            confidence = "low"
            inference = "cancelled_camera_input_without_action_association"
            ambiguity_reasons.append("camera input episode was cancelled")
        elif pose_effect:
            intent_classification = "exploratory_or_unassociated"
            confidence = "high"
            inference = "observed_camera_change_without_bounded_action_association"
        else:
            intent_classification = "cancelled_or_ineffective"
            confidence = "low"
            inference = "camera_input_without_observed_pose_effect_or_action_association"
            ambiguity_reasons.append("no observed camera pose change")
        episodes.append(
            {
                "classification": intent_classification,
                "intentClassification": intent_classification,
                "observedInputMethod": _input_method(group),
                "inference": inference,
                "confidence": confidence,
                "associationConfidence": "none",
                "cameraInputEventSequences": [
                    _integer_or_none(event.get("recorderSequence")) for event in group
                ],
                "clickEventSequence": None,
                "outcomeEventSequence": None,
                "startCameraPose": dict(start_pose) if start_pose else None,
                "endCameraPose": dict(end_pose) if end_pose else None,
                "clickCameraPose": None,
                "cameraPoseDelta": delta,
                "lastCameraInputToClickMillis": None,
                "target": None,
                "observedOutcome": None,
                "ambiguityReasons": ambiguity_reasons,
                "maxControlHoldMillis": facts["maxControlHoldMillis"],
                "maxDragPathPixels": facts["maxDragPathPixels"],
                "episodeInputSpanMillis": facts["episodeInputSpanMillis"],
                "effectiveCameraChangeObserved": pose_effect,
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        )
    episodes.sort(
        key=lambda episode: next(
            (
                value
                for value in episode["cameraInputEventSequences"]
                if value is not None
            ),
            MAX_DERIVED_CAMERA_EPISODES + 1,
        )
    )
    return episodes[:MAX_DERIVED_CAMERA_EPISODES]


def _derive_camera_intent_episodes(
    events: list[dict[str, Any]],
    *,
    modern_semantics: bool = False,
    association_semantics_v3: bool = False,
    association_semantics_v4: bool = False,
) -> list[dict[str, Any]]:
    if association_semantics_v4:
        return _derive_camera_intent_episodes_v3(
            events,
            association_semantics_v4=True,
        )
    if association_semantics_v3:
        return _derive_camera_intent_episodes_v3(events)
    event_time = lambda event: _event_time_millis(  # noqa: E731
        event, prefer_monotonic=modern_semantics
    )
    inputs = [
        event
        for event in events
        if event.get("kind") == "camera_input"
        and event_time(event) is not None
    ]
    clicks = [
        event
        for event in events
        if _is_semantic_click(
            event, require_non_consumed=modern_semantics
        )
        and (
            not modern_semantics
            or _is_exact_camera_intent_click(event)
        )
        and event_time(event) is not None
    ]
    poses = [
        (timestamp, pose)
        for event in events
        if (timestamp := event_time(event)) is not None
        and (pose := _event_camera_pose(event)) is not None
    ]
    outcomes: dict[int, Mapping[str, Any]] = {}
    for event in events:
        if event.get("kind") != "interaction_outcome":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        sequence = _integer_or_none(payload.get("clickEventSequence"))
        if sequence is not None:
            outcomes[sequence] = event

    episodes: list[dict[str, Any]] = []
    consumed_inputs: set[int] = set()
    previous_click_time: int | None = None
    for click in clicks:
        if len(episodes) >= MAX_DERIVED_CAMERA_EPISODES:
            break
        click_time = event_time(click)
        if click_time is None:
            continue
        lower = max(
            click_time - CAMERA_INTENT_LOOKBACK_MILLIS,
            previous_click_time + 1 if previous_click_time is not None else -1,
        )
        relevant_inputs = [
            event
            for event in inputs
            if _same_event_client(event, click)
            and (timestamp := event_time(event)) is not None
            and lower <= timestamp <= click_time
        ]
        relevant_poses = [
            (timestamp, pose)
            for timestamp, pose in poses
            if lower <= timestamp <= click_time
        ]
        pose_changed = (
            len(relevant_poses) >= 2
            and _pose_signature(relevant_poses[0][1])
            != _pose_signature(relevant_poses[-1][1])
        )
        previous_click_time = click_time
        if not relevant_inputs and not pose_changed:
            continue
        times = [
            timestamp
            for event in relevant_inputs
            if (timestamp := event_time(event)) is not None
        ]
        start_time = min(times) if times else relevant_poses[0][0]
        last_input_time = max(times) if times else relevant_poses[-1][0]
        start_pose = _pose_for_time(poses, start_time)
        end_pose = _pose_for_time(poses, click_time)
        delta = _pose_delta(start_pose, end_pose)
        click_payload = click.get("payload") if isinstance(click.get("payload"), Mapping) else {}
        target = _intent_target(click_payload)
        ambiguities: list[str] = []
        if target is None:
            ambiguities.append("click target was not resolved")
        elif target.get("resolution") != "exact":
            reasons = target.get("ambiguityReasons")
            if isinstance(reasons, list):
                ambiguities.extend(_clean_text(reason) for reason in reasons)
            if not ambiguities:
                ambiguities.append("click target resolution was not exact")
        method = _input_method(relevant_inputs)
        sampled_drag_distance = sum(
            math.hypot(
                _integer_or_none(payload.get("deltaX")) or 0,
                _integer_or_none(payload.get("deltaY")) or 0,
            )
            for event in relevant_inputs
            for payload in (
                event.get("payload")
                if isinstance(event.get("payload"), Mapping)
                else {},
            )
            if payload.get("inputKind") == "middle_drag"
            and payload.get("phase") == "drag"
        )
        terminal_payloads = [
            event["payload"]
            for event in relevant_inputs
            if isinstance(event.get("payload"), Mapping)
            and event["payload"].get("phase") in {"release", "cancel"}
        ]
        authoritative_durations = [
            value
            for payload in terminal_payloads
            if (value := _integer_or_none(payload.get("holdDurationMillis")))
            is not None
            and value >= 0
        ]
        authoritative_distances = [
            float(value)
            for payload in terminal_payloads
            if isinstance((value := payload.get("pathDistancePixels")), (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        ]
        input_duration = (
            max(authoritative_durations)
            if authoritative_durations
            else max(0, last_input_time - start_time)
        )
        drag_distance = (
            max(authoritative_distances)
            if authoritative_distances
            else sampled_drag_distance
        )
        click_sequence = _integer_or_none(click.get("recorderSequence"))
        outcome = outcomes.get(click_sequence or -1)
        outcome_payload = (
            outcome.get("payload")
            if isinstance(outcome, Mapping)
            and isinstance(outcome.get("payload"), Mapping)
            else {}
        )
        input_sequences = [
            _integer_or_none(event.get("recorderSequence")) for event in relevant_inputs
        ]
        consumed_inputs.update(
            sequence for sequence in input_sequences if sequence is not None
        )
        pose_effect = any(value not in (None, 0) for value in delta.values())
        cancelled = any(
            isinstance(event.get("payload"), Mapping)
            and event["payload"].get("phase") == "cancel"
            for event in relevant_inputs
        )
        if modern_semantics:
            if cancelled:
                ambiguities.append("camera input episode was cancelled")
                inference = "cancelled_camera_input_preceded_semantic_click"
            elif not relevant_inputs:
                ambiguities.append(
                    "camera pose changed without a correlated candidate control"
                )
                inference = (
                    "camera_pose_changed_without_correlated_candidate_control_"
                    "before_click"
                )
            elif not pose_effect:
                ambiguities.append("no observed camera pose change")
                inference = (
                    "candidate_camera_control_preceded_click_no_observed_pose_change"
                )
            else:
                inference = "camera_positioning_preceded_next_unique_semantic_click"
            classification = (
                method
                if not ambiguities and relevant_inputs and pose_effect and not cancelled
                else "ambiguous"
            )
            confidence = (
                "high"
                if classification != "ambiguous"
                else "low"
            )
        else:
            classification = method if not ambiguities else "ambiguous"
            inference = "camera_positioning_preceded_next_unique_semantic_click"
            confidence = (
                "high"
                if not ambiguities and relevant_inputs and pose_effect
                else "medium"
                if not ambiguities
                else "low"
            )
        episode = {
                "classification": classification,
                "observedInputMethod": method,
                "inference": inference,
                "confidence": confidence,
                "cameraInputEventSequences": input_sequences,
                "clickEventSequence": click_sequence,
                "outcomeEventSequence": (
                    _integer_or_none(outcome.get("recorderSequence"))
                    if isinstance(outcome, Mapping)
                    else None
                ),
                "startCameraPose": dict(start_pose) if start_pose else None,
                "clickCameraPose": dict(end_pose) if end_pose else None,
                "cameraPoseDelta": delta,
                "lastCameraInputToClickMillis": max(0, click_time - last_input_time),
                "target": target,
                "observedOutcome": {
                    "changes": list(outcome_payload.get("changes", []))
                    if isinstance(outcome_payload.get("changes"), list)
                    else [],
                    "attributionAmbiguous": bool(
                        outcome_payload.get("attributionAmbiguous", False)
                    ),
                }
                if outcome
                else None,
                "ambiguityReasons": list(dict.fromkeys(ambiguities)),
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        if modern_semantics:
            episode.update(
                {
                    "maxControlHoldMillis": (
                        max(authoritative_durations)
                        if authoritative_durations
                        else None
                    ),
                    "maxDragPathPixels": round(drag_distance, 2),
                    "episodeInputSpanMillis": (
                        max(times) - min(times) if times else None
                    ),
                    "effectiveCameraChangeObserved": pose_effect,
                }
            )
        else:
            episode.update(
                {
                    "inputDurationMillis": input_duration,
                    "dragDistancePixels": round(drag_distance, 2),
                }
            )
        episodes.append(episode)

    unconsumed = [
        event
        for event in inputs
        if _integer_or_none(event.get("recorderSequence")) not in consumed_inputs
    ]
    groups: list[list[dict[str, Any]]] = []
    for event in unconsumed:
        timestamp = event_time(event)
        if timestamp is None:
            continue
        previous_time = event_time(groups[-1][-1]) if groups else None
        if (
            not groups
            or not _same_event_client(groups[-1][-1], event)
            or previous_time is None
            or timestamp - previous_time > CAMERA_INPUT_JOIN_MILLIS
        ):
            groups.append([event])
        else:
            groups[-1].append(event)
    for group in groups:
        if len(episodes) >= MAX_DERIVED_CAMERA_EPISODES:
            break
        times = [
            timestamp
            for event in group
            if (timestamp := event_time(event)) is not None
        ]
        terminal_payloads = [
            event["payload"]
            for event in group
            if isinstance(event.get("payload"), Mapping)
            and event["payload"].get("phase") in {"release", "cancel"}
        ]
        authoritative_durations = [
            value
            for payload in terminal_payloads
            if (value := _integer_or_none(payload.get("holdDurationMillis")))
            is not None
            and value >= 0
        ]
        authoritative_distances = [
            float(value)
            for payload in terminal_payloads
            if isinstance((value := payload.get("pathDistancePixels")), (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        ]
        episode = {
                "classification": "ambiguous",
                "observedInputMethod": _input_method(group),
                "inference": (
                    "camera_input_without_bounded_exact_walk_or_object_click"
                    if modern_semantics
                    else "camera_input_without_bounded_semantic_click"
                ),
                "confidence": "low",
                "cameraInputEventSequences": [
                    _integer_or_none(event.get("recorderSequence")) for event in group
                ],
                "clickEventSequence": None,
                "outcomeEventSequence": None,
                "startCameraPose": (
                    dict(_pose_for_time(poses, min(times)) or {}) or None
                ),
                "clickCameraPose": None,
                "cameraPoseDelta": {"yaw": None, "pitch": None, "zoom3d": None},
                "lastCameraInputToClickMillis": None,
                "target": None,
                "observedOutcome": None,
                "ambiguityReasons": [
                    (
                        "no exact non-consumed Walk or object click followed "
                        "within the bounded lookback"
                        if modern_semantics
                        else "no unique semantic click followed within the bounded lookback"
                    )
                ],
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        if modern_semantics:
            episode.update(
                {
                    "maxControlHoldMillis": (
                        max(authoritative_durations)
                        if authoritative_durations
                        else None
                    ),
                    "maxDragPathPixels": (
                        round(max(authoritative_distances), 2)
                        if authoritative_distances
                        else 0.0
                    ),
                    "episodeInputSpanMillis": max(times) - min(times),
                    "effectiveCameraChangeObserved": False,
                }
            )
        else:
            episode.update(
                {
                    "inputDurationMillis": (
                        max(authoritative_durations)
                        if authoritative_durations
                        else max(times) - min(times)
                    ),
                    "dragDistancePixels": (
                        round(max(authoritative_distances), 2)
                        if authoritative_distances
                        else 0.0
                    ),
                }
            )
        episodes.append(episode)
    return episodes


def _menu_entry_matches_click(
    entry: Mapping[str, Any], click_payload: Mapping[str, Any]
) -> bool:
    return all(
        (
            _clean_text(entry.get(name)) == _clean_text(click_payload.get(name))
            if name in {"option", "target", "type"}
            else _integer_or_none(entry.get(name))
            == _integer_or_none(click_payload.get(name))
        )
        for name in ("option", "target", "type", "identifier", "param0", "param1")
    )


def _context_menu_open_to_click_millis(
    hovers: list[dict[str, Any]],
    click: Mapping[str, Any],
    click_time: int,
    event_time: Callable[[Mapping[str, Any]], int | None],
) -> int | None:
    """Return a conservative lower bound from contiguous menu-open evidence."""

    click_payload = (
        click.get("payload") if isinstance(click.get("payload"), Mapping) else {}
    )
    resolved = (
        click_payload.get("resolvedTarget")
        if isinstance(click_payload.get("resolvedTarget"), Mapping)
        else {}
    )
    if resolved.get("activationKind") != "context_menu_row":
        return None
    bounded_hovers = sorted(
        (
            (timestamp, hover)
            for hover in hovers
            if (timestamp := event_time(hover)) is not None
            and click_time - CONTEXT_MENU_TIMING_LOOKBACK_MILLIS
            <= timestamp
            <= click_time
        ),
        key=lambda item: (
            item[0],
            _integer_or_none(item[1].get("recorderSequence")) or -1,
        ),
    )
    earliest: int | None = None
    previous_time = click_time
    for timestamp, hover in reversed(bounded_hovers):
        if previous_time - timestamp > CONTEXT_MENU_TIMING_JOIN_MILLIS:
            break
        if not _same_event_client(hover, click):
            break
        payload = (
            hover.get("payload")
            if isinstance(hover.get("payload"), Mapping)
            else {}
        )
        entries = payload.get("entries")
        if (
            payload.get("menuOpen") is not True
            or not isinstance(entries, list)
            or not any(
                isinstance(entry, Mapping)
                and _menu_entry_matches_click(entry, click_payload)
                for entry in entries
            )
        ):
            break
        earliest = timestamp
        previous_time = timestamp
    return max(0, click_time - earliest) if earliest is not None else None


def _derive_timing_profiles(
    events: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    *,
    modern_semantics: bool = False,
    context_menu_semantics: bool = False,
) -> list[dict[str, Any]]:
    event_time = lambda event: _event_time_millis(  # noqa: E731
        event, prefer_monotonic=modern_semantics
    )
    episode_by_click = {
        _integer_or_none(episode.get("clickEventSequence")): episode
        for episode in episodes
        if _integer_or_none(episode.get("clickEventSequence")) is not None
    }
    pointers = [event for event in events if event.get("kind") == "pointer_sample"]
    hovers = [event for event in events if event.get("kind") == "hover_menu"]
    profiles: list[dict[str, Any]] = []
    for click in (
        event
        for event in events
        if _is_semantic_click(
            event, require_non_consumed=modern_semantics
        )
    ):
        click_time = event_time(click)
        click_sequence = _integer_or_none(click.get("recorderSequence"))
        if click_time is None or click_sequence is None:
            continue
        click_payload = click.get("payload") if isinstance(click.get("payload"), Mapping) else {}
        click_pointer = (
            click_payload.get("pointer")
            if isinstance(click_payload.get("pointer"), Mapping)
            else {}
        )
        click_point = (
            _integer_or_none(click_pointer.get("canvasX")),
            _integer_or_none(click_pointer.get("canvasY")),
        )
        timed_pointers = [
            (timestamp, event)
            for event in pointers
            if _same_event_client(event, click)
            and (timestamp := event_time(event)) is not None
            and click_time - CAMERA_INTENT_LOOKBACK_MILLIS <= timestamp <= click_time
        ]
        timed_pointers.sort(
            key=lambda item: (
                item[0],
                _integer_or_none(item[1].get("recorderSequence")) or -1,
            )
        )
        pointer_duration: int | None = None
        settle: int | None = None
        if timed_pointers:
            if modern_semantics:
                points = [
                    (
                        _integer_or_none(event.get("payload", {}).get("canvasX")),
                        _integer_or_none(event.get("payload", {}).get("canvasY")),
                    )
                    for _, event in timed_pointers
                ]
                changes = [
                    index
                    for index in range(1, len(points))
                    if points[index] != points[index - 1]
                ]
                if changes:
                    chain = [changes[-1]]
                    for index in reversed(changes[:-1]):
                        if (
                            timed_pointers[chain[0]][0]
                            - timed_pointers[index][0]
                            > 250
                        ):
                            break
                        chain.insert(0, index)
                    start_index = chain[0] - 1
                    if (
                        timed_pointers[chain[0]][0]
                        - timed_pointers[start_index][0]
                        > 250
                    ):
                        start_index = chain[0]
                    pointer_duration = max(
                        0,
                        timed_pointers[chain[-1]][0]
                        - timed_pointers[start_index][0],
                    )
            else:
                suffix = [timed_pointers[-1]]
                for candidate in reversed(timed_pointers[:-1]):
                    if suffix[0][0] - candidate[0] > 250:
                        break
                    suffix.insert(0, candidate)
                first_payload = suffix[0][1].get("payload", {})
                last_payload = suffix[-1][1].get("payload", {})
                first_point = (
                    _integer_or_none(first_payload.get("canvasX")),
                    _integer_or_none(first_payload.get("canvasY")),
                )
                last_point = (
                    _integer_or_none(last_payload.get("canvasX")),
                    _integer_or_none(last_payload.get("canvasY")),
                )
                if first_point != last_point:
                    pointer_duration = max(0, suffix[-1][0] - suffix[0][0])
            differing = [
                timestamp
                for timestamp, event in timed_pointers
                for payload in (
                    event.get("payload")
                    if isinstance(event.get("payload"), Mapping)
                    else {},
                )
                if (
                    _integer_or_none(payload.get("canvasX")),
                    _integer_or_none(payload.get("canvasY")),
                )
                != click_point
            ]
            if differing:
                settle = max(0, click_time - max(differing))
        matching_hovers: list[int] = []
        for hover in hovers:
            if not _same_event_client(hover, click):
                continue
            hover_time = event_time(hover)
            if hover_time is None or not click_time - 1_000 <= hover_time <= click_time:
                continue
            hover_payload = (
                hover.get("payload")
                if isinstance(hover.get("payload"), Mapping)
                else {}
            )
            hovered = hover_payload.get("hoveredTarget")
            if not isinstance(hovered, Mapping):
                continue
            identity_fields = (
                ("option", "target", "type", "identifier", "param0", "param1")
                if modern_semantics
                else ("option", "target")
            )
            if all(
                (
                    _clean_text(hovered.get(name))
                    == _clean_text(click_payload.get(name))
                )
                if name in {"option", "target", "type"}
                else _integer_or_none(hovered.get(name))
                == _integer_or_none(click_payload.get(name))
                for name in identity_fields
            ):
                matching_hovers.append(hover_time)
        episode = episode_by_click.get(click_sequence)
        resolved = _intent_target(click_payload)
        profile = {
                "clickEventSequence": click_sequence,
                "actionFamily": (
                    resolved.get("actionFamily")
                    if isinstance(resolved, Mapping)
                    else "unresolved"
                ),
                "inputMethod": (
                    episode.get("observedInputMethod") if episode is not None else None
                ),
                "lastCameraInputToClickMillis": (
                    episode.get("lastCameraInputToClickMillis")
                    if episode is not None
                    else None
                ),
                "pointerMovementDurationMillis": pointer_duration,
                "settleMillis": settle,
                "hoverToClickMillis": (
                    max(0, click_time - max(matching_hovers))
                    if matching_hovers
                    else None
                ),
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        if modern_semantics:
            profile.update(
                {
                    "maxControlHoldMillis": (
                        episode.get("maxControlHoldMillis")
                        if episode is not None
                        else None
                    ),
                    "cameraInputSpanMillis": (
                        episode.get("episodeInputSpanMillis")
                        if episode is not None
                        else None
                    ),
                }
            )
        else:
            profile["cameraInputDurationMillis"] = (
                episode.get("inputDurationMillis") if episode is not None else None
            )
        if context_menu_semantics:
            profile.update(
                {
                    # Retained for compatibility: this is the age of the
                    # freshest exact hoveredTarget observation, not dwell.
                    "hoverToClickSemantics": "last_matching_hover_observation_age",
                    "contextMenuOpenToClickMillis": (
                        _context_menu_open_to_click_millis(
                            hovers,
                            click,
                            click_time,
                            event_time,
                        )
                    ),
                    "contextMenuOpenToClickSemantics": (
                        "contiguous_menu_open_evidence_lower_bound"
                    ),
                }
            )
        profiles.append(profile)
    return profiles[:MAX_DERIVED_CAMERA_EPISODES]


def _manual_route_observation_event_before(
    events: list[dict[str, Any]], click_sequence: int
) -> Mapping[str, Any] | None:
    candidate: Mapping[str, Any] | None = None
    for event in events:
        sequence = _integer_or_none(event.get("recorderSequence"))
        if sequence is None or sequence >= click_sequence:
            break
        if event.get("kind") == "observation" and isinstance(
            event.get("payload"), Mapping
        ):
            candidate = event
    return candidate


def _scene_target_world(
    scene: Mapping[str, Any], observation: Mapping[str, Any] | None
) -> dict[str, int] | None:
    if not isinstance(observation, Mapping):
        return None
    player = (
        observation.get("player")
        if isinstance(observation.get("player"), Mapping)
        else {}
    )
    player_world = _world_from_mapping(
        player.get("world") if isinstance(player.get("world"), Mapping) else {}
    )
    player_scene = (
        player.get("scene") if isinstance(player.get("scene"), Mapping) else {}
    )
    scene_x = _integer_or_none(scene.get("x"))
    scene_y = _integer_or_none(scene.get("y"))
    player_scene_x = _integer_or_none(player_scene.get("x"))
    player_scene_y = _integer_or_none(player_scene.get("y"))
    if (
        player_world is None
        or scene_x is None
        or scene_y is None
        or player_scene_x is None
        or player_scene_y is None
        or not 0 <= scene_x <= 103
        or not 0 <= scene_y <= 103
    ):
        return None
    return {
        "x": player_world["x"] + scene_x - player_scene_x,
        "y": player_world["y"] + scene_y - player_scene_y,
        "plane": player_world["plane"],
    }


def _derive_manual_route_targets(
    events: list[dict[str, Any]],
    *,
    semantics_v2: bool = False,
) -> list[dict[str, Any]]:
    """Keep manual Walk intent separate from sampled player route positions."""

    targets: list[dict[str, Any]] = []
    semantic_sequences = [
        _integer_or_none(event.get("recorderSequence"))
        for event in events
        if _is_semantic_click(event, require_non_consumed=True)
    ]
    for event in events:
        if len(targets) >= MAX_DERIVED_MANUAL_ROUTE_TARGETS:
            break
        if not _is_semantic_click(event, require_non_consumed=True):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        option = _clean_text(payload.get("option"))
        entry_type = _clean_text(payload.get("type"))
        if entry_type.upper() != "WALK" and option.casefold() != "walk here":
            continue
        sequence = _integer_or_none(event.get("recorderSequence"))
        if sequence is None:
            continue
        resolved = (
            payload.get("resolvedTarget")
            if isinstance(payload.get("resolvedTarget"), Mapping)
            else {}
        )
        observation_event = _manual_route_observation_event_before(events, sequence)
        observation = (
            observation_event.get("payload")
            if isinstance(observation_event, Mapping)
            and isinstance(observation_event.get("payload"), Mapping)
            else None
        )
        player = (
            observation.get("player")
            if isinstance(observation, Mapping)
            and isinstance(observation.get("player"), Mapping)
            else {}
        )
        player_world = _world_from_mapping(
            player.get("world") if isinstance(player.get("world"), Mapping) else {}
        )
        player_scene = (
            dict(player["scene"])
            if isinstance(player.get("scene"), Mapping)
            else None
        )
        target_sources: dict[str, Any] = {}
        for name in (
            "selectedSceneTile",
            "menuParamTile",
            "worldTile",
            "localDestinationTile",
        ):
            if isinstance(resolved.get(name), Mapping):
                target_sources[name] = dict(resolved[name])
        tile = resolved.get("tile") if isinstance(resolved.get("tile"), Mapping) else {}
        if isinstance(tile.get("world"), Mapping):
            target_sources["tileWorld"] = dict(tile["world"])

        chosen_world: dict[str, int] | None = None
        chosen_source: str | None = None
        selected = target_sources.get("selectedSceneTile")
        if isinstance(selected, Mapping):
            selected_x = _integer_or_none(selected.get("x"))
            selected_y = _integer_or_none(selected.get("y"))
            selected_is_world = bool(
                _integer_or_none(selected.get("plane")) is not None
                and selected_x is not None
                and selected_y is not None
                and (selected_x > 103 or selected_y > 103)
            )
            chosen_world = (
                _world_from_mapping(selected)
                if selected_is_world
                else _scene_target_world(selected, observation)
            )
            if chosen_world is not None:
                chosen_source = (
                    "selectedSceneTile"
                    if selected_is_world
                    else "selectedSceneTile+observationSceneOrigin"
                )
        if chosen_world is None and isinstance(target_sources.get("tileWorld"), Mapping):
            chosen_world = _world_from_mapping(target_sources["tileWorld"])
            chosen_source = "tile.world" if chosen_world is not None else None
        if chosen_world is None and isinstance(
            target_sources.get("localDestinationTile"), Mapping
        ):
            chosen_world = _world_from_mapping(target_sources["localDestinationTile"])
            chosen_source = (
                "localDestinationTile" if chosen_world is not None else None
            )
        if chosen_world is None and isinstance(target_sources.get("worldTile"), Mapping):
            chosen_world = _world_from_mapping(target_sources["worldTile"])
            chosen_source = "worldTile" if chosen_world is not None else None
        if chosen_world is None and isinstance(
            target_sources.get("menuParamTile"), Mapping
        ):
            chosen_world = _scene_target_world(
                target_sources["menuParamTile"], observation
            )
            if chosen_world is not None:
                chosen_source = "menuParamTile+observationSceneOrigin"

        pointer = (
            payload.get("pointer")
            if isinstance(payload.get("pointer"), Mapping)
            else {}
        )
        intent_target = _intent_target(payload, include_activation_kind=True)
        distance_from_observed_player: float | None = None
        if (
            player_world is not None
            and chosen_world is not None
            and player_world["plane"] == chosen_world["plane"]
        ):
            distance_from_observed_player = round(
                math.hypot(
                    chosen_world["x"] - player_world["x"],
                    chosen_world["y"] - player_world["y"],
                ),
                2,
            )
        click_source_tick = _integer_or_none(
            event.get("source", {}).get("sourceTick")
            if isinstance(event.get("source"), Mapping)
            else None
        )
        player_sample_source_tick = _integer_or_none(
            observation_event.get("source", {}).get("sourceTick")
            if isinstance(observation_event, Mapping)
            and isinstance(observation_event.get("source"), Mapping)
            else None
        )
        player_sample_age_ticks = (
            click_source_tick - player_sample_source_tick
            if click_source_tick is not None
            and player_sample_source_tick is not None
            and click_source_tick >= player_sample_source_tick
            else None
        )
        same_source_tick_player = player_sample_age_ticks == 0
        near_source_tick_player = player_sample_age_ticks == 1
        reported_target_source = (
            intent_target.get("source") if isinstance(intent_target, Mapping) else None
        )
        target_source = (
            chosen_source
            if semantics_v2 and chosen_source is not None
            else reported_target_source
        )
        requested_distance = (
            distance_from_observed_player
            if not semantics_v2
            or same_source_tick_player
            or near_source_tick_player
            else None
        )
        target = {
                "clickEventSequence": sequence,
                "sourceTick": _integer_or_none(
                    event.get("source", {}).get("sourceTick")
                    if isinstance(event.get("source"), Mapping)
                    else None
                ),
                "eventTimeMillis": _event_time_millis(
                    event, prefer_monotonic=True
                ),
                "targetResolution": (
                    intent_target.get("resolution")
                    if isinstance(intent_target, Mapping)
                    else "unresolved"
                ),
                "targetConfidence": (
                    intent_target.get("confidence")
                    if isinstance(intent_target, Mapping)
                    else None
                ),
                "targetSource": target_source,
                "targetSourceFields": target_sources,
                "chosenTargetWorld": chosen_world,
                "chosenTargetSource": chosen_source,
                "playerWorldAtClick": player_world,
                "playerSceneAtClick": player_scene,
                "requestedTileDistance": requested_distance,
                "activationCanvasPoint": {
                    "x": _integer_or_none(pointer.get("canvasX")),
                    "y": _integer_or_none(pointer.get("canvasY")),
                },
                "interpretation": "manual_walk_target_candidate",
                "reviewOnly": True,
                "automaticConfigurationAllowed": False,
            }
        if semantics_v2:
            target.update(
                {
                    "reportedResolvedTargetSource": reported_target_source,
                    "selectedTargetCoordinateSpace": (
                        "world"
                        if chosen_source == "selectedSceneTile"
                        else "scene_plus_observed_origin"
                        if chosen_source
                        == "selectedSceneTile+observationSceneOrigin"
                        else "world"
                        if chosen_source in {"tile.world", "localDestinationTile", "worldTile"}
                        else "scene_plus_observed_origin"
                        if chosen_source == "menuParamTile+observationSceneOrigin"
                        else None
                    ),
                    "distanceFromLastObservedPlayer": distance_from_observed_player,
                    "playerSampleSourceTick": player_sample_source_tick,
                    "playerSampleAgeTicks": player_sample_age_ticks,
                    "playerWorldAtClickSemantics": (
                        "same_source_tick_player_world"
                        if same_source_tick_player
                        else "latest_prior_observed_player_world"
                        if player_sample_age_ticks is not None
                        else "observation_age_unknown"
                    ),
                    "requestedTileDistanceStatus": (
                        "same_source_tick_player_sample"
                        if same_source_tick_player
                        else "near_source_tick_player_sample_estimate"
                        if near_source_tick_player
                        else "not_claimed_from_prior_player_sample"
                        if player_sample_age_ticks is not None
                        else "not_claimed_player_sample_age_unknown"
                    ),
                }
            )
        targets.append(target)

    for previous, current in zip(targets, targets[1:]):
        previous_world = previous.get("chosenTargetWorld")
        current_world = current.get("chosenTargetWorld")
        if (
            isinstance(previous_world, Mapping)
            and isinstance(current_world, Mapping)
            and _integer_or_none(previous_world.get("plane"))
            == _integer_or_none(current_world.get("plane"))
        ):
            previous_x = _integer_or_none(previous_world.get("x"))
            previous_y = _integer_or_none(previous_world.get("y"))
            current_x = _integer_or_none(current_world.get("x"))
            current_y = _integer_or_none(current_world.get("y"))
            if None not in (previous_x, previous_y, current_x, current_y):
                current["distanceFromPreviousManualTarget"] = round(
                    math.hypot(current_x - previous_x, current_y - previous_y),
                    2,
                )
        previous_time = _integer_or_none(previous.get("eventTimeMillis"))
        current_time = _integer_or_none(current.get("eventTimeMillis"))
        previous_sequence = _integer_or_none(previous.get("clickEventSequence"))
        current_sequence = _integer_or_none(current.get("clickEventSequence"))
        if None in (
            previous_time,
            current_time,
            previous_sequence,
            current_sequence,
        ):
            continue
        elapsed = current_time - previous_time
        intervening = any(
            sequence is not None and previous_sequence < sequence < current_sequence
            for sequence in semantic_sequences
        )
        if (
            elapsed < 0
            or elapsed > MANUAL_WALK_QUICK_FOLLOWUP_MILLIS
            or intervening
            or previous.get("chosenTargetWorld") == current.get("chosenTargetWorld")
        ):
            continue
        previous["quickFollowup"] = {
            "classification": "possible_quick_followup",
            "nextClickEventSequence": current_sequence,
            "intervalMillis": elapsed,
            "interpretation": (
                "may be a correction, refinement, or ordinary follow-up; "
                "the evidence does not label either click a mistake"
            ),
            "reviewRequired": True,
        }
        current["possiblySupersedesClickEventSequence"] = previous_sequence
    return targets


def _derive_summary(
    events: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> InspectionResult:
    coverage = manifest.get("evidenceCoverage")
    modern_camera_semantics = bool(
        isinstance(coverage, Mapping)
        and coverage.get("cameraIntentSemanticsV2") is True
    )
    camera_association_semantics_v3 = bool(
        isinstance(coverage, Mapping)
        and coverage.get("cameraIntentSemanticsV3") is True
    )
    camera_association_semantics_v4 = bool(
        isinstance(coverage, Mapping)
        and coverage.get("cameraIntentSemanticsV4") is True
    )
    context_menu_timing_semantics = bool(
        isinstance(coverage, Mapping)
        and coverage.get("contextMenuTimingSemanticsV1") is True
    )
    manual_route_evidence_included = bool(
        isinstance(coverage, Mapping)
        and coverage.get("manualRouteIntentSemanticsV1") is True
    )
    manual_route_semantics_v2 = bool(
        isinstance(coverage, Mapping)
        and coverage.get("manualRouteIntentSemanticsV2") is True
    )
    route_points: list[dict[str, int]] = []
    interacted_entities: list[dict[str, Any]] = []
    selected_options: list[str] = []
    state_changes: list[dict[str, Any]] = []
    semantic: list[str] = []
    ambiguities: list[str] = []
    gaps: list[str] = []
    clicks: dict[int, dict[str, Any]] = {}
    seen_entity: set[str] = set()
    seen_change: set[str] = set()

    for event in events:
        kind = event.get("kind")
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
        sequence = _integer_or_none(event.get("recorderSequence"))
        if kind == "observation":
            player = payload.get("player") if isinstance(payload.get("player"), Mapping) else {}
            world = _world_from_mapping(
                player.get("world") if isinstance(player.get("world"), Mapping) else {}
            )
            tick = _integer_or_none(source.get("sourceTick"))
            if world is not None and tick is not None:
                key = (world["x"], world["y"], world["plane"])
                if not route_points or key != tuple(
                    route_points[-1][name] for name in ("x", "y", "plane")
                ):
                    route_points.append(
                        {**world, "sourceTick": tick, "lastSourceTick": tick}
                    )
                else:
                    route_points[-1]["lastSourceTick"] = tick
            actor_meta = (
                payload.get("actorCensusMeta")
                if isinstance(payload.get("actorCensusMeta"), Mapping)
                else {}
            )
            if actor_meta.get("capHit") is True:
                gaps.append("NPC actor census cap reached")
            if _integer_or_none(actor_meta.get("count")) is None:
                gaps.append("NPC actor census count was unavailable")
            scene_meta = (
                payload.get("sceneObjectCensusMeta")
                if isinstance(payload.get("sceneObjectCensusMeta"), Mapping)
                else {}
            )
            if scene_meta.get("capHit") is True:
                gaps.append("scene object census cap reached")
            if scene_meta.get("objectCensusCapHit") is True:
                gaps.append("scene object acquisition cap reached")
            if _integer_or_none(scene_meta.get("count")) is None:
                gaps.append("scene object census count was unavailable")
            collision_meta = (
                payload.get("collisionWindowMeta")
                if isinstance(payload.get("collisionWindowMeta"), Mapping)
                else {}
            )
            if collision_meta.get("collisionAvailable") is not True:
                gaps.append("collision window was unavailable")
            if collision_meta.get("cellCapHit") is True:
                gaps.append("collision window cell cap reached")
            if _integer_or_none(collision_meta.get("cellCount")) is None:
                gaps.append("collision window cell count was unavailable")
        elif kind == "hover_menu":
            if payload.get("entryCapHit") is True:
                gaps.append("hover menu entry cap reached")
            if _integer_or_none(payload.get("entryCount")) is None:
                gaps.append("hover menu source entry count was unavailable")
        elif kind == "menu_option_clicked" and sequence is not None:
            clicks[sequence] = event
            option = _clean_text(payload.get("option"))
            target = _clean_text(payload.get("target"))
            identifier = _integer_or_none(payload.get("identifier"))
            entry_type = _clean_text(payload.get("type"))
            plane = _integer_or_none(source.get("plane"))
            is_walk = entry_type.upper() == "WALK" or option.casefold() == "walk here"
            if not _is_semantic_click(
                event, require_non_consumed=modern_camera_semantics
            ):
                if option and option.casefold() != "cancel":
                    ambiguities.append(
                        f"click event {sequence} was consumed or lacks explicit "
                        "non-consumed evidence"
                    )
                continue
            if option and option not in selected_options:
                selected_options.append(option)
            if not is_walk and (not option or not target or identifier is None):
                ambiguities.append(
                    f"click event {sequence} lacks a complete option, target, or identifier"
                )
            if not is_walk:
                stable = (
                    payload.get("entityEvidence")
                    if isinstance(payload.get("entityEvidence"), Mapping)
                    else {}
                )
                if (
                    "NPC" in entry_type.upper()
                    and "PLAYER" not in entry_type.upper()
                    and not stable
                ):
                    ambiguities.append(
                        f"NPC menu index in click event {sequence} was not "
                        "correlated to a same-tick census ID"
                    )
                entity = {
                    "name": _clean_text(stable.get("name")) or target or None,
                    "menuIdentifier": identifier,
                    "stableEntityId": _integer_or_none(
                        stable.get("stableEntityId")
                    ),
                    "entityKind": _clean_text(stable.get("kind")) or None,
                    "type": entry_type or None,
                    "action": option or None,
                    "plane": plane,
                    "identitySource": _clean_text(stable.get("source")) or None,
                }
                if _clean_text(stable.get("objectKey")):
                    entity["objectKey"] = _clean_text(stable.get("objectKey"))
                if isinstance(stable.get("world"), Mapping):
                    entity["world"] = dict(stable["world"])
                if _clean_text(stable.get("resolution")):
                    entity["resolution"] = _clean_text(stable.get("resolution"))
                if _clean_text(stable.get("geometryFrameId")):
                    entity["geometryFrameId"] = _clean_text(
                        stable.get("geometryFrameId")
                    )
                key = json.dumps(entity, sort_keys=True, separators=(",", ":"))
                if key not in seen_entity:
                    seen_entity.add(key)
                    interacted_entities.append(entity)
        elif kind == "interaction_outcome":
            click_sequence = _integer_or_none(payload.get("clickEventSequence"))
            values = payload.get("changes", [])
            changes = [value for value in values if isinstance(value, Mapping)] if isinstance(values, list) else []
            for value in changes:
                normalized = dict(value)
                normalized["clickEventSequence"] = click_sequence
                key = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
                if key not in seen_change:
                    seen_change.add(key)
                    state_changes.append(normalized)
            if payload.get("attributionAmbiguous") is True:
                click_sequences = payload.get("clickEventSequences", [])
                ambiguities.append(
                    "multiple clicks occurred before one after-observation; "
                    f"state changes are not uniquely attributable to {click_sequences}"
                )
                continue
            click = clicks.get(click_sequence or -1)
            if click is None:
                ambiguities.append(
                    f"outcome references missing click event {click_sequence}"
                )
                continue
            if not _is_semantic_click(
                click, require_non_consumed=modern_camera_semantics
            ):
                continue
            sentence = _semantic_interaction_sentence(click, changes)
            if sentence and sentence not in semantic:
                semantic.append(sentence)
            if not changes:
                ambiguities.append(
                    f"click event {click_sequence} has an after-observation but no detected state change"
                )
        elif kind == "coverage_gap":
            code = _clean_text(payload.get("code")) or "unspecified_coverage_gap"
            detail = code
            lane = _clean_text(payload.get("lane"))
            if lane:
                detail += f" ({lane})"
            if detail not in gaps:
                gaps.append(detail)

    outcome_clicks: set[int | None] = set()
    for event in events:
        if event.get("kind") != "interaction_outcome" or not isinstance(
            event.get("payload"), Mapping
        ):
            continue
        outcome_payload = event["payload"]
        outcome_clicks.add(
            _integer_or_none(outcome_payload.get("clickEventSequence"))
        )
        values = outcome_payload.get("clickEventSequences", [])
        if isinstance(values, list):
            outcome_clicks.update(_integer_or_none(value) for value in values)
    for sequence in clicks:
        if sequence not in outcome_clicks:
            ambiguities.append(f"click event {sequence} has no after-observation outcome")

    include_intent_evidence = bool(
        isinstance(coverage, Mapping)
        and coverage.get("cameraIntentDerivation") is True
    )
    if isinstance(coverage, Mapping):
        if coverage.get("rawMouseButtonTransitions") is False:
            gaps.append(
                "global raw mouse-button transitions were not observable; "
                "whitelisted middle-camera gestures may still be present"
                if coverage.get("whitelistedCameraInput") is True
                else "raw mouse-button transitions were not observable"
            )
        if coverage.get("rawKeyboardEvents") is False:
            gaps.append(
                "global raw keyboard events were not observable; whitelisted "
                "camera-key transitions may still be present"
                if coverage.get("whitelistedCameraInput") is True
                else "raw keyboard events were not observable"
            )
    if not route_points:
        ambiguities.append("no authoritative player route points were recorded")

    suggestions = _candidate_suggestions(
        route_points,
        interacted_entities,
        state_changes,
        exact_interactions_only=camera_association_semantics_v3,
    )
    camera_episodes = (
        _derive_camera_intent_episodes(
            events,
            modern_semantics=modern_camera_semantics,
            association_semantics_v3=camera_association_semantics_v3,
            association_semantics_v4=camera_association_semantics_v4,
        )
        if include_intent_evidence
        else []
    )
    camera_review_episodes = (
        camera_episodes
        if camera_association_semantics_v4
        else _derive_camera_intent_episodes_v3(
            events,
            association_semantics_v4=True,
        )
    )
    camera_review_episodes = enrich_camera_review_episodes(
        events,
        camera_review_episodes,
    )
    # This property is derived for verified legacy artifacts too so an
    # application-owned comparison view can inspect existing demonstrations.
    # Serialization remains opt-in, preserving their stored summary/timeline.
    manual_route_targets = _derive_manual_route_targets(
        events,
        semantics_v2=manual_route_semantics_v2,
    )
    manual_route_review_targets = (
        manual_route_targets
        if manual_route_semantics_v2
        else _derive_manual_route_targets(events, semantics_v2=True)
    )
    timing_profiles = (
        _derive_timing_profiles(
            events,
            camera_episodes,
            modern_semantics=modern_camera_semantics,
            context_menu_semantics=context_menu_timing_semantics,
        )
        if include_intent_evidence
        else []
    )
    timing_review_profiles = (
        timing_profiles
        if context_menu_timing_semantics and camera_association_semantics_v4
        else _derive_timing_profiles(
            events,
            camera_review_episodes,
            modern_semantics=True,
            context_menu_semantics=True,
        )
        if include_intent_evidence
        else []
    )
    gaps = list(dict.fromkeys(gaps))
    ambiguities = list(dict.fromkeys(ambiguities))
    status = "VERIFIED_WITH_GAPS" if gaps or ambiguities else "VERIFIED"
    return InspectionResult(
        valid=True,
        status=status,
        semantic_summary=tuple(semantic),
        route_points=tuple(route_points),
        interacted_entities=tuple(interacted_entities),
        selected_menu_options=tuple(selected_options),
        state_changes=tuple(state_changes),
        ambiguities=tuple(ambiguities),
        coverage_gaps=tuple(gaps),
        candidate_suggestions=tuple(suggestions),
        camera_intent_episodes=tuple(camera_episodes),
        camera_review_episodes=tuple(camera_review_episodes),
        timing_profiles=tuple(timing_profiles),
        timing_review_profiles=tuple(timing_review_profiles),
        manual_route_targets=tuple(manual_route_targets),
        manual_route_review_targets=tuple(manual_route_review_targets),
        intent_evidence_included=include_intent_evidence,
        manual_route_evidence_included=manual_route_evidence_included,
        stop_reason=_clean_text(manifest.get("stopReason")) or None,
        requested_duration_seconds=(
            float(manifest["requestedDurationSeconds"])
            if isinstance(manifest.get("requestedDurationSeconds"), (int, float))
            and not isinstance(manifest.get("requestedDurationSeconds"), bool)
            else None
        ),
    )


def _semantic_interaction_sentence(
    click: Mapping[str, Any], changes: list[Mapping[str, Any]]
) -> str:
    payload = click.get("payload") if isinstance(click.get("payload"), Mapping) else {}
    source = click.get("source") if isinstance(click.get("source"), Mapping) else {}
    target = _clean_text(payload.get("target")) or "unknown target"
    identifier = _integer_or_none(payload.get("identifier"))
    option = _clean_text(payload.get("option")) or "unknown option"
    entry_type = _clean_text(payload.get("type")).upper()
    plane = _integer_or_none(source.get("plane"))
    if entry_type == "WALK" or option.casefold() == "walk here":
        start = "selected Walk here"
    else:
        identity = f"{target} {identifier}" if identifier is not None else target
        start = f"clicked {identity} with {option}"
    if plane is not None:
        start += f" at plane {plane}"
    plane_change = next(
        (value for value in changes if value.get("field") == "player.plane"),
        None,
    )
    if plane_change is not None and _integer_or_none(plane_change.get("after")) is not None:
        return f"{start}, then observed plane {int(plane_change['after'])}"
    world_change = next(
        (value for value in changes if value.get("field") == "player.world"),
        None,
    )
    if world_change is not None and isinstance(world_change.get("after"), Mapping):
        world = _world_from_mapping(world_change["after"])
        if world is not None:
            return (
                f"{start}, then observed world tile "
                f"{world['x']},{world['y']},{world['plane']}"
            )
    if any(value.get("field", "").startswith("inventory.") for value in changes):
        return f"{start}, then observed an inventory change"
    if any(value.get("field", "").startswith("interfaces.") for value in changes):
        return f"{start}, then observed an interface change"
    return f"{start}, then observed no modeled state change"


def _candidate_suggestions(
    route_points: list[dict[str, int]],
    interacted_entities: list[dict[str, Any]],
    state_changes: list[dict[str, Any]],
    *,
    exact_interactions_only: bool = False,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for point in _representative_route_points(route_points, 64):
        suggestions.append(
            {
                "kind": "route_anchor_candidate",
                "world": {key: point[key] for key in ("x", "y", "plane")},
                "sourceTick": point["sourceTick"],
                "reviewRequired": True,
                "activation": "never_automatic",
            }
        )
    for entity in interacted_entities[:32]:
        stable_id = _integer_or_none(entity.get("stableEntityId"))
        kind = _clean_text(entity.get("entityKind"))
        name = _clean_text(entity.get("name"))
        action = _clean_text(entity.get("action"))
        exact_identity = entity.get("resolution") == "exact" or (
            kind == "NPC"
            and entity.get("identitySource") == "actor_census_index_correlation"
            and stable_id is not None
        )
        if (
            stable_id is None
            or stable_id <= 0
            or kind not in {"GAME_OBJECT", "NPC"}
            or not name
            or not action
            or action.casefold() in {"walk here", "examine"}
            or (exact_interactions_only and not exact_identity)
        ):
            continue
        suggestions.append(
            {
                "kind": "interaction_fact_candidate",
                "entity": {
                    "name": name,
                    "stableId": stable_id,
                    "kind": kind,
                },
                "action": action,
                "sourcePlane": entity.get("plane"),
                "identitySource": entity.get("identitySource"),
                **(
                    {"identityResolution": "exact"}
                    if exact_interactions_only
                    else {}
                ),
                "reviewRequired": True,
                "activation": "never_automatic",
            }
        )
    for change in state_changes[:32]:
        if change.get("field") != "player.plane" or _integer_or_none(
            change.get("clickEventSequence")
        ) is None:
            continue
        suggestions.append(
            {
                "kind": "plane_transition_candidate",
                "fromPlane": change.get("before"),
                "toPlane": change.get("after"),
                "clickEventSequence": change.get("clickEventSequence"),
                "reviewRequired": True,
                "activation": "never_automatic",
            }
        )
    return suggestions


def _representative_route_points(
    route_points: list[dict[str, int]], limit: int
) -> list[dict[str, int]]:
    if len(route_points) <= limit:
        return route_points
    indices = {
        round(index * (len(route_points) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [route_points[index] for index in sorted(indices)]


def _timeline_markdown(
    manifest: Mapping[str, Any],
    events: list[dict[str, Any]],
    result: InspectionResult,
) -> str:
    lines = [
        f"# Demonstration: {_clean_text(manifest.get('name'))}",
        "",
        "Read-only evidence only. Raw coordinate replay and automatic activation are prohibited; every suggestion requires review, tests, and normal engine proof.",
        "",
        f"- Session: `{_clean_text(manifest.get('sessionProvenance', {}).get('sessionId') if isinstance(manifest.get('sessionProvenance'), Mapping) else '')}`",
        f"- Process: `{manifest.get('sessionProvenance', {}).get('pid') if isinstance(manifest.get('sessionProvenance'), Mapping) else None}`",
        f"- Commit: `{_clean_text(manifest.get('git', {}).get('commit') if isinstance(manifest.get('git'), Mapping) else '')}`",
        f"- Inspection status at capture: `{result.status}`",
        "",
        "## Semantic summary",
        "",
    ]
    lines.extend(
        f"- {_clean_text(value)}" for value in result.semantic_summary
    )
    if not result.semantic_summary:
        lines.append("- No complete semantic interaction outcome was observed.")
    if result.intent_evidence_included:
        lines.extend(["", "## Review-only camera intent episodes", ""])
        lines.extend(
            f"- `{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"
            for value in result.camera_intent_episodes
        )
        if not result.camera_intent_episodes:
            lines.append("- No bounded camera-to-click episode was derived.")
        lines.extend(["", "## Manual timing profiles", ""])
        lines.extend(
            f"- `{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"
            for value in result.timing_profiles
        )
        if not result.timing_profiles:
            lines.append("- No semantic click timing profile was derived.")
    if result.manual_route_evidence_included:
        lines.extend(["", "## Review-only manual Walk targets", ""])
        lines.extend(
            f"- `{json.dumps(value, sort_keys=True, separators=(',', ':'))}`"
            for value in result.manual_route_targets
        )
        if not result.manual_route_targets:
            lines.append("- No manual Walk target was derived.")
    lines.extend(["", "## Event timeline", "", "| Seq | UTC | Tick | Plane | Event | Detail |", "|---:|---|---:|---:|---|---|"])
    for event in events:
        if event.get("kind") in {"pointer_sample", "hover_menu"}:
            continue
        source = event.get("source") if isinstance(event.get("source"), Mapping) else {}
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        kind = _clean_text(event.get("kind"))
        detail = _timeline_detail(kind, payload)
        lines.append(
            "| {seq} | {utc} | {tick} | {plane} | {kind} | {detail} |".format(
                seq=event.get("recorderSequence", ""),
                utc=_escape_markdown_cell(_clean_text(event.get("recordedAtUtc"))),
                tick=source.get("sourceTick", ""),
                plane=source.get("plane", ""),
                kind=_escape_markdown_cell(kind),
                detail=_escape_markdown_cell(detail),
            )
        )
    lines.extend(["", "## Evidence gaps", ""])
    lines.extend(f"- {_clean_text(value)}" for value in result.coverage_gaps)
    if not result.coverage_gaps:
        lines.append("- None reported.")
    lines.extend(["", "## Ambiguities", ""])
    lines.extend(f"- {_clean_text(value)}" for value in result.ambiguities)
    if not result.ambiguities:
        lines.append("- None reported.")
    return "\n".join(lines) + "\n"


def _timeline_detail(kind: str, payload: Mapping[str, Any]) -> str:
    if kind == "camera_input":
        return " ".join(
            value
            for value in (
                _clean_text(payload.get("inputKind")),
                _clean_text(payload.get("phase")),
                _clean_text(payload.get("control")),
            )
            if value
        )
    if kind == "menu_option_clicked":
        return " ".join(
            value
            for value in (
                _clean_text(payload.get("option")),
                _clean_text(payload.get("target")),
                str(payload.get("identifier")) if payload.get("identifier") is not None else "",
            )
            if value
        )
    if kind == "interaction_outcome":
        changes = payload.get("changes", [])
        fields = [
            _clean_text(value.get("field"))
            for value in changes
            if isinstance(value, Mapping)
        ] if isinstance(changes, list) else []
        return ", ".join(fields) or "no modeled state change"
    for key in ("code", "reason", "text", "path", "name"):
        value = _clean_text(payload.get(key))
        if value:
            return value
    return ""


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "'")


def inspect_demonstration(path: Path | str) -> InspectionResult:
    """Verify a finalized artifact before deriving any review-only suggestion."""

    try:
        supplied_root = Path(path)
        if supplied_root.is_symlink() or not supplied_root.is_dir():
            raise DemonstrationError("demonstration path is not a safe directory")
        root = supplied_root.resolve(strict=True)
        hashes = _load_json_bounded(root / "hashes.json")
        if hashes.get("schema") != HASH_SCHEMA or hashes.get("algorithm") != "sha256":
            raise DemonstrationError("unsupported demonstration hash manifest")
        entries = hashes.get("files")
        if (
            not isinstance(entries, list)
            or not entries
            or len(entries) > MAX_ARTIFACT_FILES
        ):
            raise DemonstrationError("hash manifest has no evidence files")
        declared: set[str] = set()
        total_size = 0
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise DemonstrationError(f"hash entry {index} is not an object")
            relative = _validated_relative_path(entry.get("path"))
            relative_text = relative.as_posix()
            if relative_text in declared:
                raise DemonstrationError("hash manifest contains a duplicate path")
            declared.add(relative_text)
            candidate = root.joinpath(*relative.parts)
            if candidate.is_symlink() or not candidate.is_file():
                raise DemonstrationError(f"declared evidence file is missing: {relative_text}")
            try:
                candidate.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as error:
                raise DemonstrationError(
                    f"declared evidence path escapes artifact: {relative_text}"
                ) from error
            size = candidate.stat().st_size
            expected_size = _integer_or_none(entry.get("sizeBytes"))
            expected_hash = entry.get("sha256")
            if size > MAX_ARTIFACT_FILE_BYTES:
                raise DemonstrationError(f"evidence file is too large: {relative_text}")
            total_size += size
            if total_size > MAX_TOTAL_ARTIFACT_BYTES:
                raise DemonstrationError("demonstration artifact exceeds total size limit")
            if expected_size != size:
                raise DemonstrationError(f"evidence size mismatch: {relative_text}")
            if (
                not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or _sha256_file(candidate) != expected_hash
            ):
                raise DemonstrationError(f"evidence hash mismatch: {relative_text}")

        actual: set[str] = set()
        for candidate in root.rglob("*"):
            if candidate.is_symlink():
                raise DemonstrationError("demonstration contains a symlink")
            if candidate.is_dir() or candidate.name == "hashes.json":
                continue
            if len(actual) >= MAX_ARTIFACT_FILES:
                raise DemonstrationError("demonstration artifact has too many files")
            actual.add(candidate.relative_to(root).as_posix())
        if actual != declared:
            missing = sorted(declared - actual)
            unexpected = sorted(actual - declared)
            raise DemonstrationError(
                f"artifact file set mismatch; missing={missing}, unexpected={unexpected}"
            )
        required = {"manifest.json", "events.jsonl", "summary.json", "timeline.md"}
        if not required.issubset(declared):
            raise DemonstrationError("artifact is missing required evidence files")

        manifest = _load_json_bounded(root / "manifest.json")
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise DemonstrationError("unsupported demonstration manifest schema")
        if not (
            manifest.get("readOnly") is True
            and manifest.get("injectsInput") is False
            and manifest.get("rawReplayAllowed") is False
            and manifest.get("automaticActivationAllowed") is False
            and manifest.get("reviewRequired") is True
        ):
            raise DemonstrationError("manifest safety declarations are invalid")
        events = _read_events_unverified(root / "events.jsonl")
        _validate_event_stream(events, manifest)
        stored_summary = _load_json_bounded(root / "summary.json")
        if stored_summary.get("schema") != SUMMARY_SCHEMA:
            raise DemonstrationError("unsupported stored summary schema")
        timeline = root / "timeline.md"
        if timeline.stat().st_size == 0:
            raise DemonstrationError("timeline.md is empty")
        derived = _derive_summary(events, manifest)
        if dict(stored_summary) != derived.to_dict():
            raise DemonstrationError(
                "stored summary disagrees with the verified event stream"
            )
        try:
            stored_timeline = timeline.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise DemonstrationError("timeline.md could not be read") from error
        if stored_timeline != _timeline_markdown(manifest, events, derived):
            raise DemonstrationError(
                "stored timeline disagrees with the verified event stream"
            )
        return derived
    except (DemonstrationError, OSError, ValueError) as error:
        return InspectionResult(
            valid=False,
            status="INVALID_EVIDENCE",
            errors=(f"{type(error).__name__}: {error}",),
        )


def _validate_event_stream(
    events: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> None:
    if not events:
        raise DemonstrationError("events.jsonl is empty")
    requested_duration = manifest.get("requestedDurationSeconds")
    started_payload = events[0].get("payload")
    started_duration = (
        started_payload.get("requestedDurationSeconds")
        if isinstance(started_payload, Mapping)
        else None
    )
    if requested_duration is not None or started_duration is not None:
        if (
            isinstance(requested_duration, bool)
            or not isinstance(requested_duration, (int, float))
            or not math.isfinite(float(requested_duration))
            or not 0 < float(requested_duration) <= MAX_DURATION_SECONDS
            or started_duration != requested_duration
        ):
            raise DemonstrationError(
                "requested duration disagrees between manifest and start event"
            )
    allowed_kinds = {
        "recording_started",
        "recording_stopped",
        "annotation",
        "observation",
        "pointer_sample",
        "camera_input",
        "hover_menu",
        "menu_option_clicked",
        "interaction_outcome",
        "screenshot",
        "coverage_gap",
    }
    provenance = manifest.get("sessionProvenance")
    if not isinstance(provenance, Mapping):
        raise DemonstrationError("manifest session provenance is missing")
    session_id = provenance.get("sessionId")
    process_id = _integer_or_none(provenance.get("pid"))
    if not isinstance(session_id, str) or not session_id or process_id is None:
        raise DemonstrationError("manifest session identity is invalid")
    coverage = manifest.get("evidenceCoverage")
    strict_camera_semantics = bool(
        isinstance(coverage, Mapping)
        and coverage.get("cameraIntentSemanticsV2") is True
    )
    strict_activation_semantics = bool(
        isinstance(coverage, Mapping)
        and coverage.get("contextMenuActivationSemanticsV1") is True
    )
    latest_hot_sequence = 0
    observation_ticks: list[int] = []
    for expected, event in enumerate(events, 1):
        if event.get("schema") != EVENT_SCHEMA:
            raise DemonstrationError(f"event {expected} has an unsupported schema")
        if _integer_or_none(event.get("recorderSequence")) != expected:
            raise DemonstrationError("event recorderSequence is not contiguous and monotonic")
        kind = event.get("kind")
        if kind not in allowed_kinds:
            raise DemonstrationError(f"event {expected} has an unsupported kind")
        if not isinstance(event.get("source"), Mapping) or not isinstance(
            event.get("payload"), Mapping
        ):
            raise DemonstrationError(f"event {expected} has invalid source or payload")
        source = event["source"]
        payload = event["payload"]
        if strict_camera_semantics and kind in {
            "pointer_sample",
            "camera_input",
            "menu_option_clicked",
        }:
            if kind == "menu_option_clicked":
                pointer = payload.get("pointer")
                if not isinstance(pointer, Mapping):
                    raise DemonstrationError(
                        f"event {expected} lacks pointer timing evidence"
                    )
                _validate_nonnegative_time_fields(
                    pointer, f"event {expected} pointer"
                )
                if not isinstance(payload.get("consumed"), bool):
                    raise DemonstrationError(
                        f"event {expected} lacks explicit consumed evidence"
                    )
                resolved = payload.get("resolvedTarget")
                if isinstance(resolved, Mapping):
                    if resolved.get("schema") != "plugin_click_target.v1":
                        raise DemonstrationError(
                            f"event {expected} has an unsupported resolved target"
                        )
                    if (
                        (
                            resolved.get("resolution") == "exact"
                            or (
                                resolved.get("resolution") == "resolved"
                                and resolved.get("confidence") == "exact"
                            )
                        )
                        and resolved.get("actionFamily") == "tile_object"
                    ):
                        geometry = resolved.get("geometry")
                        if strict_activation_semantics:
                            activation_kind = resolved.get("activationKind")
                            if activation_kind not in _TARGET_ACTIVATION_KINDS:
                                raise DemonstrationError(
                                    f"event {expected} exact object target has an "
                                    "invalid activation kind"
                                )
                            raw_object = resolved.get("object")
                            object_world = (
                                raw_object.get("world")
                                if isinstance(raw_object, Mapping)
                                and isinstance(raw_object.get("world"), Mapping)
                                else {}
                            )
                            object_scene = (
                                raw_object.get("scene")
                                if isinstance(raw_object, Mapping)
                                and isinstance(raw_object.get("scene"), Mapping)
                                else {}
                            )
                            if (
                                not isinstance(raw_object, Mapping)
                                or (_integer_or_none(raw_object.get("id")) or 0) <= 0
                                or not _clean_text(raw_object.get("objectKey"))
                                or _world_from_mapping(object_world) is None
                                or _integer_or_none(object_scene.get("x")) is None
                                or _integer_or_none(object_scene.get("y")) is None
                            ):
                                raise DemonstrationError(
                                    f"event {expected} exact object target lacks "
                                    "complete object identity"
                                )
                            source_name = _clean_text(resolved.get("source"))
                            if activation_kind == "object_geometry":
                                if (
                                    source_name
                                    != "same_id_clickbox_contains_activation"
                                    or not isinstance(geometry, Mapping)
                                    or geometry.get("clickInside") is not True
                                ):
                                    raise DemonstrationError(
                                        f"event {expected} object-geometry activation "
                                        "lacks click-inside evidence"
                                    )
                            elif (
                                source_name != "menu_identifier_scene_coordinates"
                                or not isinstance(geometry, Mapping)
                                or geometry.get("clickInside") is not None
                            ):
                                raise DemonstrationError(
                                    f"event {expected} menu or unverified object "
                                    "activation is being treated as aim geometry"
                                )
                        elif not isinstance(geometry, Mapping) or (
                            geometry.get("clickInside") is not True
                        ):
                            raise DemonstrationError(
                                f"event {expected} exact object target lacks "
                                "click-inside geometry"
                            )
                        click_frame = _clean_text(payload.get("geometryFrameId"))
                        geometry_frame = _clean_text(
                            geometry.get("geometryFrameId")
                        )
                        if (
                            click_frame
                            and geometry_frame
                            and click_frame != geometry_frame
                        ):
                            raise DemonstrationError(
                                f"event {expected} target geometry frame disagrees "
                                "with the click frame"
                            )
            else:
                _validate_nonnegative_time_fields(
                    payload, f"event {expected} payload"
                )
            pose = payload.get("cameraPose")
            if pose is not None:
                _validate_camera_pose_shape(pose, f"event {expected} cameraPose")
            if kind == "camera_input":
                if (
                    payload.get("schema") != "plugin_camera_input.v1"
                    or payload.get("controlEvidence")
                    != "candidate_camera_control"
                    or payload.get("inputKind") not in _CAMERA_INPUT_KINDS
                    or payload.get("phase") not in _CAMERA_INPUT_PHASES
                    or payload.get("control") not in _CAMERA_INPUT_CONTROLS
                ):
                    raise DemonstrationError(
                        f"event {expected} has invalid candidate camera control evidence"
                    )
                if payload.get("phase") in {"release", "cancel"}:
                    hold = _integer_or_none(payload.get("holdDurationMillis"))
                    if hold is None or hold < 0:
                        raise DemonstrationError(
                            f"event {expected} has an invalid camera hold duration"
                        )
        identity_change_gap = bool(
            kind == "coverage_gap"
            and payload.get("code") == "session_or_process_changed"
            and payload.get("recordingStopped") is True
        )
        if not identity_change_gap and (
            source.get("sessionId") != session_id
            or _integer_or_none(source.get("pid")) != process_id
        ):
            raise DemonstrationError(
                f"event {expected} is not bound to the manifest client"
            )
        if kind in {
            "pointer_sample",
            "hover_menu",
            "menu_option_clicked",
            "camera_input",
        }:
            endpoint_sequence = _integer_or_none(source.get("eventSequence"))
            if endpoint_sequence is None or endpoint_sequence <= latest_hot_sequence:
                raise DemonstrationError(
                    "endpoint eventSequence is not globally monotonic"
                )
            latest_hot_sequence = endpoint_sequence
        elif source.get("eventSequence") is not None:
            raise DemonstrationError(
                f"event {expected} carries an unexpected endpoint sequence"
            )
        if kind == "observation":
            tick = _integer_or_none(source.get("sourceTick"))
            if tick is None or (observation_ticks and tick <= observation_ticks[-1]):
                raise DemonstrationError(
                    "observation source ticks are not strictly increasing"
                )
            observation_ticks.append(tick)
        _parse_timestamp(event.get("recordedAtUtc"), f"event {expected} UTC time")
        _parse_timestamp(event.get("recordedAtLocal"), f"event {expected} local time")
    declared_count = _integer_or_none(manifest.get("eventCount"))
    if declared_count != len(events):
        raise DemonstrationError("manifest event count disagrees with events.jsonl")
    if events[0].get("kind") != "recording_started" or events[-1].get("kind") != "recording_stopped":
        raise DemonstrationError("event stream lacks recording boundaries")
    if not observation_ticks:
        raise DemonstrationError("event stream has no observations")
    first_tick = _integer_or_none(manifest.get("firstSourceTick"))
    last_tick = _integer_or_none(manifest.get("lastSourceTick"))
    if first_tick != observation_ticks[0] or last_tick != observation_ticks[-1]:
        raise DemonstrationError(
            "manifest source-tick bounds disagree with observations"
        )
    if _integer_or_none(provenance.get("firstSourceTick")) != observation_ticks[0]:
        raise DemonstrationError(
            "session provenance first tick disagrees with observations"
        )


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DemonstrationError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DemonstrationError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise DemonstrationError(f"{label} has no timezone")
    return parsed


def _release_camera_input_capture(
    client: ObservationClient,
    recorder: DemonstrationRecorder | None = None,
) -> None:
    disable = getattr(client, "disable_demonstration_capture", None)
    if not callable(disable):
        return
    try:
        disable()
    except Exception as error:
        if recorder is None or not recorder._started or recorder._finalized:
            return
        try:
            recorder._append(
                "coverage_gap",
                {
                    "sessionId": recorder._session_id,
                    "pid": recorder._process_id,
                    "sourceTick": recorder._last_tick,
                    "clientTick": None,
                    "plane": (
                        recorder._last_observation.get("player", {}).get("plane")
                        if recorder._last_observation
                        else None
                    ),
                    "eventSequence": None,
                },
                {
                    "code": "camera_capture_disable_failed",
                    "errorType": type(error).__name__,
                },
            )
        except DemonstrationLimitReached:
            # Cleanup must not prevent the reserved terminal event from finalizing.
            pass


def record_live(
    name: str,
    client: ObservationClient,
    *,
    output_root: Path = Path("demo_runs"),
    duration_seconds: float = 60.0,
    poll_seconds: float = 0.2,
    annotations: Iterable[str] = (),
    screenshots_enabled: bool = True,
    stop_requested: Callable[[], bool] | None = None,
) -> Path:
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not 0 < float(duration_seconds) <= MAX_DURATION_SECONDS
    ):
        raise DemonstrationError(
            f"duration_seconds must be greater than 0 and at most {MAX_DURATION_SECONDS:g}"
        )
    if (
        isinstance(poll_seconds, bool)
        or not isinstance(poll_seconds, (int, float))
        or not 0.02 <= float(poll_seconds) <= 1.0
    ):
        raise DemonstrationError(
            "poll_seconds must be between 0.02 and 1.0 so the bounded "
            "camera-capture lease remains continuous"
        )
    if stop_requested is not None and not callable(stop_requested):
        raise DemonstrationError("stop_requested must be callable or None")
    should_stop = stop_requested or (lambda: False)
    recorder = DemonstrationRecorder(
        name,
        output_root=output_root,
        annotations=annotations,
        screenshots_enabled=screenshots_enabled,
        requested_duration_seconds=float(duration_seconds),
    )
    first: DemonstrationEvidenceSnapshot | None = None
    startup_started = time.monotonic()
    if not math.isfinite(startup_started):
        raise DemonstrationError("monotonic clock is invalid before recording")
    startup_deadline = startup_started + WORLD_MODEL_GAP_GRACE_SECONDS
    startup_polls = 0
    try:
        while first is None:
            if startup_polls and should_stop():
                raise DemonstrationError(
                    "demonstration stopped before a loaded scene was captured"
                )
            candidate = client.fetch_demonstration_evidence()
            startup_polls += 1
            if candidate.observation.loaded_scene:
                first = candidate
                break
            if _transient_gap_code(candidate) is None:
                _require_loaded_identity(candidate.observation)
            _validate_evidence_envelope_and_hot(candidate)
            if should_stop():
                raise DemonstrationError(
                    "demonstration stopped before a loaded scene was captured"
                )
            startup_now = time.monotonic()
            if not math.isfinite(startup_now) or startup_now < startup_started:
                raise DemonstrationError(
                    "monotonic clock changed during recording startup"
                )
            remaining = startup_deadline - startup_now
            if remaining <= 0:
                raise DemonstrationError(
                    "demonstration world-model evidence remained unavailable for "
                    f"{startup_polls} polls over "
                    f"{WORLD_MODEL_GAP_GRACE_SECONDS:g} seconds"
                )
            time.sleep(min(float(poll_seconds), remaining))
        recorder.start(first)
    except BaseException:
        _release_camera_input_capture(client)
        raise
    deadline = time.monotonic() + float(duration_seconds)
    reason = "duration_elapsed"
    consecutive_errors = 0
    try:
        while time.monotonic() < deadline:
            if should_stop():
                reason = "facade_stop_requested"
                break
            time.sleep(min(float(poll_seconds), max(0.0, deadline - time.monotonic())))
            if should_stop():
                reason = "facade_stop_requested"
                break
            if time.monotonic() >= deadline:
                break
            try:
                evidence = client.fetch_demonstration_evidence()
            except Exception as error:
                consecutive_errors += 1
                recorder._append(
                    "coverage_gap",
                    {
                        "sessionId": recorder._session_id,
                        "pid": recorder._process_id,
                        "sourceTick": recorder._last_tick,
                        "clientTick": None,
                        "plane": (
                            recorder._last_observation.get("player", {}).get("plane")
                            if recorder._last_observation
                            else None
                        ),
                        "eventSequence": None,
                    },
                    {
                        "code": "observation_fetch_failed",
                        "errorType": type(error).__name__,
                    },
                )
                if consecutive_errors >= 5:
                    reason = "repeated_observation_failure"
                    break
                continue
            consecutive_errors = 0
            if not recorder.add(evidence):
                reason = "scene_identity_or_loaded_state_changed"
                break
    except KeyboardInterrupt:
        reason = "operator_interrupt"
    except DemonstrationLimitReached:
        reason = "evidence_limit_reached"
    except BaseException:
        _release_camera_input_capture(client, recorder)
        recorder.finish("recorder_error")
        raise
    _release_camera_input_capture(client, recorder)
    return recorder.finish(reason)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m osrs_bot.demonstration",
        description="Record or inspect read-only manual OSRS demonstration evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    record = subparsers.add_parser("record", help="record bounded read-only evidence")
    record.add_argument("name")
    record.add_argument("--duration-seconds", type=float, default=60.0)
    record.add_argument("--poll-seconds", type=float, default=0.05)
    record.add_argument("--output-root", type=Path, default=Path("demo_runs"))
    record.add_argument("--annotation", action="append", default=[])
    record.add_argument("--no-screenshots", action="store_true")
    record.add_argument("--endpoint", default="http://127.0.0.1:8893")
    record.add_argument(
        "--auth-token",
        default=os.environ.get("OSRS_TELEMETRY_SNAPSHOT_AUTH_TOKEN"),
    )
    record.add_argument("--timeout-seconds", type=float, default=3.0)
    inspect = subparsers.add_parser("inspect", help="verify and summarize an artifact")
    inspect.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        result = inspect_demonstration(args.path)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.valid else 2
    try:
        client = ObservationClient(
            args.endpoint,
            auth_token=args.auth_token,
            timeout_seconds=args.timeout_seconds,
        )
        artifact = record_live(
            args.name,
            client,
            output_root=args.output_root,
            duration_seconds=args.duration_seconds,
            poll_seconds=args.poll_seconds,
            annotations=args.annotation,
            screenshots_enabled=not args.no_screenshots,
        )
        result = inspect_demonstration(artifact)
        print(
            json.dumps(
                {
                    "status": result.status,
                    "valid": result.valid,
                    "artifact": str(artifact.resolve()),
                    "summary": result.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if result.valid else 2
    except (DemonstrationError, OSError, ValueError) as error:
        print(
            json.dumps(
                {"status": "ERROR", "reason": f"{type(error).__name__}: {error}"},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
