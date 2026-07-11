from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
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

from .model import Observation, ScreenBounds, ScreenPoint
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
MAX_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACT_FILES = 128
MAX_TOTAL_ARTIFACT_BYTES = 256 * 1024 * 1024
EVENT_FINALIZATION_RESERVE_BYTES = 256 * 1024
POINTER_INTERVAL_MILLIS = 50
_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_TAG = re.compile(r"<[^>]*>")
_UNSET = object()


class DemonstrationError(RuntimeError):
    pass


class DemonstrationLimitReached(DemonstrationError):
    pass


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
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
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


class DemonstrationRecorder:
    """Append-only read-only evidence recorder; it owns no input surface."""

    def __init__(
        self,
        name: str,
        *,
        output_root: Path,
        annotations: Iterable[str] = (),
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        capture: Callable[..., tuple[Any, CaptureMetadata]] = capture_canvas_region,
        screenshots_enabled: bool = True,
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
        self._capture = capture
        self._screenshots_enabled = bool(screenshots_enabled)
        self._artifact_path: Path | None = None
        self._events_handle: Any | None = None
        self._event_sequence = 0
        self._event_bytes = 0
        self._seen_hot_sequences: set[int] = set()
        self._start_hot_sequence = 0
        self._last_hot_watermark = 0
        self._last_pointer_wall_time: int | None = None
        self._reported_missing_pointer_time = False
        self._drop_counts = (0, 0, 0)
        self._session_id: str | None = None
        self._process_id: int | None = None
        self._first_tick: int | None = None
        self._last_tick: int | None = None
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
        self._started = True
        self._append(
            "recording_started",
            _source(observation),
            {
                "name": self.name,
                "readOnly": True,
                "rawReplayAllowed": False,
                "reviewRequired": True,
            },
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
            self._append(
                "coverage_gap",
                _source(observation),
                {"code": "loaded_scene_lost", "recordingStopped": True},
            )
            return False
        if self._last_tick is not None and observation.tick < self._last_tick:
            self._append(
                "coverage_gap",
                _source(observation),
                {"code": "source_tick_regressed", "recordingStopped": True},
            )
            return False
        _validate_evidence_binding(evidence)

        payload = evidence.payload()
        watermark = _integer_or_none(
            _hot_payload(payload).get("latestEventSequence")
        )
        if watermark is None or watermark < self._last_hot_watermark:
            self._append(
                "coverage_gap",
                _source(observation),
                {"code": "hot_event_sequence_reset", "recordingStopped": True},
            )
            return False
        self._last_hot_watermark = watermark
        self._record_hot_events(payload, observation)
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
        self, raw_payload: Mapping[str, Any], observation: Observation
    ) -> None:
        hot = _hot_payload(raw_payload)
        current_drops = _drop_counts(hot)
        for lane, before, after in zip(
            ("client_tick", "post_menu_sort", "clicked"),
            self._drop_counts,
            current_drops,
        ):
            if after > before:
                self._append(
                    "coverage_gap",
                    _source(observation),
                    {
                        "code": "hot_tail_samples_dropped",
                        "lane": lane,
                        "droppedDelta": after - before,
                    },
                )
        self._drop_counts = current_drops

        merged: list[tuple[int, str, Mapping[str, Any]]] = []
        for lane, key in (
            ("client_tick", "clientTickTail"),
            ("post_menu_sort", "postMenuSortTail"),
            ("clicked", "clickedTail"),
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
                if (
                    self._last_pointer_wall_time is not None
                    and wall_time >= self._last_pointer_wall_time
                    and wall_time - self._last_pointer_wall_time
                    < POINTER_INTERVAL_MILLIS
                ):
                    continue
                self._last_pointer_wall_time = wall_time
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
            else:
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
                    "visible": item.geometry.visible,
                    "actionable": item.geometry.actionable,
                    "screenPoint": _point_payload(item.geometry.screen_point),
                    "screenBounds": _bounds_payload(item.geometry.screen_bounds),
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
    for key in ("clientTickTail", "postMenuSortTail", "clickedTail"):
        values = hot.get(key, [])
        if isinstance(values, list):
            for sample in values:
                if isinstance(sample, Mapping):
                    sequence = _integer_or_none(sample.get("eventSequence"))
                    if sequence is not None:
                        output.add(sequence)
    return output


def _drop_counts(hot: Mapping[str, Any]) -> tuple[int, int, int]:
    latency = hot.get("latency")
    latency = latency if isinstance(latency, Mapping) else {}
    return tuple(
        max(0, _integer_or_none(latency.get(name)) or 0)
        for name in (
            "droppedClientTickSamples",
            "droppedPostMenuSortSamples",
            "droppedClickedSamples",
        )
    )  # type: ignore[return-value]


def _pointer_payload(sample: Mapping[str, Any]) -> dict[str, Any]:
    nested = sample.get("mouse") if isinstance(sample.get("mouse"), Mapping) else {}
    return {
        "canvasX": _integer_or_none(sample.get("mouseCanvasX", nested.get("canvasX"))),
        "canvasY": _integer_or_none(sample.get("mouseCanvasY", nested.get("canvasY"))),
        "isInCanvas": bool(sample.get("isInCanvas", nested.get("isInCanvas", False))),
        "wallTimeMillis": _integer_or_none(sample.get("wallTimeMillis")),
        "observableButton": _clean_text(
            sample.get("mouseButton", sample.get("button", ""))
        )
        or None,
        "observableKey": _clean_text(
            sample.get("key", sample.get("keyText", ""))
        )
        or None,
    }


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
    payload = {
        **_menu_entry_payload(sample),
        "itemId": _integer_or_none(sample.get("itemId")),
        "consumed": bool(sample.get("consumed", False)),
        "clientTick": _integer_or_none(sample.get("clientTick")),
        "gameTickAtSample": _integer_or_none(sample.get("gameTickAtSample")),
        "pointer": _pointer_payload(sample),
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
    if menu_identifier is None or menu_identifier <= 0:
        return None
    if "GAME_OBJECT" in entry_type:
        census_match = False
        values = (
            before_observation.get("nearbyObjects", [])
            if isinstance(before_observation, Mapping)
            else []
        )
        if isinstance(values, list):
            census_match = any(
                isinstance(value, Mapping)
                and _integer_or_none(value.get("objectId")) == menu_identifier
                for value in values
            )
        return {
            "kind": "GAME_OBJECT",
            "stableEntityId": menu_identifier,
            "source": "runelite_game_object_menu_identifier",
            "censusMatch": census_match,
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


def _validate_evidence_binding(evidence: DemonstrationEvidenceSnapshot) -> None:
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
            if sample.get("sessionId") != observation.session_id or _integer_or_none(
                sample.get("clientProcessId")
            ) != observation.client_process_id:
                raise DemonstrationError(f"{key} contains evidence from another client")

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
            "hoverMenus": True,
            "semanticMenuOptionClicked": True,
            "beforeAfterObservations": True,
            "boundedScreenshotsAvailable": True,
            "rawMouseButtonTransitions": False,
            "rawKeyboardEvents": False,
            "rawInputUnavailableReason": (
                "the RuneLite endpoint exposes semantic click/menu evidence but no "
                "global raw mouse-button or keyboard hooks"
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


def _derive_summary(
    events: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> InspectionResult:
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

    coverage = manifest.get("evidenceCoverage")
    if isinstance(coverage, Mapping):
        if coverage.get("rawMouseButtonTransitions") is False:
            gaps.append("raw mouse-button transitions were not observable")
        if coverage.get("rawKeyboardEvents") is False:
            gaps.append("raw keyboard events were not observable")
    if not route_points:
        ambiguities.append("no authoritative player route points were recorded")

    suggestions = _candidate_suggestions(
        route_points, interacted_entities, state_changes
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
        if (
            stable_id is None
            or stable_id <= 0
            or kind not in {"GAME_OBJECT", "NPC"}
            or not name
            or not action
            or action.casefold() in {"walk here", "examine"}
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
    allowed_kinds = {
        "recording_started",
        "recording_stopped",
        "annotation",
        "observation",
        "pointer_sample",
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
        if kind in {"pointer_sample", "hover_menu", "menu_option_clicked"}:
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


def record_live(
    name: str,
    client: ObservationClient,
    *,
    output_root: Path = Path("demo_runs"),
    duration_seconds: float = 60.0,
    poll_seconds: float = 0.05,
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
        or not 0.02 <= float(poll_seconds) <= 5.0
    ):
        raise DemonstrationError("poll_seconds must be between 0.02 and 5.0")
    if stop_requested is not None and not callable(stop_requested):
        raise DemonstrationError("stop_requested must be callable or None")
    should_stop = stop_requested or (lambda: False)
    recorder = DemonstrationRecorder(
        name,
        output_root=output_root,
        annotations=annotations,
        screenshots_enabled=screenshots_enabled,
    )
    first = client.fetch_demonstration_evidence()
    recorder.start(first)
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
        recorder.finish("recorder_error")
        raise
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
