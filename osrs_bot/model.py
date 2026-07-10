from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


LOG_ITEM_ID = 1511
MAX_FUTURE_CLOCK_SKEW_SECONDS = 2.0


@dataclass(frozen=True)
class WorldPoint:
    x: int
    y: int
    plane: int

    def distance_to(self, other: "WorldPoint") -> int:
        if self.plane != other.plane:
            return 1_000_000
        return max(abs(self.x - other.x), abs(self.y - other.y))


@dataclass(frozen=True)
class ScreenPoint:
    x: int
    y: int


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class PlayerObservation:
    animation: int | None = None
    pose_animation: int | None = None
    interacting_type: str | None = None
    interacting_id: int | None = None
    run_energy_percent: float | None = None


@dataclass(frozen=True)
class InventoryItem:
    slot: int
    item_id: int
    quantity: int
    name: str | None = None


@dataclass(frozen=True)
class InventoryObservation:
    items: tuple[InventoryItem, ...] = ()
    slot_count: int = 28
    occupied_slots: int = 0
    free_slots: int = 28
    known: bool = False

    def quantity(self, item_id: int) -> int:
        return sum(item.quantity for item in self.items if item.item_id == item_id)

    @property
    def log_count(self) -> int:
        return self.quantity(LOG_ITEM_ID)

    @property
    def full(self) -> bool:
        return self.known and self.free_slots == 0

    @property
    def item_ids(self) -> frozenset[int]:
        return frozenset(item.item_id for item in self.items)


@dataclass(frozen=True)
class TargetGeometry:
    available: bool = False
    on_screen: bool = False
    visible: bool = False
    actionable: bool = False
    canvas_point: ScreenPoint | None = None
    screen_point: ScreenPoint | None = None
    screen_bounds: ScreenBounds | None = None
    visible_area_ratio: float | None = None


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class MenuEntry:
    option: str
    target: str
    entry_type: str
    identifier: int
    param0: int | None = None
    param1: int | None = None
    row_bounds: ScreenBounds | None = None


@dataclass(frozen=True)
class WidgetTarget:
    name: str
    visible: bool
    screen_point: ScreenPoint | None = None
    screen_bounds: ScreenBounds | None = None


@dataclass(frozen=True)
class DialogueOption:
    index: int
    key: str | None
    text: str
    visible: bool = True


@dataclass(frozen=True)
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


@dataclass(frozen=True)
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

    @property
    def loaded_scene(self) -> bool:
        return (
            self.status == "PASS"
            and self.game_state == "LOGGED_IN"
            and self.location is not None
            and self.tick >= 0
            and self.fresh
            and self.cache_wall_clock_fresh
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

    def object_by_key(self, key: str | None) -> NearbyObject | None:
        if not key:
            return None
        return next((item for item in self.nearby_objects if item.key == key), None)


class TaskPhase(str, Enum):
    FIND_TREE = "find_tree"
    CHOP = "chop"
    VERIFY_LOGS = "verify_logs"
    NAVIGATE_TO_BANK = "navigate_to_bank"
    OPEN_BANK = "open_bank"
    DEPOSIT_LOGS = "deposit_logs"
    VERIFY_DEPOSIT = "verify_deposit"
    CLOSE_BANK = "close_bank"
    NAVIGATE_TO_TREES = "navigate_to_trees"
    STAIR_DIALOGUE = "stair_dialogue"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class ActionKind(str, Enum):
    WAIT = "wait"
    INTERACT_OBJECT = "interact_object"
    WALK = "walk"
    CLICK_WIDGET = "click_widget"
    PRESS_KEY = "press_key"


class VerificationKind(str, Enum):
    NONE = "none"
    LOG_GAINED = "log_gained"
    MOVED_CLOSER = "moved_closer"
    PLANE_CHANGED = "plane_changed"
    BANK_OPEN = "bank_open"
    LOGS_DEPOSITED = "logs_deposited"
    BANK_CLOSED = "bank_closed"
    ROUTE_TRANSITION_READY = "route_transition_ready"


@dataclass(frozen=True)
class Verification:
    kind: VerificationKind
    before_tick: int
    deadline_tick: int
    before_log_count: int | None = None
    before_location: WorldPoint | None = None
    target_location: WorldPoint | None = None
    expected_plane: int | None = None
    source_session_id: str | None = None
    target_radius: int | None = None


@dataclass(frozen=True)
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
    verification: Verification | None = None
    source_menu_client_tick: int | None = None
    target_param0: int | None = None
    target_param1: int | None = None
    source_session_id: str | None = None
    source_dialogue_client_tick: int | None = None


@dataclass(frozen=True)
class Decision:
    phase: TaskPhase
    reason: str
    action: Action


@dataclass
class TaskProgress:
    phase: TaskPhase = TaskPhase.FIND_TREE
    route_index: int = 0
    target_key: str | None = None
    pending: Verification | None = None
    cycles_completed: int = 0
    failures: list[str] = field(default_factory=list)
    resume_phase: TaskPhase | None = None
