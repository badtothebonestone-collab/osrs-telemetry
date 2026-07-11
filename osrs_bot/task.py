from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .definition import FixedRouteStep
from .model import (
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryConstraint,
    NearbyObject,
    Observation,
    TaskConstraints,
    VerificationKind,
    VerificationSpec,
    WorldPoint,
)
from .profile import DEFAULT_BINDING, BoundProfile
from .task_contract import Decision, ObservationRequest, TaskSnapshot, TaskStatus
from .verification import OutcomeKind, VerificationResult

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


@dataclass
class TaskProgress:
    phase: TaskPhase = TaskPhase.FIND_TREE
    route_index: int = 0
    target_key: str | None = None
    pending: VerificationSpec | None = None
    cycles_completed: int = 0
    failures: list[str] = field(default_factory=list)
    resume_phase: TaskPhase | None = None

class WoodcutBankTask:
    """Explicit woodcut/bank FSM bound to one validated task/site profile."""

    def __init__(self, binding: BoundProfile = DEFAULT_BINDING) -> None:
        if not isinstance(binding, BoundProfile):
            raise TypeError("binding must be a validated BoundProfile")
        if len(binding.definition.resource.produced_item_ids) != 1:
            raise ValueError("WoodcutBankTask requires exactly one produced item ID")
        self.binding = binding
        self.definition = binding.definition
        self._produced_item_id = next(
            iter(binding.definition.resource.produced_item_ids)
        )
        self.progress = TaskProgress()
        self._movement_verified = False
        self._route_settle_location: WorldPoint | None = None
        self._route_settle_since_tick: int | None = None

    def observation_request(self) -> ObservationRequest:
        """Request only the current fixed walk target for projection."""
        route = self._current_route()
        if route is None or self.progress.route_index >= len(route):
            return ObservationRequest()
        step = route[self.progress.route_index]
        if not step.is_walk:
            return ObservationRequest()
        return ObservationRequest(((step.target_key, step.location),))

    def snapshot(self) -> TaskSnapshot:
        if self.progress.phase is TaskPhase.COMPLETE:
            status = TaskStatus.COMPLETE
        elif self.progress.phase is TaskPhase.BLOCKED:
            status = TaskStatus.BLOCKED
        else:
            status = TaskStatus.RUNNING
        blocker = (
            self.progress.failures[-1]
            if status is TaskStatus.BLOCKED and self.progress.failures
            else None
        )
        return TaskSnapshot(
            task_id="woodcut_bank",
            status=status,
            state=self.progress.phase.value,
            blocker=blocker,
        )

    def decide(self, observation: Observation) -> Decision:
        if self.progress.phase in {TaskPhase.COMPLETE, TaskPhase.BLOCKED}:
            return self._wait(observation, f"task is {self.progress.phase.value}")

        if self.progress.pending is not None:
            return self._wait(observation, "waiting for external action verification")

        if not observation.loaded_scene:
            return self._wait(observation, "waiting for a fresh loaded-scene observation")
        if observation.plane is None or observation.location is None:
            return self._wait(observation, "player location is incomplete")
        if not observation.session_id:
            return self._wait(observation, "session identity is unavailable")
        if not observation.menu_fresh:
            return self._wait(observation, "client menu evidence is stale")
        if observation.menu_client_tick is None:
            return self._wait(observation, "client menu sample is unavailable")
        if observation.location.plane != observation.plane:
            return self._wait(observation, "player plane and location disagree")
        inventory_already_verified_for_return = self.progress.phase in {
            TaskPhase.CLOSE_BANK,
            TaskPhase.NAVIGATE_TO_TREES,
        }
        if (
            not observation.inventory.known
            and not inventory_already_verified_for_return
        ):
            return self._wait(observation, "inventory is not observable")

        held_ids = {
            item.item_id for item in observation.inventory.items if item.quantity > 0
        }
        if (
            self.definition.inventory.require_only_allowed_items
            and not held_ids.issubset(self.definition.inventory.allowed_item_ids)
        ):
            return self._block(
                observation, "inventory violates the selected task definition"
            )

        if self.progress.phase == TaskPhase.FIND_TREE:
            return self._find_tree(observation)
        if self.progress.phase == TaskPhase.CHOP:
            return self._chop(observation)
        if self.progress.phase in {TaskPhase.VERIFY_LOGS, TaskPhase.VERIFY_DEPOSIT}:
            return self._block(observation, "verification phase has no pending verification")
        if self.progress.phase == TaskPhase.NAVIGATE_TO_BANK:
            return self._navigate(observation, self.definition.route_to_bank.steps)
        if self.progress.phase == TaskPhase.OPEN_BANK:
            return self._open_bank(observation)
        if self.progress.phase == TaskPhase.DEPOSIT_LOGS:
            return self._deposit_logs(observation)
        if self.progress.phase == TaskPhase.CLOSE_BANK:
            return self._close_bank(observation)
        if self.progress.phase == TaskPhase.NAVIGATE_TO_TREES:
            return self._navigate(observation, self.definition.route_to_resource.steps)
        if self.progress.phase == TaskPhase.STAIR_DIALOGUE:
            return self._choose_stair_direction(observation)
        return self._block(observation, "unknown task phase")

    def apply_verification(self, result: VerificationResult) -> None:
        """Apply the sole external verifier's result to the pending action."""
        pending = self.progress.pending
        if pending is None:
            raise RuntimeError("no action verification is pending")
        self.progress.pending = None

        if not result.passed or result.outcome is None:
            self.progress.phase = TaskPhase.BLOCKED
            self.progress.failures.append(f"verification failed: {result.reason}")
            return

        outcome = result.outcome.kind

        if pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
            if outcome is not OutcomeKind.ITEM_QUANTITY_INCREASED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            return
        if pending.kind is VerificationKind.MOVED_CLOSER:
            if outcome not in {OutcomeKind.MOVED_CLOSER, OutcomeKind.ARRIVED}:
                return self._block_verification_outcome(pending, outcome)
            self._movement_verified = True
            self._route_settle_location = None
            self._route_settle_since_tick = None
            return
        if pending.kind is VerificationKind.ROUTE_TRANSITION:
            if outcome is OutcomeKind.DIALOGUE_OPTION_APPEARED:
                self.progress.resume_phase = self.progress.phase
                self.progress.phase = TaskPhase.STAIR_DIALOGUE
                return
            if outcome is not OutcomeKind.PLANE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.route_index += 1
            route = self._current_route()
            if route is not None and self.progress.route_index >= len(route):
                self._finish_route()
            return
        if pending.kind is VerificationKind.PLANE_CHANGED:
            if outcome is not OutcomeKind.PLANE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            if self.progress.phase is not TaskPhase.STAIR_DIALOGUE or self.progress.resume_phase is None:
                self.progress.phase = TaskPhase.BLOCKED
                self.progress.failures.append("plane proof arrived outside stair dialogue")
                return
            self.progress.phase = self.progress.resume_phase
            self.progress.resume_phase = None
            self.progress.route_index += 1
            route = self._current_route()
            if route is not None and self.progress.route_index >= len(route):
                self._finish_route()
            return
        if pending.kind is VerificationKind.INTERFACE_OPENED:
            if outcome is not OutcomeKind.INTERFACE_OPENED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
            return
        if pending.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
            if outcome is not OutcomeKind.ITEM_QUANTITY_EQUALS:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.CLOSE_BANK
            return
        if pending.kind is VerificationKind.INTERFACE_CLOSED:
            if outcome is not OutcomeKind.INTERFACE_CLOSED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
            self.progress.route_index = 0
            return
        self.progress.phase = TaskPhase.BLOCKED
        self.progress.failures.append(f"unsupported verification result: {pending.kind.value}")

    def _block_verification_outcome(
        self, pending: VerificationSpec, outcome: OutcomeKind
    ) -> None:
        self.progress.phase = TaskPhase.BLOCKED
        self.progress.failures.append(
            f"unexpected {outcome.value} outcome for {pending.kind.value}"
        )

    def _find_tree(self, observation: Observation) -> Decision:
        if self.progress.cycles_completed >= self.binding.profile.cycle_goal:
            self.progress.phase = TaskPhase.COMPLETE
            return self._wait(observation, "profile cycle goal is complete")
        if observation.inventory.full:
            if (
                self.definition.inventory.require_produced_item_when_full
                and observation.inventory.quantity(self._produced_item_id) == 0
            ):
                return self._block(
                    observation,
                    "inventory is full without the definition's produced item",
                )
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0
            return self._wait(observation, "inventory is full; fixed bank route selected")
        work_area = self.definition.resource.work_area
        if (
            observation.plane != work_area.anchor.plane
            or observation.location.distance_to(work_area.anchor) > work_area.radius
        ):
            return self._block(observation, "player is outside the supported work area")

        actionable = [
            item for item in observation.nearby_objects if self._is_actionable_tree(item)
        ]
        candidates = [
            item for item in actionable
            if self._tree_aim_is_unambiguous(item, actionable)
        ]
        if not candidates:
            return self._wait(
                observation,
                "no geometrically unambiguous configured resource is observed",
            )
        candidates.sort(key=lambda item: (observation.location.distance_to(item.location), item.key))
        self.progress.target_key = candidates[0].key
        self.progress.phase = TaskPhase.CHOP
        return self._wait(observation, "selected nearest exact configured resource")

    def _chop(self, observation: Observation) -> Decision:
        if observation.inventory.full:
            self.progress.target_key = None
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0
            return self._wait(observation, "inventory filled before the chop")

        target = observation.object_by_key(self.progress.target_key)
        if target is None or not self._is_actionable_tree(target):
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            return self._wait(
                observation, "selected resource is no longer exactly actionable"
            )

        verification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.resource_deadline_ticks
            ),
            item_id=self._produced_item_id,
            before_quantity=observation.inventory.quantity(self._produced_item_id),
            source_session_id=observation.session_id,
        )
        self.progress.phase = TaskPhase.VERIFY_LOGS
        return self._emit_action(
            observation,
            ActionKind.INTERACT_OBJECT,
            f"{self.definition.resource.selector.action} configured resource",
            "interact with exact configured resource",
            self.definition.resource.selector.action,
            verification,
            target=target,
        )

    def _navigate(
        self, observation: Observation, route: tuple[FixedRouteStep, ...]
    ) -> Decision:
        if self._movement_verified:
            if self._route_settle_location != observation.location:
                self._route_settle_location = observation.location
                self._route_settle_since_tick = observation.tick
                return self._wait(observation, "waiting for player location to settle")
            assert self._route_settle_since_tick is not None
            if (
                observation.tick - self._route_settle_since_tick
                < self.definition.verification.route_stable_ticks
            ):
                return self._wait(observation, "waiting for player location to settle")
            self._movement_verified = False
            self._route_settle_location = None
            self._route_settle_since_tick = None

        if self.progress.route_index >= len(route):
            self._finish_route()
            return self._wait(observation, "fixed route complete")

        step = route[self.progress.route_index]
        if observation.plane != step.location.plane:
            return self._block(observation, f"wrong plane for route step {step.step_id}")

        if step.is_walk:
            if observation.location.distance_to(step.location) <= step.arrival_radius:
                self.progress.route_index += 1
                if self.progress.route_index >= len(route):
                    self._finish_route()
                return self._wait(observation, f"arrived at route step {step.step_id}")
            target = observation.object_by_key(step.target_key)
            if target is None:
                return self._wait(
                    observation,
                    f"waiting for route projection {step.step_id}",
                )
            if not self._walk_projection_identity_matches(target, step):
                return self._block(
                    observation,
                    f"route projection identity mismatch for {step.step_id}",
                )
            if not self._has_geometry(target):
                return self._wait(
                    observation,
                    f"waiting for actionable route projection {step.step_id}",
                )
            verification = VerificationSpec(
                VerificationKind.MOVED_CLOSER,
                before_tick=observation.tick,
                deadline_tick=(
                    observation.tick
                    + self.definition.verification.action_deadline_ticks
                ),
                before_location=observation.location,
                target_location=step.location,
                source_session_id=observation.session_id,
                target_radius=step.arrival_radius,
            )
            return self._emit_action(
                observation, ActionKind.WALK, f"Walk to {step.step_id}",
                f"walk fixed route step {step.step_id}", "Walk here",
                verification, target=target,
            )

        target = self._strict_route_object(observation, step)
        if target is None:
            return self._wait(
                observation,
                f"waiting for strict route object {step.step_id}",
            )
        route_option = (
            step.action
            if target.supports(step.action)
            else next(
                (option for option in target.actions if option in step.allowed_actions),
                None,
            )
        )
        if route_option is None:
            return self._block(observation, f"route action unavailable for {step.step_id}")
        verification = VerificationSpec(
            VerificationKind.ROUTE_TRANSITION,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=step.expected_plane,
            source_session_id=observation.session_id,
            dialogue_prompt_contains=(
                self.definition.verification.transition_dialogue_prompt_contains
            ),
            dialogue_option_contains=(
                self.definition.verification.transition_up_option_contains
                if step.expected_plane > observation.plane
                else self.definition.verification.transition_down_option_contains
            ),
        )
        return self._emit_action(
            observation, ActionKind.INTERACT_OBJECT,
            f"{route_option} {step.object_name}",
            f"interact with fixed route step {step.step_id}", route_option,
            verification, target=target,
        )

    def _choose_stair_direction(self, observation: Observation) -> Decision:
        route = self._current_route()
        if route is None or self.progress.route_index >= len(route):
            return self._block(observation, "stair dialogue has no route step")
        step = route[self.progress.route_index]
        if step.expected_plane is None:
            return self._block(observation, "stair route direction is unknown")
        widgets = observation.widgets
        expectations = self.definition.verification
        if (
            not widgets.dialogue_active
            or widgets.dialogue_type != "options"
            or expectations.transition_dialogue_prompt_contains.lower()
            not in widgets.dialogue_prompt.lower()
            or not widgets.dialogue_number_keys
            or widgets.dialogue_client_tick is None
        ):
            return self._block(
                observation, "expected route-transition dialogue is unavailable"
            )
        direction = "up" if step.expected_plane > observation.plane else "down"
        option_contains = (
            expectations.transition_up_option_contains
            if direction == "up"
            else expectations.transition_down_option_contains
        )
        matches = [
            option for option in widgets.dialogue_options
            if option.visible
            and option_contains.lower() in option.text.lower()
            and option.key in {str(value) for value in range(1, 10)}
        ]
        if len(matches) != 1:
            return self._block(
                observation,
                f"exact {direction} route-transition option is unavailable",
            )
        selected = matches[0]
        verification = VerificationSpec(
            VerificationKind.PLANE_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick + expectations.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=step.expected_plane,
            source_session_id=observation.session_id,
        )
        self.progress.pending = verification
        return Decision(
            self.progress.phase.value,
            f"choose exact {direction} route-transition option",
            Action(
                ActionKind.PRESS_KEY,
                f"Choose {selected.text}",
                observation.tick,
                option=selected.text,
                target_key=f"dialogue:{selected.index}",
                target_name=selected.text,
                target_id=selected.index,
                key=selected.key,
                verification=verification,
                source_session_id=observation.session_id,
                source_dialogue_client_tick=widgets.dialogue_client_tick,
                task_constraints=TaskConstraints(
                    dialogue=DialogueOptionConstraint(
                        prompt_contains=(
                            expectations.transition_dialogue_prompt_contains
                        ),
                        option_text=selected.text,
                        option_index=selected.index,
                        option_key=selected.key,
                    )
                ),
            ),
        )

    def _open_bank(self, observation: Observation) -> Decision:
        bank = self.definition.bank
        selector = bank.selector
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.widgets.bank_open:
            if not observation.widgets.bank_readable:
                return self._wait(observation, "bank is open but not readable")
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
            return self._wait(observation, "bank is already open and readable")
        if observation.plane != bank.anchor.plane:
            return self._block(
                observation, "bank may only be opened on its configured plane"
            )

        targets = [
            item
            for item in observation.nearby_objects
            if item.object_id in selector.object_ids
            and item.name == selector.name
            and item.location == bank.anchor
            and item.supports(selector.action)
            and self._has_geometry(item)
            and observation.location.distance_to(item.location)
            <= bank.interaction_radius
        ]
        if not targets:
            return self._block(observation, "exact configured bank is unavailable")
        target = sorted(targets, key=lambda item: item.key)[0]
        verification = VerificationSpec(
            VerificationKind.INTERFACE_OPENED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            expected_plane=bank.anchor.plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        return self._emit_action(
            observation,
            ActionKind.INTERACT_OBJECT,
            "Open configured bank",
            "open exact configured bank",
            selector.action,
            verification,
            target=target,
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(
                    BANK_INTERFACE_NAME, bank.anchor.plane, False
                )
            ),
        )

    def _deposit_logs(self, observation: Observation) -> Decision:
        bank_plane = self.definition.bank.anchor.plane
        inventory_rule = self.definition.inventory
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.plane != bank_plane:
            return self._block(
                observation, "items may only be deposited on the configured bank plane"
            )
        if not observation.widgets.bank_open or not observation.widgets.bank_readable:
            return self._block(observation, "readable bank must remain open before deposit")
        held = [item for item in observation.inventory.items if item.quantity > 0]
        if (
            inventory_rule.require_nonempty_deposit
            and (
                not held
                or not any(
                    item.item_id in inventory_rule.deposit_item_ids
                    for item in held
                )
            )
        ):
            return self._block(observation, "there are no configured items to deposit")
        if any(
            item.item_id not in inventory_rule.deposit_item_ids for item in held
        ):
            return self._block(
                observation, "deposit inventory violates the task definition"
            )

        target = observation.widgets.deposit_inventory
        if (
            target is None
            or target.name != DEPOSIT_INVENTORY_WIDGET_KEY
            or not target.visible
        ):
            return self._block(observation, "deposit-inventory widget is unavailable")
        point = target.screen_point
        if point is None:
            return self._block(observation, "deposit-inventory widget has no geometry")

        verification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_EQUALS,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            item_id=self._produced_item_id,
            expected_quantity=(
                self.definition.verification.deposit_expected_quantity
            ),
            expected_plane=bank_plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        self.progress.phase = TaskPhase.VERIFY_DEPOSIT
        return self._emit_action(
            observation, ActionKind.CLICK_WIDGET, "Deposit inventory",
            "deposit all configured items",
            DEPOSIT_INVENTORY_WIDGET_KEY,
            verification,
            target_key=DEPOSIT_INVENTORY_WIDGET_KEY,
            target_name=target.name,
            screen_point=point,
            task_constraints=TaskConstraints(
                inventory=InventoryConstraint(inventory_rule.deposit_item_ids),
                interface=InterfaceConstraint(
                    BANK_INTERFACE_NAME,
                    bank_plane,
                    True,
                    require_readable=True,
                ),
            ),
        )

    def _close_bank(self, observation: Observation) -> Decision:
        bank_plane = self.definition.bank.anchor.plane
        if not observation.widgets.bank_known:
            return self._wait(observation, "bank state is not observable")
        if observation.widgets.bank_pin_open:
            return self._block(observation, "bank PIN handling is out of scope")
        if observation.plane != bank_plane:
            return self._block(
                observation, "bank may only be closed on its configured plane"
            )
        if not observation.widgets.bank_open:
            self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
            self.progress.route_index = 0
            return self._wait(observation, "bank is already closed")
        verification = VerificationSpec(
            VerificationKind.INTERFACE_CLOSED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            expected_plane=bank_plane,
            source_session_id=observation.session_id,
            interface_name=BANK_INTERFACE_NAME,
        )
        target = observation.widgets.close_bank
        point = (
            target.screen_point
            if target is not None
            and target.name == CLOSE_BANK_WIDGET_KEY
            and target.visible
            else None
        )
        if point is None:
            if not observation.widgets.keyboard_close_possible:
                return self._block(observation, "close-bank input is unavailable")
            self.progress.pending = verification
            return Decision(
                self.progress.phase.value,
                "close bank with verified Escape support",
                Action(
                    ActionKind.PRESS_KEY,
                    "Close bank with Escape",
                    observation.tick,
                    option="Close bank",
                    target_key="close_bank_keyboard",
                    target_name="Close bank",
                    target_id=0,
                    key="escape",
                    verification=verification,
                    source_session_id=observation.session_id,
                    task_constraints=TaskConstraints(
                        interface=InterfaceConstraint(
                            BANK_INTERFACE_NAME,
                            bank_plane,
                            True,
                            require_keyboard_close=True,
                        )
                    ),
                ),
            )

        return self._emit_action(
            observation, ActionKind.CLICK_WIDGET, "Close bank",
            "close bank before return route",
            CLOSE_BANK_WIDGET_KEY,
            verification,
            target_key=CLOSE_BANK_WIDGET_KEY,
            target_name=target.name,
            screen_point=point,
            task_constraints=TaskConstraints(
                interface=InterfaceConstraint(BANK_INTERFACE_NAME, bank_plane, True)
            ),
        )

    def _emit_action(
        self,
        observation: Observation,
        kind: ActionKind,
        label: str,
        reason: str,
        option: str,
        verification: VerificationSpec,
        *,
        target: NearbyObject | None = None,
        target_key: str | None = None,
        target_name: str | None = None,
        screen_point=None,
        task_constraints: TaskConstraints | None = None,
    ) -> Decision:
        self.progress.pending = verification
        if target is not None:
            target_key, target_name = target.key, target.name
            screen_point = target.geometry.screen_point
        action = Action(
            kind, label, observation.tick, option=option, target_key=target_key,
            target_name=target_name,
            target_id=target.object_id if target is not None else None,
            screen_point=screen_point, verification=verification,
            source_menu_client_tick=observation.menu_client_tick,
            target_param0=target.scene_x if target is not None else None,
            target_param1=target.scene_y if target is not None else None,
            source_session_id=observation.session_id,
            task_constraints=task_constraints or TaskConstraints(),
        )
        return Decision(self.progress.phase.value, reason, action)

    def _current_route(self) -> tuple[FixedRouteStep, ...] | None:
        phase = self.progress.resume_phase if self.progress.phase is TaskPhase.STAIR_DIALOGUE else self.progress.phase
        if phase == TaskPhase.NAVIGATE_TO_BANK:
            return self.definition.route_to_bank.steps
        if phase == TaskPhase.NAVIGATE_TO_TREES:
            return self.definition.route_to_resource.steps
        return None

    def _finish_route(self) -> None:
        if self.progress.phase == TaskPhase.NAVIGATE_TO_BANK:
            self.progress.phase = TaskPhase.OPEN_BANK
        elif self.progress.phase == TaskPhase.NAVIGATE_TO_TREES:
            self.progress.cycles_completed += 1
            self.progress.phase = (
                TaskPhase.COMPLETE
                if self.progress.cycles_completed >= self.binding.profile.cycle_goal
                else TaskPhase.FIND_TREE
            )

    def _strict_route_object(
        self, observation: Observation, step: FixedRouteStep
    ) -> NearbyObject | None:
        matches = [
            item
            for item in observation.nearby_objects
            if item.object_id == step.object_id
            and item.name == step.object_name
            and item.location is not None
            and item.location.plane == step.location.plane
            and item.location.distance_to(step.location) <= step.arrival_radius
            and observation.location.distance_to(item.location) <= step.arrival_radius
            and any(item.supports(option) for option in step.allowed_actions)
            and self._has_geometry(item)
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: (observation.location.distance_to(item.location), item.key))[0]

    def _walk_projection_identity_matches(
        self, target: NearbyObject | None, step: FixedRouteStep
    ) -> bool:
        return bool(
            target is not None
            and target.key == step.target_key
            and target.object_id == 0
            and target.name == step.target_key
            and target.kind == "NAVIGATION_TILE"
            and target.actions == ("Walk here",)
            and target.location == step.location
            and target.scene_x is not None
            and target.scene_y is not None
        )

    def _is_exact_walk_projection(
        self, target: NearbyObject | None, step: FixedRouteStep
    ) -> bool:
        return bool(
            self._walk_projection_identity_matches(target, step)
            and target is not None
            and self._has_geometry(target)
        )

    def _is_actionable_tree(self, target: NearbyObject) -> bool:
        selector = self.definition.resource.selector
        work_area = self.definition.resource.work_area
        return bool(
            target.object_id in selector.object_ids
            and target.name == selector.name
            and target.supports(selector.action)
            and target.location is not None
            and target.location.plane == work_area.anchor.plane
            and target.location.distance_to(work_area.anchor) <= work_area.radius
            and target.scene_x is not None
            and target.scene_y is not None
            and self._has_geometry(target)
        )

    @staticmethod
    def _tree_aim_is_unambiguous(
        target: NearbyObject, actionable: list[NearbyObject]
    ) -> bool:
        point = target.geometry.screen_point
        if point is None:
            return False
        return not any(
            other.key != target.key
            and other.geometry.screen_bounds is not None
            and other.geometry.screen_bounds.contains(point)
            for other in actionable
        )

    @staticmethod
    def _has_geometry(target: NearbyObject) -> bool:
        geometry = target.geometry
        point = geometry.screen_point
        return bool(
            geometry.available
            and geometry.on_screen
            and geometry.visible
            and geometry.actionable
            and point is not None
            and (
                geometry.screen_bounds is None
                or geometry.screen_bounds.contains(point)
            )
        )

    def _block(self, observation: Observation, reason: str) -> Decision:
        self.progress.phase = TaskPhase.BLOCKED
        self.progress.pending = None
        self.progress.failures.append(reason)
        return self._wait(observation, reason)

    def _wait(self, observation: Observation, reason: str) -> Decision:
        return Decision(
            self.progress.phase.value,
            reason,
            Action(
                ActionKind.WAIT,
                "Wait",
                observation.tick,
                verification=self.progress.pending,
            ),
        )
