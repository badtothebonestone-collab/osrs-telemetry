from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from osrs_bot.application import (
    ApplicationSnapshot,
    DemonstrationReference,
    LifecycleState,
)
from osrs_bot.gui import (
    GUI_PRESENTATION_SCHEMA,
    _cleanup_detail,
    _camera_episode_text,
    _connection_mapping,
    _demonstration_terminal_status,
    _inspection_text,
    _lifecycle_detail,
    _movement_diagnostics_text,
    _plain_blocker,
    _presentation_scoped_status,
    _presentation_state_text,
    _receipt_text,
    _runelite_found,
)


ROOT = Path(__file__).resolve().parents[1]


def _application(*, execute_requested: bool = False) -> ApplicationSnapshot:
    return ApplicationSnapshot(
        lifecycle=LifecycleState.IDLE,
        run_id=None,
        capture_id=None,
        active_run_id=None,
        active_capture_id=None,
        execute_requested=execute_requested,
        profile_id=None,
        runtime_control=None,
        engine_frame=None,
        runtime_statistics=None,
        blockers=(),
        recent_demonstration=None,
        started_at=None,
        finished_at=None,
    )


class GuiPresentationTests(unittest.TestCase):
    def test_importing_gui_has_no_window_or_worker_side_effect(self) -> None:
        self.assertEqual("osrs_operator_gui.v1", GUI_PRESENTATION_SCHEMA)

    def test_demonstration_status_distinguishes_duration_from_early_stop(self) -> None:
        complete = DemonstrationReference(
            Path("complete"),
            True,
            "VERIFIED_WITH_GAPS",
            (),
            "duration_elapsed",
            60.0,
        )
        early = DemonstrationReference(
            Path("early"),
            True,
            "VERIFIED_WITH_GAPS",
            (),
            "scene_identity_or_loaded_state_changed",
            60.0,
        )

        self.assertEqual(
            "Complete — requested 60s duration elapsed",
            _demonstration_terminal_status(complete, LifecycleState.COMPLETE),
        )
        self.assertEqual(
            "Stopped early — scene identity or loaded state changed",
            _demonstration_terminal_status(early, LifecycleState.COMPLETE),
        )

    def test_login_and_launch_results_unwrap_authoritative_connection(self) -> None:
        result = {
            "state": "launched",
            "reason": "ready",
            "launched": True,
            "connection": {
                "endpointHealthy": True,
                "processId": 1234,
                "loadedScene": True,
            },
        }

        connection = _connection_mapping(result)

        self.assertTrue(connection["endpointHealthy"])
        self.assertEqual(1234, connection["processId"])
        self.assertEqual("launched", connection["operationState"])
        self.assertEqual("ready", connection["operationReason"])

    def test_login_result_unwraps_nested_connection_dataclass(self) -> None:
        class Connection:
            @staticmethod
            def to_dict() -> dict[str, object]:
                return {
                    "runeLiteFound": True,
                    "endpointHealthy": True,
                    "exactProcessBinding": True,
                }

        connection = _connection_mapping(
            {"recovery": {"status": "PASS"}, "connection": Connection()}
        )

        self.assertTrue(connection["runeLiteFound"])
        self.assertTrue(connection["endpointHealthy"])
        self.assertTrue(connection["exactProcessBinding"])

    def test_exact_binding_logically_proves_runelite_is_found(self) -> None:
        self.assertTrue(_runelite_found({"exactProcessBinding": True}))
        self.assertFalse(
            _runelite_found(
                {"runeLiteFound": False, "exactProcessBinding": False}
            )
        )

    def test_presentation_retains_exact_blocker_code(self) -> None:
        code = "input_process_lease_unavailable: COM6 owned"
        rendered = _plain_blocker(code)

        self.assertIn("Another process owns the Arduino lease", rendered)
        self.assertIn(code, rendered)

    def test_loaded_scene_blocker_does_not_assume_the_user_is_logged_out(self) -> None:
        rendered = _plain_blocker("loaded_scene_not_ready")

        self.assertIn("Waiting for a coherent loaded scene", rendered)
        self.assertIn("scene transition", rendered)
        self.assertIn("loaded_scene_not_ready", rendered)

    def test_compact_receipt_reads_enclosing_activation_attempt(self) -> None:
        nested_receipt = {
            "status": "PASS",
            "commands": [{"command": "MOVE"}],
            "unresolvedCommandCount": 0,
            # A legacy/malformed nested value must not override the enclosing
            # EngineFrame execution fact.
            "activationAttempted": False,
        }

        self.assertIn(
            "activation=yes",
            _receipt_text(nested_receipt, activation_attempted=True),
        )
        self.assertIn(
            "activation=no",
            _receipt_text(nested_receipt, activation_attempted=False),
        )
        self.assertIn("activation=unknown", _receipt_text(nested_receipt))

    def test_compact_receipt_surfaces_negotiated_camera_hold_evidence(self) -> None:
        rendered = _receipt_text(
            {
                "status": "PASS",
                "mode": "camera_hold",
                "commands": [
                    {"command": command}
                    for command in (
                        "ARM",
                        "CAMERA_HOLD",
                        "STOP_ALL",
                        "DISARM",
                        "STATUS",
                    )
                ],
                "unresolvedCommandCount": 0,
                "requiredCapabilities": [
                    {
                        "operation": "camera_key_hold",
                        "cameraDirection": "left",
                        "cameraHoldMs": 600,
                    }
                ],
                "negotiatedCapabilities": {
                    "protocolVersion": "arduino_hid.v2",
                    "firmwareVersion": "2.0.0",
                    "maxCameraHoldMs": 600,
                    "maxWheelStep": 3,
                },
                "activationBoundary": {
                    "command": "CAMERA_HOLD",
                    "attempted": True,
                    "acknowledged": True,
                    "requestedDurationMillis": 600,
                    "appliedDurationMillis": 600,
                },
                "cameraVerification": {
                    "kind": "camera_pose_changed",
                    "status": "pass",
                },
            },
            activation_attempted=True,
        )

        self.assertIn("mode=camera_hold", rendered)
        self.assertIn("requires=camera_key_hold", rendered)
        self.assertIn("arduino_hid.v2/fw=2.0.0", rendered)
        self.assertIn("camera<=600ms", rendered)
        self.assertIn("wheel<=3", rendered)
        self.assertIn("boundary=CAMERA_HOLD/duration=600->600ms/ACK", rendered)
        self.assertIn("verify=camera_pose_changed/pass", rendered)

    def test_compact_receipt_surfaces_exact_signed_wheel_boundary(self) -> None:
        rendered = _receipt_text(
            {
                "status": "PASS",
                "mode": "camera_zoom",
                "commands": [{"command": "WHEEL"}],
                "unresolvedCommandCount": 0,
                "requiredCapabilities": [{"operation": "camera_zoom"}],
                "activationBoundary": {
                    "command": "WHEEL",
                    "attempted": True,
                    "acknowledged": True,
                    "requestedWheelAmount": -2,
                    "appliedWheelAmount": -2,
                },
                "cameraVerification": {
                    "kind": "camera_zoom_changed",
                    "status": "pending",
                },
            },
            activation_attempted=True,
        )

        self.assertIn("requires=camera_zoom", rendered)
        self.assertIn("boundary=WHEEL/wheel=-2->-2/ACK", rendered)
        self.assertIn("verify=camera_zoom_changed/pending", rendered)

    def test_expected_waits_do_not_render_as_arduino_command_failures(self) -> None:
        waiting = SimpleNamespace(
            state=SimpleNamespace(value="WAITING_FOR_NEXT_SCENE_UPDATE"),
            display_state=SimpleNamespace(
                value="WAITING_FOR_NEXT_SCENE_UPDATE"
            ),
        )
        failed = SimpleNamespace(
            state=SimpleNamespace(value="ARDUINO_COMMAND_FAILED"),
            # Failure must bypass even a contradictory delayed display copy.
            display_state=SimpleNamespace(value="READY"),
        )

        self.assertEqual(
            "WAITING_FOR_NEXT_SCENE_UPDATE",
            _presentation_scoped_status(
                False,
                waiting,
                {
                    "WAITING_FOR_NEXT_SCENE_UPDATE",
                    "ARDUINO_COMMAND_FAILED",
                },
            ),
        )
        self.assertNotEqual(
            "ARDUINO_COMMAND_FAILED", _presentation_state_text(waiting)
        )
        self.assertEqual(
            "ARDUINO_COMMAND_FAILED",
            _presentation_scoped_status(
                False,
                failed,
                {"ARDUINO_COMMAND_FAILED"},
            ),
        )

    def test_observe_cleanup_is_not_mislabeled_as_failure(self) -> None:
        self.assertEqual(
            "Not required in Observe Only.",
            _cleanup_detail({"attempted": False, "safe": False}, _application()),
        )
        self.assertIn(
            "could not be authoritatively confirmed",
            _cleanup_detail(
                {"attempted": True, "safe": False},
                _application(execute_requested=True),
            ),
        )

    def test_lifecycle_copy_distinguishes_requested_and_acknowledged_pause(self) -> None:
        self.assertIn(
            "awaiting a no-input boundary",
            _lifecycle_detail(LifecycleState.PAUSE_REQUESTED, None),
        )
        self.assertIn(
            "currently paused",
            _lifecycle_detail(LifecycleState.PAUSED, None),
        )

    def test_movement_diagnostics_reports_engine_frame_decisions(self) -> None:
        rendered = _movement_diagnostics_text(
            {
                "route": {
                    "currentProgressTiles": 12.25,
                    "remainingDistanceTiles": 48.5,
                    "selectedRouteTarget": "west_wall_corner",
                    "requestedTileDistance": 16,
                    "actualProgressTiles": 14.75,
                    "skippedGuidancePoints": ["guide-a", "guide-b"],
                    "candidateRejections": [
                        {
                            "stepId": "castle_door",
                            "rejectionCodes": ["shortcut_unsupported"],
                        }
                    ],
                    "fallbackReason": None,
                },
                "camera": {
                    "framingClassification": "well_framed",
                    "framingContext": "route",
                    "desiredFramingRegion": {
                        "x": 120,
                        "y": 80,
                        "width": 420,
                        "height": 280,
                    },
                    "targetScreenPosition": {"x": 480, "y": 260},
                    "cameraAction": "left",
                    "holdDurationMillis": 180,
                    "routeDirectionBias": "east",
                    "correctionDistancePx": 86,
                    "edgeClearancePx": 93,
                    "requiredEdgeMarginPx": 24,
                    "lookaheadPointCount": 3,
                    "correctionAttempt": 1,
                    "correctionLimit": 10,
                    "cumulativeHoldMillis": 180,
                },
                "targeting": {
                    "authoritativeGeometrySource": "clickbox",
                    "targetShapeBounds": {
                        "x": 496,
                        "y": 305,
                        "width": 48,
                        "height": 64,
                    },
                    "insetAimRegion": {
                        "x": 502,
                        "y": 311,
                        "width": 36,
                        "height": 52,
                    },
                    "candidatePointCount": 9,
                    "selectedPoint": {"x": 512, "y": 331},
                    "selectedCandidateScore": 0.91,
                    "previousSelectedPoints": [
                        {"x": 508, "y": 329},
                        {"x": 519, "y": 338},
                    ],
                    "rejectedPointReasons": ["near_edge"],
                    "selectionSeed": "run-seed-12345",
                    "decisionId": "aim-0042",
                },
                "selectedTarget": {
                    "sourceTick": 420,
                    "geometryFrameId": "geometry-420",
                },
                "observation": {
                    "sourceTick": 420,
                    "geometryFrameId": "geometry-420",
                    "cameraZoom3d": 512,
                    "cameraZoomClassification": "too_close",
                    "desiredCameraZoomRange": {"min": 320, "max": 448},
                },
                "pointer": {
                    "directDistancePx": 505.25,
                    "plannedPathLengthPx": 522.5,
                    "plannedDurationSeconds": 0.47,
                    "style": "cubic_bezier",
                    "settledTarget": {"x": 512, "y": 331},
                    "seed": "pointer-seed",
                },
                "timing": {
                    "preMoveDelaySeconds": 0.04,
                    "settleDelaySeconds": 0.07,
                    "preClickDelaySeconds": 0.03,
                    "postActionDelaySeconds": 0.11,
                    "routePauseSeconds": 0.06,
                },
                "lastVerification": {
                    "outcome": {
                        "cameraPoseResult": {
                            "yawDelta": -237,
                            "pitchDelta": 86,
                        }
                    }
                },
            }
        )

        self.assertIn("ROUTE", rendered)
        self.assertIn("target=west_wall_corner", rendered)
        self.assertIn("request=16t", rendered)
        self.assertIn("skipped=2", rendered)
        self.assertIn("rejected=1", rendered)
        self.assertIn("why=castle_door:shortcut_unsupported", rendered)
        self.assertIn("CAMERA", rendered)
        self.assertIn("frame=well_framed", rendered)
        self.assertIn("zoom=512/too_close[320-448]", rendered)
        self.assertIn("ctx=route", rendered)
        self.assertIn("correction=86px", rendered)
        self.assertIn("edge=93/24px", rendered)
        self.assertIn("ahead=3", rendered)
        self.assertIn("pulse=1/10", rendered)
        self.assertIn("total=180ms", rendered)
        self.assertIn("result=yaw-237,pitch86", rendered)
        self.assertIn("region=(120,80 420x280)", rendered)
        self.assertIn("TARGET", rendered)
        self.assertIn("geometry=clickbox", rendered)
        self.assertIn("shape=(496,305 48x64)", rendered)
        self.assertIn("inset=(502,311 36x52)", rendered)
        self.assertIn("selected=(512,331)", rendered)
        self.assertIn("previous=2", rendered)
        self.assertIn("rejected=1", rendered)
        self.assertIn("POINTER", rendered)
        self.assertIn("style=cubic_bezier", rendered)
        self.assertIn("TIMING", rendered)
        self.assertIn("pre-click=0.03s", rendered)

    def test_camera_episode_surfaces_zoom_delta_without_claiming_wheel_input(self) -> None:
        rendered = _camera_episode_text(
            {
                "observedInputMethod": "keyboard",
                "intentClassification": "action_linked",
                "associationConfidence": "high",
                "clickEventSequence": 17,
                "cameraPoseDelta": {
                    "yaw": 320,
                    "pitch": -40,
                    "zoom3d": -64,
                },
            }
        )

        self.assertIn("delta=yaw:320/pitch:-40/zoom:-64", rendered)
        self.assertIn("method=keyboard", rendered)
        self.assertNotIn("wheel", rendered.casefold())

    def test_movement_diagnostics_is_bounded_and_tolerates_missing_data(self) -> None:
        rendered = _movement_diagnostics_text(
            {
                "route": {"selectedRouteTarget": "x" * 500},
                "camera": "malformed",
                "targeting": None,
                "pointer": [],
                "timing": {},
            }
        )

        lines = rendered.splitlines()
        self.assertEqual(5, len(lines))
        self.assertTrue(all(len(line) <= 230 for line in lines))
        self.assertIn("CAMERA  -", lines)
        self.assertIn("TARGET  -", lines)

    def test_movement_diagnostics_suppresses_only_stale_target_geometry(self) -> None:
        rendered = _movement_diagnostics_text(
            {
                "observation": {
                    "sourceTick": 421,
                    "geometryFrameId": "geometry-421",
                },
                "selectedTarget": {
                    "sourceTick": 420,
                    "geometryFrameId": "geometry-420",
                },
                "camera": {
                    "sourceTick": 421,
                    "geometryFrameId": "geometry-421",
                    "framingClassification": "barely_visible",
                    "cameraAction": "right",
                    "holdDurationMillis": 232,
                    "correctionDistancePx": 184,
                    "targetScreenPosition": {"x": 731, "y": 442},
                    "desiredFramingRegion": {
                        "x": 120,
                        "y": 80,
                        "width": 420,
                        "height": 280,
                    },
                },
                "targeting": {
                    "authoritativeGeometrySource": "clickbox",
                    "targetShapeBounds": {
                        "x": 700,
                        "y": 400,
                        "width": 62,
                        "height": 74,
                    },
                    "insetAimRegion": {
                        "x": 706,
                        "y": 406,
                        "width": 50,
                        "height": 62,
                    },
                    "candidatePointCount": 7,
                    "selectedPoint": {"x": 731, "y": 442},
                    "selectedCandidateScore": 0.88,
                    "previousSelectedPoints": [{"x": 728, "y": 438}],
                    "rejectedPointReasons": ["near_edge", "ui_overlap"],
                },
            }
        )

        camera_line, target_line = rendered.splitlines()[1:3]
        self.assertIn("frame=barely_visible", camera_line)
        self.assertIn("action=right", camera_line)
        self.assertIn("hold=232ms", camera_line)
        self.assertIn("correction=184px", camera_line)
        self.assertIn("target=(731,442)", camera_line)
        self.assertIn("region=(120,80 420x280)", camera_line)
        self.assertIn("geometry=clickbox", target_line)
        self.assertIn("shape=suppressed (awaiting fresh decision)", target_line)
        self.assertIn("inset=suppressed (awaiting fresh decision)", target_line)
        self.assertIn("selected=suppressed (awaiting fresh decision)", target_line)
        self.assertIn("previous=1", target_line)
        self.assertIn("rejected=2", target_line)
        self.assertNotIn("(700,400 62x74)", target_line)
        self.assertNotIn("(706,406 50x62)", target_line)
        self.assertNotIn("(731,442)", target_line)
        self.assertTrue(all(len(line) <= 230 for line in rendered.splitlines()))

    def test_movement_diagnostics_suppresses_only_stale_camera_geometry(self) -> None:
        rendered = _movement_diagnostics_text(
            {
                "observation": {
                    "sourceTick": 421,
                    "geometryFrameId": "geometry-421",
                },
                "selectedTarget": {
                    "sourceTick": 421,
                    "geometryFrameId": "geometry-421",
                },
                "camera": {
                    "sourceTick": 420,
                    "geometryFrameId": "geometry-420",
                    "framingClassification": "barely_visible",
                    "targetScreenPosition": {"x": 731, "y": 442},
                    "desiredFramingRegion": {
                        "x": 120,
                        "y": 80,
                        "width": 420,
                        "height": 280,
                    },
                },
                "targeting": {
                    "authoritativeGeometrySource": "clickbox",
                    "targetShapeBounds": {
                        "x": 700,
                        "y": 400,
                        "width": 62,
                        "height": 74,
                    },
                    "insetAimRegion": {
                        "x": 706,
                        "y": 406,
                        "width": 50,
                        "height": 62,
                    },
                    "selectedPoint": {"x": 731, "y": 442},
                },
            }
        )

        camera_line, target_line = rendered.splitlines()[1:3]
        self.assertIn("target=suppressed (awaiting fresh decision)", camera_line)
        self.assertIn("region=suppressed (awaiting fresh decision)", camera_line)
        self.assertNotIn("(731,442)", camera_line)
        self.assertNotIn("(120,80 420x280)", camera_line)
        self.assertIn("shape=(700,400 62x74)", target_line)
        self.assertIn("inset=(706,406 50x62)", target_line)
        self.assertIn("selected=(731,442)", target_line)
        self.assertNotIn("suppressed (awaiting fresh decision)", target_line)

    def test_movement_diagnostics_does_not_infer_staleness_from_partial_provenance(self) -> None:
        rendered = _movement_diagnostics_text(
            {
                "observation": {"sourceTick": 421},
                "selectedTarget": {"sourceTick": 420},
                "camera": {
                    "targetScreenPosition": {"x": 731, "y": 442},
                    "desiredFramingRegion": {
                        "x": 120,
                        "y": 80,
                        "width": 420,
                        "height": 280,
                    },
                },
                "targeting": {
                    "targetShapeBounds": {
                        "x": 700,
                        "y": 400,
                        "width": 62,
                        "height": 74,
                    },
                    "insetAimRegion": {
                        "x": 706,
                        "y": 406,
                        "width": 50,
                        "height": 62,
                    },
                    "selectedPoint": {"x": 731, "y": 442},
                },
            }
        )

        self.assertIn("target=(731,442)", rendered)
        self.assertIn("region=(120,80 420x280)", rendered)
        self.assertIn("shape=(700,400 62x74)", rendered)
        self.assertIn("inset=(706,406 50x62)", rendered)
        self.assertIn("selected=(731,442)", rendered)
        self.assertNotIn("suppressed (awaiting fresh decision)", rendered)

    def test_demonstration_inspection_distinguishes_observed_path_from_targets(self) -> None:
        rendered = _inspection_text(
            {
                "valid": True,
                "status": "VERIFIED",
                "routePoints": [{"x": 3200, "y": 3225, "plane": 0}],
                "manualRouteTargets": [
                    {
                        "clickEventSequence": 12,
                        "manualIntentTarget": {"x": 3203, "y": 3214, "plane": 0},
                        "distanceTiles": 12.1,
                        "intentClassification": "accepted",
                        "confidence": "medium",
                    }
                ],
                "manualRouteReviewTargets": [
                    {
                        "clickEventSequence": 12,
                        "chosenTargetWorld": {"x": 3204, "y": 3215, "plane": 0},
                        "requestedTileDistanceStatus": "same_source_tick_player_sample",
                    }
                ],
                "cameraIntentEpisodes": [
                    {
                        "classification": "mixed",
                        "clickEventSequence": 12,
                        "confidence": "medium",
                    }
                ],
                "timingProfiles": [
                    {"cameraInputDurationMillis": 180, "reviewOnly": True}
                ],
                "timingReviewProfiles": [
                    {
                        "contextMenuOpenToClickMillis": 1313,
                        "reviewOnly": True,
                    }
                ],
            }
        )

        self.assertIn(
            "Observed player path — manual demonstration (review only)", rendered
        )
        self.assertIn("not clicked tiles or the task definition route", rendered)
        self.assertIn("Manual Walk targets — inferred intent (review only)", rendered)
        self.assertIn("click=12", rendered)
        self.assertIn("target=(3204,3215,p0)", rendered)
        self.assertNotIn("target=(3203,3214,p0)", rendered)
        self.assertIn("Camera intent episodes", rendered)
        self.assertIn("method=mixed", rendered)
        self.assertIn("association=action_linked", rendered)
        self.assertIn("confidence=medium", rendered)
        self.assertIn("Reference timing profiles", rendered)
        self.assertIn('"contextMenuOpenToClickMillis":1313', rendered)
        self.assertNotIn('"cameraInputDurationMillis":180', rendered)
        self.assertIn('"reviewOnly":true', rendered)

    def test_camera_episode_presentation_separates_method_and_association(self) -> None:
        rendered = _inspection_text(
            {
                "camera_intent_episodes": [
                    {
                        "observedInputMethod": "middle_drag",
                        "intentClassification": "exploratory_or_unassociated",
                        "associationConfidence": "high",
                        "cameraPoseDelta": {"yaw": 92, "pitch": -11, "zoom3d": 0},
                        "maxDragPathPixels": 48.5,
                        "effectiveCameraChangeObserved": True,
                        "inference": "no nearby semantic action",
                    },
                    {
                        "observedInputMethod": "keyboard",
                        "classification": "ambiguous",
                        "confidence": "low",
                    },
                ]
            }
        )

        self.assertIn("method=middle_drag", rendered)
        self.assertIn("association=exploratory_or_unassociated", rendered)
        self.assertIn("delta=yaw:92/pitch:-11/zoom:0", rendered)
        self.assertIn("drag=48.5px", rendered)
        self.assertIn("effective=yes", rendered)
        self.assertIn("method=keyboard", rendered)
        self.assertIn("association=ambiguous", rendered)

    def test_route_comparison_is_readable_plane_aware_and_optional(self) -> None:
        without_comparison = _inspection_text({"valid": True})
        self.assertNotIn("Manual vs definition route", without_comparison)

        rendered = _inspection_text(
            {
                "routeComparison": {
                    "direction": "woods_to_bank",
                    "status": "comparable",
                    "reason": "manual targets progress forward on this route",
                    "manualTargetCount": 4,
                    "observedPlayerPointCount": 18,
                    "selectedRouteMetrics": {
                        "averageCorridorDeviationTiles": 1.25,
                        "maximumCorridorDeviationTiles": 3.0,
                        "forwardProgressTiles": 42.5,
                        "forwardStepCount": 3,
                        "backtrackingEventCount": 1,
                        "backtrackingTiles": 2.5,
                    },
                    "targetDistanceSummary": {
                        "basis": "consecutive_manual_target_to_target_same_plane",
                        "distancesTiles": [12.1, 6.0, 4.0],
                        "histogram": {"1-4": 1, "5-11": 1, "12-19": 1},
                    },
                    "planeViews": [
                        {
                            "plane": 0,
                            "manualTargets": [
                                {"x": 3198, "y": 3225, "plane": 0},
                                {"x": 3203, "y": 3214, "plane": 0},
                            ],
                            "observedPlayerPath": [
                                {"x": 3200, "y": 3228, "plane": 0}
                            ],
                            "mandatoryDefinitionPoints": [
                                {"x": 3205, "y": 3209, "plane": 0}
                            ],
                        }
                    ],
                }
            }
        )

        self.assertIn("Manual vs definition route (review only)", rendered)
        self.assertIn("direction=woods_to_bank", rendered)
        self.assertIn("clicked=4", rendered)
        self.assertIn("observed=18", rendered)
        self.assertIn("avg-deviation=1.25t", rendered)
        self.assertIn("max-deviation=3.0t", rendered)
        self.assertIn("backtracking=1", rendered)
        self.assertIn("order=3 forward steps", rendered)
        self.assertIn("backtracking distance: 2.5t", rendered)
        self.assertIn("distance histogram", rendered)
        self.assertIn('"12-19":1', rendered)
        self.assertIn("plane 0: manual[2]=(3198,3225,p0) -> (3203,3214,p0)", rendered)
        self.assertIn("observed[1]=(3200,3228,p0)", rendered)
        self.assertIn("mandatory[1]=(3205,3209,p0)", rendered)

    def test_gui_source_has_no_domain_or_input_authority(self) -> None:
        path = ROOT / "osrs_bot" / "gui.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        forbidden_modules = {
            "task",
            "safety",
            "action",
            "login",
            "input_coordinator",
            "arduino",
            "demonstration",
            "debug_overlay",
        }
        self.assertTrue(forbidden_modules.isdisjoint(imported_modules))
        lowered = source.casefold()
        for forbidden in (
            "pyautogui",
            "pydirectinput",
            "serial.tools",
            ".decide(",
            ".apply_verification(",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_public_wrapper_advertises_and_routes_gui(self) -> None:
        source = (ROOT / "run.cmd").read_text(encoding="utf-8")
        self.assertIn('if /I "%MODE%"=="gui" goto gui', source)
        self.assertIn("run.cmd gui", source)
        self.assertIn("python -m osrs_bot.gui", source)


if __name__ == "__main__":
    unittest.main()
