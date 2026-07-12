from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import atan2, pi

from .definition import FixedRouteStep
from .model import (
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    CAMERA_YAW_UNITS,
    CameraConstraint,
    CLOSE_BANK_WIDGET_KEY,
    DEPOSIT_INVENTORY_WIDGET_KEY,
    DialogueOption,
    DialogueOptionConstraint,
    InterfaceConstraint,
    InventoryConstraint,
    NearbyObject,
    Observation,
    TaskConstraints,
    VerificationKind,
    VerificationSpec,
    WidgetTarget,
    WorldPoint,
)
from .profile import DEFAULT_BINDING, BoundProfile
from .task_contract import (
    Decision,
    DecisionEvidence,
    ObservationRequest,
    RejectedCandidateEvidence,
    TargetEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
    TaskStatus,
)
from .verification import (
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
)


WOODCUT_BANK_TASK_ID = "woodcut_bank"
WOODCUT_BANK_TASK_DISPLAY_NAME = "Woodcut ordinary Trees and bank one inventory"
CAMERA_RECOVERY_MAX_ATTEMPTS = 8
CAMERA_RECOVERY_HOLD_MILLIS = 250
RESOURCE_NO_YIELD_MAX_RETRIES = 1


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
    blocked_from_phase: TaskPhase | None = None

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
        self._camera_recovery_step_id: str | None = None
        self._camera_recovery_attempts = 0
        self._route_projection_wait_since_tick: int | None = None
        self._pending_camera_step_id: str | None = None
        self._restart_reconciled_without_cycle_credit = False
        self._next_resource_suppression: tuple[str, str] | None = None
        self._resource_no_yield_retries = 0

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
            task_id=WOODCUT_BANK_TASK_ID,
            status=status,
            state=self.progress.phase.value,
            blocker=blocker,
            definition_id=self.definition.definition_id,
            profile_id=self.binding.profile.profile_id,
            progress=self._progress_snapshot(),
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
            if (
                pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED
                and self.progress.phase is TaskPhase.VERIFY_LOGS
                and result.failure_kind
                is VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
                and self._resource_no_yield_retries
                < RESOURCE_NO_YIELD_MAX_RETRIES
                and isinstance(self.progress.target_key, str)
                and bool(self.progress.target_key)
            ):
                self._resource_no_yield_retries += 1
                self._next_resource_suppression = (
                    self.progress.target_key,
                    "resource_no_yield",
                )
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
                return
            self._set_blocked(f"verification failed: {result.reason}")
            return

        outcome = result.outcome.kind

        if pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
            if outcome is not OutcomeKind.ITEM_QUANTITY_INCREASED:
                return self._block_verification_outcome(pending, outcome)
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            self._resource_no_yield_retries = 0
            return
        if pending.kind is VerificationKind.MOVED_CLOSER:
            if outcome not in {OutcomeKind.MOVED_CLOSER, OutcomeKind.ARRIVED}:
                return self._block_verification_outcome(pending, outcome)
            self._movement_verified = True
            self._route_settle_location = None
            self._route_settle_since_tick = None
            return
        if pending.kind is VerificationKind.CAMERA_POSE_CHANGED:
            if outcome is not OutcomeKind.CAMERA_POSE_CHANGED:
                return self._block_verification_outcome(pending, outcome)
            if (
                self._pending_camera_step_id is None
                or self._pending_camera_step_id != self._camera_recovery_step_id
            ):
                self._set_blocked("camera pose proof arrived outside route recovery")
                return
            self._camera_recovery_attempts += 1
            self._pending_camera_step_id = None
            self._route_projection_wait_since_tick = None
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
                self._set_blocked("plane proof arrived outside stair dialogue")
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
        self._set_blocked(f"unsupported verification result: {pending.kind.value}")

    def discard_pending_action(
        self, reason: str, *, target_invalidated: bool = True
    ) -> None:
        """Discard one proposal that the input boundary proved was never sent.

        This is not a failed verification: no activation occurred, so there is
        no game-state effect to verify.  Restore only the explicit phase that
        can safely produce a new proposal from a fresh observation.
        """

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("discard reason must be non-empty text")
        if not isinstance(target_invalidated, bool):
            raise TypeError("target_invalidated must be bool")
        pending = self.progress.pending
        if pending is None:
            raise RuntimeError("no pending action can be discarded")

        discardable_kinds = frozenset(
            {
                VerificationKind.ITEM_QUANTITY_INCREASED,
                VerificationKind.ITEM_QUANTITY_EQUALS,
                VerificationKind.MOVED_CLOSER,
                VerificationKind.PLANE_CHANGED,
                VerificationKind.INTERFACE_OPENED,
                VerificationKind.INTERFACE_CLOSED,
                VerificationKind.ROUTE_TRANSITION,
                VerificationKind.CAMERA_POSE_CHANGED,
            }
        )
        if pending.kind not in discardable_kinds:
            raise RuntimeError(
                f"unsupported unsent verification kind: {pending.kind.value}"
            )

        self.progress.pending = None
        if pending.kind is VerificationKind.ITEM_QUANTITY_INCREASED:
            if target_invalidated:
                if self.progress.target_key is not None:
                    self._next_resource_suppression = (
                        self.progress.target_key,
                        "preactivation_target_invalidated",
                    )
                self.progress.target_key = None
                self.progress.phase = TaskPhase.FIND_TREE
            else:
                self.progress.phase = TaskPhase.CHOP
        elif pending.kind is VerificationKind.ITEM_QUANTITY_EQUALS:
            self.progress.phase = TaskPhase.DEPOSIT_LOGS
        elif pending.kind is VerificationKind.CAMERA_POSE_CHANGED:
            self._pending_camera_step_id = None

    def _block_verification_outcome(
        self, pending: VerificationSpec, outcome: OutcomeKind
    ) -> None:
        self._set_blocked(
            f"unexpected {outcome.value} outcome for {pending.kind.value}"
        )

    def _find_tree(self, observation: Observation) -> Decision:
        suppression = self._next_resource_suppression
        self._next_resource_suppression = None
        suppressed_key = suppression[0] if suppression is not None else None
        suppression_code = suppression[1] if suppression is not None else None
        if self.progress.cycles_completed >= self.binding.profile.cycle_goal:
            self.progress.phase = TaskPhase.COMPLETE
            return self._wait(observation, "profile cycle goal is complete")
        work_area = self.definition.resource.work_area
        if observation.inventory.full:
            if (
                self.definition.inventory.require_produced_item_when_full
                and observation.inventory.quantity(self._produced_item_id) == 0
            ):
                return self._block(
                    observation,
                    "inventory is full without the definition's produced item",
                )
            outside_work_area = bool(
                observation.plane != work_area.anchor.plane
                or observation.location.distance_to(work_area.anchor)
                > work_area.radius
            )
            resume_index = (
                self._bank_route_resume_index(observation)
                if outside_work_area
                else None
            )
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0 if resume_index is None else resume_index
            if resume_index is not None:
                self.progress.target_key = None
                self.progress.pending = None
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                return self._wait(
                    observation,
                    "reobserved full inventory on the validated bank route",
                )
            return self._wait(observation, "inventory is full; fixed bank route selected")
        if (
            observation.plane != work_area.anchor.plane
            or observation.location.distance_to(work_area.anchor) > work_area.radius
        ):
            bank = self.definition.bank
            resume_index = self._return_route_resume_index(observation)
            inventory_empty = bool(
                observation.inventory.known
                and observation.inventory.occupied_slots == 0
                and not observation.inventory.items
            )
            inside_bank_area = bool(
                observation.plane == bank.anchor.plane
                and observation.location.distance_to(bank.anchor)
                <= bank.interaction_radius
            )
            if (
                inventory_empty
                and inside_bank_area
                and not observation.widgets.bank_known
            ):
                # Inside the bank UI's interaction area, bank_open=false is not
                # closure proof unless bank_known is also true. Do not let an
                # overlapping route step bypass that uncertainty.
                resume_index = None
            if (
                inventory_empty
                and inside_bank_area
                and observation.widgets.bank_pin_open
            ):
                return self._block(
                    observation,
                    "bank PIN handling is out of scope",
                )
            if (
                inventory_empty
                and observation.widgets.bank_known
                and inside_bank_area
                and (
                    observation.widgets.bank_open
                    or resume_index is None
                )
            ):
                self.progress.target_key = None
                self.progress.pending = None
                self.progress.phase = (
                    TaskPhase.CLOSE_BANK
                    if observation.widgets.bank_open
                    else TaskPhase.NAVIGATE_TO_TREES
                )
                self.progress.route_index = 0
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                reason = (
                    "reobserved empty inventory with an open bank at the "
                    "validated bank interaction area"
                    if observation.widgets.bank_open
                    else "reobserved empty inventory at the validated bank "
                    "return-route start"
                )
                return self._wait(observation, reason)
            if resume_index is not None:
                self.progress.target_key = None
                self.progress.pending = None
                self.progress.phase = TaskPhase.NAVIGATE_TO_TREES
                self.progress.route_index = resume_index
                self._restart_reconciled_without_cycle_credit = True
                self._movement_verified = False
                self._route_settle_location = None
                self._route_settle_since_tick = None
                self._reset_camera_recovery()
                return self._wait(
                    observation,
                    "reobserved empty inventory on the validated return route",
                )
            return self._block(observation, "player is outside the supported work area")

        candidates, rejected = self._classify_trees(observation)
        if suppressed_key is not None:
            assert suppression_code is not None
            suppressed = tuple(
                target for target in candidates if target.key == suppressed_key
            )
            candidates = tuple(
                target for target in candidates if target.key != suppressed_key
            )
            rejected += tuple(
                (target, (suppression_code,))
                for target in suppressed
            )
        evidence = self._object_decision_evidence(
            observation,
            action=self.definition.resource.selector.action,
            eligible=candidates,
            rejected=rejected,
        )
        if not candidates:
            return self._wait(
                observation,
                "no geometrically unambiguous configured resource is observed",
                evidence=evidence,
            )
        self.progress.target_key = candidates[0].key
        self.progress.phase = TaskPhase.CHOP
        return self._wait(
            observation,
            "selected nearest exact configured resource",
            evidence=self._object_decision_evidence(
                observation,
                action=self.definition.resource.selector.action,
                selected=candidates[0],
                eligible=candidates,
                rejected=rejected,
            ),
        )

    def _return_route_resume_index(
        self, observation: Observation
    ) -> int | None:
        if observation.inventory.occupied_slots != 0 or observation.inventory.items:
            return None
        matches = [
            index
            for index, step in enumerate(self.definition.route_to_resource.steps)
            if observation.plane == step.location.plane
            and observation.location.distance_to(step.location)
            <= step.arrival_radius
        ]
        return max(matches) if matches else None

    def _bank_route_resume_index(
        self, observation: Observation
    ) -> int | None:
        if not observation.inventory.full:
            return None
        matches = [
            index
            for index, step in enumerate(self.definition.route_to_bank.steps)
            if observation.plane == step.location.plane
            and observation.location.distance_to(step.location)
            <= step.arrival_radius
        ]
        return max(matches) if matches else None

    def _chop(self, observation: Observation) -> Decision:
        if observation.inventory.full:
            self.progress.target_key = None
            self.progress.phase = TaskPhase.NAVIGATE_TO_BANK
            self.progress.route_index = 0
            return self._wait(observation, "inventory filled before the chop")

        target = observation.object_by_key(self.progress.target_key)
        target_rejections = (
            self._tree_rejection_codes(target) if target is not None else ()
        )
        if target is None or target_rejections:
            evidence = (
                self._object_decision_evidence(
                    observation,
                    action=self.definition.resource.selector.action,
                    rejected=((target, target_rejections),),
                )
                if target is not None
                else DecisionEvidence()
            )
            self.progress.target_key = None
            self.progress.phase = TaskPhase.FIND_TREE
            return self._wait(
                observation,
                "selected resource is no longer exactly actionable",
                evidence=evidence,
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
            evidence=self._object_decision_evidence(
                observation,
                action=self.definition.resource.selector.action,
                selected=target,
                eligible=(target,),
            ),
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
                self._reset_camera_recovery()
                self.progress.route_index += 1
                if self.progress.route_index >= len(route):
                    self._finish_route()
                return self._wait(observation, f"arrived at route step {step.step_id}")
            target = observation.object_by_key(step.target_key)
            if target is None:
                self._route_projection_wait_since_tick = None
                return self._wait(
                    observation,
                    f"waiting for route projection {step.step_id}",
                )
            if not self._walk_projection_identity_matches(target, step):
                return self._block(
                    observation,
                    f"route projection identity mismatch for {step.step_id}",
                    evidence=self._object_decision_evidence(
                        observation,
                        action=step.action,
                        rejected=((target, self._walk_projection_rejection_codes(target, step)),),
                    ),
                )
            if not self._has_geometry(target):
                return self._recover_route_projection(observation, step, target)
            self._reset_camera_recovery()
            verification = VerificationSpec(
                VerificationKind.MOVED_CLOSER,
                before_tick=observation.tick,
                deadline_tick=(
                    observation.tick
                    + self.definition.verification.movement_deadline_ticks
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
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    selected=target,
                    eligible=(target,),
                ),
            )

        route_targets, rejected, route_actions = self._classify_route_objects(
            observation, step
        )
        if not route_targets:
            return self._wait(
                observation,
                f"waiting for strict route object {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=rejected,
                    candidate_actions=route_actions,
                ),
            )
        target = route_targets[0]
        route_option = route_actions.get(target.key)
        if route_option is None:
            return self._block(
                observation,
                f"route action unavailable for {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, ("action_unavailable",)), *rejected),
                ),
            )
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
            evidence=self._object_decision_evidence(
                observation,
                action=route_option,
                selected=target,
                eligible=route_targets,
                rejected=rejected,
                candidate_actions=route_actions,
            ),
        )

    def _recover_route_projection(
        self,
        observation: Observation,
        step: FixedRouteStep,
        target: NearbyObject,
    ) -> Decision:
        if self._camera_recovery_step_id != step.step_id:
            self._reset_camera_recovery()
            self._camera_recovery_step_id = step.step_id
        if self._route_projection_wait_since_tick is None:
            self._route_projection_wait_since_tick = observation.tick
            return self._wait(
                observation,
                f"waiting for stable route projection {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, self._geometry_rejection_codes(target)),),
                ),
            )
        if observation.tick <= self._route_projection_wait_since_tick:
            return self._wait(
                observation,
                f"waiting for a later route projection {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, self._geometry_rejection_codes(target)),),
                ),
            )
        if self._camera_recovery_attempts >= CAMERA_RECOVERY_MAX_ATTEMPTS:
            return self._block(
                observation,
                f"camera recovery exhausted for route projection {step.step_id}",
                evidence=self._object_decision_evidence(
                    observation,
                    action=step.action,
                    rejected=((target, self._geometry_rejection_codes(target)),),
                ),
            )
        if observation.camera_yaw is None or observation.geometry_frame_id is None:
            return self._block(
                observation,
                f"camera pose unavailable for route projection {step.step_id}",
            )
        direction = self._camera_turn_direction(
            observation.location, step.location, observation.camera_yaw
        )
        if direction is None:
            return self._block(
                observation,
                f"route projection {step.step_id} is unavailable at the aligned camera yaw",
            )
        verification = VerificationSpec(
            VerificationKind.CAMERA_POSE_CHANGED,
            before_tick=observation.tick,
            deadline_tick=(
                observation.tick
                + self.definition.verification.action_deadline_ticks
            ),
            before_location=observation.location,
            source_session_id=observation.session_id,
            before_camera_yaw=observation.camera_yaw,
            before_geometry_frame_id=observation.geometry_frame_id,
            camera_key=direction,
        )
        constraint = CameraConstraint(
            target_key=step.target_key,
            target_location=step.location,
            source_location=observation.location,
            source_geometry_frame_id=observation.geometry_frame_id,
            before_yaw=observation.camera_yaw,
            direction=direction,
            hold_millis=CAMERA_RECOVERY_HOLD_MILLIS,
        )
        self.progress.pending = verification
        self._pending_camera_step_id = step.step_id
        return Decision(
            self.progress.phase.value,
            (
                f"turn camera {direction} for route projection {step.step_id} "
                f"({self._camera_recovery_attempts + 1}/"
                f"{CAMERA_RECOVERY_MAX_ATTEMPTS})"
            ),
            Action(
                ActionKind.PRESS_KEY,
                f"Turn camera toward {step.step_id}",
                observation.tick,
                option=f"Turn camera {direction}",
                target_key=target.key,
                target_name=target.name,
                target_id=target.object_id,
                key=direction,
                key_hold_millis=CAMERA_RECOVERY_HOLD_MILLIS,
                verification=verification,
                target_param0=target.scene_x,
                target_param1=target.scene_y,
                source_session_id=observation.session_id,
                task_constraints=TaskConstraints(camera=constraint),
            ),
            self._object_decision_evidence(
                observation,
                action=f"Turn camera {direction}",
                selected=target,
                eligible=(target,),
            ),
        )

    @staticmethod
    def _camera_turn_direction(
        source: WorldPoint,
        target: WorldPoint,
        camera_yaw: int,
    ) -> str | None:
        dx = target.x - source.x
        dy = target.y - source.y
        if dx == 0 and dy == 0:
            return None
        target_bearing = round(
            (atan2(dx, -dy) % (2 * pi))
            * CAMERA_YAW_UNITS
            / (2 * pi)
        ) % CAMERA_YAW_UNITS
        desired_camera_yaw = (
            target_bearing + CAMERA_YAW_UNITS // 2
        ) % CAMERA_YAW_UNITS
        error = (
            desired_camera_yaw - camera_yaw + CAMERA_YAW_UNITS // 2
        ) % CAMERA_YAW_UNITS - CAMERA_YAW_UNITS // 2
        if error == 0:
            return None
        return "right" if error > 0 else "left"

    def _reset_camera_recovery(self) -> None:
        self._camera_recovery_step_id = None
        self._camera_recovery_attempts = 0
        self._route_projection_wait_since_tick = None
        self._pending_camera_step_id = None

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
        matches = []
        eligible_evidence = []
        rejected_evidence = []
        allowed_keys = {str(value) for value in range(1, 10)}
        for option in widgets.dialogue_options:
            codes = []
            if not option.visible:
                codes.append("not_visible")
            if option_contains.lower() not in option.text.lower():
                codes.append("text_mismatch")
            if option.key not in allowed_keys:
                codes.append("key_unavailable")
            target_evidence = self._dialogue_target_evidence(observation, option)
            if codes:
                rejected_evidence.append(
                    RejectedCandidateEvidence(target_evidence, tuple(codes))
                )
            else:
                matches.append(option)
                eligible_evidence.append(target_evidence)
        dialogue_evidence = DecisionEvidence(
            eligible=tuple(eligible_evidence),
            rejected=tuple(rejected_evidence),
        )
        if len(matches) != 1:
            return self._block(
                observation,
                f"exact {direction} route-transition option is unavailable",
                evidence=dialogue_evidence,
            )
        selected = matches[0]
        selected_evidence = self._dialogue_target_evidence(observation, selected)
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
            DecisionEvidence(
                selected=selected_evidence,
                eligible=tuple(eligible_evidence),
                rejected=tuple(rejected_evidence),
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

        targets, rejected = self._classify_bank_objects(observation)
        if not targets:
            return self._block(
                observation,
                "exact configured bank is unavailable",
                evidence=self._object_decision_evidence(
                    observation,
                    action=selector.action,
                    rejected=rejected,
                ),
            )
        target = targets[0]
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
            evidence=self._object_decision_evidence(
                observation,
                action=selector.action,
                selected=target,
                eligible=targets,
                rejected=rejected,
            ),
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
            evidence = DecisionEvidence()
            if target is not None:
                codes = []
                if target.name != DEPOSIT_INVENTORY_WIDGET_KEY:
                    codes.append("name_mismatch")
                if not target.visible:
                    codes.append("not_visible")
                evidence = self._single_rejected_evidence(
                    self._widget_target_evidence(
                        observation,
                        DEPOSIT_INVENTORY_WIDGET_KEY,
                        target,
                        "Deposit inventory",
                    ),
                    tuple(codes),
                )
            return self._block(
                observation,
                "deposit-inventory widget is unavailable",
                evidence=evidence,
            )
        point = target.screen_point
        if point is None:
            return self._block(
                observation,
                "deposit-inventory widget has no geometry",
                evidence=self._single_rejected_evidence(
                    self._widget_target_evidence(
                        observation,
                        DEPOSIT_INVENTORY_WIDGET_KEY,
                        target,
                        "Deposit inventory",
                    ),
                    ("screen_point_unavailable",),
                ),
            )

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
            evidence=self._single_selected_evidence(
                self._widget_target_evidence(
                    observation,
                    DEPOSIT_INVENTORY_WIDGET_KEY,
                    target,
                    "Deposit inventory",
                )
            ),
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
            rejected_widget: tuple[RejectedCandidateEvidence, ...] = ()
            if target is not None:
                codes = []
                if target.name != CLOSE_BANK_WIDGET_KEY:
                    codes.append("name_mismatch")
                if not target.visible:
                    codes.append("not_visible")
                if target.screen_point is None:
                    codes.append("screen_point_unavailable")
                rejected_widget = (
                    RejectedCandidateEvidence(
                        self._widget_target_evidence(
                            observation,
                            CLOSE_BANK_WIDGET_KEY,
                            target,
                            "Close bank",
                        ),
                        tuple(codes or ("input_unavailable",)),
                    ),
                )
            if not observation.widgets.keyboard_close_possible:
                return self._block(
                    observation,
                    "close-bank input is unavailable",
                    evidence=DecisionEvidence(rejected=rejected_widget),
                )
            self.progress.pending = verification
            target_evidence = TargetEvidence(
                key="close_bank_keyboard",
                name="Close bank",
                object_id=0,
                action="Escape",
                source_tick=observation.tick,
                geometry_frame_id=observation.geometry_frame_id,
                point=None,
                bounds=None,
            )
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
                DecisionEvidence(
                    selected=target_evidence,
                    eligible=(target_evidence,),
                    rejected=rejected_widget,
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
            evidence=self._single_selected_evidence(
                self._widget_target_evidence(
                    observation,
                    CLOSE_BANK_WIDGET_KEY,
                    target,
                    "Close bank",
                )
            ),
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
        evidence: DecisionEvidence | None = None,
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
        if evidence is None and target is not None:
            evidence = self._object_decision_evidence(
                observation,
                action=option,
                selected=target,
                eligible=(target,),
            )
        return Decision(
            self.progress.phase.value,
            reason,
            action,
            evidence or DecisionEvidence(),
        )

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
            if self._restart_reconciled_without_cycle_credit:
                self._restart_reconciled_without_cycle_credit = False
                self.progress.route_index = 0
                self.progress.phase = TaskPhase.FIND_TREE
                return
            self.progress.cycles_completed += 1
            self.progress.phase = (
                TaskPhase.COMPLETE
                if self.progress.cycles_completed >= self.binding.profile.cycle_goal
                else TaskPhase.FIND_TREE
            )

    def _progress_snapshot(self) -> TaskProgressSnapshot:
        phase = (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            else (
                self.progress.blocked_from_phase
                if self.progress.phase is TaskPhase.BLOCKED
                and self.progress.blocked_from_phase is not None
                else self.progress.phase
            )
        )
        if phase is TaskPhase.NAVIGATE_TO_BANK:
            route = self.definition.route_to_bank
            return TaskProgressSnapshot(
                route.route_id,
                self.progress.route_index,
                len(route.steps),
            )
        if phase is TaskPhase.NAVIGATE_TO_TREES:
            route = self.definition.route_to_resource
            return TaskProgressSnapshot(
                route.route_id,
                self.progress.route_index,
                len(route.steps),
            )
        return TaskProgressSnapshot(
            "cycles",
            self.progress.cycles_completed,
            self.binding.profile.cycle_goal,
        )

    @staticmethod
    def _target_evidence(
        observation: Observation,
        target: NearbyObject,
        action: str | None,
    ) -> TargetEvidence:
        return TargetEvidence(
            key=target.key,
            name=target.name,
            object_id=target.object_id,
            action=action,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=target.geometry.screen_point,
            bounds=target.geometry.screen_bounds,
        )

    def _object_decision_evidence(
        self,
        observation: Observation,
        *,
        action: str,
        selected: NearbyObject | None = None,
        eligible: tuple[NearbyObject, ...] = (),
        rejected: tuple[tuple[NearbyObject, tuple[str, ...]], ...] = (),
        candidate_actions: dict[str, str] | None = None,
    ) -> DecisionEvidence:
        actions = candidate_actions or {}

        def target_evidence(target: NearbyObject) -> TargetEvidence:
            return self._target_evidence(
                observation,
                target,
                actions.get(target.key, action),
            )

        eligible_evidence = tuple(target_evidence(target) for target in eligible)
        selected_evidence = target_evidence(selected) if selected is not None else None
        rejected_evidence = tuple(
            RejectedCandidateEvidence(target_evidence(target), codes)
            for target, codes in sorted(rejected, key=lambda item: item[0].key)
        )
        return DecisionEvidence(
            selected=selected_evidence,
            eligible=eligible_evidence,
            rejected=rejected_evidence,
        )

    @staticmethod
    def _widget_target_evidence(
        observation: Observation,
        key: str,
        target: WidgetTarget,
        action: str,
    ) -> TargetEvidence:
        return TargetEvidence(
            key=key,
            name=target.name,
            object_id=None,
            action=action,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=target.screen_point,
            bounds=target.screen_bounds,
        )

    @staticmethod
    def _dialogue_target_evidence(
        observation: Observation, option: DialogueOption
    ) -> TargetEvidence:
        return TargetEvidence(
            key=f"dialogue:{option.index}",
            name=option.text,
            object_id=option.index,
            action=option.text,
            source_tick=observation.tick,
            geometry_frame_id=observation.geometry_frame_id,
            point=None,
            bounds=None,
        )

    @staticmethod
    def _single_selected_evidence(target: TargetEvidence) -> DecisionEvidence:
        return DecisionEvidence(selected=target, eligible=(target,))

    @staticmethod
    def _single_rejected_evidence(
        target: TargetEvidence, codes: tuple[str, ...]
    ) -> DecisionEvidence:
        return DecisionEvidence(
            rejected=(RejectedCandidateEvidence(target, codes),)
        )

    def _classify_trees(
        self, observation: Observation
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
    ]:
        actionable: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        for target in observation.nearby_objects:
            codes = self._tree_rejection_codes(target)
            if codes:
                rejected.append((target, codes))
            else:
                actionable.append(target)

        eligible: list[NearbyObject] = []
        for target in actionable:
            if self._tree_aim_is_unambiguous(target, actionable):
                eligible.append(target)
            else:
                rejected.append((target, ("aim_point_occluded",)))
        eligible.sort(
            key=lambda target: (
                observation.location.distance_to(target.location),
                target.key,
            )
        )
        return tuple(eligible), tuple(rejected)

    def _classify_route_objects(
        self, observation: Observation, step: FixedRouteStep
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
        dict[str, str],
    ]:
        eligible: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        actions: dict[str, str] = {}
        for target in observation.nearby_objects:
            codes = []
            if target.object_id != step.object_id:
                codes.append("object_id_mismatch")
            if target.name != step.object_name:
                codes.append("name_mismatch")
            if target.location is None:
                codes.append("location_unavailable")
            else:
                if target.location.plane != step.location.plane:
                    codes.append("wrong_plane")
                elif target.location.distance_to(step.location) > step.arrival_radius:
                    codes.append("outside_step_radius")
                if observation.location.distance_to(target.location) > step.arrival_radius:
                    codes.append("outside_interaction_radius")
            route_option = self._route_option(target, step)
            if route_option is None:
                codes.append("action_unavailable")
            codes.extend(self._geometry_rejection_codes(target))
            if codes:
                rejected.append((target, tuple(codes)))
            else:
                eligible.append(target)
                assert route_option is not None
                actions[target.key] = route_option
        eligible.sort(
            key=lambda target: (
                observation.location.distance_to(target.location),
                target.key,
            )
        )
        return tuple(eligible), tuple(rejected), actions

    def _classify_bank_objects(
        self, observation: Observation
    ) -> tuple[
        tuple[NearbyObject, ...],
        tuple[tuple[NearbyObject, tuple[str, ...]], ...],
    ]:
        bank = self.definition.bank
        selector = bank.selector
        eligible: list[NearbyObject] = []
        rejected: list[tuple[NearbyObject, tuple[str, ...]]] = []
        for target in observation.nearby_objects:
            codes = []
            if target.object_id not in selector.object_ids:
                codes.append("object_id_not_supported")
            if target.name != selector.name:
                codes.append("name_mismatch")
            if target.location != bank.anchor:
                codes.append("location_mismatch")
            if not target.supports(selector.action):
                codes.append("action_unavailable")
            codes.extend(self._geometry_rejection_codes(target))
            if (
                target.location is not None
                and observation.location.distance_to(target.location)
                > bank.interaction_radius
            ):
                codes.append("outside_interaction_radius")
            if codes:
                rejected.append((target, tuple(codes)))
            else:
                eligible.append(target)
        eligible.sort(key=lambda target: target.key)
        return tuple(eligible), tuple(rejected)

    @staticmethod
    def _route_option(target: NearbyObject, step: FixedRouteStep) -> str | None:
        if target.supports(step.action):
            return step.action
        return next(
            (option for option in target.actions if option in step.allowed_actions),
            None,
        )

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

    @staticmethod
    def _walk_projection_rejection_codes(
        target: NearbyObject, step: FixedRouteStep
    ) -> tuple[str, ...]:
        codes = []
        if target.key != step.target_key:
            codes.append("key_mismatch")
        if target.object_id != 0:
            codes.append("object_id_mismatch")
        if target.name != step.target_key:
            codes.append("name_mismatch")
        if target.kind != "NAVIGATION_TILE":
            codes.append("kind_mismatch")
        if target.actions != ("Walk here",):
            codes.append("actions_mismatch")
        if target.location != step.location:
            codes.append("location_mismatch")
        if target.scene_x is None or target.scene_y is None:
            codes.append("scene_coordinates_unavailable")
        return tuple(codes)

    def _is_exact_walk_projection(
        self, target: NearbyObject | None, step: FixedRouteStep
    ) -> bool:
        return bool(
            self._walk_projection_identity_matches(target, step)
            and target is not None
            and self._has_geometry(target)
        )

    def _is_actionable_tree(self, target: NearbyObject) -> bool:
        return not self._tree_rejection_codes(target)

    def _tree_rejection_codes(self, target: NearbyObject) -> tuple[str, ...]:
        selector = self.definition.resource.selector
        work_area = self.definition.resource.work_area
        codes = []
        if target.object_id not in selector.object_ids:
            codes.append("object_id_not_supported")
        if target.name != selector.name:
            codes.append("name_mismatch")
        if not target.supports(selector.action):
            codes.append("action_unavailable")
        if target.location is None:
            codes.append("location_unavailable")
        else:
            if target.location.plane != work_area.anchor.plane:
                codes.append("wrong_plane")
            elif target.location.distance_to(work_area.anchor) > work_area.radius:
                codes.append("outside_work_area")
        if target.scene_x is None or target.scene_y is None:
            codes.append("scene_coordinates_unavailable")
        codes.extend(self._geometry_rejection_codes(target))
        return tuple(codes)

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

    @staticmethod
    def _geometry_rejection_codes(target: NearbyObject) -> tuple[str, ...]:
        geometry = target.geometry
        codes = []
        if not geometry.available:
            codes.append("geometry_unavailable")
        if not geometry.on_screen:
            codes.append("off_screen")
        if not geometry.visible:
            codes.append("not_visible")
        if not geometry.actionable:
            codes.append("not_actionable")
        if geometry.screen_point is None:
            codes.append("screen_point_unavailable")
        elif (
            geometry.screen_bounds is not None
            and not geometry.screen_bounds.contains(geometry.screen_point)
        ):
            codes.append("screen_point_outside_bounds")
        return tuple(codes)

    def _block(
        self,
        observation: Observation,
        reason: str,
        *,
        evidence: DecisionEvidence | None = None,
    ) -> Decision:
        self._set_blocked(reason)
        self.progress.pending = None
        return self._wait(observation, reason, evidence=evidence)

    def _set_blocked(self, reason: str) -> None:
        source_phase = (
            self.progress.resume_phase
            if self.progress.phase is TaskPhase.STAIR_DIALOGUE
            and self.progress.resume_phase is not None
            else self.progress.phase
        )
        if source_phase is not TaskPhase.BLOCKED:
            self.progress.blocked_from_phase = source_phase
        self.progress.phase = TaskPhase.BLOCKED
        self.progress.failures.append(reason)

    def _wait(
        self,
        observation: Observation,
        reason: str,
        *,
        evidence: DecisionEvidence | None = None,
    ) -> Decision:
        return Decision(
            self.progress.phase.value,
            reason,
            Action(
                ActionKind.WAIT,
                "Wait",
                observation.tick,
                verification=self.progress.pending,
            ),
            evidence or DecisionEvidence(),
        )
