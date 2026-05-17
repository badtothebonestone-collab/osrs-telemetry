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
from analyzers.live_state import PathingContext, ServiceContext


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
            response = snapshot_with_logs(session, 1, [], objects=synthetic_tree_objects(5))
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
