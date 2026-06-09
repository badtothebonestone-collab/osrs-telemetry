import sys
import tempfile
import time
import unittest
from pathlib import Path


VIEWER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIEWER_DIR))

import arduino_input_bridge
import arduino_live_mirror
import arduino_mirror_verifier
import input_trace_joiner


class FakeCommandClient:
    def __init__(self):
        self.records = []
        self.closed = False

    def connect(self):
        return {"status": "PASS", "available": True, "connected": True, "port": "COM9", "protocol": "arduino_hid.v1"}

    def _record(self, command, payload, metadata):
        record = {
            "schema": arduino_input_bridge.ARDUINO_ACTION_COMMAND_SCHEMA,
            "kind": "command_sent",
            "command": command,
            "command_kind": command,
            "sent_at_monotonic": time.monotonic(),
            "port": "COM9",
            "protocol": "arduino_hid.v1",
            "payload": payload,
            "ack_received": True,
            "ack_latency_ms": 1.0,
            "probeCommand": False,
            **(metadata or {}),
        }
        record.update(payload)
        self.records.append(record)
        return record

    def send_move(self, dx, dy, *, metadata=None):
        return self._record("MOVE", {"dx": int(dx), "dy": int(dy)}, metadata)

    def send_click(self, button="left", *, hold_ms=40, metadata=None):
        return self._record("CLICK", {"button": button, "hold_ms": hold_ms}, metadata)

    def send_mouse_down(self, button="left", *, metadata=None):
        return self._record("MOUSE_DOWN", {"button": button}, metadata)

    def send_mouse_up(self, button="left", *, metadata=None):
        return self._record("MOUSE_UP", {"button": button}, metadata)

    def close(self):
        self.closed = True


def make_mirror(tmp, fake, **settings):
    mirror = arduino_live_mirror.ArduinoLiveMirror(
        tmp,
        recording_id="r",
        command_client=fake,
        settings=arduino_live_mirror.LiveMirrorSettings(send_interval_ms=0, **settings),
    )
    mirror.start(require_active=True)
    mirror.arm(delay_ms=0)
    return mirror


class ArduinoLiveMirrorTest(unittest.TestCase):
    def test_live_mirror_converts_os_move_to_non_probe_move_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 7, "elapsed_seconds": 1.0, "dx": 12, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["nonProbeActionCommandCount"], 1)
            self.assertEqual(fake.records[0]["command"], "MOVE")
            self.assertFalse(fake.records[0]["probeCommand"])
            self.assertTrue(fake.records[0]["liveMirrorCommand"])
            self.assertEqual(fake.records[0]["sourceInputEventSeq"], 7)

    def test_mirror_starts_disarmed_and_only_sends_after_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = arduino_live_mirror.ArduinoLiveMirror(
                tmp,
                recording_id="r",
                command_client=fake,
                settings=arduino_live_mirror.LiveMirrorSettings(send_interval_ms=0),
            )
            mirror.start()
            mirror.process_input_event({"kind": "click", "event_seq": 1, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.03)
            mirror.arm(delay_ms=0)
            mirror.process_input_event({"kind": "click", "event_seq": 2, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertEqual(summary["droppedEventsByReason"].get("mirror_disarmed"), 1)

    def test_test_window_mode_disarms_after_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = arduino_live_mirror.ArduinoLiveMirror(
                tmp,
                recording_id="r",
                command_client=fake,
                settings=arduino_live_mirror.LiveMirrorSettings(send_interval_ms=0, arm_mode="test_window", test_duration_sec=0.05),
            )
            mirror.start(require_active=True)
            mirror.arm(delay_ms=0, mode="test_window", duration_sec=0.05)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 1, "dx": 4, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.12)
            mirror.process_input_event({"kind": "click", "event_seq": 2, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["armMode"], "test_window")
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertGreaterEqual(summary["droppedEventsByReason"].get("mirror_disarmed", 0), 1)

    def test_recording_persistent_mode_ignores_test_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = arduino_live_mirror.ArduinoLiveMirror(
                tmp,
                recording_id="r",
                command_client=fake,
                settings=arduino_live_mirror.LiveMirrorSettings(send_interval_ms=0, arm_mode="recording_persistent", test_duration_sec=0.05, feedback_suppression_ms=0),
            )
            mirror.start(require_active=True)
            mirror.arm(delay_ms=0, mode="recording_persistent", duration_sec=0.05)
            time.sleep(0.12)
            mirror.process_input_event({"kind": "click", "event_seq": 3, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["armMode"], "recording_persistent")
            self.assertTrue(summary["recordingPersistent"])
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertNotEqual(summary.get("disarmReason"), "test_window_elapsed")

    def test_held_left_button_produces_one_click_not_repeated_clicks(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake)
            base = {"button": "left", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 100, "screen_y": 100}
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 10, **base})
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 11, **base})
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 12, **base})
            mirror.process_input_event({"kind": "mouse_up", "event_seq": 13, **base})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertEqual([record["command"] for record in fake.records], ["CLICK"])

    def test_repeated_polling_click_feedback_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, feedback_suppression_ms=500, click_cooldown_ms=120)
            event = {"kind": "click", "button": "left", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 100, "screen_y": 100}
            for seq in range(1, 12):
                mirror.process_input_event({**event, "event_seq": seq})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertGreaterEqual(summary["feedbackSuppressedButtonEventCount"], 1)

    def test_move_echo_is_suppressed_and_not_mirrored_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, echo_suppression=True, echo_window_ms=500)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 1, "dx": 10, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 2, "dx": 10, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["movementCommandCount"], 1)
            self.assertEqual(summary["echoSuppressedMoveCount"], 1)
            self.assertEqual(summary["echoMatchedCommandCount"], 1)

    def test_click_echo_is_suppressed_and_not_mirrored_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, echo_suppression=True, click_echo_window_ms=500, click_cooldown_ms=0)
            event = {"kind": "click", "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"}
            mirror.process_input_event({**event, "event_seq": 1})
            time.sleep(0.05)
            mirror.process_input_event({**event, "event_seq": 2})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertEqual(summary["echoSuppressedClickCount"], 1)

    def test_validation_menu_row_profile_maps_clicks_and_auto_pauses(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(
                tmp,
                fake,
                mirror_profile="validation_menu_row",
                echo_suppression=True,
                auto_pause_after_menu_selection=True,
                clear_queue_on_menu_selection=True,
            )
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 1, "dx": 20, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            mirror.process_input_event({"kind": "click", "event_seq": 2, "button": "right", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            mirror.process_input_event({"kind": "click", "event_seq": 3, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            mirror.process_input_event({"kind": "click", "event_seq": 4, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["movementCommandCount"], 0)
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertEqual(summary["mapOnlyClickCount"], 2)
            self.assertEqual(fake.records, [])
            self.assertTrue(summary["mirrorAutoPaused"])
            self.assertEqual(summary["autoPauseReason"], "menu_selection")
            self.assertGreaterEqual(summary["droppedEventsByReason"].get("mirror_disarmed", 0), 1)

    def test_map_only_click_policy_records_mapping_without_sending_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, mirror_click_policy="map_only")
            mirror.process_input_event({"kind": "click", "event_seq": 20, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertEqual(summary["mapOnlyClickCount"], 1)
            self.assertEqual(summary["droppedEventsByReason"].get("click_policy_map_only"), 1)
            self.assertEqual(fake.records, [])

    def test_live_unsuppressed_click_sends_and_flags_duplicate_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, mirror_click_policy="live_unsuppressed")
            mirror.process_input_event({"kind": "click", "event_seq": 21, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertEqual(summary["liveClickWithoutSuppressionCount"], 1)
            self.assertEqual(summary["duplicateRiskClickCount"], 1)
            self.assertTrue(fake.records[0]["duplicateClickLikely"])

    def test_live_requires_source_suppression_downgrades_to_map_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, mirror_click_policy="live_requires_source_suppression")
            mirror.process_input_event({"kind": "click", "event_seq": 22, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertEqual(summary["mapOnlyClickCount"], 1)
            self.assertTrue(summary["clickPolicyDowngraded"])
            self.assertEqual(summary["clickPolicyDowngradeReason"], "source_suppression_not_verified")

    def test_arduino_source_only_allows_arduino_attributed_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, mirror_click_policy="arduino_source_only")
            mirror.process_input_event(
                {
                    "kind": "click",
                    "event_seq": 23,
                    "button": "left",
                    "foreground_window_title": "RuneLite",
                    "region": "viewport",
                    "rawInputDevice": {"available": True, "deviceName": "Arduino Leonardo HID Mouse"},
                }
            )
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertEqual(summary["arduinoPhysicalClickCount"], 1)
            self.assertEqual(fake.records[0]["clickOwner"], "arduino_physical_click_source")

    def test_stale_queued_movement_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, drop_move_older_than_ms=1)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 1, "dx": 20, "dy": 0, "monotonic_time": time.monotonic() - 1.0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["movementCommandCount"], 0)
            self.assertEqual(summary["staleCommandsDropped"], 1)

    def test_drag_release_and_middle_mouse_do_not_emit_normal_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake)
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 1, "button": "middle", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 10, "screen_y": 10})
            mirror.process_input_event({"kind": "drag_start", "event_seq": 2, "button": "middle", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 10, "screen_y": 10})
            mirror.process_input_event({"kind": "mouse_up", "event_seq": 3, "button": "middle", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 40, "screen_y": 40})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertGreaterEqual(summary["droppedEventsByReason"].get("drag_release_not_click", 0), 1)

    def test_right_click_produces_one_right_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake)
            base = {"button": "right", "foreground_window_title": "RuneLite", "region": "viewport", "screen_x": 100, "screen_y": 100}
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 1, **base})
            mirror.process_input_event({"kind": "mouse_up", "event_seq": 2, **base})
            time.sleep(0.05)
            mirror.stop()
            self.assertEqual([record["button"] for record in fake.records], ["right"])

    def test_click_cooldown_drops_repeated_same_button_clicks(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, feedback_suppression_ms=0, click_cooldown_ms=500)
            for seq in range(1, 4):
                mirror.process_input_event({"kind": "click", "event_seq": seq, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 1)
            self.assertGreaterEqual(summary["throttledCommandCount"], 1)

    def test_rate_limiter_and_panic_threshold_pause_bursts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, feedback_suppression_ms=0, click_cooldown_ms=0, same_button_cooldown_ms=0, max_clicks_per_second=100, max_button_commands_per_second=100, panic_command_threshold=3)
            for seq in range(1, 8):
                mirror.process_input_event({"kind": "click", "event_seq": seq, "button": "left", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertLessEqual(summary["clickCommandCount"], 3)
            self.assertGreaterEqual(summary["panicStopCount"], 1)

    def test_ui_and_non_runelite_events_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, arm_only_when_runelite_focused=True)
            mirror.process_input_event({"kind": "click", "event_seq": 1, "button": "left", "foreground_window_title": "OSRS Telemetry Control", "region": "viewport"})
            mirror.process_input_event({"kind": "click", "event_seq": 2, "button": "left", "foreground_window_title": "Notepad", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 0)
            self.assertEqual(summary["uiControlEventsDropped"], 1)
            self.assertEqual(summary["foregroundFilteredEventsDropped"], 1)

    def test_down_up_button_mode_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, button_mode="down_up")
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 10, "elapsed_seconds": 1.4, "button": "right", "foreground_window_title": "RuneLite", "region": "viewport"})
            mirror.process_input_event({"kind": "mouse_down", "event_seq": 11, "elapsed_seconds": 1.45, "button": "right", "foreground_window_title": "RuneLite", "region": "viewport"})
            mirror.process_input_event({"kind": "mouse_up", "event_seq": 12, "elapsed_seconds": 1.5, "button": "right", "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            summary = mirror.stop()
            self.assertEqual(summary["clickCommandCount"], 2)
            self.assertEqual([record["command"] for record in fake.records], ["MOUSE_DOWN", "MOUSE_UP"])

    def test_large_movement_chunks_into_multiple_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommandClient()
            mirror = make_mirror(tmp, fake, max_step_px=20)
            mirror.process_input_event({"kind": "mouse_move", "event_seq": 9, "elapsed_seconds": 1.3, "dx": 55, "dy": 0, "foreground_window_title": "RuneLite", "region": "viewport"})
            time.sleep(0.05)
            mirror.stop()
            self.assertGreater(len(fake.records), 1)
            self.assertEqual(sum(record["dx"] for record in fake.records), 55)

    def test_summary_from_commands_flags_click_storm(self):
        commands = [
            {
                "kind": "command_sent",
                "command": "CLICK",
                "command_kind": "CLICK",
                "liveMirrorCommand": True,
                "probeCommand": False,
                "sent_at_monotonic": 1.0 + index * 0.02,
                "sourceInputEventSeq": index,
                "ack_received": True,
            }
            for index in range(30)
        ]
        summary = arduino_live_mirror.build_summary_from_commands(commands)
        self.assertIn("live_mirror_click_storm", summary["liveMirrorSafetyClassifications"])
        self.assertGreater(summary["maxClickCommandsPerSecondObserved"], 8)

    def test_probe_clean_and_noisy_classification(self):
        clean = arduino_mirror_verifier.classify_probe_result(
            command_sent=True,
            supported=True,
            acked=True,
            requested_move=True,
            commanded_dx=25,
            commanded_dy=0,
            observed_dx=24,
            observed_dy=0,
            max_error_px=100,
        )
        noisy = arduino_mirror_verifier.classify_probe_result(
            command_sent=True,
            supported=True,
            acked=True,
            requested_move=True,
            commanded_dx=12,
            commanded_dy=0,
            observed_dx=-465,
            observed_dy=178,
            max_error_px=100,
        )
        self.assertEqual(clean["classification"], "arduino_probe_verified_clean")
        self.assertEqual(noisy["classification"], "arduino_probe_verified_noisy")

    def test_non_probe_commands_can_verify_mirror(self):
        input_events = [
            {"kind": "mouse_move", "event_seq": 1, "elapsed_seconds": 1.0, "dx": 12, "dy": 0},
            {"kind": "mouse_move", "event_seq": 2, "elapsed_seconds": 1.05, "dx": 12, "dy": 0},
        ]
        arduino_events = [
            {
                "kind": "command_sent",
                "command": "MOVE",
                "dx": 12,
                "dy": 0,
                "elapsed_seconds": 1.0,
                "sourceInputEventSeq": 1,
                "liveMirrorCommand": True,
                "probeCommand": False,
            }
        ]
        summary = arduino_mirror_verifier.build_input_path_integrity(input_events, arduino_events, requested_mode="mirror")
        self.assertEqual(summary["inputPathClassification"], "arduino_mirror_verified")
        self.assertTrue(summary["liveMirrorVerified"])

    def test_analyzer_detects_menu_actions_after_mirror_disarm(self):
        input_events = [
            {"kind": "capture_start", "event_seq": 1, "elapsed_seconds": 0.0},
            {"kind": "click", "event_seq": 2, "elapsed_seconds": 7.0},
        ]
        classifications = [
            {
                "eventSeq": 2,
                "eventKind": "click",
                "classification": "menu_selection_click",
                "targetRelativeEligible": True,
                "time": {"elapsedSeconds": 7.0},
                "targetContext": {"targetName": "Staircase", "targetAction": "Climb-down"},
            }
        ]
        manifest = {
            "arduino": {
                "live_mirror_requested": True,
                "live_mirror_armed": {"arm_mode": "test_window", "arm_delay_ms": 0, "test_duration_sec": 5},
            }
        }
        timing = input_trace_joiner.annotate_mirror_action_timing(input_events, classifications, [], manifest)
        self.assertEqual(timing["finalMirrorRecordingVerdict"], "WARN")
        self.assertEqual(timing["menuSelectionsAfterDisarm"], 1)
        self.assertFalse(classifications[0]["mirrorArmedAtAction"])

    def test_analyzer_detects_post_action_arduino_commands(self):
        input_events = [
            {"kind": "capture_start", "event_seq": 1, "elapsed_seconds": 0.0},
            {"kind": "click", "event_seq": 2, "elapsed_seconds": 2.0},
        ]
        classifications = [
            {
                "eventSeq": 2,
                "eventKind": "click",
                "classification": "menu_selection_click",
                "targetRelativeEligible": True,
                "time": {"elapsedSeconds": 2.0},
                "targetContext": {"targetName": "Staircase", "targetAction": "Climb-down"},
            }
        ]
        commands = [
            {"kind": "command_sent", "command": "MOVE", "elapsed_seconds": 3.0, "sourceInputEventSeq": 3, "liveMirrorCommand": True, "probeCommand": False},
            {"kind": "command_sent", "command": "CLICK", "elapsed_seconds": 4.0, "sourceInputEventSeq": 4, "liveMirrorCommand": True, "probeCommand": False},
        ]
        manifest = {
            "arduino": {
                "live_mirror_requested": True,
                "live_mirror_armed": {"arm_mode": "recording_persistent", "arm_delay_ms": 0},
            }
        }
        timing = input_trace_joiner.annotate_mirror_action_timing(input_events, classifications, commands, manifest)
        self.assertEqual(timing["finalMirrorRecordingVerdict"], "WARN")
        self.assertEqual(timing["postActionArduinoCommandCount"], 2)
        self.assertEqual(timing["postActionMovementCommandCount"], 1)
        self.assertTrue(timing["postActionWeirdMovementSuspected"])

    def test_click_ownership_detects_duplicate_os_plus_arduino_click(self):
        input_events = [{"kind": "click", "event_seq": 5, "elapsed_seconds": 3.0}]
        commands = [
            {
                "kind": "command_sent",
                "command": "CLICK",
                "elapsed_seconds": 3.0,
                "sourceInputEventSeq": 5,
                "liveMirrorCommand": True,
                "probeCommand": False,
                "clickPolicyUsed": "live_unsuppressed",
            }
        ]
        summary = input_trace_joiner.build_click_ownership_summary(
            input_events,
            commands,
            {"arduino": {"live_mirror_settings": {"mirror_click_policy": "live_unsuppressed"}}},
        )
        self.assertEqual(summary["duplicateClickLikelyCount"], 1)
        self.assertEqual(summary["liveClickWithoutSuppressionCount"], 1)
        self.assertEqual(summary["clickOwners"].get("duplicate_os_plus_arduino_click"), 1)

    def test_click_ownership_reports_map_only_clicks(self):
        input_events = [{"kind": "click", "event_seq": 6, "elapsed_seconds": 4.0}]
        commands = [
            {
                "kind": "command_dropped",
                "command": "CLICK",
                "elapsed_seconds": 4.0,
                "sourceInputEventSeq": 6,
                "mapOnlyClick": True,
                "dropReason": "click_policy_map_only",
            }
        ]
        summary = input_trace_joiner.build_click_ownership_summary(
            input_events,
            commands,
            {"arduino": {"live_mirror_settings": {"mirror_click_policy": "map_only"}}},
        )
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["mapOnlyClickCount"], 1)
        self.assertEqual(summary["duplicateClickLikelyCount"], 0)
        self.assertEqual(summary["clickOwners"].get("conversion_trace_click_only"), 1)


if __name__ == "__main__":
    unittest.main()
