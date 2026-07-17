from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType


MAX_FUTURE_CLOCK_SKEW_SECONDS = 2.0
BANK_INTERFACE_NAME = "bank"
DEPOSIT_INVENTORY_WIDGET_KEY = "deposit_inventory"
CLOSE_BANK_WIDGET_KEY = "close_bank"
CAMERA_YAW_UNITS = 16_384


@dataclass(frozen=True, slots=True)
class WorldPoint:
    x: int
    y: int
    plane: int

    def distance_to(self, other: "WorldPoint") -> int:
        if self.plane != other.plane:
            return 1_000_000
        return max(abs(self.x - other.x), abs(self.y - other.y))


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class ScreenBounds:
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> ScreenPoint:
        return ScreenPoint(self.x + self.width // 2, self.y + self.height // 2)

    def contains(self, point: ScreenPoint) -> bool:
        return (
            self.x <= point.x < self.x + self.width
            and self.y <= point.y < self.y + self.height
        )


@dataclass(frozen=True, slots=True)
class PlayerObservation:
    animation: int | None = None
    pose_animation: int | None = None
    interacting_type: str | None = None
    interacting_id: int | None = None
    run_energy_percent: float | None = None


@dataclass(frozen=True, slots=True)
class InventoryItem:
    slot: int
    item_id: int
    quantity: int
    name: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryObservation:
    items: tuple[InventoryItem, ...] = ()
    slot_count: int = 28
    occupied_slots: int = 0
    free_slots: int = 28
    known: bool = False

    def quantity(self, item_id: int) -> int:
        return sum(item.quantity for item in self.items if item.item_id == item_id)

    @property
    def full(self) -> bool:
        return self.known and self.free_slots == 0

    @property
    def item_ids(self) -> frozenset[int]:
        return frozenset(item.item_id for item in self.items)


@dataclass(frozen=True, slots=True)
class TargetGeometry:
    available: bool = False
    on_screen: bool = False
    visible: bool = False
    actionable: bool = False
    canvas_point: ScreenPoint | None = None
    screen_point: ScreenPoint | None = None
    screen_bounds: ScreenBounds | None = None
    geometry_source: str | None = None
    screen_polygon: tuple[ScreenPoint, ...] = ()
    visible_area_ratio: float | None = None
    edge_distance_px: float | None = None
    scene_supported: bool | None = None
    collision_supported: bool | None = None
    shortcut_clear: bool | None = None


@dataclass(frozen=True, slots=True)
class NearbyObject:
    key: str
    object_id: int
    name: str
    kind: str
    actions: tuple[str, ...]
    location: WorldPoint | None
    distance: int | None
    geometry: TargetGeometry
    scene_x: int | None = None
    scene_y: int | None = None

    def supports(self, option: str) -> bool:
        return option in self.actions


@dataclass(frozen=True, slots=True)
class SceneIndex:
    """Immutable exact-identity indexes over one Observation's scene rows."""

    _by_key: Mapping[str, NearbyObject] = field(repr=False)
    _by_object_id: Mapping[int, tuple[NearbyObject, ...]] = field(repr=False)

    @classmethod
    def build(cls, objects: Iterable[NearbyObject]) -> "SceneIndex":
        by_key: dict[str, NearbyObject] = {}
        by_object_id: dict[int, list[NearbyObject]] = {}
        for item in objects:
            if not isinstance(item, NearbyObject):
                raise TypeError("scene index values must be NearbyObject")
            if item.key in by_key:
                raise ValueError(f"nearby object keys must be unique: {item.key!r}")
            by_key[item.key] = item
            by_object_id.setdefault(item.object_id, []).append(item)
        frozen_ids = {
            object_id: tuple(sorted(items, key=lambda item: item.key))
            for object_id, items in by_object_id.items()
        }
        return cls(
            MappingProxyType(dict(by_key)),
            MappingProxyType(frozen_ids),
        )

    @property
    def by_key(self) -> Mapping[str, NearbyObject]:
        return self._by_key

    @property
    def by_object_id(self) -> Mapping[int, tuple[NearbyObject, ...]]:
        return self._by_object_id

    def object_by_key(self, key: str | None) -> NearbyObject | None:
        return self._by_key.get(key) if key else None

    def objects_by_id(self, object_id: int) -> tuple[NearbyObject, ...]:
        return self._by_object_id.get(object_id, ())


@dataclass(frozen=True, slots=True)
class SceneCensusEvidence:
    """Typed, fail-closed completeness and operation evidence for a scene census."""

    schema: str = "scene_census_evidence.v1"
    source_schema: str | None = None
    metadata_present: bool = False
    complete: bool | None = None
    authoritative_absence_eligible: bool | None = None
    priority_absence_eligible: bool | None = None
    scene_coverage_complete: bool | None = None
    count: int | None = None
    returned: int | None = None
    response_cap_hit: bool | None = None
    source_cap_hit: bool | None = None
    center_world_location: WorldPoint | None = None
    anchor_source: str | None = None
    radius_tiles: int | None = None
    requested_tile_count: int | None = None
    scanned_tile_slots: int | None = None
    scanned_tiles: int | None = None
    missing_tile_count: int | None = None
    discovered_object_count: int | None = None
    source_duplicate_object_count: int | None = None
    source_contradictory_duplicate_count: int | None = None
    indexed_object_count: int | None = None
    enriched_object_count: int | None = None
    projected_object_count: int | None = None
    requested_priority_object_ids: tuple[int, ...] = ()
    requested_priority_object_keys: tuple[str, ...] = ()
    reported_priority_object_ids: tuple[int, ...] = ()
    returned_priority_object_ids: tuple[int, ...] = ()
    priority_objects_complete: bool | None = None
    reported_priority_object_keys: tuple[str, ...] = ()
    returned_priority_object_keys: tuple[str, ...] = ()
    priority_keys_complete: bool | None = None
    duplicate_row_count: int = 0
    duplicate_group_count: int = 0
    conflicting_duplicate_keys: tuple[str, ...] = ()
    omitted_unnamed_count: int = 0
    parsed_object_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "metadata_present",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be bool")
        for name in (
            "complete",
            "authoritative_absence_eligible",
            "priority_absence_eligible",
            "scene_coverage_complete",
            "response_cap_hit",
            "source_cap_hit",
            "priority_objects_complete",
            "priority_keys_complete",
        ):
            if getattr(self, name) is not None and not isinstance(
                getattr(self, name), bool
            ):
                raise TypeError(f"{name} must be bool or None")
        for name in (
            "count",
            "returned",
            "radius_tiles",
            "requested_tile_count",
            "scanned_tile_slots",
            "scanned_tiles",
            "missing_tile_count",
            "discovered_object_count",
            "source_duplicate_object_count",
            "source_contradictory_duplicate_count",
            "indexed_object_count",
            "enriched_object_count",
            "projected_object_count",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        for name in (
            "duplicate_row_count",
            "duplicate_group_count",
            "omitted_unnamed_count",
            "parsed_object_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("source_schema", "anchor_source"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty or None")
        if self.center_world_location is not None and not isinstance(
            self.center_world_location, WorldPoint
        ):
            raise TypeError("center_world_location must be WorldPoint or None")
        for name in (
            "requested_priority_object_ids",
            "reported_priority_object_ids",
            "returned_priority_object_ids",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                for value in values
            ):
                raise TypeError(f"{name} must be a tuple of positive integers")
        if not isinstance(self.conflicting_duplicate_keys, tuple) or any(
            not isinstance(value, str) or not value
            for value in self.conflicting_duplicate_keys
        ):
            raise TypeError(
                "conflicting_duplicate_keys must be a tuple of non-empty strings"
            )
        for name in (
            "requested_priority_object_keys",
            "reported_priority_object_keys",
            "returned_priority_object_keys",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value for value in values
            ):
                raise TypeError(f"{name} must be a tuple of non-empty strings")


@dataclass(frozen=True, slots=True)
class ObservationPipelineEvidence:
    """Transport, cache, sizing, and parsing diagnostics without control authority."""

    schema: str = "observation_pipeline_evidence.v1"
    source_schema: str | None = None
    request_id: str | None = None
    query_sequence: int | None = None
    query_purpose: str | None = None
    source_tick: int | None = None
    client_tick: int | None = None
    session_id: str | None = None
    process_id: int | None = None
    geometry_frame_id: str | None = None
    raw_cache_key: str | None = None
    response_bytes: int | None = None
    http_millis: float | None = None
    decode_millis: float | None = None
    parse_millis: float | None = None
    index_millis: float | None = None
    service_timing_millis: float | None = None
    cache_hit: bool | None = None
    cache_miss: bool | None = None
    cache_entries: int | None = None
    cache_hits: int | None = None
    cache_misses: int | None = None
    refresh_sequence: int | None = None
    refresh_reason: str | None = None
    refresh_duration_millis: float | None = None
    query_duration_millis: float | None = None
    world_model_age_millis: int | None = None
    max_response_bytes: int | None = None
    requested_projection_refs: int | None = None
    effective_projection_refs: int | None = None
    projection_refs_before_cap: int | None = None
    projection_refs_after_cap: int | None = None
    trimmed_projection_refs: int | None = None
    projection_refs_capped: bool | None = None
    serialization_passes: int | None = None
    serialized_bytes_reused_for_write: bool | None = None
    operation_counts: tuple[tuple[str, int], ...] = ()
    query_diagnostics_schema: str | None = None
    query_lane: str | None = None
    query_status: str | None = None
    request_coalesced: bool | None = None
    work_executed: bool | None = None
    timeout_millis: float | None = None
    queue_wait_millis: float | None = None
    execution_millis: float | None = None
    active_request_count: int | None = None
    pending_request_count: int | None = None
    max_queue_depth: int | None = None
    submitted_request_count: int | None = None
    executed_request_count: int | None = None
    coalesced_request_count: int | None = None
    superseded_request_count: int | None = None
    timed_out_request_count: int | None = None
    expired_before_execution_count: int | None = None
    late_result_count: int | None = None
    failed_request_count: int | None = None
    last_queue_wait_millis: float | None = None
    max_queue_wait_millis: float | None = None
    last_execution_millis: float | None = None
    max_execution_millis: float | None = None
    endpoint_queue_schema: str | None = None
    endpoint_worker_limit: int | None = None
    endpoint_pending_capacity: int | None = None
    endpoint_active_worker_count: int | None = None
    endpoint_pending_request_count: int | None = None
    endpoint_pending_remaining_capacity: int | None = None
    endpoint_largest_worker_count: int | None = None
    endpoint_completed_request_count: int | None = None
    endpoint_execution_rejection_count: int | None = None
    endpoint_rejection_policy: str | None = None
    endpoint_snapshot_request_active: bool | None = None
    endpoint_busy_rejection_count: int | None = None
    endpoint_executor_state: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "response_bytes",
            "query_sequence",
            "source_tick",
            "client_tick",
            "cache_entries",
            "cache_hits",
            "cache_misses",
            "refresh_sequence",
            "world_model_age_millis",
            "max_response_bytes",
            "requested_projection_refs",
            "effective_projection_refs",
            "projection_refs_before_cap",
            "projection_refs_after_cap",
            "trimmed_projection_refs",
            "serialization_passes",
            "active_request_count",
            "pending_request_count",
            "max_queue_depth",
            "submitted_request_count",
            "executed_request_count",
            "coalesced_request_count",
            "superseded_request_count",
            "timed_out_request_count",
            "expired_before_execution_count",
            "late_result_count",
            "failed_request_count",
            "endpoint_worker_limit",
            "endpoint_pending_capacity",
            "endpoint_active_worker_count",
            "endpoint_pending_request_count",
            "endpoint_pending_remaining_capacity",
            "endpoint_largest_worker_count",
            "endpoint_completed_request_count",
            "endpoint_execution_rejection_count",
            "endpoint_busy_rejection_count",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        for name in (
            "http_millis",
            "decode_millis",
            "parse_millis",
            "index_millis",
            "service_timing_millis",
            "refresh_duration_millis",
            "query_duration_millis",
            "timeout_millis",
            "queue_wait_millis",
            "execution_millis",
            "last_queue_wait_millis",
            "max_queue_wait_millis",
            "last_execution_millis",
            "max_execution_millis",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative number or None")
        for name in (
            "cache_hit",
            "cache_miss",
            "projection_refs_capped",
            "request_coalesced",
            "work_executed",
            "serialized_bytes_reused_for_write",
            "endpoint_snapshot_request_active",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{name} must be bool or None")
        for name in (
            "source_schema",
            "request_id",
            "query_purpose",
            "session_id",
            "geometry_frame_id",
            "raw_cache_key",
            "refresh_reason",
            "query_diagnostics_schema",
            "query_lane",
            "query_status",
            "endpoint_queue_schema",
            "endpoint_rejection_policy",
            "endpoint_executor_state",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-empty or None")
        if self.process_id is not None and (
            not isinstance(self.process_id, int)
            or isinstance(self.process_id, bool)
            or self.process_id <= 0
        ):
            raise ValueError("process_id must be a positive integer or None")
        if not isinstance(self.operation_counts, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not item[0]
            or not isinstance(item[1], int)
            or isinstance(item[1], bool)
            or item[1] < 0
            for item in self.operation_counts
        ):
            raise TypeError(
                "operation_counts must be a tuple of non-negative integer pairs"
            )


@dataclass(frozen=True, slots=True)
class MenuEntry:
    option: str
    target: str
    entry_type: str
    identifier: int
    param0: int | None = None
    param1: int | None = None
    row_bounds: ScreenBounds | None = None


@dataclass(frozen=True, slots=True)
class WidgetTarget:
    name: str
    visible: bool
    screen_point: ScreenPoint | None = None
    screen_bounds: ScreenBounds | None = None


@dataclass(frozen=True, slots=True)
class DialogueOption:
    index: int
    key: str | None
    text: str
    visible: bool = True


@dataclass(frozen=True, slots=True)
class WidgetObservation:
    bank_known: bool = False
    bank_open: bool = False
    bank_pin_open: bool = False
    bank_readable: bool = False
    keyboard_close_possible: bool = False
    deposit_inventory: WidgetTarget | None = None
    close_bank: WidgetTarget | None = None
    dialogue_active: bool = False
    dialogue_type: str = "none"
    dialogue_prompt: str = ""
    dialogue_options: tuple[DialogueOption, ...] = ()
    dialogue_number_keys: bool = False
    dialogue_client_tick: int | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    player: PlayerObservation
    location: WorldPoint | None
    plane: int | None
    inventory: InventoryObservation
    nearby_objects: tuple[NearbyObject, ...]
    menus: tuple[MenuEntry, ...]
    widgets: WidgetObservation
    canvas_bounds: ScreenBounds | None
    game_state: str
    timestamp: datetime
    tick: int
    status: str
    fresh: bool
    cache_wall_clock_fresh: bool
    viewport_bounds: ScreenBounds | None = None
    player_screen_point: ScreenPoint | None = None
    client_window_bounds: ScreenBounds | None = None
    scene_playable: bool = False
    session_id: str | None = None
    warnings: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    menu_client_tick: int | None = None
    menu_mouse_screen_point: ScreenPoint | None = None
    menu_open: bool = False
    menu_bounds: ScreenBounds | None = None
    client_focused: bool = False
    client_process_id: int | None = None
    assembled_at: datetime | None = None
    frame_id: str | None = None
    geometry_frame_id: str | None = None
    source_coherent: bool = False
    menu_fresh: bool = False
    menu_source_tick: int | None = None
    menu_timestamp: datetime | None = None
    menu_session_id: str | None = None
    menu_process_id: int | None = None
    camera_yaw: int | None = None
    camera_pitch: int | None = None
    camera_zoom: int | None = None
    text_input_active: bool | None = None
    max_source_age_millis: int = 2_000
    scene_census: SceneCensusEvidence = SceneCensusEvidence()
    pipeline: ObservationPipelineEvidence = ObservationPipelineEvidence()
    _prebuilt_scene_index: InitVar[SceneIndex | None] = None
    _scene_index: SceneIndex = field(init=False, repr=False, compare=False)

    def __post_init__(self, _prebuilt_scene_index: SceneIndex | None) -> None:
        if not isinstance(self.nearby_objects, tuple) or not all(
            isinstance(item, NearbyObject) for item in self.nearby_objects
        ):
            raise TypeError("nearby_objects must be a tuple of NearbyObject values")
        if not isinstance(self.scene_census, SceneCensusEvidence):
            raise TypeError("scene_census must be SceneCensusEvidence")
        if not isinstance(self.pipeline, ObservationPipelineEvidence):
            raise TypeError("pipeline must be ObservationPipelineEvidence")
        if _prebuilt_scene_index is None:
            index = SceneIndex.build(self.nearby_objects)
        elif not isinstance(_prebuilt_scene_index, SceneIndex):
            raise TypeError("_prebuilt_scene_index must be SceneIndex or None")
        else:
            index = _prebuilt_scene_index
            if tuple(index.by_key.values()) != self.nearby_objects:
                raise ValueError("prebuilt scene index does not match nearby_objects")
        object.__setattr__(self, "_scene_index", index)

    @property
    def loaded_scene(self) -> bool:
        return (
            self.status == "PASS"
            and self.game_state == "LOGGED_IN"
            and self.location is not None
            and self.tick >= 0
            and self.fresh
            and self.cache_wall_clock_fresh
            and self.source_coherent
            and self.scene_playable
            and self.timestamp_not_future
            and not self.missing_capabilities
        )

    @property
    def age_seconds(self) -> float:
        """Signed wall-clock age; negative values are future-dated samples."""
        now = datetime.now(timezone.utc)
        stamp = self.timestamp
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (now - stamp).total_seconds()

    @property
    def timestamp_not_future(self) -> bool:
        try:
            return self.age_seconds >= -MAX_FUTURE_CLOCK_SKEW_SECONDS
        except (AttributeError, TypeError, ValueError, OverflowError):
            return False

    @property
    def menu_age_seconds(self) -> float | None:
        if self.menu_timestamp is None:
            return None
        stamp = self.menu_timestamp
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stamp).total_seconds()

    def object_by_key(self, key: str | None) -> NearbyObject | None:
        return self._scene_index.object_by_key(key)

    def objects_by_id(self, object_id: int) -> tuple[NearbyObject, ...]:
        return self._scene_index.objects_by_id(object_id)

    @property
    def scene_index(self) -> SceneIndex:
        return self._scene_index


class ActionKind(str, Enum):
    WAIT = "wait"
    INTERACT_OBJECT = "interact_object"
    WALK = "walk"
    CLICK_WIDGET = "click_widget"
    PRESS_KEY = "press_key"
    CAMERA_HOLD = "camera_hold"
    CAMERA_ZOOM = "camera_zoom"


class VerificationKind(str, Enum):
    ITEM_QUANTITY_INCREASED = "item_quantity_increased"
    ITEM_QUANTITY_EQUALS = "item_quantity_equals"
    MOVED_CLOSER = "moved_closer"
    PLANE_CHANGED = "plane_changed"
    INTERFACE_OPENED = "interface_opened"
    INTERFACE_CLOSED = "interface_closed"
    ROUTE_TRANSITION = "route_transition"
    CAMERA_POSE_CHANGED = "camera_pose_changed"
    CAMERA_ZOOM_CHANGED = "camera_zoom_changed"


@dataclass(frozen=True, slots=True)
class VerificationSpec:
    kind: VerificationKind
    before_tick: int
    deadline_tick: int
    item_id: int | None = None
    before_quantity: int | None = None
    expected_quantity: int | None = None
    before_location: WorldPoint | None = None
    target_location: WorldPoint | None = None
    expected_plane: int | None = None
    source_session_id: str | None = None
    target_radius: int | None = None
    interface_name: str | None = None
    dialogue_prompt_contains: str | None = None
    dialogue_option_contains: str | None = None
    before_camera_yaw: int | None = None
    before_camera_pitch: int | None = None
    before_geometry_frame_id: str | None = None
    camera_key: str | None = None
    before_camera_zoom: int | None = None
    camera_zoom_amount: int | None = None
    before_process_id: int | None = None
    before_bank_known: bool | None = None
    before_bank_open: bool | None = None
    before_bank_pin_open: bool | None = None
    before_bank_readable: bool | None = None
    before_dialogue_active: bool | None = None
    before_dialogue_type: str | None = None
    before_text_input_active: bool | None = None


@dataclass(frozen=True, slots=True)
class InventoryConstraint:
    allowed_item_ids: frozenset[int]
    require_nonempty: bool = True

    def __post_init__(self) -> None:
        if not self.allowed_item_ids or any(
            not isinstance(item_id, int)
            or isinstance(item_id, bool)
            or item_id <= 0
            for item_id in self.allowed_item_ids
        ):
            raise ValueError("allowed_item_ids must contain positive item IDs")
        if not isinstance(self.require_nonempty, bool):
            raise ValueError("require_nonempty must be a bool")


@dataclass(frozen=True, slots=True)
class InterfaceConstraint:
    interface_name: str
    expected_plane: int
    expected_open: bool
    require_readable: bool = False
    require_keyboard_close: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.interface_name, str) or not self.interface_name.strip():
            raise ValueError("interface_name must be a non-empty string")
        if (
            not isinstance(self.expected_plane, int)
            or isinstance(self.expected_plane, bool)
            or self.expected_plane < 0
        ):
            raise ValueError("expected_plane must be a non-negative integer")
        if not isinstance(self.expected_open, bool):
            raise ValueError("expected_open must be a bool")
        if not isinstance(self.require_readable, bool):
            raise ValueError("require_readable must be a bool")
        if not isinstance(self.require_keyboard_close, bool):
            raise ValueError("require_keyboard_close must be a bool")
        if self.require_readable and not self.expected_open:
            raise ValueError("a readable interface must be expected open")
        if self.require_keyboard_close and not self.expected_open:
            raise ValueError("keyboard close support requires an open interface")


@dataclass(frozen=True, slots=True)
class DialogueOptionConstraint:
    prompt_contains: str
    option_text: str
    option_index: int
    option_key: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("prompt_contains", self.prompt_contains),
            ("option_text", self.option_text),
            ("option_key", self.option_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if (
            not isinstance(self.option_index, int)
            or isinstance(self.option_index, bool)
            or self.option_index <= 0
        ):
            raise ValueError("option_index must be a positive integer")


@dataclass(frozen=True, slots=True)
class CameraConstraint:
    target_key: str
    target_location: WorldPoint
    source_location: WorldPoint
    source_geometry_frame_id: str
    before_yaw: int
    direction: str
    hold_millis: int
    before_pitch: int | None = None
    desired_region: ScreenBounds | None = None
    framing_classification: str = "not_visible"
    target_id: int = 0
    target_name: str | None = None
    target_kind: str = "NAVIGATION_TILE"
    target_action: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("target_key", "source_geometry_frame_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.target_location, WorldPoint):
            raise TypeError("target_location must be WorldPoint")
        if not isinstance(self.source_location, WorldPoint):
            raise TypeError("source_location must be WorldPoint")
        if (
            not isinstance(self.before_yaw, int)
            or isinstance(self.before_yaw, bool)
            or not 0 <= self.before_yaw < CAMERA_YAW_UNITS
        ):
            raise ValueError("before_yaw must be a valid fixed-point camera yaw")
        if self.direction not in {"left", "right", "up", "down"}:
            raise ValueError("direction must be left, right, up, or down")
        if (
            not isinstance(self.hold_millis, int)
            or isinstance(self.hold_millis, bool)
            or not 1 <= self.hold_millis <= 600
        ):
            raise ValueError("hold_millis must be between 1 and 600")
        if self.before_pitch is not None and (
            not isinstance(self.before_pitch, int)
            or isinstance(self.before_pitch, bool)
            or self.before_pitch < 0
        ):
            raise ValueError("before_pitch must be non-negative or None")
        if self.desired_region is not None and not isinstance(
            self.desired_region, ScreenBounds
        ):
            raise TypeError("desired_region must be ScreenBounds or None")
        if (
            not isinstance(self.framing_classification, str)
            or not self.framing_classification.strip()
        ):
            raise ValueError("framing_classification must be non-empty")
        if (
            not isinstance(self.target_id, int)
            or isinstance(self.target_id, bool)
            or self.target_id < 0
        ):
            raise ValueError("target_id must be non-negative")
        for field_name in ("target_name", "target_action"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{field_name} must be non-empty or None")
        if not isinstance(self.target_kind, str) or not self.target_kind.strip():
            raise ValueError("target_kind must be non-empty")


@dataclass(frozen=True, slots=True)
class CameraZoomConstraint:
    """Semantic bounded zoom request tied to one locked camera target."""

    target_key: str
    target_location: WorldPoint
    source_location: WorldPoint
    source_geometry_frame_id: str
    before_yaw: int
    before_pitch: int | None
    before_zoom: int
    amount: int
    desired_zoom_min: int
    desired_zoom_max: int
    target_id: int = 0
    target_name: str | None = None
    target_kind: str = "NAVIGATION_TILE"
    target_action: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("target_key", "source_geometry_frame_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.target_location, WorldPoint):
            raise TypeError("target_location must be WorldPoint")
        if not isinstance(self.source_location, WorldPoint):
            raise TypeError("source_location must be WorldPoint")
        if (
            not isinstance(self.before_yaw, int)
            or isinstance(self.before_yaw, bool)
            or not 0 <= self.before_yaw < CAMERA_YAW_UNITS
        ):
            raise ValueError("before_yaw must be a valid fixed-point camera yaw")
        if self.before_pitch is not None and (
            not isinstance(self.before_pitch, int)
            or isinstance(self.before_pitch, bool)
            or self.before_pitch < 0
        ):
            raise ValueError("before_pitch must be non-negative or None")
        if (
            not isinstance(self.before_zoom, int)
            or isinstance(self.before_zoom, bool)
            or self.before_zoom < 0
        ):
            raise ValueError("before_zoom must be non-negative")
        if (
            not isinstance(self.amount, int)
            or isinstance(self.amount, bool)
            or self.amount == 0
            or abs(self.amount) > 3
        ):
            raise ValueError("amount must be a nonzero signed value within 3")
        for field_name in ("desired_zoom_min", "desired_zoom_max"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
            ):
                raise ValueError(f"{field_name} must be a positive integer")
        if self.desired_zoom_min > self.desired_zoom_max:
            raise ValueError("desired zoom minimum cannot exceed maximum")
        if (
            not isinstance(self.target_id, int)
            or isinstance(self.target_id, bool)
            or self.target_id < 0
        ):
            raise ValueError("target_id must be non-negative")
        for field_name in ("target_name", "target_action"):
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{field_name} must be non-empty or None")
        if not isinstance(self.target_kind, str) or not self.target_kind.strip():
            raise ValueError("target_kind must be non-empty")


@dataclass(frozen=True, slots=True)
class TaskConstraints:
    inventory: InventoryConstraint | None = None
    interface: InterfaceConstraint | None = None
    dialogue: DialogueOptionConstraint | None = None
    camera: CameraConstraint | None = None
    camera_zoom: CameraZoomConstraint | None = None

    def __post_init__(self) -> None:
        exclusive = sum(
            value is not None
            for value in (
                self.interface,
                self.dialogue,
                self.camera,
                self.camera_zoom,
            )
        )
        if exclusive > 1:
            raise ValueError(
                "an action cannot combine interface, dialogue, and camera constraints"
            )
        if (
            self.camera is not None or self.camera_zoom is not None
        ) and self.inventory is not None:
            raise ValueError("a camera action cannot carry an inventory constraint")


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    label: str
    source_tick: int
    option: str | None = None
    target_key: str | None = None
    target_name: str | None = None
    target_id: int | None = None
    screen_point: ScreenPoint | None = None
    key: str | None = None
    verification: VerificationSpec | None = None
    source_menu_client_tick: int | None = None
    target_param0: int | None = None
    target_param1: int | None = None
    source_session_id: str | None = None
    source_dialogue_client_tick: int | None = None
    task_constraints: TaskConstraints = field(default_factory=TaskConstraints)
    key_hold_millis: int = 50
    decision_id: str | None = None
    behavior_seed: int | None = None
    pre_move_delay_seconds: float = 0.0
    settle_delay_seconds: float = 0.0
    pre_click_delay_seconds: float = 0.0
    post_action_delay_seconds: float = 0.0
