from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_bot.model import (
    LOG_ITEM_ID,
    ActionKind,
    InventoryItem,
    InventoryObservation,
    NearbyObject,
    Observation,
    PlayerObservation,
    ScreenBounds,
    ScreenPoint,
    TargetGeometry,
    TaskPhase,
    WidgetObservation,
    WidgetTarget,
    WorldPoint,
)
from osrs_bot.task import (
    BANK_ANCHOR,
    BANK_OBJECT_ID,
    ROUTE_STABLE_TICKS,
    ROUTE_TO_BANK,
    ROUTE_TO_TREES,
    TREE_AREA,
    TREE_OBJECT_ID,
    WoodcutBankTask,
)
from osrs_bot.verification import Verifier


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_lumbridge_cycle.json"
SESSION_ID = "golden-session"
POINT = ScreenPoint(400, 300)
BOUNDS = ScreenBounds(380, 280, 40, 40)
GEOMETRY = TargetGeometry(
    available=True,
    on_screen=True,
    visible=True,
    actionable=True,
    canvas_point=ScreenPoint(300, 200),
    screen_point=POINT,
    screen_bounds=BOUNDS,
    visible_area_ratio=1.0,
)


def _point(raw: list[int]) -> WorldPoint:
    return WorldPoint(int(raw[0]), int(raw[1]), int(raw[2]))


def _inventory(logs: int, *, known: bool = True) -> InventoryObservation:
    if not known:
        return InventoryObservation(known=False)
    items = tuple(
        InventoryItem(slot, LOG_ITEM_ID, 1, "Logs") for slot in range(logs)
    )
    return InventoryObservation(
        items=items,
        slot_count=28,
        occupied_slots=logs,
        free_slots=28 - logs,
        known=True,
    )


def _observation(
    location: WorldPoint,
    tick: int,
    *,
    logs: int,
    inventory_known: bool = True,
    objects: tuple[NearbyObject, ...] = (),
    widgets: WidgetObservation | None = None,
) -> Observation:
    timestamp = datetime.now(timezone.utc)
    process_id = 4242
    frame_id = f"golden-frame-{tick}"
    return Observation(
        player=PlayerObservation(),
        location=location,
        plane=location.plane,
        inventory=_inventory(logs, known=inventory_known),
        nearby_objects=objects,
        menus=(),
        widgets=widgets or WidgetObservation(bank_known=True),
        canvas_bounds=ScreenBounds(0, 0, 800, 600),
        game_state="LOGGED_IN",
        timestamp=timestamp,
        tick=tick,
        status="PASS",
        fresh=True,
        cache_wall_clock_fresh=True,
        scene_playable=True,
        session_id=SESSION_ID,
        menu_client_tick=10_000 + tick,
        menu_mouse_screen_point=POINT,
        client_focused=True,
        client_process_id=process_id,
        assembled_at=timestamp,
        frame_id=frame_id,
        geometry_frame_id=frame_id,
        source_coherent=True,
        menu_fresh=True,
        menu_source_tick=tick,
        menu_timestamp=timestamp,
        menu_session_id=SESSION_ID,
        menu_process_id=process_id,
    )


def _tree(raw: dict[str, Any]) -> NearbyObject:
    return NearbyObject(
        key="golden:tree:1276",
        object_id=int(raw["objectId"]),
        name=str(raw["name"]),
        kind="GAME_OBJECT",
        actions=(str(raw["action"]),),
        location=TREE_AREA,
        distance=0,
        geometry=GEOMETRY,
        scene_x=50,
        scene_y=50,
        resource_candidate=True,
    )


def _route_target(raw: dict[str, Any]) -> NearbyObject:
    location = _point(raw["location"])
    if raw["kind"] == "walk":
        key = f"route:{raw['id']}"
        return NearbyObject(
            key=key,
            object_id=0,
            name=key,
            kind="NAVIGATION_TILE",
            actions=("Walk here",),
            location=location,
            distance=8,
            geometry=GEOMETRY,
            scene_x=50,
            scene_y=50,
            route_candidate=True,
        )
    return NearbyObject(
        key=f"golden:{raw['id']}",
        object_id=int(raw["objectId"]),
        name=str(raw["name"]),
        kind="GAME_OBJECT",
        actions=(str(raw["action"]),),
        location=location,
        distance=0,
        geometry=GEOMETRY,
        scene_x=50,
        scene_y=50,
        route_candidate=True,
    )


def _route_contract(step: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": step.step_id,
        "kind": "walk" if step.is_walk else "object",
        "location": [step.location.x, step.location.y, step.location.plane],
        "arrivalRadius": step.arrival_radius,
    }
    if not step.is_walk:
        record.update(
            {
                "objectId": step.object_id,
                "name": step.object_name,
                "action": step.action,
                "expectedPlane": step.expected_plane,
            }
        )
    return record


class GoldenLumbridgeCycleReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.task = WoodcutBankTask()
        self.verifier = Verifier()
        self.tick = 100

    def _apply_verified(self, decision: Any, after: Observation) -> str:
        specification = decision.action.verification
        self.assertIsNotNone(specification)
        result = self.verifier.evaluate(specification, after)
        self.assertTrue(result.passed, result.reason)
        self.task.apply_verification(True, result.reason)
        return result.reason

    def _replay_route(
        self,
        records: list[dict[str, Any]],
        *,
        logs: int,
        inventory_known: bool,
    ) -> WorldPoint:
        current = TREE_AREA if self.task.progress.phase is TaskPhase.NAVIGATE_TO_BANK else BANK_ANCHOR
        for index, raw in enumerate(records):
            self.assertEqual(index, self.task.progress.route_index)
            location = _point(raw["location"])
            if raw.get("arrivalOnly"):
                self.tick += 1
                decision = self.task.decide(
                    _observation(
                        location,
                        self.tick,
                        logs=logs,
                        inventory_known=inventory_known,
                    )
                )
                self.assertEqual(ActionKind.WAIT, decision.action.kind)
                current = location
                continue

            target = _route_target(raw)
            if raw["kind"] == "walk":
                radius = int(raw["arrivalRadius"])
                approach = WorldPoint(
                    location.x + radius + 2,
                    location.y,
                    location.plane,
                )
                self.tick += 1
                before = _observation(
                    approach,
                    self.tick,
                    logs=logs,
                    inventory_known=inventory_known,
                    objects=(target,),
                )
                decision = self.task.decide(before)
                self.assertEqual(ActionKind.WALK, decision.action.kind)
                self.assertEqual(f"route:{raw['id']}", decision.action.target_key)

                self.tick += 1
                arrived = _observation(
                    location,
                    self.tick,
                    logs=logs,
                    inventory_known=inventory_known,
                )
                self.assertEqual("arrived", self._apply_verified(decision, arrived))
                settling = self.task.decide(arrived)
                self.assertEqual(ActionKind.WAIT, settling.action.kind)
                self.assertEqual(index, self.task.progress.route_index)

                self.tick += ROUTE_STABLE_TICKS
                settled = self.task.decide(
                    _observation(
                        location,
                        self.tick,
                        logs=logs,
                        inventory_known=inventory_known,
                    )
                )
                self.assertEqual(ActionKind.WAIT, settled.action.kind)
            else:
                self.tick += 1
                before = _observation(
                    location,
                    self.tick,
                    logs=logs,
                    inventory_known=inventory_known,
                    objects=(target,),
                )
                decision = self.task.decide(before)
                self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
                self.assertEqual(raw["action"], decision.action.option)

                self.tick += 1
                after_location = WorldPoint(
                    location.x, location.y, int(raw["expectedPlane"])
                )
                after = _observation(
                    after_location,
                    self.tick,
                    logs=logs,
                    inventory_known=inventory_known,
                )
                self.assertEqual("plane_changed", self._apply_verified(decision, after))
                current = after_location
                continue
            current = location
        return current

    def test_fixture_matches_the_frozen_task_and_route_contract(self) -> None:
        task = self.fixture["task"]
        self.assertEqual(
            [TREE_AREA.x, TREE_AREA.y, TREE_AREA.plane], task["treeArea"]
        )
        self.assertEqual(TREE_OBJECT_ID, task["tree"]["objectId"])
        self.assertEqual(LOG_ITEM_ID, task["tree"]["producedItemId"])
        self.assertEqual(
            [BANK_ANCHOR.x, BANK_ANCHOR.y, BANK_ANCHOR.plane],
            task["bankAnchor"],
        )
        self.assertEqual(BANK_OBJECT_ID, task["bank"]["objectId"])

        outbound = [_route_contract(step) for step in ROUTE_TO_BANK]
        inbound = [_route_contract(step) for step in ROUTE_TO_TREES]
        expected_outbound = [
            {key: value for key, value in record.items() if key != "arrivalOnly"}
            for record in task["routeToBank"]
        ]
        expected_inbound = [
            {key: value for key, value in record.items() if key != "arrivalOnly"}
            for record in task["routeToTrees"]
        ]
        self.assertEqual(expected_outbound, outbound)
        self.assertEqual(expected_inbound, inbound)

    def test_replays_complete_woodcut_bank_return_fsm(self) -> None:
        task_data = self.fixture["task"]
        tree = _tree(task_data["tree"])
        chop_actions = 0

        for logs in range(28):
            self.tick += 1
            before = _observation(
                TREE_AREA, self.tick, logs=logs, objects=(tree,)
            )
            selected = self.task.decide(before)
            self.assertEqual(ActionKind.WAIT, selected.action.kind)
            self.assertEqual(TaskPhase.CHOP, self.task.progress.phase)

            self.tick += 1
            action_observation = _observation(
                TREE_AREA, self.tick, logs=logs, objects=(tree,)
            )
            decision = self.task.decide(action_observation)
            self.assertEqual(ActionKind.INTERACT_OBJECT, decision.action.kind)
            chop_actions += 1

            self.tick += 1
            gained = _observation(TREE_AREA, self.tick, logs=logs + 1)
            self.assertEqual("log_gained", self._apply_verified(decision, gained))

        self.assertEqual(
            self.fixture["expected"]["chopActions"], chop_actions
        )
        self.tick += 1
        full = _observation(TREE_AREA, self.tick, logs=28)
        route_selected = self.task.decide(full)
        self.assertEqual(ActionKind.WAIT, route_selected.action.kind)
        self.assertEqual(TaskPhase.NAVIGATE_TO_BANK, self.task.progress.phase)

        bank_location = self._replay_route(
            task_data["routeToBank"], logs=28, inventory_known=True
        )
        self.assertEqual(BANK_ANCHOR, bank_location)
        self.assertEqual(TaskPhase.OPEN_BANK, self.task.progress.phase)

        bank_object = NearbyObject(
            key="golden:bank-booth",
            object_id=int(task_data["bank"]["objectId"]),
            name=str(task_data["bank"]["name"]),
            kind="GAME_OBJECT",
            actions=(str(task_data["bank"]["action"]),),
            location=BANK_ANCHOR,
            distance=0,
            geometry=GEOMETRY,
            scene_x=50,
            scene_y=50,
            route_candidate=True,
            service_candidate=True,
        )
        closed = WidgetObservation(bank_known=True)
        self.tick += 1
        open_decision = self.task.decide(
            _observation(
                BANK_ANCHOR,
                self.tick,
                logs=28,
                objects=(bank_object,),
                widgets=closed,
            )
        )
        self.assertEqual(ActionKind.INTERACT_OBJECT, open_decision.action.kind)

        opened = WidgetObservation(
            bank_known=True,
            bank_open=True,
            bank_readable=True,
            deposit_inventory=WidgetTarget(
                "deposit_inventory", True, POINT, BOUNDS
            ),
            close_bank=WidgetTarget("close_bank", True),
            keyboard_close_possible=True,
        )
        self.tick += 1
        opened_observation = _observation(
            BANK_ANCHOR, self.tick, logs=28, widgets=opened
        )
        self.assertEqual(
            "bank_open", self._apply_verified(open_decision, opened_observation)
        )

        deposit_decision = self.task.decide(opened_observation)
        self.assertEqual(ActionKind.CLICK_WIDGET, deposit_decision.action.kind)
        self.tick += 1
        deposited = _observation(
            BANK_ANCHOR, self.tick, logs=0, widgets=opened
        )
        self.assertEqual(
            "logs_deposited",
            self._apply_verified(deposit_decision, deposited),
        )

        self.tick += 1
        unknown_after_deposit = _observation(
            BANK_ANCHOR,
            self.tick,
            logs=0,
            inventory_known=False,
            widgets=opened,
        )
        close_decision = self.task.decide(unknown_after_deposit)
        self.assertEqual(ActionKind.PRESS_KEY, close_decision.action.kind)
        self.assertEqual("escape", close_decision.action.key)

        self.tick += 1
        bank_closed = _observation(
            BANK_ANCHOR,
            self.tick,
            logs=0,
            inventory_known=False,
            widgets=WidgetObservation(bank_known=True, bank_open=False),
        )
        self.assertEqual(
            "bank_closed", self._apply_verified(close_decision, bank_closed)
        )
        self.assertEqual(TaskPhase.NAVIGATE_TO_TREES, self.task.progress.phase)

        final_location = self._replay_route(
            task_data["routeToTrees"], logs=0, inventory_known=False
        )
        expected = self.fixture["expected"]
        self.assertEqual(_point(expected["finalLocation"]), final_location)
        self.assertEqual(TaskPhase(expected["terminalPhase"]), self.task.progress.phase)
        self.assertEqual(expected["cyclesCompleted"], self.task.progress.cycles_completed)
        self.assertEqual([], self.task.progress.failures)


if __name__ == "__main__":
    unittest.main()
