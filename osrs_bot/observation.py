from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from math import ceil, floor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from .model import CAMERA_YAW_UNITS, DialogueOption, InventoryItem, InventoryObservation, MenuEntry, NearbyObject, Observation
from .model import PlayerObservation, ScreenBounds, ScreenPoint, TargetGeometry, WidgetObservation, WidgetTarget, WorldPoint

RESPONSE_SCHEMA = "plugin_snapshot_response.v2"
SENSOR_FRAME_SCHEMA = "sensor_frame.v1"
MAX_TILE_PROJECTIONS = 16
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
class ObservationDecodeError(ObservationError): pass
class ObservationSchemaError(ObservationError): pass


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

def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ObservationSchemaError(f"{path} must be an array of strings")
    return tuple(value)

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

def build_snapshot_request(tile_projections: Iterable[tuple[str, WorldPoint]] | None = None) -> dict[str, Any]:
    tiles = _normalize_tiles(tile_projections)
    request: dict[str, Any] = {
        "schema": "plugin_snapshot_request.v1", "needs": list(CANONICAL_NEEDS),
        "snapshotTier": "hot", "responseMode": "compact", "maxAgeTicks": 0,
        "maxSourceAgeMillis": 2000,
        "includeGeometry": True, "includeCollisionWindow": False, "includeWatchValues": False,
        "includeMenuEntries": True, "menuEntryLimit": 16, "maxClientTickSamples": 0,
        "maxMenuSamples": 0, "maxClickedSamples": 0,
        "worldModel": {"radiusTiles": 32, "maxObjects": 64, "includeProjection": True, "includeCollision": False}}
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

def _world_point(raw: Mapping[str, Any], path: str) -> WorldPoint | None:
    values = (raw.get("worldX"), raw.get("worldY"), raw.get("plane"))
    if all(value is None for value in values):
        return None
    x, y, plane = (_integer(value, path) for value in values)
    return None if x < 0 or y < 0 or plane < 0 else WorldPoint(x, y, plane)

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
    available = _boolean(projection.get("geometryAvailable"), "object.geometryAvailable")
    on_screen = _boolean(projection.get("onScreen"), "object.onScreen")
    visible = _boolean(projection.get("visible"), "object.visible")
    raw_actionable = _boolean(projection.get("actionableByCanvas", projection.get("actionable")),
                              "object.actionableByCanvas")
    ratio = _number(projection.get("visibleAreaRatio"), "object.visibleAreaRatio", optional=True)
    return TargetGeometry(available=available, on_screen=on_screen, visible=visible,
                          actionable=raw_actionable and screen_point is not None,
                          canvas_point=canvas_point, screen_point=screen_point,
                          screen_bounds=transform.bounds(bounds_raw) if transform else None,
                          visible_area_ratio=ratio)

def _inventory(payloads: Mapping[str, Any]) -> InventoryObservation:
    outer = _payload(payloads, "inventory")
    raw = _mapping(outer.get("inventory"), "payloads.inventory.inventory", optional=True)
    if not raw:
        return InventoryObservation()
    slot_count = _integer(raw.get("slotCount", 28), "inventory.slotCount")
    items_value = raw.get("items", [])
    if not isinstance(items_value, list):
        raise ObservationSchemaError("inventory.items must be an array")
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

def _menu_state(
    payloads: Mapping[str, Any], transform: _CanvasTransform | None
) -> tuple[
    tuple[MenuEntry, ...], int | None, ScreenPoint | None, bool,
    ScreenBounds | None,
]:
    interaction = _payload(payloads, "interaction_hot")
    menu = _mapping(interaction.get("postMenuSort", interaction.get("hoverMenu")), "interaction_hot.postMenuSort", optional=True)
    values = menu.get("entries", []) if menu else []
    if not isinstance(values, list):
        raise ObservationSchemaError("interaction_hot menu entries must be an array")
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

def _nearby_objects(payloads: Mapping[str, Any], transform: _CanvasTransform | None,
                    player_location: WorldPoint | None, requested_tiles: Mapping[str, WorldPoint]) -> tuple[NearbyObject, ...]:
    objects: dict[str, NearbyObject] = {}
    for census_name in (
        "scene_object_census",
        "resource_object_census",
        "route_object_census",
        "service_object_census",
    ):
        census = _payload(payloads, census_name)
        values = census.get("objects", []) if census else []
        if not isinstance(values, list):
            raise ObservationSchemaError(f"payloads.{census_name}.objects must be an array")
        for index, value in enumerate(values):
            raw = _mapping(value, f"{census_name}.objects[{index}]")
            key, name, kind = raw.get("objectKey"), raw.get("name", raw.get("objectName")), raw.get("kind")
            if not all(isinstance(field, str) and field for field in (key, kind)):
                raise ObservationSchemaError(f"{census_name}.objects[{index}] lacks object identity")
            if name is None or (isinstance(name, str) and not name.strip()):
                # RuneLite can expose valid object ids/actions before resolving
                # a definition name. Such an object cannot be matched safely,
                # so omit it without invalidating the rest of the census.
                continue
            if not isinstance(name, str):
                raise ObservationSchemaError(f"{census_name}.objects[{index}] has an invalid name")
            actions = _string_tuple(raw.get("actions"), f"{census_name}.objects[{index}].actions")
            location = _world_point(raw, f"{census_name}.objects[{index}].location")
            distance = _integer(raw.get("distanceToPlayer"), f"{census_name}.objects[{index}].distanceToPlayer", optional=True)
            if distance is None and location and player_location:
                distance = player_location.distance_to(location)
            candidate = NearbyObject(key=key, object_id=_integer(raw.get("id"), f"{census_name}.objects[{index}].id"),
                                     name=name, kind=kind, actions=actions, location=location, distance=distance,
                                     geometry=_target_geometry(raw, transform),
                                     scene_x=_integer(raw.get("sceneX"), "object.sceneX", optional=True),
                                     scene_y=_integer(raw.get("sceneY"), "object.sceneY", optional=True),
                                     resource_candidate=_boolean(raw.get("resourceCandidate"), "object.resourceCandidate"),
                                     route_candidate=_boolean(raw.get("routeObjectCandidate"), "object.routeObjectCandidate"),
                                     service_candidate=_boolean(raw.get("serviceObjectCandidate"), "object.serviceObjectCandidate"))
            objects[key] = _merge_object(objects.get(key), candidate)

    tile_payload = _payload(payloads, "tile_projection")
    tile_values = tile_payload.get("tiles", []) if tile_payload else []
    if not isinstance(tile_values, list):
        raise ObservationSchemaError("payloads.tile_projection.tiles must be an array")
    for index, value in enumerate(tile_values):
        raw = _mapping(value, f"tile_projection.tiles[{index}]")
        if raw.get("status") not in {"PASS", "WARN"}:
            continue
        label = raw.get("label")
        if not isinstance(label, str) or not label:
            raise ObservationSchemaError(f"tile_projection.tiles[{index}].label must be a string")
        requested = requested_tiles.get(label)
        if requested is None:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}] was not requested"
            )
        location = _world_point(raw, f"tile_projection.tiles[{index}].location") or requested
        if location is None:
            raise ObservationSchemaError(f"tile_projection.tiles[{index}] lacks a world location")
        if location != requested:
            raise ObservationSchemaError(
                f"tile_projection.tiles[{index}] disagrees with the requested world location"
            )
        geometry = _target_geometry(raw, transform)
        objects[label] = NearbyObject(key=label, object_id=0, name=label, kind="NAVIGATION_TILE",
                                      actions=("Walk here",), location=location,
                                      distance=player_location.distance_to(location) if player_location else None,
                                      geometry=geometry,
                                      scene_x=_integer(raw.get("sceneX"), "tile.sceneX", optional=True),
                                      scene_y=_integer(raw.get("sceneY"), "tile.sceneY", optional=True),
                                      route_candidate=True)
    return tuple(objects.values())

def _merge_object(current: NearbyObject | None, new: NearbyObject) -> NearbyObject:
    if current is None:
        return new
    geometry = new.geometry if (new.geometry.actionable, new.geometry.available) > (current.geometry.actionable, current.geometry.available) else current.geometry
    distances = [value for value in (current.distance, new.distance) if value is not None]
    return NearbyObject(key=current.key, object_id=current.object_id, name=current.name, kind=current.kind,
                        actions=tuple(dict.fromkeys((*current.actions, *new.actions))),
                        location=current.location or new.location, distance=min(distances) if distances else None,
                        geometry=geometry, scene_x=current.scene_x if current.scene_x is not None else new.scene_x,
                        scene_y=current.scene_y if current.scene_y is not None else new.scene_y,
                        resource_candidate=current.resource_candidate or new.resource_candidate,
                        route_candidate=current.route_candidate or new.route_candidate,
                        service_candidate=current.service_candidate or new.service_candidate)

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
    option_values = dialogue.get("options", []) if dialogue else []
    if not isinstance(option_values, list):
        raise ObservationSchemaError("dialogue_state.options must be an array")
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
        "resource_object_census",
        "route_object_census",
        "service_object_census",
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

def parse_observation(value: Mapping[str, Any], tile_projections: Iterable[tuple[str, WorldPoint]] | None = None) -> Observation:
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
    if camera_yaw is not None and not 0 <= camera_yaw < CAMERA_YAW_UNITS:
        raise ObservationSchemaError("cameraYaw is outside the fixed-point yaw range")
    if camera_pitch is not None and camera_pitch < 0:
        raise ObservationSchemaError("cameraPitch must be non-negative")
    base_player = _mapping(baseline.get("player"), "payloads.baseline.player", optional=True)
    location = _world_point(base_player, "payloads.baseline.player location")
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
    return Observation(player=player, location=location, plane=location.plane if location else None,
                       inventory=_inventory(payloads), nearby_objects=_nearby_objects(payloads, transform, location, dict(tiles)),
                       menus=menus, widgets=_widgets(payloads, transform),
                       canvas_bounds=transform.canvas_bounds if transform else None, game_state=game_state,
                       timestamp=frame.captured_at, tick=frame.source_tick, status=status,
                       fresh=_boolean(freshness.get("fresh"), "freshness.fresh"),
                       cache_wall_clock_fresh=_boolean(freshness.get("sourceCaptureFresh"), "freshness.sourceCaptureFresh"),
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
                       camera_yaw=camera_yaw, camera_pitch=camera_pitch)

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

    def fetch(self, tile_projections: Iterable[tuple[str, WorldPoint]] | None = None) -> Observation:
        tiles = _normalize_tiles(tile_projections)
        payload = self._post_snapshot(build_snapshot_request(tiles))
        return parse_observation(payload, tiles)

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

    def _post_snapshot(self, request_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps(request_payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if self._auth_token:
            headers["X-Plugin-Snapshot-Token"] = self._auth_token
        request = Request(self._snapshot_url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raise ObservationTransportError(f"snapshot endpoint returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ObservationTransportError(f"snapshot endpoint unavailable: {exc}") from exc
        try:
            decoded = raw.decode("utf-8")
            payload = json.loads(decoded, parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ObservationDecodeError("snapshot endpoint returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ObservationDecodeError("snapshot endpoint returned a non-object JSON value")
        return payload
