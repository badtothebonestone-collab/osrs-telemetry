import json
import io
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


VIEWER_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(VIEWER_DIR))
sys.path.insert(0, str(TESTS_DIR))

import live_core_daemon as daemon
import live_context_format
import live_target_processor as live
import test_live_target_processor as fixtures
import diagnose_brain_progress
from analyzers import resource_return_analyzer
from analyzers.live_state import BankOperationContext, BankUiContext, PathingContext, ResourceReturnContext, ServiceContext, TargetContext


def make_args(session: Path, *extra: str):
    return daemon.parse_args(["--session", str(session), "--context-port", "0", *extra])


def synthetic_snapshot(session: Path, tick: int = 1) -> dict:
    tree = fixtures.raw_scene_object(
        10820,
        3201,
        3201,
        140,
        120,
        name="Oak tree",
        object_key=f"oak-{tick}",
    )
    return fixtures.snapshot_response_from_lines(fixtures.compact_packet_lines(session, {tick: [tree]}))


def synthetic_tree_objects(count: int, tick: int = 1) -> list[dict]:
    return [
        fixtures.raw_scene_object(
            10820,
            3201 + index,
            3201,
            140 + index * 8,
            120 + index * 4,
            name="Oak tree",
            object_key=f"oak-{tick}-{index}",
        )
        for index in range(count)
    ]


def snapshot_with_logs(session: Path, tick: int, slots: list[int], *, omit_inventory: bool = False, objects: list[dict] | None = None) -> dict:
    if objects is None:
        objects = [
            fixtures.raw_scene_object(
                10820,
                3201,
                3201,
                140,
                120,
                name="Oak tree",
                object_key=f"oak-{tick}",
            )
        ]
    response = fixtures.snapshot_response_from_lines(
        fixtures.compact_packet_lines(session, {tick: objects}),
        omit_needs=["inventory"] if omit_inventory else [],
    )
    if not omit_inventory:
        items = [{"slot": slot, "itemId": 1511, "quantity": 1} for slot in slots]
        signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in items)
        response["payloads"]["inventory"] = {
            "inventory": {
                "known": True,
                "freeSlots": 28 - len(items),
                "filledSlots": len(items),
                "itemCount": len(items),
                "inventorySlotCount": 28,
                "slotCount": 28,
                "signature": signature,
                "inventorySignature": signature,
                "items": items,
            },
            "equipment": {"known": True, "items": []},
        }
    return response


def snapshot_request_side_effect(responses: list[dict]):
    queue = [(response, len(json.dumps(response))) for response in responses]

    def request():
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    return request


class LiveCoreDaemonTest(unittest.TestCase):
    def test_daily_defaults_use_compact_packets_and_no_debug_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session)

        self.assertEqual(args.input_source, "compact-packets")
        self.assertFalse(args.write_debug_live_files)
        self.assertEqual(args.overlay_mode, "intent")
        self.assertEqual(args.task_policy, "woodcutting_bank")

    def test_status_exposes_input_geometry_from_snapshot_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session, 1)
            response["payloads"]["baseline"]["inputGeometry"] = {
                "geometryAvailable": True,
                "canvasScreenX": 1000,
                "canvasScreenY": 2000,
                "canvasWidth": 800,
                "canvasHeight": 600,
                "sourceCanvasWidth": 400,
                "sourceCanvasHeight": 300,
                "displayScaleX": 2.0,
                "displayScaleY": 2.0,
                "sourceTick": 1,
            }
            args = make_args(session, "--input-source", "plugin-snapshot")
            core = daemon.LiveCoreDaemon(session, args)

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core.poll_once()
                status = core.state.status()

        self.assertTrue(status["inputGeometryAvailable"])
        self.assertEqual(status["canvasScreenOrigin"], {"x": 1000, "y": 2000})
        self.assertEqual(status["canvasSize"], {"width": 800, "height": 600})
        self.assertEqual(status["sourceCanvasSize"], {"width": 400, "height": 300})
        self.assertEqual(status["displayScale"], {"x": 2.0, "y": 2.0})

    def test_task_policy_argument_is_preserved_for_brain_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_firemake",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

        decision = core.state.brain_decision
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(decision["genericTaskState"]["processTypeNeeded"], "firemaking")
        self.assertIn("processInventoryContext", decision)
        self.assertTrue(decision["processInventoryContext"]["processRequired"])
        self.assertFalse(decision["processInventoryContext"].get("serviceTypeNeeded"))
        self.assertEqual(core.state.source_status["brainTaskPolicy"], "woodcutting_firemake")

    def test_process_inventory_status_domains_do_not_require_tree_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)), objects=[])
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--daily-mode",
                "snapshot-no-files",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_firemake",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

        status = core.state.status()
        decision = core.state.brain_decision
        self.assertEqual(decision["genericTaskState"]["phase"], "inventory_full")
        self.assertEqual(decision["genericTaskState"]["activeIntent"], "process_inventory")
        self.assertEqual(status["processInventoryNeeded"], True)
        self.assertEqual(status["processTypeNeeded"], "firemaking")
        self.assertEqual(status["requiredContextDomains"], ["inventory", "process_inventory"])
        self.assertEqual(status["missingRequiredContextDomains"], [])
        self.assertIn("target.candidates", status["optionalMissingContextDomains"])

    def test_startup_preset_resolves_to_runtime_control_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--preset", "woodcut_firemake")
            control = daemon.runtime_control_from_args(args)

        self.assertEqual(args.preset, "woodcut_firemake")
        self.assertEqual(args.brain_task, "woodcutting")
        self.assertEqual(args.task_policy, "woodcutting_firemake")
        self.assertEqual(args.goal_count, 5)
        self.assertEqual(args.overlay_mode, "intent")
        self.assertEqual(args.overlay_backup_candidates, 2)
        self.assertEqual(control.activeMissionPreset, "woodcut_firemake")
        self.assertEqual(control.taskPolicy, "woodcutting_firemake")
        self.assertEqual(control.goalCount, 5)

    def test_startup_preset_goal_count_can_be_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--preset", "woodcut_bank", "--goal-count", "10")
            control = daemon.runtime_control_from_args(args)

        self.assertEqual(args.task_policy, "woodcutting_bank")
        self.assertEqual(args.goal_count, 10)
        self.assertEqual(control.goalCount, 10)

    def test_startup_preset_task_policy_can_be_overridden_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--preset", "woodcut_bank", "--task-policy", "woodcutting_firemake")
            core = daemon.LiveCoreDaemon(session, args)

        self.assertEqual(args.task_policy, "woodcutting_firemake")
        self.assertIn("task policy overridden by explicit --task-policy", args.startup_warnings)
        self.assertIn("task policy overridden by explicit --task-policy", core.state.warnings)
        self.assertIn("task policy overridden by explicit --task-policy", core.runtime_control.warnings)
        self.assertEqual(core.runtime_control.activeMissionPreset, "woodcut_bank")
        self.assertEqual(core.runtime_control.taskPolicy, "woodcutting_firemake")

    def test_startup_unknown_preset_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            with self.assertRaises(SystemExit):
                make_args(session, "--preset", "not_a_preset")

    def test_bank_policy_service_candidate_adds_navigation_intent_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank",
                best_service_candidate={
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "id": 10355,
                    "distanceTiles": 4,
                    "navigation": {"directReachability": "reachable"},
                },
                candidate_count=1,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    core = daemon.LiveCoreDaemon(session, args)
                    core.poll_once()

        nav = core.state.brain_decision["navigationIntentContext"]
        self.assertTrue(nav["navigationNeeded"])
        self.assertEqual(nav["navigationReason"], "service_target_available")
        self.assertEqual(nav["targetKind"], "service")
        self.assertEqual(nav["destinationTarget"]["classId"], "bank_booth")
        self.assertEqual(core.state.source_status["navigationIntentReason"], "service_target_available")

    def test_status_exposes_pathing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank",
                best_service_candidate={
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "id": 10355,
                    "worldX": 3208,
                    "worldY": 3219,
                    "plane": 0,
                    "sceneX": 20,
                    "sceneY": 21,
                    "distanceTiles": 4,
                    "navigation": {"directReachability": "reachable"},
                },
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=True,
                local_reachability="reachable",
                path_length_tiles=4,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                next_waypoint_tile={"worldX": 3201, "worldY": 3200, "plane": 0},
                path_intent_key="woodcutting:inventory_full:needs_service|objectKey:bank-booth-1|3208:3219:0|sceneObject|bank_booth",
                destination_target_key="objectKey:bank-booth-1",
                path_intent_retained=True,
                path_stable_for_ticks=3,
                movement_state="moving",
                retention_reason="player_moving_same_destination",
                switch_reason=None,
                pathing_millis=0.1,
                path_nodes_expanded=5,
                collision_window_available=True,
                collision_window_fresh=True,
                collision_window_radius=24,
                collision_window_center_world={"worldX": 3200, "worldY": 3200, "plane": 0},
                collision_window_plane=0,
                collision_window_age_ticks=0,
                destination_inside_collision_window=True,
                destination_plane_matches=True,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        status = core.state.status()
        brain = core.state.brain_decision
        self.assertIn("pathingContext", brain)
        self.assertTrue(brain["pathingContext"]["pathingNeeded"])
        self.assertTrue(status["pathingNeeded"])
        self.assertEqual(status["pathingLocalReachability"], "reachable")
        self.assertEqual(status["pathingPathLengthTiles"], 4)
        self.assertEqual(status["pathingDestinationTile"]["worldX"], 3208)
        self.assertEqual(status["pathingNextWaypointTile"]["worldX"], 3201)
        self.assertTrue(status["pathIntentRetained"])
        self.assertEqual(status["pathStableForTicks"], 3)
        self.assertEqual(status["pathMovementState"], "moving")
        self.assertEqual(status["pathRetentionReason"], "player_moving_same_destination")
        self.assertEqual(status["pathDestinationTargetKey"], "objectKey:bank-booth-1")
        self.assertTrue(status["pathingCollisionWindowAvailable"])
        self.assertTrue(status["pathingDestinationInsideCollisionWindow"])
        self.assertTrue(status["pathingDestinationPlaneMatches"])

    def test_service_ready_pathing_updates_brain_phase_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
                "sceneX": 20,
                "sceneY": 21,
                "distanceTiles": 1,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                final_approach_tile={"worldX": 3207, "worldY": 3219, "plane": 0},
                path_target_tile={"worldX": 3207, "worldY": 3219, "plane": 0},
                local_reachability="reachable",
                reason="arrived_at_service",
                arrived_at_final_approach=True,
                arrived_near_destination=True,
                distance_to_final_approach=0,
                distance_to_destination=1,
                distance_to_path_target=0,
                arrived_stable_for_ticks=2,
                arrival_reason="arrived_at_final_approach",
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=1,
                path_completed=True,
                path_completion_reason="arrived_at_service",
                retained_path_after_arrival=True,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        status = core.state.status()
        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "service_available")
        self.assertEqual(generic["activeIntent"], "service_available")
        self.assertTrue(status["serviceReady"])
        self.assertEqual(status["serviceReadyReason"], "arrived_at_service")
        self.assertTrue(status["pathCompleted"])
        self.assertFalse(status["pathingNeeded"])
        self.assertEqual(status["pathCompletionReason"], "arrived_at_service")

    def test_readable_bank_ui_updates_brain_phase_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            response["payloads"]["bank_ui"] = {
                "bankOpen": True,
                "bankPinOpen": False,
                "bankRootVisible": True,
                "bankContainerVisible": True,
                "bankInventoryVisible": True,
                "depositInventoryButtonVisible": True,
                "closeButtonVisible": True,
                "inventorySummary": {"freeSlots": 0, "occupiedSlots": 28, "matchingResourceCount": 28},
                "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        status = core.state.status()
        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "service_open")
        self.assertEqual(generic["activeIntent"], "bank_operation_pending")
        self.assertIn("bankUiContext", core.state.brain_decision)
        self.assertIn("bankOperationContext", core.state.brain_decision)
        self.assertTrue(core.state.brain_decision["bankOperationContext"]["operationNeeded"])
        self.assertEqual(core.state.brain_decision["bankOperationContext"]["operationType"], "deposit_inventory")
        self.assertTrue(status["bankOpen"])
        self.assertTrue(status["bankReadable"])
        self.assertFalse(status["bankPinOpen"])
        self.assertTrue(status["bankOperationNeeded"])
        self.assertEqual(status["bankOperationType"], "deposit_inventory")
        self.assertEqual(status["bankResourceItemQuantity"], 28)
        self.assertTrue(status["closeButtonVisible"])
        self.assertEqual(status["bankOccupiedSlots"], 12)
        self.assertEqual(status["bankUniqueItemCount"], 2)

    def test_readable_bank_ui_with_no_logs_defers_tree_target_until_bank_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            response["payloads"]["inventory"]["inventory"] = {
                "known": True,
                "freeSlots": 15,
                "filledSlots": 13,
                "itemCount": 1300,
                "inventoryFull": False,
                "inventorySlotCount": 28,
                "slotCount": 28,
                "signature": coin_signature,
                "inventorySignature": coin_signature,
                "items": coin_items,
            }
            response["payloads"]["bank_ui"] = {
                "bankOpen": True,
                "bankPinOpen": False,
                "bankRootVisible": True,
                "bankContainerVisible": True,
                "bankInventoryVisible": True,
                "depositInventoryButtonVisible": True,
                "closeButtonVisible": True,
                "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
                "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        status = core.state.status()
        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "waiting_for_world_view")
        self.assertEqual(generic["activeIntent"], "close_service_context")
        self.assertIsNone(generic.get("activeIntentTarget"))
        self.assertTrue(core.state.brain_decision["bankOperationContext"]["bankingComplete"])
        self.assertEqual(core.state.brain_decision["bankOperationContext"]["resourceItemQuantity"], 0)
        self.assertIn("returnToResourceContext", core.state.brain_decision)
        self.assertTrue(core.state.brain_decision["returnToResourceContext"]["returnNeeded"])
        self.assertIn("postBankReacquisitionContext", core.state.brain_decision)
        self.assertEqual(core.state.brain_decision["postBankReacquisitionContext"]["reason"], "bank_ui_still_open")
        self.assertFalse(core.state.brain_decision["postBankReacquisitionContext"]["resourceTargetReacquisitionAllowed"])
        self.assertIn("closeBankContext", core.state.brain_decision)
        self.assertTrue(core.state.brain_decision["closeBankContext"]["closeBankNeeded"])
        self.assertTrue(core.state.brain_decision["closeBankContext"]["closeBankReady"])
        self.assertEqual(core.state.brain_decision["closeBankContext"]["reason"], "close_button_available")
        self.assertFalse(status["bankOperationNeeded"])
        self.assertTrue(status["bankingComplete"])
        self.assertEqual(status["bankOperationCompletionReason"], "no_resource_items_held")
        self.assertTrue(status["returnToResourceNeeded"])
        self.assertTrue(status["postBankReacquisitionNeeded"])
        self.assertTrue(status["postBankUiStillOpen"])
        self.assertFalse(status["postBankResourceTargetReacquisitionAllowed"])
        self.assertTrue(status["closeBankNeeded"])
        self.assertTrue(status["closeBankReady"])
        self.assertEqual(status["closeBankReason"], "close_button_available")

    def test_readable_bank_ui_with_no_logs_and_no_tree_waits_for_world_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [], objects=[])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            response["payloads"]["inventory"]["inventory"] = {
                "known": True,
                "freeSlots": 15,
                "filledSlots": 13,
                "itemCount": 1300,
                "inventoryFull": False,
                "inventorySlotCount": 28,
                "slotCount": 28,
                "signature": coin_signature,
                "inventorySignature": coin_signature,
                "items": coin_items,
            }
            response["payloads"]["bank_ui"] = {
                "bankOpen": True,
                "bankPinOpen": False,
                "bankRootVisible": True,
                "bankContainerVisible": True,
                "bankInventoryVisible": True,
                "depositInventoryButtonVisible": True,
                "closeButtonVisible": True,
                "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
                "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "waiting_for_world_view")
        self.assertEqual(generic["activeIntent"], "close_service_context")
        self.assertIsNone(generic.get("activeIntentTarget"))
        self.assertTrue(core.state.brain_decision["returnToResourceContext"]["returnNeeded"])
        self.assertIn("postBankReacquisitionContext", core.state.brain_decision)
        self.assertTrue(core.state.brain_decision["postBankReacquisitionContext"]["postBankReacquisitionNeeded"])
        self.assertEqual(core.state.brain_decision["postBankReacquisitionContext"]["reason"], "bank_ui_still_open")
        self.assertFalse(core.state.brain_decision["postBankReacquisitionContext"]["resourceTargetReacquisitionAllowed"])
        self.assertTrue(core.state.brain_decision["closeBankContext"]["closeBankNeeded"])
        self.assertTrue(core.state.brain_decision["closeBankContext"]["closeBankReady"])
        self.assertEqual(core.state.brain_decision["closeBankContext"]["reason"], "close_button_available")
        self.assertNotIn("target.candidates", core.state.brain_decision.get("missingRequiredContextDomains", []))
        self.assertFalse(core.state.source_status["targetCandidatesRequired"])

    def test_bank_closed_after_banking_complete_returns_to_visible_tree_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            open_response = snapshot_with_logs(session, 1, [], objects=[])
            west_tree = fixtures.raw_scene_object(
                10820,
                3197,
                3248,
                140,
                120,
                name="Oak tree",
                object_key="oak-return-area-2",
            )
            closed_response = snapshot_with_logs(session, 2, [], objects=[west_tree])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            for response, bank_open in ((open_response, True), (closed_response, False)):
                response["payloads"]["inventory"]["inventory"] = {
                    "known": True,
                    "freeSlots": 15,
                    "filledSlots": 13,
                    "itemCount": 1300,
                    "inventoryFull": False,
                    "inventorySlotCount": 28,
                    "slotCount": 28,
                    "signature": coin_signature,
                    "inventorySignature": coin_signature,
                    "items": coin_items,
                }
                response["payloads"]["bank_ui"] = {
                    "bankOpen": bank_open,
                    "bankPinOpen": False,
                    "bankRootVisible": bank_open,
                    "bankContainerVisible": bank_open,
                    "bankInventoryVisible": bank_open,
                    "depositInventoryButtonVisible": bank_open,
                    "closeButtonVisible": bank_open,
                    "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
                    "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]} if bank_open else {},
                }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            no_service = ServiceContext(service_required=False, source_tick=2)
            no_pathing = PathingContext(pathing_needed=False, source_tick=2)
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect([open_response, open_response, closed_response, closed_response])):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", side_effect=[service, no_service]):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=[pathing, no_pathing, no_pathing, no_pathing, no_pathing]):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()
                        core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "target_selected")
        self.assertEqual(generic["activeIntent"], "select_target")
        self.assertEqual(generic["activeIntentTarget"]["classId"], "tree")
        self.assertEqual(core.state.brain_decision["postBankReacquisitionContext"]["reason"], "resource_target_visible")
        self.assertTrue(core.state.brain_decision["postBankReacquisitionContext"]["resourceTargetReacquisitionAllowed"])
        self.assertFalse(core.state.brain_decision["closeBankContext"]["closeBankNeeded"])

    def test_reacquired_tree_target_wins_over_stale_return_route_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            open_response = snapshot_with_logs(session, 1, [], objects=[])
            west_tree = fixtures.raw_scene_object(
                10820,
                3197,
                3248,
                140,
                120,
                name="Oak tree",
                object_key="oak-return-area-stale-route",
            )
            closed_response = snapshot_with_logs(session, 2, [], objects=[west_tree])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            for response, bank_open in ((open_response, True), (closed_response, False)):
                response["payloads"]["inventory"]["inventory"] = {
                    "known": True,
                    "freeSlots": 15,
                    "filledSlots": 13,
                    "itemCount": 1300,
                    "inventoryFull": False,
                    "inventorySlotCount": 28,
                    "slotCount": 28,
                    "signature": coin_signature,
                    "inventorySignature": coin_signature,
                    "items": coin_items,
                }
                response["payloads"]["bank_ui"] = {
                    "bankOpen": bank_open,
                    "bankPinOpen": False,
                    "bankRootVisible": bank_open,
                    "bankContainerVisible": bank_open,
                    "bankInventoryVisible": bank_open,
                    "depositInventoryButtonVisible": bank_open,
                    "closeButtonVisible": bank_open,
                    "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
                    "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]} if bank_open else {},
                }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            stale_return = ResourceReturnContext(
                return_destination_needed=True,
                return_destination_available=True,
                return_destination_tile={"worldX": 3197, "worldY": 3248, "plane": 0},
                return_destination_source="profile_anchor",
                resource_target_currently_visible=True,
                destination_target={"targetType": "tile", "targetName": "West trees", "worldX": 3197, "worldY": 3248, "plane": 0},
                reason="resource_target_visible",
                banking_complete=True,
                bank_open=False,
                source_tick=2,
            )
            stale_return_route = {
                "schema": "return_route_context.v1",
                "routeAvailable": True,
                "returnRouteId": "lumbridge_bank_return",
                "currentNavigationTarget": {"targetType": "tile", "targetName": "Return waypoint", "worldX": 3199, "worldY": 3246, "plane": 0},
                "returnActionReady": True,
                "state": "return_route_ready",
            }
            no_service = ServiceContext(service_required=False, source_tick=2)
            no_pathing = PathingContext(pathing_needed=False, source_tick=2)
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect([open_response, open_response, closed_response, closed_response])):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", side_effect=[service, no_service]):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=[pathing, no_pathing, no_pathing, no_pathing, no_pathing]):
                        with mock.patch.object(daemon.resource_return_analyzer, "analyze_resource_return_context", return_value=stale_return):
                            with mock.patch.object(daemon.service_route_core, "build_return_route_context", return_value=stale_return_route):
                                core = daemon.LiveCoreDaemon(session, args)
                                core.poll_once()
                                core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "target_selected")
        self.assertEqual(generic["activeIntent"], "select_target")
        self.assertEqual(generic["activeIntentTarget"]["classId"], "tree")
        self.assertEqual(core.state.brain_decision["phase"], "target_selected")

    def test_resource_target_cycle_stage_clears_stale_return_route_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            tree = fixtures.raw_scene_object(
                10820,
                3214,
                3232,
                160,
                135,
                name="Oak tree",
                object_key="oak-post-service-live-target",
            )
            response = snapshot_with_logs(session, 3, [0, 1], objects=[tree])
            response["payloads"]["inventory"]["inventory"].update(
                {
                    "freeSlots": 13,
                    "filledSlots": 15,
                    "inventoryFull": False,
                    "resourceCounts": {"woodcutting_logs": {"count": 2}},
                }
            )
            response["payloads"]["bank_ui"] = {
                "bankOpen": False,
                "bankPinOpen": False,
                "bankRootVisible": False,
                "bankContainerVisible": False,
                "bankInventoryVisible": False,
                "depositInventoryButtonVisible": False,
                "closeButtonVisible": False,
                "inventorySummary": {"freeSlots": 13, "occupiedSlots": 15, "matchingResourceCount": 2},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            stale_return = ResourceReturnContext(
                return_destination_needed=True,
                return_destination_available=True,
                return_destination_tile={"worldX": 3196, "worldY": 3248, "plane": 0},
                return_destination_source="profile_anchor",
                resource_target_currently_visible=True,
                destination_target={"targetType": "tile", "targetName": "Profile anchor", "worldX": 3196, "worldY": 3248, "plane": 0},
                reason="using_profile_resource_anchor",
                banking_complete=True,
                bank_open=False,
                source_tick=3,
            )
            stale_return_route = {
                "schema": "return_route_context.v1",
                "routeAvailable": True,
                "returnRouteId": "lumbridge_bank_return",
                "currentNavigationTarget": {"targetType": "tile", "targetName": "Return waypoint", "worldX": 3199, "worldY": 3246, "plane": 0},
                "returnActionReady": True,
                "state": "return_route_ready",
            }
            bank_complete = BankOperationContext(
                operation_needed=False,
                operation_type="none",
                resource_item_quantity=0,
                inventory_free_slots=13,
                inventory_full=False,
                banking_complete=True,
                completion_reason="no_resource_items_held",
                source_tick=3,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=ServiceContext(service_required=False, source_tick=3)):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=PathingContext(pathing_needed=False, source_tick=3)):
                        with mock.patch.object(daemon.bank_operation_analyzer, "analyze_bank_operation_context", return_value=bank_complete):
                            with mock.patch.object(daemon.resource_return_analyzer, "analyze_resource_return_context", return_value=stale_return):
                                with mock.patch.object(daemon.service_route_core, "build_return_route_context", return_value=stale_return_route):
                                    core = daemon.LiveCoreDaemon(session, args)
                                    core.state.cycle_history.update(
                                        {
                                            "tick": 2,
                                            "cycleStage": "resource_target_selected",
                                            "phase": "target_selected",
                                            "activeIntent": "select_target",
                                            "resourceTargetAvailable": True,
                                            "bankingComplete": True,
                                        }
                                    )
                                    core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "target_selected")
        self.assertEqual(generic["activeIntent"], "select_target")
        self.assertEqual(generic["activeIntentTarget"]["classId"], "tree")
        self.assertEqual(core.state.brain_decision["phase"], "target_selected")

    def test_bank_closed_after_banking_complete_without_tree_uses_profile_return_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            open_response = snapshot_with_logs(session, 1, [], objects=[])
            closed_response = snapshot_with_logs(session, 2, [], objects=[])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            for response, bank_open in ((open_response, True), (closed_response, False)):
                response["payloads"]["inventory"]["inventory"] = {
                    "known": True,
                    "freeSlots": 15,
                    "filledSlots": 13,
                    "itemCount": 1300,
                    "inventoryFull": False,
                    "inventorySlotCount": 28,
                    "slotCount": 28,
                    "signature": coin_signature,
                    "inventorySignature": coin_signature,
                    "items": coin_items,
                }
                response["payloads"]["bank_ui"] = {
                    "bankOpen": bank_open,
                    "bankPinOpen": False,
                    "bankRootVisible": bank_open,
                    "bankContainerVisible": bank_open,
                    "bankInventoryVisible": bank_open,
                    "depositInventoryButtonVisible": bank_open,
                    "closeButtonVisible": bank_open,
                    "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
                    "bankSummary": {"occupiedSlots": 12, "uniqueItemIds": [1511, 1521]} if bank_open else {},
                }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service_target = {
                "targetType": "sceneObject",
                "classId": "bank_booth",
                "targetName": "Bank booth",
                "id": 10355,
                "worldX": 3208,
                "worldY": 3219,
                "plane": 0,
            }
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate=service_target,
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                destination=service_target,
                destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0},
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            no_service = ServiceContext(service_required=False, source_tick=2)
            no_pathing = PathingContext(pathing_needed=False, source_tick=2)
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect([open_response, open_response, closed_response, closed_response])):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", side_effect=[service, no_service]):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=[pathing, no_pathing, no_pathing, no_pathing, no_pathing]):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()
                        core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "return_to_resource")
        self.assertEqual(generic["activeIntent"], "return_to_resource_area")
        self.assertTrue(core.state.brain_decision["resourceReturnContext"]["returnDestinationAvailable"])
        self.assertEqual(core.state.brain_decision["resourceReturnContext"]["returnDestinationSource"], "profile_anchor")
        self.assertEqual(core.state.brain_decision["resourceReturnContext"]["reason"], "using_profile_resource_anchor")
        self.assertEqual(generic["returnRouteAvailable"], True)
        self.assertEqual(core.state.brain_decision["postBankReacquisitionContext"]["reason"], "no_resource_target_observed")
        self.assertTrue(core.state.brain_decision["postBankReacquisitionContext"]["resourceTargetReacquisitionAllowed"])
        self.assertFalse(core.state.brain_decision["closeBankContext"]["closeBankNeeded"])
        self.assertNotIn("no_target_observed", generic.get("blockingConditions", []))

    def test_bank_closed_after_banking_complete_uses_return_anchor_when_visible_tree_is_far_from_resource_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            distant_tree = fixtures.raw_scene_object(
                10820,
                3212,
                3232,
                220,
                160,
                name="Oak tree",
                object_key="castle-tree-after-bank",
            )
            response = snapshot_with_logs(session, 2, [], objects=[distant_tree])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            response["payloads"]["inventory"]["inventory"] = {
                "known": True,
                "freeSlots": 15,
                "filledSlots": 13,
                "itemCount": 1300,
                "inventoryFull": False,
                "inventorySlotCount": 28,
                "slotCount": 28,
                "signature": coin_signature,
                "inventorySignature": coin_signature,
                "items": coin_items,
            }
            response["payloads"]["bank_ui"] = {
                "bankOpen": False,
                "bankPinOpen": False,
                "bankRootVisible": False,
                "bankContainerVisible": False,
                "bankInventoryVisible": False,
                "depositInventoryButtonVisible": False,
                "closeButtonVisible": False,
                "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            no_service = ServiceContext(service_required=False, source_tick=2)
            pathing_generic_states: list[dict] = []
            pathing_search_budgets: list[tuple[int | None, float | None]] = []

            def fake_pathing_context(**kwargs):
                generic = dict(kwargs.get("generic_task_state") or {})
                pathing_generic_states.append(generic)
                pathing_search_budgets.append((kwargs.get("max_nodes"), kwargs.get("budget_millis")))
                active_target = generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else None
                tile = (
                    {"worldX": active_target.get("worldX"), "worldY": active_target.get("worldY"), "plane": active_target.get("plane")}
                    if active_target
                    else None
                )
                return PathingContext(
                    pathing_needed=bool(active_target and generic.get("activeIntent") == "return_to_resource_area"),
                    destination=active_target,
                    destination_tile=tile,
                    local_reachability="unknown" if active_target else "unknown",
                    reason="resource_return_destination" if active_target else "not_needed_for_current_phase",
                    source_tick=2,
                )

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=no_service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=fake_pathing_context):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.state.brain_decision = {
                            "bankOperationContext": {"bankingComplete": True, "completionReason": "no_resource_items_held"},
                            "postBankReacquisitionContext": {"postBankReacquisitionNeeded": True, "reason": "bank_ui_still_open"},
                        }
                        core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        return_context = core.state.brain_decision["resourceReturnContext"]
        return_route = core.state.brain_decision["returnRouteContext"]
        self.assertEqual(return_context["reason"], "using_profile_resource_anchor")
        self.assertTrue(return_context["resourceTargetCurrentlyVisible"])
        self.assertEqual(generic["phase"], "return_to_resource")
        self.assertEqual(generic["activeIntent"], "return_to_resource_area")
        self.assertEqual(generic["activeIntentTarget"], return_route["currentNavigationTarget"])
        self.assertNotEqual(generic["activeIntentTarget"].get("objectKey"), "castle-tree-after-bank")
        self.assertTrue(any(state.get("activeIntent") == "return_to_resource_area" for state in pathing_generic_states))

    def test_bank_closed_after_banking_complete_without_tree_uses_resource_memory_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 2, [], objects=[])
            coin_items = [{"slot": slot, "itemId": 995, "quantity": 100} for slot in range(13)]
            coin_signature = "|".join(f"{item['slot']}:{item['itemId']}:{item['quantity']}" for item in coin_items)
            response["payloads"]["inventory"]["inventory"] = {
                "known": True,
                "freeSlots": 15,
                "filledSlots": 13,
                "itemCount": 1300,
                "inventoryFull": False,
                "inventorySlotCount": 28,
                "slotCount": 28,
                "signature": coin_signature,
                "inventorySignature": coin_signature,
                "items": coin_items,
            }
            response["payloads"]["bank_ui"] = {
                "bankOpen": False,
                "bankPinOpen": False,
                "bankRootVisible": False,
                "bankContainerVisible": False,
                "bankInventoryVisible": False,
                "depositInventoryButtonVisible": False,
                "closeButtonVisible": False,
                "inventorySummary": {"freeSlots": 15, "occupiedSlots": 13, "matchingResourceCount": 0},
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            no_service = ServiceContext(service_required=False, source_tick=2)
            pathing_generic_states: list[dict] = []
            pathing_search_budgets: list[tuple[int | None, float | None]] = []

            def fake_pathing_context(**kwargs):
                generic = dict(kwargs.get("generic_task_state") or {})
                pathing_generic_states.append(generic)
                pathing_search_budgets.append((kwargs.get("max_nodes"), kwargs.get("budget_millis")))
                active_target = generic.get("activeIntentTarget") if isinstance(generic.get("activeIntentTarget"), dict) else None
                tile = (
                    {"worldX": active_target.get("worldX"), "worldY": active_target.get("worldY"), "plane": active_target.get("plane")}
                    if active_target
                    else None
                )
                return PathingContext(
                    pathing_needed=bool(active_target and generic.get("activeIntent") == "return_to_resource_area"),
                    destination=active_target,
                    destination_tile=tile,
                    local_reachability="unknown" if active_target else "unknown",
                    reason="resource_return_destination" if active_target else "not_needed_for_current_phase",
                    source_tick=2,
                )

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=no_service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=fake_pathing_context):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.state.brain_decision = {
                            "bankOperationContext": {"bankingComplete": True, "completionReason": "no_resource_items_held"},
                            "postBankReacquisitionContext": {"postBankReacquisitionNeeded": True, "reason": "bank_ui_still_open"},
                        }
                        core.state.resource_area_memory = resource_return_analyzer.ResourceAreaMemoryState(
                            last_resource_activity_tick=1,
                            last_resource_player_tile={"worldX": 3155, "worldY": 3236, "plane": 0},
                            last_resource_target_tile={"worldX": 3156, "worldY": 3237, "plane": 0},
                            last_resource_target_name="Oak tree",
                            last_resource_target_id=10820,
                            last_resource_target_class="tree",
                            last_resource_cluster_center={"worldX": 3156, "worldY": 3237, "plane": 0},
                            last_resource_plane=0,
                            last_resource_profile="woodcutting",
                        )
                        core.poll_once()

        generic = core.state.brain_decision["genericTaskState"]
        status = core.state.status()
        return_route = core.state.brain_decision["returnRouteContext"]
        self.assertEqual(generic["phase"], "return_to_resource")
        self.assertEqual(generic["activeIntent"], "return_to_resource_area")
        self.assertEqual(generic["activeIntentTarget"], return_route["currentNavigationTarget"])
        self.assertTrue(core.state.brain_decision["resourceReturnContext"]["returnDestinationAvailable"])
        self.assertEqual(core.state.brain_decision["resourceReturnContext"]["reason"], "using_remembered_resource_area")
        self.assertEqual(return_route["schema"], "return_route_context.v1")
        self.assertTrue(return_route["returnActionReady"])
        self.assertEqual(return_route["targetResourceArea"], {"worldX": 3156, "worldY": 3237, "plane": 0})
        self.assertTrue(core.state.brain_decision["pathingContext"]["pathingNeeded"])
        self.assertEqual(core.state.brain_decision["pathingContext"]["destination"], return_route["currentNavigationTarget"])
        self.assertEqual(status["resourceReturnDestinationAvailable"], True)
        self.assertEqual(status["returnRouteAvailable"], True)
        self.assertEqual(status["returnRouteId"], "lumbridge_west_trees_to_lumbridge_castle_bank_return")
        self.assertEqual(status["resourceAnchorKnown"], True)
        self.assertTrue(any(state.get("activeIntent") == "return_to_resource_area" for state in pathing_generic_states))
        self.assertTrue(any((nodes or 0) >= 8192 and (budget or 0.0) >= 25.0 for nodes, budget in pathing_search_budgets))

    def test_select_target_intent_does_not_stabilize_bank_service_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--input-source", "plugin-snapshot", "--task-policy", "woodcutting_bank")
            core = daemon.LiveCoreDaemon(session, args)
            bank_target = {
                "objectKey": "bank-booth-1",
                "targetName": "Bank booth",
                "targetType": "sceneObject",
                "classId": "bank_related",
                "worldX": 3208,
                "worldY": 3221,
                "plane": 2,
                "navigation": {"directReachability": "reachable"},
            }
            core.state.latest_context = {
                "status": {"lastProcessedTick": 77},
                "candidates": [bank_target],
                "profileCandidates": [],
            }
            core.state.analysis_result.targets = TargetContext(
                candidates=[bank_target],
                broad_candidates=[bank_target],
                profile_candidates=[],
                raw_best_target=None,
                candidate_count=1,
                profile_candidate_count=0,
                source_tick=77,
            )
            decision = {
                "task": "woodcutting",
                "genericTaskState": {
                    "phase": "needs_more_context",
                    "activeIntent": "select_target",
                    "activeIntentTarget": None,
                },
            }

            stable, fields = core.stabilize_intent(decision)

        self.assertIsNone(stable.selectedTarget)
        self.assertIsNone(fields["intentSelectedTargetKey"])

    def test_bank_pin_ui_blocks_after_service_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            response["payloads"]["bank_ui"] = {
                "bankOpen": True,
                "bankPinOpen": True,
                "bankRootVisible": True,
            }
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank_full",
                best_service_candidate={"targetType": "sceneObject", "classId": "bank_booth", "targetName": "Bank booth", "worldX": 3208, "worldY": 3219, "plane": 0},
                candidate_count=1,
                source_tick=1,
            )
            pathing = PathingContext(
                pathing_needed=False,
                service_ready=True,
                service_ready_reason="arrived_at_service",
                service_ready_stable_for_ticks=2,
                path_completed=True,
                source_tick=1,
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        status = core.state.status()
        generic = core.state.brain_decision["genericTaskState"]
        self.assertEqual(generic["phase"], "blocked")
        self.assertEqual(generic["activeIntent"], "needs_user_resolution")
        self.assertIn("bank_pin_required", generic["blockingConditions"])
        self.assertTrue(status["bankPinOpen"])

    def test_status_exposes_compact_cycle_history_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                first_count = core.state.status()["cycleHistoryCount"]
                core.poll_once()
                status = core.state.status()

        self.assertGreaterEqual(first_count, 1)
        self.assertEqual(status["cycleHistoryCount"], first_count)
        self.assertIn("currentCycleStage", status)
        self.assertIn("currentCycleStageStableForTicks", status)
        self.assertIn("cycleHistoryTail", status)
        self.assertLessEqual(len(status["cycleHistoryTail"]), 10)

    def test_daemon_passes_in_memory_path_intent_state_to_pathing_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(
                service_required=True,
                service_type_needed="bank",
                best_service_candidate={
                    "objectKey": "bank-booth-1",
                    "targetType": "sceneObject",
                    "classId": "bank_booth",
                    "targetName": "Bank booth",
                    "id": 10355,
                    "worldX": 3208,
                    "worldY": 3219,
                    "plane": 0,
                    "sceneX": 20,
                    "sceneY": 21,
                },
                service_candidates=[],
                candidate_count=1,
            )
            pathing = PathingContext(pathing_needed=True, destination_tile={"worldX": 3208, "worldY": 3219, "plane": 0})
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", return_value=pathing) as pathing_mock:
                        core = daemon.LiveCoreDaemon(session, args)
                        core.poll_once()

        kwargs = pathing_mock.call_args.kwargs
        self.assertIs(kwargs["path_intent_state"], core.state.path_intent_state)
        self.assertIsNotNone(kwargs["activity_context"])
        self.assertEqual(kwargs["generic_task_state"]["activeIntent"], "needs_service")

    def test_daemon_passes_in_memory_service_target_state_to_service_analyzer(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(service_required=True, service_type_needed="bank", candidate_count=0)
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service) as service_mock:
                    core = daemon.LiveCoreDaemon(session, args)
                    core.poll_once()

        kwargs = service_mock.call_args.kwargs
        self.assertIs(kwargs["service_target_state"], core.state.service_target_state)
        self.assertEqual(kwargs["current_plane"], core.state.analysis_result.player.plane)

    def test_daemon_attaches_service_route_context_when_service_target_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(service_required=True, service_type_needed="bank_full", candidate_count=0)
            route_context = {
                "schema": "service_route_context.v1",
                "status": "WARN",
                "routeAvailable": True,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepStatus": "static_route_prior",
                "currentNodeId": "lumbridge_west_stair_anchor",
                "nextEdge": {"type": "walk_to"},
                "completedSteps": ["first stairs up"],
                "currentNavigationTarget": {
                    "targetType": "service_route_anchor",
                    "targetName": "Lumbridge Castle west stair approach",
                    "worldX": 3205,
                    "worldY": 3229,
                    "plane": 0,
                    "verifiedLive": False,
                },
                "warnings": ["route prior is unverified"],
            }
            real_pathing = daemon.pathing_analyzer.analyze_pathing_context
            pathing_calls = []

            def wrapped_pathing(*args, **kwargs):
                pathing_calls.append(kwargs)
                return real_pathing(*args, **kwargs)

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.service_route_core, "build_service_route_context", return_value=route_context) as route_mock:
                        with mock.patch.object(daemon.pathing_analyzer, "analyze_pathing_context", side_effect=wrapped_pathing):
                            core = daemon.LiveCoreDaemon(session, args)
                            core.poll_once()

        route_kwargs = route_mock.call_args.kwargs
        self.assertIs(route_kwargs["route_state"], core.state.service_route_state)
        service_payload = core.state.brain_decision["serviceContext"]
        self.assertEqual(service_payload["serviceRouteContext"]["routeId"], route_context["routeId"])
        self.assertEqual(core.state.brain_decision["serviceRouteContext"]["routeStepStatus"], "static_route_prior")
        self.assertEqual(core.state.brain_decision["navigationIntentContext"]["targetKind"], "service_route")
        status = core.state.context()["status"]
        self.assertEqual(status["serviceRouteId"], route_context["routeId"])
        self.assertEqual(status["serviceRouteStepStatus"], "static_route_prior")
        public_status = core.state.status()
        self.assertEqual(public_status["serviceRouteId"], route_context["routeId"])
        self.assertEqual(public_status["serviceRouteStepStatus"], "static_route_prior")
        self.assertTrue(public_status["serviceRouteAvailable"])
        self.assertEqual(public_status["serviceRouteCurrentNodeId"], "lumbridge_west_stair_anchor")
        self.assertEqual(public_status["serviceRouteNextEdgeType"], "walk_to")
        self.assertEqual(public_status["serviceRouteCompletedSteps"], ["first stairs up"])
        self.assertEqual(public_status["serviceRouteObjectsVisible"], 0)
        self.assertEqual(public_status["serviceRouteObjectsActionable"], 0)
        self.assertEqual(public_status["serviceRouteServiceObjectsVisible"], 0)
        self.assertFalse(public_status["serviceRouteSelectedObjectPresent"])
        self.assertGreaterEqual(pathing_calls[0]["max_nodes"], 8192)
        self.assertGreaterEqual(pathing_calls[0]["budget_millis"], 25.0)

    def test_new_service_cycle_clears_stale_service_route_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, list(range(28)))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "60",
                "--task-policy",
                "woodcutting_bank",
            )
            service = ServiceContext(service_required=True, service_type_needed="bank_full", candidate_count=0)
            route_context = {
                "schema": "service_route_context.v1",
                "status": "WARN",
                "routeAvailable": True,
                "routeId": "lumbridge_west_trees_to_lumbridge_castle_bank",
                "routeStepStatus": "static_route_prior",
                "currentNodeId": "lumbridge_castle_west_approach",
                "completedSteps": [],
                "currentNavigationTarget": {
                    "targetType": "service_route_anchor",
                    "targetName": "Lumbridge Castle west approach",
                    "worldX": 3201,
                    "worldY": 3240,
                    "plane": 0,
                    "verifiedLive": False,
                },
            }

            def build_route_context(**kwargs):
                self.assertEqual(kwargs["route_state"].completed_steps, [])
                self.assertIsNone(kwargs["route_state"].active_route_id)
                return route_context

            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                with mock.patch.object(daemon.service_analyzer, "analyze_service_context", return_value=service):
                    with mock.patch.object(daemon.service_route_core, "build_service_route_context", side_effect=build_route_context):
                        core = daemon.LiveCoreDaemon(session, args)
                        core.state.service_route_state.active_route_id = "lumbridge_west_trees_to_lumbridge_castle_bank"
                        core.state.service_route_state.completed_steps = ["first stairs up"]
                        core.state.brain_decision = {
                            "serviceContext": {"serviceRequired": False},
                            "genericTaskState": {"phase": "target_selected", "activeIntent": "select_target"},
                        }
                        core.poll_once()

        self.assertEqual(core.state.brain_decision["serviceRouteResetReason"], "service_cycle_started")
        self.assertEqual(core.state.brain_decision["serviceRouteContext"]["currentNodeId"], "lumbridge_castle_west_approach")

    def test_daily_daemon_does_not_write_policy_task_or_analyzer_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [0, 1, 2])
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--task-policy",
                "woodcutting_bank",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            unexpected = []
            for pattern in ("*policy*.json", "*policy*.jsonl", "*task_state*.json", "*task_state*.jsonl", "*analyzer*.json", "*analyzer*.jsonl"):
                unexpected.extend(path for path in session.rglob(pattern) if path.name != "overlay_debug_state.json")

        self.assertEqual(unexpected, [])

    def test_snapshot_no_file_mode_selects_plugin_snapshot_without_compact_file_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--daily-mode", "snapshot-no-files")

        self.assertEqual(args.daily_mode, "snapshot-no-files")
        self.assertEqual(args.input_source, "plugin-snapshot")
        self.assertEqual(args.plugin_snapshot_tier, "hot")
        processor_options = daemon.processor_args(args, live.PLUGIN_SNAPSHOT_SOURCE, suppress_output_writes=True)
        self.assertFalse(processor_options.require_compact_packets)
        self.assertEqual(processor_options.input_source, "plugin-snapshot")

    def test_processor_args_preserve_preset_for_plugin_snapshot_service_hints(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--daily-mode", "snapshot-no-files", "--preset", "woodcut_bank")

        processor_options = daemon.processor_args(args, live.PLUGIN_SNAPSHOT_SOURCE, suppress_output_writes=True)

        self.assertEqual(processor_options.preset, "woodcut_bank")
        body = live.plugin_snapshot_request_body(processor_options)
        self.assertIn("route_transition", body["desiredClasses"])
        self.assertIn("bank_related", body["desiredClasses"])

    def test_snapshot_no_file_status_marks_compact_files_not_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--daily-mode", "snapshot-no-files")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                result = core.poll_once()

        status = result["status"]
        self.assertEqual(status["inputSourceActive"], "plugin-snapshot")
        self.assertTrue(status["noFileDaily"])
        self.assertFalse(status["compactPacketFilesRequired"])
        self.assertFalse(status["debugMirrorEnabled"])
        self.assertFalse((session / "live_packets").exists())

    def test_snapshot_no_file_mode_does_not_silently_fallback_to_compact_packets(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--daily-mode", "snapshot-no-files")
            core = daemon.LiveCoreDaemon(session, args)

        self.assertEqual(core.initial_source(), live.PLUGIN_SNAPSHOT_SOURCE)
        self.assertEqual(args.plugin_snapshot_fallback, "none")

    def test_processor_args_centralizes_max_draw_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--overlay-debug-target-limit", "500")

        processor_options = daemon.processor_args(args, live.COMPACT_PACKET_SOURCE, suppress_output_writes=True)

        self.assertEqual(processor_options.overlay_debug_target_limit, live.MAX_DRAW_LIMIT)
        self.assertEqual(processor_options.overlay_debug_hull_limit, live.MAX_DRAW_HULL_LIMIT)

    def test_builds_context_from_synthetic_plugin_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                result = core.poll_once()
                context = core.build_context_response(
                    {
                        "schema": "context_request.v1",
                        "task": "woodcutting",
                        "needs": ["baseline", "best:tree", "nearest:tree", "candidates", "inventory", "diagnostics"],
                        "maxCandidates": 3,
                        "responseMode": "compact",
                    }
                )

        self.assertEqual(result["status"]["inputSourceActive"], "plugin-snapshot")
        self.assertGreater(context["candidates"]["count"], 0)
        self.assertIsNotNone(context["bestCandidates"]["tree"])
        self.assertEqual(context["bestCandidates"]["tree"]["targetName"], "Oak tree")

    def test_health_and_context_endpoints_serve_memory_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.start_context_server()
                try:
                    port = args.context_port
                    health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read().decode("utf-8"))
                    body = json.dumps(
                        {
                            "schema": "context_request.v1",
                            "task": "woodcutting",
                            "needs": ["best:tree", "diagnostics"],
                            "maxCandidates": 1,
                            "responseMode": "compact",
                        }
                    ).encode("utf-8")
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/context",
                        data=body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    context = json.loads(urllib.request.urlopen(request, timeout=1).read().decode("utf-8"))
                finally:
                    core.stop_context_server()

        self.assertEqual(health["schema"], "context_health.v1")
        self.assertTrue(health["liveCoreDaemonActive"])
        self.assertEqual(health["service"], "live_core_daemon")
        self.assertEqual(context["schema"], "context_response.v1")
        self.assertIsNotNone(context["bestCandidates"]["tree"])

    def test_debug_live_files_are_not_written_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            live_dir = session / "interaction_geometry" / "live"
            self.assertFalse((live_dir / "live_status.json").exists())
            self.assertFalse((live_dir / "live_candidates.jsonl").exists())
            self.assertFalse((live_dir / "overlay_debug_state.json").exists())
            self.assertFalse((live_dir / "intent_stabilizer_state.json").exists())
            self.assertFalse((live_dir / "intent_history.jsonl").exists())

    def test_overlay_state_is_written_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--write-overlay-state")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            overlay_path = session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            self.assertTrue(overlay_path.exists())
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            self.assertEqual(overlay["schema"], "telemetry_overlay_debug_state.v1")
            self.assertFalse((overlay_path.parent / "intent_stabilizer_state.json").exists())
            self.assertFalse((overlay_path.parent / "intent_history.jsonl").exists())

    def test_intent_overlay_contains_selected_tree_and_limited_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [0], objects=synthetic_tree_objects(5))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--write-overlay-state",
                "--human-dashboard",
                "--goal-count",
                "5",
                "--overlay-backup-candidates",
                "2",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            overlay_path = session / "interaction_geometry" / "live" / "overlay_debug_state.json"
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))

        intent = overlay["intentState"]
        markers = intent["markers"]
        self.assertEqual(intent["schema"], "overlay_intent_state.v1")
        self.assertEqual(overlay["summary"]["overlayMode"], "intent")
        self.assertEqual(markers[0]["markerType"], "selected_target")
        self.assertEqual(markers[0]["label"], "Target: Oak tree")
        self.assertTrue(markers[0]["selected"])
        self.assertGreater(markers[0]["priority"], 30)
        for marker in markers[1:]:
            if marker["markerType"] == "backup_candidate":
                self.assertFalse(marker["selected"])
                self.assertLess(marker["priority"], markers[0]["priority"])
        self.assertEqual(len([marker for marker in markers if marker["markerType"] == "backup_candidate"]), 2)
        self.assertLess(len(markers), overlay["summary"]["candidateCount"])
        self.assertEqual(overlay["summary"]["intentMarkerCount"], len(markers))
        self.assertGreater(overlay["summary"]["candidateMarkersSuppressed"], 0)
        self.assertTrue(markers[0]["actionable"])
        self.assertEqual(markers[0]["safeAimPoint"]["status"], "PASS")
        self.assertGreaterEqual(overlay["summary"]["safeAimpoints"], 1)
        self.assertTrue(overlay["summary"]["selectedSafeAimPoint"])
        status = core.state.status()
        self.assertIn("intentOverlayContext", status)
        self.assertEqual(status["intentOverlayContext"]["selectedMarker"]["markerType"], "selected_target")
        self.assertEqual(len(status["intentOverlayContext"]["backupMarkers"]), 2)

    def test_intent_overlay_uses_stabilized_target_not_raw_flicker(self):
        def candidate(key: str, score: int, distance: int) -> dict:
            return {
                "objectKey": key,
                "targetName": f"Tree {key}",
                "targetType": "sceneObject",
                "classId": "tree",
                "id": 1278,
                "hash": score,
                "worldX": 3200 + distance,
                "worldY": 3200,
                "plane": 0,
                "sceneX": 10 + distance,
                "sceneY": 10,
                "qualityScore": score,
                "distanceTiles": distance,
                "targetLiveState": "live_assumed",
                "navigation": {"directReachability": "reachable"},
                "aimPoint": {"canvasX": 120 + distance, "canvasY": 140},
                "lastSeenTick": 1,
                "present": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            args = make_args(session, "--brain-task", "woodcutting", "--overlay-backup-candidates", "1")
            first = candidate("oak-a", 100, 3)
            jitter = candidate("oak-b", 101, 2)
            state = daemon.intent_stabilizer.IntentState()
            daemon.intent_stabilizer.choose_stable_intent(
                state,
                [first, jitter],
                {
                    "activeTask": "woodcutting",
                    "activeIntent": "target_available",
                    "latestTick": 1,
                    "rawBestTarget": first,
                    "profile": "woodcutting",
                },
            )
            stable = daemon.intent_stabilizer.choose_stable_intent(
                state,
                [jitter, first],
                {
                    "activeTask": "woodcutting",
                    "activeIntent": "target_available",
                    "latestTick": 2,
                    "rawBestTarget": jitter,
                    "profile": "woodcutting",
                },
            )
            overlay = daemon.build_intent_overlay_state(
                {"status": {"lastProcessedTick": 2}, "candidates": [jitter, first]},
                {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
                args,
                daemon.utc_now(),
                stable,
            )

        self.assertEqual(stable.rawBestTargetKey, "oak-b")
        self.assertEqual(stable.selectedTargetKey, "oak-a")
        self.assertEqual(overlay["markers"][0]["markerType"], "selected_target")
        self.assertEqual(overlay["markers"][0]["objectKey"], "oak-a")
        self.assertEqual(overlay["markers"][0]["switchReason"], "retained_current_target")

    def test_selected_duplicate_backup_is_merged_into_selected_marker(self):
        selected_raw = {
            "targetName": "Tree",
            "targetType": "sceneObject",
            "classId": "tree",
            "id": 1278,
            "hash": 1340218036,
            "worldX": 3156,
            "worldY": 3237,
            "plane": 0,
            "sceneX": 52,
            "sceneY": 53,
            "qualityScore": 100,
            "distanceTiles": 2,
            "aimPoint": {"canvasX": 200, "canvasY": 220},
            "navigation": {"directReachability": "reachable"},
            "targetLiveState": "live_assumed",
        }
        richer_duplicate = dict(
            selected_raw,
            objectKey="0:3156:3237:52:53:GAME_OBJECT:1278:1340218036:0",
            kind="GAME_OBJECT",
            layer="ground",
            clickableHull=[[10, 10], [20, 10], [20, 20]],
            clickboxPolygon=[[10, 10], [20, 10], [20, 20]],
            convexHull=[[9, 9], [21, 9], [21, 21]],
            canvasTilePolygon=[[8, 8], [22, 8], [22, 22]],
            geometrySource="clickbox",
        )
        backup = dict(
            selected_raw,
            objectKey="0:3154:3240:50:56:GAME_OBJECT:1278:234:0",
            hash=234,
            worldX=3154,
            worldY=3240,
            sceneX=50,
            sceneY=56,
            clickableHull=[[30, 30], [40, 30], [40, 40]],
        )
        state = daemon.intent_stabilizer.IntentState()
        stable = daemon.intent_stabilizer.choose_stable_intent(
            state,
            [richer_duplicate, backup],
            {
                "activeTask": "woodcutting",
                "activeIntent": "target_available",
                "latestTick": 344,
                "rawBestTarget": selected_raw,
                "profile": "woodcutting",
            },
        )
        args = make_args(Path("session"), "--brain-task", "woodcutting", "--overlay-backup-candidates", "2")
        overlay = daemon.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 344}, "candidates": [richer_duplicate, backup]},
            {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
            args,
            daemon.utc_now(),
            stable,
        )

        selected = overlay["markers"][0]
        backups = [marker for marker in overlay["markers"] if marker["markerType"] == "backup_candidate"]
        self.assertEqual(selected["markerType"], "selected_target")
        self.assertTrue(selected["selected"])
        self.assertEqual(selected["role"], "selected")
        self.assertTrue(selected["label"].startswith("Target:"))
        self.assertEqual(selected["objectKey"], "0:3156:3237:52:53:GAME_OBJECT:1278:1340218036:0")
        self.assertEqual(selected["kind"], "GAME_OBJECT")
        self.assertIn("clickableHull", selected)
        self.assertIn("clickboxPolygon", selected)
        self.assertTrue(selected["clickableHullAvailable"])
        self.assertEqual(selected["geometrySource"], "clickableHull")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["role"], "backup")
        self.assertFalse(backups[0]["selected"])
        self.assertTrue(backups[0]["label"].startswith("Backup"))
        self.assertNotEqual(backups[0].get("objectKey"), selected.get("objectKey"))

    def test_intent_overlay_prefers_previous_backups_when_selected_is_stable(self):
        def candidate(key: str, score: int, distance: int) -> dict:
            return {
                "objectKey": key,
                "targetName": f"Tree {key}",
                "targetType": "sceneObject",
                "classId": "tree",
                "id": 1278,
                "hash": score,
                "worldX": 3200 + distance,
                "worldY": 3200,
                "plane": 0,
                "sceneX": 10 + distance,
                "sceneY": 10,
                "qualityScore": score,
                "distanceTiles": distance,
                "targetLiveState": "live_assumed",
                "navigation": {"directReachability": "reachable"},
                "aimPoint": {"canvasX": 120 + distance, "canvasY": 140},
                "lastSeenTick": 1,
                "present": True,
            }

        args = make_args(Path("session"), "--brain-task", "woodcutting", "--overlay-backup-candidates", "2")
        state = daemon.intent_stabilizer.IntentState()
        selected = candidate("oak-selected", 100, 2)
        backup_a = candidate("oak-backup-a", 95, 3)
        backup_b = candidate("oak-backup-b", 94, 4)
        backup_c = candidate("oak-backup-c", 99, 1)

        first = daemon.intent_stabilizer.choose_stable_intent(
            state,
            [selected, backup_a, backup_b],
            {
                "activeTask": "woodcutting",
                "activeIntent": "target_available",
                "latestTick": 1,
                "rawBestTarget": selected,
                "profile": "woodcutting",
            },
        )
        daemon.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 1}, "candidates": [selected, backup_a, backup_b]},
            {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
            args,
            daemon.utc_now(),
            first,
        )
        second = daemon.intent_stabilizer.choose_stable_intent(
            state,
            [selected, backup_c, backup_a, backup_b],
            {
                "activeTask": "woodcutting",
                "activeIntent": "target_available",
                "latestTick": 2,
                "rawBestTarget": selected,
                "profile": "woodcutting",
            },
        )
        overlay = daemon.build_intent_overlay_state(
            {"status": {"lastProcessedTick": 2}, "candidates": [selected, backup_c, backup_a, backup_b]},
            {"task": "woodcutting", "phase": "target_available", "confidence": 0.8},
            args,
            daemon.utc_now(),
            second,
        )

        backups = [marker for marker in overlay["markers"] if marker["markerType"] == "backup_candidate"]
        self.assertEqual([marker["objectKey"] for marker in backups], ["oak-backup-a", "oak-backup-b"])
        self.assertNotIn("oak-selected", [marker["objectKey"] for marker in backups])

    def test_intent_overlay_keeps_marker_schema_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [], objects=synthetic_tree_objects(3))
            args = make_args(session, "--input-source", "plugin-snapshot", "--write-overlay-state", "--human-dashboard", "--goal-count", "5")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            overlay = json.loads((session / "interaction_geometry" / "live" / "overlay_debug_state.json").read_text(encoding="utf-8"))

        markers = overlay["intentState"]["markers"]
        self.assertTrue(markers)
        self.assertIn("markerType", markers[0])
        self.assertIn("targetType", markers[0])

    def test_non_woodcutting_task_clears_tree_intent_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [], objects=synthetic_tree_objects(4))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--write-overlay-state",
                "--human-dashboard",
                "--brain-task",
                "banking",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            overlay = json.loads((session / "interaction_geometry" / "live" / "overlay_debug_state.json").read_text(encoding="utf-8"))

        markers = overlay["intentState"]["markers"]
        self.assertEqual(overlay["intentState"]["activeTask"], "banking")
        self.assertFalse([marker for marker in markers if marker.get("classId") == "tree"])

    def test_generic_intent_marker_schema_supports_future_target_types(self):
        candidate = {
            "name": "Banker",
            "classId": "banker",
            "targetType": "npc",
            "id": 123,
            "hash": 456,
            "objectKey": "banker-1",
            "worldX": 3200,
            "worldY": 3201,
            "plane": 0,
            "sceneX": 10,
            "sceneY": 11,
            "localX": 6400,
            "localY": 6408,
            "aimPoint": {"canvasX": 120, "canvasY": 130},
            "geometrySource": "live_object",
            "lastSeenTick": 42,
            "navigation": {"directReachability": "reachable"},
            "targetLiveState": "live_assumed",
            "qualityTier": "primary",
        }

        marker = daemon.intent_marker_from_candidate(candidate, "selected_target", "Target: Banker", "future task selected target")

        self.assertEqual(marker["targetType"], "npc")
        self.assertEqual(marker["markerType"], "selected_target")
        self.assertEqual(marker["aimPoint"], {"canvasX": 120, "canvasY": 130})
        self.assertEqual(marker["reachability"], "reachable")
        self.assertEqual(marker["objectKey"], "banker-1")
        self.assertEqual(marker["hash"], 456)
        self.assertEqual(marker["worldX"], 3200)
        self.assertEqual(marker["sceneX"], 10)
        self.assertEqual(marker["localX"], 6400)
        self.assertEqual(marker["geometrySource"], "live_object")
        self.assertEqual(marker["projectionMode"], "live_tile_fallback")
        self.assertFalse(marker["projectionStale"])
        self.assertEqual(marker["tick"], 42)
        self.assertEqual(marker["markerVersion"], "overlay_intent_marker.v1")
        self.assertTrue(marker["markerId"])

    def test_intent_marker_marks_projection_sentinel_as_not_actionable(self):
        candidate = {
            "name": "Oak tree",
            "classId": "tree",
            "targetType": "sceneObject",
            "id": 10820,
            "objectKey": "oak-edge",
            "worldX": 3189,
            "worldY": 3248,
            "plane": 0,
            "sceneX": 10,
            "sceneY": 11,
            "aimPoint": {"canvasX": 2147483647.5, "canvasY": 2147483647.5, "source": "live_object_pending"},
            "geometrySource": "bounds",
            "navigation": {"directReachability": "reachable"},
            "targetLiveState": "live",
        }

        marker = daemon.intent_marker_from_candidate(candidate, "selected_target", "Target: Oak tree", "edge projection pending")
        target = daemon.overlay_target_from_intent_marker(marker)

        self.assertFalse(marker["actionable"])
        self.assertEqual(marker["validButUnsafeReason"], "invalidAimPoint")
        self.assertEqual(marker["safeAimPoint"]["status"], "FAIL")
        self.assertFalse(target["actionable"])
        self.assertEqual(target["validButUnsafeReason"], "invalidAimPoint")

    def test_candidate_overlay_mode_keeps_debug_candidate_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = snapshot_with_logs(session, 1, [], objects=synthetic_tree_objects(5))
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--write-overlay-state",
                "--overlay-mode",
                "candidates",
                "--overlay-debug-target-limit",
                "5",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

            overlay = json.loads((session / "interaction_geometry" / "live" / "overlay_debug_state.json").read_text(encoding="utf-8"))

        self.assertEqual(overlay["summary"]["overlayMode"], "candidates")
        self.assertEqual(overlay["summary"]["targetsWritten"], 5)
        self.assertNotIn("intentState", overlay)

    def test_auto_falls_back_to_compact_packets_when_snapshot_unhealthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            tree = fixtures.raw_scene_object(10820, 3201, 3201, 140, 120, name="Oak tree", object_key="oak-1")
            fixtures.write_compact_packets(session, {1: [tree]})
            args = make_args(session, "--input-source", "auto")
            with mock.patch.object(daemon, "plugin_snapshot_health_available", return_value={"available": False, "error": "connection refused"}):
                core = daemon.LiveCoreDaemon(session, args)
                result = core.poll_once()

        self.assertEqual(result["status"]["inputSourceActive"], "compact-packets")
        self.assertGreater(result["status"]["candidateCount"], 0)

    def test_woodcutting_bank_uses_service_candidates_without_replacing_tree_best(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            tree = fixtures.raw_scene_object(10820, 3201, 3201, 140, 120, name="Oak tree", object_key="oak-1")
            bank = fixtures.raw_scene_object(
                10355,
                3207,
                3215,
                180,
                150,
                name="Bank booth",
                actions=["Bank"],
                object_key="bank-booth-1",
            )
            response = snapshot_with_logs(session, 1, list(range(28)), objects=[tree, bank])
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--task-policy",
                "woodcutting_bank",
                "--goal-count",
                "5",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

        targets = core.state.analysis_result.targets
        service = core.state.analysis_result.service
        navigation_intent = core.state.analysis_result.navigation_intent
        pathing = core.state.analysis_result.pathing
        status = core.state.status()

        self.assertEqual(targets.raw_best_target["objectKey"], "oak-1")
        self.assertEqual(targets.profile_candidate_count, 1)
        self.assertEqual(targets.service_candidate_input_count, 1)
        self.assertEqual(targets.service_candidate_inputs[0]["objectKey"], "bank-booth-1")
        self.assertEqual(service.best_service_candidate["objectKey"], "bank-booth-1")
        self.assertEqual(navigation_intent.destination_target["objectKey"], "bank-booth-1")
        self.assertEqual(pathing.destination_tile, {"worldX": 3207, "worldY": 3215, "plane": 0})
        self.assertTrue(pathing.collision_window_available)
        self.assertTrue(pathing.collision_window_fresh)
        self.assertEqual(pathing.collision_window_radius, 10)
        self.assertTrue(pathing.destination_inside_collision_window)
        self.assertNotIn("navigation.local_collision_window", pathing.missing_capabilities)
        self.assertEqual(status["serviceCandidateInputCount"], 1)
        self.assertEqual(status["profileCandidateCount"], 1)
        self.assertTrue(status["collisionWindowAvailable"])
        self.assertTrue(status["pathingCollisionWindowAvailable"])

    def test_loaded_service_scene_bank_booth_blocks_deposit_fallback_without_projection_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            tree = fixtures.raw_scene_object(10820, 3201, 3201, 140, 120, name="Oak tree", object_key="oak-1")
            deposit = fixtures.raw_scene_object(
                10583,
                3210,
                3217,
                180,
                150,
                name="Bank Deposit Box",
                actions=["Deposit"],
                object_key="deposit-1",
            )
            booth = fixtures.raw_scene_object(
                10355,
                3208,
                3221,
                160,
                140,
                name="Bank booth",
                actions=["Bank"],
                object_key="booth-loaded",
            )
            response = snapshot_with_logs(session, 1, list(range(28)), objects=[tree, deposit])
            response["payloads"]["projection"]["serviceSceneObjects"] = [fixtures.compact_scene_object(booth)]
            args = make_args(
                session,
                "--input-source",
                "plugin-snapshot",
                "--task-policy",
                "woodcutting_bank",
                "--goal-count",
                "5",
            )
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

        targets = core.state.analysis_result.targets
        service = core.state.analysis_result.service
        status = core.state.status()

        self.assertEqual(targets.raw_best_target["objectKey"], "oak-1")
        self.assertEqual(targets.profile_candidate_count, 1)
        self.assertEqual(targets.loaded_service_scene_count, 1)
        self.assertEqual(targets.service_candidate_input_count, 2)
        self.assertEqual(service.best_service_candidate["objectKey"], "booth-loaded")
        self.assertEqual(service.selected_service_group, "full_bank")
        self.assertFalse(service.deposit_fallback_allowed)
        self.assertEqual(status["serviceCandidateSourceLanes"]["loadedServiceScene"], 1)
        self.assertEqual(status["sourceStageCounts"]["bank_booth"]["loadedServiceScene"], 1)

    def test_context_response_preserves_read_only_navigation_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                context = core.build_context_response(
                    {
                        "schema": "context_request.v1",
                        "task": "woodcutting",
                        "needs": ["baseline", "best:tree", "nearest:tree", "inventory", "activity", "diagnostics"],
                        "responseMode": "compact",
                    }
                )

        navigation = context["bestCandidates"]["tree"]["navigation"]
        self.assertEqual(navigation["interactionRadiusTiles"], 2)
        self.assertIn("pathLengthTiles", navigation)

    def test_benchmark_summary_uses_context_best_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--benchmark")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                result = core.poll_once()
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    core.print_benchmark(result)

        line = buffer.getvalue()
        self.assertIn("best=Oak tree 10820", line)
        self.assertNotIn("best=None", line)

    def test_no_goal_count_brain_is_observe_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()

        decision = core.state.brain_decision
        self.assertTrue(decision["progress"]["progressDisabled"])
        self.assertIsNone(decision["goalProgress"]["gainedSinceStart"])
        self.assertEqual(core.state.brain_state["resourceGainedCounts"], {})
        self.assertIn("observing woodcutting context", daemon.brain_core.format_human(decision))
        self.assertIn("disabled, no goal count set", daemon.brain_core.format_human(decision))

    def test_brain_state_file_resets_when_session_scope_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_session = Path(tmp) / "old"
            new_session = Path(tmp) / "new"
            old_session.mkdir()
            new_session.mkdir()
            state_path = Path(tmp) / "brain_state.json"
            state = daemon.brain_core.default_state("woodcutting", 5)
            state["latestTick"] = 99
            state["resourceGainedCounts"] = {"woodcutting_logs": 5}
            state["brainStateScope"] = daemon.brain_state_scope(old_session, "woodcutting", 5)
            daemon.brain_core.write_state(str(state_path), state)
            args = make_args(new_session, "--goal-count", "5", "--brain-state-file", str(state_path))

            loaded, warning = daemon.load_daemon_brain_state(new_session, args)

        self.assertEqual(warning, "brain state scope changed; progress baseline was reset")
        self.assertEqual(loaded["resourceGainedCounts"], {})
        self.assertEqual(loaded["brainStateScope"]["sessionPath"], str(new_session.resolve()))

    def test_reset_brain_state_is_applied_once_at_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5", "--reset-brain-state")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                self.assertTrue(core.brain_reset_applied)
                self.assertFalse(core.args.reset_brain_state)
                core.poll_once()
                first_warnings = list(core.state.context()["status"].get("warnings") or [])
                result = core.poll_once()

        status = core.state.context()["status"]
        self.assertTrue(status["brainResetApplied"])
        self.assertTrue("brainBaselineEstablished" in status)
        self.assertTrue(any("brain state reset requested" in warning for warning in first_warnings))
        self.assertFalse(any("brain state reset requested" in warning for warning in result["status"].get("warnings") or []))
        self.assertIn("brainObservedGained", status)

    def test_status_exposes_brain_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                status = core.state.status()

        self.assertEqual(status["brainPhase"], core.state.brain_decision["phase"])
        self.assertIsInstance(status["brainProgress"], dict)
        self.assertIn("baselineEstablished", status["brainProgress"])
        self.assertIn("intentStabilizerMillis", status)
        self.assertIn("intentCandidatesConsidered", status)
        self.assertIn("intentSwitchReason", status)
        self.assertIn("intentCandidateWasRetained", status)
        self.assertIn("intentCandidateWasSwitched", status)
        self.assertIn("intentRetainedDueToGrace", status)
        self.assertIn("intentCurrentMissingTicks", status)
        self.assertIn("intentSwitchAuditTail", status)

    def test_missing_inventory_poll_retains_previous_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            invalid = snapshot_with_logs(session, 3, [])
            invalid["payloads"]["inventory"] = {"inventory": {"known": True, "freeSlots": 23, "filledSlots": 5, "inventoryFull": False}}
            responses = [
                snapshot_with_logs(session, 1, [0, 1, 2, 3, 4]),
                snapshot_with_logs(session, 1, [0, 1, 2, 3, 4]),
                snapshot_with_logs(session, 2, [0, 1, 2, 3, 4, 5, 6, 7, 8]),
                invalid,
            ]
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect(responses)):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.poll_once()
                core.poll_once()
                status = core.state.status()

        progress = status["brainProgress"]
        self.assertEqual(progress["displayedGoalProgress"], 4)
        self.assertTrue(progress["progressRetainedFromPrevious"])
        self.assertEqual(status["progressFlickerPreventedCount"], 1)

    def test_empty_candidate_poll_retains_previous_candidates_within_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            good = snapshot_with_logs(session, 1, [])
            empty = snapshot_with_logs(session, 2, [], objects=[])
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect([good, good, empty])):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.poll_once()
                context = core.build_context_response(
                    {
                        "schema": "context_request.v1",
                        "task": "woodcutting",
                        "needs": ["best:tree", "candidates", "diagnostics"],
                        "maxCandidates": 3,
                        "responseMode": "compact",
                    }
                )
                status = core.state.status()

        self.assertGreater(context["candidates"]["count"], 0)
        self.assertTrue(status["candidateRetainedPrevious"])
        self.assertTrue(status["contextRetainedPrevious"])

    def test_empty_candidate_retention_expires_after_grace(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            good = snapshot_with_logs(session, 1, [])
            empty2 = snapshot_with_logs(session, 2, [], objects=[])
            empty3 = snapshot_with_logs(session, 3, [], objects=[])
            empty4 = snapshot_with_logs(session, 4, [], objects=[])
            args = make_args(session, "--input-source", "plugin-snapshot")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", side_effect=snapshot_request_side_effect([good, good, empty2, empty3, empty4])):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.poll_once()
                core.poll_once()
                core.poll_once()
                context = core.build_context_response(
                    {
                        "schema": "context_request.v1",
                        "task": "woodcutting",
                        "needs": ["best:tree", "candidates", "diagnostics"],
                        "maxCandidates": 3,
                        "responseMode": "compact",
                    }
                )
                status = core.state.status()

        self.assertEqual(context["candidates"]["count"], 0)
        self.assertFalse(status["contextRetainedPrevious"])

    def test_diagnose_from_daemon_uses_memory_when_writes_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session"
            response = synthetic_snapshot(session)
            args = make_args(session, "--input-source", "plugin-snapshot", "--human-dashboard", "--goal-count", "5")
            with mock.patch.object(live.PluginSnapshotTailer, "_request_snapshot", return_value=(response, len(json.dumps(response)))):
                core = daemon.LiveCoreDaemon(session, args)
                core.poll_once()
                core.start_context_server()
                try:
                    payload = diagnose_brain_progress.diagnose_from_daemon(f"http://127.0.0.1:{args.context_port}", "woodcutting", 5, "woodcutting_logs")
                finally:
                    core.stop_context_server()

        self.assertEqual(payload["source"], "daemon")
        self.assertTrue(payload["liveCoreDaemonActive"])
        self.assertIn("progressEstimate", payload)

    def test_diagnose_flags_invalid_matched_slot_item_id_none(self):
        payload = {
            "sessionPath": "daemon",
            "latestTick": 1,
            "resourceGroup": "woodcutting_logs",
            "itemIdsCounted": [1511, 1521],
            "currentMatchedSlots": [],
            "invalidMatchedSlots": [{"slot": 9, "itemId": None, "quantity": None, "counted": True}],
            "currentCount": {"known": True, "count": 0},
            "brainState": {"baselineEstablished": True, "baselineHeldCount": 5},
            "progressEstimate": {
                "currentHeldCount": 5,
                "baselineHeldCount": 5,
                "displayedGoalProgress": 0,
                "goalCount": 5,
                "goalComplete": False,
                "source": "inventory_snapshot_held_vs_baseline",
                "matchedSlots": [],
                "warnings": [],
            },
            "warnings": [],
        }
        payload["explanation"] = diagnose_brain_progress.explain(payload)
        output = diagnose_brain_progress.format_human(payload)
        self.assertIn("invalid matched slot without itemId", "\n".join(payload["explanation"]))
        self.assertIn("invalid matched slot: slot 9 itemId=None counted=True", output)

    def test_compact_human_context_hides_daily_frame_warning(self):
        response = {
            "schema": "context_response.v1",
            "status": "PASS",
            "warnings": ["no frame path in live baseline"],
            "missingCapabilities": ["inventoryDeltas", "inventory.deltas", "animationFrame"],
            "baseline": {"player": {"worldX": 1, "worldY": 2, "plane": 0}},
            "inventory": {"freeSlots": 27, "inventoryFull": False},
            "activity": {"apparentState": "idle"},
            "woodcuttingState": {"woodcuttingState": "target_depleted"},
            "recentEvents": [{"eventType": "target_depleted", "summary": "Target depleted", "tick": 1}],
        }
        output = live_context_format.format_context_human(response, compact=True)
        self.assertNotIn("no frame path", output)
        self.assertNotIn("animation frame detail", output)
        self.assertEqual(output.count("inventory change tracking is not available yet"), 1)
        self.assertIn("Activity: idle", output)
        self.assertNotIn("Activity: idle / target_depleted", output)
        self.assertIn("Recent task signals:", output)


if __name__ == "__main__":
    unittest.main()
