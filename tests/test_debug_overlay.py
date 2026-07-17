from __future__ import annotations

import ast
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import osrs_bot.debug_overlay as overlay_module
from osrs_bot.debug_overlay import (
    CANDIDATE_COLOR,
    ELIGIBLE_COLOR,
    PASSIVE_EX_STYLE_MASK,
    REJECTED_COLOR,
    SELECTED_COLOR,
    SW_SHOWNOACTIVATE,
    WS_EX_NOACTIVATE,
    DebugOverlay,
    build_overlay_scene,
    configure_passive_window,
)
from osrs_bot.engine_frame import EngineFramePublisher, EngineStage, ObservationReference
from osrs_bot.input_coordinator import (
    CommandEvidence,
    FirmwareSafetyStatus,
    InputReceipt,
    PointerMotionEvidence,
)
from osrs_bot.model import Action, ActionKind, ScreenBounds, ScreenPoint, WorldPoint
from osrs_bot.task_contract import (
    CameraDecisionEvidence,
    Decision,
    DecisionEvidence,
    RejectedCandidateEvidence,
    RouteCandidateRejectionEvidence,
    RouteDecisionEvidence,
    TargetEvidence,
    TargetingDecisionEvidence,
    TaskSnapshot,
    TaskStatus,
    TimingDecisionEvidence,
)


ROOT = Path(__file__).resolve().parents[1]


def _target(key: str, x: int, *, tick: int = 50) -> TargetEvidence:
    return TargetEvidence(
        key,
        "Tree",
        1276,
        "Chop down",
        tick,
        "geometry-50",
        ScreenPoint(x, 100),
        ScreenBounds(x - 5, 95, 11, 11),
    )


def _frame(*, observation_tick: int = 50):
    selected = _target("selected", 100)
    eligible = _target("eligible", 150)
    rejected = _target("rejected", 200)
    decision = Decision(
        "find_tree",
        "selected exact resource",
        Action(ActionKind.WAIT, "Wait", 50),
        DecisionEvidence(
            selected,
            (selected, eligible),
            (RejectedCandidateEvidence(rejected, ("ambiguous_aim",)),),
        ),
    )
    publisher = EngineFramePublisher()
    return publisher.publish(
        stage=EngineStage.DECIDED,
        task=TaskSnapshot("woodcut_bank", TaskStatus.RUNNING, "find_tree"),
        observation=ObservationReference(
            source_tick=observation_tick,
            captured_at=datetime.now(timezone.utc),
            frame_id=f"frame-{observation_tick}",
            geometry_frame_id="geometry-50",
            session_id="session-1",
            process_id=1234,
            canvas_bounds=ScreenBounds(0, 0, 765, 503),
            game_state="LOGGED_IN",
            loaded_scene=True,
            client_focused=True,
            fresh=True,
            cache_wall_clock_fresh=True,
            source_coherent=True,
        ),
        decision=decision,
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


def _pointer_receipt() -> InputReceipt:
    names = (
        "ARM",
        "MOVE",
        "MOUSE_DOWN",
        "MOUSE_UP",
        "STOP_ALL",
        "DISARM",
        "STATUS",
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
        pointer_motion=PointerMotionEvidence(
            plan_count=1,
            planned_step_count=1,
            executed_step_count=1,
            requested_start=ScreenPoint(40, 200),
            requested_target=ScreenPoint(110, 110),
            last_planned_target=ScreenPoint(110, 110),
            settled_target=ScreenPoint(111, 109),
            direct_distance_px=114.0,
            planned_path_length_px=121.5,
            planned_duration_seconds=0.287,
            style="cubic_bezier",
            context="object_interaction",
            seed="42",
            decision_id="aim-42",
            control_points=(ScreenPoint(60, 185), ScreenPoint(100, 140)),
        ),
    )


def _diagnostic_frame():
    source = _frame()
    assert source.decision is not None
    selected = source.decision.evidence.selected
    eligible = source.decision.evidence.eligible
    rejected = source.decision.evidence.rejected
    decision = Decision(
        "find_tree",
        "selected varied exact resource point",
        Action(ActionKind.WAIT, "Wait", 50),
        DecisionEvidence(
            selected,
            eligible,
            rejected,
            route=RouteDecisionEvidence(
                progress_tiles=18.0,
                remaining_tiles=37.0,
                lateral_deviation_tiles=0.4,
                selected_step_id="west_trees_entry",
                selected_location=WorldPoint(3165, 3228, 0),
                requested_distance_tiles=14.0,
                expected_progress_tiles=13.5,
                actual_progress_tiles=12.0,
                skipped_guidance_points=("west_wall_corner", "west_field"),
                mandatory_next_step_id="castle_stairs",
                candidate_rejections=(
                    RouteCandidateRejectionEvidence(
                        "castle_stairs", ("shortcut_unsupported",)
                    ),
                ),
            ),
            camera=CameraDecisionEvidence(
                classification="barely_visible",
                desired_region=ScreenBounds(130, 90, 420, 290),
                target_point=ScreenPoint(100, 100),
                action="yaw_left",
                hold_millis=190,
                route_direction_bias="west",
                correction_distance_px=48.0,
                framing_context="interaction",
                source_tick=50,
                geometry_frame_id="geometry-50",
                target_bounds=ScreenBounds(80, 80, 60, 70),
                edge_clearance_px=18.0,
                required_edge_margin_px=72,
                lookahead_points=(ScreenPoint(150, 110), ScreenPoint(210, 125)),
                lookahead_bounds=ScreenBounds(140, 95, 90, 50),
                yaw_error_units=-900,
                correction_attempt=2,
                correction_limit=8,
                cumulative_hold_millis=390,
            ),
            targeting=TargetingDecisionEvidence(
                geometry_source="clickbox",
                shape_bounds=ScreenBounds(80, 80, 60, 70),
                inset_region=ScreenBounds(88, 88, 44, 54),
                candidate_points=(
                    ScreenPoint(98, 104),
                    ScreenPoint(110, 110),
                    ScreenPoint(122, 126),
                ),
                selected_point=ScreenPoint(110, 110),
                selected_score=0.91,
                previous_points=(ScreenPoint(98, 104),),
                decision_id="aim-42",
                seed=42,
                rejected_reasons=("near_edge",),
            ),
            timing=TimingDecisionEvidence(
                decision_id="aim-42",
                seed=42,
                pre_move_delay_seconds=0.031,
                settle_delay_seconds=0.062,
                pre_click_delay_seconds=0.027,
                post_action_delay_seconds=0.119,
                route_pause_seconds=0.044,
            ),
        ),
    )
    return EngineFramePublisher().publish(
        stage=EngineStage.DECIDED,
        task=source.task,
        observation=source.observation,
        decision=decision,
        last_execution_receipt=_pointer_receipt(),
    )


class _Native:
    def __init__(self) -> None:
        self.style = 0
        self.root_hwnd = 456
        self.resolved: list[int] = []
        self.style_reads: list[int] = []
        self.style_writes: list[int] = []
        self.positioned: list[tuple[int, ScreenBounds]] = []
        self.shown: list[tuple[int, int]] = []

    def get_root_window(self, hwnd: int) -> int:
        self.resolved.append(hwnd)
        return self.root_hwnd

    def get_extended_style(self, hwnd: int) -> int:
        self.style_reads.append(hwnd)
        return self.style

    def set_extended_style(self, hwnd: int, style: int) -> None:
        self.style_writes.append(hwnd)
        self.style = style

    def position_topmost_no_activate(self, hwnd: int, bounds: ScreenBounds) -> None:
        self.positioned.append((hwnd, bounds))

    def show_no_activate(self, hwnd: int) -> None:
        self.shown.append((hwnd, SW_SHOWNOACTIVATE))


class DebugOverlayTests(unittest.TestCase):
    def test_scene_uses_only_current_engine_geometry_and_required_colors(self) -> None:
        frame = _frame()

        compact = build_overlay_scene(frame)
        detailed = build_overlay_scene(frame, show_rejected=True)

        self.assertEqual(
            (SELECTED_COLOR, ELIGIBLE_COLOR),
            tuple(item.color for item in compact.rectangles),
        )
        self.assertEqual(
            (SELECTED_COLOR, ELIGIBLE_COLOR, REJECTED_COLOR),
            tuple(item.color for item in detailed.rectangles),
        )
        self.assertTrue(any("woodcut_bank" in line for line in compact.text_lines))
        self.assertTrue(any("decision:" in line for line in compact.text_lines))
        self.assertTrue(any("target:" in line for line in compact.text_lines))
        self.assertTrue(any("safety:" in line for line in compact.text_lines))

    def test_stale_target_geometry_is_suppressed_instead_of_reprojected(self) -> None:
        scene = build_overlay_scene(_frame(observation_tick=51), show_rejected=True)

        self.assertEqual((), scene.rectangles)
        self.assertTrue(any("target:" in line for line in scene.text_lines))
        self.assertTrue(any("geometry suppressed" in line for line in scene.text_lines))
        self.assertFalse(any("100,100" in line for line in scene.text_lines))

    def test_new_engine_diagnostics_are_visualized_without_selection_logic(self) -> None:
        scene = build_overlay_scene(_diagnostic_frame())

        labels = tuple(item.label for item in scene.rectangles)
        self.assertIn("desired camera framing", labels)
        self.assertIn("camera target shape", labels)
        self.assertIn("camera lookahead bounds", labels)
        self.assertIn("target clickbox", labels)
        self.assertIn("usable aim", labels)
        self.assertEqual(3, sum(point.color == CANDIDATE_COLOR for point in scene.points))
        self.assertTrue(any(point.label == "camera target" for point in scene.points))
        self.assertEqual(
            2,
            sum(point.label == "camera lookahead" for point in scene.points),
        )
        self.assertTrue(any(point.label == "selected aim" for point in scene.points))
        self.assertTrue(any(point.label == "settled" for point in scene.points))
        self.assertTrue(
            any(line.label == "recent pointer path" for line in scene.polylines)
        )
        for prefix in ("route:", "camera:", "aim:", "pointer:", "timing:"):
            self.assertTrue(any(line.startswith(prefix) for line in scene.text_lines))
        self.assertTrue(any("request 14.0 tiles" in line for line in scene.text_lines))
        self.assertTrue(
            any(
                "rejected 1 (castle_stairs:shortcut_unsupported)" in line
                for line in scene.text_lines
            )
        )
        self.assertTrue(any("candidates 3" in line for line in scene.text_lines))
        camera_line = next(
            line for line in scene.text_lines if line.startswith("camera:")
        )
        self.assertIn("interaction barely_visible", camera_line)
        self.assertIn("correction 48.0 px", camera_line)
        self.assertIn("clearance 18.0/72 px", camera_line)
        self.assertIn("attempt 2/8", camera_line)
        self.assertIn("cumulative 390 ms", camera_line)

    def test_diagnostic_geometry_is_suppressed_with_stale_observation(self) -> None:
        frame = _diagnostic_frame()
        assert frame.observation is not None
        stale = EngineFramePublisher().publish(
            stage=frame.stage,
            task=frame.task,
            observation=ObservationReference(
                source_tick=51,
                captured_at=frame.observation.captured_at,
                frame_id="frame-51",
                geometry_frame_id="geometry-51",
                session_id=frame.observation.session_id,
                process_id=frame.observation.process_id,
                canvas_bounds=frame.observation.canvas_bounds,
                game_state=frame.observation.game_state,
                loaded_scene=True,
                client_focused=True,
                fresh=True,
                cache_wall_clock_fresh=True,
                source_coherent=True,
            ),
            decision=frame.decision,
            last_execution_receipt=frame.last_execution_receipt,
        )

        scene = build_overlay_scene(stale)

        self.assertEqual((), scene.rectangles)
        self.assertEqual((), scene.points)
        self.assertEqual((), scene.polylines)
        self.assertIn("aim: geometry suppressed", scene.text_lines)
        self.assertFalse(any("110,110" in line for line in scene.text_lines))

    def test_current_camera_geometry_does_not_require_selected_target_geometry(self) -> None:
        source = _diagnostic_frame()
        assert source.decision is not None
        camera = source.decision.evidence.camera
        decision = Decision(
            "navigate_to_bank",
            "frame route lookahead",
            Action(ActionKind.WAIT, "Wait", 50),
            DecisionEvidence(camera=camera),
        )
        frame = EngineFramePublisher().publish(
            stage=EngineStage.DECIDED,
            task=source.task,
            observation=source.observation,
            decision=decision,
        )

        scene = build_overlay_scene(frame)

        self.assertTrue(any(point.label == "camera target" for point in scene.points))
        self.assertEqual(
            2,
            sum(point.label == "camera lookahead" for point in scene.points),
        )
        self.assertTrue(
            any(item.label == "desired camera framing" for item in scene.rectangles)
        )

    def test_route_projection_contract_fields_render_when_present(self) -> None:
        points = []
        polylines = []
        overlay_module._append_route_geometry(
            SimpleNamespace(
                projected_route_points=(
                    ScreenPoint(100, 200),
                    ScreenPoint(180, 190),
                    ScreenPoint(250, 170),
                ),
                mandatory_route_points=(ScreenPoint(250, 170),),
                skipped_route_points=(ScreenPoint(180, 190),),
                selected_screen_point=ScreenPoint(250, 170),
            ),
            points,
            polylines,
        )

        self.assertEqual("route corridor", polylines[0].label)
        self.assertEqual(3, len(polylines[0].points))
        self.assertEqual(
            ("mandatory", "", "route target"), tuple(point.label for point in points)
        )

    def test_wall_clock_stale_frame_becomes_text_only_without_new_publication(self) -> None:
        frame = _frame()

        scene = build_overlay_scene(
            frame,
            now=frame.observation.captured_at + timedelta(seconds=2.001),
            show_rejected=True,
        )

        self.assertEqual((), scene.rectangles)
        self.assertIn("STALE", scene.text_lines[0])
        self.assertTrue(any("last known target" in line for line in scene.text_lines))

    def test_terminal_frame_is_text_only_and_banner_is_bounded(self) -> None:
        source = _frame()
        publisher = EngineFramePublisher()
        terminal = publisher.publish(
            stage=EngineStage.TERMINAL,
            task=source.task,
            observation=source.observation,
            decision=source.decision,
            blocker="runtime_limit_reached",
        )

        visible = build_overlay_scene(
            terminal,
            now=terminal.published_at + timedelta(seconds=1),
        )
        bounded = build_overlay_scene(
            terminal,
            now=terminal.published_at + timedelta(seconds=8.001),
        )

        self.assertEqual((), visible.rectangles)
        self.assertIn("target geometry cleared", visible.text_lines[0])
        self.assertEqual((), bounded.rectangles)
        self.assertEqual(1, len(bounded.text_lines))
        self.assertIn("terminal summary retained", bounded.text_lines[0])

    def test_disconnected_presentation_is_text_only_with_last_frame_age(self) -> None:
        frame = _frame()
        presentation = SimpleNamespace(
            state=SimpleNamespace(value="DISCONNECTED"),
            run_id="run-000001",
            geometry_allowed=False,
        )

        scene = build_overlay_scene(
            frame,
            presentation=presentation,
            bound_run_id="run-000001",
            now=frame.observation.captured_at + timedelta(seconds=3),
        )

        self.assertEqual((), scene.rectangles)
        self.assertEqual(
            "DISCONNECTED — last live frame was 3.0 seconds ago",
            scene.text_lines[0],
        )

    def test_native_host_proves_click_through_and_no_activate_flags(self) -> None:
        native = _Native()
        bounds = ScreenBounds(10, 20, 300, 200)

        proof = configure_passive_window(123, bounds, native=native)

        self.assertTrue(proof.valid)
        self.assertEqual(456, proof.hwnd)
        self.assertEqual([123], native.resolved)
        self.assertEqual([456, 456], native.style_reads)
        self.assertEqual([456], native.style_writes)
        self.assertEqual(PASSIVE_EX_STYLE_MASK, native.style & PASSIVE_EX_STYLE_MASK)
        self.assertTrue(native.style & WS_EX_NOACTIVATE)
        self.assertEqual([(456, bounds)], native.positioned)
        self.assertEqual([(456, SW_SHOWNOACTIVATE)], native.shown)

    def test_missing_top_level_host_fails_before_styles_or_show(self) -> None:
        native = _Native()
        native.root_hwnd = 0

        with self.assertRaisesRegex(RuntimeError, "top-level window"):
            configure_passive_window(
                123, ScreenBounds(10, 20, 300, 200), native=native
            )

        self.assertEqual([], native.positioned)
        self.assertEqual([], native.shown)
        self.assertEqual([], native.style_reads)
        self.assertEqual([], native.style_writes)

    def test_runtime_render_failure_is_surfaced_during_cleanup(self) -> None:
        publisher = EngineFramePublisher()
        overlay = DebugOverlay(publisher)
        overlay._error = RuntimeError("render failed")

        with self.assertRaisesRegex(RuntimeError, "runtime failed: render failed"):
            overlay.stop()

    def test_start_timeout_requests_cleanup_before_returning(self) -> None:
        release = threading.Event()

        class SlowOverlay(DebugOverlay):
            def _run(self) -> None:
                release.wait(1.0)

        overlay = SlowOverlay(EngineFramePublisher())
        with self.assertRaisesRegex(RuntimeError, "initialize in time"):
            overlay.start(timeout_seconds=0.001)

        self.assertTrue(overlay._stop.is_set())
        release.set()
        assert overlay._thread is not None
        overlay._thread.join(1.0)
        self.assertFalse(overlay._thread.is_alive())

    def test_overlay_source_has_no_control_or_input_authority(self) -> None:
        path = ROOT / "osrs_bot" / "debug_overlay.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        self.assertNotIn("arduino", imported)
        self.assertNotIn("input_coordinator", imported)
        self.assertNotIn("safety", imported)
        for forbidden in (
            "SafetyGate",
            "ObservationClient",
            "InputCoordinator",
            "execute_pointer",
            "execute_key",
            ".bind(",
        ):
            self.assertNotIn(forbidden, source)

    def test_overlay_collects_tk_objects_on_the_host_thread(self) -> None:
        path = ROOT / "osrs_bot" / "debug_overlay.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        run = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_run"
        )
        finally_nodes = [
            node.finalbody
            for node in ast.walk(run)
            if isinstance(node, ast.Try) and node.finalbody
        ]
        self.assertTrue(finally_nodes)
        cleanup_source = "\n".join(
            ast.unparse(statement)
            for body in finally_nodes
            for statement in body
        )
        self.assertIn("root.destroy()", cleanup_source)
        self.assertNotIn("gc.collect()", cleanup_source)


if __name__ == "__main__":
    unittest.main()
