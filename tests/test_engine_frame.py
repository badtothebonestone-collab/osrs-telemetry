from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from osrs_bot.behavior import BehaviorConfig
from osrs_bot.engine_frame import (
    CleanupEvidence,
    EngineFramePublisher,
    EngineStage,
    ObservationReference,
)
from osrs_bot.input_coordinator import (
    CameraInputVerificationEvidence,
    CommandEvidence,
    CursorFeedbackEvidence,
    DelayedCursorFeedbackEvent,
    FirmwareSafetyStatus,
    InputActivationBoundary,
    InputReceipt,
    PointerMotionEvidence,
)
from osrs_bot.input_capabilities import (
    InputCapabilities,
    InputOperation,
    RequiredInputCapabilities,
)
from osrs_bot.model import (
    Action,
    ActionKind,
    BANK_INTERFACE_NAME,
    InventoryItem,
    InventoryObservation,
    Observation,
    ObservationPipelineEvidence,
    PlayerObservation,
    SceneCensusEvidence,
    ScreenBounds,
    ScreenPoint,
    VerificationKind,
    VerificationSpec,
    WidgetObservation,
    WorldPoint,
)
from osrs_bot.observability import (
    ObservabilityEvidence,
    TimingEvidence,
    TimingPhase,
    WaitState,
)
from osrs_bot.safety import SafetyCheck
from osrs_bot.task_contract import (
    CameraAcquisitionState,
    Decision,
    DecisionEvidence,
    CameraDecisionEvidence,
    RejectedCandidateEvidence,
    RouteCandidateRejectionEvidence,
    RouteDecisionEvidence,
    TargetContinuityEvidence,
    TargetEvidence,
    TargetingDecisionEvidence,
    TaskProgressSnapshot,
    TaskSnapshot,
    TaskStatus,
    TimingDecisionEvidence,
)
from osrs_bot.verification import (
    CameraPoseResult,
    CameraUiState,
    CameraZoomResult,
    Outcome,
    OutcomeKind,
    VerificationFailureKind,
    VerificationResult,
    VerificationStatus,
)


def _command(sequence: int, name: str) -> CommandEvidence:
    return CommandEvidence(
        command_id=f"cmd-{sequence:08d}",
        sequence=sequence,
        command=name,
        status="PASS",
        write_ok=True,
        ack_received=True,
        accepted=True,
        response_token="OK",
        payload_token=name,
    )


def _receipt() -> InputReceipt:
    names = (
        "ARM",
        "MOVE",
        "MOUSE_DOWN",
        "MOUSE_UP",
        "STOP_ALL",
        "DISARM",
        "STATUS",
    )
    delayed_feedback = DelayedCursorFeedbackEvent(
        plan=1,
        step=1,
        command_dx=1,
        command_dy=0,
        before=ScreenPoint(10, 10),
        last=ScreenPoint(11, 10),
        extra_polls=3,
        elapsed_millis=100,
        first_effect_millis=60,
        complete_effect_millis=60,
        outcome="settled",
    )
    return InputReceipt(
        transaction_id="input-00000001",
        mode="pointer",
        intent_ids=("tree",),
        status="PASS",
        reason="input_transaction_succeeded",
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=True,
        disarm_acknowledged=True,
        firmware_status_acknowledged=True,
        firmware_status=FirmwareSafetyStatus(False, 0, 0),
        commands=tuple(_command(index, name) for index, name in enumerate(names, 1)),
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        cursor_feedback=CursorFeedbackEvidence(
            wait_count=1,
            settled_count=1,
            max_extra_polls=3,
            max_elapsed_millis=100,
            last_wait=delayed_feedback,
        ),
        pointer_motion=PointerMotionEvidence(
            plan_count=1,
            planned_step_count=3,
            executed_step_count=3,
            requested_start=ScreenPoint(10, 10),
            requested_target=ScreenPoint(100, 80),
            last_planned_target=ScreenPoint(100, 80),
            settled_target=ScreenPoint(100, 80),
            direct_distance_px=114.0,
            planned_path_length_px=121.0,
            planned_duration_seconds=0.21,
            style="cubic_bezier",
            context="object",
            seed="73",
            decision_id="aim-73",
            control_points=(ScreenPoint(40, 45), ScreenPoint(75, 70)),
        ),
    )


def _expanded_capabilities() -> InputCapabilities:
    return InputCapabilities(
        schema_version="input_capabilities.v2",
        protocol_version="arduino_hid.v2",
        firmware_version="2.0.0",
        pointer=True,
        mouse=True,
        relative_move=True,
        max_move_delta=20,
        buttons=frozenset({"left", "right", "middle"}),
        button_down_up=True,
        click=True,
        max_click_hold_ms=250,
        keyboard=True,
        key_set="basic",
        key_press=True,
        max_key_press_ms=250,
        hold_keys=True,
        max_hold_keys_ms=250,
        camera_key_hold=True,
        camera_keys=frozenset({"left", "right", "up", "down"}),
        max_camera_hold_ms=600,
        wheel=True,
        max_wheel_step=3,
        arm=True,
        watchdog=True,
        watchdog_ms=1000,
        stop_all=True,
        disarm=True,
        status=True,
        reset_safe=True,
    )


def _camera_hold_receipt() -> InputReceipt:
    commands = tuple(
        _command(index, name)
        for index, name in enumerate(
            ("ARM", "CAMERA_HOLD", "STOP_ALL", "DISARM", "STATUS"),
            1,
        )
    )
    return InputReceipt(
        transaction_id="input-camera-0001",
        mode="camera_hold",
        intent_ids=("camera-locked-target",),
        status="PASS",
        reason="input_transaction_succeeded",
        connected=True,
        arm_acknowledged=True,
        stop_all_acknowledged=True,
        disarm_acknowledged=True,
        firmware_status_acknowledged=True,
        firmware_status=FirmwareSafetyStatus(False, 0, 0),
        commands=commands,
        unresolved_command_count=0,
        failed_command_count=0,
        ack_missing_count=0,
        ledger_complete=True,
        ledger_closed=True,
        backend_closed=True,
        required_capabilities=(
            RequiredInputCapabilities.camera_hold("left", 600),
        ),
        negotiated_capabilities=_expanded_capabilities(),
        activation_boundary=InputActivationBoundary(
            operation=InputOperation.CAMERA_KEY_HOLD,
            command="CAMERA_HOLD",
            expected_pid=4321,
            attempted=True,
            acknowledged=True,
            command_sequence=2,
            direction="left",
            requested_duration_millis=600,
            applied_duration_millis=600,
            source_geometry_frame_id="geometry-100",
            before_yaw=1200,
            before_pitch=800,
            before_zoom=360,
        ),
        camera_verification=CameraInputVerificationEvidence(
            kind="camera_pose_changed",
            status="pass",
            reason="camera_pose_changed",
            observed_tick=101,
            before_yaw=1200,
            after_yaw=920,
            before_pitch=800,
            after_pitch=800,
            before_zoom=360,
            after_zoom=360,
            before_geometry_frame_id="geometry-100",
            after_geometry_frame_id="geometry-101",
            ui_state_unchanged=True,
        ),
    )


def _target(key: str, x: int) -> TargetEvidence:
    return TargetEvidence(
        key=key,
        name="Tree",
        object_id=1276,
        action="Chop down",
        source_tick=100,
        geometry_frame_id="geometry-100",
        point=ScreenPoint(x, 80),
        bounds=ScreenBounds(x - 5, 75, 11, 11),
        world_location=WorldPoint(3200 + x, 3200, 0),
        distance=2,
    )


class EngineFrameTests(unittest.TestCase):
    def test_legacy_frame_construction_defaults_additive_observability(self) -> None:
        frame = EngineFramePublisher().publish(
            stage=EngineStage.OBSERVED,
            task=TaskSnapshot("probe", TaskStatus.RUNNING, "ready"),
        )

        self.assertEqual(ObservabilityEvidence(), frame.observability)
        self.assertEqual([], frame.to_dict()["observability"]["timing"]["phases"])
        with self.assertRaises(FrozenInstanceError):
            frame.observability = ObservabilityEvidence()  # type: ignore[misc]

    def test_legacy_receipt_defaults_new_camera_capability_evidence(self) -> None:
        payload = _receipt().to_dict()

        self.assertEqual([], payload["requiredCapabilities"])
        self.assertIsNone(payload["negotiatedCapabilities"])
        self.assertIsNone(payload["activationBoundary"])
        self.assertIsNone(payload["cameraVerification"])

    def test_camera_capability_receipt_survives_engine_frame_exactly(self) -> None:
        receipt = _camera_hold_receipt()
        frame = EngineFramePublisher().publish(
            stage=EngineStage.VERIFIED,
            task=TaskSnapshot("probe", TaskStatus.RUNNING, "camera"),
            last_execution_status="SENT",
            last_execution_reason="action_sent",
            last_execution_activation_attempted=True,
            last_execution_receipt=receipt,
        )

        payload = frame.to_dict()["lastExecution"]["receipt"]
        self.assertTrue(receipt.successful)
        self.assertEqual(
            "camera_key_hold",
            payload["requiredCapabilities"][0]["operation"],
        )
        self.assertEqual(
            "input_capabilities.v2",
            payload["negotiatedCapabilities"]["schema"],
        )
        self.assertEqual(
            "arduino_hid.v2",
            payload["negotiatedCapabilities"]["protocolVersion"],
        )
        self.assertEqual(
            600,
            payload["negotiatedCapabilities"]["maxCameraHoldMs"],
        )
        self.assertEqual(3, payload["negotiatedCapabilities"]["maxWheelStep"])
        boundary = payload["activationBoundary"]
        self.assertEqual("CAMERA_HOLD", boundary["command"])
        self.assertEqual(600, boundary["requestedDurationMillis"])
        self.assertEqual(600, boundary["appliedDurationMillis"])
        self.assertTrue(boundary["acknowledged"])
        verification = payload["cameraVerification"]
        self.assertEqual("camera_pose_changed", verification["kind"])
        self.assertEqual("pass", verification["status"])
        self.assertEqual(920, verification["afterYaw"])
        self.assertTrue(verification["uiStateUnchanged"])
        self.assertTrue(frame.cleanup.safe)

    def test_observability_is_distinct_from_behavior_selected_timing(self) -> None:
        evidence = ObservabilityEvidence(
            timing=TimingEvidence().record(TimingPhase.TASK_DECISION, 4),
            wait_state=WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,
            wait_elapsed_millis=2,
            observed_wait_states=(WaitState.WAITING_FOR_NEXT_SCENE_UPDATE,),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.DECIDED,
            task=TaskSnapshot("probe", TaskStatus.RUNNING, "ready"),
            observability=evidence,
        )

        payload = frame.to_dict()
        self.assertIsNone(payload["timing"])
        self.assertEqual("engine_observability.v1", payload["observability"]["schema"])
        self.assertEqual(
            "WAITING_FOR_NEXT_SCENE_UPDATE",
            payload["observability"]["waitState"],
        )

    def test_publisher_retains_one_deeply_immutable_diagnostic_truth(self) -> None:
        selected = _target("tree:selected", 100)
        eligible = _target("tree:eligible", 140)
        rejected = _target("tree:rejected", 180)
        evidence = DecisionEvidence(
            selected=selected,
            eligible=(selected, eligible),
            rejected=(
                RejectedCandidateEvidence(rejected, ("geometry_unavailable",)),
            ),
            route=RouteDecisionEvidence(
                progress_tiles=22.0,
                remaining_tiles=41.0,
                lateral_deviation_tiles=0.25,
                selected_step_id="castle_path_3",
                selected_location=WorldPoint(3212, 3224, 0),
                requested_distance_tiles=16.0,
                expected_progress_tiles=15.5,
                actual_progress_tiles=14.75,
                skipped_guidance_points=("castle_path_1", "castle_path_2"),
                mandatory_next_step_id="castle_door",
                projected_route_points=(
                    ScreenPoint(90, 90),
                    ScreenPoint(120, 100),
                ),
                projected_route_labels=("castle_path_2:normal", "castle_path_3:turn"),
                mandatory_route_points=(ScreenPoint(120, 100),),
                skipped_route_points=(ScreenPoint(90, 90),),
                selected_screen_point=ScreenPoint(120, 100),
                candidate_rejections=(
                    RouteCandidateRejectionEvidence(
                        "castle_door", ("shortcut_unsupported",)
                    ),
                ),
            ),
            camera=CameraDecisionEvidence(
                classification="well_framed",
                desired_region=ScreenBounds(70, 60, 500, 320),
                target_point=ScreenPoint(120, 100),
                action="none",
                hold_millis=0,
                route_direction_bias="right",
                correction_distance_px=0.0,
                framing_context="interaction",
                source_tick=100,
                geometry_frame_id="geometry-100",
                target_bounds=ScreenBounds(80, 60, 40, 40),
                edge_clearance_px=12.5,
                required_edge_margin_px=72,
                lookahead_points=(ScreenPoint(140, 110), ScreenPoint(180, 120)),
                lookahead_bounds=ScreenBounds(120, 90, 100, 50),
                yaw_error_units=-1_200,
                screen_correction_x_px=36.0,
                screen_correction_y_px=-12.0,
                correction_attempt=2,
                correction_limit=8,
                cumulative_hold_millis=410,
            ),
            targeting=TargetingDecisionEvidence(
                geometry_source="clickbox",
                shape_bounds=ScreenBounds(80, 60, 40, 40),
                inset_region=ScreenBounds(84, 64, 32, 32),
                candidate_points=(ScreenPoint(90, 80), ScreenPoint(105, 88)),
                selected_point=ScreenPoint(105, 88),
                selected_score=18.5,
                previous_points=(ScreenPoint(90, 80),),
                decision_id="aim-73",
                seed=73,
                shape_polygon=(
                    ScreenPoint(80, 60),
                    ScreenPoint(120, 60),
                    ScreenPoint(120, 100),
                    ScreenPoint(80, 100),
                ),
            ),
            timing=TimingDecisionEvidence(
                decision_id="timing-73",
                seed=73,
                pre_move_delay_seconds=0.04,
                settle_delay_seconds=0.08,
                pre_click_delay_seconds=0.05,
                post_action_delay_seconds=0.11,
                route_pause_seconds=0.0,
            ),
        )
        specification = VerificationSpec(
            VerificationKind.ITEM_QUANTITY_INCREASED,
            before_tick=100,
            deadline_tick=110,
            item_id=1511,
            before_quantity=0,
            source_session_id="session-1",
        )
        decision = Decision(
            "verify_logs",
            "interact with exact configured resource",
            Action(
                ActionKind.INTERACT_OBJECT,
                "Chop configured resource",
                100,
                option="Chop down",
                target_key=selected.key,
                target_name=selected.name,
                target_id=selected.object_id,
                screen_point=selected.point,
                verification=specification,
                source_session_id="session-1",
            ),
            evidence=evidence,
        )
        route_progress = TaskProgressSnapshot("route", 3, 19)
        cycle_progress = TaskProgressSnapshot("cycles", 0, 1)
        snapshot = TaskSnapshot(
            "woodcut_bank",
            TaskStatus.RUNNING,
            "verify_logs",
            definition_id="lumbridge_west_trees_v1",
            profile_id="default_woodcut_one_cycle_v1",
            progress=route_progress,
            route_step="castle_path_3",
            route_progress=route_progress,
            cycle_progress=cycle_progress,
            target_continuity=TargetContinuityEvidence(
                locked_target_key="tree:selected",
                locked_tick=96,
                last_seen_tick=100,
                incomplete_omission_frames=1,
                retention_reason="incomplete census retained exact identity",
                last_unlock_reason="previous target authoritatively absent",
            ),
        )
        observation = ObservationReference(
            100,
            datetime.now(timezone.utc),
            "frame-100",
            "geometry-100",
            "session-1",
            1234,
            ScreenBounds(0, 0, 765, 503),
        )
        verification = VerificationResult(
            VerificationStatus.PASS,
            "item_quantity_increased",
            Outcome(OutcomeKind.ITEM_QUANTITY_INCREASED, 101),
        )
        publisher = EngineFramePublisher()

        frame = publisher.publish(
            stage=EngineStage.VERIFIED,
            task=snapshot,
            observation=observation,
            decision=decision,
            safety_checks=(SafetyCheck("pre_move", "pre_move_safe", True),),
            pending_verification=specification,
            last_verification=verification,
            last_execution_status="SENT",
            last_execution_reason="action_sent",
            last_execution_activation_attempted=True,
            last_execution_receipt=_receipt(),
        )

        self.assertEqual(1, frame.sequence)
        self.assertIs(frame, publisher.latest())
        self.assertIs(selected, frame.selected_target)
        self.assertEqual((selected, eligible), frame.eligible_targets)
        self.assertEqual(("geometry_unavailable",), frame.rejected_targets[0].rejection_codes)
        self.assertTrue(frame.cleanup.safe)
        payload = frame.to_dict()
        self.assertEqual("engine_frame.v1", payload["schema"])
        self.assertEqual("lumbridge_west_trees_v1", payload["task"]["definitionId"])
        self.assertEqual("castle_path_3", payload["task"]["routeStep"])
        self.assertEqual(3, payload["task"]["routeProgress"]["current"])
        self.assertEqual(0, payload["task"]["cycleProgress"]["current"])
        self.assertEqual(
            {
                "lockedTargetKey": "tree:selected",
                "lockedTick": 96,
                "lastSeenTick": 100,
                "incompleteOmissionFrames": 1,
                "retentionReason": "incomplete census retained exact identity",
                "lastUnlockReason": "previous target authoritatively absent",
            },
            payload["task"]["targetContinuity"],
        )
        self.assertEqual("tree:selected", payload["selectedTarget"]["key"])
        self.assertEqual(16.0, payload["route"]["requestedTileDistance"])
        self.assertEqual(
            {"x": 120, "y": 100}, payload["route"]["selectedScreenPoint"]
        )
        self.assertEqual(
            [
                {
                    "stepId": "castle_door",
                    "rejectionCodes": ["shortcut_unsupported"],
                }
            ],
            payload["route"]["candidateRejections"],
        )
        self.assertEqual(4, len(payload["targeting"]["authoritativePolygon"]))
        self.assertEqual("well_framed", payload["camera"]["framingClassification"])
        self.assertEqual("interaction", payload["camera"]["framingContext"])
        self.assertEqual(100, payload["camera"]["sourceTick"])
        self.assertEqual("geometry-100", payload["camera"]["geometryFrameId"])
        self.assertEqual(
            {"x": 80, "y": 60, "width": 40, "height": 40},
            payload["camera"]["targetShapeBounds"],
        )
        self.assertEqual(12.5, payload["camera"]["edgeClearancePx"])
        self.assertEqual(72, payload["camera"]["requiredEdgeMarginPx"])
        self.assertEqual(59.5, payload["camera"]["marginShortfallPx"])
        self.assertEqual(2, payload["camera"]["lookaheadPointCount"])
        self.assertEqual(
            [{"x": 140, "y": 110}, {"x": 180, "y": 120}],
            payload["camera"]["lookaheadPoints"],
        )
        self.assertEqual(
            {"x": 120, "y": 90, "width": 100, "height": 50},
            payload["camera"]["lookaheadBounds"],
        )
        self.assertEqual(-1_200, payload["camera"]["yawErrorUnits"])
        self.assertEqual(
            {"x": 36.0, "y": -12.0},
            payload["camera"]["screenCorrection"],
        )
        self.assertEqual(2, payload["camera"]["correctionAttempt"])
        self.assertEqual(8, payload["camera"]["correctionLimit"])
        self.assertEqual(410, payload["camera"]["cumulativeHoldMillis"])
        self.assertEqual("cubic_bezier", payload["pointer"]["style"])
        self.assertEqual(0.05, payload["timing"]["preClickDelaySeconds"])
        self.assertEqual(
            {"x": 3300, "y": 3200, "plane": 0},
            payload["selectedTarget"]["worldLocation"],
        )
        self.assertEqual(2, payload["selectedTarget"]["distance"])
        self.assertEqual("item_quantity_increased", payload["lastVerification"]["outcome"]["kind"])
        self.assertIsNone(payload["lastVerification"]["failureKind"])
        self.assertIn("cameraYaw", payload["observation"])
        self.assertIsNone(payload["observation"]["textInputActive"])
        self.assertIn("keyHoldMillis", payload["decision"]["action"])
        self.assertEqual(1511, payload["pendingVerification"]["itemId"])
        self.assertEqual(0, payload["pendingVerification"]["beforeQuantity"])
        self.assertTrue(payload["lastExecution"]["activationAttempted"])
        cursor_feedback = payload["lastExecution"]["receipt"]["cursorFeedback"]
        self.assertEqual(1, cursor_feedback["waitCount"])
        self.assertEqual("settled", cursor_feedback["lastWait"]["outcome"])
        self.assertEqual(60, cursor_feedback["lastWait"]["firstEffectMillis"])
        self.assertTrue(payload["cleanup"]["safe"])
        with self.assertRaises(FrozenInstanceError):
            frame.stage = EngineStage.TERMINAL  # type: ignore[misc]
        self.assertFalse(hasattr(frame, "__dict__"))

    def test_camera_decision_diagnostics_are_defaulted_and_bounded(self) -> None:
        required = dict(
            classification="usable",
            desired_region=None,
            target_point=None,
            action="none",
            hold_millis=0,
            route_direction_bias="none",
            correction_distance_px=0.0,
        )

        evidence = CameraDecisionEvidence(**required)

        self.assertEqual("interaction", evidence.framing_context)
        self.assertEqual((), evidence.lookahead_points)
        self.assertEqual(0, evidence.correction_attempt)
        self.assertEqual(CameraAcquisitionState.IDLE, evidence.acquisition_state)
        self.assertEqual(250, evidence.capability_max_hold_millis)
        self.assertEqual(0, evidence.response_sample_count)
        with self.assertRaises(ValueError):
            CameraDecisionEvidence(**required, edge_clearance_px=float("inf"))
        with self.assertRaises(ValueError):
            CameraDecisionEvidence(**required, required_edge_margin_px=-1)
        with self.assertRaises(ValueError):
            CameraDecisionEvidence(
                **required,
                lookahead_points=tuple(ScreenPoint(index, index) for index in range(17)),
            )
        with self.assertRaises(ValueError):
            CameraDecisionEvidence(
                **required, correction_attempt=9, correction_limit=8
            )

    def test_observation_reference_retains_same_observation_presentation_facts(self) -> None:
        captured = datetime.now(timezone.utc)
        location = WorldPoint(3195, 3248, 0)
        inventory = InventoryObservation(
            items=(InventoryItem(0, 1511, 3, "Logs"),),
            slot_count=28,
            occupied_slots=1,
            free_slots=27,
            known=True,
        )
        observation = Observation(
            player=PlayerObservation(),
            location=location,
            plane=0,
            inventory=inventory,
            nearby_objects=(),
            menus=(),
            widgets=WidgetObservation(),
            canvas_bounds=ScreenBounds(10, 20, 765, 503),
            player_screen_point=ScreenPoint(390, 350),
            game_state="LOGGED_IN",
            timestamp=captured,
            tick=101,
            status="PASS",
            fresh=True,
            cache_wall_clock_fresh=True,
            scene_playable=True,
            session_id="session-101",
            client_focused=True,
            client_process_id=4321,
            assembled_at=captured,
            frame_id="frame-101",
            geometry_frame_id="geometry-101",
            source_coherent=True,
            camera_zoom=512,
            text_input_active=True,
        )

        reference = ObservationReference.from_observation(
            observation,
            behavior_config=BehaviorConfig(
                camera_zoom_desired_min=320,
                camera_zoom_desired_max=448,
            ),
        )
        payload = reference.to_dict()

        self.assertEqual("LOGGED_IN", reference.game_state)
        self.assertTrue(reference.loaded_scene)
        self.assertTrue(reference.client_focused)
        self.assertTrue(reference.text_input_active)
        self.assertTrue(reference.fresh)
        self.assertTrue(reference.cache_wall_clock_fresh)
        self.assertTrue(reference.source_coherent)
        self.assertIs(location, reference.player_location)
        self.assertEqual(ScreenPoint(390, 350), reference.player_screen_point)
        self.assertEqual(0, reference.player_plane)
        self.assertEqual(512, reference.camera_zoom)
        self.assertEqual("too_close", reference.camera_zoom_classification)
        self.assertIs(inventory, reference.inventory)
        self.assertEqual(
            {"x": 3195, "y": 3248, "plane": 0},
            payload["playerLocation"],
        )
        self.assertEqual({"x": 390, "y": 350}, payload["playerScreenPoint"])
        self.assertEqual(0, payload["playerPlane"])
        self.assertEqual(512, payload["cameraZoom3d"])
        self.assertEqual("too_close", payload["cameraZoomClassification"])
        self.assertTrue(payload["textInputActive"])
        self.assertEqual(
            {"min": 320, "max": 448},
            payload["desiredCameraZoomRange"],
        )
        self.assertEqual(
            {
                "slot": 0,
                "itemId": 1511,
                "quantity": 3,
                "name": "Logs",
            },
            payload["inventory"]["items"][0],
        )
        self.assertNotIn("count", payload["sceneCensus"])
        self.assertEqual(
            {"schema": "observation_pipeline_evidence.v1"},
            payload["observationPipeline"],
        )
        with self.assertRaises(FrozenInstanceError):
            reference.inventory.known = False  # type: ignore[misc,union-attr]
        with self.assertRaises(FrozenInstanceError):
            reference.inventory.items[0].quantity = 4  # type: ignore[misc,union-attr]

    def test_engine_frame_serializes_additive_census_and_pipeline_evidence(self) -> None:
        reference = ObservationReference(
            source_tick=174,
            captured_at=datetime.now(timezone.utc),
            frame_id="frame-174",
            geometry_frame_id="geometry-174",
            session_id="session-174",
            process_id=4321,
            canvas_bounds=ScreenBounds(0, 0, 765, 503),
            scene_census=SceneCensusEvidence(
                source_schema="scene_object_census.v2",
                metadata_present=True,
                complete=False,
                authoritative_absence_eligible=False,
                response_cap_hit=True,
                source_cap_hit=False,
                count=100,
                returned=64,
                requested_priority_object_ids=(1276,),
                requested_priority_object_keys=("tree:1",),
                reported_priority_object_ids=(1276,),
                returned_priority_object_ids=(1276,),
                priority_objects_complete=True,
                reported_priority_object_keys=("tree:1",),
                returned_priority_object_keys=("tree:1",),
                priority_keys_complete=True,
                duplicate_row_count=2,
                duplicate_group_count=1,
                conflicting_duplicate_keys=("conflict:1",),
                parsed_object_count=63,
            ),
            pipeline=ObservationPipelineEvidence(
                source_schema="world_model_pipeline.v1",
                request_id="request-174",
                response_bytes=31250,
                cache_hit=True,
                refresh_sequence=12,
                operation_counts=(("definitionLookups", 2),),
                query_diagnostics_schema="client_thread_query_diagnostics.v1",
                query_lane="world_model",
                query_status="PASS",
                active_request_count=1,
                pending_request_count=1,
                max_queue_depth=2,
                coalesced_request_count=3,
                serialization_passes=1,
                serialized_bytes_reused_for_write=True,
                endpoint_queue_schema=(
                    "plugin_snapshot_endpoint_queue_diagnostics.v1"
                ),
                endpoint_worker_limit=4,
                endpoint_pending_capacity=8,
                endpoint_active_worker_count=1,
                endpoint_pending_request_count=2,
                endpoint_busy_rejection_count=3,
                endpoint_executor_state="RUNNING",
            ),
        )

        payload = EngineFramePublisher().publish(
            stage=EngineStage.OBSERVED,
            task=TaskSnapshot("probe", TaskStatus.RUNNING, "observed"),
            observation=reference,
        ).to_dict()["observation"]

        self.assertEqual(
            "scene_census_evidence.v1", payload["sceneCensus"]["schema"]
        )
        self.assertTrue(payload["sceneCensus"]["responseCapHit"])
        self.assertFalse(
            payload["sceneCensus"]["authoritativeAbsenceEligible"]
        )
        self.assertEqual(
            ["conflict:1"],
            payload["sceneCensus"]["conflictingDuplicateKeys"],
        )
        self.assertEqual(
            ["tree:1"], payload["sceneCensus"]["requestedPriorityObjectKeys"]
        )
        self.assertEqual(
            "observation_pipeline_evidence.v1",
            payload["observationPipeline"]["schema"],
        )
        self.assertEqual(31250, payload["observationPipeline"]["responseBytes"])
        self.assertEqual(
            {"definitionLookups": 2},
            payload["observationPipeline"]["operationCounts"],
        )
        self.assertEqual(
            2,
            payload["observationPipeline"]["queryDiagnostics"]["maxDepth"],
        )
        self.assertEqual(
            3,
            payload["observationPipeline"]["queryDiagnostics"][
                "coalescedCount"
            ],
        )
        self.assertEqual(
            1, payload["observationPipeline"]["serializationPasses"]
        )
        self.assertTrue(
            payload["observationPipeline"]["serializedBytesReusedForWrite"]
        )
        self.assertEqual(
            4,
            payload["observationPipeline"]["endpointQueueDiagnostics"][
                "workerLimit"
            ],
        )
        self.assertEqual(
            3,
            payload["observationPipeline"]["endpointQueueDiagnostics"][
                "snapshotBusyRejectionCount"
            ],
        )

    def test_activation_attempted_requires_a_boolean(self) -> None:
        with self.assertRaises(TypeError):
            EngineFramePublisher().publish(
                stage=EngineStage.EXECUTED,
                task=TaskSnapshot("probe", TaskStatus.RUNNING, "ready"),
                last_execution_activation_attempted=1,  # type: ignore[arg-type]
            )

    def test_typed_verification_failure_is_serialized(self) -> None:
        failure = VerificationResult(
            VerificationStatus.FAIL,
            "item_quantity_unchanged_at_deadline",
            failure_kind=(
                VerificationFailureKind.ITEM_QUANTITY_UNCHANGED_AT_DEADLINE
            ),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.VERIFIED,
            task=TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree"),
            last_verification=failure,
        )

        payload = frame.to_dict()["lastVerification"]
        self.assertEqual(
            "item_quantity_unchanged_at_deadline",
            payload["failureKind"],
        )
        self.assertIsNone(payload["outcome"])

    def test_verified_camera_pose_result_survives_as_numeric_diagnostics(self) -> None:
        result = VerificationResult(
            VerificationStatus.PASS,
            "camera_pose_changed",
            Outcome(
                OutcomeKind.CAMERA_POSE_CHANGED,
                102,
                CameraPoseResult(
                    camera_key="left",
                    before_yaw=50,
                    after_yaw=16_300,
                    yaw_delta=-134,
                    before_pitch=1_024,
                    after_pitch=1_110,
                    pitch_delta=86,
                    before_geometry_frame_id="geometry-100",
                    after_geometry_frame_id="geometry-102",
                ),
            ),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.VERIFIED,
            task=TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree"),
            last_verification=result,
        )

        pose = frame.to_dict()["lastVerification"]["outcome"]["cameraPoseResult"]
        self.assertEqual("left", pose["cameraKey"])
        self.assertEqual(-134, pose["yawDelta"])
        self.assertEqual(86, pose["pitchDelta"])
        self.assertTrue(pose["geometryFrameChanged"])
        self.assertIsNone(
            frame.to_dict()["lastVerification"]["outcome"]["cameraZoomResult"]
        )

    def test_verified_camera_zoom_result_retains_pose_identity_ui_and_geometry(self) -> None:
        ui_state = CameraUiState(
            bank_known=False,
            bank_open=False,
            bank_pin_open=False,
            bank_readable=False,
            dialogue_active=False,
            dialogue_type="none",
            text_input_active=False,
        )
        result = VerificationResult(
            VerificationStatus.PASS,
            "camera_zoom_changed",
            Outcome(
                OutcomeKind.CAMERA_ZOOM_CHANGED,
                103,
                camera_zoom_result=CameraZoomResult(
                    wheel_amount=1,
                    before_zoom=300,
                    after_zoom=324,
                    zoom_delta=24,
                    before_yaw=640,
                    after_yaw=640,
                    before_pitch=900,
                    after_pitch=900,
                    before_process_id=4321,
                    after_process_id=4321,
                    before_location=WorldPoint(3195, 3248, 0),
                    after_location=WorldPoint(3195, 3248, 0),
                    source_session_id="session-1",
                    before_geometry_frame_id="geometry-100",
                    after_geometry_frame_id="geometry-103",
                    before_ui_state=ui_state,
                    after_ui_state=ui_state,
                ),
            ),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.VERIFIED,
            task=TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree"),
            last_verification=result,
        )

        zoom = frame.to_dict()["lastVerification"]["outcome"]["cameraZoomResult"]
        self.assertEqual(1, zoom["wheelAmount"])
        self.assertEqual(24, zoom["zoom3dDelta"])
        self.assertTrue(zoom["yawUnchanged"])
        self.assertTrue(zoom["pitchUnchanged"])
        self.assertTrue(zoom["processUnchanged"])
        self.assertTrue(zoom["playerLocationUnchanged"])
        self.assertTrue(zoom["geometryFrameChanged"])
        self.assertTrue(zoom["uiStateUnchanged"])
        self.assertFalse(zoom["afterUiState"]["textInputActive"])

    def test_sequence_is_monotonic_and_wait_returns_only_newer_frames(self) -> None:
        publisher = EngineFramePublisher()
        snapshot = TaskSnapshot("probe", TaskStatus.RUNNING, "ready")

        first = publisher.publish(stage=EngineStage.OBSERVED, task=snapshot)
        self.assertIs(first, publisher.wait_for_newer(0, timeout=0))
        self.assertIsNone(publisher.wait_for_newer(first.sequence, timeout=0))
        second = publisher.publish(stage=EngineStage.TERMINAL, task=snapshot)

        self.assertEqual(first.sequence + 1, second.sequence)
        self.assertIs(second, publisher.wait_for_newer(first.sequence, timeout=0))

    def test_cleanup_is_derived_from_authoritative_receipt_not_status_text(self) -> None:
        safe = CleanupEvidence.from_receipt(_receipt())
        absent = CleanupEvidence.from_receipt(None)

        self.assertTrue(safe.safe)
        self.assertFalse(absent.attempted)
        self.assertFalse(absent.safe)

    def test_pending_verification_serializes_every_condition_needed_to_inspect_it(self) -> None:
        snapshot = TaskSnapshot("probe", TaskStatus.RUNNING, "verify")
        route = VerificationSpec(
            VerificationKind.ROUTE_TRANSITION,
            before_tick=10,
            deadline_tick=20,
            before_location=WorldPoint(3205, 3208, 1),
            target_location=WorldPoint(3205, 3208, 2),
            expected_plane=2,
            target_radius=1,
            source_session_id="session-1",
            dialogue_prompt_contains="which floor",
            dialogue_option_contains="climb up",
        )
        interface = VerificationSpec(
            VerificationKind.INTERFACE_OPENED,
            before_tick=20,
            deadline_tick=25,
            expected_plane=2,
            source_session_id="session-1",
            interface_name=BANK_INTERFACE_NAME,
        )
        zoom = VerificationSpec(
            VerificationKind.CAMERA_ZOOM_CHANGED,
            before_tick=25,
            deadline_tick=30,
            before_location=WorldPoint(3195, 3248, 0),
            source_session_id="session-1",
            before_camera_yaw=640,
            before_camera_pitch=900,
            before_camera_zoom=300,
            camera_zoom_amount=-1,
            before_process_id=4321,
            before_geometry_frame_id="geometry-25",
            before_bank_known=False,
            before_bank_open=False,
            before_bank_pin_open=False,
            before_bank_readable=False,
            before_dialogue_active=False,
            before_dialogue_type="none",
            before_text_input_active=False,
        )
        publisher = EngineFramePublisher()

        route_payload = publisher.publish(
            stage=EngineStage.DECIDED,
            task=snapshot,
            pending_verification=route,
        ).to_dict()["pendingVerification"]
        interface_payload = publisher.publish(
            stage=EngineStage.DECIDED,
            task=snapshot,
            pending_verification=interface,
        ).to_dict()["pendingVerification"]
        zoom_payload = publisher.publish(
            stage=EngineStage.DECIDED,
            task=snapshot,
            pending_verification=zoom,
        ).to_dict()["pendingVerification"]

        self.assertEqual(
            {"x": 3205, "y": 3208, "plane": 1},
            route_payload["beforeLocation"],
        )
        self.assertEqual(2, route_payload["expectedPlane"])
        self.assertEqual(1, route_payload["targetRadius"])
        self.assertEqual("which floor", route_payload["dialoguePromptContains"])
        self.assertEqual("climb up", route_payload["dialogueOptionContains"])
        self.assertEqual(BANK_INTERFACE_NAME, interface_payload["interfaceName"])
        self.assertEqual(300, zoom_payload["beforeCameraZoom3d"])
        self.assertEqual(-1, zoom_payload["cameraZoomAmount"])
        self.assertEqual(4321, zoom_payload["beforeProcessId"])
        self.assertFalse(zoom_payload["beforeTextInputActive"])
        self.assertEqual(2, interface_payload["expectedPlane"])


if __name__ == "__main__":
    unittest.main()
