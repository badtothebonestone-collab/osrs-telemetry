from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


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
    visible_area_ratio: float | None = None


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
    resource_candidate: bool = False
    route_candidate: bool = False
    service_candidate: bool = False

    def supports(self, option: str) -> bool:
        return option in self.actions


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
        if not key:
            return None
        return next((item for item in self.nearby_objects if item.key == key), None)


class ActionKind(str, Enum):
    WAIT = "wait"
    INTERACT_OBJECT = "interact_object"
    WALK = "walk"
    CLICK_WIDGET = "click_widget"
    PRESS_KEY = "press_key"


class VerificationKind(str, Enum):
    ITEM_QUANTITY_INCREASED = "item_quantity_increased"
    ITEM_QUANTITY_EQUALS = "item_quantity_equals"
    MOVED_CLOSER = "moved_closer"
    PLANE_CHANGED = "plane_changed"
    INTERFACE_OPENED = "interface_opened"
    INTERFACE_CLOSED = "interface_closed"
    ROUTE_TRANSITION = "route_transition"
    CAMERA_POSE_CHANGED = "camera_pose_changed"


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
    before_geometry_frame_id: str | None = None
    camera_key: str | None = None


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
        if self.direction not in {"left", "right"}:
            raise ValueError("direction must be left or right")
        if (
            not isinstance(self.hold_millis, int)
            or isinstance(self.hold_millis, bool)
            or not 1 <= self.hold_millis <= 250
        ):
            raise ValueError("hold_millis must be between 1 and 250")


@dataclass(frozen=True, slots=True)
class TaskConstraints:
    inventory: InventoryConstraint | None = None
    interface: InterfaceConstraint | None = None
    dialogue: DialogueOptionConstraint | None = None
    camera: CameraConstraint | None = None

    def __post_init__(self) -> None:
        exclusive = sum(
            value is not None
            for value in (self.interface, self.dialogue, self.camera)
        )
        if exclusive > 1:
            raise ValueError(
                "an action cannot combine interface, dialogue, and camera constraints"
            )
        if self.camera is not None and self.inventory is not None:
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
