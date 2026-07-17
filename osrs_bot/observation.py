from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from html import unescape
from math import ceil, floor
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from .contract_limits import MAX_PRIORITY_OBJECT_IDS
from .model import CAMERA_YAW_UNITS, DialogueOption, EquipmentObservation, InventoryItem, InventoryObservation, MenuEntry, NearbyObject, Observation
from .model import ObservationPipelineEvidence, PlayerObservation, SceneCensusEvidence, SceneIndex, ScreenBounds, ScreenPoint, TargetGeometry, WidgetObservation, WidgetTarget, WorldPoint

RESPONSE_SCHEMA = "plugin_snapshot_response.v2"
SENSOR_FRAME_SCHEMA = "sensor_frame.v1"
MAX_SOURCE_AGE_MILLIS = 2_000
MAX_TILE_PROJECTIONS = 16
MAX_PRIORITY_OBJECT_KEY_LENGTH = 256
MAX_SNAPSHOT_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_ERROR_RESPONSE_BYTES = 64 * 1024
MAX_SCENE_OBJECT_ROWS = 64
MAX_MENU_ENTRY_ROWS = 16
MAX_INVENTORY_ITEM_ROWS = 28
MAX_EQUIPMENT_ITEM_ROWS = 32
MAX_DIALOGUE_OPTION_ROWS = 16
CORE_FACT_NEEDS = ("baseline", "inventory", "activity", "bank_ui", "dialogue_state")
CANONICAL_NEEDS = ("baseline", "inventory", "activity", "interaction_hot",
                   "scene_object_census", "bank_ui", "dialogue_state")
DEMONSTRATION_NEEDS = CANONICAL_NEEDS + (
    "client_tick_tail",
    "actor_census",
    "collision_window",
)
_TAG = re.compile(r"<[^>]*>")

class ObservationError(RuntimeError):
    pass
class ObservationRequestError(ObservationError): pass
class ObservationTransportError(ObservationError): pass


class ObservationBackpressureError(ObservationTransportError):
    """A bounded, retryable endpoint-admission rejection."""

    def __init__(self, status_code: int, error_code: str) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = True
        super().__init__(
            f"snapshot endpoint returned HTTP {status_code} ({error_code})"
        )


class ObservationDecodeError(ObservationError): pass
class ObservationSchemaError(ObservationError): pass


class ObservationWorldModelHandoffError(ObservationError):
    """A bounded retry signal for one exact dynamic-provenance handoff."""

    error_code = "world_model_provenance_mismatch"
    retryable = True

    def __init__(self, missing_capabilities: tuple[str, ...]) -> None:
        self.missing_capabilities = missing_capabilities
        super().__init__(
            "planned snapshot crossed a transient world-model provenance handoff"
        )


@dataclass(frozen=True, slots=True)
class _TransportEvidence:
    response_bytes: int
    http_millis: float
    decode_millis: float


@dataclass(frozen=True, slots=True)
class _PlannedResponseContract:
    center_world_location: WorldPoint | None
    radius_tiles: int
    purpose: str
    priority_object_ids: tuple[int, ...]
    priority_object_keys: tuple[str, ...]


class _SnapshotPayload(dict[str, Any]):
    """A normal JSON dict carrying non-serialized local transport evidence."""

    __slots__ = ("transport_evidence",)

    def __init__(
        self,
        value: Mapping[str, Any],
        transport_evidence: _TransportEvidence,
    ) -> None:
        super().__init__(value)
        self.transport_evidence = transport_evidence


@dataclass(frozen=True, slots=True)
class DemonstrationEvidenceSnapshot:
    observation: Observation
    payload_json: str
    request_json: str
    fetched_at_utc: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation):
            raise TypeError("observation must be Observation")
        for field_name in ("payload_json", "request_json"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be non-empty canonical JSON")
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, ValueError) as error:
                raise ValueError(f"{field_name} must be valid JSON") from error
            if not isinstance(decoded, dict):
                raise ValueError(f"{field_name} must encode an object")
        if not isinstance(self.fetched_at_utc, datetime):
            raise TypeError("fetched_at_utc must be datetime")
        if (
            self.fetched_at_utc.tzinfo is None
            or self.fetched_at_utc.utcoffset() is None
        ):
            raise ValueError("fetched_at_utc must be timezone-aware")

    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    def request(self) -> dict[str, Any]:
        return json.loads(self.request_json)

@dataclass(frozen=True, slots=True)
class _CanvasTransform:
    canvas_bounds: ScreenBounds
    source_width: int
    source_height: int
    @property
    def scale_x(self) -> float:
        return self.canvas_bounds.width / self.source_width
    @property
    def scale_y(self) -> float:
        return self.canvas_bounds.height / self.source_height

    def point(self, x: Any, y: Any) -> ScreenPoint | None:
        cx = _number(x, "canvas point x", optional=True)
        cy = _number(y, "canvas point y", optional=True)
        if cx is None or cy is None or not (0 <= cx < self.source_width and 0 <= cy < self.source_height):
            return None
        point = ScreenPoint(round(self.canvas_bounds.x + cx * self.scale_x),
                            round(self.canvas_bounds.y + cy * self.scale_y))
        return point if self.canvas_bounds.contains(point) else None

    def bounds(self, raw: Mapping[str, Any] | None) -> ScreenBounds | None:
        if not raw:
            return None
        x = _number(raw.get("x"), "canvas bounds x", optional=True)
        y = _number(raw.get("y"), "canvas bounds y", optional=True)
        width = _number(raw.get("w", raw.get("width")), "canvas bounds width", optional=True)
        height = _number(raw.get("h", raw.get("height")), "canvas bounds height", optional=True)
        if None in (x, y, width, height) or width <= 0 or height <= 0:
            return None
        left, top = max(0.0, x), max(0.0, y)
        right = min(float(self.source_width), x + width)
        bottom = min(float(self.source_height), y + height)
        if right <= left or bottom <= top:
            return None
        sx = max(self.canvas_bounds.x, floor(self.canvas_bounds.x + left * self.scale_x))
        sy = max(self.canvas_bounds.y, floor(self.canvas_bounds.y + top * self.scale_y))
        ex = min(self.canvas_bounds.x + self.canvas_bounds.width, ceil(self.canvas_bounds.x + right * self.scale_x))
        ey = min(self.canvas_bounds.y + self.canvas_bounds.height, ceil(self.canvas_bounds.y + bottom * self.scale_y))
        return ScreenBounds(sx, sy, ex - sx, ey - sy) if ex > sx and ey > sy else None

    def polygon(self, raw: Any, path: str) -> tuple[ScreenPoint, ...]:
        if not isinstance(raw, list):
            raise ObservationSchemaError(f"{path} must be an array")
        if not 3 <= len(raw) <= 256:
            raise ObservationSchemaError(f"{path} must contain 3 to 256 points")
        canvas_points: list[tuple[int, int]] = []
        for index, value in enumerate(raw):
            point_path = f"{path}[{index}]"
            if isinstance(value, Mapping):
                x = _integer(value.get("x"), f"{point_path}.x")
                y = _integer(value.get("y"), f"{point_path}.y")
            elif isinstance(value, list) and len(value) == 2:
                x = _integer(value[0], f"{point_path}[0]")
                y = _integer(value[1], f"{point_path}[1]")
            else:
                raise ObservationSchemaError(
                    f"{point_path} must be an x/y object or two-integer array"
                )
            canvas_points.append((x, y))
        twice_area = sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(
                canvas_points, canvas_points[1:] + canvas_points[:1], strict=True
            )
        )
        if twice_area == 0:
            raise ObservationSchemaError(f"{path} must have non-zero area")
        return tuple(
            ScreenPoint(
                round(self.canvas_bounds.x + x * self.scale_x),
                round(self.canvas_bounds.y + y * self.scale_y),
            )
            for x, y in canvas_points
        )

def _mapping(value: Any, path: str, *, optional: bool = False) -> Mapping[str, Any]:
    if value is None and optional:
        return {}
    if not isinstance(value, Mapping):
        raise ObservationSchemaError(f"{path} must be an object")
    return value

def _integer(value: Any, path: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ObservationSchemaError(f"{path} must be an integer")
    return int(value)

def _number(value: Any, path: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObservationSchemaError(f"{path} must be a number")
    return float(value)

def _boolean(value: Any, path: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ObservationSchemaError(f"{path} must be a boolean")
    return value


def _optional_boolean(value: Any, path: str) -> bool | None:
    return None if value is None else _boolean(value, path)

def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ObservationSchemaError(f"{path} must be an array of strings")
    return tuple(value)


def _bounded_list(value: Any, path: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise ObservationSchemaError(f"{path} must be an array")
    if len(value) > maximum:
        raise ObservationSchemaError(
            f"{path} exceeds the maximum of {maximum} rows"
        )
    return value


def _nonnegative_integer(
    value: Any,
    path: str,
    *,
    optional: bool = True,
) -> int | None:
    result = _integer(value, path, optional=optional)
    if result is not None and result < 0:
        raise ObservationSchemaError(f"{path} must be non-negative")
    return result


def _nonnegative_number(value: Any, path: str) -> float | None:
    result = _number(value, path, optional=True)
    if result is not None and result < 0:
        raise ObservationSchemaError(f"{path} must be non-negative")
    return result


def _positive_integer_tuple(
    value: Any,
    path: str,
    *,
    maximum: int = MAX_PRIORITY_OBJECT_IDS,
) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ObservationSchemaError(f"{path} must be an array")
    if len(value) > maximum:
        raise ObservationSchemaError(f"{path} exceeds the maximum of {maximum} rows")
    result = tuple(
        _integer(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    if any(item <= 0 for item in result) or len(set(result)) != len(result):
        raise ObservationSchemaError(
            f"{path} must contain unique positive integers"
        )
    return result


def _nonempty_string_tuple(
    value: Any,
    path: str,
    *,
    maximum: int = MAX_PRIORITY_OBJECT_IDS,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ObservationSchemaError(f"{path} must be an array")
    if len(value) > maximum:
        raise ObservationSchemaError(f"{path} exceeds the maximum of {maximum} rows")
    if any(not isinstance(item, str) or not item for item in value):
        raise ObservationSchemaError(f"{path} must contain non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ObservationSchemaError(f"{path} must not contain duplicates")
    return result

def _payload(payloads: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    return _mapping(payloads.get(name), f"payloads.{name}", optional=True)

def _normalize_tiles(value: Iterable[tuple[str, WorldPoint]] | None) -> tuple[tuple[str, WorldPoint], ...]:
    try:
        tiles = tuple(value or ())
    except TypeError as exc:
        raise ObservationRequestError("tile_projections must be iterable") from exc
    if len(tiles) > MAX_TILE_PROJECTIONS:
        raise ObservationRequestError(f"at most {MAX_TILE_PROJECTIONS} tile projections are allowed")
    seen: set[str] = set()
    for item in tiles:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ObservationRequestError("each tile projection must be (label, WorldPoint)")
        label, location = item
        if not isinstance(label, str) or not label.strip() or label in seen or not isinstance(location, WorldPoint):
            raise ObservationRequestError("tile projection labels must be unique non-empty strings with WorldPoint values")
        seen.add(label)
    return tiles

def _normalize_priority_object_ids(
    value: Iterable[int] | None,
) -> tuple[int, ...]:
    try:
        object_ids = tuple(value or ())
    except TypeError as exc:
        raise ObservationRequestError("priority_object_ids must be iterable") from exc
    if len(object_ids) > MAX_PRIORITY_OBJECT_IDS:
        raise ObservationRequestError(
            f"at most {MAX_PRIORITY_OBJECT_IDS} priority object IDs are allowed"
        )
    if len(set(object_ids)) != len(object_ids):
        raise ObservationRequestError("priority object IDs must be unique")
    if any(
        isinstance(object_id, bool)
        or not isinstance(object_id, int)
        or object_id <= 0
        for object_id in object_ids
    ):
        raise ObservationRequestError("priority object IDs must be positive integers")
    return object_ids


def _normalize_priority_object_keys(
    value: Iterable[str] | None,
) -> tuple[str, ...]:
    try:
        object_keys = tuple(value or ())
    except TypeError as exc:
        raise ObservationRequestError("priority_object_keys must be iterable") from exc
    if len(object_keys) > MAX_PRIORITY_OBJECT_IDS:
        raise ObservationRequestError(
            f"at most {MAX_PRIORITY_OBJECT_IDS} priority object keys are allowed"
        )
    if any(
        not isinstance(object_key, str)
        or not object_key.strip()
        or len(object_key) > MAX_PRIORITY_OBJECT_KEY_LENGTH
        for object_key in object_keys
    ):
        raise ObservationRequestError(
            "priority object keys must be non-empty strings of at most 256 characters"
        )
    if len(set(object_keys)) != len(object_keys):
        raise ObservationRequestError("priority object keys must be unique")
    return object_keys


def _bounded_query_integer(
    value: int | None,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ObservationRequestError(
            f"{field} must be an integer from {minimum} through {maximum}"
        )
    return value


def build_snapshot_request(
    tile_projections: Iterable[tuple[str, WorldPoint]] | None = None,
    priority_object_ids: Iterable[int] | None = None,
    priority_object_keys: Iterable[str] | None = None,
    *,
    center_world_location: WorldPoint | None = None,
    radius_tiles: int | None = None,
    max_objects: int | None = None,
    max_projection_objects: int | None = None,
    purpose: str | None = None,
) -> dict[str, Any]:
    tiles = _normalize_tiles(tile_projections)
    object_ids = _normalize_priority_object_ids(priority_object_ids)
    object_keys = _normalize_priority_object_keys(priority_object_keys)
    if center_world_location is not None and not isinstance(
        center_world_location, WorldPoint
    ):
        raise ObservationRequestError(
            "center_world_location must be WorldPoint or None"
        )
    radius = _bounded_query_integer(
        radius_tiles, "radius_tiles", minimum=1, maximum=96
    )
    object_limit = _bounded_query_integer(
        max_objects,
        "max_objects",
        minimum=0,
        maximum=MAX_SCENE_OBJECT_ROWS,
    )
    projection_limit = _bounded_query_integer(
        max_projection_objects,
        "max_projection_objects",
        minimum=0,
        maximum=MAX_SCENE_OBJECT_ROWS,
    )
    effective_object_limit = 64 if object_limit is None else object_limit
    if projection_limit is not None and projection_limit > effective_object_limit:
        raise ObservationRequestError(
            "max_projection_objects must not exceed max_objects"
        )
    if purpose is not None and (
        not isinstance(purpose, str)
        or not purpose
        or len(purpose) > 64
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in purpose
        )
    ):
        raise ObservationRequestError(
            "purpose must be a lowercase identifier of at most 64 characters"
        )
    request: dict[str, Any] = {
        "schema": "plugin_snapshot_request.v1", "needs": list(CANONICAL_NEEDS),
        "snapshotTier": "hot", "responseMode": "compact", "maxAgeTicks": 0,
        "maxSourceAgeMillis": MAX_SOURCE_AGE_MILLIS,
        "includeGeometry": True, "includeCollisionWindow": False, "includeWatchValues": False,
        "includeMenuEntries": True, "menuEntryLimit": 16, "maxClientTickSamples": 0,
        "maxMenuSamples": 0, "maxClickedSamples": 0, "maxCameraInputSamples": 0,
        "worldModel": {"radiusTiles": 32, "maxObjects": 64, "includeProjection": True, "includeCollision": False}}
    world_model = request["worldModel"]
    if radius is not None:
        world_model["radiusTiles"] = radius
    if object_limit is not None:
        world_model["maxObjects"] = object_limit
    if projection_limit is not None:
        world_model["maxProjectionObjects"] = projection_limit
    if center_world_location is not None:
        world_model["centerWorldLocation"] = {
            "worldX": center_world_location.x,
            "worldY": center_world_location.y,
            "plane": center_world_location.plane,
        }
    if purpose is not None:
        world_model["purpose"] = purpose
    if object_ids:
        world_model["priorityObjectIds"] = list(object_ids)
    if object_keys:
        world_model["priorityObjectKeys"] = list(object_keys)
    if tiles:
        request["tileProjectionRequests"] = [
            {"label": label, "worldX": point.x, "worldY": point.y, "plane": point.plane} for label, point in tiles]
    return request


def build_demonstration_request(
    tile_projections: Iterable[tuple[str, WorldPoint]] | None = None,
) -> dict[str, Any]:
    request = build_snapshot_request(tile_projections)
    request.update(
        needs=list(DEMONSTRATION_NEEDS),
        includeCollisionWindow=True,
        maxClientTickSamples=64,
        maxMenuSamples=32,
        maxClickedSamples=32,
        maxCameraInputSamples=64,
        menuEntryLimit=16,
    )
    request["worldModel"] = {
        "radiusTiles": 16,
        "maxObjects": 64,
        "includeProjection": True,
        "includeCollision": True,
        "maxCollisionTiles": 512,
        "includeActors": True,
        "maxActors": 64,
    }
    return request


def build_demonstration_capture_disable_request() -> dict[str, Any]:
    """Build the explicit request that releases the bounded input-capture lease."""
    request = build_snapshot_request()
    request["disableCameraInputCapture"] = True
    return request

def _canvas_transform(baseline: Mapping[str, Any]) -> _CanvasTransform | None:
    raw = _mapping(baseline.get("inputGeometry"), "payloads.baseline.inputGeometry", optional=True)
    if not raw or not _boolean(raw.get("geometryAvailable"), "inputGeometry.geometryAvailable"):
        return None
    if raw.get("schema") != "input_geometry.v1":
        raise ObservationSchemaError("inputGeometry schema must be input_geometry.v1")
    if raw.get("coordinateSpace") != "device_pixels":
        raise ObservationSchemaError(
            "inputGeometry coordinateSpace must be device_pixels"
        )
    x = _integer(raw.get("canvasScreenX"), "inputGeometry.canvasScreenX")
    y = _integer(raw.get("canvasScreenY"), "inputGeometry.canvasScreenY")
    width = _integer(raw.get("canvasWidth"), "inputGeometry.canvasWidth")
    height = _integer(raw.get("canvasHeight"), "inputGeometry.canvasHeight")
    viewport = _mapping(baseline.get("cameraViewport"), "payloads.baseline.cameraViewport", optional=True)
    source_width = _integer(raw.get("sourceCanvasWidth", viewport.get("canvasWidth", width)), "inputGeometry.sourceCanvasWidth")
    source_height = _integer(raw.get("sourceCanvasHeight", viewport.get("canvasHeight", height)), "inputGeometry.sourceCanvasHeight")
    if min(width, height, source_width, source_height) <= 0:
        raise ObservationSchemaError("inputGeometry canvas dimensions must be positive")
    return _CanvasTransform(ScreenBounds(x, y, width, height), source_width, source_height)


def _client_window_bounds(
    input_geometry: Mapping[str, Any],
    canvas_bounds: ScreenBounds | None,
) -> ScreenBounds | None:
    fields = (
        "clientWindowX",
        "clientWindowY",
        "clientWindowWidth",
        "clientWindowHeight",
    )
    values = tuple(input_geometry.get(field) for field in fields)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ObservationSchemaError(
            "inputGeometry client window bounds are incomplete"
        )
    x, y, width, height = (
        _integer(value, f"inputGeometry.{field}")
        for field, value in zip(fields, values, strict=True)
    )
    if width <= 0 or height <= 0:
        raise ObservationSchemaError(
            "inputGeometry client window dimensions must be positive"
        )
    bounds = ScreenBounds(x, y, width, height)
    if canvas_bounds is not None and not (
        bounds.contains(ScreenPoint(canvas_bounds.x, canvas_bounds.y))
        and bounds.contains(
            ScreenPoint(
                canvas_bounds.x + canvas_bounds.width - 1,
                canvas_bounds.y + canvas_bounds.height - 1,
            )
        )
    ):
        raise ObservationSchemaError(
            "inputGeometry client window does not contain the canvas"
        )
    return bounds

def _world_point(raw: Mapping[str, Any], path: str) -> WorldPoint | None:
    values = (raw.get("worldX"), raw.get("worldY"), raw.get("plane"))
    if all(value is None for value in values):
        return None
    x, y, plane = (_integer(value, path) for value in values)
    return None if x < 0 or y < 0 or plane < 0 else WorldPoint(x, y, plane)


def _convex_screen_hull(
    points: tuple[ScreenPoint, ...],
) -> tuple[ScreenPoint, ...]:
    """Normalize RuneLite canvas-tile corners into a non-crossing polygon."""

    unique = sorted({(point.x, point.y) for point in points})
    if len(unique) < 3:
        return ()

    def cross(
        origin: tuple[int, int],
        first: tuple[int, int],
        second: tuple[int, int],
    ) -> int:
        return (
            (first[0] - origin[0]) * (second[1] - origin[1])
            - (first[1] - origin[1]) * (second[0] - origin[0])
        )

    lower: list[tuple[int, int]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[int, int]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    return tuple(ScreenPoint(x, y) for x, y in hull) if len(hull) >= 3 else ()

def _target_geometry(raw: Mapping[str, Any], transform: _CanvasTransform | None) -> TargetGeometry:
    projection = _mapping(raw.get("projection", raw.get("projectionStatus")), "object.projection", optional=True)
    if not projection:
        projection = raw
    aim = _mapping(projection.get("aimPoint"), "object.projection.aimPoint", optional=True)
    canvas_location = _mapping(projection.get("canvasLocation"), "object.projection.canvasLocation", optional=True)
    canvas_x = aim.get("canvasX", aim.get("x", canvas_location.get("x")))
    canvas_y = aim.get("canvasY", aim.get("y", canvas_location.get("y")))
    canvas_point = None
    if canvas_x is not None or canvas_y is not None:
        x = _integer(canvas_x, "object aimPoint x")
        y = _integer(canvas_y, "object aimPoint y")
        canvas_point = ScreenPoint(x, y)
    screen_point = transform.point(canvas_x, canvas_y) if transform and canvas_point else None
    bounds_raw: Mapping[str, Any] | None = None
    for key in ("clickboxBounds", "convexHullBounds", "canvasTileBounds", "bounds"):
        candidate = projection.get(key)
        if candidate is not None:
            bounds_raw = _mapping(candidate, f"object.projection.{key}")
            break
    polygon_raw = projection.get("authoritativePolygon")
    geometry_source = projection.get("authoritativeGeometrySource")
    polygon_path = "object.projection.authoritativePolygon"
    if polygon_raw is None and projection.get("canvasTilePolygon") is not None:
        polygon_raw = projection.get("canvasTilePolygon")
        geometry_source = "canvas_tile"
        polygon_path = "object.projection.canvasTilePolygon"
    if geometry_source is not None and geometry_source not in {
        "clickbox", "convex_hull", "canvas_tile"
    }:
        raise ObservationSchemaError(
            "object projection authoritative geometry source is invalid"
        )
    if polygon_raw is not None and geometry_source is None:
        raise ObservationSchemaError(
            "object projection polygon requires an authoritative geometry source"
        )
    screen_polygon = (
        transform.polygon(polygon_raw, polygon_path)
        if transform and polygon_raw is not None
        else ()
    )
    if geometry_source == "canvas_tile" and screen_polygon:
        screen_polygon = _convex_screen_hull(screen_polygon)
    available = _boolean(projection.get("geometryAvailable"), "object.geometryAvailable")
    on_screen = _boolean(projection.get("onScreen"), "object.onScreen")
    visible = _boolean(projection.get("visible"), "object.visible")
    raw_actionable = _boolean(projection.get("actionableByCanvas", projection.get("actionable")),
                              "object.actionableByCanvas")
    ratio = _number(projection.get("visibleAreaRatio"), "object.visibleAreaRatio", optional=True)
    edge_distance = _number(
        projection.get("edgeDistancePx"),
        "object.edgeDistancePx",
        optional=True,
    )
    scene_supported = _optional_boolean(
        projection.get("sceneSupported"), "object.sceneSupported"
    )
    collision_supported = _optional_boolean(
        projection.get("collisionSupported"), "object.collisionSupported"
    )
    shortcut_clear = _optional_boolean(
        projection.get("shortcutClear"), "object.shortcutClear"
    )
    authoritative_geometry_complete = geometry_source is None or bool(screen_polygon)
    return TargetGeometry(available=available, on_screen=on_screen, visible=visible,
                          actionable=(raw_actionable and screen_point is not None
                                      and authoritative_geometry_complete),
                          canvas_point=canvas_point, screen_point=screen_point,
                           screen_bounds=transform.bounds(bounds_raw) if transform else None,
                           geometry_source=geometry_source,
                           screen_polygon=screen_polygon,
                           visible_area_ratio=ratio,
                           edge_distance_px=edge_distance,
                           scene_supported=scene_supported,
                           collision_supported=collision_supported,
                           shortcut_clear=shortcut_clear)

def _inventory(payloads: Mapping[str, Any]) -> InventoryObservation:
    outer = _payload(payloads, "inventory")
    raw = _mapping(outer.get("inventory"), "payloads.inventory.inventory", optional=True)
    if not raw:
        return InventoryObservation()
    slot_count = _integer(raw.get("slotCount", 28), "inventory.slotCount")
    items_value = _bounded_list(
        raw.get("items", []), "inventory.items", MAX_INVENTORY_ITEM_ROWS
    )
    items: list[InventoryItem] = []
    for index, value in enumerate(items_value):
        item = _mapping(value, f"inventory.items[{index}]")
        slot = _integer(item.get("slot"), f"inventory.items[{index}].slot")
        item_id = _integer(item.get("itemId"), f"inventory.items[{index}].itemId")
        quantity = _integer(item.get("quantity"), f"inventory.items[{index}].quantity")
        name = item.get("name")
        if not 0 <= slot < slot_count or quantity <= 0 or (name is not None and not isinstance(name, str)):
            raise ObservationSchemaError(f"inventory.items[{index}] has invalid values")
        items.append(InventoryItem(slot, item_id, quantity, name))
    occupied = _integer(raw.get("occupiedSlots", raw.get("filledSlots", len({item.slot for item in items}))), "inventory.occupiedSlots")
    free = _integer(raw.get("freeSlots", max(0, slot_count - occupied)), "inventory.freeSlots")
    known = _boolean(raw.get("known"), "inventory.known")
    if slot_count <= 0 or min(occupied, free) < 0 or occupied + free > slot_count:
        raise ObservationSchemaError("inventory slot counts are inconsistent")
    unique_slots = {item.slot for item in items}
    if len(unique_slots) != len(items):
        raise ObservationSchemaError("inventory contains duplicate slots")
    if known and (occupied + free != slot_count or len(unique_slots) != occupied):
        raise ObservationSchemaError("known inventory items and slot counts disagree")
    return InventoryObservation(tuple(sorted(items, key=lambda item: item.slot)), slot_count, occupied, free, known)


def _equipment(payloads: Mapping[str, Any]) -> EquipmentObservation:
    outer = _payload(payloads, "inventory")
    raw = _mapping(
        outer.get("equipment"),
        "payloads.inventory.equipment",
        optional=True,
    )
    if not raw:
        return EquipmentObservation()
    slot_count = _integer(raw.get("slotCount", 14), "equipment.slotCount")
    if not 0 < slot_count <= MAX_EQUIPMENT_ITEM_ROWS:
        raise ObservationSchemaError(
            f"equipment.slotCount must be between 1 and {MAX_EQUIPMENT_ITEM_ROWS}"
        )
    items_value = _bounded_list(
        raw.get("items", []), "equipment.items", MAX_EQUIPMENT_ITEM_ROWS
    )
    items: list[InventoryItem] = []
    for index, value in enumerate(items_value):
        item = _mapping(value, f"equipment.items[{index}]")
        slot = _integer(item.get("slot"), f"equipment.items[{index}].slot")
        item_id = _integer(
            item.get("itemId"), f"equipment.items[{index}].itemId"
        )
        quantity = _integer(
            item.get("quantity"), f"equipment.items[{index}].quantity"
        )
        name = item.get("name")
        if (
            not 0 <= slot < slot_count
            or item_id <= 0
            or quantity <= 0
            or (name is not None and not isinstance(name, str))
        ):
            raise ObservationSchemaError(
                f"equipment.items[{index}] has invalid values"
            )
        items.append(InventoryItem(slot, item_id, quantity, name))
    unique_slots = {item.slot for item in items}
    if len(unique_slots) != len(items):
        raise ObservationSchemaError("equipment contains duplicate slots")
    occupied = _integer(
        raw.get("occupiedSlots", raw.get("filledSlots", len(unique_slots))),
        "equipment.occupiedSlots",
    )
    free = _integer(
        raw.get("freeSlots", max(0, slot_count - occupied)),
        "equipment.freeSlots",
    )
    known = _boolean(raw.get("known"), "equipment.known")
    if min(occupied, free) < 0 or occupied + free > slot_count:
        raise ObservationSchemaError("equipment slot counts are inconsistent")
    if known and (
        occupied + free != slot_count or len(unique_slots) != occupied
    ):
        raise ObservationSchemaError(
            "known equipment items and slot counts disagree"
        )
    if not known and (items or occupied):
        raise ObservationSchemaError(
            "unknown equipment cannot contain item evidence"
        )
    return EquipmentObservation(
        tuple(sorted(items, key=lambda item: item.slot)),
        slot_count,
        occupied,
        free,
        known,
    )


def _menu_state(
    payloads: Mapping[str, Any], transform: _CanvasTransform | None
) -> tuple[
    tuple[MenuEntry, ...], int | None, ScreenPoint | None, bool,
    ScreenBounds | None,
]:
    interaction = _payload(payloads, "interaction_hot")
    menu = _mapping(interaction.get("postMenuSort", interaction.get("hoverMenu")), "interaction_hot.postMenuSort", optional=True)
    values = _bounded_list(
        menu.get("entries", []) if menu else [],
        "interaction_hot menu entries",
        MAX_MENU_ENTRY_ROWS,
    )
    menu_open = _boolean(menu.get("menuOpen"), "menu.menuOpen") if menu else False
    raw_menu_bounds = _mapping(
        menu.get("menuBounds"), "menu.menuBounds", optional=True
    ) if menu else {}
    menu_bounds = (
        transform.bounds(raw_menu_bounds)
        if menu_open and transform and raw_menu_bounds
        else None
    )
    entries: list[MenuEntry] = []
    for index, value in enumerate(values):
        raw = _mapping(value, f"menu.entries[{index}]")
        option, target, entry_type = raw.get("option"), raw.get("target", ""), raw.get("type")
        if not all(isinstance(field, str) for field in (option, target, entry_type)):
            raise ObservationSchemaError(f"menu.entries[{index}] has invalid text fields")
        row_bounds = None
        if menu_bounds is not None and transform is not None:
            x = _number(raw_menu_bounds.get("x"), "menu.menuBounds.x")
            y = _number(raw_menu_bounds.get("y"), "menu.menuBounds.y")
            width = _number(raw_menu_bounds.get("width", raw_menu_bounds.get("w")), "menu.menuBounds.width")
            height = _number(raw_menu_bounds.get("height", raw_menu_bounds.get("h")), "menu.menuBounds.height")
            scroll = _integer(
                raw_menu_bounds.get("scroll"),
                "menu.menuBounds.scroll",
                optional=True,
            ) or 0
            if width <= 2 or height <= 0 or scroll < 0:
                raise ObservationSchemaError("open menu bounds are invalid")
            visual_index = index - scroll
            row_y = y + 19 + 15 * visual_index
            if visual_index >= 0 and row_y + 15 <= y + height:
                # RuneLite renders each option on a fixed 15-canvas-pixel row
                # below the 19-pixel menu header. Stay one pixel inside the
                # horizontal menu edge because the edge itself is not a hit.
                row_bounds = transform.bounds({
                    "x": x + 1,
                    "y": row_y,
                    "width": width - 1,
                    "height": 15,
                })
        entries.append(MenuEntry(option=option, target=unescape(_TAG.sub("", target)).strip(), entry_type=entry_type,
                                 identifier=_integer(raw.get("identifier"), f"menu.entries[{index}].identifier"),
                                 param0=_integer(raw.get("param0"), f"menu.entries[{index}].param0", optional=True),
                                 param1=_integer(raw.get("param1"), f"menu.entries[{index}].param1", optional=True),
                                 row_bounds=row_bounds))
    if entries and menu:
        top_option = menu.get("topOption")
        top_target = menu.get("topTarget")
        top_identifier = menu.get("topIdentifier")
        if top_option is not None and top_option != entries[0].option:
            raise ObservationSchemaError("menu topOption disagrees with entries[0]")
        if top_target is not None:
            if not isinstance(top_target, str):
                raise ObservationSchemaError("menu topTarget must be a string")
            cleaned = unescape(_TAG.sub("", top_target)).strip()
            if cleaned != entries[0].target:
                raise ObservationSchemaError("menu topTarget disagrees with entries[0]")
        if top_identifier is not None and _integer(top_identifier, "menu.topIdentifier") != entries[0].identifier:
            raise ObservationSchemaError("menu topIdentifier disagrees with entries[0]")
    client_tick = _integer(menu.get("clientTick"), "menu.clientTick", optional=True) if menu else None
    mouse_point = None
    if menu and transform:
        mouse_point = transform.point(menu.get("mouseCanvasX"), menu.get("mouseCanvasY"))
    return tuple(entries), client_tick, mouse_point, menu_open, menu_bounds

@dataclass(frozen=True, slots=True)
class _ObjectParseEvidence:
    raw_row_count: int
    parsed_object_count: int
    duplicate_row_count: int
    duplicate_group_count: int
    conflicting_duplicate_keys: tuple[str, ...]
    omitted_unnamed_count: int


def _object_identity_signature(item: NearbyObject) -> tuple[Any, ...]:
    return (
        item.key,
        item.object_id,
        item.name,
        item.kind,
        item.location,
        item.scene_x,
        item.scene_y,
    )


def _object_fingerprint(item: NearbyObject) -> str:
    geometry = item.geometry
    value = {
        "actions": list(item.actions),
        "distance": item.distance,
        "geometry": {
            "available": geometry.available,
            "onScreen": geometry.on_screen,
            "visible": geometry.visible,
            "actionable": geometry.actionable,
            "canvasPoint": (
                None
                if geometry.canvas_point is None
                else [geometry.canvas_point.x, geometry.canvas_point.y]
            ),
            "screenPoint": (
                None
                if geometry.screen_point is None
                else [geometry.screen_point.x, geometry.screen_point.y]
            ),
            "screenBounds": (
                None
                if geometry.screen_bounds is None
                else [
                    geometry.screen_bounds.x,
                    geometry.screen_bounds.y,
                    geometry.screen_bounds.width,
                    geometry.screen_bounds.height,
                ]
            ),
            "geometrySource": geometry.geometry_source,
            "screenPolygon": [[point.x, point.y] for point in geometry.screen_polygon],
            "visibleAreaRatio": geometry.visible_area_ratio,
            "edgeDistancePx": geometry.edge_distance_px,
            "sceneSupported": geometry.scene_supported,
            "collisionSupported": geometry.collision_supported,
            "shortcutClear": geometry.shortcut_clear,
        },
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object_selection_key(item: NearbyObject) -> tuple[Any, ...]:
    geometry = item.geometry
    evidence_fields = sum(
        value is not None
        for value in (
            item.location,
            item.distance,
            item.scene_x,
            item.scene_y,
            geometry.canvas_point,
            geometry.screen_point,
            geometry.screen_bounds,
            geometry.geometry_source,
            geometry.visible_area_ratio,
            geometry.edge_distance_px,
            geometry.scene_supported,
            geometry.collision_supported,
            geometry.shortcut_clear,
        )
    )
    return (
        geometry.actionable,
        geometry.available,
        bool(geometry.screen_polygon),
        geometry.visible,
        geometry.on_screen,
        len(item.actions),
        evidence_fields,
        _object_fingerprint(item),
    )


def _nearby_objects(
    payloads: Mapping[str, Any],
    transform: _CanvasTransform | None,
    player_location: WorldPoint | None,
    requested_tiles: Mapping[str, WorldPoint],
) -> tuple[tuple[NearbyObject, ...], _ObjectParseEvidence]:
    census_name = "scene_object_census"
    census = _payload(payloads, census_name)
    values = _bounded_list(
        census.get("objects", []) if census else [],
        f"payloads.{census_name}.objects",
        MAX_SCENE_OBJECT_ROWS,
    )
    grouped: dict[str, list[NearbyObject]] = {}
    omitted_unnamed_count = 0
    for index, value in enumerate(values):
        raw = _mapping(value, f"{census_name}.objects[{index}]")
        key = raw.get("objectKey")
        name = raw.get("name", raw.get("objectName"))
        kind = raw.get("kind")
        if not all(isinstance(field, str) and field for field in (key, kind)):
            raise ObservationSchemaError(
                f"{census_name}.objects[{index}] lacks object identity"
            )
        if name is None or (isinstance(name, str) and not name.strip()):
            # A row without a definition name cannot be matched safely. Keep
            # the omission explicit in census evidence instead of fabricating
            # identity from another duplicate row.
            omitted_unnamed_count += 1
            continue
        if not isinstance(name, str):
            raise ObservationSchemaError(
                f"{census_name}.objects[{index}] has an invalid name"
            )
        path = f"{census_name}.objects[{index}]"
        actions = _string_tuple(raw.get("actions"), f"{path}.actions")
        location = _world_point(raw, f"{path}.location")
        reported_distance = _nonnegative_integer(
            raw.get("distanceToPlayer"), f"{path}.distanceToPlayer"
        )
        distance = (
            player_location.distance_to(location)
            if player_location is not None and location is not None
            else reported_distance
        )
        candidate = NearbyObject(
            key=key,
            object_id=_integer(raw.get("id"), f"{path}.id"),
            name=name,
            kind=kind,
            actions=actions,
            location=location,
            distance=distance,
            geometry=_target_geometry(raw, transform),
            scene_x=_integer(raw.get("sceneX"), f"{path}.sceneX", optional=True),
            scene_y=_integer(raw.get("sceneY"), f"{path}.sceneY", optional=True),
        )
        grouped.setdefault(key, []).append(candidate)

    objects: dict[str, NearbyObject] = {}
    duplicate_row_count = 0
    duplicate_group_count = 0
    conflicting_duplicate_keys: list[str] = []
    for key in sorted(grouped):
        candidates = grouped[key]
        if len(candidates) == 1:
            objects[key] = candidates[0]
            continue
        if len(candidates) > 1:
            duplicate_group_count += 1
            duplicate_row_count += len(candidates) - 1
        if len({_object_identity_signature(item) for item in candidates}) != 1:
            conflicting_duplicate_keys.append(key)
            continue
        # Select one complete row. Never union actions, minimize distance, or
        # borrow geometry from a different row.
        objects[key] = max(candidates, key=_object_selection_key)

    parsed_scene_count = len(objects)
    tile_payload = _payload(payloads, "tile_projection")
    tile_values = _bounded_list(
        tile_payload.get("tiles", []) if tile_payload else [],
        "payloads.tile_projection.tiles",
        MAX_TILE_PROJECTIONS,
    )
    seen_tile_labels: set[str] = set()
    for index, value in enumerate(tile_values):
        raw = _mapping(value, f"tile_projection.tiles[{index}]")
        if raw.get("status") not in {"PASS", "WARN"}:
            continue
        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}].label must be a string"
            )
        if label in seen_tile_labels or label in objects:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}].label is duplicated"
            )
        seen_tile_labels.add(label)
        requested = requested_tiles.get(label)
        if requested is None:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}] was not requested"
            )
        location = _world_point(raw, f"tile_projection.tiles[{index}].location") or requested
        if location != requested:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}] disagrees with the requested world location"
            )
        geometry = _target_geometry(raw, transform)
        objects[label] = NearbyObject(
            key=label,
            object_id=0,
            name=label,
            kind="NAVIGATION_TILE",
            actions=("Walk here",),
            location=location,
            distance=(
                player_location.distance_to(location) if player_location else None
            ),
            geometry=geometry,
            scene_x=_integer(raw.get("sceneX"), "tile.sceneX", optional=True),
            scene_y=_integer(raw.get("sceneY"), "tile.sceneY", optional=True),
        )
    ordered = tuple(objects[key] for key in sorted(objects))
    return ordered, _ObjectParseEvidence(
        raw_row_count=len(values),
        parsed_object_count=parsed_scene_count,
        duplicate_row_count=duplicate_row_count,
        duplicate_group_count=duplicate_group_count,
        conflicting_duplicate_keys=tuple(conflicting_duplicate_keys),
        omitted_unnamed_count=omitted_unnamed_count,
    )

def _widget_target(name: str, visible: bool, raw: Any, transform: _CanvasTransform | None) -> WidgetTarget | None:
    widget = _mapping(raw, f"bank_ui.{name}", optional=True)
    if not visible and not widget:
        return None
    bounds = transform.bounds(widget) if transform else None
    return WidgetTarget(name=name, visible=visible, screen_point=bounds.center if bounds else None, screen_bounds=bounds)

def _widgets(payloads: Mapping[str, Any], transform: _CanvasTransform | None) -> WidgetObservation:
    raw = _payload(payloads, "bank_ui")
    bank_known = _boolean(raw.get("known"), "bank_ui.known") if raw else False
    bank_open = _boolean(raw.get("bankOpen"), "bank_ui.bankOpen") if raw else False
    pin_open = _boolean(raw.get("bankPinOpen"), "bank_ui.bankPinOpen") if raw else False
    container_visible = _boolean(raw.get("bankContainerVisible"), "bank_ui.bankContainerVisible") if raw else False
    deposit_visible = _boolean(raw.get("depositInventoryButtonVisible"), "bank_ui.depositInventoryButtonVisible") if raw else False
    close_visible = _boolean(raw.get("closeButtonVisible", raw.get("bankCloseButtonVisible")), "bank_ui.closeButtonVisible") if raw else False
    readable = _boolean(raw.get("bankReadable"), "bank_ui.bankReadable", default=bank_open and container_visible) if raw else False
    keyboard_close = _boolean(raw.get("keyboardClosePossible"), "bank_ui.keyboardClosePossible") if raw else False
    dialogue = _payload(payloads, "dialogue_state")
    active = _boolean(dialogue.get("active"), "dialogue_state.active") if dialogue else False
    dialogue_type = dialogue.get("type", "none") if dialogue else "none"
    prompt = dialogue.get("promptText", "") if dialogue else ""
    if not isinstance(dialogue_type, str) or not isinstance(prompt, str):
        raise ObservationSchemaError("dialogue state text fields must be strings")
    option_values = _bounded_list(
        dialogue.get("options", []) if dialogue else [],
        "dialogue_state.options",
        MAX_DIALOGUE_OPTION_ROWS,
    )
    options: list[DialogueOption] = []
    for index, value in enumerate(option_values):
        option = _mapping(value, f"dialogue_state.options[{index}]")
        key, text = option.get("key"), option.get("text")
        if key is not None and not isinstance(key, str):
            raise ObservationSchemaError("dialogue option key must be a string")
        if not isinstance(text, str) or not text.strip():
            raise ObservationSchemaError("dialogue option text must be non-empty")
        options.append(DialogueOption(
            _integer(option.get("index"), "dialogue option index"), key,
            unescape(_TAG.sub("", text)).strip(),
            _boolean(option.get("visible"), "dialogue option visible", default=True),
        ))
    number_keys = _boolean(dialogue.get("canUseNumberKeys"), "dialogue_state.canUseNumberKeys") if dialogue else False
    dialogue_client_tick = _integer(
        dialogue.get("latestClientTick"), "dialogue_state.latestClientTick",
        optional=True,
    ) if dialogue else None
    return WidgetObservation(bank_known=bank_known, bank_open=bank_open, bank_pin_open=pin_open, bank_readable=readable,
                             keyboard_close_possible=keyboard_close,
                             deposit_inventory=_widget_target("deposit_inventory", deposit_visible, raw.get("depositInventoryButtonWidget"), transform),
                             close_bank=_widget_target("close_bank", close_visible, raw.get("closeButtonWidget"), transform),
                             dialogue_active=active, dialogue_type=dialogue_type,
                             dialogue_prompt=unescape(_TAG.sub("", prompt)).strip(),
                             dialogue_options=tuple(options), dialogue_number_keys=number_keys,
                             dialogue_client_tick=dialogue_client_tick)

def _timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise ObservationSchemaError(f"{path} must be a string")
    try:
        timestamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ObservationSchemaError(f"{path} is not a valid timestamp") from exc
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _FrameContract:
    frame_id: str
    geometry_frame_id: str | None
    source_tick: int
    captured_at: datetime
    completed_at: datetime
    session_id: str | None
    client_process_id: int | None
    coherent: bool


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ObservationSchemaError(f"{path} must be a string")
    return value or None


def _frame_contract(root: Mapping[str, Any], payloads: Mapping[str, Any]) -> _FrameContract:
    frame = _mapping(root.get("sensorFrame"), "sensorFrame")
    if frame.get("schema") != SENSOR_FRAME_SCHEMA:
        raise ObservationSchemaError(f"expected sensorFrame schema {SENSOR_FRAME_SCHEMA!r}")
    frame_id = frame.get("frameId")
    if not isinstance(frame_id, str) or not frame_id.strip():
        raise ObservationSchemaError("sensorFrame.frameId must be a non-empty string")
    source_tick = _integer(frame.get("sourceTick"), "sensorFrame.sourceTick")
    latest_tick = _integer(root.get("latestTick"), "latestTick")
    captured_at = _timestamp(frame.get("capturedAtUtc"), "sensorFrame.capturedAtUtc")
    completed_at = _timestamp(frame.get("completedAtUtc"), "sensorFrame.completedAtUtc")
    if completed_at < captured_at:
        raise ObservationSchemaError("sensorFrame.completedAtUtc precedes capturedAtUtc")
    coherent = _boolean(frame.get("coherent"), "sensorFrame.coherent")
    complete = _boolean(frame.get("complete"), "sensorFrame.complete")
    if source_tick != latest_tick:
        coherent = False

    facts = _mapping(frame.get("facts"), "sensorFrame.facts")
    for fact_name in CORE_FACT_NEEDS:
        fact = _mapping(facts.get(fact_name), f"sensorFrame.facts.{fact_name}")
        fact_tick = _integer(
            fact.get("sourceTick"), f"sensorFrame.facts.{fact_name}.sourceTick"
        )
        fact_captured = _timestamp(
            fact.get("capturedAtUtc"),
            f"sensorFrame.facts.{fact_name}.capturedAtUtc",
        )
        available = _boolean(
            fact.get("available"), f"sensorFrame.facts.{fact_name}.available"
        )
        _string_tuple(
            fact.get("errors"), f"sensorFrame.facts.{fact_name}.errors"
        )
        if fact_tick != source_tick or not (captured_at <= fact_captured <= completed_at):
            coherent = False
        if available != (fact_name in payloads):
            coherent = False
        if not available:
            complete = False

    session_id = _optional_string(frame.get("sessionId"), "sensorFrame.sessionId")
    client_process_id = _integer(
        frame.get("clientProcessId"), "sensorFrame.clientProcessId", optional=True
    )
    geometry_frame_id = _optional_string(
        frame.get("geometryFrameId"), "sensorFrame.geometryFrameId"
    )
    return _FrameContract(
        frame_id=frame_id,
        geometry_frame_id=geometry_frame_id,
        source_tick=source_tick,
        captured_at=captured_at,
        completed_at=completed_at,
        session_id=session_id,
        client_process_id=client_process_id,
        coherent=coherent and complete,
    )


def _menu_contract(
    payloads: Mapping[str, Any],
    freshness: Mapping[str, Any],
    frame: _FrameContract,
) -> tuple[bool, int | None, datetime | None, str | None, int | None]:
    interaction = _payload(payloads, "interaction_hot")
    if not interaction:
        return False, None, None, None, None
    source_tick = _integer(
        interaction.get("sourceTick"), "interaction_hot.sourceTick", optional=True
    )
    captured_at = (
        _timestamp(interaction.get("capturedAtUtc"), "interaction_hot.capturedAtUtc")
        if interaction.get("capturedAtUtc") is not None
        else None
    )
    session_id = _optional_string(
        interaction.get("sessionId"), "interaction_hot.sessionId"
    )
    process_id = _integer(
        interaction.get("clientProcessId"),
        "interaction_hot.clientProcessId",
        optional=True,
    )
    raw_fresh = _boolean(freshness.get("menuFresh"), "freshness.menuFresh")
    bound = bool(
        raw_fresh
        and source_tick == frame.source_tick
        and captured_at is not None
        and session_id == frame.session_id
        and process_id == frame.client_process_id
    )
    return bound, source_tick, captured_at, session_id, process_id


def _dynamic_sources_coherent(
    payloads: Mapping[str, Any],
    frame: _FrameContract,
    assembled_at: datetime,
    max_source_age_millis: int,
) -> bool:
    coherent = True
    for name in (
        "scene_object_census",
        "actor_census",
        "collision_window",
        "tile_projection",
    ):
        payload = _payload(payloads, name)
        if not payload:
            continue
        source_tick = _integer(
            payload.get("sourceTick", payload.get("tick")),
            f"payloads.{name}.sourceTick",
            optional=True,
        )
        session_id = _optional_string(
            payload.get("sessionId"), f"payloads.{name}.sessionId"
        )
        process_id = _integer(
            payload.get("clientProcessId"),
            f"payloads.{name}.clientProcessId",
            optional=True,
        )
        geometry_frame_id = _optional_string(
            payload.get("geometryFrameId"),
            f"payloads.{name}.geometryFrameId",
        )
        captured_at = (
            _timestamp(
                payload.get("capturedAtUtc"),
                f"payloads.{name}.capturedAtUtc",
            )
            if payload.get("capturedAtUtc") is not None
            else None
        )
        capture_age_millis = (
            (assembled_at - captured_at).total_seconds() * 1000.0
            if captured_at is not None
            else None
        )
        coherent &= bool(
            source_tick == frame.source_tick
            and session_id == frame.session_id
            and process_id == frame.client_process_id
            and geometry_frame_id == frame.geometry_frame_id
            and captured_at is not None
            and captured_at >= frame.completed_at
            and captured_at <= assembled_at + timedelta(seconds=2)
            and capture_age_millis is not None
            and -2000.0 <= capture_age_millis <= max_source_age_millis
        )
    return coherent


def _center_world_location(value: Any) -> WorldPoint | None:
    if value is None:
        return None
    raw = _mapping(value, "scene_object_census.centerWorldLocation")
    if any(key in raw for key in ("worldX", "worldY")):
        return _world_point(raw, "scene_object_census.centerWorldLocation")
    x = _integer(raw.get("x"), "scene_object_census.centerWorldLocation.x")
    y = _integer(raw.get("y"), "scene_object_census.centerWorldLocation.y")
    plane = _integer(
        raw.get("plane"), "scene_object_census.centerWorldLocation.plane"
    )
    if x < 0 or y < 0 or plane < 0:
        raise ObservationSchemaError(
            "scene_object_census.centerWorldLocation must be non-negative"
        )
    return WorldPoint(x, y, plane)


def _scene_census_evidence(
    payloads: Mapping[str, Any],
    object_evidence: _ObjectParseEvidence,
    requested_priority_object_ids: tuple[int, ...],
    requested_priority_object_keys: tuple[str, ...],
    *,
    strict_priority_request_match: bool = False,
) -> SceneCensusEvidence:
    census = _payload(payloads, "scene_object_census")
    if not census:
        return SceneCensusEvidence(
            requested_priority_object_ids=requested_priority_object_ids,
            requested_priority_object_keys=requested_priority_object_keys,
            duplicate_row_count=object_evidence.duplicate_row_count,
            duplicate_group_count=object_evidence.duplicate_group_count,
            conflicting_duplicate_keys=object_evidence.conflicting_duplicate_keys,
            omitted_unnamed_count=object_evidence.omitted_unnamed_count,
            parsed_object_count=object_evidence.parsed_object_count,
        )
    source_schema = _optional_string(
        census.get("schema"), "scene_object_census.schema"
    )
    if source_schema not in {"scene_object_census.v1", "scene_object_census.v2"}:
        raise ObservationSchemaError(
            "scene_object_census schema must be scene_object_census.v1 or v2"
        )

    base_names = (
        "count",
        "returned",
        "capHit",
        "objectCensusCapHit",
    )
    priority_id_names = (
        "priorityObjectIds",
        "returnedPriorityObjectIds",
        "priorityObjectsComplete",
    )
    priority_key_names = (
        "priorityObjectKeys",
        "returnedPriorityObjectKeys",
    )
    metadata_present = any(name in census for name in base_names)
    if metadata_present and any(name not in census for name in base_names):
        missing = sorted(name for name in base_names if name not in census)
        raise ObservationSchemaError(
            "scene_object_census completeness metadata is partial: "
            + ", ".join(missing)
        )
    if not metadata_present:
        return SceneCensusEvidence(
            source_schema=source_schema,
            requested_priority_object_ids=requested_priority_object_ids,
            requested_priority_object_keys=requested_priority_object_keys,
            duplicate_row_count=object_evidence.duplicate_row_count,
            duplicate_group_count=object_evidence.duplicate_group_count,
            conflicting_duplicate_keys=object_evidence.conflicting_duplicate_keys,
            omitted_unnamed_count=object_evidence.omitted_unnamed_count,
            parsed_object_count=object_evidence.parsed_object_count,
        )
    priority_id_metadata_present = any(
        name in census for name in priority_id_names
    )
    if priority_id_metadata_present and any(
        name not in census for name in priority_id_names
    ):
        missing = sorted(
            name for name in priority_id_names if name not in census
        )
        raise ObservationSchemaError(
            "scene_object_census priority ID metadata is partial: "
            + ", ".join(missing)
        )
    priority_key_metadata_present = any(
        name in census for name in priority_key_names
    )
    if priority_key_metadata_present and any(
        name not in census for name in priority_key_names
    ):
        missing = sorted(
            name for name in priority_key_names if name not in census
        )
        raise ObservationSchemaError(
            "scene_object_census priority key metadata is partial: "
            + ", ".join(missing)
        )

    count = _nonnegative_integer(census.get("count"), "scene_object_census.count")
    returned = _nonnegative_integer(
        census.get("returned"), "scene_object_census.returned"
    )
    response_cap_hit = _boolean(
        census.get("capHit"), "scene_object_census.capHit"
    )
    if "responseCapHit" in census:
        explicit_response_cap_hit = _boolean(
            census.get("responseCapHit"), "scene_object_census.responseCapHit"
        )
        if explicit_response_cap_hit != response_cap_hit:
            raise ObservationSchemaError(
                "scene_object_census responseCapHit disagrees with capHit"
            )
    source_cap_hit = _boolean(
        census.get("objectCensusCapHit"),
        "scene_object_census.objectCensusCapHit",
    )
    if count < returned:
        raise ObservationSchemaError(
            "scene_object_census count cannot be less than returned"
        )
    if returned != object_evidence.raw_row_count:
        raise ObservationSchemaError(
            "scene_object_census returned disagrees with objects length"
        )
    if response_cap_hit != (count > returned):
        raise ObservationSchemaError(
            "scene_object_census capHit disagrees with count and returned"
        )

    reported_priority_ids = (
        _positive_integer_tuple(
            census.get("priorityObjectIds"),
            "scene_object_census.priorityObjectIds",
        )
        if priority_id_metadata_present
        else ()
    )
    returned_priority_ids = (
        _positive_integer_tuple(
            census.get("returnedPriorityObjectIds"),
            "scene_object_census.returnedPriorityObjectIds",
        )
        if priority_id_metadata_present
        else ()
    )
    reported_priority_keys = (
        _nonempty_string_tuple(
            census.get("priorityObjectKeys"),
            "scene_object_census.priorityObjectKeys",
        )
        if priority_key_metadata_present
        else ()
    )
    returned_priority_keys = (
        _nonempty_string_tuple(
            census.get("returnedPriorityObjectKeys"),
            "scene_object_census.returnedPriorityObjectKeys",
        )
        if priority_key_metadata_present
        else ()
    )
    priority_key_request_mismatch = (
        priority_key_metadata_present
        and reported_priority_keys != requested_priority_object_keys
    )
    priority_id_request_mismatch = (
        priority_id_metadata_present
        and reported_priority_ids != requested_priority_object_ids
    )
    if priority_key_request_mismatch and (
        strict_priority_request_match or requested_priority_object_keys
    ):
        raise ObservationSchemaError(
            "scene_object_census priorityObjectKeys disagree with the request"
        )
    if priority_id_request_mismatch and (
        strict_priority_request_match or requested_priority_object_ids
    ):
        raise ObservationSchemaError(
            "scene_object_census priorityObjectIds disagree with the request"
        )
    if not set(returned_priority_ids).issubset(reported_priority_ids):
        raise ObservationSchemaError(
            "returnedPriorityObjectIds must be a subset of priorityObjectIds"
        )
    if not set(returned_priority_keys).issubset(reported_priority_keys):
        raise ObservationSchemaError(
            "returnedPriorityObjectKeys must be a subset of priorityObjectKeys"
        )
    raw_rows = _bounded_list(
        census.get("objects", []),
        "payloads.scene_object_census.objects",
        MAX_SCENE_OBJECT_ROWS,
    )
    returned_row_ids = {
        _integer(
            _mapping(row, f"scene_object_census.objects[{index}]").get("id"),
            f"scene_object_census.objects[{index}].id",
        )
        for index, row in enumerate(raw_rows)
    }
    returned_row_keys = {
        _mapping(row, f"scene_object_census.objects[{index}]").get("objectKey")
        for index, row in enumerate(raw_rows)
    }
    expected_returned_priority_ids = tuple(
        object_id
        for object_id in reported_priority_ids
        if object_id in returned_row_ids
    )
    if returned_priority_ids != expected_returned_priority_ids:
        raise ObservationSchemaError(
            "returnedPriorityObjectIds disagree with returned object rows"
        )
    expected_returned_priority_keys = tuple(
        key for key in reported_priority_keys if key in returned_row_keys
    )
    if returned_priority_keys != expected_returned_priority_keys:
        raise ObservationSchemaError(
            "returnedPriorityObjectKeys disagree with returned object rows"
        )
    priority_keys_complete = (
        len(returned_priority_keys) == len(reported_priority_keys)
        if priority_key_metadata_present
        else None
    )
    priority_objects_complete = (
        _boolean(
            census.get("priorityObjectsComplete"),
            "scene_object_census.priorityObjectsComplete",
        )
        if priority_id_metadata_present
        else None
    )
    if priority_objects_complete is not None:
        expected_priority_complete = (
            len(returned_priority_ids) == len(reported_priority_ids)
            and (
                priority_keys_complete is not False
                if priority_key_metadata_present
                else True
            )
        )
        if priority_objects_complete != expected_priority_complete:
            raise ObservationSchemaError(
                "priorityObjectsComplete disagrees with priority object lists"
            )

    scene_coverage_complete = _optional_boolean(
        census.get("sceneCoverageComplete"),
        "scene_object_census.sceneCoverageComplete",
    )
    reported_census_complete = _optional_boolean(
        census.get("censusComplete"), "scene_object_census.censusComplete"
    )
    authoritative_absence_eligible = _optional_boolean(
        census.get("authoritativeAbsenceEligible"),
        "scene_object_census.authoritativeAbsenceEligible",
    )
    priority_absence_eligible = _optional_boolean(
        census.get("priorityAbsenceEligible"),
        "scene_object_census.priorityAbsenceEligible",
    )
    if reported_census_complete is True and (
        source_cap_hit or scene_coverage_complete is False
    ):
        raise ObservationSchemaError(
            "scene_object_census censusComplete contradicts source cap or coverage evidence"
        )
    if reported_census_complete is not None:
        complete = reported_census_complete
    elif scene_coverage_complete is not None:
        complete = bool(scene_coverage_complete and not source_cap_hit)
    elif source_cap_hit:
        complete = False
    else:
        # Legacy count/cap metadata did not prove that every tile in the raw
        # radius was scanned. Do not infer completeness merely from no cap.
        complete = None
    if authoritative_absence_eligible is True and (
        complete is not True or response_cap_hit
    ):
        raise ObservationSchemaError(
            "authoritativeAbsenceEligible requires a complete uncapped response"
        )
    if priority_absence_eligible is True and complete is not True:
        raise ObservationSchemaError(
            "priorityAbsenceEligible requires a complete raw census"
        )
    if (
        requested_priority_object_ids and not priority_id_metadata_present
    ) or (
        requested_priority_object_keys and not priority_key_metadata_present
    ):
        priority_absence_eligible = False
    if priority_id_request_mismatch or priority_key_request_mismatch:
        # Legacy readers may inspect a stale or foreign response, but its
        # unsolicited priority tuple can never authorize negative proof.
        priority_absence_eligible = False

    source_contradictory_duplicate_count = _nonnegative_integer(
        census.get("contradictoryDuplicateCount"),
        "scene_object_census.contradictoryDuplicateCount",
    )
    source_conflicting_duplicate_keys = _nonempty_string_tuple(
        census.get("contradictoryObjectKeys", []),
        "scene_object_census.contradictoryObjectKeys",
        maximum=MAX_SCENE_OBJECT_ROWS,
    )
    if (
        source_contradictory_duplicate_count is not None
        and len(source_conflicting_duplicate_keys)
        > source_contradictory_duplicate_count
    ):
        raise ObservationSchemaError(
            "contradictoryObjectKeys exceed contradictoryDuplicateCount"
        )
    conflicting_duplicate_keys = tuple(
        sorted(
            set(source_conflicting_duplicate_keys)
            | set(object_evidence.conflicting_duplicate_keys)
        )
    )
    if (
        conflicting_duplicate_keys
        or object_evidence.omitted_unnamed_count
        or bool(source_contradictory_duplicate_count)
    ):
        authoritative_absence_eligible = False
    if set(conflicting_duplicate_keys).intersection(reported_priority_keys):
        priority_absence_eligible = False

    def optional_count(name: str) -> int | None:
        return _nonnegative_integer(
            census.get(name), f"scene_object_census.{name}"
        )

    anchor_source = _optional_string(
        census.get("anchorSource"), "scene_object_census.anchorSource"
    )
    return SceneCensusEvidence(
        source_schema=source_schema,
        metadata_present=True,
        complete=complete,
        authoritative_absence_eligible=authoritative_absence_eligible,
        priority_absence_eligible=priority_absence_eligible,
        scene_coverage_complete=scene_coverage_complete,
        count=count,
        returned=returned,
        response_cap_hit=response_cap_hit,
        source_cap_hit=source_cap_hit,
        center_world_location=_center_world_location(
            census.get("centerWorldLocation")
        ),
        anchor_source=anchor_source,
        radius_tiles=optional_count("radiusTiles"),
        requested_tile_count=optional_count("requestedTileCount"),
        scanned_tile_slots=optional_count("scannedTileSlots"),
        scanned_tiles=optional_count("scannedTiles"),
        missing_tile_count=optional_count("missingTileCount"),
        discovered_object_count=optional_count("discoveredObjectCount"),
        source_duplicate_object_count=optional_count("duplicateObjectCount"),
        source_contradictory_duplicate_count=(
            source_contradictory_duplicate_count
        ),
        indexed_object_count=optional_count("indexedObjectCount"),
        enriched_object_count=optional_count("enrichedObjectCount"),
        projected_object_count=optional_count("projectedObjectCount"),
        requested_priority_object_ids=requested_priority_object_ids,
        requested_priority_object_keys=requested_priority_object_keys,
        reported_priority_object_ids=reported_priority_ids,
        returned_priority_object_ids=returned_priority_ids,
        priority_objects_complete=priority_objects_complete,
        reported_priority_object_keys=reported_priority_keys,
        returned_priority_object_keys=returned_priority_keys,
        priority_keys_complete=priority_keys_complete,
        duplicate_row_count=object_evidence.duplicate_row_count,
        duplicate_group_count=object_evidence.duplicate_group_count,
        conflicting_duplicate_keys=conflicting_duplicate_keys,
        omitted_unnamed_count=object_evidence.omitted_unnamed_count,
        parsed_object_count=object_evidence.parsed_object_count,
    )


def _observation_pipeline_evidence(
    root: Mapping[str, Any],
    frame: _FrameContract,
) -> ObservationPipelineEvidence:
    world_model = _mapping(root.get("worldModel"), "worldModel", optional=True)
    pipeline_value = root.get("pipeline")
    if pipeline_value is None:
        pipeline_value = world_model.get("pipeline")
    pipeline = _mapping(pipeline_value, "pipeline", optional=True)
    source_schema = _optional_string(pipeline.get("schema"), "pipeline.schema")
    if source_schema is not None and source_schema != "world_model_pipeline.v1":
        raise ObservationSchemaError(
            "pipeline schema must be world_model_pipeline.v1"
        )
    quality_value = root.get("worldModelQuality")
    if quality_value is None:
        quality_value = world_model.get("quality")
    quality = _mapping(quality_value, "worldModelQuality", optional=True)
    sizing = _mapping(root.get("responseSizing"), "responseSizing", optional=True)
    diagnostics_value = root.get("queryDiagnostics")
    if diagnostics_value is None:
        diagnostics_value = world_model.get("queryDiagnostics")
    diagnostics = _mapping(
        diagnostics_value, "queryDiagnostics", optional=True
    )
    endpoint_diagnostics = _mapping(
        root.get("endpointQueueDiagnostics"),
        "endpointQueueDiagnostics",
        optional=True,
    )

    def pipeline_count(name: str) -> int | None:
        return _nonnegative_integer(pipeline.get(name), f"pipeline.{name}")

    def sizing_count(name: str) -> int | None:
        return _nonnegative_integer(sizing.get(name), f"responseSizing.{name}")

    def diagnostics_count(name: str) -> int | None:
        return _nonnegative_integer(
            diagnostics.get(name), f"queryDiagnostics.{name}"
        )

    def endpoint_count(name: str) -> int | None:
        return _nonnegative_integer(
            endpoint_diagnostics.get(name),
            f"endpointQueueDiagnostics.{name}",
        )

    operation_counts_raw = _mapping(
        pipeline.get("operationCounts"), "pipeline.operationCounts", optional=True
    )
    operation_count_map: dict[str, int] = {}
    for name in sorted(operation_counts_raw):
        if not isinstance(name, str) or not name:
            raise ObservationSchemaError(
                "pipeline.operationCounts keys must be non-empty strings"
            )
        count = _nonnegative_integer(
            operation_counts_raw[name], f"pipeline.operationCounts.{name}",
            optional=False,
        )
        operation_count_map[name] = count
    for name in (
        "requestedTileCount",
        "scannedTileSlots",
        "scannedTiles",
        "missingTileCount",
        "discoveredObjectCount",
        "duplicateObjectCount",
        "contradictoryDuplicateCount",
        "indexedObjectCount",
        "filteredObjectCount",
        "enrichedObjectCount",
        "enrichmentCacheHits",
        "projectedObjectCount",
        "projectionCacheHits",
        "returnedObjectCount",
        "totalEnrichedObjectCount",
        "totalProjectedObjectCount",
    ):
        if name not in pipeline:
            continue
        count = pipeline_count(name)
        if count is None:
            raise ObservationSchemaError(f"pipeline.{name} must be an integer")
        if name in operation_count_map and operation_count_map[name] != count:
            raise ObservationSchemaError(
                f"pipeline operation count {name} is contradictory"
            )
        operation_count_map[name] = count

    refresh_sequence = pipeline_count("refreshSequence")
    if refresh_sequence is None:
        refresh_sequence = _nonnegative_integer(
            quality.get("refreshSequence"),
            "worldModelQuality.refreshSequence",
        )
    refresh_reason = _optional_string(
        pipeline.get("reason", pipeline.get("refreshReason")),
        "pipeline.reason",
    )
    if refresh_reason is None:
        refresh_reason = _optional_string(
            quality.get("refreshReason"), "worldModelQuality.refreshReason"
        )
    request_id = _optional_string(root.get("requestId"), "requestId")
    pipeline_source_tick = pipeline_count("sourceTick")
    pipeline_session_id = _optional_string(
        pipeline.get("sessionId"), "pipeline.sessionId"
    )
    pipeline_process_id = _integer(
        pipeline.get("clientProcessId"),
        "pipeline.clientProcessId",
        optional=True,
    )
    if pipeline_process_id is not None and pipeline_process_id <= 0:
        raise ObservationSchemaError("pipeline.clientProcessId must be positive")
    pipeline_geometry_frame_id = _optional_string(
        pipeline.get("geometryFrameId"), "pipeline.geometryFrameId"
    )
    for name, actual, expected in (
        ("sourceTick", pipeline_source_tick, frame.source_tick),
        ("sessionId", pipeline_session_id, frame.session_id),
        ("clientProcessId", pipeline_process_id, frame.client_process_id),
        ("geometryFrameId", pipeline_geometry_frame_id, frame.geometry_frame_id),
    ):
        if actual is not None and actual != expected:
            raise ObservationSchemaError(
                f"pipeline.{name} disagrees with the sensor frame"
            )
    diagnostics_schema = _optional_string(
        diagnostics.get("schema"), "queryDiagnostics.schema"
    )
    if diagnostics_schema is not None and (
        diagnostics_schema != "client_thread_query_diagnostics.v1"
    ):
        raise ObservationSchemaError(
            "queryDiagnostics schema must be client_thread_query_diagnostics.v1"
        )
    if diagnostics and diagnostics_schema is None:
        raise ObservationSchemaError("queryDiagnostics schema is required")
    endpoint_queue_schema = _optional_string(
        endpoint_diagnostics.get("schema"),
        "endpointQueueDiagnostics.schema",
    )
    if endpoint_queue_schema is not None and (
        endpoint_queue_schema
        != "plugin_snapshot_endpoint_queue_diagnostics.v1"
    ):
        raise ObservationSchemaError(
            "endpointQueueDiagnostics schema is unsupported"
        )
    if endpoint_diagnostics and endpoint_queue_schema is None:
        raise ObservationSchemaError(
            "endpointQueueDiagnostics schema is required"
        )
    transport = getattr(root, "transport_evidence", None)
    if transport is not None and not isinstance(transport, _TransportEvidence):
        raise ObservationSchemaError("local transport evidence is invalid")
    return ObservationPipelineEvidence(
        source_schema=source_schema,
        request_id=request_id,
        query_sequence=pipeline_count("querySequence"),
        query_purpose=_optional_string(
            pipeline.get("queryPurpose"), "pipeline.queryPurpose"
        ),
        source_tick=pipeline_source_tick,
        client_tick=pipeline_count("clientTick"),
        session_id=pipeline_session_id,
        process_id=pipeline_process_id,
        geometry_frame_id=pipeline_geometry_frame_id,
        raw_cache_key=_optional_string(
            pipeline.get("rawCacheKey"), "pipeline.rawCacheKey"
        ),
        response_bytes=(transport.response_bytes if transport is not None else None),
        http_millis=(transport.http_millis if transport is not None else None),
        decode_millis=(transport.decode_millis if transport is not None else None),
        service_timing_millis=_nonnegative_number(
            root.get("serviceTimingMillis"), "serviceTimingMillis"
        ),
        cache_hit=_optional_boolean(pipeline.get("cacheHit"), "pipeline.cacheHit"),
        cache_miss=_optional_boolean(
            pipeline.get("cacheMiss"), "pipeline.cacheMiss"
        ),
        cache_entries=pipeline_count("cacheEntries"),
        cache_hits=pipeline_count("cacheHits"),
        cache_misses=pipeline_count("cacheMisses"),
        refresh_sequence=refresh_sequence,
        refresh_reason=refresh_reason,
        refresh_duration_millis=_nonnegative_number(
            pipeline.get("refreshDurationMillis"),
            "pipeline.refreshDurationMillis",
        ),
        query_duration_millis=_nonnegative_number(
            pipeline.get("queryDurationMillis"),
            "pipeline.queryDurationMillis",
        ),
        world_model_age_millis=_nonnegative_integer(
            quality.get("worldModelAgeMs"), "worldModelQuality.worldModelAgeMs"
        ),
        max_response_bytes=sizing_count("maxResponseBytes"),
        requested_projection_refs=sizing_count("requestedProjectionRefs"),
        effective_projection_refs=sizing_count("effectiveProjectionRefs"),
        projection_refs_before_cap=sizing_count("projectionRefsBeforeCap"),
        projection_refs_after_cap=sizing_count("projectionRefsAfterCap"),
        trimmed_projection_refs=sizing_count("trimmedProjectionRefs"),
        projection_refs_capped=_optional_boolean(
            sizing.get("projectionRefsCapped"),
            "responseSizing.projectionRefsCapped",
        ),
        serialization_passes=sizing_count("serializationPasses"),
        serialized_bytes_reused_for_write=_optional_boolean(
            sizing.get("serializedBytesReusedForWrite"),
            "responseSizing.serializedBytesReusedForWrite",
        ),
        operation_counts=tuple(sorted(operation_count_map.items())),
        query_diagnostics_schema=diagnostics_schema,
        query_lane=_optional_string(
            diagnostics.get("lane"), "queryDiagnostics.lane"
        ),
        query_status=_optional_string(
            diagnostics.get("requestStatus"),
            "queryDiagnostics.requestStatus",
        ),
        request_coalesced=_optional_boolean(
            diagnostics.get("requestCoalesced"),
            "queryDiagnostics.requestCoalesced",
        ),
        work_executed=_optional_boolean(
            diagnostics.get("workExecuted"),
            "queryDiagnostics.workExecuted",
        ),
        timeout_millis=_nonnegative_number(
            diagnostics.get("timeoutMillis"), "queryDiagnostics.timeoutMillis"
        ),
        queue_wait_millis=_nonnegative_number(
            diagnostics.get("queueWaitMillis"),
            "queryDiagnostics.queueWaitMillis",
        ),
        execution_millis=_nonnegative_number(
            diagnostics.get("executionMillis"),
            "queryDiagnostics.executionMillis",
        ),
        active_request_count=diagnostics_count("activeRequestCount"),
        pending_request_count=diagnostics_count("pendingRequestCount"),
        max_queue_depth=diagnostics_count("maxDepth"),
        submitted_request_count=diagnostics_count("submittedCount"),
        executed_request_count=diagnostics_count("executedCount"),
        coalesced_request_count=diagnostics_count("coalescedCount"),
        superseded_request_count=diagnostics_count("supersededCount"),
        timed_out_request_count=diagnostics_count("timedOutCount"),
        expired_before_execution_count=diagnostics_count(
            "expiredBeforeExecutionCount"
        ),
        late_result_count=diagnostics_count("lateResultCount"),
        failed_request_count=diagnostics_count("failedCount"),
        last_queue_wait_millis=_nonnegative_number(
            diagnostics.get("lastQueueWaitMillis"),
            "queryDiagnostics.lastQueueWaitMillis",
        ),
        max_queue_wait_millis=_nonnegative_number(
            diagnostics.get("maxQueueWaitMillis"),
            "queryDiagnostics.maxQueueWaitMillis",
        ),
        last_execution_millis=_nonnegative_number(
            diagnostics.get("lastExecutionMillis"),
            "queryDiagnostics.lastExecutionMillis",
        ),
        max_execution_millis=_nonnegative_number(
            diagnostics.get("maxExecutionMillis"),
            "queryDiagnostics.maxExecutionMillis",
        ),
        endpoint_queue_schema=endpoint_queue_schema,
        endpoint_worker_limit=endpoint_count("workerLimit"),
        endpoint_pending_capacity=endpoint_count("pendingCapacity"),
        endpoint_active_worker_count=endpoint_count("activeWorkerCount"),
        endpoint_pending_request_count=endpoint_count("pendingRequestCount"),
        endpoint_pending_remaining_capacity=endpoint_count(
            "pendingRemainingCapacity"
        ),
        endpoint_largest_worker_count=endpoint_count("largestWorkerCount"),
        endpoint_completed_request_count=endpoint_count(
            "completedRequestCount"
        ),
        endpoint_execution_rejection_count=endpoint_count(
            "executionRejectionCount"
        ),
        endpoint_rejection_policy=_optional_string(
            endpoint_diagnostics.get("rejectionPolicy"),
            "endpointQueueDiagnostics.rejectionPolicy",
        ),
        endpoint_snapshot_request_active=_optional_boolean(
            endpoint_diagnostics.get("snapshotRequestActive"),
            "endpointQueueDiagnostics.snapshotRequestActive",
        ),
        endpoint_busy_rejection_count=endpoint_count(
            "snapshotBusyRejectionCount"
        ),
        endpoint_executor_state=_optional_string(
            endpoint_diagnostics.get("executorState"),
            "endpointQueueDiagnostics.executorState",
        ),
    )


def _validate_planned_response_contract(
    contract: _PlannedResponseContract,
    location: WorldPoint | None,
    scene_census: SceneCensusEvidence,
    pipeline: ObservationPipelineEvidence,
) -> None:
    if not scene_census.metadata_present:
        raise ObservationSchemaError(
            "planned response is missing scene census contract metadata"
        )

    expected_center = contract.center_world_location or location
    if expected_center is None:
        raise ObservationSchemaError(
            "planned player-anchored response has no player location"
        )
    expected_anchor_source = (
        "explicit" if contract.center_world_location is not None else "player"
    )
    if scene_census.center_world_location != expected_center:
        raise ObservationSchemaError(
            "scene_object_census.centerWorldLocation disagrees with the planned request"
        )
    if scene_census.anchor_source != expected_anchor_source:
        raise ObservationSchemaError(
            "scene_object_census.anchorSource disagrees with the planned request"
        )
    if scene_census.radius_tiles != contract.radius_tiles:
        raise ObservationSchemaError(
            "scene_object_census.radiusTiles disagrees with the planned request"
        )
    if pipeline.query_purpose != contract.purpose:
        raise ObservationSchemaError(
            "pipeline.queryPurpose disagrees with the planned request"
        )


def _is_complete_planned_tile_projection_payload(
    payload: Mapping[str, Any],
    requested_tiles: tuple[tuple[str, WorldPoint], ...],
) -> bool:
    """Validate the request-bound identity shape of a successful tile lane."""
    rows = payload.get("tiles")
    if (
        payload.get("schema") != "tile_projection_response.v1"
        or payload.get("status") not in {"PASS", "WARN"}
        or not isinstance(rows, list)
        or len(rows) != len(requested_tiles)
    ):
        return False
    requested = dict(requested_tiles)
    seen: set[str] = set()
    row_statuses: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        label = row.get("label")
        status = row.get("status")
        if (
            row.get("schema") != "tile_projection.v1"
            or not isinstance(label, str)
            or label in seen
            or label not in requested
            or status not in {"PASS", "WARN"}
        ):
            return False
        point = requested[label]
        if (
            row.get("worldX") != point.x
            or row.get("worldY") != point.y
            or row.get("plane") != point.plane
        ):
            return False
        seen.add(label)
        row_statuses.append(status)
    expected_status = (
        "PASS" if all(status == "PASS" for status in row_statuses) else "WARN"
    )
    return seen == set(requested) and payload.get("status") == expected_status


def _is_transient_planned_world_model_handoff(
    root: Mapping[str, Any],
    payloads: Mapping[str, Any],
    observation: Observation,
    *,
    requested_tile_projections: tuple[tuple[str, WorldPoint], ...],
) -> bool:
    """Recognize only the endpoint's exact non-authoritative handoff shape.

    A snapshot request can straddle a SensorFrame/world-model refresh.  The
    endpoint then returns its still-coherent core as WARN and deliberately
    omits the rejected dynamic census.  That response is useful retry evidence,
    but it is never a planned observation and cannot satisfy the census lock.
    """
    raw_missing = root.get("missingCapabilities")
    raw_warnings = root.get("warnings")
    if (
        not isinstance(raw_missing, list)
        or any(not isinstance(value, str) for value in raw_missing)
        or not isinstance(raw_warnings, list)
        or any(not isinstance(value, str) for value in raw_warnings)
    ):
        return False

    missing = frozenset(observation.missing_capabilities)
    warnings = frozenset(observation.warnings)
    world_missing = frozenset({"scene_object_census"})
    world_warnings = frozenset({"world_model_provenance_mismatch"})
    interaction_missing = frozenset({"interaction_hot"})
    interaction_warnings = frozenset(
        {"menu_evidence_provenance_mismatch_or_stale"}
    )
    tile_missing = frozenset({"tile_projection"})
    tile_warnings = frozenset({"tile_projection_provenance_mismatch"})
    combined = "interaction_hot" in missing
    tile_handoff = "tile_projection" in missing
    expected_missing = (
        world_missing
        | (interaction_missing if combined else frozenset())
        | (tile_missing if tile_handoff else frozenset())
    )
    expected_warnings = (
        world_warnings
        | (interaction_warnings if combined else frozenset())
        | (tile_warnings if tile_handoff else frozenset())
    )

    if (
        observation.status != "WARN"
        or root.get("status") != "WARN"
        or observation.game_state != "LOGGED_IN"
        or observation.location is None
        or observation.tick < 0
        or not observation.fresh
        or not observation.cache_wall_clock_fresh
        or not observation.source_coherent
        or not observation.scene_playable
        or not observation.timestamp_not_future
        or observation.scene_census.metadata_present
        or "scene_object_census" in payloads
        or "worldModel" in root
        or "worldModelQuality" in root
        or "pipeline" in root
        or (
            tile_handoff
            and (
                not requested_tile_projections
                or "tile_projection" in payloads
                or "tileProjections" in root
            )
        )
        or missing != expected_missing
        or warnings != expected_warnings
        or frozenset(raw_missing) != missing
        or frozenset(raw_warnings) != warnings
        or len(raw_missing) != len(missing)
        or len(raw_warnings) != len(warnings)
    ):
        return False

    tile_payload = payloads.get("tile_projection")
    root_tile_payload = root.get("tileProjections")
    if requested_tile_projections:
        if tile_handoff:
            if (
                "tile_projection" in payloads
                or "tileProjections" in root
            ):
                return False
        elif not (
            isinstance(tile_payload, Mapping)
            and isinstance(root_tile_payload, Mapping)
            and dict(tile_payload) == dict(root_tile_payload)
            and _is_complete_planned_tile_projection_payload(
                tile_payload,
                requested_tile_projections,
            )
        ):
            return False
    elif (
        tile_handoff
        or "tile_projection" in payloads
        or "tileProjections" in root
    ):
        return False

    interaction = payloads.get("interaction_hot")
    root_interaction = root.get("clientTickHot")
    freshness = root.get("freshness")
    expected_menu_fresh = not combined
    return bool(
        isinstance(interaction, Mapping)
        and isinstance(root_interaction, Mapping)
        and isinstance(freshness, Mapping)
        and dict(interaction) == dict(root_interaction)
        and interaction.get("schema") == "client_tick_hot.v1"
        and interaction.get("sessionId") == observation.session_id
        and interaction.get("clientProcessId") == observation.client_process_id
        and observation.menu_source_tick is not None
        and observation.menu_source_tick >= 0
        and observation.menu_timestamp is not None
        and observation.menu_fresh is expected_menu_fresh
        and freshness.get("menuFresh") is expected_menu_fresh
    )


def parse_observation(
    value: Mapping[str, Any],
    tile_projections: Iterable[tuple[str, WorldPoint]] | None = None,
    priority_object_ids: Iterable[int] | None = None,
    priority_object_keys: Iterable[str] | None = None,
    *,
    _planned_response_contract: _PlannedResponseContract | None = None,
) -> Observation:
    parse_started = perf_counter()
    root = _mapping(value, "snapshot response")
    if root.get("schema") != RESPONSE_SCHEMA:
        raise ObservationSchemaError(f"expected schema {RESPONSE_SCHEMA!r}")
    payloads = _mapping(root.get("payloads"), "payloads")
    frame = _frame_contract(root, payloads)
    baseline = _payload(payloads, "baseline")
    input_geometry = _mapping(
        baseline.get("inputGeometry"), "payloads.baseline.inputGeometry",
        optional=True,
    )
    transform = _canvas_transform(baseline)
    client_window_bounds = _client_window_bounds(
        input_geometry,
        transform.canvas_bounds if transform else None,
    ) if input_geometry else None
    camera_viewport = _mapping(
        baseline.get("cameraViewport"),
        "payloads.baseline.cameraViewport",
        optional=True,
    )
    camera_yaw = _integer(
        camera_viewport.get("cameraYaw"),
        "payloads.baseline.cameraViewport.cameraYaw",
        optional=True,
    )
    camera_pitch = _integer(
        camera_viewport.get("cameraPitch"),
        "payloads.baseline.cameraViewport.cameraPitch",
        optional=True,
    )
    camera_zoom = _integer(
        camera_viewport.get("zoom3d"),
        "payloads.baseline.cameraViewport.zoom3d",
        optional=True,
    )
    text_input_active = _optional_boolean(
        baseline.get("textInputActive"),
        "payloads.baseline.textInputActive",
    )
    if camera_yaw is not None and not 0 <= camera_yaw < CAMERA_YAW_UNITS:
        raise ObservationSchemaError("cameraYaw is outside the fixed-point yaw range")
    if camera_pitch is not None and camera_pitch < 0:
        raise ObservationSchemaError("cameraPitch must be non-negative")
    if camera_zoom is not None and camera_zoom < 0:
        raise ObservationSchemaError("zoom3d must be non-negative")
    viewport_bounds = None
    if transform and camera_viewport:
        viewport_width = camera_viewport.get(
            "viewportWidth", camera_viewport.get("canvasWidth")
        )
        viewport_height = camera_viewport.get(
            "viewportHeight", camera_viewport.get("canvasHeight")
        )
        viewport_bounds = transform.bounds(
            {
                "x": camera_viewport.get("viewportXOffset", 0),
                "y": camera_viewport.get("viewportYOffset", 0),
                "width": viewport_width,
                "height": viewport_height,
            }
        )
    base_player = _mapping(baseline.get("player"), "payloads.baseline.player", optional=True)
    location = _world_point(base_player, "payloads.baseline.player location")
    player_screen_point = (
        transform.point(base_player.get("canvasX"), base_player.get("canvasY"))
        if transform
        else None
    )
    activity = _payload(payloads, "activity")
    interacting = _mapping(activity.get("interacting", base_player.get("interacting")), "activity.interacting", optional=True)
    interacting_type = interacting.get("type")
    if interacting_type is not None and not isinstance(interacting_type, str):
        raise ObservationSchemaError("activity.interacting.type must be a string")
    interacting_id = _integer(interacting.get("id"), "activity.interacting.id", optional=True)
    if interacting_id is not None and interacting_id < 0:
        interacting_id = None
    player = PlayerObservation(animation=_integer(activity.get("animation", base_player.get("animation")), "activity.animation", optional=True),
                               pose_animation=_integer(activity.get("poseAnimation", base_player.get("poseAnimation")), "activity.poseAnimation", optional=True),
                               interacting_type=None if interacting_type in (None, "", "UNKNOWN") else interacting_type, interacting_id=interacting_id,
                               run_energy_percent=_number(activity.get("runEnergyPercent", base_player.get("runEnergyPercent")), "activity.runEnergyPercent", optional=True))
    tiles = _normalize_tiles(tile_projections)
    requested_priority_ids = _normalize_priority_object_ids(priority_object_ids)
    requested_priority_keys = _normalize_priority_object_keys(priority_object_keys)
    freshness = _mapping(root.get("freshness"), "freshness", optional=True)
    game_state = baseline.get("gameState", "UNKNOWN")
    status = root.get("status")
    if not isinstance(game_state, str) or not isinstance(status, str):
        raise ObservationSchemaError("gameState and status must be strings")
    interaction = _payload(payloads, "interaction_hot")
    assembled_at = _timestamp(root.get("assembledAtUtc"), "assembledAtUtc")
    max_source_age_millis = _integer(
        freshness.get("maxSourceAgeMillis"),
        "freshness.maxSourceAgeMillis",
    )
    if max_source_age_millis < 0:
        raise ObservationSchemaError("freshness.maxSourceAgeMillis must be non-negative")
    menu_fresh, menu_source_tick, menu_timestamp, menu_session_id, menu_process_id = _menu_contract(
        payloads, freshness, frame
    )
    observed_process_id = _integer(
        input_geometry.get("clientProcessId", frame.client_process_id),
        "inputGeometry.clientProcessId",
        optional=True,
    ) if input_geometry else frame.client_process_id
    geometry_source_tick = _integer(
        input_geometry.get("sourceTick"),
        "inputGeometry.sourceTick",
        optional=True,
    ) if input_geometry else None
    source_coherent = bool(
        frame.coherent
        and _boolean(freshness.get("frameCoherent"), "freshness.frameCoherent")
        and frame.session_id
        and frame.client_process_id is not None
        and frame.client_process_id > 0
        and frame.geometry_frame_id
        and observed_process_id == frame.client_process_id
        and geometry_source_tick == frame.source_tick
        and _dynamic_sources_coherent(
            payloads,
            frame,
            assembled_at,
            max_source_age_millis,
        )
    )
    menus, menu_client_tick, menu_mouse_point, menu_open, menu_bounds = _menu_state(payloads, transform)
    inventory = _inventory(payloads)
    equipment = _equipment(payloads)
    widgets = _widgets(payloads, transform)
    nearby_objects, object_evidence = _nearby_objects(
        payloads, transform, location, dict(tiles)
    )
    index_started = perf_counter()
    scene_index = SceneIndex.build(nearby_objects)
    index_millis = (perf_counter() - index_started) * 1000.0
    scene_census = _scene_census_evidence(
        payloads,
        object_evidence,
        requested_priority_ids,
        requested_priority_keys,
        strict_priority_request_match=_planned_response_contract is not None,
    )
    pipeline = replace(
        _observation_pipeline_evidence(root, frame),
        parse_millis=(perf_counter() - parse_started) * 1000.0,
        index_millis=index_millis,
    )
    observation = Observation(player=player, location=location, plane=location.plane if location else None,
                       inventory=inventory, nearby_objects=nearby_objects,
                       menus=menus, widgets=widgets,
                       canvas_bounds=transform.canvas_bounds if transform else None,
                       viewport_bounds=viewport_bounds,
                       player_screen_point=player_screen_point, game_state=game_state,
                       timestamp=frame.captured_at, tick=frame.source_tick, status=status,
                       fresh=_boolean(freshness.get("fresh"), "freshness.fresh"),
                       cache_wall_clock_fresh=_boolean(freshness.get("sourceCaptureFresh"), "freshness.sourceCaptureFresh"),
                       client_window_bounds=client_window_bounds,
                       scene_playable=_boolean(baseline.get("scenePlayable"), "baseline.scenePlayable"),
                       session_id=frame.session_id, warnings=_string_tuple(root.get("warnings"), "warnings"),
                       missing_capabilities=_string_tuple(root.get("missingCapabilities"), "missingCapabilities"),
                       menu_client_tick=menu_client_tick, menu_mouse_screen_point=menu_mouse_point,
                       menu_open=menu_open, menu_bounds=menu_bounds,
                       client_focused=_boolean(input_geometry.get("isClientFocused"), "inputGeometry.isClientFocused") if input_geometry else False,
                       client_process_id=observed_process_id,
                       assembled_at=assembled_at, frame_id=frame.frame_id,
                       geometry_frame_id=frame.geometry_frame_id,
                       source_coherent=source_coherent, menu_fresh=menu_fresh,
                       menu_source_tick=menu_source_tick, menu_timestamp=menu_timestamp,
                       menu_session_id=menu_session_id, menu_process_id=menu_process_id,
                       camera_yaw=camera_yaw, camera_pitch=camera_pitch,
                       camera_zoom=camera_zoom,
                       text_input_active=text_input_active,
                       max_source_age_millis=max_source_age_millis,
                       scene_census=scene_census,
                       pipeline=pipeline,
                       equipment=equipment,
                       _prebuilt_scene_index=scene_index)
    if _planned_response_contract is not None:
        if _is_transient_planned_world_model_handoff(
            root,
            payloads,
            observation,
            requested_tile_projections=tiles,
        ):
            raise ObservationWorldModelHandoffError(
                observation.missing_capabilities
            )
        _validate_planned_response_contract(
            _planned_response_contract,
            location,
            scene_census,
            pipeline,
        )
    return observation

class ObservationClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8893", *, auth_token: str | None = None,
                 timeout_seconds: float = 3.0) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ObservationRequestError("base_url must be an HTTP(S) origin without a path")
        if timeout_seconds <= 0:
            raise ObservationRequestError("timeout_seconds must be positive")
        self._snapshot_url = urlunsplit((parts.scheme, parts.netloc, "/snapshot", "", ""))
        self._auth_token = auth_token.strip() if auth_token else None
        self._timeout_seconds = timeout_seconds

    def fetch(
        self,
        tile_projections: Iterable[tuple[str, WorldPoint]] | None = None,
        priority_object_ids: Iterable[int] | None = None,
        priority_object_keys: Iterable[str] | None = None,
    ) -> Observation:
        tiles = _normalize_tiles(tile_projections)
        object_ids = _normalize_priority_object_ids(priority_object_ids)
        object_keys = _normalize_priority_object_keys(priority_object_keys)
        payload = self._post_snapshot(
            build_snapshot_request(tiles, object_ids, object_keys)
        )
        return parse_observation(payload, tiles, object_ids, object_keys)

    def fetch_planned(self, request: object) -> Observation:
        """Execute one validated task query plan without widening legacy clients."""
        from .task_contract import ObservationRequest

        if not isinstance(request, ObservationRequest):
            raise ObservationRequestError("request must be ObservationRequest")
        tiles = _normalize_tiles(request.tile_projections)
        object_ids = _normalize_priority_object_ids(request.priority_object_ids)
        object_keys = _normalize_priority_object_keys(request.priority_object_keys)
        response_contract = _PlannedResponseContract(
            center_world_location=request.center_world_location,
            radius_tiles=32 if request.radius_tiles is None else request.radius_tiles,
            purpose=request.purpose,
            priority_object_ids=object_ids,
            priority_object_keys=object_keys,
        )
        payload = self._post_snapshot(
            build_snapshot_request(
                tiles,
                object_ids,
                object_keys,
                center_world_location=request.center_world_location,
                radius_tiles=request.radius_tiles,
                max_objects=request.max_objects,
                max_projection_objects=request.max_projection_objects,
                purpose=request.purpose,
            )
        )
        return parse_observation(
            payload,
            tiles,
            object_ids,
            object_keys,
            _planned_response_contract=response_contract,
        )

    def fetch_demonstration_evidence(
        self,
        tile_projections: Iterable[tuple[str, WorldPoint]] | None = None,
    ) -> DemonstrationEvidenceSnapshot:
        """Fetch additive read-only evidence for the manual demo recorder only."""
        tiles = _normalize_tiles(tile_projections)
        request_payload = build_demonstration_request(tiles)
        payload = self._post_snapshot(request_payload)
        canonical_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        canonical_request = json.dumps(
            request_payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return DemonstrationEvidenceSnapshot(
            observation=parse_observation(payload, tiles),
            payload_json=canonical_payload,
            request_json=canonical_request,
            fetched_at_utc=datetime.now(timezone.utc),
        )

    def disable_demonstration_capture(self) -> None:
        """Explicitly release the plugin's bounded camera-input capture lease."""
        self._post_snapshot(build_demonstration_capture_disable_request())

    def _post_snapshot(self, request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps(request_payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if self._auth_token:
            headers["X-Plugin-Snapshot-Token"] = self._auth_token
        request = Request(self._snapshot_url, data=body, headers=headers, method="POST")
        http_started = perf_counter()
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                content_length_value: Any = None
                getheader = getattr(response, "getheader", None)
                if callable(getheader):
                    content_length_value = getheader("Content-Length")
                elif getattr(response, "headers", None) is not None:
                    content_length_value = response.headers.get("Content-Length")
                if content_length_value is not None:
                    try:
                        content_length = int(content_length_value)
                    except (TypeError, ValueError) as exc:
                        raise ObservationTransportError(
                            "snapshot endpoint returned an invalid Content-Length"
                        ) from exc
                    if content_length < 0:
                        raise ObservationTransportError(
                            "snapshot endpoint returned an invalid Content-Length"
                        )
                    if content_length > MAX_SNAPSHOT_RESPONSE_BYTES:
                        raise ObservationTransportError(
                            "snapshot response exceeds the 4194304-byte limit"
                        )
                raw = response.read(MAX_SNAPSHOT_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            error_payload: Any = None
            if exc.code == 503:
                try:
                    error_raw = exc.read(MAX_SNAPSHOT_ERROR_RESPONSE_BYTES + 1)
                    if (
                        isinstance(error_raw, bytes)
                        and len(error_raw) <= MAX_SNAPSHOT_ERROR_RESPONSE_BYTES
                    ):
                        error_payload = json.loads(
                            error_raw.decode("utf-8"),
                            parse_constant=lambda constant: (
                                _ for _ in ()
                            ).throw(ValueError(constant)),
                        )
                except (
                    OSError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                    RecursionError,
                ):
                    error_payload = None
            if (
                isinstance(error_payload, Mapping)
                and error_payload.get("errorCode") == "endpoint_busy"
                and error_payload.get("retryable") is True
            ):
                raise ObservationBackpressureError(
                    status_code=exc.code,
                    error_code="endpoint_busy",
                ) from None
            raise ObservationTransportError(
                f"snapshot endpoint returned HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ObservationTransportError(f"snapshot endpoint unavailable: {exc}") from exc
        http_millis = (perf_counter() - http_started) * 1000.0
        if len(raw) > MAX_SNAPSHOT_RESPONSE_BYTES:
            raise ObservationTransportError(
                "snapshot response exceeds the 4194304-byte limit"
            )
        decode_started = perf_counter()
        try:
            decoded = raw.decode("utf-8")
            payload = json.loads(decoded, parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ObservationDecodeError("snapshot endpoint returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ObservationDecodeError("snapshot endpoint returned a non-object JSON value")
        decode_millis = (perf_counter() - decode_started) * 1000.0
        return _SnapshotPayload(
            payload,
            _TransportEvidence(
                response_bytes=len(raw),
                http_millis=http_millis,
                decode_millis=decode_millis,
            ),
        )
