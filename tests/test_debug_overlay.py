from __future__ import annotations

import ast
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from osrs_bot.debug_overlay import (
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
from osrs_bot.model import Action, ActionKind, ScreenBounds, ScreenPoint
from osrs_bot.task_contract import (
    Decision,
    DecisionEvidence,
    RejectedCandidateEvidence,
    TargetEvidence,
    TaskSnapshot,
    TaskStatus,
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
            observation_tick,
            datetime.now(timezone.utc),
            f"frame-{observation_tick}",
            "geometry-50",
            "session-1",
            1234,
            ScreenBounds(0, 0, 765, 503),
        ),
        decision=decision,
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
